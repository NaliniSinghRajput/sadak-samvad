"""
PROJECT_V1 — the edge/cloud contract
====================================
Everything the bus is allowed to send. Raw video stays on the vehicle; only
these compact records travel. The class also MEASURES what it emitted, so the
bandwidth claim in our pitch is an observation rather than an assertion.

Team The RoadRunners · SIH 2026
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from config import EventConfig


@dataclass
class Event:
    """One thing worth telling the city about.

    Field names are the wire contract. Nothing here is optional decoration —
    an operator needs to know what, where, when, how sure, and be able to
    verify it by eye.
    """
    event_id: str
    vehicle_id: str
    route_id: str
    ts_utc: str
    t_video_s: float
    frame_idx: int
    category: str          # road_surface | vehicle | pedestrian | infrastructure
    label: str             # pothole_candidate | car | person | traffic_light ...
    confidence: float
    bbox: list             # [x, y, w, h] in processed-frame pixels
    speed_index: float     # relative ego-speed at time of detection
    attrs: dict = field(default_factory=dict)
    thumbnail_b64: str | None = None

    def to_json(self) -> str:
        return json.dumps({k: v for k, v in self.__dict__.items() if v is not None},
                          separators=(",", ":"))


class EventSink:
    """Collects, de-duplicates, and accounts for outgoing events.

    De-duplication is the whole reason the bandwidth number is small. A bus
    approaching a pothole sees it in fifty consecutive frames. The city wants
    ONE record, not fifty. We suppress repeats of the same label in the same
    part of the frame inside a short time window.

    MULTI-SIGHTING CONFIRMATION (added 2026-08-26, and it earned its place)
        For labels listed in `confirm_labels` we do the opposite of emitting on
        first sight: we HOLD the record until the same thing has been seen
        `min_track_hits` times in that window, and only then send it, carrying
        the sighting count with it.

        The reason is a real failure we caught. The repair-priority ledger's
        number-one item -- the single worst defect on a 6.5 minute route -- was
        the rider's own knee, seen for an instant while the camera tilted down
        onto the fuel tank. Every geometric guard we had was blind to it: the box
        sat high in the frame, so the near-field ego rules never applied, and the
        road-plane trapezoid cheerfully agreed that his thigh was road.

        Persistence catches what geometry cannot. A defect the vehicle is driving
        towards is seen again, and again, growing, over several hundred
        milliseconds. A momentary artefact is seen once. This is the same test the
        classical SurfaceProposer already applies to its candidates; the trained
        detector had simply never been held to it.

        The cost is honest and small: a road-damage alert is confirmed over the
        de-duplication window rather than fired instantly. For a repair backlog
        that is not a latency anyone will notice, and it is the difference
        between a ledger a city can act on and one it learns to distrust.
    """

    def __init__(self, cfg: EventConfig, vehicle_id: str, route_id: str, out_dir: str):
        self.cfg = cfg
        self.vehicle_id = vehicle_id
        self.route_id = route_id
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.path = os.path.join(out_dir, "events.jsonl")
        self._fh = open(self.path, "w", encoding="utf-8")
        self._recent: list[dict] = []   # live tracks: label, cx, cy, t_last, hits, pending
        self._n = 0
        self._suppressed = 0
        self._bytes = 0
        self._counts: dict[str, int] = {}
        self._unconfirmed = 0           # held records that never reached the threshold

    # -- track bookkeeping -------------------------------------------------
    def _expire(self, t: float) -> None:
        """Retire tracks whose window has closed, flushing any that qualified."""
        keep = []
        for r in self._recent:
            if t - r["t_last"] <= self.cfg.dedup_window_s:
                keep.append(r)
            elif r["pending"] is not None:
                if r["hits"] >= self.cfg.min_track_hits:
                    self._write(r["pending"], r["hits"])
                else:
                    self._unconfirmed += 1
        self._recent = keep

    def _match(self, label: str, cx: float, cy: float):
        for r in self._recent:
            if r["label"] == label and np.hypot(cx - r["cx"], cy - r["cy"]) <= self.cfg.dedup_dist_px:
                return r
        return None

    # -- thumbnails --------------------------------------------------------
    def _thumb(self, frame: np.ndarray, bbox) -> str | None:
        if not self.cfg.emit_thumbnails:
            return None
        x, y, w, h = [int(v) for v in bbox]
        pad = int(0.15 * max(w, h))
        H, W = frame.shape[:2]
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
        if x1 <= x0 or y1 <= y0:
            return None
        crop = frame[y0:y1, x0:x1]
        s = self.cfg.thumb_max_px / max(crop.shape[0], crop.shape[1], 1)
        if s < 1:
            crop = cv2.resize(crop, (max(1, int(crop.shape[1] * s)),
                                     max(1, int(crop.shape[0] * s))))
        ok, buf = cv2.imencode(".jpg", crop,
                               [cv2.IMWRITE_JPEG_QUALITY, self.cfg.thumb_quality])
        return base64.b64encode(buf).decode("ascii") if ok else None

    # -- public API --------------------------------------------------------
    def emit(self, *, category: str, label: str, confidence: float, bbox,
             frame_idx: int, t_video_s: float, speed_index: float,
             frame: np.ndarray | None = None, attrs: dict | None = None) -> Event | None:
        x, y, w, h = [float(v) for v in bbox]
        cx, cy = x + w / 2, y + h / 2
        self._expire(t_video_s)

        hit = self._match(label, cx, cy)
        if hit is not None:
            hit["hits"] += 1
            hit["cx"], hit["cy"], hit["t_last"] = cx, cy, t_video_s
            self._suppressed += 1
            return None

        draft = dict(
            vehicle_id=self.vehicle_id, route_id=self.route_id,
            t_video_s=round(t_video_s, 2), frame_idx=frame_idx,
            category=category, label=label, confidence=round(float(confidence), 3),
            bbox=[round(v, 1) for v in (x, y, w, h)],
            speed_index=round(float(speed_index), 2),
            attrs=dict(attrs or {}),
            thumbnail_b64=self._thumb(frame, bbox) if frame is not None else None,
        )
        needs_confirming = label in self.cfg.confirm_labels
        self._recent.append({"label": label, "cx": cx, "cy": cy, "t_last": t_video_s,
                             "hits": 1, "pending": draft if needs_confirming else None})
        if needs_confirming:
            return None                      # held until the sightings add up
        return self._write(draft, 1)

    def _write(self, draft: dict, sightings: int) -> Event:
        self._n += 1
        draft = dict(draft)
        draft["attrs"] = dict(draft["attrs"])
        draft["attrs"]["sightings"] = sightings
        ev = Event(event_id=f"{self.vehicle_id}-{self._n:06d}",
                   ts_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **draft)
        line = ev.to_json()
        self._fh.write(line + "\n")
        self._bytes += len(line.encode("utf-8")) + 1
        self._counts[ev.label] = self._counts.get(ev.label, 0) + 1
        return ev

    def close(self, video_bytes: int, duration_s: float) -> dict:
        """Shut the log and compute the bandwidth case, from real measurements."""
        self._expire(float("inf"))          # flush or discard everything still held
        self._fh.close()
        payload_bytes = os.path.getsize(self.path)

        # Strip thumbnails to show the pure-telemetry figure as well
        tele = 0
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                d = json.loads(line)
                d.pop("thumbnail_b64", None)
                tele += len(json.dumps(d, separators=(",", ":")).encode()) + 1

        hours = max(duration_s / 3600.0, 1e-9)
        stats = {
            "events_emitted": self._n,
            "duplicates_suppressed": self._suppressed,
            "held_records_never_confirmed": self._unconfirmed,
            "events_by_label": dict(sorted(self._counts.items(),
                                           key=lambda kv: -kv[1])),
            "video_bytes": video_bytes,
            "event_bytes_with_thumbnails": payload_bytes,
            "event_bytes_telemetry_only": tele,
            "compression_ratio_with_thumbnails": round(video_bytes / max(payload_bytes, 1), 1),
            "compression_ratio_telemetry_only": round(video_bytes / max(tele, 1), 1),
            "events_per_hour": round(self._n / hours, 1),
            "projected_telemetry_MB_per_bus_per_12h_shift":
                round(tele / max(duration_s, 1e-9) * 12 * 3600 / 1e6, 2),
            "projected_video_GB_per_bus_per_12h_shift":
                round(video_bytes / max(duration_s, 1e-9) * 12 * 3600 / 1e9, 1),
        }
        with open(os.path.join(self.out_dir, "bandwidth_report.json"), "w") as fh:
            json.dump(stats, fh, indent=2)
        return stats
