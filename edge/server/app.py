"""
PROJECT_V3 — local command dashboard for a FOUR-CAMERA edge node
================================================================
Team The Road Runners · SIH 2026 · PS 26124 (Bharat Electronics Limited)

WHAT IT IS
    A small local web application: FastAPI + Uvicorn on 127.0.0.1, one WebSocket
    carrying previews, detections and events, and a single-page frontend with no
    external dependency of any kind. Nothing leaves the machine and nothing is
    fetched from a CDN, so it runs with the network cable pulled out — which is
    the only state a demo venue guarantees.

TWO SOURCES, ONE PIPELINE
    LIVE      four phones on the hotspot, each serving MJPEG
    RECORDED  a video file — and if it is one of our 2x2 grid recordings, it is
              split into four cameras, so the recorded demo exercises exactly the
              same four-camera scheduler as the live one.

    That is the design claim worth making out loud: the file mode is not a
    different program with a video player in it. The frames enter the same
    scheduler through the same interface; only the door changes.

PORTS
    8080  TheRoadRunners_v1 (internal round)      — untouched
    8081  Project_v2, geolocated single camera    — untouched
    8082  this
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import threading
import time
import urllib.request

from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi import Response                                   # noqa: E402
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, StreamingResponse)
from fastapi.staticfiles import StaticFiles

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (HERE, os.path.join(ROOT, "_p2src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from cameras import OPENER, QUAD_TILES, normalise_url         # noqa: E402
from capabilities import (ATTRIBUTION, CAPABILITIES, HONEST_LIMITS,  # noqa: E402
                          SCHEDULER_NOTE, TEST_METRICS)
import capabilities as _caps                                  # noqa: E402

# The two models added for PS 26124 carry their own measured numbers. They are
# optional so an older capabilities.py still starts the node.
EXTRA_METRICS = [m for m in (
    ("Waterlogging", getattr(_caps, "WATERLOGGING_METRICS", None)),
    ("Number plates", getattr(_caps, "PLATE_METRICS", None))) if m[1]]
from config import Config                                     # noqa: E402
from multicam import MultiCamEngine                           # noqa: E402
from perception import Models, SEVERITY                       # noqa: E402
from positioning import probe_phone_gps                       # noqa: E402
from telemetry import find_sidecar                            # noqa: E402
from report_v3 import build_report                            # noqa: E402

WEB = os.path.join(ROOT, "web")
UPLOADS = os.path.join(ROOT, "uploads")
SESSIONS = os.path.join(ROOT, "sessions")
OUTPUT = os.path.join(ROOT, "output")
for p in (UPLOADS, SESSIONS, OUTPUT):
    os.makedirs(p, exist_ok=True)

CAMERAS_JSON = os.path.join(ROOT, "cameras.json")
# ...\SIH_2026\Stage_3_SIH2026_Build\Project_v3_MultiCam\dashboard -> SIH_2026
SIH_ROOT = os.path.abspath(os.path.join(ROOT, "..", "..", ".."))

app = FastAPI(title="TheRoadRunners_v3")

STATE: dict = {"engine": None, "queue": None, "models": None,
               "loading": False, "load_error": None, "last_finished": None}


# ---------------------------------------------------------------------------
# sources the operator can choose from
# ---------------------------------------------------------------------------
def _video_candidates() -> list[dict]:
    """Every video on this machine we can offer, with what it actually is.

    A missing file is simply absent from the list rather than a button that
    fails when pressed.
    """
    v3 = os.path.abspath(os.path.join(ROOT, ".."))          # Project_v3_MultiCam
    stage1 = os.path.join(SIH_ROOT, "Stage_1_SIH2026_KIIT")
    out = []

    def add(path, label, layout, note, start_s=0.0, end_s=None):
        """`start_s`/`end_s` mark the stretch where all four cameras are live.

        The combined drives carry the fourth camera only for the minutes the
        iPhone was recording. For a demo that is a trap — start at zero and the
        fourth tile is black — so each file says where its four-camera stretch
        is, the dashboard starts there, and the playback loops inside it.
        """
        if os.path.exists(path):
            out.append({"path": path, "label": label, "layout": layout,
                        "note": note, "bytes": os.path.getsize(path),
                        "start_s": start_s, "end_s": end_s})

    fc = os.path.join(v3, "Four_Camera_Combine", "output")
    add(os.path.join(fc, "Morning_4Camera_Combined.mp4"),
        "Morning drive — 4 cameras", "quad",
        "19 Sep 2026. All four cameras are live from 4:44 to 24:48; the dashboard "
        "starts and loops inside that stretch.", start_s=290.0, end_s=1486.0)
    add(os.path.join(fc, "Night_4Camera_Combined.mp4"),
        "Night drive — 4 cameras", "quad",
        "18 Sep 2026. All four cameras are live from 4:54 to 11:35; the dashboard "
        "starts and loops inside that stretch. Dark, and the fourth camera is "
        "stabilised handheld footage.", start_s=300.0, end_s=693.0)
    dem = os.path.join(v3, "Four_Camera_Combine", "demo_segments")
    add(os.path.join(dem, "Morning_4Cam_AllActive.mp4"),
        "Morning — four cameras throughout", "quad",
        "The morning drive trimmed to the stretch where all four cameras are live. "
        "Nothing to set: start it anywhere.")
    add(os.path.join(dem, "Night_4Cam_AllActive.mp4"),
        "Night — four cameras throughout", "quad",
        "The night drive trimmed to the stretch where all four cameras are live.")
    hz = os.path.join(v3, "Four_Camera_Combine", "hazard_highlights")
    add(os.path.join(hz, "Morning_Hazard_Highlights.mp4"),
        "Morning — hazard highlights", "quad",
        "Only the stretches that show a hazard. Short, and busy with events.")
    add(os.path.join(hz, "Rain_Hazard_Highlights.mp4"),
        "Rain — hazard highlights (3 cameras)", "quad",
        "Waterlogging. The fourth tile is empty on this drive — the dashboard will say so.")
    add(os.path.join(stage1, "Datasets", "1_Kanjhawla_Road_Delhi.mkv"),
        "Kanjhawla Road, Delhi — single camera", "single",
        "The measured baseline clip from Project_v1. One camera, bike POV.")
    add(os.path.join(stage1, "Project_v2", "GPS-Dataset", "_testclip",
                     "n3_clip_352_366.mp4"),
        "Varanasi night, 14 s clip — single camera", "single",
        "Project_v2's quick demo clip: animal, pothole and VRU in fourteen seconds.")
    add(os.path.join(stage1, "Project_v2", "GPS-Dataset", "naini-3_gps.MP4"),
        "Varanasi night, Naini run 3 — single camera", "single",
        "Project_v2's monsoon night capture. Geolocated in v2; here it is one camera.")
    add(os.path.join(stage1, "Datasets", "Video Datatset",
                     "2_Varanasi_Coaching_road.MOV"),
        "Varanasi coaching road, night — single camera", "single",
        "The clip with NO part of the host vehicle in frame — the ego gate should "
        "report that it found none.")
    for f in sorted(os.listdir(UPLOADS)):
        if f.lower().endswith((".mp4", ".mkv", ".mov", ".avi", ".webm")):
            add(os.path.join(UPLOADS, f), f"Uploaded — {f}", "single",
                "Uploaded by the operator. Choose the layout before starting.")
    return out


def _saved_cameras() -> list[dict]:
    if os.path.exists(CAMERAS_JSON):
        try:
            with open(CAMERAS_JSON, encoding="utf-8") as f:
                return json.load(f).get("cameras", [])
        except Exception:                                   # noqa: BLE001
            pass
    return [
        {"id": "cam1", "name": "Camera 1", "position": "Front", "kind": "mjpeg", "url": ""},
        {"id": "cam2", "name": "Camera 2", "position": "Rear", "kind": "mjpeg", "url": ""},
        {"id": "cam3", "name": "Camera 3", "position": "Left", "kind": "mjpeg", "url": ""},
        {"id": "cam4", "name": "Camera 4", "position": "Right", "kind": "mjpeg", "url": ""},
    ]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


app.mount("/web", StaticFiles(directory=WEB), name="web")


def _blur_jpeg(jpeg: bytes, boxes) -> bytes | None:
    """Decode, blur the given fractional boxes, re-encode. Returns None on any
    failure, so a preview never goes dark because redaction had a bad frame."""
    try:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        h, w = img.shape[:2]
        touched = False
        for bx, by, bw, bh in boxes:
            x0, y0 = int(bx * w), int(by * h)
            x1, y1 = int((bx + bw) * w), int((by + bh) * h)
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(w, x1), min(h, y1)
            if x1 <= x0 or y1 <= y0:
                continue
            k = max(3, (min(x1 - x0, y1 - y0) // 2) | 1)
            img[y0:y1, x0:x1] = cv2.GaussianBlur(img[y0:y1, x0:x1], (k, k), 0)
            touched = True
        if not touched:
            return None
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        return buf.tobytes() if ok else None
    except Exception:
        return None


@app.get("/api/stream/{cam_id}")
async def stream(cam_id: str):
    """The camera's own JPEGs — plates blurred — as an MJPEG stream.

    Previews used to travel base64-encoded over the WebSocket, which made the
    page parse about 1.3 MB of JSON every second and — on a laptop already busy
    with the detector — was enough for Chrome to reclaim the tab. An <img>
    pointed at this endpoint costs the page nothing: the decode happens in the
    browser's own pipeline, the bytes are the phone's original JPEGs, and a slow
    client simply gets fewer frames.

    The one thing that is NOT passthrough is number plates. The detector only
    reaches each camera about once a second, so the preview is blurred from the
    union of the last few plate positions, grown a little to cover the motion
    between them. That is best-effort, and the limits panel says so; what is
    STORED or SENT is blurred from the frame it was detected in, which is not.
    """
    eng = STATE["engine"]
    if eng is None or cam_id not in eng.cameras:
        return JSONResponse({"ok": False, "reason": "no such camera"}, 404)
    cam = eng.cameras[cam_id]
    boundary = "trrframe"

    async def gen():
        last = -1
        idle = 0
        while True:
            e = STATE["engine"]
            if e is None or cam_id not in e.cameras:
                return
            jpeg, seq = e.cameras[cam_id].latest_jpeg()
            if jpeg is None or seq == last:
                idle += 1
                if idle > 600:                   # ~20 s with nothing: let go
                    return
                await asyncio.sleep(0.033)
                continue
            idle = 0
            last = seq
            try:
                boxes = e.plate_boxes(cam_id)
            except Exception:
                boxes = []
            if boxes:
                # Decode/blur/encode is CPU work; on the event loop it starves
                # the other three camera streams, which then sit on "waiting for
                # the first frame". Hand it to a thread.
                blurred = await asyncio.to_thread(_blur_jpeg, jpeg, boxes)
                if blurred is not None:
                    jpeg = blurred
            yield (f"--{boundary}\r\nContent-Type: image/jpeg\r\n"
                   f"Content-Length: {len(jpeg)}\r\n\r\n").encode() + jpeg + b"\r\n"
            await asyncio.sleep(0.02)

    return StreamingResponse(gen(), media_type=f"multipart/x-mixed-replace; boundary={boundary}",
                             headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/api/vocab")
def vocab():
    return {"severity": SEVERITY, "capabilities": CAPABILITIES,
            "limits": HONEST_LIMITS, "metrics": TEST_METRICS,
            "metrics_extra": [{"name": n, **m} for n, m in EXTRA_METRICS],
            "attribution": ATTRIBUTION, "scheduler_note": SCHEDULER_NOTE,
            "quad_tiles": QUAD_TILES}


@app.get("/api/sources")
def sources():
    return {"videos": _video_candidates(), "cameras": _saved_cameras(),
            "uploads_dir": UPLOADS}


@app.get("/api/sidecars")
def sidecars(video: str = ""):
    """GPS tracks that could belong to this video.

    The operator should not have to hunt for a sidecar, and should never be
    offered one that does not exist. We look next to the video, in the capture
    kit's own folders, and report exactly what we found.
    """
    out, seen = [], set()

    def add(path, why):
        p = os.path.abspath(path)
        if p in seen or not os.path.exists(p):
            return
        seen.add(p)
        out.append({"path": p, "name": os.path.basename(p), "why": why,
                    "bytes": os.path.getsize(p)})

    if video:
        auto = find_sidecar(video, [])
        if auto:
            add(auto, "matched to this video by name")
        vdir = os.path.dirname(video)
        for d in (vdir, os.path.join(vdir, "_kit"), os.path.dirname(vdir)):
            if os.path.isdir(d):
                for f in sorted(os.listdir(d)):
                    if f.lower().endswith((".gpx", ".csv")):
                        add(os.path.join(d, f), f"found in {os.path.basename(d) or d}")
    kit = os.path.join(SIH_ROOT, "Stage_1_SIH2026_KIIT", "Project_v2",
                       "GPS-Dataset", "_kit")
    if os.path.isdir(kit):
        for f in sorted(os.listdir(kit)):
            if f.lower().endswith((".gpx", ".csv")):
                add(os.path.join(kit, f), "Project_v2 capture kit")
    return {"sidecars": out}


@app.post("/api/probe_gps")
async def probe_gps(payload: dict):
    return probe_phone_gps(payload.get("host", ""))


@app.post("/api/cameras")
async def save_cameras(payload: dict):
    with open(CAMERAS_JSON, "w", encoding="utf-8") as f:
        json.dump({"cameras": payload.get("cameras", []),
                   "saved": time.strftime("%Y-%m-%dT%H:%M:%S")}, f, indent=2)
    return {"ok": True}


@app.post("/api/probe")
async def probe(payload: dict):
    """Fetch ONE frame from a camera and say what came back.

    This exists because the alternative is starting a four-camera run and
    guessing which phone is wrong. The answer says what to do, not what went
    wrong in Python.
    """
    url = normalise_url(payload.get("url", ""))
    if not url:
        return {"ok": False, "reason": "No address given."}
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TRR-v3"})
        with OPENER.open(req, timeout=float(payload.get("timeout", 4.0))) as r:
            ctype = r.headers.get("Content-Type", "")
            buf = b""
            deadline = time.time() + 4.0
            while time.time() < deadline:
                chunk = r.read(4096)
                if not chunk:
                    break
                buf += chunk
                s = buf.find(b"\xff\xd8")
                e = buf.find(b"\xff\xd9", s + 2) if s >= 0 else -1
                if s >= 0 and e > 0:
                    jpeg = buf[s:e + 2]
                    import cv2
                    import numpy as np
                    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
                    h, w = (img.shape[:2] if img is not None else (0, 0))
                    return {"ok": True, "url": url, "ms": int((time.time() - t0) * 1000),
                            "content_type": ctype, "frame_bytes": len(jpeg),
                            "width": int(w), "height": int(h)}
            return {"ok": False, "url": url,
                    "reason": ("Connected, but no JPEG arrived. If this is an iPhone app, "
                               "the path is usually different — try /live or /video.")}
    except Exception as exc:                                # noqa: BLE001
        from cameras import _friendly_error
        return {"ok": False, "url": url, "reason": _friendly_error(exc, url)}


@app.post("/api/probe_device")
async def probe_device(payload: dict):
    """Open a webcam attached to THIS laptop and say what came back.

    A laptop running the dashboard is also a camera — its own webcam needs no
    server, no address and no hotspot hop. This is the same one-frame check the
    phones get, so a camera is never trusted until it has produced a picture.
    """
    import cv2
    idx = int(payload.get("index", 0))
    backend = cv2.CAP_DSHOW if os.name == "nt" else 0
    cap = cv2.VideoCapture(idx, backend)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(idx)
    if not cap.isOpened():
        cap.release()
        return {"ok": False, "reason": (f"Camera {idx} would not open. Close Teams, Zoom or the "
                                        f"Camera app and try again, or try index {idx + 1}.")}
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return {"ok": False, "reason": (f"Camera {idx} opened but gave no picture — another "
                                        f"program is probably holding it.")}
    h, w = frame.shape[:2]
    return {"ok": True, "index": idx, "width": int(w), "height": int(h)}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    dest = os.path.join(UPLOADS, os.path.basename(file.filename))
    with open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    return {"ok": True, "path": dest, "name": os.path.basename(dest)}


def _ensure_models(status_cb) -> Models:
    if STATE["models"] is not None:
        return STATE["models"]
    cfg = Config()
    STATE["models"] = Models(cfg, on_status=status_cb)
    return STATE["models"]


@app.post("/api/start")
async def start(payload: dict):
    """Start a run. `mode` is 'live' or 'recorded'; the rest is the source."""
    if STATE["engine"] is not None:
        STATE["engine"].stop()
        STATE["engine"] = None

    q: queue.Queue = queue.Queue(maxsize=600)
    STATE["queue"] = q
    cfg = Config()
    cfg.stream.resize_width = int(payload.get("work_width", 640))

    mode = payload.get("mode", "live")
    specs: list[dict] = []
    label = ""
    if mode == "live":
        for c in payload.get("cameras", []):
            if not c.get("url") and c.get("kind", "mjpeg") == "mjpeg":
                continue
            specs.append(c)
        if not specs:
            return JSONResponse({"ok": False,
                                 "reason": "No camera addresses were given."}, 400)
        label = f"{len(specs)} live camera(s) on the hotspot"
    else:
        path = payload.get("path", "")
        if not os.path.exists(path):
            return JSONResponse({"ok": False, "reason": f"Not found: {path}"}, 400)
        layout = payload.get("layout", "single")
        start_s = float(payload.get("start_s", 0.0))
        end_s = payload.get("end_s")
        names = payload.get("tile_names") or ["Camera 1", "Camera 2",
                                              "Camera 3", "Camera 4"]
        positions = payload.get("tile_positions") or ["Front", "Rear", "Left", "Right"]
        if layout == "quad":
            for i, cid in enumerate(("cam1", "cam2", "cam3", "cam4")):
                specs.append({"id": cid, "name": names[i], "position": positions[i],
                              "kind": "file", "path": path, "crop": cid,
                              "loop": bool(payload.get("loop", True)),
                              "start_s": start_s, "end_s": end_s})
        else:
            specs.append({"id": "cam1", "name": names[0], "position": positions[0],
                          "kind": "file", "path": path,
                          "loop": bool(payload.get("loop", True)),
                          "start_s": start_s, "end_s": end_s})
        label = os.path.basename(path)

    def status_cb(phase, message):
        try:
            q.put_nowait({"type": "status", "phase": phase, "message": message})
        except queue.Full:
            pass

    def boot():
        try:
            status_cb("loading", "Waking the detectors…")
            models = _ensure_models(status_cb)
            eng = MultiCamEngine(specs, q, cfg, models, mode=mode,
                                 work_width=cfg.stream.resize_width,
                                 preview_fps=float(payload.get("preview_fps", 6)),
                                 source_label=label,
                                 position=payload.get("position"))
            STATE["engine"] = eng
            eng.start()
            status_cb("ready", "Edge node armed.")
        except Exception as exc:                            # noqa: BLE001
            STATE["load_error"] = str(exc)
            try:
                q.put_nowait({"type": "error", "message": str(exc)})
            except queue.Full:
                pass

    threading.Thread(target=boot, daemon=True).start()
    return {"ok": True, "mode": mode, "cameras": len(specs), "source": label}


@app.post("/api/stop")
async def stop():
    eng = STATE["engine"]
    if eng is None:
        return {"ok": False, "reason": "nothing running"}
    snap = eng.snapshot()
    eng.stop()
    STATE["last_finished"] = {"snapshot": snap, "events": eng.events,
                              "alerts": eng.alerts, "ledger": eng.ledger,
                              "timeline": eng.timeline,
                              "session": eng.record_path}
    STATE["engine"] = None
    return {"ok": True, "snapshot": snap}


@app.post("/api/pause")
async def pause():
    eng = STATE["engine"]
    if eng is None:
        return {"ok": False}
    return {"ok": True, "paused": eng.toggle_pause()}


@app.get("/api/state")
def state():
    eng = STATE["engine"]
    return {"running": eng is not None,
            "snapshot": eng.snapshot() if eng else None,
            "models_loaded": STATE["models"] is not None,
            "load_error": STATE["load_error"]}


@app.get("/api/export/events.jsonl", response_class=PlainTextResponse)
def export_events():
    eng = STATE["engine"]
    if eng is not None:
        return eng.export_events_jsonl()
    last = STATE["last_finished"]
    if not last:
        return "no run yet"
    return "\n".join(json.dumps(e, separators=(",", ":")) for e in last["events"])


@app.get("/api/export/ledger.csv", response_class=PlainTextResponse)
def export_ledger():
    eng = STATE["engine"]
    if eng is not None:
        return eng.export_ledger_csv()
    return "no run in progress"


@app.get("/api/report.html", response_class=HTMLResponse)
def report():
    """A one-page, self-contained report of the run in progress (or the last one)."""
    eng = STATE["engine"]
    if eng is not None:
        snap, events, alerts, ledger = (eng.snapshot(), eng.events, eng.alerts,
                                        eng.ledger)
        label = eng.source_label
    elif STATE["last_finished"]:
        f = STATE["last_finished"]
        snap, events, alerts, ledger = (f["snapshot"], f["events"], f["alerts"],
                                        f["ledger"])
        label = f["snapshot"].get("mode", "")
    else:
        return HTMLResponse("<p>No run yet.</p>")
    return HTMLResponse(build_report(snap, events, alerts, ledger, label,
                                     HONEST_LIMITS, TEST_METRICS))


@app.get("/api/export/defects.geojson")
def export_geojson():
    eng = STATE["engine"]
    if eng is not None:
        return JSONResponse(eng.export_geojson(),
                            headers={"Content-Disposition":
                                     "attachment; filename=defects.geojson"})
    return JSONResponse({"type": "FeatureCollection", "features": [],
                         "properties": {"note": "no run in progress"}})


@app.get("/api/sessions")
def sessions():
    out = []
    for f in sorted(os.listdir(SESSIONS), reverse=True)[:20]:
        p = os.path.join(SESSIONS, f)
        out.append({"name": f, "bytes": os.path.getsize(p),
                    "modified": time.strftime("%Y-%m-%d %H:%M",
                                              time.localtime(os.path.getmtime(p)))})
    return {"sessions": out, "dir": SESSIONS}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    sent_hello = False
    eng = STATE["engine"]
    if eng is not None:
        # Someone opened or refreshed the dashboard while a run is going. Replay
        # enough for the screen to be complete rather than starting them half way
        # through with an empty ledger.
        await sock.send_text(json.dumps({
            "type": "resync",
            "started": {"type": "started", "mode": eng.mode,
                        "source_label": eng.source_label,
                        "cameras": [{"id": c.id, "name": c.name,
                                     "position": c.position, "kind": c.kind}
                                    for c in eng.cameras.values()],
                        "work_width": eng.work_width,
                        "models": eng.models.describe(),
                        "position": eng.position.summary()},
            # Deliberately small. A resync happens on every reconnect, and a
            # fat one on a busy machine is how a hiccup becomes a crash.
            "events": eng.events[-80:],
            "alerts": eng.alerts[-12:],
            "ledger": eng.ledger[-12:],
            "timeline": eng.timeline[-400:],
        }, separators=(",", ":")))
    try:
        while True:
            q = STATE["queue"]
            if q is None:
                if not sent_hello:
                    await sock.send_text(json.dumps({"type": "idle"}))
                    sent_hello = True
                await asyncio.sleep(0.15)
                continue
            try:
                msg = q.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.02)
                continue
            await sock.send_text(json.dumps(msg, separators=(",", ":")))
    except WebSocketDisconnect:
        return
    except Exception:                                       # noqa: BLE001
        return


def main():
    import uvicorn
    port = int(os.environ.get("TRR_V3_PORT", "8082"))
    print("=" * 66)
    print("  The Road Runners — v3 multi-camera edge node")
    print(f"  Dashboard:  http://127.0.0.1:{port}")
    print("  Offline by construction. Ctrl+C to stop.")
    print("=" * 66)
    # ws_ping_interval=None: when the machine is loaded, the asyncio loop can be
    # late answering a ping, and uvicorn would close a perfectly healthy socket —
    # which the page then has to re-sync, on the machine that was already busy.
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning",
                ws_ping_interval=None, ws_ping_timeout=None,
                timeout_keep_alive=75)


if __name__ == "__main__":
    main()
