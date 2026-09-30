"""
PROJECT_V2 — geometry on the ground
===================================
Team The RoadRunners · SIH 2026 · PS 26124 (Bharat Electronics Limited)

Three jobs, none of them requiring a GIS library on a Raspberry Pi:

  1. A LOCAL METRIC FRAME. Latitude and longitude are angles, and doing distance
     arithmetic in degrees is wrong by a factor that depends on where you are.
     Over a one-kilometre route a flat local projection is accurate to far
     better than the GPS noise, so we build one once, around the route's own
     centre, and work in metres from then on.

  2. SEGMENTS. A municipality does not repair a coordinate, it repairs a stretch
     of road. Binning defects into fixed-length segments along the driven track
     turns a scatter of points into a ranked work order — which is what the
     problem statement actually asks for when it says "infrastructure deficiency
     ledger".

  3. REPEAT-PASS AGREEMENT. The Naini capture drove the same corridor twice, in
     opposite directions, nineteen minutes apart. A defect found on both passes
     is very probably real; one found on a single pass is a candidate. That
     gives us a precision estimate with NO manual labelling — the first
     self-measuring quality signal this project has had. It is not a substitute
     for ground truth and it is not reported as accuracy: it is agreement, and
     the difference is stated wherever the number is shown.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from telemetry import haversine_m


# ---------------------------------------------------------------------------
# 1. local metric frame
# ---------------------------------------------------------------------------
class LocalFrame:
    """Equirectangular projection about a reference point, in metres.

    Error grows with the square of the distance from the reference; at 1 km it
    is a few centimetres, which is three orders of magnitude below our GPS
    noise. Using UTM here would be more correct and would buy nothing we can
    measure, at the cost of a dependency on a device that has to boot in a bus.
    """

    def __init__(self, ref_lat: float, ref_lon: float):
        self.ref_lat = ref_lat
        self.ref_lon = ref_lon
        self.m_per_deg_lat = 111132.92 - 559.82 * math.cos(2 * math.radians(ref_lat))
        self.m_per_deg_lon = 111412.84 * math.cos(math.radians(ref_lat))

    def to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        return ((lon - self.ref_lon) * self.m_per_deg_lon,
                (lat - self.ref_lat) * self.m_per_deg_lat)

    def to_latlon(self, x: float, y: float) -> tuple[float, float]:
        return (self.ref_lat + y / self.m_per_deg_lat,
                self.ref_lon + x / self.m_per_deg_lon)

    @classmethod
    def from_points(cls, pts) -> "LocalFrame":
        lats = [p[0] for p in pts]
        lons = [p[1] for p in pts]
        return cls((min(lats) + max(lats)) / 2.0, (min(lons) + max(lons)) / 2.0)


# ---------------------------------------------------------------------------
# 2. route segments
# ---------------------------------------------------------------------------
@dataclass
class Segment:
    """One 25 m piece of road, and everything the bus learned about it.

    This is the unit every heat layer is built from. A heat map is not a blur
    over a picture — it is an aggregate per place, and the place has to be a
    thing a repair crew or a traffic engineer can be sent to. Twenty-five metres
    is about the smallest bin that is not mostly GPS noise (3-5 m per fix), and
    it is roughly the length of road a patching crew treats as one job.
    """
    index: int
    start_m: float
    end_m: float
    lat: float
    lon: float
    # -- defect layer -----------------------------------------------------
    defects: int = 0
    severity_score: float = 0.0
    labels: dict = field(default_factory=dict)
    # -- traffic layers ---------------------------------------------------
    mean_speed_kmh: float = 0.0
    vehicles: int = 0          # total vehicle detections attributed here
    frames: int = 0            # processed frames whose fix fell here
    # -- safety layer -----------------------------------------------------
    safety: int = 0            # VRU + animal-in-carriageway alerts
    _speeds: list = field(default_factory=list)

    @property
    def vehicle_density(self) -> float:
        """Vehicles per processed frame, not raw count.

        Raw counts would make a heat map of WHERE THE BUS SAT STILL, not where
        the traffic was: a segment the vehicle crawled through for a minute
        accumulates detections a segment it drove past in two seconds never can.
        Dividing by the frames actually spent there removes that bias, and it is
        the difference between a density map and a dwell map.
        """
        return self.vehicles / self.frames if self.frames else 0.0

    def as_dict(self) -> dict:
        return {"index": self.index,
                "from_m": round(self.start_m, 1), "to_m": round(self.end_m, 1),
                "lat": round(self.lat, 6), "lon": round(self.lon, 6),
                "defects": self.defects,
                "severity_score": round(self.severity_score, 2),
                "labels": self.labels,
                "mean_speed_kmh": round(self.mean_speed_kmh, 2),
                "vehicles": self.vehicles,
                "frames": self.frames,
                "vehicle_density": round(self.vehicle_density, 3),
                "safety": self.safety}


class RouteIndex:
    """The driven track, resampled and cut into fixed-length segments.

    Built from the telemetry's REAL fixes only. Interpolated samples lie on
    straight lines the vehicle did not necessarily drive, and a route geometry
    that quietly straightens itself across a 29-second dropout would mis-place
    every defect that fell in it.
    """

    SEVERITY_WEIGHT = {"severe": 3.0, "moderate": 2.0, "minor": 1.0}

    def __init__(self, points: list[tuple[float, float]], seg_len_m: float = 25.0):
        self.seg_len_m = float(seg_len_m)
        self.pts = list(points)
        self.frame = LocalFrame.from_points(self.pts) if self.pts else LocalFrame(0, 0)
        self.xy: list[tuple[float, float]] = [self.frame.to_xy(la, lo) for la, lo in self.pts]
        # cumulative distance along the track
        self.cum: list[float] = [0.0]
        for i in range(1, len(self.pts)):
            self.cum.append(self.cum[-1] + haversine_m(*self.pts[i - 1], *self.pts[i]))
        self.length_m = self.cum[-1] if self.cum else 0.0

        # Precomputed segment arrays for the vectorised projection above.
        if len(self.xy) >= 2:
            P = np.asarray(self.xy, dtype=float)
            self._A = P[:-1]
            self._V = P[1:] - P[:-1]
            self._L2 = np.maximum((self._V ** 2).sum(axis=1), 1e-12)
            self._seglen = np.sqrt(self._L2)
            self._cum = np.asarray(self.cum[:-1], dtype=float)
        else:
            self._A = self._V = self._L2 = self._seglen = self._cum = np.zeros((0, 2))

        n = max(1, int(math.ceil(self.length_m / self.seg_len_m))) if self.length_m else 0
        self.segments: list[Segment] = []
        for i in range(n):
            s0, s1 = i * self.seg_len_m, min((i + 1) * self.seg_len_m, self.length_m)
            la, lo = self.at_distance((s0 + s1) / 2.0)
            self.segments.append(Segment(i, s0, s1, la, lo))

    # -- queries -----------------------------------------------------------
    def at_distance(self, d_m: float) -> tuple[float, float]:
        """Interpolated lat/lon at a given distance along the track."""
        if not self.pts:
            return (0.0, 0.0)
        if d_m <= 0:
            return self.pts[0]
        if d_m >= self.length_m:
            return self.pts[-1]
        import bisect
        i = max(1, bisect.bisect_left(self.cum, d_m))
        d0, d1 = self.cum[i - 1], self.cum[i]
        f = 0.0 if d1 <= d0 else (d_m - d0) / (d1 - d0)
        (la0, lo0), (la1, lo1) = self.pts[i - 1], self.pts[i]
        return (la0 + f * (la1 - la0), lo0 + f * (lo1 - lo0))

    def project(self, lat: float, lon: float) -> tuple[float, float]:
        """Nearest point on the track: returns (distance_along_m, offset_m).

        `offset_m` is how far the queried point sits off the driven line. A large
        offset means the position is not on this route at all, which is how a
        stray fix from a dropout announces itself.

        Vectorised in v2 because the heat layers call it once per processed frame
        as well as once per event, and a Python loop over four thousand track
        points per frame is the difference between a live map and a slideshow.
        """
        if len(self.xy) < 2:
            return (0.0, float("inf"))
        px, py = self.frame.to_xy(lat, lon)
        A = self._A
        V = self._V
        L2 = self._L2
        t = np.clip(((px - A[:, 0]) * V[:, 0] + (py - A[:, 1]) * V[:, 1]) / L2, 0.0, 1.0)
        cx = A[:, 0] + t * V[:, 0]
        cy = A[:, 1] + t * V[:, 1]
        d = np.hypot(px - cx, py - cy)
        i = int(np.argmin(d))
        return (float(self._cum[i] + t[i] * self._seglen[i]), float(d[i]))

    def segment_of(self, lat: float, lon: float, max_offset_m: float = 60.0) -> Segment | None:
        along, off = self.project(lat, lon)
        if off > max_offset_m or not self.segments:
            return None
        i = min(len(self.segments) - 1, int(along // self.seg_len_m))
        return self.segments[i]

    # -- accumulation ------------------------------------------------------
    def add_defect(self, lat: float, lon: float, label: str,
                   severity: str | None = None) -> Segment | None:
        seg = self.segment_of(lat, lon)
        if seg is None:
            return None
        seg.defects += 1
        seg.severity_score += self.SEVERITY_WEIGHT.get(severity or "", 1.0)
        seg.labels[label] = seg.labels.get(label, 0) + 1
        return seg

    def add_observation(self, lat: float, lon: float, vehicles: int) -> None:
        """Attribute one processed frame, and what it saw, to a place.

        `frames` is counted whether or not anything was detected — a segment
        where the bus looked and saw nothing is evidence of an empty road, and
        leaving it out would turn "no traffic" into "no data".
        """
        seg = self.segment_of(lat, lon)
        if seg is None:
            return
        seg.frames += 1
        seg.vehicles += int(vehicles)

    def add_safety(self, lat: float, lon: float) -> None:
        """A vulnerable road user or an animal in the travelled way, at a place."""
        seg = self.segment_of(lat, lon)
        if seg is not None:
            seg.safety += 1

    def add_speed(self, lat: float, lon: float, speed_kmh: float) -> None:
        seg = self.segment_of(lat, lon)
        if seg is None:
            return
        seg._speeds.append(speed_kmh)
        seg.mean_speed_kmh = sum(seg._speeds) / len(seg._speeds)

    def worst(self, n: int = 10) -> list[dict]:
        rows = [s for s in self.segments if s.defects]
        rows.sort(key=lambda s: (-s.severity_score, -s.defects, s.index))
        return [s.as_dict() for s in rows[:n]]

    def slowest(self, n: int = 5, min_samples: int = 8) -> list[dict]:
        """Where the vehicle actually crawled — a bottleneck located in SPACE.

        v1 could only say *when* the feed was slow. With GPS this becomes a
        place on a map, which is the form a traffic engineer can act on.
        """
        rows = [s for s in self.segments if len(s._speeds) >= min_samples]
        rows.sort(key=lambda s: s.mean_speed_kmh)
        return [s.as_dict() for s in rows[:n]]

    # -- the heat layers ---------------------------------------------------
    # PS 26124 asks for congestion heat maps, defect-density maps and safety
    # risk maps. All four of its layers are the same operation over a different
    # measure, so there is one method and a `layer` name rather than four
    # near-identical ones — and the client picks the layer, so adding a fifth is
    # a dictionary entry rather than a new message type.
    LAYERS = {
        "density":  ("Traffic density", "vehicles per frame", False),
        "speed":    ("Congestion", "km/h — slower is hotter", True),
        "defects":  ("Road defects", "severity load", False),
        "safety":   ("Safety events", "VRU + animals", False),
    }

    def heat(self) -> dict:
        """Per-segment values for every layer, plus the range each one spans.

        Returned as parallel arrays rather than a list of objects: this goes out
        over the WebSocket several times a run, and the compact form is about a
        fifth of the size for exactly the same information.

        `inverted` marks a layer where LOW is bad — speed is the only one, and
        forgetting it is how a congestion map ends up colouring the free-flowing
        stretches red.
        """
        segs = self.segments
        out = {
            "seg_len_m": self.seg_len_m,
            "length_m": round(self.length_m, 1),
            "index":   [s.index for s in segs],
            "from_m":  [round(s.start_m, 1) for s in segs],
            "lat":     [round(s.lat, 6) for s in segs],
            "lon":     [round(s.lon, 6) for s in segs],
            "frames":  [s.frames for s in segs],
            "layers": {},
        }
        values = {
            "density": [round(s.vehicle_density, 3) for s in segs],
            "speed":   [round(s.mean_speed_kmh, 2) for s in segs],
            "defects": [round(s.severity_score, 2) for s in segs],
            "safety":  [s.safety for s in segs],
        }
        # WHICH SEGMENTS EACH LAYER ACTUALLY KNOWS ABOUT — and they differ.
        # Congestion comes from the GPS track, so it is known for every metre the
        # vehicle drove, whether or not the detector processed a frame there.
        # Density, defects and safety come from PROCESSED FRAMES, so they are known
        # only where the detector actually looked.
        #
        # Conflating the two would be the most tempting lie available here: it
        # would fill the map in and make a short clip look like a full survey. A
        # segment nobody visited is NOT a zero — it is unmeasured, and it is drawn
        # as unmeasured.
        seen_by_layer = {
            "density": [sg.frames > 0 for sg in segs],
            "speed":   [len(sg._speeds) > 0 for sg in segs],
            "defects": [sg.frames > 0 for sg in segs],
            "safety":  [sg.frames > 0 for sg in segs],
        }
        for key, (label, unit, inverted) in self.LAYERS.items():
            v = values[key]
            mask = seen_by_layer[key]
            seen = [x for x, ok in zip(v, mask) if ok]
            out["layers"][key] = {
                "label": label, "unit": unit, "inverted": inverted,
                "values": v,
                "seen": mask,
                "source": ("GPS track" if key == "speed" else "processed frames"),
                "min": round(min(seen), 3) if seen else 0.0,
                "max": round(max(seen), 3) if seen else 0.0,
                "measured": len(seen),
            }
        return out

    def as_dict(self) -> dict:
        return {"length_m": round(self.length_m, 1),
                "seg_len_m": self.seg_len_m,
                "n_segments": len(self.segments),
                "segments": [s.as_dict() for s in self.segments if s.defects or s._speeds]}


# ---------------------------------------------------------------------------
# 3. repeat-pass agreement
# ---------------------------------------------------------------------------
def repeat_pass_agreement(run_a: list[dict], run_b: list[dict],
                          radius_m: float = 15.0) -> dict:
    """How much do two passes over the same road agree about where the defects are?

    `run_a` / `run_b` are lists of dicts carrying at least `lat`, `lon`, and
    optionally `severity` and `label`.

    WHAT THIS NUMBER IS
        The fraction of defects reported on one pass that have a counterpart
        within `radius_m` on the other. `radius_m` must be at least as large as
        the position error: with 3-5 m of GPS noise on each pass plus a couple of
        seconds of sync uncertainty, 15 m is the smallest honest radius and it is
        stated wherever the figure is shown.

    WHAT IT IS NOT
        It is NOT precision, and it must never be reported as accuracy. Two
        passes can agree on a false positive — a drain cover looks like a hole
        from both directions. What it does measure is REPEATABILITY, and a
        detector that cannot find the same hole twice from opposite directions
        has a problem that no test-set mAP will reveal.

        A defect seen on only one pass is not automatically wrong either. The
        two runs are in opposite directions: a hole in the far lane may simply
        be out of frame one way round. That asymmetry is reported rather than
        averaged away.
    """
    if not run_a or not run_b:
        return {"ok": False, "reason": "need defects from both passes",
                "radius_m": radius_m}

    frame = LocalFrame.from_points([(d["lat"], d["lon"]) for d in (run_a + run_b)])
    axy = [frame.to_xy(d["lat"], d["lon"]) for d in run_a]
    bxy = [frame.to_xy(d["lat"], d["lon"]) for d in run_b]

    def matches(src, dst):
        out = []
        for i, (x, y) in enumerate(src):
            best_j, best_d = None, float("inf")
            for j, (u, v) in enumerate(dst):
                d = math.hypot(x - u, y - v)
                if d < best_d:
                    best_j, best_d = j, d
            out.append((i, best_j, best_d))
        return out

    ma = matches(axy, bxy)
    mb = matches(bxy, axy)
    a_hit = [m for m in ma if m[2] <= radius_m]
    b_hit = [m for m in mb if m[2] <= radius_m]

    confirmed = []
    for i, j, d in a_hit:
        e = dict(run_a[i])
        e["confirmed_by"] = run_b[j].get("event_id")
        e["separation_m"] = round(d, 1)
        confirmed.append(e)

    return {
        "ok": True,
        "radius_m": radius_m,
        "n_run_a": len(run_a),
        "n_run_b": len(run_b),
        "a_confirmed": len(a_hit),
        "b_confirmed": len(b_hit),
        "a_agreement_pct": round(100.0 * len(a_hit) / len(run_a), 1),
        "b_agreement_pct": round(100.0 * len(b_hit) / len(run_b), 1),
        "median_separation_m": round(
            sorted(m[2] for m in a_hit)[len(a_hit) // 2], 1) if a_hit else None,
        "confirmed": confirmed,
        "note": ("Agreement between two passes over the same corridor, not accuracy. "
                 "Two passes can agree on a false positive; and because the passes "
                 "run in opposite directions, a defect in the far lane may be "
                 "legitimately invisible one way round."),
    }
