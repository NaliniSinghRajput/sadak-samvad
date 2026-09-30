"""
PROJECT_V3 — capability maturity matrix
=======================================
Every ask in PS 26124, with an honest maturity tag, plus the rows v3 adds.

    LIVE      running in this demo, on this feed, with a number behind it
    DERIVED   computed and measured from the event telemetry, shown as a panel
    DESIGNED  architecture and data path defined, not built

Nothing is tagged LIVE that you cannot point at on screen while it runs. This
file is the single place the claim is made, so it cannot drift between the
dashboard, the deck and the report.

WHAT MOVED IN v3, AND WHY
    * Multi-camera capture             (new) -> LIVE
      Four phones on a hotspot, ingested as MJPEG, analysed by one detector on
      one laptop. The rig was built and driven three times (morning, night,
      rain) before this dashboard existed, so the claim rests on recordings that
      already exist.

    * Real-time streaming ingest       (new) -> LIVE
      v1 and v2 replayed a file against the wall clock and said so on their own
      faces. v3 takes frames off four cameras as they arrive. The recorded mode
      still exists and still goes through the same scheduler.

    * Per-camera calibration           (new) -> LIVE
      Each camera learns its own ego mask, from the live stream, and each one is
      gated separately. Four views, four answers, four chips on screen.

    * Camera health & coverage         (new) -> LIVE
      Which cameras are online, what each is costing in bandwidth, how much of
      the inference budget each one got, and how stale its last analysis is.

    * Geolocated events / heat maps    -> in Project_v2, not in this node
      v2 carries the GPS capture and the four heat-map layers. v3's rig has no
      position feed on the phones yet, so this node does NOT claim position. On
      a bus the AIS-140 feed supplies it, and the event schema already has the
      field. Shown here as DESIGNED rather than borrowed from another demo.
"""

SCHEDULER_NOTE = (
    "One laptop, four cameras, one detector, taken in turn. A camera with no new "
    "frame is skipped rather than re-analysed, so the per-camera rate is the "
    "aggregate divided by the cameras actually producing — both numbers are above."
)

SCHEDULER_NOTE_LONG = (
    SCHEDULER_NOTE + " Equal shares are the default because a priority scheduler "
    "starves the quiet camera, and the quiet camera is exactly where a surprise "
    "appears. A second edge box doubles the budget without changing any of this."
)

CAPABILITIES = [
    # --- the v3 rows ---
    {"group": "Multi-camera node", "name": "Four-camera live ingest", "state": "LIVE",
     "note": "Four phones serving MJPEG over a phone-hosted hotspot, no internet. "
             "Bytes are counted at the door, so the bandwidth figure on this screen "
             "is a measurement of this run."},
    {"group": "Multi-camera node", "name": "Round-robin inference scheduler", "state": "LIVE",
     "note": "One detector shared fairly across the cameras, with the achieved "
             "per-camera rate shown rather than an aspiration."},
    {"group": "Multi-camera node", "name": "Per-camera ego calibration", "state": "LIVE",
     "note": "Each view learns its own ego mask from the live stream and passes or "
             "fails the same two-of-three validity gate independently."},
    {"group": "Multi-camera node", "name": "Camera health & coverage", "state": "LIVE",
     "note": "Online/offline, input frame rate, bandwidth, reconnects, inference "
             "share and time since last analysis, per camera."},
    {"group": "Multi-camera node", "name": "Recorded feed through the same pipeline",
     "state": "LIVE",
     "note": "A 2x2 grid recording is split into four file-backed cameras. The "
             "recorded demo exercises the same scheduler as the live one."},
    {"group": "Multi-camera node", "name": "Cross-camera event fusion", "state": "DERIVED",
     "note": "Events from all cameras share one ledger, one alert stack and one "
             "timeline, each tagged with the camera and mounting position. What is "
             "NOT done is re-identifying the same object across two cameras — that "
             "needs a calibrated geometry between them, which a set of taped-on "
             "phones does not have."},
    {"group": "Multi-camera node", "name": "Continuous recording of all feeds", "state": "LIVE",
     "note": "The separate recorder writes every stream to disk with a per-frame "
             "wall-clock index, offline, for later analysis."},

    # --- perception, on the edge ---
    {"group": "Road condition", "name": "Pothole detection", "state": "LIVE",
     "note": "Trained detector, three-level visual severity grading."},
    {"group": "Road condition", "name": "Damaged road surface", "state": "LIVE",
     "note": "Classical proposer; reported as candidate, never as a classification."},
    {"group": "Road condition", "name": "Waterlogging", "state": "DESIGNED",
     "note": "BUILT IN v3, MEASURED ON OUR OWN ROAD, AND TURNED OFF — the second time this "
             "capability has failed honestly rather than shipped quietly. A detector trained "
             "on a public waterlogging set reaches mAP50 0.539 on that "
             "set's own held-out test split, then fires on dry Bhubaneswar tarmac, because "
             "that set is mostly flood scenes where the water IS the frame, so the model "
             "learns ROAD. Fine-tuning against dry frames from our own drive as hard "
             "negatives did not separate them: at every confidence where the rain drive "
             "still fires, the dry drive does too. It ships disabled with those numbers. "
             "What it needs is labelled Indian standing water, which is the next thing "
             "to collect, not a threshold we could have tuned until it looked right."},
    {"group": "Road condition", "name": "Missing road dividers", "state": "DESIGNED",
     "note": "Absence detection needs a map prior or repeat passes."},
    {"group": "Road condition", "name": "Zebra crossings", "state": "DESIGNED",
     "note": "BUILT IN v3, MEASURED ON OUR OWN ROAD, AND TURNED OFF. A classical "
             "parallel-bar matcher on the road plane. Loose, it called a crossing 34 "
             "times in 90 s of dry Bhubaneswar road, mostly striped kerbs. Tightened "
             "so even spacing is a hard gate, it found 2 candidates in 297 s — tree "
             "shadow on the carriageway, and the boot of a car — and no true crossing, "
             "because there is none in that footage. It ships disabled with those "
             "numbers rather than as a capability we cannot show working. Saying a "
             "crossing is MISSING is a harder problem again: absence needs a prior of "
             "where one ought to be."},
    {"group": "Road condition", "name": "Damaged / missing signboards", "state": "DESIGNED",
     "note": "Detection is tractable; 'missing' again needs the prior."},

    # --- traffic ---
    {"group": "Traffic", "name": "Vehicle detection & classification", "state": "LIVE",
     "note": "Car, truck, bus, motorcycle, bicycle — on every camera."},
    {"group": "Traffic", "name": "Vehicle counting & density", "state": "LIVE",
     "note": "De-duplicated per camera, so it counts distinct encounters, not boxes."},
    {"group": "Traffic", "name": "Side-road and rear traffic", "state": "LIVE",
     "note": "NEW IN v3 and the reason for four cameras: a front camera cannot see a "
             "vehicle overtaking on the right or a queue building behind."},
    {"group": "Traffic", "name": "Traffic bottleneck identification", "state": "DERIVED",
     "note": "A congestion index computed from what the node already counts: vehicles "
             "in view, how fast the scene is moving past, and people in the carriageway. "
             "It is arithmetic over detections and the dashboard labels it 'derived, not "
             "sensed' so nobody reads it as a loop or a speed sensor. In v2 this is a "
             "PLACE, because v2 has GPS."},
    {"group": "Traffic", "name": "True vehicle speed", "state": "DERIVED",
     "note": "km/h comes from the position feed when there is one; without it the node "
             "reports the optical-flow index only, and says which it is showing."},
    {"group": "Traffic", "name": "Origin-destination patterns", "state": "DESIGNED",
     "note": "Needs fleet-scale data over time."},

    # --- safety ---
    {"group": "Safety", "name": "Pedestrian detection", "state": "LIVE",
     "note": "COCO person class, ego-vehicle occupants excluded."},
    {"group": "Safety", "name": "Vulnerable road user in carriageway", "state": "LIVE",
     "note": "Footfall on the road plane; riders suppressed by the lower-body rider "
             "test and a 1.5 s two-wheeler memory."},
    {"group": "Safety", "name": "Animal in carriageway", "state": "LIVE",
     "note": "Same footfall geometry as the VRU alert. Species is indicative; the "
             "alert is about an obstruction."},
    {"group": "Safety", "name": "Traffic signal detection", "state": "LIVE",
     "note": "COCO traffic-light class."},
    {"group": "Safety", "name": "Hit-and-run / rash driving", "state": "DESIGNED",
     "note": "Needs multi-object tracking plus an evidentiary chain of custody. Four "
             "cameras make it more tractable, not solved."},
    {"group": "Safety", "name": "Number-plate detection and redaction", "state": "LIVE",
     "note": "A trained plate detector whose ONLY job is to blur. Held-out detection "
             "mAP50 0.859 on 1000 images. Every plate "
             "found is Gaussian-blurred before the frame is thumbnailed, logged or shown, "
             "so no registration leaves the vehicle. The threshold is deliberately LOW "
             "(0.20): a false positive costs a smudge, a miss leaks a plate. Recall at "
             "that threshold by plate width: 24% at 24-48 px, "
             "97% at 48-96 px, 97% above 96 px. "
             "A bus that photographs every plate it passes is a surveillance network, and "
             "the DPDP Act is the reason this is a blur and not a database."},
    {"group": "Safety", "name": "ANPR — reading the characters", "state": "DESIGNED",
     "note": "We detect plates; we do not claim to read them. Tesseract on ground-truth "
             "crops from this dataset returns text on 60% of "
             "them and something plate-shaped on 49%, "
             "and neither number is character accuracy against a true registration, which "
             "this dataset does not carry. Enforcement-grade ANPR needs that, plus a chain "
             "of custody. Deliberately last: the cost of a wrong plate is borne by a person."},

    # --- platform ---
    {"group": "Platform", "name": "Edge-first bandwidth minimisation", "state": "LIVE",
     "note": "Measured live on this screen, against four real cameras: bytes in at "
             "the door versus telemetry bytes out."},
    {"group": "Platform", "name": "Structured event contract", "state": "LIVE",
     "note": "The JSON in the ticker is the actual wire format, carrying the camera "
             "and its mounting position."},
    {"group": "Platform", "name": "Ego-vehicle rejection", "state": "LIVE",
     "note": "Learned mask plus a measured geometric zone, gated by a majority of "
             "three checks — now per camera, learned live."},
    {"group": "Platform", "name": "Infrastructure deficiency ledger", "state": "LIVE",
     "note": "Ranked for a repair crew, with a thumbnail on every row for operator "
             "verification, and the camera that saw it."},
    {"group": "Platform", "name": "Offline operation", "state": "LIVE",
     "note": "Hotspot only. No internet, no CDN, no tile server, no API key."},
    {"group": "Platform", "name": "Defect map & geolocated events", "state": "LIVE",
     "note": "NEW IN v3, AND CONDITIONAL — it is live exactly when a position feed is. "
             "Two are supported: one phone's own GNSS read live from the camera app, "
             "and a recorded GPS track (CSV or GPX) attached to a recorded drive. With "
             "neither, the map says so and events carry time and camera only. Drawn on "
             "canvas from our own coordinates: no tiles, no API key, no network."},
    {"group": "Platform", "name": "Export to a municipal GIS", "state": "LIVE",
     "note": "The geo-tagged ledger exports as GeoJSON — points plus the driven track — "
             "which opens in QGIS or any web map with no conversion, carrying its own "
             "accuracy note so the caveat cannot be separated from the data."},
    {"group": "Platform", "name": "City-wide fleet heat maps", "state": "DESIGNED",
     "note": "One vehicle is a route profile. The city-wide picture needs many buses "
             "over many days; the gap is coverage, not method."},
]

HONEST_LIMITS = [
    "One laptop runs one detector. Four cameras SHARE it, so each camera is analysed at roughly a quarter of the single-camera rate. Both numbers are on screen; neither is dressed up.",
    "Severity is a VISUAL grade from a trained classifier. It is not a depth measurement, and a camera cannot measure depth.",
    "Position is optional and its source is named on screen: one phone's GNSS, or a recorded GPS track. With neither, events carry time and camera and the map says so rather than inventing a line. On a bus, AIS-140 supplies the feed and the schema already has the field.",
    "A position is attached to an event only from a REAL fix. A stale or interpolated one is drawn for context, ringed in grey, and never becomes the coordinate a crew is sent to. Phone GNSS is street-level (3-8 m, worse between buildings), not lane-level.",
    "The map is drawn from our own coordinates on a plain canvas — there is no basemap, so it shows the route and the defects on it, not the streets around them. That is deliberate: a tile server is a network dependency, and the venue's Wi-Fi is not one worth having.",
    "Events are not fused ACROSS cameras. The same pothole seen by the front and then the left camera is two events, because fusing them needs a calibrated geometry between cameras that a set of taped-on phones does not have.",
    "Accuracy figures quoted are from the held-out test split only (mAP50 0.505). Validation drove early stopping and is no longer neutral.",
    "A vulnerable-road-user alert means a human in the travelled way, deliberately NOT resolved to pedestrian versus cyclist.",
    "Waterlogging is NOT detected. A model trained on a public set scored well on that set and then fired on dry tarmac; hard negatives from our own drive did not separate the two. It ships disabled with its numbers, and what it needs is labelled Indian standing water.",
    "We detect number plates so we can BLUR them. We do not read them, and nothing in this build should be described as ANPR. Anything STORED or SENT — the thumbnail on a ledger row, the frame behind an alert — is blurred from the frame the plate was detected in. The live preview on this screen is blurred too, but best-effort: the detector reaches each camera about once a second, so the preview uses the last few plate positions grown to cover the motion between them, and a plate moving fast across the frame can show for a fraction of a second before it is covered.",
    "Zebra crossings are NOT detected. The matcher was built, measured on our own drive, and ships disabled: loose it called 34 crossings in 90 s of dry road, tightened it found two shadows and a car boot in 297 s and no real crossing. We would rather show the measurement than the feature.",
    "Wi-Fi is the weak link, not the detector. A phone that drops off the hotspot stops contributing, and the dashboard shows that as a camera going amber rather than as a gap nobody notices.",
    "The rig is four phones standing in for a bus's existing NVR, and a laptop standing in for the onboard edge module. It is a test rig and is described as one.",
]

TEST_METRICS = {
    "split": "held-out test", "n_images": 156,
    "mAP50": 0.505, "mAP50_95": 0.310, "precision": 0.566, "recall": 0.507,
    "per_class": {"risk-pothole (severe)": 0.648,
                  "medium-pothole (moderate)": 0.485,
                  "safe-pothole (minor)": 0.383},
    "note": "Validation was used for early stopping and is therefore not neutral. "
            "The severest class is the best detected, which is the right way round "
            "for repair prioritisation.",
}

WATERLOGGING_METRICS = {
    "split": "held-out test", "n_images": 114,
    "mAP50": 0.5388, "mAP50_95": 0.3632,
    "precision": 0.5949, "recall": 0.6529,
    "enabled": False,
    "note": "A GOOD SCORE ON SOMEONE ELSE'S TEST SPLIT. Trained on Waterlogging v2 (Roboflow Universe, CC BY 4.0). On our own dry morning drive the same model produced 69 waterlogging boxes through the node's road-plane and ego gates, and after fine-tuning on 337 dry frames from that drive there was still no confidence at which the dry road is quiet and our monsoon footage is not. The detector is OFF in this build. This number is here to show what a held-out score is worth when the held-out set is not your road.",
}

PLATE_METRICS = {
    "split": "held-out val", "n_images": 1000,
    "mAP50": 0.8594, "mAP50_95": 0.4362,
    "precision": 0.8982, "recall": 0.8015,
    "ocr_text_rate": 0.5986, "ocr_plausible_rate": 0.491,
    "note": ("Detection only. The OCR rates say how often ANY text comes out of a "
             "ground-truth crop and how often it looks like a plate. They are NOT "
             "character accuracy and must not be quoted as ANPR accuracy."),
}

ATTRIBUTION = ("Road-damage detector fine-tuned on Detection-Potholes-Classes v7, "
               "Roboflow Universe, workspace potholesdetection-aq76f. Licensed CC BY 4.0. "
               "Waterlogging detector trained on Waterlogging v2, Roboflow Universe, "
               "CC BY 4.0. Number-plate detector trained on License Plate Recognition v13, "
               "Roboflow Universe, CC BY 4.0. "
               "Multi-camera captures: The Road Runners rig, Bhubaneswar, 18–19 Sep 2026.")
