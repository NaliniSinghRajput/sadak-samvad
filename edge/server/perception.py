"""
PROJECT_V3 — perception: one model set, one analyser per camera
===============================================================
Team The Road Runners · SIH 2026 · PS 26124

WHAT MOVED FROM v2, AND WHY IT HAD TO
    v2 had one engine that owned one video, one model, one ego mask. Four
    cameras break that in one specific way: the MODELS are shared (loading
    yolo11s four times would quadruple memory for no gain) but everything
    GEOMETRIC is per camera — each phone points somewhere different, so each one
    has its own ego mask, its own road plane, its own motion estimate and its
    own de-duplication memory.

    So this file is a deliberate split:
        Models          loaded once, called by whichever camera's turn it is
        CameraAnalyser  one per camera, holds all the per-view state

    The detection RULES are v2's, carried over with their reasoning intact: the
    ego-vehicle rejection, the rider test with its two-wheeler memory, the road
    plane footfall test for humans and animals, the trained road-damage detector
    with its placement gates, and the classical surface proposer.

WHAT IS HONESTLY DIFFERENT ON FOUR CAMERAS
    Each camera is analysed less often, because there is one laptop. That is a
    scheduling fact, not a quality claim, and the dashboard shows the real
    per-camera rate rather than an aspiration. See multicam.py.
"""
from __future__ import annotations

import base64
import os
import sys
import threading
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "_p2src")
for _p in (HERE, SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config import Config                                          # noqa: E402
from pothole import PotholeDetector                                # noqa: E402
from vision import EgoMotion, SurfaceProposer, road_roi            # noqa: E402
from ego_live import LiveEgoLearner                                # noqa: E402
import extras                                                     # noqa: E402

# Severity vocabulary — one source of truth, read by the frontend from /api/vocab.
SEVERITY = {
    "severe":   {"rank": 3, "status": "critical", "icon": "octagon",  "label": "SEVERE"},
    "moderate": {"rank": 2, "status": "warning",  "icon": "triangle", "label": "MODERATE"},
    "minor":    {"rank": 1, "status": "good",     "icon": "circle",   "label": "MINOR"},
}
CLASS_PRIORITY = {
    "vru_in_carriageway":    ("critical", "octagon",  "VULNERABLE ROAD USER"),
    "animal_in_carriageway": ("critical", "octagon",  "ANIMAL IN CARRIAGEWAY"),
    "road_damage_candidate": ("info",     "diamond",  "UNVERIFIED ANOMALY"),
}
LEDGER_LABELS = ("pothole",)
SAFETY_LABELS = ("vru_in_carriageway", "animal_in_carriageway")


def find_weights(name: str, extra_dirs=()) -> str | None:
    """Locate weights without duplicating them.

    Project_v1 holds both models and this node must run the SAME detector that
    was measured — two copies are two things that can silently drift apart.
    """
    # Walk up from here and look for Project_v1 wherever it sits. The folder
    # tree was reorganised once already (Stage_1 / Stage_2 / Stage_3) and a
    # hard-coded relative path is the kind of thing that breaks silently the
    # next time something moves.
    cands = list(extra_dirs) + [os.path.join(ROOT, "models")]
    anc = ROOT
    for _ in range(5):
        anc = os.path.dirname(anc)
        if not anc:
            break
        cands += [
            os.path.join(anc, "Stage_1_SIH2026_KIIT", "Project_v1", "models"),
            os.path.join(anc, "Stage_1_SIH2026_KIIT", "Project_v1"),
            os.path.join(anc, "Project_v1", "models"),
            os.path.join(anc, "Project_v1"),
        ]
    for d in cands:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


class Models:
    """The detectors, loaded once and shared by every camera.

    A lock serialises inference. On this laptop there is one CPU budget and two
    threads asking a single model to predict at the same time do not go faster —
    they interleave and make both answers late. One at a time, round-robin, is
    both simpler and measurably better.
    """

    def __init__(self, cfg: Config, weights_dir: str | None = None,
                 on_status=None):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.coco = None
        self.pothole = None
        self.coco_path = None
        self.pothole_path = None
        self._say = on_status or (lambda *a, **k: None)
        self._load(weights_dir)

    def _load(self, weights_dir):
        from ultralytics import YOLO          # imported late: it is slow
        extra = [weights_dir] if weights_dir else []
        w = find_weights(self.cfg.detector.weights, extra)
        self._say("loading", f"Loading COCO detector ({self.cfg.detector.weights})…")
        self.coco = YOLO(w or self.cfg.detector.weights)
        self.coco_path = w or self.cfg.detector.weights
        pw = find_weights("pothole_best.pt", extra)
        if pw:
            self._say("loading", "Loading the trained road-damage detector…")
            self.pothole_path = pw
            self.pothole = PotholeDetector(pw, cfg=self.cfg.pothole)

        # capabilities added for PS 26124 — optional; absent weights are fine
        self.extras = extras.ExtraModels(weights_dir=weights_dir, on_status=self._say)

    def describe(self) -> dict:
        return {
            "coco": os.path.basename(self.coco_path or ""),
            "coco_path": self.coco_path,
            "pothole": os.path.basename(self.pothole_path or "") or None,
            "pothole_path": self.pothole_path,
            **(self.extras.describe() if getattr(self, "extras", None) else {}),
        }


class CameraAnalyser:
    """Everything that is true of ONE view: geometry, memory and verdicts."""

    def __init__(self, cam_id: str, name: str, position: str, models: Models,
                 cfg: Config, width: int = 640):
        self.id = cam_id
        self.name = name
        self.position = position
        self.models = models
        self.cfg = cfg
        self.W = int(width)
        self.H = 0
        self.ready = False
        self.ego = None
        self.halo = None
        self.plane = None
        self.roi = None
        self.motion = EgoMotion(cfg.motion)
        self.surface = SurfaceProposer(cfg.surface)
        self.learner: LiveEgoLearner | None = None
        self.cal_lock = threading.Lock()
        self.ego_present = False
        self.ego_report: dict = {"state": "learning", "reason":
                                 "Learning which pixels are our own vehicle…"}
        self._recent: list[tuple] = []      # de-duplication memory
        self._tw_memory: list[tuple] = []   # two-wheeler memory
        self.counts_total = {"vehicle": 0, "pedestrian": 0, "animal": 0,
                             "infrastructure": 0, "defect": 0, "candidate": 0}
        self.class_mix: dict[str, int] = {}
        self.events_emitted = 0
        self.duplicates_suppressed = 0
        self.own_vehicle_rejected = 0
        self.gate_rejected = {"area": 0, "aspect": 0, "plane": 0, "ego": 0}
        self.ms_per_frame = 0.0
        self.speed_index = 0.0
        self._perf: list[float] = []
        self.last_detections: list[dict] = []
        self.last_analysis_t = 0.0

    # -- geometry ----------------------------------------------------------
    def _ensure_size(self, frame):
        if self.H:
            return
        h, w = frame.shape[:2]
        self.H = max(2, int(h * self.W / max(w, 1)))
        self.plane = road_roi((self.W, self.H), self.cfg.roi, None)
        self.roi = road_roi((self.W, self.H), self.cfg.roi, None)
        self.ego = np.zeros((self.H, self.W), np.uint8)
        self.halo = np.zeros((self.H, self.W), np.uint8)
        self.learner = LiveEgoLearner(self.cfg.ego, (self.W, self.H))
        self.ready = True

    def _apply_ego(self):
        z = self.cfg.egozone
        rep = self.learner.report
        self.ego_report = rep
        self.ego_present = bool(rep.get("ego_present"))
        self.ego = (self.learner.mask if self.learner.mask is not None
                    else np.zeros((self.H, self.W), np.uint8))
        if self.ego_present:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                          (z.halo_dilate_px, z.halo_dilate_px))
            self.halo = cv2.dilate(self.ego, k)
        else:
            # NO EGO VEHICLE MEANS NO EGO RULES — not a smaller mask, none at all.
            # Left on, the side-of-frame rule would start deleting real
            # motorcycles at the edge of the road on the strength of an
            # assumption that no longer holds. (v1 §3, kept.)
            self.halo = np.zeros((self.H, self.W), np.uint8)
        self.roi = road_roi((self.W, self.H), self.cfg.roi,
                            self.ego if self.ego_present else None)

    # -- ego rejection (v2 semantics) --------------------------------------
    @staticmethod
    def _frac(mask, x0, y0, x1, y1) -> float:
        sub = mask[y0:y1, x0:x1]
        return 0.0 if sub.size == 0 else float((sub > 0).mean())

    def _is_own_vehicle(self, x0, y0, x1, y1, cls) -> bool:
        z = self.cfg.egozone
        if self._frac(self.ego, x0, y0, x1, y1) > z.max_mask_overlap:
            return True
        if not z.enabled or not self.ego_present:
            return False
        cy = (y0 + y1) / 2.0 / self.H
        cx = (x0 + x1) / 2.0 / self.W
        if cy <= z.near_row:
            return False
        halo = self._frac(self.halo, x0, y0, x1, y1)
        if halo > z.max_halo_overlap:
            return True
        if y1 >= self.H - z.bottom_tol_px and halo >= z.bottom_halo:
            return True
        if cls in z.rider_classes and (cx < z.side_frac or cx > 1.0 - z.side_frac):
            return True
        return False

    def _duplicate(self, label, cx, cy, t) -> bool:
        c = self.cfg.events
        self._recent = [r for r in self._recent if t - r[3] <= c.dedup_window_s]
        for lab, px, py, _ in self._recent:
            if lab == label and np.hypot(cx - px, cy - py) <= c.dedup_dist_px:
                return True
        self._recent.append((label, cx, cy, t))
        return False

    def _on_road_plane(self, x1, y1, x2, y2) -> bool:
        fx = int(np.clip(x1 + (x2 - x1) / 2, 0, self.W - 1))
        fy = int(np.clip(y2 - (y2 - y1) * 0.03, 0, self.H - 1))
        return bool(self.plane[fy, fx] > 0)

    def _near_enough(self, y1, y2) -> bool:
        return (y2 - y1) >= self.cfg.egozone.vru_min_height_frac * self.H

    def _riding(self, x1, y1, x2, y2, two_wheelers) -> bool:
        legs = (x1, y2 - (y2 - y1) * self.cfg.egozone.rider_band_frac, x2, y2)
        return any(_overlap_frac(legs, tw) > self.cfg.egozone.rider_overlap
                   or _overlap_frac((x1, y1, x2, y2), tw) > 0.35
                   for tw in two_wheelers)

    # -- calibration, fed by its own pump ----------------------------------
    def offer_calibration(self, frame_bgr, t_wall: float) -> bool:
        """Offer a frame to the ego learner. Returns True when it has decided.

        The learner needs frame PAIRS a fraction of a second apart, because that
        is what optical flow is for. The scheduler visits a camera roughly every
        two seconds, which is far too coarse — so calibration has its own pump,
        reading the camera directly while the run gets under way. It costs one
        small Farneback per camera per offer, and it stops the moment the verdict
        is in.
        """
        with self.cal_lock:
            self._ensure_size(frame_bgr)
            if self.learner is None or self.learner.done:
                return True
            small = cv2.resize(frame_bgr, (self.W, self.H))
            self.learner.offer(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), t_wall)
            if self.learner.done:
                self._apply_ego()
                return True
            return False

    # -- the one call the scheduler makes ----------------------------------
    def analyse(self, frame_bgr, t_wall: float) -> dict:
        """Analyse one frame from this camera. Returns detections + raw events."""
        t0 = time.time()
        self._ensure_size(frame_bgr)
        frame = cv2.resize(frame_bgr, (self.W, self.H))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        self.speed_index = self.motion.update(gray, self.roi)

        # --- number plates: FIND, then BLUR, before anything is thumbnailed ---
        ex = getattr(self.models, "extras", None)
        plates = []
        if ex is not None and ex.plate is not None:
            with self.models.lock:
                plates = extras.detect_plates(ex.plate, frame,
                                              imgsz=self.cfg.detector.imgsz)
        self.plates_seen = getattr(self, "plates_seen", 0) + len(plates)
        self._frame_pub = extras.redact(frame, plates) if plates else frame
        # Keep a short trail of where plates were, normalised, so the LIVE
        # PREVIEW can be blurred too. The round robin only reaches this camera
        # every second or so, so a single box would sit where the plate used to
        # be; the union of the last few, grown a little, covers the swept path.
        self.recent_plates = [pr for pr in getattr(self, "recent_plates", [])
                              if t_wall - pr[0] <= 2.5]
        if plates:
            self.recent_plates.append(
                (t_wall, [_norm(pl["bbox"], self.W, self.H) for pl in plates]))
        self.recent_plates = self.recent_plates[-6:]
        d = self.cfg.detector
        counts = {"vehicle": 0, "pedestrian": 0, "animal": 0,
                  "infrastructure": 0, "defect": 0, "candidate": 0,
                  "waterlogging": 0}
        dets: list[dict] = []
        events: list[dict] = []

        # --- COCO pass ----------------------------------------------------
        with self.models.lock:
            res = self.models.coco.predict(frame, conf=d.conf, iou=d.iou,
                                           imgsz=d.imgsz, verbose=False)[0]
        kept = []
        for box, cls, conf in zip(res.boxes.xyxy.cpu().numpy(),
                                  res.boxes.cls.cpu().numpy().astype(int),
                                  res.boxes.conf.cpu().numpy()):
            x1, y1, x2, y2 = [float(v) for v in box]
            ix0, iy0 = max(0, int(x1)), max(0, int(y1))
            ix1, iy1 = min(self.W, int(x2)), min(self.H, int(y2))
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            if self._is_own_vehicle(ix0, iy0, ix1, iy1, int(cls)):
                self.own_vehicle_rejected += 1
                continue
            kept.append((x1, y1, x2, y2, int(cls), float(conf)))

        # TWO-WHEELER MEMORY — a bicycle does not vanish between frames, but the
        # detector's confidence in it does, and when it flickers off the rider on
        # top is briefly a lone `person` standing in the road. (v1 bug #1.)
        for k in kept:
            if k[4] in (1, 3):
                self._tw_memory.append((t_wall, (k[0], k[1], k[2], k[3])))
        self._tw_memory = [m for m in self._tw_memory
                           if t_wall - m[0] <= self.cfg.egozone.rider_memory_s]
        two_wheelers = [m[1] for m in self._tw_memory]

        for x1, y1, x2, y2, cls, conf in kept:
            if cls in d.vehicle_classes:
                cat, label = "vehicle", d.vehicle_classes[cls]
            elif cls == d.person_class:
                cat, label = "pedestrian", "person"
            elif cls in d.infra_classes:
                cat, label = "infrastructure", d.infra_classes[cls]
            elif cls in d.animal_classes:
                cat, label = "animal", d.animal_classes[cls]
            else:
                continue

            sev = None
            attrs = {"src": "coco"}
            if cat == "pedestrian":
                riding = self._riding(x1, y1, x2, y2, two_wheelers)
                if (self._on_road_plane(x1, y1, x2, y2)
                        and self._near_enough(y1, y2) and not riding):
                    cat, label, sev = "vru", "vru_in_carriageway", "severe"
                    attrs["note"] = ("A human in the travelled way. Not resolved to "
                                     "pedestrian vs cyclist — a rider whose machine is "
                                     "not detected is still a vulnerable road user.")
                elif riding:
                    label = "rider"
            elif cat == "animal":
                attrs["species"] = label
                if self._on_road_plane(x1, y1, x2, y2) and self._near_enough(y1, y2):
                    cat, label, sev = "animal_road", "animal_in_carriageway", "severe"
                    attrs["note"] = ("Livestock or stray in the travelled way. The species "
                                     "is the COCO label and is indicative only.")

            key = ("vehicle" if cat == "vehicle" else
                   "pedestrian" if cat in ("pedestrian", "vru") else
                   "animal" if cat in ("animal", "animal_road") else "infrastructure")
            counts[key] += 1
            bbox = [x1, y1, x2 - x1, y2 - y1]
            dets.append({"bbox": _norm(bbox, self.W, self.H), "cat": cat,
                         "label": label, "conf": round(float(conf), 3), "sev": sev})
            events.append(self._event(cat, label, conf, bbox, t_wall, frame, attrs, sev))

        # --- trained road-damage pass -------------------------------------
        if self.models.pothole is not None:
            before = dict(self.models.pothole.rejected)
            with self.models.lock:
                found = self.models.pothole.detect(frame, self.plane, self.ego)
            for dd in found:
                x, y, w, h = dd["bbox"]
                if self._is_own_vehicle(max(0, int(x)), max(0, int(y)),
                                        min(self.W, int(x + w)),
                                        min(self.H, int(y + h)), -1):
                    self.own_vehicle_rejected += 1
                    continue
                counts["defect"] += 1
                dets.append({"bbox": _norm(dd["bbox"], self.W, self.H), "cat": "defect",
                             "label": "pothole", "conf": round(float(dd["confidence"]), 3),
                             "sev": dd["severity"]})
                events.append(self._event(
                    "road_surface", "pothole", dd["confidence"], dd["bbox"], t_wall, frame,
                    {"src": "trained_model", "visual_severity": dd["severity"],
                     "severity_rank": dd["severity_rank"], "raw_class": dd["raw_class"],
                     "area_frac": dd.get("area_frac"),
                     "note": "visual severity grade, NOT a depth measurement"},
                    dd["severity"]))
            for k2 in self.gate_rejected:
                self.gate_rejected[k2] += (self.models.pothole.rejected.get(k2, 0)
                                           - before.get(k2, 0))

        # --- trained waterlogging pass --------------------------------------
        if ex is not None and ex.water is not None:
            with self.models.lock:
                wfound, wrej = extras.detect_waterlogging(
                    ex.water, frame, self.plane, self.ego,
                    imgsz=self.cfg.detector.imgsz)
            if not hasattr(self, "water_rejected"):
                self.water_rejected = {}
            for k2, v2 in wrej.items():
                self.water_rejected[k2] = self.water_rejected.get(k2, 0) + v2
            for wd in wfound:
                counts["waterlogging"] = counts.get("waterlogging", 0) + 1
                dets.append({"bbox": _norm(wd["bbox"], self.W, self.H),
                             "cat": "waterlogging", "label": "waterlogging",
                             "conf": round(float(wd["confidence"]), 3),
                             "sev": wd["severity"]})
                events.append(self._event(
                    "waterlogging", "waterlogging", wd["confidence"], wd["bbox"],
                    t_wall, frame,
                    {"src": "trained_model", "extent_severity": wd["severity"],
                     "severity_rank": wd["severity_rank"],
                     "area_frac": wd["area_frac"],
                     "note": ("severity is EXTENT across the carriageway, not depth. "
                              "Depth is not observable from a single camera.")},
                    wd["severity"]))

        # --- zebra crossing (classical pattern, not a model) ----------------
        try:
            for z in extras.detect_zebra(frame, self.plane):
                counts["infrastructure"] = counts.get("infrastructure", 0) + 1
                dets.append({"bbox": _norm(z["bbox"], self.W, self.H),
                             "cat": "infrastructure", "label": "zebra_crossing",
                             "conf": z["confidence"], "sev": None})
                events.append(self._event(
                    "infrastructure", "zebra_crossing", z["confidence"], z["bbox"],
                    t_wall, frame,
                    {"src": "classical", "bars": z["bars"],
                     "spacing_regularity": z["spacing_regularity"],
                     "note": ("painted-pattern match on the road plane. Presence only — "
                              "judging a crossing MISSING needs junction context we do "
                              "not yet have.")},
                    None))
        except Exception:
            pass

        # --- classical surface proposer ------------------------------------
        for c in self.surface.update(frame, self.roi):
            counts["candidate"] += 1
            dets.append({"bbox": _norm(list(c["bbox"]), self.W, self.H),
                         "cat": "candidate", "label": "road_damage_candidate",
                         "conf": 0.0, "sev": None})
            events.append(self._event("road_surface", "road_damage_candidate",
                                      min(0.5 + 0.1 * c["hits"], 0.9), c["bbox"],
                                      t_wall, frame,
                                      {"src": "proposer", "growth": c["growth"],
                                       "persisted_frames": c["hits"],
                                       "note": "surface anomaly, not a classified pothole"},
                                      None))

        events = [e for e in events if e]
        for k, v in counts.items():
            self.counts_total[k] = self.counts_total.get(k, 0) + v
        for det in dets:
            if det["cat"] in ("vehicle", "pedestrian", "vru", "animal", "animal_road"):
                self.class_mix[det["label"]] = self.class_mix.get(det["label"], 0) + 1

        self._perf.append(time.time() - t0)
        self._perf = self._perf[-20:]
        self.ms_per_frame = round(float(np.mean(self._perf)) * 1000, 1)
        self.last_detections = dets
        self.last_analysis_t = time.time()
        self.congestion = extras.congestion_index(
            counts.get("vehicle", 0), self.speed_index, counts.get("pedestrian", 0))
        return {"camera": self.id, "detections": dets, "events": events,
                "counts": counts, "speed_index": round(self.speed_index, 1),
                "work_size": [self.W, self.H],
                "congestion": self.congestion,
                "plates_redacted": len(plates),
                "plates": [{"bbox": _norm(pl["bbox"], self.W, self.H),
                            "conf": round(float(pl["confidence"]), 3)}
                           for pl in plates],
                "ms": self.ms_per_frame}

    # -- events -------------------------------------------------------------
    def _event(self, category, label, confidence, bbox, t_wall, frame, attrs, severity):
        pub = getattr(self, "_frame_pub", None)
        if pub is not None:
            frame = pub                      # never thumbnail an unredacted plate
        x, y, w, h = [float(v) for v in bbox]
        if self._duplicate(label, x + w / 2, y + h / 2, t_wall):
            self.duplicates_suppressed += 1
            return None
        self.events_emitted += 1
        ev = {
            "camera": self.id, "camera_name": self.name, "position": self.position,
            "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_wall)),
            "t_wall": round(t_wall, 2),
            "category": category, "label": label,
            "confidence": round(float(confidence), 3),
            "bbox": [round(v, 1) for v in (x, y, w, h)],
            "bbox_norm": _norm([x, y, w, h], self.W, self.H),
            "speed_index": round(float(self.speed_index), 2),
            "severity": severity,
            "attrs": attrs,
        }
        if severity or label in CLASS_PRIORITY:
            # The picture an operator judges by. Thumbnails are deliberately left
            # OUT of the telemetry byte count, exactly as they would be on a bus:
            # evidence is fetched on demand, not pushed.
            ev["thumbnail_b64"] = _crop_b64(frame, bbox, max_px=320, quality=74)
        return ev

    def stats(self) -> dict:
        return {
            "id": self.id, "name": self.name, "position": self.position,
            "ms_per_frame": self.ms_per_frame,
            "events": self.events_emitted,
            "duplicates_suppressed": self.duplicates_suppressed,
            "own_vehicle_rejected": self.own_vehicle_rejected,
            "gate_rejected": dict(self.gate_rejected),
            "counts_total": dict(self.counts_total),
            "class_mix": dict(self.class_mix),
            "speed_index": round(self.speed_index, 1),
            "plates_redacted": getattr(self, "plates_seen", 0),
            "water_rejected": dict(getattr(self, "water_rejected", {})),
            "congestion": getattr(self, "congestion", None),
            "ego": {k: (self.learner.report if (self.learner and not self.learner.done)
                        else self.ego_report).get(k) for k in
                    ("ego_present", "reason", "state", "mask_frac", "split_half_iou",
                     "appearance_ratio", "pairs_used", "tests", "tests_passed",
                     "learned_live", "timed_out")},
        }

    def masks_png(self) -> dict:
        if not self.ready:
            return {}
        return {"ego_mask_png": _png_b64(self.ego),
                "road_plane_png": _png_b64(self.plane)}


def _norm(bbox, W, H) -> list[float]:
    """Boxes travel as fractions of the frame, so the browser can draw them over
    a preview of any size without knowing our working resolution."""
    x, y, w, h = bbox
    return [round(x / W, 4), round(y / H, 4), round(w / W, 4), round(h / H, 4)]


def _overlap_frac(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    return float(inter / max((ax1 - ax0) * (ay1 - ay0), 1e-6))


def _png_b64(mask) -> str:
    ok, buf = cv2.imencode(".png", mask)
    return base64.b64encode(buf).decode("ascii") if ok else ""


def _crop_b64(frame, bbox, max_px=320, quality=74) -> str | None:
    x, y, w, h = [int(v) for v in bbox]
    pad = int(0.18 * max(w, h))
    H, W = frame.shape[:2]
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
    if x1 <= x0 or y1 <= y0:
        return None
    crop = frame[y0:y1, x0:x1]
    s = max_px / max(crop.shape[0], crop.shape[1], 1)
    if s < 1:
        crop = cv2.resize(crop, (max(1, int(crop.shape[1] * s)),
                                 max(1, int(crop.shape[0] * s))))
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(buf).decode("ascii") if ok else None
