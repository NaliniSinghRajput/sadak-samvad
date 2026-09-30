"""
PROJECT_V2 — GPS telemetry
==========================
Team The RoadRunners · SIH 2026 · PS 26124 (Bharat Electronics Limited)

WHAT CHANGED FROM v1, AND WHY IT MATTERS
    Project_v1 had no position at all. Every event it emitted said *what* was on
    the road and *when* in the video, and nothing about *where*. That was stated
    as a limitation on the dashboard and in the deck, because it was one: a
    defect ledger a municipality cannot navigate to is a list, not a work order.

    The Naini capture of 28-Aug-2026 carries a real GPS track. This module is
    the bridge between it and the perception pipeline, and it exists to do one
    job carefully: given a moment in the video, say where the vehicle was — or
    say honestly that it does not know.

THE THING THIS MODULE REFUSES TO DO
    It will not silently hand back an interpolated position as if it were a
    measurement. Roughly a quarter of the rows in these sidecars are filled in
    across GPS dropouts of up to 29 seconds. At 6.7 km/h a 29-second dropout is
    54 metres of road with no fix in it. Every `Fix` therefore carries
    `trustworthy`, and the pipeline is expected to check it before it attaches a
    coordinate to a pothole. A wrong coordinate is worse than no coordinate: a
    missing one sends nobody anywhere, a wrong one sends a crew to the wrong
    street and costs the platform its credibility on the first field trial.

ACCURACY, STATED ONCE, PLAINLY
    A consumer 1 Hz GPS at these speeds gives roughly 3-5 m of position error.
    The video/GPS alignment was recovered by anchor-matching a standstill rather
    than from clock metadata, which is good to about 1-2 s — another 2-4 m
    along-track at 6.7 km/h. So the honest resolution of this system is
    STREET-LEVEL, not lane-level, and nothing downstream may claim otherwise.
    `Fix.accuracy_note` carries that sentence so it travels with the data.
"""
from __future__ import annotations

import bisect
import csv
import math
import os
from dataclasses import dataclass

# Kept as a module constant rather than a magic number in three places.
POSITION_ACCURACY_NOTE = (
    "Consumer 1 Hz GPS, ~3-5 m position error, plus ~1-2 s of anchor-matched "
    "video sync (~2-4 m along-track at these speeds). Street-level, not lane-level."
)


@dataclass
class Fix:
    """One position sample, with its provenance attached."""
    t_video_s: float
    utc_time: str
    lat: float
    lon: float
    ele_m: float
    speed_kmh: float
    bearing_deg: float
    fix_gap_s: float
    interpolated: int

    @property
    def trustworthy(self) -> bool:
        """False when no real GPS fix existed within 3.5 s of this sample.

        Check this before attaching a coordinate to anything a human will act
        on. The dashboard shows FIX or INTERP per event for the same reason.
        """
        return self.interpolated == 0

    @property
    def quality(self) -> str:
        return "fix" if self.interpolated == 0 else "interp"

    accuracy_note = POSITION_ACCURACY_NOTE

    def as_dict(self) -> dict:
        return {
            "lat": round(self.lat, 7),
            "lon": round(self.lon, 7),
            "ele_m": round(self.ele_m, 1),
            "speed_kmh": round(self.speed_kmh, 2),
            "bearing_deg": None if math.isnan(self.bearing_deg) else round(self.bearing_deg, 1),
            "gps_quality": self.quality,
            "fix_gap_s": round(self.fix_gap_s, 1),
        }


class Telemetry:
    """The 10 Hz sidecar for one video, indexed for lookup by video time.

    `video_start_offset_s` exists because a clip cut out of a longer master
    still has to line up with the master's telemetry. Cut 45 seconds starting at
    t=260 and pass 260, and `at_time(0)` returns the position at master t=260.
    On a real bus this is the same arithmetic that lines a segment of an RTSP
    recording up with the vehicle's AIS-140 feed, so it is not test scaffolding.
    """

    def __init__(self, csv_path: str, source_fps: float = 60.0,
                 video_start_offset_s: float = 0.0):
        self.csv_path = csv_path
        self.source_fps = source_fps
        self.offset = float(video_start_offset_s)
        self.t: list[float] = []
        self.rows: list[Fix] = []
        with open(csv_path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if not r.get("lat"):
                    continue                       # outside GPS coverage entirely
                bearing = r.get("bearing_deg") or ""
                self.t.append(float(r["t_video_s"]))
                self.rows.append(Fix(
                    float(r["t_video_s"]), r.get("utc_time", ""),
                    float(r["lat"]), float(r["lon"]), float(r.get("ele_m") or 0.0),
                    float(r.get("speed_kmh") or 0.0),
                    float(bearing) if bearing else float("nan"),
                    float(r.get("fix_gap_s") or 0.0),
                    int(r.get("interpolated") or 0)))

    # -- lookup ------------------------------------------------------------
    def at_time(self, t_video_s: float) -> Fix | None:
        """Nearest sample to a moment in THIS video (offset already applied)."""
        if not self.rows:
            return None
        t = t_video_s + self.offset
        i = bisect.bisect_left(self.t, t)
        if i == 0:
            return self.rows[0]
        if i >= len(self.t):
            return self.rows[-1]
        before, after = self.t[i - 1], self.t[i]
        return self.rows[i - 1] if (t - before) <= (after - t) else self.rows[i]

    def at_frame(self, frame_idx: int, fps: float | None = None) -> Fix | None:
        return self.at_time(frame_idx / (fps or self.source_fps))

    # -- summary -----------------------------------------------------------
    def coverage(self) -> tuple[int, int]:
        good = sum(1 for r in self.rows if r.trustworthy)
        return good, len(self.rows) - good

    def bounds(self) -> dict:
        lats = [r.lat for r in self.rows]
        lons = [r.lon for r in self.rows]
        return {"min_lat": min(lats), "max_lat": max(lats),
                "min_lon": min(lons), "max_lon": max(lons)}

    def route(self, every: int = 5, real_only: bool = False) -> list[list[float]]:
        """The track as a polyline, thinned for drawing.

        `real_only` drops interpolated samples. The dashboard draws the full
        track for shape and overlays the real fixes for provenance, so both
        forms are wanted.
        """
        src = [r for r in self.rows if (r.trustworthy or not real_only)]
        return [[round(r.lat, 6), round(r.lon, 6)] for r in src[::max(1, every)]]

    def distance_m(self) -> float:
        """Path length over real fixes only. Interpolated points lie on straight
        lines the vehicle did not necessarily drive, so including them would
        quietly shorten the route across every dropout."""
        pts = [r for r in self.rows if r.trustworthy]
        return sum(haversine_m(pts[i].lat, pts[i].lon, pts[i + 1].lat, pts[i + 1].lon)
                   for i in range(len(pts) - 1))

    def summary(self) -> dict:
        good, interp = self.coverage()
        speeds = [r.speed_kmh for r in self.rows]
        return {
            "csv": os.path.basename(self.csv_path),
            "samples": len(self.rows),
            "real_fixes": good,
            "interpolated": interp,
            "coverage_pct": round(100.0 * good / max(1, len(self.rows)), 1),
            "max_fix_gap_s": round(max((r.fix_gap_s for r in self.rows), default=0.0), 1),
            "distance_m": round(self.distance_m(), 1),
            "mean_speed_kmh": round(sum(speeds) / max(1, len(speeds)), 2),
            "max_speed_kmh": round(max(speeds, default=0.0), 2),
            "t_span_s": round(self.t[-1] - self.t[0], 1) if self.t else 0.0,
            "offset_s": self.offset,
            "accuracy_note": POSITION_ACCURACY_NOTE,
            "bounds": self.bounds(),
        }


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
def find_sidecar(video_path: str, extra_dirs: list[str] | None = None) -> str | None:
    """Find the telemetry CSV that belongs to a video, without being told.

    A dashboard operator should not have to hunt for a sidecar. We look for the
    obvious name next to the video, then in the folders the capture kit actually
    ships in. If nothing is found the pipeline runs exactly as v1 did — no
    position, and it says so on screen — which is the correct degradation.
    """
    base = os.path.splitext(os.path.basename(video_path))[0]
    stems = [base]
    # a clip cut from a master keeps the master's stem as a prefix:
    #   naini-2_gps.MP4 -> n2_clip_260_305.mp4 will not match, but
    #   naini-2_gps_cut.mp4 -> naini-2_gps will.
    if "_" in base:
        stems.append(base.rsplit("_", 1)[0])
    vdir = os.path.dirname(os.path.abspath(video_path))
    cands = [vdir,
             os.path.join(vdir, "gps"),
             os.path.join(vdir, "_kit"),
             os.path.join(vdir, "..", "_kit"),
             os.path.join(vdir, "..", "GPS-Dataset", "_kit"),
             os.path.join(vdir, "..", "..", "GPS-Dataset", "_kit")]
    if extra_dirs:
        cands = list(extra_dirs) + cands
    for d in cands:
        for stem in stems:
            p = os.path.abspath(os.path.join(d, stem + ".csv"))
            if os.path.exists(p):
                return p
    return None


# ---------------------------------------------------------------------------
# geodesy — small, exact enough, and dependency-free on purpose
# ---------------------------------------------------------------------------
EARTH_R = 6371008.8      # mean Earth radius, metres (IUGG)


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))
