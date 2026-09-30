"""
PROJECT_V3 — camera sources
===========================
Team The Road Runners · SIH 2026 · PS 26124 (Bharat Electronics Limited)

ONE INTERFACE, THREE KINDS OF CAMERA
    The whole point of v3 is that a recorded file and a live phone reach the
    analyser through the *same* door. A judge who watches the uploaded video and
    then watches the live feed is watching one pipeline, not two demos.

        MjpegCamera   a phone running IP Webcam on the hotspot  (live)
        DeviceCamera  a USB or virtual webcam on this laptop    (live)
        FileCamera    a recorded video, optionally one tile of  (recorded)
                      a 2x2 grid recording, played against the
                      wall clock so it behaves like a camera

    Every source keeps the SAME two things: the newest JPEG exactly as it
    arrived, and — only when the analyser asks — that JPEG decoded. Nothing is
    buffered. A camera hands you the newest frame; it does not hand you the
    backlog (the v2 lesson, carried forward).

WHY THE JPEG IS KEPT AS BYTES
    A phone already sends JPEG. Re-encoding it to show a preview would cost CPU
    we need for inference, so the preview stream is the phone's own bytes passed
    straight through. Decoding happens once per ANALYSED frame, not once per
    displayed frame.

BYTES ARE COUNTED AT THE DOOR
    Each source counts exactly how many bytes it received. That is what makes
    the bandwidth claim on the dashboard a measurement rather than an estimate:
    we know what four cameras actually cost, because we weighed them.
"""
from __future__ import annotations

import os
import socket
import threading
import time
import urllib.error
import urllib.request

import cv2
import numpy as np

# A corporate/campus proxy in the environment will happily intercept a request
# to 10.211.x.x and fail it. The cameras are on the hotspot, one hop away, so
# every request here goes direct. (Learned the hard way on the recorder.)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"


def normalise_url(addr: str) -> str:
    """Accept what a person actually types and return a stream URL.

    `192.168.43.5`            -> http://192.168.43.5:8080/video   (IP Webcam)
    `192.168.43.5:8081/live`  -> http://192.168.43.5:8081/live    (iOS apps)
    `http://.../video`        -> unchanged
    """
    a = (addr or "").strip()
    if not a:
        return ""
    if not a.startswith(("http://", "https://", "rtsp://")):
        a = "http://" + a
    tail = a.split("://", 1)[1]
    if "/" not in tail:                      # bare host or host:port
        if ":" not in tail:
            a += ":8080"
        a += "/video"
    return a


class BaseCamera:
    """Common state: newest frame, health, and an honest byte counter."""

    kind = "base"

    def __init__(self, cam_id: str, name: str, position: str):
        self.id = cam_id
        self.name = name or cam_id
        self.position = position or ""
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._frame: np.ndarray | None = None      # decoded, only if we decoded it
        self._seq = 0
        self._t = 0.0
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None
        # health / accounting
        self.status = "starting"        # starting | live | reconnecting | error | ended
        self.detail = ""
        self.bytes_in = 0
        self.frames_in = 0
        self.first_frame_at: float | None = None
        self.last_frame_at: float = 0.0
        self.reconnects = 0
        self._recent: list[float] = []             # arrival times, for fps_in
        self.analysed = 0                          # frames the scheduler took
        self.last_analysed_at: float = 0.0

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name=f"cam-{self.id}")
        self.thread.start()

    def stop(self):
        self._stop.set()

    # -- publishing --------------------------------------------------------
    def _publish_jpeg(self, jpeg: bytes, frame: np.ndarray | None = None):
        now = time.time()
        with self._lock:
            self._jpeg = jpeg
            self._frame = frame
            self._seq += 1
            self._t = now
        self.frames_in += 1
        self.bytes_in += len(jpeg)
        self.last_frame_at = now
        if self.first_frame_at is None:
            self.first_frame_at = now
        self._recent.append(now)
        if len(self._recent) > 60:
            self._recent = self._recent[-60:]
        self.status = "live"

    # -- reading -----------------------------------------------------------
    def latest_jpeg(self) -> tuple[bytes | None, int]:
        with self._lock:
            return self._jpeg, self._seq

    def latest_frame(self) -> tuple[np.ndarray | None, int, float]:
        """Decode-on-demand. Only the scheduler calls this, once per analysis."""
        with self._lock:
            jpeg, frame, seq, t = self._jpeg, self._frame, self._seq, self._t
        if frame is not None:
            return frame, seq, t
        if jpeg is None:
            return None, seq, t
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return img, seq, t

    # -- numbers for the dashboard ----------------------------------------
    def fps_in(self) -> float:
        r = [t for t in self._recent if time.time() - t < 4.0]
        if len(r) < 2:
            return 0.0
        return round((len(r) - 1) / max(r[-1] - r[0], 1e-6), 1)

    def kbps_in(self) -> float:
        if self.first_frame_at is None:
            return 0.0
        span = max(time.time() - self.first_frame_at, 1e-6)
        return round(self.bytes_in / span / 1024.0, 1)

    def health(self) -> dict:
        stale = (time.time() - self.last_frame_at) if self.last_frame_at else None
        return {
            "id": self.id, "name": self.name, "position": self.position,
            "kind": self.kind, "status": self.status, "detail": self.detail,
            "fps_in": self.fps_in(), "kbps_in": self.kbps_in(),
            "frames_in": self.frames_in, "bytes_in": self.bytes_in,
            "analysed": self.analysed,
            "stale_s": round(stale, 1) if stale is not None else None,
            "reconnects": self.reconnects,
            "since_analysis_s": (round(time.time() - self.last_analysed_at, 2)
                                 if self.last_analysed_at else None),
        }

    def _run(self):                                    # pragma: no cover
        raise NotImplementedError


class MjpegCamera(BaseCamera):
    """A phone running IP Webcam (Android) or an equivalent iOS app.

    The stream is `multipart/x-mixed-replace`: JPEG after JPEG, separated by a
    boundary. We do not parse the MIME headers — we find the JPEG start and end
    markers, which is what the recorder does and what survives the several
    slightly different servers these apps ship.
    """

    kind = "mjpeg"

    def __init__(self, cam_id, name, position, url: str, timeout: float = 8.0):
        super().__init__(cam_id, name, position)
        self.url = normalise_url(url)
        self.timeout = timeout

    def _run(self):
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self.detail = ""
                req = urllib.request.Request(self.url, headers={"User-Agent": "TRR-v3"})
                with OPENER.open(req, timeout=self.timeout) as r:
                    self.status = "live"
                    backoff = 1.0
                    buf = b""
                    while not self._stop.is_set():
                        chunk = r.read(8192)
                        if not chunk:
                            raise ConnectionError("stream closed by the camera")
                        buf += chunk
                        # Keep only the newest complete JPEG in the buffer: if we
                        # fall behind, the right thing is to skip frames, not to
                        # queue them.
                        while True:
                            s = buf.find(SOI)
                            e = buf.find(EOI, s + 2) if s >= 0 else -1
                            if s < 0 or e < 0:
                                break
                            jpeg = buf[s:e + 2]
                            buf = buf[e + 2:]
                            self._publish_jpeg(jpeg)
                        if len(buf) > 4_000_000:       # runaway garbage guard
                            buf = b""
            except Exception as exc:                    # noqa: BLE001
                if self._stop.is_set():
                    break
                self.status = "reconnecting"
                self.detail = _friendly_error(exc, self.url)
                self.reconnects += 1
                time.sleep(backoff)
                backoff = min(backoff * 1.8, 8.0)
        self.status = "ended"


class DeviceCamera(BaseCamera):
    """A USB webcam, or a virtual camera such as iVCam / Camo / OBS."""

    kind = "device"

    def __init__(self, cam_id, name, position, index: int, width=1280, height=720,
                 quality: int = 72):
        super().__init__(cam_id, name, position)
        self.index = int(index)
        self.width, self.height, self.quality = width, height, quality

    def _run(self):
        backend = cv2.CAP_DSHOW if os.name == "nt" else 0
        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            cap = cv2.VideoCapture(self.index)
        if not cap.isOpened():
            self.status = "error"
            self.detail = (f"Camera index {self.index} would not open. Close any app "
                           f"using it (Zoom, Teams, the Camera app) and try again.")
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        misses = 0
        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok:
                misses += 1
                if misses > 60:
                    self.status = "error"
                    self.detail = "The camera stopped returning frames."
                    break
                time.sleep(0.05)
                continue
            misses = 0
            ok_j, buf = cv2.imencode(".jpg", frame,
                                     [cv2.IMWRITE_JPEG_QUALITY, self.quality])
            if ok_j:
                self._publish_jpeg(buf.tobytes(), frame)
        cap.release()
        self.status = "ended"


def trim_letterbox(frame, thresh: int = 14):
    """Return (x, y, w, h) of the picture inside any black bars.

    A tile cut out of a 2x2 screen recording keeps the recording's black
    surround, and a black bar is not something anyone wants analysed: it wastes
    inference on nothing and — measured — it can even be mistaken for the
    vehicle's own bodywork, because it has no motion and never changes. So it is
    removed at the source, once, from the first frames.
    """
    import numpy as _np
    g = frame.max(axis=2) if frame.ndim == 3 else frame
    cols = _np.where(g.max(axis=0) > thresh)[0]
    rows = _np.where(g.max(axis=1) > thresh)[0]
    if cols.size < 8 or rows.size < 8:
        return None
    x0, x1 = int(cols[0]), int(cols[-1]) + 1
    y0, y1 = int(rows[0]), int(rows[-1]) + 1
    if (x1 - x0) * (y1 - y0) > 0.97 * g.size:
        return None                       # nothing worth trimming
    return x0, y0, x1 - x0, y1 - y0


class FileCamera(BaseCamera):
    """A recorded video played against the wall clock, so it behaves like a feed.

    `crop` is (x, y, w, h) in fractions of the frame. That is how one 2x2 screen
    recording becomes four cameras: the same file, four windows onto it. The
    combined recordings this project made are exactly that shape, so the demo
    that runs on a file and the demo that runs on four phones differ only in
    where the pixels come from.
    """

    kind = "file"

    def __init__(self, cam_id, name, position, path: str,
                 crop: tuple[float, float, float, float] | None = None,
                 loop: bool = True, quality: int = 72, preview_width: int = 640,
                 start_s: float = 0.0, stream_fps: float = 8.0):
        super().__init__(cam_id, name, position)
        self.path = path
        self.crop = crop
        self.loop = loop
        self.quality = quality
        self.preview_width = preview_width
        self.start_s = float(start_s or 0.0)
        # A PHONE DOES NOT SEND 30 FRAMES A SECOND, AND NEITHER SHOULD THIS.
        # The rig's cameras deliver roughly 6-10 fps over the hotspot, and
        # decoding a 30 fps file four times over would spend on decoding the CPU
        # the detector needs. So a file-backed camera is sampled at the same rate
        # a real one produces — which is also what makes the two modes
        # comparable rather than merely similar.
        self.stream_fps = float(stream_fps or 8.0)
        self.duration_s = 0.0
        self.t_video = 0.0

    def _run(self):
        cap = cv2.VideoCapture(self.path)
        if not cap.isOpened():
            self.status = "error"
            self.detail = f"Could not open {os.path.basename(self.path)}"
            return
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        self.duration_s = n / fps if n else 0.0
        if self.start_s > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(self.start_s * fps))
        t0 = time.time()
        base = self.start_s
        trim = None
        trim_checked = 0
        period = 1.0 / max(self.stream_fps, 0.5)
        next_pub = time.time()
        while not self._stop.is_set():
            elapsed = time.time() - t0
            want = int((base + elapsed) * fps)
            pos = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
            if want > pos + 1:
                # Behind the clock: skip forward rather than decode frames we are
                # about to throw away. `grab` reads without decoding, which is the
                # cheap way to do it; a long jump seeks instead. (v2's correction,
                # kept and made cheaper.)
                if want - pos > 45:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, want)
                else:
                    for _ in range(want - pos - 1):
                        if not cap.grab():
                            break
            ok, frame = cap.read()
            if not ok:
                if self.loop:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    t0 = time.time()
                    base = 0.0
                    continue
                self.status = "ended"
                self.detail = "End of file."
                break
            self.t_video = int(cap.get(cv2.CAP_PROP_POS_FRAMES)) / fps
            if self.crop:
                h, w = frame.shape[:2]
                x = int(self.crop[0] * w); y = int(self.crop[1] * h)
                cw = int(self.crop[2] * w); ch = int(self.crop[3] * h)
                frame = frame[max(0, y):y + ch, max(0, x):x + cw]
            if trim is None and trim_checked < 4:
                trim_checked += 1
                trim = trim_letterbox(frame)
            if trim:
                tx, ty, tw_, th_ = trim
                frame = frame[ty:ty + th_, tx:tx + tw_]
            if frame.shape[1] > self.preview_width:
                s = self.preview_width / frame.shape[1]
                frame = cv2.resize(frame, (self.preview_width,
                                           max(2, int(frame.shape[0] * s))))
            ok_j, buf = cv2.imencode(".jpg", frame,
                                     [cv2.IMWRITE_JPEG_QUALITY, self.quality])
            if ok_j:
                self._publish_jpeg(buf.tobytes(), frame)
            next_pub += period
            time.sleep(max(0.0, next_pub - time.time()))
            if next_pub < time.time() - 1.0:          # fell a long way behind
                next_pub = time.time()
        cap.release()

    def health(self) -> dict:
        h = super().health()
        h |= {"t_video_s": round(self.t_video, 1),
              "duration_s": round(self.duration_s, 1),
              "file": os.path.basename(self.path)}
        return h


class SharedFileReader:
    """ONE decoder for a 2x2 recording, feeding four cameras.

    THE BUG THIS FIXES
        The first version gave every tile its own `FileCamera`, so a 1.4 GB
        1920x1128 recording was opened and decoded FOUR TIMES in parallel — four
        full-resolution H.264 decodes, to produce four crops of the same frame.
        On a laptop already running the detector that is enough to starve the
        browser, and a starved tab is a tab the operating system reclaims: the
        dashboard drew for a few seconds, went white, reloaded, and did it again.

        One decode, four crops. The cameras downstream cannot tell the
        difference, which is the point — they are still four independent cameras
        with their own calibration and their own place in the schedule.
    """

    def __init__(self, path: str, loop: bool = True, start_s: float = 0.0,
                 stream_fps: float = 8.0, preview_width: int = 640,
                 quality: int = 72, end_s: float | None = None):
        self.path = path
        self.loop = loop
        self.start_s = float(start_s or 0.0)
        # The stretch of this recording where all four cameras are live. Play
        # outside it and a tile is black through no fault of the node.
        self.end_s = float(end_s) if end_s else None
        self.stream_fps = float(stream_fps or 8.0)
        self.preview_width = int(preview_width)
        self.quality = int(quality)
        self.cams: list["FileTileCamera"] = []
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.duration_s = 0.0
        self.t_video = 0.0

    def add(self, cam: "FileTileCamera"):
        self.cams.append(cam)
        cam.reader = self

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name="filereader")
        self.thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        cap = cv2.VideoCapture(self.path)
        if not cap.isOpened():
            for c in self.cams:
                c.status = "error"
                c.detail = f"Could not open {os.path.basename(self.path)}"
            return
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        self.duration_s = n / fps if n else 0.0
        if self.start_s > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(self.start_s * fps))
        period = 1.0 / max(self.stream_fps, 0.5)
        next_pub = time.time()
        t0 = time.time()
        base = self.start_s
        while not self._stop.is_set():
            elapsed = time.time() - t0
            want = int((base + elapsed) * fps)
            pos = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
            if want > pos + 1:
                if want - pos > 45:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, want)
                else:
                    for _ in range(want - pos - 1):
                        if not cap.grab():
                            break
            ok, frame = cap.read()
            past_window = (self.end_s is not None
                           and int(cap.get(cv2.CAP_PROP_POS_FRAMES)) / fps >= self.end_s)
            if not ok or past_window:
                if self.loop:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, int(self.start_s * fps))
                    t0 = time.time()
                    base = self.start_s
                    continue
                for c in self.cams:
                    c.status = "ended"
                    c.detail = ("End of the four-camera stretch." if past_window
                                else "End of file.")
                break
            self.t_video = int(cap.get(cv2.CAP_PROP_POS_FRAMES)) / fps
            h, w = frame.shape[:2]
            for c in self.cams:
                sub = frame
                if c.crop:
                    x = int(c.crop[0] * w); y = int(c.crop[1] * h)
                    cw = int(c.crop[2] * w); ch = int(c.crop[3] * h)
                    sub = frame[max(0, y):y + ch, max(0, x):x + cw]
                if c.trim is None and c.trim_checks < 4:
                    c.trim_checks += 1
                    c.trim = trim_letterbox(sub)
                if c.trim:
                    tx, ty, tw_, th_ = c.trim
                    sub = sub[ty:ty + th_, tx:tx + tw_]
                if sub.shape[1] > self.preview_width:
                    sc = self.preview_width / sub.shape[1]
                    sub = cv2.resize(sub, (self.preview_width,
                                           max(2, int(sub.shape[0] * sc))))
                ok_j, buf = cv2.imencode(".jpg", sub,
                                         [cv2.IMWRITE_JPEG_QUALITY, self.quality])
                if ok_j:
                    c.t_video = self.t_video
                    c._publish_jpeg(buf.tobytes(), sub)
            next_pub += period
            time.sleep(max(0.0, next_pub - time.time()))
            if next_pub < time.time() - 1.0:
                next_pub = time.time()
        cap.release()


class FileTileCamera(BaseCamera):
    """One window onto a shared recording. The decoding happens once, elsewhere."""

    kind = "file"

    def __init__(self, cam_id, name, position, path, crop=None):
        super().__init__(cam_id, name, position)
        self.path = path
        self.crop = crop
        self.reader: SharedFileReader | None = None
        self.trim = None
        self.trim_checks = 0
        self.t_video = 0.0

    def start(self):
        self.status = "starting"      # the reader publishes; nothing to run here

    def stop(self):
        self._stop.set()

    def health(self) -> dict:
        h = super().health()
        h |= {"t_video_s": round(self.t_video, 1),
              "duration_s": round(self.reader.duration_s if self.reader else 0, 1),
              "file": os.path.basename(self.path)}
        return h


def build_cameras(specs: list[dict]):
    """Build every camera for a run, sharing one decoder per recorded file.

    Returns (cameras, readers). Live cameras are unaffected.
    """
    cams, readers = [], []
    by_path: dict[str, SharedFileReader] = {}
    for spec in specs:
        if spec.get("kind") == "file":
            path = spec["path"]
            crop = spec.get("crop")
            if isinstance(crop, str):
                crop = QUAD_TILES.get(crop)
            reader = by_path.get(path)
            if reader is None:
                reader = SharedFileReader(path, loop=bool(spec.get("loop", True)),
                                          start_s=float(spec.get("start_s", 0.0)),
                                          end_s=spec.get("end_s"))
                by_path[path] = reader
                readers.append(reader)
            cam = FileTileCamera(spec.get("id") or "cam", spec.get("name") or "",
                                 spec.get("position", ""), path,
                                 tuple(crop) if crop else None)
            reader.add(cam)
            cams.append(cam)
        else:
            cams.append(build_camera(spec))
    return cams, readers


# Tile geometry of the 2x2 Quad View screen recordings this project produced.
# Measured on the recordings themselves (1920x1128), expressed as fractions so a
# differently sized recording of the same layout still works.
QUAD_TILES = {
    "cam1": (0.005, 0.218, 0.492, 0.363),
    "cam2": (0.501, 0.218, 0.492, 0.363),
    "cam3": (0.005, 0.601, 0.492, 0.363),
    "cam4": (0.501, 0.601, 0.492, 0.363),
}


def _friendly_error(exc: Exception, url: str) -> str:
    """Say what to DO, not what went wrong in Python."""
    s = str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        return f"The camera answered HTTP {exc.code}. Check the path in {url}."
    if isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in s:
        return ("No answer. The phone and this laptop must be on the SAME hotspot, "
                "and IP Webcam must be running with the server started.")
    if isinstance(exc, ConnectionRefusedError) or "refused" in s:
        return "Connection refused — the camera app is not serving on that port."
    if "unreachable" in s or "No route" in s:
        return "No route to that address. Re-read the IP off the phone; it changes on rejoin."
    return s[:160]


def build_camera(spec: dict) -> BaseCamera:
    """Make a camera from one row of the dashboard's source table."""
    kind = spec.get("kind", "mjpeg")
    cid = spec.get("id") or "cam"
    name = spec.get("name") or cid
    pos = spec.get("position", "")
    if kind == "mjpeg":
        return MjpegCamera(cid, name, pos, spec["url"])
    if kind == "device":
        return DeviceCamera(cid, name, pos, int(spec.get("index", 0)))
    if kind == "file":
        crop = spec.get("crop")
        if isinstance(crop, str):
            crop = QUAD_TILES.get(crop)
        return FileCamera(cid, name, pos, spec["path"],
                          crop=tuple(crop) if crop else None,
                          loop=bool(spec.get("loop", True)),
                          start_s=float(spec.get("start_s", 0.0)))
    raise ValueError(f"unknown camera kind: {kind}")
