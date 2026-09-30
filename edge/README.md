# Project_v3 — four-camera edge node

Team The Road Runners · SIH 2026 · PS 26124 (Bharat Electronics Limited)

One screen that runs **either four live phone cameras or a recorded drive**, through one
detector, on one laptop. `RUNBOOK_v3.md` says how to start it. This file says how it is built
and why.

---

## The claim, and the constraint behind it

v1 and v2 analysed one feed. A bus has several cameras and one edge box, so the interesting
question is not "can it detect a pothole" — that was answered and measured — but **what happens
when four cameras want the same detector.**

Measured on this laptop, the pipeline runs at roughly 3.5 frames per second on one stream.
Four streams do not make the laptop faster. So the node round-robins a single inference budget
across the cameras and **reports the per-camera rate it actually achieved**, on screen, beside
the aggregate. That number is a design input for a real deployment — it says how many cameras
one edge box can carry — and it is shown rather than hidden.

## One pipeline, two doors

```
  four phones (MJPEG over the hotspot) ─┐
                                        ├─► scheduler ─► detector ─► events ─► dashboard
  a recorded 2x2 drive, split in four ──┘
```

A recorded 2×2 grid recording is split back into four **file-backed cameras**, sampled at the
same rate a phone delivers. The recorded demo therefore exercises the same scheduler, the same
per-camera calibration and the same event path as the live one. It is not a video player with
a dashboard around it.

## Layout

```
dashboard/
  run_dashboard_v3.bat          start here
  run_rehearsal_cameras.bat     live mode without the phones (see the runbook)
  server/
    app.py            FastAPI + WebSocket on 127.0.0.1:8082
    cameras.py        MjpegCamera · DeviceCamera · FileCamera — one interface
    multicam.py       the round-robin scheduler, event log, session recording
    perception.py     shared models + one CameraAnalyser per camera
    ego_live.py       learning the ego mask from a LIVE stream (v2's gate, new source)
    capabilities.py   the capability maturity matrix and the honest limits
    report_v3.py      the self-contained one-page run report
  _p2src/             COPIED from Project_v2\src — config, vision, pothole, geo, telemetry
  web/                index.html · app.css · app.js · assets  (no framework, no CDN)
  tools/fake_cameras.py
  cameras.json        saved camera addresses
  sessions/           one JSONL per run, written as it happens
```

**Weights are read in place** from `Project_v1\models\` (`yolo11s.pt`, `pothole_best.pt`). They
are never copied, because two copies are two things that can silently drift apart, and the
whole point is that this runs the same detector that was measured.

`_p2src/` is a **copy** of Project_v2's `src/`, in the same spirit as `TheRoadRunners_v1/_p2src`:
v3 can be developed without touching a working demo. If the perception core changes in v2,
copy it forward deliberately.

## Where the defects are — the map

A defect ledger a municipality cannot navigate to is a list, not a work order. v3 puts every
geo-tagged defect on a map drawn on a plain canvas from our own coordinates: **no tiles, no API
key, no network**. Position is optional and its source is always named on screen:

| source | what it is |
|---|---|
| `phone` | one phone's own GNSS, polled live from IP Webcam's `/sensors.json` endpoint (Location data must be ON in the app) |
| `sidecar` | a recorded GPS track attached to a recorded drive — the Project_v2 CSV, or a GPX |
| `none` | no position feed. Events carry time and camera, and the map says so rather than drawing a line it cannot justify |

The rule carried from v2 does not bend: **an event is geo-tagged only from a REAL fix.** A stale
or interpolated position is drawn ringed in grey for context and never becomes the coordinate a
crew is sent to. Defects within about fifteen metres on screen merge into one pin carrying the
count, so a busy stretch reads as a busy stretch. `Expand` opens the map full-screen, and the
ledger exports as **GeoJSON** — points plus the driven track — which opens in QGIS with no
conversion.

## What v3 adds to the perception core

Everything about what a detection *means* is v2's, carried over with its reasoning: ego-vehicle
rejection, the rider test with its two-wheeler memory, the road-plane footfall test for humans
and animals, the trained road-damage detector with its placement gates. What is new:

1. **Per-camera state.** Each camera has its own ego mask, road plane, motion estimate and
   de-duplication memory. Four views, four answers.
2. **Ego calibration from a live stream.** v1 and v2 seeked around a file before playing it. A
   camera cannot be rewound, so pairs are collected as they arrive, by a pump that runs for the
   first minute of a run and then stops. The **gate is v2's code, imported** — only the source
   of the frames is different.
3. **A fourth check on the ego mask, added because the other three were fooled.** A tile cut out
   of a grid recording carries black bars. A black bar has no motion and perfect split-half
   agreement, so it scored 0.99 on the strongest test and was accepted as a vehicle body. It is
   not: it is a region that never changes. A candidate whose pixels are effectively constant is
   now vetoed outright, whatever the majority says. Black bars are also trimmed at the source.
4. **Bytes counted at the door.** Every camera counts what it received, so the bandwidth figure
   on screen is a measurement of that run rather than an estimate.

## What it deliberately does not do

The full list is in the dashboard under *What this does & does not do*. The three that matter:

- **No position.** These phones publish no GPS. Project_v2 carries the geolocated version on a
  real GPS capture; here the field exists in the schema and is empty, and the dashboard says so.
- **No fusion across cameras.** The same pothole seen by two cameras is two events. Fusing them
  needs a calibrated geometry between cameras that taped-on phones do not have.
- **Accuracy is the held-out test split only** — mAP50 0.505. Validation drove early stopping
  and is not neutral.
