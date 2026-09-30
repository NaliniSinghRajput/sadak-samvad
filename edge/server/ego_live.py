"""
PROJECT_V3 — learning the ego mask from a LIVE camera
=====================================================

THE PROBLEM v1 AND v2 DID NOT HAVE
    Both earlier versions learned "which pixels are our own vehicle" by seeking
    around a file before playing it. A live camera cannot be rewound, so the
    node has to learn the same thing from frames as they arrive, while it is
    already working.

WHAT IS UNCHANGED, DELIBERATELY
    The mask itself, and the gate that decides whether to believe it, are the
    v2 code — `_ego_mask_from` and the two-of-three verdict are imported, not
    re-typed. Only the SOURCE of the frame pairs is different. If we had rewritten
    the gate here, the two would drift apart and the calibration table in
    `EgoMaskConfig` would stop describing what actually runs.

HOW IT COLLECTS
    Pairs of consecutive frames are taken from the live stream, spread over the
    first stretch of the run, and a pair is kept only if the vehicle was MOVING
    when it was taken (a stopped pair claims the whole world is bolted to the
    camera — the v2 lesson). When enough pairs survive, the gate runs once and
    the answer is published to the operator, with its numbers.

UNTIL IT HAS AN ANSWER
    There is no mask, so no ego rules are applied and the dashboard says
    "learning". It does not guess, and it does not pretend the calibration is
    done. A node that reports it cannot yet find its own vehicle is worth more
    than one that invents a mask out of dark road.
"""
from __future__ import annotations

import time

import cv2
import numpy as np

from vision import _ego_mask_from      # noqa: F401  (v2 code, reused on purpose)


class LiveEgoLearner:
    """Collects frame pairs from a live camera and runs v2's two-of-three gate."""

    def __init__(self, cfg, size: tuple[int, int], min_pairs: int = 12,
                 max_wait_s: float = 75.0, min_gap_s: float = 0.35):
        self.cfg = cfg
        self.size = size                       # (W, H) at working resolution
        self.min_pairs = int(min_pairs)
        self.max_wait_s = float(max_wait_s)
        self.min_gap_s = float(min_gap_s)
        self._prev_gray: np.ndarray | None = None
        self._prev_t: float = 0.0
        self._stack: list[np.ndarray] = []      # flow magnitude fields
        self._grays: list[np.ndarray] = []
        self._t0 = time.time()
        self.attempted = 0
        self.stopped_pairs = 0
        self.done = False
        self.report: dict = {"ego_present": False, "reason": "learning",
                             "pairs_used": 0, "state": "learning"}
        self.mask: np.ndarray | None = None

    # -- collection --------------------------------------------------------
    def offer(self, gray_small: np.ndarray, t: float) -> None:
        """Offer a working-resolution greyscale frame. Cheap when not needed."""
        if self.done:
            return
        fs = float(getattr(self.cfg, "flow_scale", 0.5) or 1.0)
        small_size = (max(32, int(self.size[0] * fs)), max(32, int(self.size[1] * fs)))
        s = cv2.resize(gray_small, small_size) if fs < 1.0 else gray_small

        if self._prev_gray is None or (t - self._prev_t) > 1.5:
            self._prev_gray, self._prev_t = s, t
            self._last_full = gray_small
            return
        if (t - self._prev_t) < self.min_gap_s:
            return

        self.attempted += 1
        flow = cv2.calcOpticalFlowFarneback(self._prev_gray, s, None,
                                            0.5, 3, 21, 3, 5, 1.2, 0)
        mag = np.linalg.norm(flow, axis=2)
        moving = float(np.median(mag)) / max(fs, 1e-6) >= self.cfg.min_scene_flow
        if moving:
            if small_size != self.size:
                mag = cv2.resize(mag, self.size,
                                 interpolation=cv2.INTER_LINEAR) / max(fs, 1e-6)
            self._stack.append(mag.astype(np.float32))
            self._grays.append(self._last_full.astype(np.float32))
        else:
            self.stopped_pairs += 1

        self._prev_gray, self._prev_t = s, t
        self._last_full = gray_small
        self.report |= {"pairs_used": len(self._stack), "attempted": self.attempted,
                        "stopped_pairs": self.stopped_pairs}

        if len(self._stack) >= self.min_pairs:
            self._decide()
        elif (time.time() - self._t0) > self.max_wait_s:
            self._decide(timed_out=True)

    # -- the verdict (v2's gate, unchanged) --------------------------------
    def _decide(self, timed_out: bool = False) -> None:
        W, H = self.size
        cfg = self.cfg
        report = {"ego_present": False, "reason": "", "mask_frac": 0.0,
                  "split_half_iou": None, "appearance_ratio": None,
                  "pairs_used": len(self._stack), "state": "done",
                  "learned_live": True, "timed_out": timed_out}

        if len(self._stack) < 4:
            report["reason"] = ("Not enough usable frame pairs — this camera has seen "
                                "almost no motion, so no ego mask was learned. "
                                "Running without one.")
            self.mask = np.zeros((H, W), np.uint8)
            self.report = report
            self.done = True
            return

        S = np.stack(self._stack, axis=0)
        full = _ego_mask_from(np.quantile(S, cfg.time_quantile, axis=0), cfg, self.size)
        report["mask_frac"] = round(float((full > 0).mean()), 4)
        if not full.any():
            report["reason"] = "No candidate region survived the geometric priors."
            self.mask = full
            self.report = report
            self.done = True
            return

        a = _ego_mask_from(np.quantile(S[0::2], cfg.time_quantile, axis=0), cfg, self.size)
        b = _ego_mask_from(np.quantile(S[1::2], cfg.time_quantile, axis=0), cfg, self.size)
        union = float(((a > 0) | (b > 0)).sum())
        iou = float(((a > 0) & (b > 0)).sum()) / max(union, 1.0)
        report["split_half_iou"] = round(iou, 3)

        sd = np.stack(self._grays, axis=0).std(axis=0)
        lower = slice(int(H * cfg.search_from_row), H)
        band = sd[lower, :]
        inside = sd[full > 0]
        outside = band[full[lower, :] == 0]
        ratio = (float(np.median(inside)) / max(float(np.median(outside)), 1e-6)
                 if inside.size and outside.size else 0.0)
        report["appearance_ratio"] = round(ratio, 3)

        # A FOURTH CHECK, ADDED IN v3 AFTER A LETTERBOXED FEED FOOLED THE OTHER
        # THREE. A tile cut out of a 2x2 screen recording — and some phone apps
        # in portrait — carry black bars. A black bar has no flow and perfect
        # split-half agreement, so it scored 0.99 on the strongest test and was
        # accepted as a vehicle body. It is not: it is a region that does not
        # change at all. A real bonnet vibrates, reflects and is lit; it has SOME
        # variation. So a candidate whose pixels are effectively constant is
        # vetoed outright, whatever the majority says.
        inside_sd = float(np.median(sd[full > 0])) if (sd[full > 0]).size else 0.0
        if inside_sd < 0.6:
            report["inside_sd"] = round(inside_sd, 3)
            report["reason"] = (
                "The candidate region does not change at all between frames "
                f"(temporal variation {inside_sd:.2f}). That is a blank border or a "
                "dead area of the picture, not a vehicle body. Running without a mask.")
            report["tests"] = {"constant-region veto": {"value": round(inside_sd, 3),
                                                        "threshold": 0.6, "passed": False}}
            self.mask = np.zeros((H, W), np.uint8)
            self.report = report
            self.done = True
            return

        checks = [
            ("split-half agreement", iou, cfg.min_split_half_iou,
             "the two halves of the feed disagree about where it is"),
            ("mask fraction", report["mask_frac"], cfg.min_mask_frac,
             "the region is too small to be a vehicle body"),
            ("appearance", ratio, cfg.min_appearance_ratio,
             "the region has less structure than the road around it"),
        ]
        passed = [c for c in checks if c[1] >= c[2]]
        failed = [c for c in checks if c[1] < c[2]]
        report["tests"] = {c[0]: {"value": round(float(c[1]), 3), "threshold": c[2],
                                  "passed": c[1] >= c[2]} for c in checks}
        report["tests_passed"] = len(passed)

        if len(passed) < cfg.min_tests_passed:
            report["reason"] = (
                "No ego vehicle is visible on this camera: only "
                f"{len(passed)} of {len(checks)} checks passed. " +
                "; ".join(f"{c[3]} ({c[1]:.2f} < {c[2]})" for c in failed) +
                ". Running without a mask.")
            self.mask = np.zeros((H, W), np.uint8)
        else:
            report["ego_present"] = True
            report["reason"] = (
                f"Ego vehicle found — {report['mask_frac']*100:.1f}% of frame, "
                f"agreement {iou:.2f}, structure {ratio:.2f}, "
                f"{len(passed)} of {len(checks)} checks passed.")
            self.mask = full

        self.report = report
        self.done = True
        # The stacks are the biggest thing this object holds; let them go.
        self._stack, self._grays = [], []
