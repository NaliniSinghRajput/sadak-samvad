"""
PROJECT_V2 — configuration
==========================
Every tunable number in one place. Nothing magic is buried in the code.

Team The RoadRunners · SIH 2026 · PS 26124 (Bharat Electronics Limited)
"""
from dataclasses import dataclass, field, asdict


@dataclass
class StreamConfig:
    """How we consume video.

    The edge node must behave identically whether frames arrive from a live
    RTSP camera or from a file. `target_fps` is the rate the PIPELINE runs at,
    not the rate the file was recorded at — we throttle down because a
    Raspberry Pi 5 cannot process 50 fps and does not need to. At 30 km/h,
    5 fps still gives a new frame every 1.7 m of road.
    """
    target_fps: float = 5.0          # frames per second actually processed
    resize_width: int = 960          # inference width; height follows aspect
    realtime: bool = False           # True = sleep to simulate a live camera
    # If the node has processed nothing for this long, process the next frame
    # regardless of the clock. Dropping every frame is arithmetically correct
    # and operationally useless: a frozen feed and a crashed node look the same
    # to the person watching, so we would rather show a late frame and report
    # the real rate underneath it. Measured cause: on 1080p60 footage the decode
    # alone runs slower than real time, so a purely clock-driven rule dropped
    # 180 of 180 sampled frames and displayed nothing at all.
    max_stall_s: float = 2.0


@dataclass
class EgoMaskConfig:
    """Masking out the vehicle we are riding in.

    A bike's handlebars, or a bus's bonnet and wiper arms, occupy a fixed
    region of every frame. Those pixels never change, so a per-pixel temporal
    variance over a sample of frames isolates them automatically — no
    hand-drawn polygon, and it re-learns itself on a different vehicle.
    """
    sample_frames: int = 60          # frames sampled across the whole video (opening bid)
    # -- ADAPTIVE SAMPLING (v2, added 2026-08-28 after a measured failure) ----
    # v1 sampled a FIXED 60 frames -> 30 pairs, then discarded every pair where
    # the vehicle was not moving (see min_scene_flow). On the Kanjhawla clip 29
    # of 30 pairs survived and the learner was comfortable. On the Naini night
    # runs, where the car crawls through stalled traffic at 6.5 km/h, only 12-14
    # survived -- too few for a stable temporal quantile -- and the learner
    # returned NO MASK on naini-3, a video in which the bonnet fills the bottom
    # fifth of every single frame. A false negative, and not a subtle one.
    #
    # Measured, at 960x540, with the same gate thresholds:
    #                        usable pairs   mask %   split-half IoU   appearance
    #   naini-2  @60 frames        14        18.1        0.443          1.188
    #   naini-2  @120 frames       30        17.2        0.802          1.249
    #   naini-3  @60 frames        12         0.0         --             --   MISSED
    #   naini-3  @120 frames       50        21.0        0.656          1.189
    #   Kanjhawla@60 frames        29        19.2        0.505          1.079
    #
    # The fix is not a new threshold. It is that the sample budget must be set by
    # how much of the feed is MOVING, not by a constant. So we keep sampling until
    # `min_usable_pairs` survive, and stop at `max_sample_frames` so a video of a
    # parked bus terminates instead of scanning forever.
    min_usable_pairs: int = 24       # keep sampling until this many pairs survive
    max_sample_frames: int = 400     # hard ceiling, so a stationary feed still ends
    # Dense optical flow costs O(pixels), and the ego mask is a coarse object
    # that gets dilated afterwards anyway, so computing the flow at half
    # resolution and upsampling the result costs nothing we can see and makes
    # calibration roughly four times faster. That matters: this runs before the
    # first frame reaches the operator, and a dashboard that thinks for two
    # minutes on startup is a dashboard nobody waits for.
    flow_scale: float = 0.5
    var_percentile: float = 28.0     # below this percentile of FLOW = moves with camera
    search_from_row: float = 0.45    # only look for ego body below this height fraction
    dilate_px: int = 9               # grow the mask to catch soft edges
    min_blob_frac: float = 0.004     # ignore static blobs smaller than this
    min_scene_flow: float = 0.60     # px/frame; below this the vehicle is stopped -- skip the pair
    time_quantile: float = 0.25      # per-pixel temporal quantile of flow (see learn_ego_mask)
    # -- validity gate: does this feed contain an ego vehicle AT ALL? ---------
    # Some cameras see no part of their own vehicle (a helmet or chest mount, a
    # windscreen cam that clears the bonnet). Without a gate the learner invents
    # one out of dark road, because it is built to return the low tail of flow
    # whatever that tail contains. v1 required TWO tests to pass, both of them.
    #
    # v2 CHANGES THE COMBINING RULE, NOT THE TESTS. Re-measured 2026-08-28 across
    # five videos with the adaptive sampler, at 960x540, flow_scale 0.5:
    #
    #   video                              truth      pairs  mask%    IoU  appear
    #   1_Kanjhawla_Road_Delhi.mkv         EGO           29   17.5  0.682   1.141
    #   naini-2_gps.MP4                    EGO           24   16.3  0.777   1.149
    #   naini-3_gps.MP4                    EGO           24   16.5  0.445   1.115
    #   n2_clip_260_305.mp4 (45 s)         EGO           24   17.3  0.658   0.713
    #   2_Varanasi_Coaching_road.MOV       NO EGO        28    1.9  0.173   0.653
    #
    # Read the last two rows together, because they are the whole argument. The
    # APPEARANCE test compares the region's temporal variation against the ROAD's.
    # On the 45-second clip the car is crawling through stalled traffic on a wet,
    # mirrored carriageway full of moving headlights, so the road's variation is
    # abnormally high and the ratio collapses to 0.713 — below the threshold, on
    # footage where the bonnet fills a fifth of every frame. The test did not
    # become wrong; its denominator moved. Meanwhile the NEGATIVE sits at 0.653,
    # so appearance alone cannot separate those two cases at all.
    #
    # What DOES separate them, cleanly and on two independent axes at once:
    #   * split-half agreement   0.173 (negative) vs 0.445 and up (positives)
    #   * mask fraction          1.9%  (negative) vs 16.3% and up (positives)
    #
    # So the rule is now TWO OF THREE. Every positive passes at least two tests;
    # the negative fails all three. Nothing was deleted and no threshold was bent
    # to fit a case — the weakest test simply lost its power of veto, which is
    # what the evidence supports. If a sixth video is misjudged, add its row to
    # the table above and re-derive; do not quietly move a number.
    min_split_half_iou: float = 0.30      # negative 0.173, worst positive 0.445
    min_mask_frac: float = 0.06           # negative 0.019, worst positive 0.163
    min_appearance_ratio: float = 0.80    # negative 0.653, worst positive 0.713 -- weakest
    min_tests_passed: int = 2             # of the three above


@dataclass
class RigidityConfig:
    """Per-frame test for 'is this our own vehicle?' — see vision.RigidityTest.

    The learned ego mask is one static shape for the whole video. This is the
    same physical idea asked per box, per frame, so it survives a camera that
    tilts and a rider whose arms move.
    """
    enabled: bool = False            # OFF: measured, did not work on this footage -- see vision.RigidityTest
    flow_scale: float = 0.25         # dense flow at quarter resolution — the ratio is all we need
    rigid_ratio: float = 0.30        # box flow below 30% of scene flow = attached to us
    min_scene_flow: float = 0.45     # px/frame at reduced scale; below this we are stopped, so abstain
    apply_below_row: float = 0.55    # only judge the near field (see the trap in the docstring)


@dataclass
class RoadROIConfig:
    """The drivable surface, as a trapezoid below the horizon.

    Anything above the horizon cannot be road. Anything inside the ego mask is
    our own vehicle. What remains is where road defects can physically be.
    """
    horizon_frac: float = 0.34       # image height fraction where road starts
    top_width_frac: float = 0.30     # trapezoid width at the horizon
    bottom_width_frac: float = 1.00  # trapezoid width at the bottom
    bottom_frac: float = 1.00        # how far down the trapezoid extends


@dataclass
class EgoZoneConfig:
    """Where OUR OWN vehicle appears in the frame — the rule that actually works.

    Three things were tried, in this order, and the order is the lesson:

      1. A learned static mask (`learn_ego_mask`). Necessary, not sufficient.
         It covers what is ALWAYS us; it misses arms that swing and a camera
         that tilts down onto its own fuel tank.

      2. A per-frame optical-flow "is this rigidly attached to the camera?"
         test (`vision.RigidityTest`). Beautiful idea, correct physics, and it
         FAILED on measurement — see that docstring. Kept in the tree, disabled,
         because knowing why it failed is worth more than deleting it.

      3. This: a geometric zone, measured against 3,226 real detections on a
         100 s clip. It removed 264 of 806 `person` boxes and 365 of 784
         `motorcycle` boxes — the rider and his own bike — at a cost of 9 cars
         out of 1,156. Those are the numbers, not an estimate.

    The three sub-rules, each with a physical reason:
      * `max_mask_overlap`  — mostly inside the learned mask. That is us.
      * `bottom_tol` + `bottom_halo` — a box that runs off the BOTTOM edge of
        the frame in the near field, next to the learned mask, is attached to
        us. Nothing on the road can leave the frame downwards without first
        passing under the vehicle.
      * `side_frac` + `near_row` for rider classes — in the near-field outer
        periphery, a `person` or `motorcycle` is the operator's hands and our
        own bodywork. A real road user that close and that far to the side is
        already half out of frame and was counted seconds earlier when centred.
        Deliberately restricted to `rider_classes`: cars and trucks there are
        real, and the measurement above confirms it.

    ON A BUS this matters much less — a bonnet really is static, so rule 1 does
    most of the work. This footage is a motorcycle POV, which is a harder case
    than the deployment target, not an easier one. Say it that way.
    """
    enabled: bool = True
    halo_dilate_px: int = 70         # the neighbourhood the rider's limbs swing through
    max_mask_overlap: float = 0.35   # fraction inside the learned static mask
    max_halo_overlap: float = 0.40   # fraction inside the dilated neighbourhood
    near_row: float = 0.60           # only judge below this height fraction
    bottom_tol_px: int = 8           # "runs off the bottom edge" tolerance
    bottom_halo: float = 0.10
    side_frac: float = 0.18          # outer 18% of columns, left and right
    rider_classes: tuple = (0, 1, 3)  # COCO person, bicycle, motorcycle
    vru_min_height_frac: float = 0.045   # a figure smaller than this is too far to be urgent
    rider_band_frac: float = 0.45         # lower body: the part that sits on the machine
    rider_overlap: float = 0.22           # of that band, covered by a two-wheeler
    rider_memory_s: float = 1.5           # a bicycle does not vanish between frames


@dataclass
class DetectorConfig:
    """COCO-pretrained object detection.

    yolo11s is the accuracy/speed compromise for a Pi-class device. The COCO
    classes we care about are listed explicitly — everything else is discarded
    at source so we never pay to track a potted plant.
    """
    weights: str = "yolo11s.pt"
    conf: float = 0.35
    iou: float = 0.50
    imgsz: int = 960
    # COCO ids -> our semantic classes
    vehicle_classes: dict = field(default_factory=lambda: {
        1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck",
    })
    person_class: int = 0
    infra_classes: dict = field(default_factory=lambda: {
        9: "traffic_light", 11: "stop_sign",
    })
    # NEW IN v2. Livestock and strays standing in the carriageway are one of the
    # most common causes of night-time collisions on Indian roads, and they are
    # already in COCO -- we were discarding them at source. The Naini night runs
    # contain cattle in the travelled way, which is what made this worth wiring
    # up rather than leaving on the "designed" list.
    animal_classes: dict = field(default_factory=lambda: {
        16: "dog", 17: "horse", 18: "sheep", 19: "cow",
    })


@dataclass
class PotholeConfig:
    """Geometric sanity gates for the TRAINED road-damage detector.

    Added 2026-08-26 after eyeballing the first full-video run. The detector
    itself is fine; what it lacked was a sense of where a pothole can
    physically be and how big one can physically look.

    Every gross false positive in that run shared one of three signatures:
      1. an enormous box  — the whole frame, or half of it. A pothole seen from
         a camera 1.2-3 m up simply cannot subtend that solid angle. `max_area_frac`
         kills these outright.
      2. a box lying mostly OFF the drivable surface — on the verge, on rubble,
         on the rider's own arm. The old test asked only whether the box's base
         POINT fell inside the road ROI, which a huge box passes trivially. We
         now test the bottom `base_band_frac` of the box — the strip where the
         defect meets the road — and require `min_plane_overlap` of it to be
         road plane. Testing the whole box was tried and was too harsh: a real
         pothole box legitimately extends upward past the trapezoid edge.
      3. a box on our own vehicle. Same overlap logic as the COCO path.

    None of this is a hack: each gate encodes a physical fact about the scene,
    which is why they generalise to bus footage rather than fitting this clip.
    """
    conf: float = 0.35
    iou: float = 0.50
    imgsz: int = 640
    max_area_frac: float = 0.10      # a box bigger than 10% of frame is not a pothole
    base_band_frac: float = 0.30     # test the bottom 30% of the box -- where it meets the road
    min_plane_overlap: float = 0.50  # that band must be at least half road plane
    max_ego_overlap: float = 0.35    # more than this on our own vehicle = discard
    max_aspect: float = 6.0          # a 10:1 sliver is a texture artefact, not a hole


@dataclass
class SurfaceConfig:
    """Road-surface damage PROPOSER.

    IMPORTANT, AND STATED PLAINLY: this is not a trained pothole classifier.
    COCO contains no pothole class and no general-purpose model knows one.
    This module proposes *candidate* damage regions from surface appearance and
    then filters them on physics — size plausibility at the estimated range,
    and persistence across frames while the apparent size grows the way a
    genuine road-plane object must as the vehicle closes on it. A tree shadow
    fails the growth test; a hole does not.

    It is a bridge until a road-damage-trained detector is dropped into
    `detectors/pothole.py`. Precision is deliberately favoured over recall.
    """
    enabled: bool = True
    blur_ksize: int = 7
    block_size: int = 61             # adaptive threshold neighbourhood (odd)
    c_offset: int = 9                # how much darker than local mean counts
    open_ksize: int = 5
    close_ksize: int = 11
    min_area_px: int = 900
    max_area_frac: float = 0.06      # a "pothole" filling 6% of frame is not one
    min_solidity: float = 0.55       # holes are blobby, cracks are not
    max_aspect: float = 4.0
    min_row_frac: float = 0.55       # ignore the far distance: too few pixels
    persist_frames: int = 3          # must survive this many consecutive frames
    match_dist_px: int = 90          # candidate association radius between frames


@dataclass
class MotionConfig:
    """Ego-motion from sparse optical flow.

    This footage carries no GPS or telemetry track, so speed is not given to
    us. Median optical-flow magnitude in the road region is monotonic in speed
    and is enough to (a) normalise event severity, (b) detect stops and hard
    braking, and (c) predict how fast a road-plane object should grow.

    It is a RELATIVE index, not km/h. Converting it to true speed needs either
    a calibrated homography or the vehicle's own speed feed. Do not report it
    as a velocity.
    """
    max_corners: int = 220
    quality: float = 0.02
    min_distance: int = 12
    win_size: int = 21
    smooth_alpha: float = 0.25       # EMA on the speed index


@dataclass
class EventConfig:
    """What leaves the edge node.

    The entire architectural claim of this project is that raw video never goes
    to the cloud — only compact structured events do. These settings define
    that contract and let us MEASURE the compression, rather than assert it.
    """
    dedup_window_s: float = 4.0      # same class+place within this window = one event
    dedup_dist_px: int = 140
    min_track_hits: int = 3          # a track must be seen this often to be reported
    confirm_labels: tuple = ("pothole",)   # labels held until min_track_hits sightings
    emit_thumbnails: bool = True     # small JPEG crop for operator verification
    thumb_max_px: int = 160
    thumb_quality: int = 60


@dataclass
class WaterConfig:
    """Standing water / waterlogging PROPOSER — BUILT, MEASURED, AND TURNED OFF.

    PS 26124 asks for waterlogging. v1 tagged it DESIGNED because there was no
    footage containing any. The Naini runs of 28-Aug-2026 are a monsoon night on
    a flooded carriageway, so it was finally worth building — and it was built,
    on explicit physics, and it does not work on this footage. The code stays,
    disabled, with the measurement that killed it. This is the same treatment
    RigidityTest got in v1 and for the same reason: knowing why an idea failed is
    worth more than deleting the evidence that it did.

    THE PHYSICS IT ASSUMED
        Standing water at night is a mirror: street lamps reappear in it, and
        surface ripples smear each reflection into a VERTICALLY ELONGATED bright
        streak. Dry asphalt under the same lamp scatters diffusely and gives a
        broad soft pool with no streaks. So: several tall thin specular blobs,
        clustered, low in the frame, on the road plane, with unusually smooth
        surroundings.

    WHAT THE MEASUREMENT SAID  (n2_clip_260_305.mp4, 640x360, road plane only)
        t=  2s  17 bright blobs,  6 of usable size,  0 vertically elongated
        t=  8s  17 blobs,  9 sized,  2 tall
        t= 14s  27 blobs, 13 sized,  1 tall
        t= 20s  36 blobs, 11 sized,  1 tall
        t= 26s  23 blobs,  9 sized,  2 tall
        t= 32s  35 blobs, 12 sized,  0 tall
        t= 38s  12 blobs,  3 sized,  0 tall
        t= 44s  25 blobs, 10 sized,  0 tall
        Across the whole clip the proposer emitted ZERO candidates. Most bright
        blobs are WIDER than tall (aspect 0.2-1.3): the vehicle spends this
        stretch crawling in stalled traffic, the water is nearly still, and a
        still mirror does not smear anything. The ripple assumption is a moving-
        water assumption.

    THE SECOND, DEEPER PROBLEM — AND IT IS THE INTERESTING ONE
        A rewrite was tried that dropped the shape assumption and tested each
        tile of the road plane for the mirror signature directly: specular
        highlights, a darker-than-road background, and lower-than-road texture.
        It found 0-3 qualifying tiles out of 390 per frame.

        The reason is worth stating because it is not a tuning failure. Every one
        of those tests is RELATIVE TO THE ROAD — and on a street that is entirely
        waterlogged there is no dry road left to be relative to. The measured
        road median sat at 84-113 of 255: the whole carriageway is bright and
        mirrored, so "darker and smoother than the road" describes nothing,
        because the road IS the water.

        That is the same shape of mistake as the ego-gate appearance test, which
        also compares a region against a road whose statistics moved. Twice now,
        on this footage, a ratio against a moving reference has been the thing
        that broke. Worth remembering before writing a third one.

    WHAT WOULD ACTUALLY BE NEEDED
        A cue that does not need a dry reference. The strongest candidate is
        REFLECTION SYMMETRY: water mirrors the scene above the waterline, so a
        vertically flipped patch correlates with the patch above it. That needs a
        waterline hypothesis and proper evaluation against a dry control video,
        which is real work and is not something to ship untested the week it is
        thought of. Until then this capability stays DESIGNED in the matrix and
        the dashboard says so.

    TO RE-ENABLE FOR EXPERIMENTS: set enabled=True. Nothing downstream breaks —
    events are labelled `waterlogging_candidate` and go to the operator, never
    to a work order.
    """
    enabled: bool = False            # measured, does not work on this footage; see above
    bright_percentile: float = 97.0   # top of the road-plane intensity histogram
    min_streak_px: int = 40           # a reflection smaller than this is noise
    max_streak_frac: float = 0.02     # a huge bright blob is a headlight, not a reflection
    min_streak_aspect: float = 1.6    # height/width; ripples smear reflections VERTICALLY
    min_streaks: int = 3              # one streak is a lamp. Several together is a surface.
    cluster_dilate_px: int = 45       # how close streaks must be to count as one sheet
    min_region_frac: float = 0.004    # ignore specks
    max_region_frac: float = 0.35     # a "puddle" covering a third of the frame is not one
    smoothness_ratio: float = 1.05    # region texture must be BELOW road median x this
    min_row_frac: float = 0.50        # only the near/mid field: far reflections are unreliable
    persist_frames: int = 3           # must survive this many consecutive frames
    match_dist_px: int = 120          # association radius between frames


@dataclass
class GeoConfig:
    """Turning events into places.

    Everything here exists because v1 could not answer "where?". The numbers are
    chosen against the measured error of the capture rig, not for neatness:
    with 3-5 m of GPS error and 1-2 s of anchor-matched sync, a 25 m segment is
    about the smallest bin that is not mostly noise, and 15 m is the smallest
    honest radius for calling two passes' detections "the same defect".
    """
    enabled: bool = True
    segment_len_m: float = 25.0        # the unit a repair crew actually works on
    repeat_pass_radius_m: float = 15.0 # >= position error, by construction
    max_offset_m: float = 60.0         # a fix further than this off-track is not on this route
    require_real_fix: bool = True      # never geo-tag an event from an interpolated sample
    route_thin_every: int = 5          # polyline decimation for drawing only
    slow_kmh: float = 5.0              # below this, for congestion scoring
    sidecar_dirs: tuple = ()           # extra folders to search for a telemetry CSV
    # How often the per-segment heat table is pushed to the dashboard. A few
    # dozen segments of numbers is about a fiftieth of one JPEG frame, so this
    # can be frequent; every 4th processed frame makes the map fill in visibly
    # as the vehicle drives rather than appearing all at once at the end.
    heat_every_n_frames: int = 4


@dataclass
class Config:
    stream: StreamConfig = field(default_factory=StreamConfig)
    ego: EgoMaskConfig = field(default_factory=EgoMaskConfig)
    roi: RoadROIConfig = field(default_factory=RoadROIConfig)
    rigid: RigidityConfig = field(default_factory=RigidityConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    egozone: EgoZoneConfig = field(default_factory=EgoZoneConfig)
    pothole: PotholeConfig = field(default_factory=PotholeConfig)
    surface: SurfaceConfig = field(default_factory=SurfaceConfig)
    motion: MotionConfig = field(default_factory=MotionConfig)
    events: EventConfig = field(default_factory=EventConfig)
    water: WaterConfig = field(default_factory=WaterConfig)
    geo: GeoConfig = field(default_factory=GeoConfig)

    # Identity of this edge node, as it would be on a real bus
    vehicle_id: str = "TRR-BUS-02"
    route_id: str = "NAINI-VNS-01"

    def to_dict(self):
        return asdict(self)
