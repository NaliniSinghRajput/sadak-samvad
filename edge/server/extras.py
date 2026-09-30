"""Capabilities added for PS 26124 beyond the v3 baseline.

Three things live here, and each one is deliberately conservative about what it
claims:

* **Waterlogging** — a YOLO model trained on a public waterlogging set. It only
  accepts a detection that sits on the learned road plane, because a puddle in a
  field is not a road hazard and a reflection in a shop window is not water.

* **Number plates** — a YOLO model trained on 5,000 plate images. The default
  action is to BLUR, not to read. A plate is personal data under the DPDP Act and
  a bus that photographs every plate it passes is a surveillance network. Reading
  is attempted only when `read_plates` is on, and the text is carried as
  `plate_text_unverified` with its own OCR confidence, never as an assertion.

* **Congestion** — not a model at all. It is arithmetic over detections the node
  already has, and it is labelled as derived so nobody mistakes it for a sensor.
"""
from __future__ import annotations

import os
import re
import cv2
import numpy as np

# --------------------------------------------------------------------------
# weights discovery — same convention the rest of the node uses
# --------------------------------------------------------------------------
def _find(name: str, extra_dirs=()) -> str | None:
    # Reuse the node's own weights discovery first, so a model dropped next to
    # pothole_best.pt in Project_v1/models is found without a second copy. The
    # import is deferred because perception imports this module.
    try:
        from perception import find_weights as _node_find
        p = _node_find(name, tuple(extra_dirs))
        if p:
            return p
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    cands = list(extra_dirs) + [
        here, os.path.join(here, "weights"), os.path.dirname(here),
        os.path.join(os.path.dirname(here), "weights"),
        os.path.join(os.path.dirname(os.path.dirname(here)), "weights"),
    ]
    for d in cands:
        if not d:
            continue
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


class ExtraModels:
    """Waterlogging and plate detectors. Both are optional: if the weights are
    not on disk the node runs exactly as it did before and says so."""

    def __init__(self, weights_dir: str | None = None, on_status=None):
        self._say = on_status or (lambda *a, **k: None)
        self.water = None
        self.plate = None
        self.water_path = None
        self.plate_path = None
        extra = [weights_dir] if weights_dir else []
        from ultralytics import YOLO

        wp = _find("waterlogging_best.pt", extra)
        if wp:
            self._say("loading", "Loading the trained waterlogging detector…")
            self.water = YOLO(wp)
            self.water_path = wp

        pp = _find("plate_best.pt", extra)
        if pp:
            self._say("loading", "Loading the trained number-plate detector…")
            self.plate = YOLO(pp)
            self.plate_path = pp

    def describe(self) -> dict:
        return {
            "waterlogging": os.path.basename(self.water_path or "") or None,
            "waterlogging_path": self.water_path,
            "plate": os.path.basename(self.plate_path or "") or None,
            "plate_path": self.plate_path,
        }


# --------------------------------------------------------------------------
# waterlogging
# --------------------------------------------------------------------------
# MEASURED AND TURNED OFF, the same way the v2 proposer was. Trained on
# Waterlogging v2 (Roboflow Universe, CC BY 4.0) it reached mAP50 0.539 on that
# set's own held-out test split, then produced 69 waterlogging boxes in 90 s of
# DRY Bhubaneswar road through these very gates. Fine-tuned against 337 dry
# frames from our own drive as hard negatives it improved, but not enough: at
# conf 0.65 the dry road gives 3 boxes in 136 images and our own monsoon footage
# gives NONE. There is no threshold at which the dry drive is quiet and the rain
# drive is not, because the public set's water is grey and reflective and Indian
# floodwater is brown and opaque. That is a data problem, and a threshold tuned
# until it looked right would be a fabrication. Numbers in train/water_evidence.json.
# Set True to re-enable once there is labelled Indian standing water to train on.
WATER_ENABLED = False

# Set from our own footage by choose_water_threshold.py, which sweeps the value
# over a dry drive and a rain drive THROUGH THE NODE'S OWN GATES, because the
# bare model's confidence distribution is not what the node sees.
WATER_CONF = float(os.environ.get("WATER_CONF", "0.35"))
WATER_MIN_PLANE_OVERLAP = 0.35          # must actually be on the road
WATER_MIN_AREA = 0.004
WATER_MAX_AREA = 0.65


def _plane_overlap(plane, x0, y0, x1, y1) -> float:
    if plane is None:
        return 1.0
    sub = plane[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if sub.size == 0:
        return 0.0
    return float((sub > 0).mean())


def water_severity(area_frac: float) -> tuple[str, int]:
    """Depth is not observable from a single camera. Extent is. We grade extent
    and say so — a wide sheet of water across the carriageway is the thing a
    control room needs to know about, whether it is 5 cm or 30 cm deep."""
    if area_frac >= 0.12:
        return "severe", 3
    if area_frac >= 0.04:
        return "moderate", 2
    return "minor", 1


def detect_waterlogging(model, frame_bgr, plane, ego, imgsz=512):
    """Returns a list of dicts: bbox in pixels, confidence, severity, area_frac."""
    if not WATER_ENABLED or model is None:
        return [], {"plane": 0, "area": 0, "ego": 0}
    H, W = frame_bgr.shape[:2]
    res = model.predict(frame_bgr, conf=WATER_CONF, imgsz=imgsz, verbose=False)[0]
    out = []
    rejected = {"plane": 0, "area": 0, "ego": 0}
    for box, conf in zip(res.boxes.xyxy.cpu().numpy(),
                         res.boxes.conf.cpu().numpy()):
        x0, y0, x1, y1 = [int(v) for v in box]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(W, x1), min(H, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        area_frac = ((x1 - x0) * (y1 - y0)) / float(W * H)
        if not (WATER_MIN_AREA <= area_frac <= WATER_MAX_AREA):
            rejected["area"] += 1
            continue
        if ego is not None and _plane_overlap(ego, x0, y0, x1, y1) > 0.5:
            rejected["ego"] += 1                 # it is our own wet bonnet
            continue
        if _plane_overlap(plane, x0, y0, x1, y1) < WATER_MIN_PLANE_OVERLAP:
            rejected["plane"] += 1               # water, but not on the road
            continue
        sev, rank = water_severity(area_frac)
        out.append({"bbox": [float(x0), float(y0), float(x1 - x0), float(y1 - y0)],
                    "confidence": float(conf), "severity": sev,
                    "severity_rank": rank, "area_frac": round(area_frac, 4)})
    return out, rejected


# --------------------------------------------------------------------------
# number plates — detect, redact, and only then (optionally) read
# --------------------------------------------------------------------------
# Deliberately LOW. For a detector that exists to BLUR, the two errors are not
# equal: a false positive costs a smudge on a patch of road, a miss leaks a
# registration. So we take the recall and accept the smudges, and the threshold
# is an environment variable so it can be raised for anything that is not
# redaction.
PLATE_CONF = float(os.environ.get("PLATE_CONF", "0.20"))
PLATE_MIN_AREA = 0.00004
PLATE_MAX_AREA = 0.08
_PLATE_RE = re.compile(r"^[A-Z]{2}[ -]?\d{1,2}[ -]?[A-Z]{0,3}[ -]?\d{3,4}$")


def detect_plates(model, frame_bgr, imgsz=512):
    if model is None:
        return []
    H, W = frame_bgr.shape[:2]
    res = model.predict(frame_bgr, conf=PLATE_CONF, imgsz=imgsz, verbose=False)[0]
    out = []
    for box, conf in zip(res.boxes.xyxy.cpu().numpy(),
                         res.boxes.conf.cpu().numpy()):
        x0, y0, x1, y1 = [int(v) for v in box]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(W, x1), min(H, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        af = ((x1 - x0) * (y1 - y0)) / float(W * H)
        if not (PLATE_MIN_AREA <= af <= PLATE_MAX_AREA):
            continue
        out.append({"bbox": [float(x0), float(y0), float(x1 - x0), float(y1 - y0)],
                    "confidence": float(conf)})
    return out


def redact(frame_bgr, plates, strength: int = 3):
    """Blur every plate. This is the DEFAULT path: the frame an operator sees and
    the thumbnail that ships with an event are both redacted, so an uninvolved
    driver's registration never leaves the vehicle."""
    if not plates:
        return frame_bgr
    out = frame_bgr.copy()
    for p in plates:
        x, y, w, h = [int(v) for v in p["bbox"]]
        x, y = max(0, x), max(0, y)
        w, h = max(1, w), max(1, h)
        roi = out[y:y + h, x:x + w]
        if roi.size == 0:
            continue
        k = max(3, (min(w, h) // strength) | 1)
        out[y:y + h, x:x + w] = cv2.GaussianBlur(roi, (k, k), 0)
    return out


def read_plate(frame_bgr, bbox, min_chars: int = 6):
    """Best-effort OCR. Returns (text, confidence 0-1, plausible) or (None, 0, False).

    This is Tesseract on a small, motion-blurred crop from a moving vehicle. It is
    not an evidentiary ANPR pipeline and the caller must not present it as one.
    """
    try:
        import pytesseract
    except Exception:
        return None, 0.0, False
    x, y, w, h = [int(v) for v in bbox]
    crop = frame_bgr[max(0, y):y + h, max(0, x):x + w]
    if crop.size == 0 or min(crop.shape[:2]) < 8:
        return None, 0.0, False
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    g = cv2.bilateralFilter(g, 7, 55, 55)
    g = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    cfg = ("--psm 7 --oem 3 -c "
           "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    try:
        data = pytesseract.image_to_data(g, config=cfg,
                                         output_type=pytesseract.Output.DICT)
    except Exception:
        return None, 0.0, False
    txt, confs = [], []
    for s, c in zip(data.get("text", []), data.get("conf", [])):
        s = (s or "").strip().upper()
        try:
            c = float(c)
        except Exception:
            c = -1.0
        if s and c >= 0:
            txt.append(s)
            confs.append(c)
    raw = "".join(txt)
    raw = re.sub(r"[^A-Z0-9]", "", raw)
    if len(raw) < min_chars:
        return None, 0.0, False
    conf = float(np.mean(confs)) / 100.0 if confs else 0.0
    plausible = bool(_PLATE_RE.match(raw)) or (8 <= len(raw) <= 10)
    return raw, round(conf, 3), plausible


# --------------------------------------------------------------------------
# congestion — derived, not sensed
# --------------------------------------------------------------------------
def congestion_index(vehicles_in_view: int, speed_index: float,
                     vru_in_view: int = 0) -> dict:
    """A 0-100 index from what the node already counts.

    Rationale: a bottleneck is many vehicles AND little forward motion. Either
    alone is not congestion — an empty road at a red light is not a bottleneck,
    and a fast-moving dense stream is heavy traffic, not a jam.
    `speed_index` is the node's existing ego-motion proxy, 0 = stationary.
    """
    dens = min(1.0, vehicles_in_view / 12.0)
    slow = 1.0 - min(1.0, float(speed_index) / 6.0)
    idx = int(round(100.0 * (0.65 * dens * slow + 0.25 * dens + 0.10 * slow)))
    idx = max(0, min(100, idx))
    if idx >= 70:
        level = "bottleneck"
    elif idx >= 45:
        level = "heavy"
    elif idx >= 20:
        level = "moderate"
    else:
        level = "free"
    return {"index": idx, "level": level,
            "vehicles_in_view": int(vehicles_in_view),
            "vru_in_view": int(vru_in_view),
            "speed_index": round(float(speed_index), 2),
            "basis": "derived from detections and ego-motion, not a speed sensor"}


# --------------------------------------------------------------------------
# zebra crossings — classical, because there is no dataset for Indian markings
# --------------------------------------------------------------------------
# MEASURED AND TURNED OFF. Tightened, this found 2 candidates in 297 s of our
# own morning drive — one was tree shadow on the carriageway, the other the boot
# of a car — and no true crossing, because there is no crossing in that footage
# to find. A detector we cannot show working on our own road does not get to
# claim a capability, so it ships disabled with its numbers, the same way the v2
# waterlogging proposer did. Set True to re-enable and re-measure.
ZEBRA_ENABLED = False
ZEBRA_MIN_BARS = 5
ZEBRA_MIN_CONF = 0.62
# Regularity is a GATE, not a weighted term. The first version blended it with
# the bar count, so twelve scattered bright blobs on a striped kerb scored 0.61
# and were reported as a crossing — 34 times in 90 seconds of dry Bhubaneswar
# road. Even spacing is the whole signal: without it there is no crossing.
ZEBRA_MIN_REG = 0.70
ZEBRA_MAX_LEN_CV = 0.45          # a crossing's stripes are of a kind
ZEBRA_MAX_WIDTH_FRAC = 0.85      # a run that spans the whole frame is a kerb line


def detect_zebra(frame_bgr, plane, min_bars: int = ZEBRA_MIN_BARS):
    """Find a painted crossing: a run of bright, parallel, evenly spaced bars
    lying on the road plane.

    Deliberately classical. A zebra crossing is a geometric pattern, not a
    semantic object, and we have no labelled Indian crossing data — inventing a
    model here would mean claiming a capability we could not defend. This finds
    the pattern and reports how strongly it matched, so a weak match reads as a
    weak match rather than a confident wrong answer.

    Returns [] or a single dict, because a frame has at most one crossing ahead.
    """
    if not ZEBRA_ENABLED:
        return []
    H, W = frame_bgr.shape[:2]
    if plane is None:
        return []
    ys, xs = np.where(plane > 0)
    if ys.size < 500:
        return []
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    if (y1 - y0) < 40 or (x1 - x0) < 60:
        return []
    roi = frame_bgr[y0:y1, x0:x1]
    g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    g = cv2.GaussianBlur(g, (5, 5), 0)
    # paint is brighter than the road it sits on
    thr = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                cv2.THRESH_BINARY, 31, -12)
    thr = cv2.morphologyEx(thr, cv2.MORPH_OPEN,
                           cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    cnts, _ = cv2.findContours(thr, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    bars = []
    rh, rw = thr.shape[:2]
    for c in cnts:
        a = cv2.contourArea(c)
        if a < 0.0008 * rh * rw or a > 0.10 * rh * rw:
            continue
        rect = cv2.minAreaRect(c)
        (cx, cy), (w_, h_), ang = rect
        if w_ < 1 or h_ < 1:
            continue
        long_, short_ = max(w_, h_), min(w_, h_)
        if short_ < 3:
            continue
        ar = long_ / short_
        if not (2.2 <= ar <= 14.0):
            continue
        fill = a / float(w_ * h_)
        if fill < 0.55:
            continue
        theta = ang if w_ >= h_ else ang + 90.0
        bars.append((cx, cy, long_, short_, theta % 180.0, a))
    if len(bars) < min_bars:
        return []
    # a crossing is a run of bars pointing the same way
    angs = np.array([b[4] for b in bars])
    med = float(np.median(angs))
    keep = [b for b, t in zip(bars, angs) if min(abs(t - med), 180 - abs(t - med)) < 16.0]
    if len(keep) < min_bars:
        return []
    # ...and evenly spaced along the perpendicular
    perp = np.array([b[0] * np.cos(np.radians(med + 90)) +
                     b[1] * np.sin(np.radians(med + 90)) for b in keep])
    perp.sort()
    gaps = np.diff(perp)
    gaps = gaps[gaps > 1.0]
    if gaps.size < min_bars - 2:
        return []
    reg = 1.0 - min(1.0, float(np.std(gaps) / max(np.mean(gaps), 1e-6)))
    if reg < ZEBRA_MIN_REG:
        return []
    lens = np.array([b[2] for b in keep], dtype=np.float32)
    len_cv = float(np.std(lens) / max(float(np.mean(lens)), 1e-6))
    if len_cv > ZEBRA_MAX_LEN_CV:
        return []
    len_score = 1.0 - min(1.0, len_cv / ZEBRA_MAX_LEN_CV)
    count_score = min(1.0, len(keep) / 7.0)
    conf = 0.45 * reg + 0.30 * count_score + 0.25 * len_score
    if conf < ZEBRA_MIN_CONF:
        return []
    cxs = [b[0] for b in keep]; cys = [b[1] for b in keep]
    hw = max(b[2] for b in keep) / 2.0
    bx0 = max(0, int(x0 + min(cxs) - hw)); bx1 = min(W, int(x0 + max(cxs) + hw))
    by0 = max(0, int(y0 + min(cys) - 10)); by1 = min(H, int(y0 + max(cys) + 10))
    if bx1 <= bx0 or by1 <= by0:
        return []
    if (bx1 - bx0) > ZEBRA_MAX_WIDTH_FRAC * W:
        return []
    return [{"bbox": [float(bx0), float(by0), float(bx1 - bx0), float(by1 - by0)],
             "confidence": round(float(conf), 3),
             "bars": len(keep),
             "spacing_regularity": round(float(reg), 3),
             "bar_length_cv": round(len_cv, 3)}]
