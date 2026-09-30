# Sadak Samvad · सड़क संवाद

**Citizen voice + bus-camera evidence → Gemini → a ranked, state-ready public-works list.**
Build with AI: Code for Communities (2nd ed.) · **Track 1 — AI for Digital Public Infrastructure & Governance** · Team **The Road Runners**

🔗 **Live prototype:** _LIVE_URL_  ·  🎬 **Demo video:** _VIDEO_URL_

---

## The problem (Track 1)
Roads, drains and streetlights are the development requests Indians raise most — through municipal apps, CPGRAMS, WhatsApp groups, phone calls and ward offices, in 22 scheduled languages. These requests live in fragmented systems, are rarely verified, and are never compared with where money is actually being sanctioned. The result is misaligned public spending and infrastructure gaps no one can see.

## What Sadak Samvad does — end to end
| # | Step | What happens | Google AI |
|---|---|---|---|
| 1 | **Citizen intake** | Report by **voice, text, photo**, via web, **WhatsApp, IVR, SMS or Common Service Centre**, in any Indian language | **Gemini multimodal** transcribes audio, reads the photo, detects language, translates, classifies (10 categories), grades severity 1–5, flags vulnerable users, routes to the department, and **replies in the citizen's own language** (controlled JSON output) |
| 2 | **Automatic evidence** | A camera node on city buses detects potholes and road hazards on-device, blurs number plates, and sends events only (4,308 : 1 data reduction) | YOLO11 on the edge; **Gemini vision second opinion** confirms/overrules each crop and writes the work-order line |
| 3 | **Fusion & priority** | Requests and camera events are gridded into hotspots; score = 35 % demand · 25 % severity · 20 % camera-confirmed · 10 % vulnerable users · 10 % district population (Census 2011) | transparent scoring (every component shown) |
| 4 | **Investment alignment** | Each hotspot is checked against the sanctioned-works register (PWD / Smart Cities / AMRUT / PMGSY) → **unfunded gaps** and **% of planned spend that lands where citizens asked** | — |
| 5 | **Decision support** | One-page **policy brief** for a Municipal Commissioner, State PWD or Ministry, in any of 23 languages; **"Ask the data"** grounded Q&A | **Gemini** |
| 6 | **Live store** | Every real report is written to **Firebase Realtime Database** and appears on every official's map instantly | Firebase |

## Architecture
```
 Citizen (voice/text/photo; web · WhatsApp · IVR · SMS · CSC)        City bus edge node (edge/)
        │                                                              4 cameras → YOLO11 → events
        ▼                                                              plates blurred on device
  Gemini multimodal intake  ── JSON ticket ──►  Firebase RTDB  ◄── GeoJSON events ── tools/export_evidence.py
        │                                            │
        └──────────── reply in citizen's language    ▼
                                           Priority engine (hotspots × demand × severity
                                           × camera × vulnerability × population)
                                                     │  × sanctioned-works register → gaps
                                                     ▼
                                   Governance dashboard (Firebase Hosting)
                                   Gemini vision verification · Gemini policy brief · Ask-the-data
```

## Repository
```
web/          the deployed prototype (static; Firebase Hosting)
  app.js        intake, hotspot/priority engine, alignment, brief, Q&A
  gemini.js     Gemini REST client (model fallback, JSON schema output)
  data/         evidence.json (real edge events) · citizen_seed.json (synthetic sample requests + works; Census 2011 populations)
edge/         the bus-camera edge node (FastAPI + YOLO11, 4 cameras, on-device plate blurring)
tools/        export_evidence.py (edge sessions → web evidence layer) · make_seed.py (sample data generator)
firebase.json · database.rules.json   hosting + validated, append-only public report store
```

## Data — stated plainly
* **Real:** bus-camera detections from our own drives — 34 GNSS-tagged potholes, Varanasi (29 Aug 2026); 3,944 detections from a 4-camera, 13.5 km drive in Bhubaneswar (19 Sep 2026; GPS was off, positions interpolated along the driven route by time and labelled so). District populations: Census of India 2011.
* **Synthetic (labelled "sample" in the UI and data):** 130 citizen requests in 13 languages across 14 districts / 13 states and 61 sanctioned works — stand-ins for CPGRAMS / state grievance feeds and state works registers until those APIs are connected. Every report submitted on the live site is real and stored.

## Run it
```bash
# 1. local
cp web/config.example.js web/config.js   # add a Gemini API key (AI Studio) and, optionally, your RTDB URL
python -m http.server 8080 -d web         # open http://localhost:8080
# 2. deploy (Firebase Hosting + Realtime Database rules)
npx firebase-tools login
npx firebase-tools deploy --project <your-project-id>
# 3. edge node (optional; laptop CPU is enough)
cd edge && pip install -r requirements.txt && python -m uvicorn server.app:app --port 8082
```
Security: the browser key is restricted to the site's domain (HTTP referrer) and to the Generative Language API only. Production path: move intake behind a Cloud Run endpoint (key server-side) that also receives the **WhatsApp Business Cloud API** webhook.

## Why it scales across India — and BRICS
* **Configuration, not code:** districts, languages, departments and schemes are data. A new state = its district list + works register.
* **Reach:** voice and 22 languages + CSC/IVR channels include citizens who never use a portal.
* **Cost:** static hosting + serverless + Gemini Flash; bus nodes reuse existing fleets (one laptop-class box serves 4 cameras).
* **Digital Public Good:** open source, open schemas (GeoJSON events, JSON tickets), privacy by design (plates blurred on device, no video leaves the bus). Any BRICS city can fork it with its own languages.
* **Pilot in weeks:** 1 city bus depot + 1 municipal corporation + the state works register.

**Scale path on Google Cloud:** Cloud Run intake API · WhatsApp Business webhook · BigQuery for national analytics · Vertex AI for hotspot forecasting (monsoon waterlogging) · Google Maps Platform · Cloud Speech-to-Text for IVR.

## What is new for this hackathon (Rule 2)
The edge camera node (`edge/`) was first built by the team for Smart India Hackathon 2026 (PS 26124). **Built new for this challenge:** the Gemini multimodal multilingual intake, Gemini vision verification, the fusion/priority engine, the investment-alignment analysis, the Gemini policy brief and Q&A, the Firebase live store and the whole governance web app.

## Credits & licences (Rule 3)
Gemini API (Google) · Firebase (Google) · Leaflet 1.9.4 (BSD-2-Clause) · marked 12 (MIT) · OpenStreetMap tiles (ODbL) · Edge node: Ultralytics YOLO11 (AGPL-3.0), OpenCV (Apache-2.0), FastAPI (MIT), NumPy (BSD); pothole model trained on Roboflow Universe "Detection-Potholes-Classes" (CC BY 4.0). Census of India 2011 district populations (data.gov.in).
Our code: MIT (see LICENSE); `edge/` is distributed under AGPL-3.0 because it links Ultralytics YOLO.
