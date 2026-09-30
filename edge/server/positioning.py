"""
PROJECT_V3 — where the vehicle was
==================================
Team The Road Runners · SIH 2026 · PS 26124

A defect ledger a municipality cannot navigate to is a list, not a work order.
v2 said that and proved it on a recorded GPS capture. v3 has to answer the same
question on a rig of four phones, so position comes from one of three places —
and the dashboard always says WHICH, because a coordinate whose provenance is
unknown is worth less than no coordinate at all.

    phone     one phone's own GPS, read live from IP Webcam's sensor endpoint
    sidecar   a GPS track recorded alongside the video (the v2 CSV, or a GPX)
    none      no position feed. Events carry time and camera, and the map says so

THE RULE THAT DOES NOT BEND (carried from v2)
    An event is geo-tagged only from a REAL fix. A position interpolated across
    a dropout is drawn on the map as a dotted line if it helps the eye, but it
    never becomes the coordinate of a pothole. Sending a crew 50 m wrong is
    worse than sending them nothing.

ACCURACY, STATED ONCE
    A phone GPS gives roughly 3-8 m in the open and much worse between tall
    buildings. That is STREET-LEVEL, not lane-level, and nothing downstream may
    claim otherwise. The sentence travels with the data in `accuracy_note`.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

from telemetry import Fix, Telemetry, find_sidecar, haversine_m   # v2 code, reused

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PHONE_ACCURACY_NOTE = (
    "Phone GNSS read live from the camera app, roughly 3-8 m in the open and "
    "worse between buildings. Street-level, not lane-level."
)
SIDECAR_ACCURACY_NOTE = (
    "Recorded GPS track aligned to the video clock. Street-level, not lane-level."
)

# How stale a phone fix may be before it stops being trustworthy. Same 3.5 s the
# v2 sidecar rule uses, so the two sources are judged by one standard.
MAX_FIX_AGE_S = 3.5


def _mk_fix(t_run, lat, lon, ele=0.0, speed=0.0, bearing=float("nan"),
            gap=0.0, interpolated=0) -> Fix:
    return Fix(round(t_run, 2), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               float(lat), float(lon), float(ele), float(speed), bearing,
               float(gap), int(interpolated))


class BaseSource:
    kind = "none"
    accuracy_note = ""

    def __init__(self):
        self.status = "idle"
        self.detail = ""
        self.track: list[list[float]] = []      # [lat, lon] real fixes, thinned
        self.last: Fix | None = None
        self.samples = 0
        self.real = 0

    def start(self):                            # pragma: no cover - trivial
        pass

    def stop(self):                             # pragma: no cover - trivial
        pass

    def fix_at(self, t_run: float, t_video: float | None = None) -> Fix | None:
        return None

    def _remember(self, fix: Fix):
        self.last = fix
        self.samples += 1
        if fix.trustworthy:
            self.real += 1
            if (not self.track or
                    haversine_m(self.track[-1][0], self.track[-1][1],
                                fix.lat, fix.lon) > 2.0):
                self.track.append([round(fix.lat, 6), round(fix.lon, 6)])
                if len(self.track) > 4000:
                    self.track = self.track[::2]

    def summary(self) -> dict:
        d = {"kind": self.kind, "status": self.status, "detail": self.detail,
             "samples": self.samples, "real_fixes": self.real,
             "accuracy_note": self.accuracy_note,
             "track_points": len(self.track)}
        if self.last is not None:
            d["last"] = self.last.as_dict()
        return d


class NoPosition(BaseSource):
    """The honest default. Says what would make position available."""

    kind = "none"

    def __init__(self, reason: str = ""):
        super().__init__()
        self.status = "absent"
        self.detail = reason or (
            "No position feed on this run. Turn on Location in IP Webcam on one "
            "phone and give its address here, or attach a recorded GPS track to a "
            "recorded drive. Events still carry time and camera.")


class PhoneGpsSource(BaseSource):
    """One phone's GNSS, polled from IP Webcam's sensor endpoint.

    IP Webcam publishes its sensors as JSON. The GPS block looks like

        {"gps": {"data": [[1758712345678, [20.2961, 85.8245, 25.0]]]}}

    and the app only fills it in when Location is enabled. Several builds differ
    in the details, so the parser takes the last row it can find rather than
    insisting on one shape, and reports plainly when there is nothing to read.
    """

    kind = "phone"
    accuracy_note = PHONE_ACCURACY_NOTE

    def __init__(self, host: str, period_s: float = 1.0):
        super().__init__()
        self.host = _host_of(host)
        self.period = float(period_s)
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None
        self._last_real_t = 0.0
        self._t0 = time.time()

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        self._t0 = time.time()
        self.thread = threading.Thread(target=self._run, daemon=True, name="gps")
        self.thread.start()

    def stop(self):
        self._stop.set()

    # -- polling -----------------------------------------------------------
    def _urls(self):
        return [f"http://{self.host}/sensors.json?sense=gps",
                f"http://{self.host}/sensors.json",
                f"http://{self.host}/gps.json"]

    def _run(self):
        misses = 0
        while not self._stop.is_set():
            got = False
            for url in self._urls():
                try:
                    with OPENER.open(url, timeout=3.0) as r:
                        payload = json.loads(r.read().decode("utf-8", "replace"))
                except Exception:                            # noqa: BLE001
                    continue
                pos = _parse_sensor_json(payload)
                if pos:
                    lat, lon, ele, speed = pos
                    t = time.time()
                    self._last_real_t = t
                    self.status = "live"
                    self.detail = ""
                    self._remember(_mk_fix(t - self._t0, lat, lon, ele, speed))
                    got = True
                    break
            if not got:
                misses += 1
                if misses >= 3:
                    self.status = "waiting"
                    self.detail = ("The phone is reachable but is not publishing a "
                                   "position. In IP Webcam, switch ON 'Location data' "
                                   "(Data logging → Location) and give the phone a "
                                   "minute of clear sky.")
            time.sleep(self.period)
        self.status = "stopped"

    def fix_at(self, t_run: float, t_video: float | None = None) -> Fix | None:
        """The newest fix, marked untrustworthy once it goes stale."""
        if self.last is None:
            return None
        age = time.time() - self._last_real_t
        if age > MAX_FIX_AGE_S:
            stale = _mk_fix(t_run, self.last.lat, self.last.lon, self.last.ele_m,
                            self.last.speed_kmh, self.last.bearing_deg,
                            gap=age, interpolated=1)
            return stale
        return _mk_fix(t_run, self.last.lat, self.last.lon, self.last.ele_m,
                       self.last.speed_kmh, self.last.bearing_deg, gap=age)


class SidecarSource(BaseSource):
    """A GPS track recorded alongside a video: the v2 CSV, or a GPX.

    For a recorded run the position of an event is the position of the vehicle at
    that moment IN THE VIDEO, not at the wall clock — so this source is asked by
    video time, and the file-backed cameras report theirs.
    """

    kind = "sidecar"
    accuracy_note = SIDECAR_ACCURACY_NOTE

    def __init__(self, path: str, offset_s: float = 0.0):
        super().__init__()
        self.path = path
        self.offset = float(offset_s)
        self.tel: Telemetry | None = None
        try:
            if path.lower().endswith(".gpx"):
                self.tel = _telemetry_from_gpx(path, offset_s)
            else:
                self.tel = Telemetry(path, video_start_offset_s=offset_s)
            self.status = "loaded"
            self.detail = ""
            good, interp = self.tel.coverage()
            self.real = good
            self.samples = len(self.tel.rows)
            self.track = self.tel.route(every=3, real_only=True)
        except Exception as exc:                              # noqa: BLE001
            self.status = "error"
            self.detail = f"Could not read {os.path.basename(path)}: {exc}"

    def fix_at(self, t_run: float, t_video: float | None = None) -> Fix | None:
        if self.tel is None:
            return None
        fix = self.tel.at_time(t_video if t_video is not None else t_run)
        if fix is not None:
            self.last = fix
        return fix

    def summary(self) -> dict:
        d = super().summary()
        if self.tel is not None:
            d |= {"file": os.path.basename(self.path), "sidecar": self.tel.summary()}
        return d


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _host_of(addr: str) -> str:
    """Accept an address in any of the shapes the operator might paste."""
    a = (addr or "").strip()
    a = a.replace("http://", "").replace("https://", "")
    a = a.split("/")[0]
    if ":" not in a:
        a += ":8080"
    return a


def _parse_sensor_json(payload) -> tuple[float, float, float, float] | None:
    """Pull (lat, lon, ele, speed) out of whatever the camera app returned.

    Deliberately forgiving: several IP Webcam builds and several iOS apps all
    publish 'the GPS' in slightly different shapes, and a demo should not fail
    because a key was renamed between versions.
    """
    if not isinstance(payload, dict):
        return None

    # IP Webcam: {"gps": {"data": [[t_ms, [lat, lon, alt]], ...]}}
    for key in ("gps", "gps_location", "location"):
        blk = payload.get(key)
        if isinstance(blk, dict) and isinstance(blk.get("data"), list) and blk["data"]:
            row = blk["data"][-1]
            vals = row[1] if (isinstance(row, list) and len(row) > 1
                              and isinstance(row[1], list)) else row
            if isinstance(vals, list) and len(vals) >= 2:
                lat, lon = float(vals[0]), float(vals[1])
                ele = float(vals[2]) if len(vals) > 2 else 0.0
                if _plausible(lat, lon):
                    return lat, lon, ele, 0.0

    # Flat shapes: {"lat":..., "lon":...} or {"latitude":..., "longitude":...}
    for la, lo in (("lat", "lon"), ("latitude", "longitude"), ("lat", "lng")):
        if la in payload and lo in payload:
            try:
                lat, lon = float(payload[la]), float(payload[lo])
            except (TypeError, ValueError):
                continue
            if _plausible(lat, lon):
                return (lat, lon, float(payload.get("altitude", 0) or 0),
                        float(payload.get("speed", 0) or 0))

    # Nested once (some builds wrap everything in "sensors")
    for v in payload.values():
        if isinstance(v, dict):
            got = _parse_sensor_json(v)
            if got:
                return got
    return None


def _plausible(lat, lon) -> bool:
    return -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0)


def _telemetry_from_gpx(path: str, offset_s: float = 0.0) -> Telemetry:
    """Read a GPX into the same object the CSV path produces.

    The rest of the system then cannot tell the two apart, which is the point:
    one code path for position, whatever recorded it.
    """
    tree = ET.parse(path)
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    pts = tree.getroot().findall(".//g:trkpt", ns) or tree.getroot().findall(".//trkpt")
    rows, ts = [], []
    for p in pts:
        lat = float(p.get("lat")); lon = float(p.get("lon"))
        t_el = p.find("g:time", ns) if p.find("g:time", ns) is not None else p.find("time")
        ele_el = p.find("g:ele", ns) if p.find("g:ele", ns) is not None else p.find("ele")
        t = _gpx_time(t_el.text) if t_el is not None and t_el.text else None
        ts.append(t)
        rows.append((lat, lon, float(ele_el.text) if ele_el is not None and ele_el.text else 0.0))
    if not rows:
        raise ValueError("no track points in this GPX")
    t0 = next((t for t in ts if t is not None), 0.0)

    tel = Telemetry.__new__(Telemetry)          # build one without a CSV
    tel.csv_path = path
    tel.source_fps = 30.0
    tel.offset = float(offset_s)
    tel.t = []
    tel.rows = []
    prev = None
    for (lat, lon, ele), t in zip(rows, ts):
        t_video = (t - t0) if t is not None else (len(tel.rows) * 1.0)
        speed = 0.0
        if prev is not None and t is not None and prev[2] is not None and t > prev[2]:
            speed = haversine_m(prev[0], prev[1], lat, lon) / (t - prev[2]) * 3.6
        gap = 0.0 if (prev is None or t is None or prev[2] is None) else (t - prev[2])
        tel.t.append(t_video)
        tel.rows.append(_mk_fix(t_video, lat, lon, ele, speed, float("nan"),
                                gap, 1 if gap > MAX_FIX_AGE_S else 0))
        prev = (lat, lon, t)
    return tel


def _gpx_time(s: str) -> float:
    s = s.strip().replace("Z", "+00:00")
    try:
        import datetime as _dt
        return _dt.datetime.fromisoformat(s).timestamp()
    except Exception:                                        # noqa: BLE001
        return 0.0


def build_source(spec: dict | None) -> BaseSource:
    """Make the position source the operator asked for, or the honest default."""
    if not spec or spec.get("kind") in (None, "", "none"):
        return NoPosition()
    kind = spec["kind"]
    if kind == "phone":
        src = PhoneGpsSource(spec.get("host") or spec.get("url", ""),
                             float(spec.get("period_s", 1.0)))
        src.start()
        return src
    if kind == "sidecar":
        path = spec.get("path") or ""
        if not path and spec.get("video"):
            path = find_sidecar(spec["video"], []) or ""
        if not path or not os.path.exists(path):
            return NoPosition("No GPS track was found next to this video. "
                              "Pick one, or run without position.")
        return SidecarSource(path, float(spec.get("offset_s", 0.0)))
    return NoPosition()


def probe_phone_gps(host: str, timeout: float = 4.0) -> dict:
    """One-shot check the operator can press before depending on it."""
    h = _host_of(host)
    for url in (f"http://{h}/sensors.json?sense=gps", f"http://{h}/sensors.json",
                f"http://{h}/gps.json"):
        try:
            with OPENER.open(url, timeout=timeout) as r:
                payload = json.loads(r.read().decode("utf-8", "replace"))
        except Exception as exc:                              # noqa: BLE001
            last = str(exc)
            continue
        pos = _parse_sensor_json(payload)
        if pos:
            return {"ok": True, "url": url, "lat": round(pos[0], 6),
                    "lon": round(pos[1], 6), "ele_m": round(pos[2], 1)}
        return {"ok": False, "url": url,
                "reason": ("The phone answered but published no position. In IP "
                           "Webcam turn ON 'Location data' and wait for a fix.")}
    return {"ok": False, "reason": (
        "No sensor endpoint answered on that phone. IP Webcam publishes one at "
        "/sensors.json when Location data is switched on.")}
