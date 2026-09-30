# Sadak Samvad — सड़क संवाद

**Track 1 · AI for Digital Public Infrastructure & Governance** — Team *The Road Runners*

Roads, drains and streetlights are the development requests Indians raise most often, yet complaints sit in fragmented portals, in 22 languages, with no link to where money is actually being sanctioned. **Sadak Samvad** is a multilingual, voice-first Digital Public Good that turns two streams into one ranked, state-ready works list:

1. **Citizens** report by **voice, text, photo, WhatsApp, IVR or a Common Service Centre**, in any scheduled Indian language. **Gemini** transcribes, translates, classifies, grades severity, reads the photo, routes it to the right department and replies to the citizen in their own language.
2. **City buses** carry our edge camera node (built and road-tested by the team in Bhubaneswar — 4-camera drive KIIT → Esplanade, Sep 2026 — plus a single-camera, GPS-geotagged run in Varanasi, Aug 2026). It detects potholes and hazards on-device, blurs number plates, and sends only events. **Gemini vision** re-checks each crop and writes the work-order line.
3. The **priority engine** fuses demand, severity, camera confirmation, vulnerable users and district population (Census 2011), and checks each hotspot against the **sanctioned-works plan** — exposing unfunded hotspots and spending that lands where nobody asked.
4. **Gemini** writes the policy brief for a Municipal Commissioner, a State PWD or a Ministry — in the reader's language — and answers questions grounded only in the data.

## How Google AI does the work
| Step | Google technology |
|---|---|
| Voice / text / photo understanding in 22 languages, structured JSON | Gemini (multimodal, controlled JSON output) |
| Second-opinion verification of bus-camera detections | Gemini vision |
| Policy brief and grounded Q&A, any language | Gemini |
| Hosting, live citizen-report store | Firebase Hosting, Firebase Realtime Database |
| Scale path | Cloud Run intake API + WhatsApp Business webhook, BigQuery for national analytics, Vertex AI for hotspot forecasting, Google Maps Platform |

## Data used in this prototype — stated plainly
* **Bus-camera evidence — real.** 34 GPS-tagged potholes from a single-camera run in Varanasi (29 Aug 2026) and a 3,944-detection 4-camera drive over 13.5 km in Bhubaneswar (19 Sep 2026; GPS was off, so positions are interpolated along the driven route by time and marked so).
* **District populations — real**, Census of India 2011.
* **Citizen requests and sanctioned works — synthetic samples**, labelled "sample" everywhere, standing in for CPGRAMS / state grievance portals and state works lists until those feeds are connected. Every report you submit here is real and is stored live.

## Why it scales across India (and BRICS)
* No state-specific code: districts, languages, departments and schemes are configuration.
* Voice + 22 languages + CSC channel reach citizens who never use a portal.
* Runs as static hosting + serverless; a state can be onboarded in days by loading its district list and works register.
* Open source, open data schema (GeoJSON events, JSON requests) — a Digital Public Good any city, or any BRICS country with its own languages, can fork.

## Built on / credits
Gemini API (Google), Firebase, Leaflet (BSD-2), OpenStreetMap tiles (ODbL), marked (MIT). Edge node: Ultralytics YOLO11 (AGPL-3.0), OpenCV (Apache-2.0), FastAPI (MIT); pothole model trained on the Roboflow "Detection-Potholes-Classes" dataset. The edge node was first built by the team for SIH 2026 (PS 26124); the Gemini intake, multilingual layer, investment alignment, priority engine and policy layer were built for this hackathon.
