"""
PROJECT_V1 — trained road-damage detector
=========================================
Wraps a YOLO model fine-tuned on the Roboflow "Detection-Potholes-Classes"
dataset (CC BY 4.0, workspace `potholesdetection-aq76f`, v7).

The dataset's three classes are already severity-graded — safe / medium / risk —
which is a genuine gift: it means the detector reports not just *that* there is a
defect but *how bad it looks*. We keep those class names rather than collapsing
them, because prioritising repairs is the whole point for a road authority.

WHAT THIS IS AND IS NOT
    IS  : a learned classifier of road-damage APPEARANCE, with a severity grade
          learned from human annotation.
    NOT : a measurement of depth. A camera cannot measure how deep a hole is.
          The grade is visual severity, and it is labelled as such everywhere.

Team The RoadRunners · SIH 2026
"""
from __future__ import annotations

import os

import numpy as np


# Visual-severity ordering, worst first. Used for prioritisation, not for depth.
SEVERITY_RANK = {"risk-pothole": 3, "medium-pothole": 2, "safe-pothole": 1}
SEVERITY_LABEL = {
    "risk-pothole": "severe",
    "medium-pothole": "moderate",
    "safe-pothole": "minor",
}


class PotholeDetector:
    """Second-stage detector, run alongside the COCO model.

    Kept as its own model rather than merged into one network because the two
    have different lifecycles: the COCO detector is fixed and general, while
    this one gets retrained every time we gather more road-damage data. On the
    Pi they run sequentially on the same frame; the cost is measured and
    reported in the run summary so the trade-off is visible, not hidden.
    """

    def __init__(self, weights: str, cfg=None, conf: float = 0.35,
                 iou: float = 0.5, imgsz: int = 640):
        if not os.path.exists(weights):
            raise FileNotFoundError(
                f"Pothole weights not found: {weights}\n"
                f"Train them with notebooks/train_pothole_colab.ipynb, then place "
                f"best.pt at models/pothole_best.pt")
        from ultralytics import YOLO
        self.model = YOLO(weights)
        self.cfg = cfg
        self.conf = getattr(cfg, "conf", conf)
        self.iou = getattr(cfg, "iou", iou)
        self.imgsz = getattr(cfg, "imgsz", imgsz)
        self.max_area_frac = getattr(cfg, "max_area_frac", 0.10)
        self.base_band_frac = getattr(cfg, "base_band_frac", 0.30)
        self.min_plane_overlap = getattr(cfg, "min_plane_overlap", 0.50)
        self.max_ego_overlap = getattr(cfg, "max_ego_overlap", 0.35)
        self.max_aspect = getattr(cfg, "max_aspect", 6.0)
        self.names = self.model.names
        # Rejection bookkeeping. We COUNT what we throw away and why, because a
        # filter you cannot audit is indistinguishable from a filter that is
        # silently eating your true positives.
        self.rejected = {"area": 0, "aspect": 0, "plane": 0, "ego": 0}
        print(f"      pothole model classes: {list(self.names.values())}")

    @staticmethod
    def _overlap(mask, x0, y0, x1, y1) -> float:
        """Fraction of the box that lies inside a binary mask."""
        sub = mask[y0:y1, x0:x1]
        if sub.size == 0:
            return 0.0
        return float((sub > 0).mean())

    def detect(self, frame, plane=None, ego=None) -> list[dict]:
        """Detect road damage, then apply geometric sanity gates.

        The gates are as important as the network. A detector trained on
        close-up pothole photographs has never been shown a whole street, so
        when it meets tined concrete or a rubble-strewn verge it will
        occasionally answer with a box the size of the frame. That answer is
        not wrong in the model's own terms — it is a category error we can
        rule out with geometry, for free, and without touching the weights.

        Order matters: cheap scalar tests first, mask arithmetic last.
        """
        res = self.model.predict(frame, conf=self.conf, iou=self.iou,
                                 imgsz=self.imgsz, verbose=False)[0]
        out = []
        H, W = frame.shape[:2]
        frame_area = float(H * W)
        for box, cls, cf in zip(res.boxes.xyxy.cpu().numpy(),
                                res.boxes.cls.cpu().numpy().astype(int),
                                res.boxes.conf.cpu().numpy()):
            x1, y1, x2, y2 = [float(v) for v in box]
            bw, bh = max(x2 - x1, 1.0), max(y2 - y1, 1.0)

            # 1. Size. Perspective bounds how large a road-plane defect can look.
            area_frac = (bw * bh) / frame_area
            if area_frac > self.max_area_frac:
                self.rejected["area"] += 1
                continue

            # 2. Shape. A very long thin box is a surface texture or a kerb line.
            if max(bw / bh, bh / bw) > self.max_aspect:
                self.rejected["aspect"] += 1
                continue

            ix0, iy0 = max(0, int(x1)), max(0, int(y1))
            ix1, iy1 = min(W, int(x2)), min(H, int(y2))
            if ix1 <= ix0 or iy1 <= iy0:
                continue

            # 3. Place. The BASE of the box -- the strip where the defect meets
            #    the road -- must actually be road plane. Not the single base
            #    point the first version tested (any large box passes that by
            #    accident), and not the whole box either (a real pothole box
            #    legitimately pokes above the trapezoid edge). The base band is
            #    the part whose position on the road plane is physically meant.
            by0 = max(iy0, int(y2 - bh * self.base_band_frac))
            if plane is not None and self._overlap(plane, ix0, by0, ix1, iy1) < self.min_plane_overlap:
                self.rejected["plane"] += 1
                continue

            # 4. Not us. Same rule the COCO path uses for the ego vehicle.
            if ego is not None and self._overlap(ego, ix0, iy0, ix1, iy1) > self.max_ego_overlap:
                self.rejected["ego"] += 1
                continue

            raw = self.names.get(int(cls), str(cls))
            out.append({
                "bbox": [x1, y1, bw, bh],
                "raw_class": raw,
                "severity": SEVERITY_LABEL.get(raw, "unknown"),
                "severity_rank": SEVERITY_RANK.get(raw, 0),
                "confidence": float(cf),
                "area_frac": round(area_frac, 4),
            })
        # Worst first, so a truncated event stream still carries the worst news
        out.sort(key=lambda d: (-d["severity_rank"], -d["confidence"]))
        return out
