#!/usr/bin/env python3
"""
fake_cameras.py — four MJPEG cameras, served from one recorded drive
====================================================================
Team The Road Runners · SIH 2026

WHY THIS EXISTS
    The live mode of the dashboard needs four cameras on a network. Four phones,
    a hotspot and a car are not always available — the evening before a
    submission, for instance. This serves the four tiles of one of our own 2x2
    recordings as four separate MJPEG streams on this machine, with exactly the
    endpoints IP Webcam uses.

    The dashboard cannot tell the difference, and neither can the detector. That
    is the point: it exercises the real live path (HTTP, multipart, reconnects,
    per-camera calibration, the scheduler) rather than a special test mode
    hidden inside the app.

    It is a REHEARSAL TOOL. Say so if it is ever used in front of anyone — the
    dashboard will honestly report these as live cameras, because as far as it
    is concerned they are.

USE
    python tools\\fake_cameras.py "..\\Four_Camera_Combine\\output\\Morning_4Camera_Combined.mp4"
    python tools\\fake_cameras.py <video> --port 8090 --fps 8 --quality 70

Then put the four addresses it prints into the dashboard's live tab.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

# The tile geometry of our Quad View screen recordings, as fractions.
TILES = {
    "cam1": (0.005, 0.218, 0.492, 0.363),
    "cam2": (0.501, 0.218, 0.492, 0.363),
    "cam3": (0.005, 0.601, 0.492, 0.363),
    "cam4": (0.501, 0.601, 0.492, 0.363),
}
BOUNDARY = "trrframe"
STATE: dict = {}
GPS: dict = {"track": [], "t0": 0.0, "speed_mps": 6.0}


def load_gpx(path):
    """Read a GPX so the rehearsal cameras can also publish a position.

    IP Webcam serves the phone's GNSS at /sensors.json when Location data is on.
    Serving the same shape here means the dashboard's live-map path is exercised
    for real — the same parser, the same staleness rule — rather than tested only
    on the day, in a car, with four phones.
    """
    import xml.etree.ElementTree as ET
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    root = ET.parse(path).getroot()
    pts = root.findall(".//g:trkpt", ns) or root.findall(".//trkpt")
    GPS["track"] = [(float(p.get("lat")), float(p.get("lon"))) for p in pts]
    GPS["t0"] = time.time()
    return len(GPS["track"])


def gps_now():
    """Walk the track at a steady pace and return where we are."""
    tr = GPS["track"]
    if not tr:
        return None
    i = int((time.time() - GPS["t0"]) * 1.0) % len(tr)     # one point per second
    lat, lon = tr[i]
    return lat, lon


class Tile:
    """Decodes its own crop of the video and publishes JPEGs at a steady rate."""

    def __init__(self, path, crop, fps, quality, width, loop=True, start_s=0.0):
        self.path, self.crop = path, crop
        self.fps, self.quality, self.width = fps, quality, width
        self.loop, self.start_s = loop, start_s
        self.jpeg = None
        self.seq = 0
        self.trim = None
        self.trim_checks = 0
        self.cv = threading.Condition()

    def run(self):
        cap = cv2.VideoCapture(self.path)
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        if self.start_s:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(self.start_s * src_fps))
        step = max(1, int(round(src_fps / self.fps)))
        period = 1.0 / self.fps
        nxt = time.time()
        while True:
            for _ in range(step):
                ok, frame = cap.read()
                if not ok:
                    break
            if not ok:
                if not self.loop:
                    return
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            h, w = frame.shape[:2]
            x, y, cw, ch = self.crop
            sub = frame[int(y * h):int((y + ch) * h), int(x * w):int((x + cw) * w)]
            if self.trim is None and self.trim_checks < 4:
                self.trim_checks += 1
                g = sub.max(axis=2)
                cols = [i for i, v in enumerate(g.max(axis=0)) if v > 14]
                rows = [i for i, v in enumerate(g.max(axis=1)) if v > 14]
                if len(cols) > 8 and len(rows) > 8 and (
                        (cols[-1]-cols[0]) * (rows[-1]-rows[0]) < 0.97 * g.size):
                    self.trim = (cols[0], rows[0], cols[-1]+1, rows[-1]+1)
            if self.trim:
                x0, y0, x1, y1 = self.trim
                sub = sub[y0:y1, x0:x1]
            if sub.shape[1] > self.width:
                s = self.width / sub.shape[1]
                sub = cv2.resize(sub, (self.width, max(2, int(sub.shape[0] * s))))
            ok_j, buf = cv2.imencode(".jpg", sub, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
            if ok_j:
                with self.cv:
                    self.jpeg = buf.tobytes()
                    self.seq += 1
                    self.cv.notify_all()
            nxt += period
            time.sleep(max(0.0, nxt - time.time()))

    def wait(self, after: int, timeout=5.0):
        with self.cv:
            if self.seq <= after:
                self.cv.wait(timeout)
            return self.jpeg, self.seq


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):        # keep the console readable
        pass

    def do_GET(self):
        path = self.path.split("?")[0].strip("/")
        parts = path.split("/")
        name = parts[0] if parts else ""
        if name == "sensors.json" or (len(parts) > 1 and parts[1] == "sensors.json"):
            return self._sensors()
        if name == "" or name == "index.html":
            return self._index()
        tile = STATE.get(name)
        if tile is None:
            return self.send_error(404, "no such camera")
        if len(parts) > 1 and parts[1] == "shot.jpg":
            return self._shot(tile)
        return self._stream(tile)

    def _sensors(self):
        pos = gps_now()
        if pos is None:
            body = b'{"gps":{"data":[]}}'
        else:
            body = json.dumps({"gps": {"unit": "deg",
                                       "desc": ["latitude", "longitude", "altitude"],
                                       "data": [[int(time.time() * 1000),
                                                 [pos[0], pos[1], 25.0]]]}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _index(self):
        body = ("<!doctype html><meta charset=utf-8><title>Rehearsal cameras</title>"
                "<body style='background:#05101C;color:#F2F7FC;font:14px system-ui;padding:24px'>"
                "<h2>Rehearsal cameras</h2><p>Four MJPEG streams from one recorded drive.</p><ul>"
                + "".join(f"<li><code>/{k}/video</code></li>" for k in STATE)
                + "</ul></body>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _shot(self, tile):
        jpeg, _ = tile.wait(-1)
        if jpeg is None:
            return self.send_error(503, "no frame yet")
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpeg)))
        self.end_headers()
        self.wfile.write(jpeg)

    def _stream(self, tile):
        self.send_response(200)
        self.send_header("Content-Type",
                         f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        seq = -1
        try:
            while True:
                jpeg, seq = tile.wait(seq)
                if jpeg is None:
                    break
                head = (f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                        f"Content-Length: {len(jpeg)}\r\n\r\n").encode()
                self.wfile.write(head)
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:                                   # noqa: BLE001
        return "127.0.0.1"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--fps", type=float, default=8.0)
    ap.add_argument("--quality", type=int, default=70)
    ap.add_argument("--width", type=int, default=800)
    ap.add_argument("--start", type=float, default=0.0, help="seconds into the file")
    ap.add_argument("--gpx", default="",
                    help="a GPX track to publish as the phone's GPS at /sensors.json")
    ap.add_argument("--single", action="store_true",
                    help="serve the whole frame as one camera instead of four tiles")
    args = ap.parse_args()

    if not os.path.exists(args.video):
        sys.exit(f"Not found: {args.video}")

    if args.gpx:
        n = load_gpx(args.gpx)
        print(f"  GPS: {n} points from {os.path.basename(args.gpx)} "
              f"-> /sensors.json (IP Webcam shape)")
    tiles = ({"cam1": (0.0, 0.0, 1.0, 1.0)} if args.single else TILES)
    for name, crop in tiles.items():
        t = Tile(args.video, crop, args.fps, args.quality, args.width,
                 start_s=args.start)
        STATE[name] = t
        threading.Thread(target=t.run, daemon=True).start()

    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    srv.daemon_threads = True
    ip = local_ip()
    print("=" * 62)
    print("  Rehearsal cameras — four MJPEG streams from one recording")
    print(f"  Source: {os.path.basename(args.video)}  ({args.fps:g} fps each)")
    for name in tiles:
        print(f"    {name}:  127.0.0.1:{args.port}/{name}/video")
    print(f"  From another device on this network: {ip}:{args.port}/<cam>/video")
    print("  Ctrl+C to stop.")
    print("=" * 62)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopping.")


if __name__ == "__main__":
    main()
