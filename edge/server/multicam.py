"""
PROJECT_V3 — the multi-camera edge node
=======================================
Team The Road Runners · SIH 2026 · PS 26124

WHAT THIS ADDS TO v2, IN ONE LINE
    v2 analysed one feed. A bus has four cameras, and one laptop. This is the
    scheduler that makes four feeds share one detector honestly, and the bus that
    carries what they find to the screen.

THE CONSTRAINT, STATED BEFORE THE FEATURE
    Measured on this laptop, the v1/v2 pipeline runs at roughly 3.5 frames per
    second on ONE stream at 960 px. Four streams do not make the laptop faster.
    So the node does not pretend to analyse 4 x 3.5 fps; it ROUND-ROBINS a single
    inference budget across the cameras and reports the per-camera rate it
    actually achieved. On a real vehicle this is the number that decides how many
    cameras one edge box can carry — it is a design input, not an embarrassment,
    and the dashboard shows it as such.

WHY ROUND-ROBIN AND NOT "WHOEVER IS BUSIEST"
    A priority scheduler would starve the quiet cameras, and the quiet camera is
    exactly where an unexpected hazard appears. Equal shares, measured, with the
    option to weight a camera up if the operator chooses. Fairness is the default
    because the failure mode of the alternative is silent.

WHAT TRAVELS TO THE BROWSER
    Three different things, at three different rates, because they have three
    different costs:
        preview   the camera's own JPEG, passed through untouched  (~6/s)
        analysis  boxes as FRACTIONS of the frame + events         (as produced)
        stats     health, rates, bandwidth, scheduler shares       (2/s)
    The browser draws the boxes over the preview. Nothing is re-encoded on this
    machine for display, which is CPU we would rather spend on the detector.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time

from cameras import BaseCamera, build_cameras           # noqa: E402
from perception import (CameraAnalyser, CLASS_PRIORITY, LEDGER_LABELS,   # noqa: E402
                        Models, SAFETY_LABELS, SEVERITY)
from positioning import build_source                    # noqa: E402

# Standing water is a DRAINAGE defect, not a surface one. It belongs in the
# repair ledger and in the GeoJSON a municipality opens, but it must not be
# counted in the "road defects" figure, which means potholes and says so.
DRAINAGE_LABELS = ("waterlogging",)


def _extras_flag(name: str, default: bool = False) -> bool:
    try:
        import extras
        return bool(getattr(extras, name, default))
    except Exception:
        return default

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SESSIONS = os.path.join(ROOT, "sessions")


class MultiCamEngine:
    """Owns the cameras, the schedule, the event log and the numbers."""

    def __init__(self, specs: list[dict], out_queue: "queue.Queue", cfg,
                 models: Models, mode: str = "live", work_width: int = 640,
                 preview_fps: float = 6.0, record: bool = True,
                 source_label: str = "", position: dict | None = None):
        self.specs = specs
        self.q = out_queue
        self.cfg = cfg
        self.models = models
        self.mode = mode                      # "live" | "recorded"
        self.work_width = int(work_width)
        self.preview_fps = float(preview_fps)
        self.source_label = source_label
        self.cameras: dict[str, BaseCamera] = {}
        self.readers: list = []
        self.analysers: dict[str, CameraAnalyser] = {}
        self.order: list[str] = []
        self._rr = 0
        self._stop = threading.Event()
        self._pause = threading.Event()
        self.threads: list[threading.Thread] = []
        self.started_at = 0.0
        self.event_n = 0
        self.events: list[dict] = []          # full log (no thumbnails kept twice)
        self.alerts: list[dict] = []
        self.ledger: list[dict] = []
        self.telemetry_bytes = 0
        self.frames_analysed = 0
        self.analysis_ms: list[float] = []
        self.per_camera_analysed: dict[str, int] = {}
        self.timeline: list[dict] = []        # (t, camera, label, severity)
        # POSITION. One source, chosen by the operator, named on the screen. See
        # positioning.py for the rule that an event is geo-tagged only from a
        # real fix.
        self.position = build_source(position)
        self.geo_defects: list[dict] = []     # what the map draws
        self.events_geotagged = 0
        self.events_no_fix = 0
        self.record_path = None
        self._rec_lock = threading.Lock()
        if record:
            os.makedirs(SESSIONS, exist_ok=True)
            self.record_path = os.path.join(
                SESSIONS, time.strftime("session-%Y%m%d-%H%M%S.jsonl"))

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        self.started_at = time.time()
        # One decoder per recorded file, shared by its tiles (see cameras.py).
        cams, self.readers = build_cameras(self.specs)
        for cam in cams:
            self.cameras[cam.id] = cam
            self.analysers[cam.id] = CameraAnalyser(
                cam.id, cam.name, cam.position, self.models, self.cfg,
                width=self.work_width)
            self.order.append(cam.id)
            self.per_camera_analysed[cam.id] = 0
            cam.start()
        for r in self.readers:
            r.start()
        self._push("started", {
            "mode": self.mode, "source_label": self.source_label,
            "cameras": [{"id": c.id, "name": c.name, "position": c.position,
                         "kind": c.kind} for c in self.cameras.values()],
            "work_width": self.work_width,
            "models": self.models.describe(),
            "session": os.path.basename(self.record_path) if self.record_path else None,
            "position": self.position.summary(),
        })
        # NOTE: there is no preview pump any more. Preview frames go out over
        # plain HTTP as MJPEG (see app.py), exactly as a phone serves them — so
        # the browser decodes them itself and the WebSocket carries only JSON.
        # Base64 previews on the socket cost ~1.3 MB/s of string churn in the
        # page, which is what made a loaded laptop reclaim the tab.
        for target, name in ((self._scheduler, "scheduler"),
                             (self._calibration_pump, "calibration"),
                             (self._stats_pump, "stats")):
            t = threading.Thread(target=target, daemon=True, name=f"v3-{name}")
            t.start()
            self.threads.append(t)

    def stop(self):
        self._stop.set()
        self.position.stop()
        for r in self.readers:
            r.stop()
        for c in self.cameras.values():
            c.stop()

    def toggle_pause(self) -> bool:
        if self._pause.is_set():
            self._pause.clear()
        else:
            self._pause.set()
        return self._pause.is_set()

    # -- messaging ---------------------------------------------------------
    def _push(self, kind: str, payload: dict):
        msg = {"type": kind, **payload}
        try:
            self.q.put_nowait(msg)
        except queue.Full:
            pass
        if self.record_path and kind in ("started", "event", "stats", "finished",
                                         "calibration"):
            with self._rec_lock:
                try:
                    with open(self.record_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(msg, separators=(",", ":")) + "\n")
                except Exception:                     # noqa: BLE001
                    pass

    # -- the scheduler -----------------------------------------------------
    def _scheduler(self):
        """One inference at a time, cameras taken in turn.

        A camera is skipped when it has produced no NEW frame since we last
        looked at it — re-analysing the same pixels tells us nothing and spends
        the budget another camera could have used. That skip is also how a dead
        camera stops costing anything.
        """
        last_seq: dict[str, int] = {}
        idle_sleep = 0.02
        while not self._stop.is_set():
            if self._pause.is_set():
                time.sleep(0.1)
                continue
            picked = None
            for _ in range(len(self.order)):
                cid = self.order[self._rr % len(self.order)]
                self._rr += 1
                cam = self.cameras[cid]
                if cam.status not in ("live",):
                    continue
                _, seq = cam.latest_jpeg()
                if seq and seq != last_seq.get(cid):
                    picked = (cid, seq)
                    break
            if picked is None:
                time.sleep(idle_sleep)
                continue

            cid, seq = picked
            cam = self.cameras[cid]
            frame, seq2, t_frame = cam.latest_frame()
            if frame is None:
                time.sleep(idle_sleep)
                continue
            last_seq[cid] = seq2
            an = self.analysers[cid]
            t0 = time.time()
            try:
                out = an.analyse(frame, time.time())
            except Exception as exc:                         # noqa: BLE001
                self._push("error", {"message": f"{cid}: {exc}"})
                time.sleep(0.2)
                continue
            dt = time.time() - t0
            self.analysis_ms.append(dt * 1000)
            self.analysis_ms = self.analysis_ms[-40:]
            self.frames_analysed += 1
            self.per_camera_analysed[cid] += 1
            cam.analysed += 1
            cam.last_analysed_at = time.time()

            # Where were we when this frame was taken? A file-backed camera
            # knows its own video time; a live camera is simply "now".
            t_video = getattr(cam, "t_video", None)
            fix = self.position.fix_at(time.time() - self.started_at, t_video)
            for ev in out["events"]:
                self._emit(ev, fix)

            self._push("analysis", {
                "camera": cid,
                "detections": out["detections"],
                "counts": out["counts"],
                "speed_index": out["speed_index"],
                "ms": round(dt * 1000, 1),
                "work_size": out["work_size"],
                "seq": seq2,
                "t": round(time.time() - self.started_at, 2),
            })


    # -- calibration pump ---------------------------------------------------
    def _calibration_pump(self):
        """Give every camera the frame pairs its ego learner needs, then stop.

        Optical flow needs two frames a few hundred milliseconds apart. The
        scheduler cannot supply that — with four cameras it returns to each one
        about every two seconds — so calibration reads the cameras itself for the
        first minute or so of a run and then gets out of the way.
        """
        done: set[str] = set()
        while not self._stop.is_set() and len(done) < len(self.order):
            for cid in self.order:
                if cid in done:
                    continue
                cam = self.cameras[cid]
                if cam.status != "live":
                    continue
                frame, _, _ = cam.latest_frame()
                if frame is None:
                    continue
                an = self.analysers[cid]
                try:
                    if an.offer_calibration(frame, time.time()):
                        done.add(cid)
                        self._push("calibration", {"camera": cid, "ego": an.ego_report}
                                   | an.masks_png())
                except Exception as exc:                       # noqa: BLE001
                    self._push("error", {"message": f"{cid} calibration: {exc}"})
                    done.add(cid)
            time.sleep(0.35)

    # -- events ------------------------------------------------------------
    def _emit(self, ev: dict, fix=None):
        self.event_n += 1
        ev["event_id"] = f"RR3-{self.event_n:06d}"
        ev["vehicle_id"] = self.cfg.vehicle_id
        ev["route_id"] = self.cfg.route_id
        ev["t"] = round(time.time() - self.started_at, 2)

        # -- POSITION, WITH ITS PROVENANCE (the v2 rule, unchanged) ----------
        # Only a REAL fix geo-tags an event. A stale or interpolated one is kept
        # for the map's line but never becomes the coordinate of a pothole,
        # because sending a crew to the wrong place costs more than sending them
        # nowhere.
        if fix is not None:
            if fix.trustworthy:
                ev["geo"] = fix.as_dict()
                ev["geo"]["accuracy_note"] = self.position.accuracy_note
                ev["geo"]["source"] = self.position.kind
                self.events_geotagged += 1
            else:
                ev["geo_withheld"] = {
                    "reason": f"no real fix within {fix.fix_gap_s:.1f} s",
                    "fix_gap_s": round(fix.fix_gap_s, 1)}
                self.events_no_fix += 1
        else:
            self.events_no_fix += 1
        # The bandwidth claim we defend is telemetry WITHOUT the thumbnail: on a
        # bus the picture is fetched on demand, not pushed to the city.
        lean = {k: v for k, v in ev.items() if k != "thumbnail_b64"}
        ev["bytes"] = len(json.dumps(lean, separators=(",", ":")).encode()) + 1
        self.telemetry_bytes += ev["bytes"]

        self.events.append(lean)
        self.timeline.append({"t": ev["t"], "camera": ev["camera"],
                              "label": ev["label"], "severity": ev.get("severity")})
        if ev.get("severity") or ev["label"] in CLASS_PRIORITY:
            self.alerts.append(ev)
            self.alerts = self.alerts[-200:]
        if ev["label"] in LEDGER_LABELS or ev["label"] in DRAINAGE_LABELS:
            self.ledger.append({k: ev.get(k) for k in
                                ("event_id", "camera", "camera_name", "position",
                                 "label", "severity", "confidence", "t", "ts_utc",
                                 "thumbnail_b64", "attrs")} | {"geo": ev.get("geo")})
        if "geo" in ev and (ev["label"] in LEDGER_LABELS
                            or ev["label"] in DRAINAGE_LABELS
                            or ev["label"] in SAFETY_LABELS):
            self.geo_defects.append({
                "event_id": ev["event_id"], "label": ev["label"],
                "severity": ev.get("severity"), "confidence": ev["confidence"],
                "camera": ev["camera"], "camera_name": ev["camera_name"],
                "mounted": ev.get("position"), "t": ev["t"],
                "lat": ev["geo"]["lat"], "lon": ev["geo"]["lon"],
                "gps_quality": ev["geo"].get("gps_quality"),
            })
        self._push("event", {"event": ev})

    # -- stats pump --------------------------------------------------------
    def _stats_pump(self):
        while not self._stop.is_set():
            self._push("stats", self.snapshot())
            time.sleep(0.5)

    def snapshot(self) -> dict:
        up = max(time.time() - self.started_at, 1e-6)
        ingest_bytes = sum(c.bytes_in for c in self.cameras.values())
        ingest_kbps = sum(c.kbps_in() for c in self.cameras.values())
        tel_bps = self.telemetry_bytes / up
        cams = []
        for cid in self.order:
            cam = self.cameras[cid]
            an = self.analysers[cid]
            share = self.per_camera_analysed[cid] / max(self.frames_analysed, 1)
            cams.append(cam.health() | an.stats() | {
                "share": round(share, 3),
                "fps_analysed": round(self.per_camera_analysed[cid] / up, 2),
            })
        sev_counts = {"severe": 0, "moderate": 0, "minor": 0}
        # Severity is graded on more than defects — a person in the carriageway
        # is graded too — so the ledger needs its OWN severity split. Without it
        # the road-defect tile reads "0 defects, 92 severe", which is nonsense on
        # its face and exactly the kind of thing a panel notices.
        sev_defects = {"severe": 0, "moderate": 0, "minor": 0}
        for e in self.events:
            s = e.get("severity")
            if s in sev_counts:
                sev_counts[s] += 1
                if e["label"] in LEDGER_LABELS:
                    sev_defects[s] += 1
        labels: dict[str, int] = {}
        for e in self.events:
            labels[e["label"]] = labels.get(e["label"], 0) + 1
        pos = self.position.summary()
        pos |= {"track": self.position.track[-1500:],
                "geotagged": self.events_geotagged,
                "no_fix": self.events_no_fix,
                "defects": self.geo_defects[-400:]}
        return {
            "uptime_s": round(up, 1),
            "position": pos,
            "mode": self.mode,
            "paused": self._pause.is_set(),
            "cameras": cams,
            "cameras_online": sum(1 for c in self.cameras.values() if c.status == "live"),
            "cameras_total": len(self.cameras),
            "frames_analysed": self.frames_analysed,
            "fps_analysed": round(self.frames_analysed / up, 2),
            "ms_per_frame": round(sum(self.analysis_ms) / max(len(self.analysis_ms), 1), 1),
            "events": self.event_n,
            "events_per_min": round(self.event_n / up * 60.0, 1),
            "alerts": len(self.alerts),
            "defects": sum(1 for e in self.events if e["label"] in LEDGER_LABELS),
            "safety_alerts": sum(1 for e in self.events if e["label"] in SAFETY_LABELS),
            "severity": sev_counts,
            "severity_defects": sev_defects,
            "labels": labels,
            "ingest_bytes": ingest_bytes,
            "ingest_kbps": round(ingest_kbps, 1),
            "telemetry_bytes": self.telemetry_bytes,
            "telemetry_bps": round(tel_bps, 1),
            "compression": (round(ingest_bytes / self.telemetry_bytes, 1)
                            if self.telemetry_bytes else None),
            "projected_mb_12h": round(self.telemetry_bytes / up * 3600 * 12 / 1e6, 1),
            "projected_video_gb_12h": round(ingest_bytes / up * 3600 * 12 / 1e9, 1),
            # --- capabilities added for PS 26124 -------------------------------
            # The front end must be able to tell "none seen" from "not running".
            "waterlogging_enabled": _extras_flag("WATER_ENABLED"),
            "zebra_enabled": _extras_flag("ZEBRA_ENABLED"),
            "waterlogging": sum(1 for e in self.events
                                if e.get("category") == "waterlogging"),
            "waterlogging_severe": sum(1 for e in self.events
                                       if e.get("category") == "waterlogging"
                                       and e.get("severity") == "severe"),
            "plates_redacted": sum(int(a.get("plates_redacted") or 0)
                                   for a in self.analysers_stats()),
            "congestion": self._congestion_now(),
        }

    # -- derived analytics ---------------------------------------------------
    def plate_boxes(self, cam_id: str, grow: float = 0.35) -> list[list[float]]:
        """Where plates have just been on this camera, as fractions of the
        frame, grown a little. Used to blur the live preview as well as the
        stored thumbnail — an operator screen showing a legible plate beside a
        tile that says plates are blurred is a claim the node has not earned."""
        an = self.analysers.get(cam_id)
        if an is None:
            return []
        now = time.time()
        out = []
        for t, boxes in getattr(an, "recent_plates", []):
            if now - t > 2.5:
                continue
            for x, y, w, h in boxes:
                gx, gy = w * grow, h * grow
                out.append([max(0.0, x - gx), max(0.0, y - gy),
                            min(1.0, w + 2 * gx), min(1.0, h + 2 * gy)])
        return out

    def analysers_stats(self) -> list[dict]:
        out = []
        for an in self.analysers.values():
            try:
                out.append(an.stats())
            except Exception:
                pass
        return out

    def _congestion_now(self) -> dict | None:
        """Fleet-facing congestion is a road-segment property, not a camera one,
        so we take the worst current reading across the cameras that can see
        forward. It is derived from detections, never from a speed sensor."""
        best = None
        for an in self.analysers.values():
            c = getattr(an, "congestion", None)
            if not c:
                continue
            if best is None or c["index"] > best["index"]:
                best = dict(c)
                best["camera"] = an.id
        return best

    # -- export -------------------------------------------------------------
    def export_events_jsonl(self) -> str:
        return "\n".join(json.dumps(e, separators=(",", ":")) for e in self.events)

    def export_geojson(self) -> dict:
        """The ledger as GeoJSON — the form a municipal GIS eats with no
        conversion, carrying its own accuracy note so the caveat cannot be
        separated from the data."""
        feats = [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [d["lon"], d["lat"]]},
            "properties": {k: v for k, v in d.items() if k not in ("lat", "lon")}
            | {"vehicle_id": self.cfg.vehicle_id, "route_id": self.cfg.route_id},
        } for d in self.geo_defects]
        if len(self.position.track) > 1:
            feats.append({
                "type": "Feature",
                "geometry": {"type": "LineString",
                             "coordinates": [[p[1], p[0]] for p in self.position.track]},
                "properties": {"name": "driven track (real fixes only)"},
            })
        return {"type": "FeatureCollection", "features": feats,
                "properties": {"source": self.source_label,
                               "position_source": self.position.kind,
                               "accuracy_note": self.position.accuracy_note,
                               "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                          time.gmtime())}}

    def export_ledger_csv(self) -> str:
        rows = ["event_id,camera,position,label,severity,confidence,t_s,ts_utc,lat,lon,gps_quality"]
        for d in self.ledger:
            g = d.get("geo") or {}
            rows.append(",".join(str(d.get(k, "")) for k in
                                 ("event_id", "camera_name", "position", "label",
                                  "severity", "confidence", "t", "ts_utc"))
                        + f",{g.get('lat','')},{g.get('lon','')},{g.get('gps_quality','')}")
        return "\n".join(rows)
