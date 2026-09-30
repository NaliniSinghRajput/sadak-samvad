"""
PROJECT_V2 — vision primitives
==============================
Ego-vehicle masking, road region of interest, ego-motion, and the road-surface
damage proposer. These are the parts that do not depend on any trained model,
which is exactly why they are worth having: they work on day one, they cost
almost nothing on a Raspberry Pi, and they carry real engineering content.

Team The RoadRunners · SIH 2026
"""
from __future__ import annotations

import cv2
import numpy as np

from config import (EgoMaskConfig, RoadROIConfig, MotionConfig, SurfaceConfig,
                    WaterConfig)


# ---------------------------------------------------------------------------
# 1. EGO-VEHICLE MASK
# ---------------------------------------------------------------------------
def learn_ego_mask(video_path: str, cfg: EgoMaskConfig, size: tuple[int, int]) -> np.ndarray:
    """Find the pixels that belong to our own vehicle, using MOTION not brightness.

    The physical fact we exploit: anything bolted to the vehicle — handlebars,
    mirrors, a bus bonnet, wiper arms, the A-pillar, the dashboard — is rigidly
    attached to the camera, so its optical flow relative to the camera is
    essentially zero no matter how violently the vehicle shakes. The world does
    the opposite: it streams past with large flow.

    An earlier version thresholded temporal intensity variance. That failed on
    this footage, because a bike-mounted camera shakes hard enough to give the
    handlebars real variance, while a uniformly dark stretch of muddy road has
    almost none — so it masked the road and left the bike exposed. Flow does not
    have that failure mode, because it measures the right thing.

    Two geometric priors finish the job:
      * the ego vehicle lives in the lower part of the frame, and
      * it always touches the bottom edge — a floating blob in the middle of the
        road is road, not vehicle.

    Returns uint8: 255 = ego vehicle (ignore), 0 = world (process).
    """
    stack, _ = _ego_flow_stack(video_path, cfg, size)
    h, w = size[1], size[0]
    if len(stack) < 3:
        return np.zeros((h, w), np.uint8)
    mag = np.quantile(np.stack(stack, axis=0), cfg.time_quantile, axis=0)
    return _ego_mask_from(mag, cfg, size)


def _spread_order(n: int) -> list[int]:
    """Visit 0..n-1 in an order that is always spread over the whole range.

    Recursive bisection: 0, n-1, middle, quarters, eighths... Stopping after any
    prefix of this order still leaves you with samples covering the entire video
    rather than the first thirty seconds of it. That property is what lets the
    sampler below quit early on a feed where the vehicle is moving, without
    biasing the ego mask toward one stretch of road.
    """
    if n <= 0:
        return []
    order, seen = [], set()

    def push(i):
        if 0 <= i < n and i not in seen:
            seen.add(i)
            order.append(i)

    push(0)
    push(n - 1)
    step = n
    while len(order) < n and step > 1:
        step = max(1, step // 2)
        for i in range(step, n, step):
            push(i)
    for i in range(n):
        push(i)
    return order


def _ego_flow_stack(video_path: str, cfg: EgoMaskConfig, size: tuple[int, int]):
    """Sample frame PAIRS across the whole video and return their flow fields.

    Also returns the first frame of each pair in greyscale, because the validity
    check in `learn_ego_mask_checked` needs to ask what the region LOOKS like,
    not only how it moves.

    ADAPTIVE SINCE v2. A pair is only usable if the vehicle was MOVING when it
    was taken — a stopped pair says "the whole world is bolted to the camera",
    which is the single most damaging thing it could contribute. v1 sampled a
    fixed 60 frames and accepted whatever survived that filter. On footage shot
    crawling through stalled traffic that left as few as 12 usable pairs, and the
    learner missed a bonnet that fills a fifth of the frame (the numbers are in
    EgoMaskConfig). So the budget is now driven by the result, not by a constant:
    keep sampling, always spread across the whole video, until `min_usable_pairs`
    survive or `max_sample_frames` is exhausted.

    The ceiling matters as much as the floor. A bus parked at a depot never
    produces a usable pair, and a sampler with no ceiling would read the entire
    file looking for one.
    """
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1

    floor_pairs = max(8, cfg.sample_frames // 2)
    max_pairs = max(floor_pairs, cfg.max_sample_frames // 2)
    want = int(getattr(cfg, "min_usable_pairs", 24))

    fs = float(getattr(cfg, "flow_scale", 1.0) or 1.0)
    small = (max(32, int(size[0] * fs)), max(32, int(size[1] * fs))) if fs < 1.0 else size

    starts = np.linspace(0, max(total - 3, 0), max_pairs).astype(int)
    stack, grays = [], []
    attempted = 0
    for k in _spread_order(len(starts)):
        # Enough usable pairs AND at least the opening budget looked at: stop.
        if len(stack) >= want and attempted >= floor_pairs:
            break
        attempted += 1
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(starts[k]))
        ok1, f1 = cap.read()
        ok2, f2 = cap.read()
        if not (ok1 and ok2):
            continue
        s1 = cv2.cvtColor(cv2.resize(f1, small), cv2.COLOR_BGR2GRAY)
        s2 = cv2.cvtColor(cv2.resize(f2, small), cv2.COLOR_BGR2GRAY)
        flow = cv2.calcOpticalFlowFarneback(s1, s2, None, 0.5, 3, 21, 3, 5, 1.2, 0)
        mag = np.linalg.norm(flow, axis=2)
        # DISCARD PAIRS WHERE THE VEHICLE IS STOPPED. When the bus is at a signal
        # every pixel has near-zero flow, so a stopped pair says "the whole world
        # is bolted to the camera" — the single most damaging thing it could
        # contribute to this estimate. The threshold is in px/frame at FULL
        # resolution, so a flow field measured at reduced scale is rescaled back
        # before the comparison — otherwise halving the scale would silently
        # halve the motion threshold too.
        if float(np.median(mag)) / max(fs, 1e-6) < cfg.min_scene_flow:
            continue
        if small != size:
            mag = cv2.resize(mag, size, interpolation=cv2.INTER_LINEAR) / max(fs, 1e-6)
        stack.append(mag.astype(np.float32))
        grays.append(cv2.cvtColor(cv2.resize(f1, size), cv2.COLOR_BGR2GRAY).astype(np.float32))
    cap.release()
    return stack, grays



def _ego_mask_from(mag: np.ndarray, cfg: EgoMaskConfig,
                   size: tuple[int, int]) -> np.ndarray:
    """Threshold one flow field into a candidate ego mask."""
    h, w = size[1], size[0]
    # Low flow == moves with the camera == part of our vehicle
    lower = slice(int(h * cfg.search_from_row), h)
    thr = np.percentile(mag[lower, :], cfg.var_percentile)
    mask = np.zeros((h, w), np.uint8)
    mask[lower, :] = ((mag[lower, :] <= thr) * 255).astype(np.uint8)

    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.dilate_px, cfg.dilate_px))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))

    # Keep only substantial blobs that REACH THE BOTTOM EDGE of the frame
    nlab, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = np.zeros_like(mask)
    min_area = cfg.min_blob_frac * h * w
    for i in range(1, nlab):
        if stats[i, cv2.CC_STAT_AREA] < min_area:
            continue
        touches_bottom = (stats[i, cv2.CC_STAT_TOP] +
                          stats[i, cv2.CC_STAT_HEIGHT]) >= h - 3
        if touches_bottom:
            out[lab == i] = 255

    # Fill interior holes: a bright, reflective instrument cluster can register
    # apparent flow and punch a hole in the middle of the vehicle. The vehicle
    # is a solid body, so we fill its outline.
    cnts, _ = cv2.findContours(out, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(out)
    for c in cnts:
        cv2.drawContours(filled, [c], -1, 255, thickness=cv2.FILLED)
    return cv2.dilate(filled, k)



# ---------------------------------------------------------------------------
# 1b. RIGID-ATTACHMENT TEST  (added 2026-08-26)
# ---------------------------------------------------------------------------
class RigidityTest:
    """Is this detection part of OUR OWN vehicle? Asked per frame, not once.

    WHY THE LEARNED MASK WAS NOT ENOUGH
        `learn_ego_mask` produces ONE static mask for the whole video. That is
        exactly right for a bus: the bonnet is where the bonnet is. It is not
        right for this footage, where the rider's arms swing, the bike leans,
        and every so often he tips his head and the camera fills with his own
        knee and fuel tank. A static mask cannot cover a body that moves, and
        the audit showed the cost plainly -- his hands were being reported as
        `person` in most frames, and four times as a pothole.

    THE PHYSICS, WHICH IS THE SAME PHYSICS AS THE MASK
        Anything rigidly attached to the camera has near-zero optical flow
        relative to it, however hard the vehicle shakes. The world streams past.
        So instead of asking "is this box inside the region that was static on
        average across the whole video?", we ask the question we actually mean,
        about this box, in this frame:

            is the flow inside this box far below the flow of the scene around it?

        A hand on the throttle: yes, always. A pedestrian we are driving past:
        no, never. And it costs one Farneback call per frame at quarter
        resolution -- a few milliseconds -- because we do not need precision,
        only the ratio.

    OUTCOME: THIS DOES NOT WORK, AND IS DISABLED BY DEFAULT
        Measured on this footage, the flow inside the ego region came out EQUAL
        TO OR HIGHER THAN the flow of the near-field road, at every baseline
        from 20 ms to 160 ms and at both quarter and half resolution. The
        reason is a property of dense flow, not of the idea: the handlebars and
        the rider's gloved hands are dark and almost textureless, and Farneback
        regularises textureless regions by borrowing motion from their
        neighbours. So the ego region gets handed the world's motion, and the
        ratio the whole test rests on collapses to 1.

        The idea is sound and would work with a textured ego region or a
        feature-tracking formulation. It does not work here. It stays in the
        tree, disabled, with its numbers, because a method that failed for a
        understood reason is a result -- and because the next person to have
        this idea should find the measurement already done.

    THE ONE TRAP, AND THE GUARD
        Distant objects near the vanishing point ALSO have low flow: a car
        150 m ahead travelling at our speed barely moves in the image. Calling
        that rigid would delete exactly the traffic we are paid to count. So the
        test is applied only BELOW `apply_below_row` -- the near field, where our
        own bodywork lives and where nothing distant can appear. The same
        geometric prior that makes the mask work makes this safe.
    """

    def __init__(self, cfg):
        self.scale = cfg.flow_scale
        self.ratio = cfg.rigid_ratio
        self.min_scene_flow = cfg.min_scene_flow
        self.apply_below_row = cfg.apply_below_row
        self.prev = None
        self.mag = None
        self.scene = 0.0
        self.n_rigid = 0

    def update(self, gray: np.ndarray, gray_next: np.ndarray | None,
               roi: np.ndarray | None = None) -> None:
        """Measure flow across a SHORT baseline: this frame and the next one.

        The reference `self.scene` is the flow of the NEAR-FIELD ROAD, not of
        the whole frame. That distinction was the second thing this test got
        wrong: a whole-frame median is dominated by sky and far distance, which
        barely move even at speed, so it came out around 2 px and the rider's
        hands -- moving about as little -- did not look unusual against it. The
        road a few metres ahead is what streams past fastest and is the only
        honest yardstick for "the world is moving and this thing is not".
        """
        if gray_next is None:
            self.mag = None
            return
        f = lambda g: cv2.resize(g, None, fx=self.scale, fy=self.scale,
                                 interpolation=cv2.INTER_AREA)
        flow = cv2.calcOpticalFlowFarneback(f(gray), f(gray_next), None,
                                            0.5, 3, 15, 3, 5, 1.1, 0)
        self.mag = np.linalg.norm(flow, axis=2)

        h = self.mag.shape[0]
        band = self.mag[int(h * self.apply_below_row):, :]
        if roi is not None:
            r = cv2.resize(roi, (self.mag.shape[1], h),
                           interpolation=cv2.INTER_NEAREST)[int(h * self.apply_below_row):, :]
            vals = band[r > 0]
            if vals.size < 50:
                vals = band.reshape(-1)
        else:
            vals = band.reshape(-1)
        self.scene = float(np.median(vals)) if vals.size else 0.0

    def is_rigid(self, x0: int, y0: int, x1: int, y1: int, H: int) -> bool:
        """True if this box moves with the camera rather than with the world."""
        if self.mag is None:
            return False
        # Only judge the near field. Above this row, low flow means 'far away',
        # not 'attached to us', and deleting it would delete real traffic.
        if (y0 + y1) / 2.0 < H * self.apply_below_row:
            return False
        # If the vehicle is stopped the whole frame is static and the ratio is
        # meaningless. Abstain rather than guess.
        if self.scene < self.min_scene_flow:
            return False
        s = self.scale
        a, b = int(y0 * s), max(int(y1 * s), int(y0 * s) + 1)
        c, d = int(x0 * s), max(int(x1 * s), int(x0 * s) + 1)
        sub = self.mag[a:b, c:d]
        if sub.size == 0:
            return False
        if float(np.median(sub)) < self.ratio * self.scene:
            self.n_rigid += 1
            return True
        return False



# ---------------------------------------------------------------------------
# 1a. IS THERE AN EGO VEHICLE AT ALL?  (added 2026-08-26)
# ---------------------------------------------------------------------------
def learn_ego_mask_checked(video_path: str, cfg: EgoMaskConfig,
                           size: tuple[int, int]) -> tuple[np.ndarray, dict]:
    """`learn_ego_mask`, plus the question it could never answer: is this real?

    THE FAILURE THIS EXISTS TO STOP
        `learn_ego_mask` always returns a mask. It takes the lowest percentile of
        flow in the lower frame and calls that the vehicle — so it finds an "ego
        vehicle" in footage that contains none. On a night ride where the camera
        sees no part of its own vehicle, the darkest, most textureless patches of
        road have the least measurable flow, and the road itself gets masked out:
        a black smear over the carriageway in every frame, deleting real
        detections underneath it.

        That is not a threshold that needs tuning. It is a missing concept. A
        method that cannot return "nothing here" will hallucinate something.

    THE TWO TESTS, AND WHY THE OBVIOUS ONE DOES NOT WORK
        The tempting test is separation: is the flow inside the mask much lower
        than outside? It is useless, and measurably so — the mask is DEFINED as
        the low tail, so its flow is always lower. Measured: 0.29 on footage with
        a real ego vehicle, 0.23 on footage with none. It ranks the false case as
        the better one. The test is circular.

        What actually distinguishes a real ego vehicle:

        1. SPLIT-HALF AGREEMENT. Bolt something to the camera and it is in the
           same pixels for the whole ride. So build the mask twice, from two
           independent halves of the sampled frames, and intersect them. A real
           object is found by both. Noise is not.
           Measured: IoU 0.573 with handlebars in view, 0.345 without.

        2. APPEARANCE. A real object has edges and structure, so its pixels vary
           at least as much over time as the road does — a shaking camera sees to
           that. A false mask lands where it does precisely BECAUSE that region is
           dark and flat, so it varies less.
           Measured: ratio 1.13 with handlebars in view, 0.63 without.

           Note the direction, because it is the opposite of the intuition that
           "the ego vehicle is static, so it must vary less". That intuition is
           what made the very first version of this module threshold intensity
           variance, and it is why that version masked the road and left the bike
           exposed. Same trap, third visit.

    v2 — WHAT CHANGED, AND WHAT DID NOT
        Both tests above survive unchanged, and a third was added: the MASK
        FRACTION itself. Re-measured across five videos (table in
        EgoMaskConfig), the appearance test was found to fail in the
        false-negative direction on a wet, mirrored carriageway, because its
        denominator is the road's own temporal variation and a road full of
        moving reflections inflates it. On that footage a real bonnet scored
        0.713 while the known negative scored 0.653 — the test could not
        separate them. Split-half agreement (0.173 vs 0.445+) and mask fraction
        (1.9% vs 16.3%+) separate them cleanly, on two independent axes.

        So the verdict is now a MAJORITY of three rather than a unanimous two.
        Nothing was deleted; the weakest test lost its power of veto and kept
        its place in the report. That is the difference between following
        evidence and bending a threshold to fit a case.

    WHEN IN DOUBT, NO MASK. A missing mask costs us: the host vehicle may be
    reported as traffic. A wrong mask costs more: it blacks out the road, hides
    real defects, and is the first thing anyone watching will ask about. So the
    gate is deliberately biased toward returning nothing, and it SAYS SO — the
    report travels to the dashboard and is shown to the operator rather than
    quietly swallowed.

    Calibrated on two videos, which is a thin basis and is stated as such. If a
    third video is misjudged, widen the margin rather than deleting the test.

    Returns (mask, report). `mask` is all zeros when no ego vehicle is found.
    """
    stack, grays = _ego_flow_stack(video_path, cfg, size)
    h, w = size[1], size[0]
    report = {"ego_present": False, "reason": "", "mask_frac": 0.0,
              "split_half_iou": None, "appearance_ratio": None,
              "pairs_used": len(stack)}

    if len(stack) < 4:
        report["reason"] = ("Not enough usable frame pairs — the vehicle appears "
                            "stationary for most of this feed.")
        return np.zeros((h, w), np.uint8), report

    S = np.stack(stack, axis=0)
    full = _ego_mask_from(np.quantile(S, cfg.time_quantile, axis=0), cfg, size)
    report["mask_frac"] = round(float((full > 0).mean()), 4)
    if not full.any():
        report["reason"] = "No candidate region survived the geometric priors."
        return full, report

    a = _ego_mask_from(np.quantile(S[0::2], cfg.time_quantile, axis=0), cfg, size)
    b = _ego_mask_from(np.quantile(S[1::2], cfg.time_quantile, axis=0), cfg, size)
    union = float(((a > 0) | (b > 0)).sum())
    iou = float(((a > 0) & (b > 0)).sum()) / max(union, 1.0)
    report["split_half_iou"] = round(iou, 3)

    sd = np.stack(grays, axis=0).std(axis=0)
    lower = slice(int(h * cfg.search_from_row), h)
    band = sd[lower, :]
    inside = sd[full > 0]
    outside = band[full[lower, :] == 0]
    ratio = (float(np.median(inside)) / max(float(np.median(outside)), 1e-6)
             if inside.size and outside.size else 0.0)
    report["appearance_ratio"] = round(ratio, 3)

    # -- THE GATE: TWO OF THREE ------------------------------------------
    # v1 required both tests to pass. Re-measured across five videos (the table
    # is in EgoMaskConfig), the appearance test turned out to fail in the
    # FALSE-NEGATIVE direction on a wet, mirrored carriageway: its denominator is
    # the road's own temporal variation, and a road full of moving reflections
    # inflates that denominator until a perfectly real bonnet scores 0.713. The
    # known negative scores 0.653, so appearance alone cannot separate those two
    # at all. Split-half agreement and mask fraction can, and do, on two
    # independent axes simultaneously.
    #
    # So the weakest test lost its veto rather than its place. All three are
    # still computed, all three are still reported to the operator, and the
    # verdict now needs a majority. The negative fails all three; every positive
    # we have passes at least two.
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
            "No ego vehicle is visible in this feed: only "
            f"{len(passed)} of {len(checks)} checks passed. " +
            "; ".join(f"{c[3]} ({c[1]:.2f} < {c[2]})" for c in failed) +
            ". Running without a mask.")
        return np.zeros((h, w), np.uint8), report

    report["ego_present"] = True
    report["reason"] = (
        f"Ego vehicle found — {report['mask_frac']*100:.1f}% of frame, "
        f"agreement {iou:.2f}, structure {ratio:.2f}, "
        f"{len(passed)} of {len(checks)} checks passed"
        + ("." if not failed else
           " (" + ", ".join(f"{c[0]} {c[1]:.2f} below {c[2]}" for c in failed) + ")."))
    return full, report

# ---------------------------------------------------------------------------
# 2. ROAD REGION OF INTEREST
# ---------------------------------------------------------------------------
def road_roi(size: tuple[int, int], cfg: RoadROIConfig, ego: np.ndarray | None) -> np.ndarray:
    """A trapezoid of drivable surface, minus our own vehicle.

    Nothing above the horizon is road. This is not sophisticated, but it is
    correct, it is free, and it removes the sky, the buildings and the trees —
    which is where most false positives would otherwise come from.
    """
    w, h = size
    y_top = int(h * cfg.horizon_frac)
    y_bot = int(h * cfg.bottom_frac)
    tw, bw = cfg.top_width_frac * w, cfg.bottom_width_frac * w
    cx = w / 2.0
    poly = np.array([[
        (cx - tw / 2, y_top), (cx + tw / 2, y_top),
        (cx + bw / 2, y_bot), (cx - bw / 2, y_bot),
    ]], np.int32)

    roi = np.zeros((h, w), np.uint8)
    cv2.fillPoly(roi, poly, 255)
    if ego is not None:
        roi[ego > 0] = 0
    return roi


# ---------------------------------------------------------------------------
# 3. EGO-MOTION  (relative speed index — NOT km/h)
# ---------------------------------------------------------------------------
class EgoMotion:
    """Median sparse optical-flow magnitude inside the road region.

    Monotonic in speed, cheap, and needs no calibration. We use it to normalise
    severity (the same hole hit faster produces a bigger signal), to detect
    stops, and to predict how fast a road-plane object should grow as we
    approach it — which is the test that separates a hole from a shadow.
    """

    def __init__(self, cfg: MotionConfig):
        self.cfg = cfg
        self.prev = None
        self.index = 0.0            # smoothed flow magnitude, px/frame

    def update(self, gray: np.ndarray, roi: np.ndarray) -> float:
        c = self.cfg
        if self.prev is None:
            self.prev = gray
            return self.index

        pts = cv2.goodFeaturesToTrack(self.prev, c.max_corners, c.quality,
                                      c.min_distance, mask=roi)
        raw = 0.0
        if pts is not None and len(pts) > 8:
            nxt, st, _ = cv2.calcOpticalFlowPyrLK(
                self.prev, gray, pts, None,
                winSize=(c.win_size, c.win_size), maxLevel=3,
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03))
            if nxt is not None and st is not None:
                good_old = pts[st.ravel() == 1].reshape(-1, 2)
                good_new = nxt[st.ravel() == 1].reshape(-1, 2)
                if len(good_old) > 8:
                    d = np.linalg.norm(good_new - good_old, axis=1)
                    raw = float(np.median(d))

        a = c.smooth_alpha
        self.index = a * raw + (1 - a) * self.index
        self.prev = gray
        return self.index


# ---------------------------------------------------------------------------
# 4. ROAD-SURFACE DAMAGE PROPOSER
# ---------------------------------------------------------------------------
class SurfaceProposer:
    """Proposes candidate road-damage regions, then makes them earn it.

    Stage 1 — appearance. A pothole is a depression, so it holds shadow: it is
    locally darker than the surrounding road. Adaptive thresholding finds
    locally-dark patches without caring about global brightness, so it survives
    sun, shade and dust.

    Stage 2 — geometry. Filter on area, aspect ratio and solidity. Reject
    anything above the row limit, where a defect is too few pixels to judge.

    Stage 3 — PHYSICS, which is the part that matters. Track each candidate
    across frames. A real object lying on the road plane must approach the
    camera and grow. A shadow cast on the road by a tree or a wire does not
    grow the same way, and noise does not persist at all. A candidate is only
    promoted to a detection once it has survived `persist_frames` consecutive
    frames while behaving like something physically on the road.

    This is a proposer, not a classifier. It says "the surface here is
    anomalous", not "this is a pothole of depth d". That distinction is kept
    everywhere downstream, deliberately.
    """

    def __init__(self, cfg: SurfaceConfig):
        self.cfg = cfg
        self.tracks: list[dict] = []
        self._next_id = 1

    def _candidates(self, bgr: np.ndarray, roi: np.ndarray) -> list[dict]:
        c = self.cfg
        h, w = roi.shape

        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        L = cv2.GaussianBlur(lab[:, :, 0], (c.blur_ksize, c.blur_ksize), 0)

        bs = c.block_size if c.block_size % 2 == 1 else c.block_size + 1
        dark = cv2.adaptiveThreshold(L, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY_INV, bs, c.c_offset)
        dark = cv2.bitwise_and(dark, roi)
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((c.open_ksize,) * 2, np.uint8))
        dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((c.close_ksize,) * 2, np.uint8))

        cnts, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out = []
        max_area = c.max_area_frac * h * w
        for cnt in cnts:
            area = cv2.contourArea(cnt)
            if area < c.min_area_px or area > max_area:
                continue
            x, y, bw_, bh_ = cv2.boundingRect(cnt)
            cy = y + bh_ / 2
            if cy < h * c.min_row_frac:
                continue
            ar = max(bw_, bh_) / max(min(bw_, bh_), 1)
            if ar > c.max_aspect:
                continue
            hull = cv2.convexHull(cnt)
            ha = cv2.contourArea(hull)
            solidity = area / ha if ha > 0 else 0
            if solidity < c.min_solidity:
                continue
            out.append({"bbox": (x, y, bw_, bh_), "area": float(area),
                        "centroid": (x + bw_ / 2, cy), "solidity": float(solidity)})
        return out

    def update(self, bgr: np.ndarray, roi: np.ndarray) -> list[dict]:
        """Returns candidates that have been CONFIRMED this frame."""
        c = self.cfg
        cands = self._candidates(bgr, roi)

        for t in self.tracks:
            t["matched"] = False

        confirmed = []
        for cand in cands:
            best, best_d = None, c.match_dist_px
            cx, cy = cand["centroid"]
            for t in self.tracks:
                if t["matched"]:
                    continue
                d = float(np.hypot(cx - t["centroid"][0], cy - t["centroid"][1]))
                if d < best_d:
                    best, best_d = t, d

            if best is None:
                self.tracks.append({
                    "id": self._next_id, "centroid": cand["centroid"],
                    "bbox": cand["bbox"], "area": cand["area"],
                    "hits": 1, "misses": 0, "matched": True,
                    "growth": 1.0, "reported": False,
                })
                self._next_id += 1
                continue

            # PHYSICS GATE: something on the road plane grows as we close on it
            growth = cand["area"] / max(best["area"], 1.0)
            best.update(centroid=cand["centroid"], bbox=cand["bbox"],
                        area=cand["area"], hits=best["hits"] + 1,
                        misses=0, matched=True,
                        growth=0.6 * best["growth"] + 0.4 * growth)

            approaching = best["growth"] >= 0.95      # not shrinking = plausible
            if best["hits"] >= c.persist_frames and approaching and not best["reported"]:
                best["reported"] = True
                confirmed.append({
                    "track_id": best["id"], "bbox": best["bbox"],
                    "area": best["area"], "growth": round(best["growth"], 3),
                    "hits": best["hits"], "solidity": cand["solidity"],
                })

        for t in self.tracks:
            if not t["matched"]:
                t["misses"] += 1
        self.tracks = [t for t in self.tracks if t["misses"] <= 2]
        return confirmed


# ---------------------------------------------------------------------------
# 5. WATERLOGGING PROPOSER  (new in v2)
# ---------------------------------------------------------------------------
class WaterloggingProposer:
    """Standing water on the carriageway, proposed from specular reflections.

    PS 26124 asks for waterlogging detection. v1 answered honestly that it had
    no training data and no footage containing any, and left the capability
    tagged DESIGNED. The Naini runs are a monsoon night on a flooded street, so
    the honest answer has changed — but only as far as the evidence allows.

    THE SIGNATURE, AND WHY IT IS NOT "LOOK FOR A DARK PATCH"
        A dark patch is a shadow, a tar repair, or a badly exposed frame. What
        actually distinguishes standing water at night is that it is a MIRROR:
        every street lamp above it appears again in it, and small ripples smear
        each reflection into a tall, thin, vertical streak. Dry asphalt under the
        same lamp scatters light diffusely and gives a broad soft pool with no
        streaks at all.

        So the test is compound, and every part of it is a physical statement:
          1. several BRIGHT blobs, on the road plane, in the near or mid field;
          2. each blob VERTICALLY ELONGATED — that is the ripple smear, and it is
             the part a dry reflective surface does not reproduce;
          3. those blobs CLOSE TOGETHER — one reflection is a lamp, a cluster of
             them is a continuous reflective sheet, which is what a puddle is;
          4. the surface BETWEEN them no rougher than the road elsewhere — water
             fills the texture in, gravel and rubble do not.

    WHAT IT DELIBERATELY DOES NOT CLAIM
        Not depth. Not "flooded" versus "damp". Not daylight — with no point
        sources there are no specular streaks, and this proposer will simply stay
        quiet, which is the correct behaviour for a method whose physics has
        stopped applying. Every region it finds is emitted as
        `waterlogging_candidate` and goes to an operator, exactly like the
        classical surface proposer it is modelled on.
    """

    def __init__(self, cfg: WaterConfig):
        self.cfg = cfg
        self._tracks: list[dict] = []      # {cx, cy, hits, emitted, last_seen}
        self._frame_i = 0

    # -- internals ---------------------------------------------------------
    def _streaks(self, gray, roi):
        c = self.cfg
        H, W = gray.shape[:2]
        vals = gray[roi > 0]
        if vals.size < 500:
            return [], None
        thr = float(np.percentile(vals, c.bright_percentile))
        bright = ((gray >= thr) & (roi > 0)).astype(np.uint8) * 255
        bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN,
                                  cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
        n, lab, stats, cent = cv2.connectedComponentsWithStats(bright, 8)
        out = []
        max_area = c.max_streak_frac * H * W
        for i in range(1, n):
            a = stats[i, cv2.CC_STAT_AREA]
            w = max(1, stats[i, cv2.CC_STAT_WIDTH])
            h = stats[i, cv2.CC_STAT_HEIGHT]
            if a < c.min_streak_px or a > max_area:
                continue
            if h / w < c.min_streak_aspect:
                continue
            out.append({"cx": float(cent[i][0]), "cy": float(cent[i][1]),
                        "bbox": (int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                                 int(w), int(h)), "area": int(a)})
        return out, bright

    @staticmethod
    def _texture(gray, mask):
        """Mean absolute Laplacian inside a mask — a scale-free roughness proxy."""
        if mask is None or not np.any(mask):
            return 0.0
        lap = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
        return float(np.abs(lap[mask > 0]).mean())

    # -- api ---------------------------------------------------------------
    def update(self, bgr, plane, ego=None) -> list[dict]:
        c = self.cfg
        if not c.enabled:
            return []
        self._frame_i += 1
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        H, W = gray.shape[:2]

        roi = (plane > 0).astype(np.uint8) * 255
        roi[:int(H * c.min_row_frac), :] = 0
        if ego is not None:
            roi[ego > 0] = 0

        streaks, bright = self._streaks(gray, roi)
        if len(streaks) < c.min_streaks:
            self._age()
            return []

        # 3. cluster the streaks into candidate sheets
        canvas = np.zeros((H, W), np.uint8)
        for st in streaks:
            x, y, w, h = st["bbox"]
            cv2.rectangle(canvas, (x, y), (x + w, y + h), 255, -1)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                      (c.cluster_dilate_px, c.cluster_dilate_px))
        sheets = cv2.morphologyEx(canvas, cv2.MORPH_CLOSE, k)
        sheets = cv2.dilate(sheets, k)
        sheets[roi == 0] = 0

        n, lab, stats, cent = cv2.connectedComponentsWithStats(sheets, 8)
        road_tex = self._texture(gray, ((roi > 0) & (bright == 0)).astype(np.uint8))
        found = []
        for i in range(1, n):
            area = stats[i, cv2.CC_STAT_AREA]
            frac = area / float(H * W)
            if frac < c.min_region_frac or frac > c.max_region_frac:
                continue
            region = (lab == i).astype(np.uint8)
            inside = sum(1 for st in streaks if region[int(np.clip(st["cy"], 0, H - 1)),
                                                       int(np.clip(st["cx"], 0, W - 1))] > 0)
            if inside < c.min_streaks:
                continue
            reg_tex = self._texture(gray, ((region > 0) & (bright == 0)).astype(np.uint8))
            if road_tex > 0 and reg_tex > road_tex * c.smoothness_ratio:
                continue
            x, y = stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP]
            w, h = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
            found.append({"bbox": [float(x), float(y), float(w), float(h)],
                          "cx": float(cent[i][0]), "cy": float(cent[i][1]),
                          "streaks": int(inside), "area_frac": round(frac, 4),
                          "texture_ratio": round(reg_tex / max(road_tex, 1e-6), 3)})

        return self._persist(found)

    def _age(self):
        self._tracks = [t for t in self._tracks
                        if self._frame_i - t["last_seen"] <= self.cfg.persist_frames]

    def _persist(self, found):
        """A puddle does not appear for one frame. Require persistence, exactly
        as the classical surface proposer does, and for the same reason: a
        single-frame reflection is a passing headlight."""
        c = self.cfg
        out = []
        for f in found:
            hit = None
            for t in self._tracks:
                if np.hypot(f["cx"] - t["cx"], f["cy"] - t["cy"]) <= c.match_dist_px:
                    hit = t
                    break
            if hit is None:
                self._tracks.append({"cx": f["cx"], "cy": f["cy"], "hits": 1,
                                     "emitted": False, "last_seen": self._frame_i})
                continue
            hit["cx"], hit["cy"] = f["cx"], f["cy"]
            hit["hits"] += 1
            hit["last_seen"] = self._frame_i
            if hit["hits"] >= c.persist_frames and not hit["emitted"]:
                hit["emitted"] = True
                f["hits"] = hit["hits"]
                out.append(f)
        self._age()
        return out
