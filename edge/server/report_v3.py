"""
PROJECT_V3 — a one-page run report, self-contained
==================================================
The artefact you can put in a deck or hand to someone after the demo: what this
run saw, from which camera, with the thumbnails an operator would verify. No
external CSS, no fonts, no images from anywhere else — it opens on any machine
with the network off, which is the same promise the dashboard makes.
"""
from __future__ import annotations

import html
import time


def build_report(snapshot: dict, events: list, alerts: list, ledger: list,
                 source_label: str, limits: list, metrics: dict) -> str:
    def esc(x):
        return html.escape(str(x))

    PRETTY = {"vru_in_carriageway": "VRU in carriageway",
              "animal_in_carriageway": "Animal in carriageway",
              "road_damage_candidate": "Surface anomaly",
              "pothole": "Pothole"}

    def lab(x):
        return esc(PRETTY.get(x, x))

    cams = snapshot.get("cameras", [])
    cam_rows = "".join(
        f"<tr><td><b>{esc(c['name'])}</b><div class=m>{esc(c.get('position',''))}</div></td>"
        f"<td>{esc(c.get('status'))}</td><td class=n>{c.get('fps_in',0)}</td>"
        f"<td class=n>{c.get('fps_analysed',0)}</td><td class=n>{round(c.get('share',0)*100)}%</td>"
        f"<td class=n>{c.get('events',0)}</td><td class=n>{c.get('ms_per_frame',0)}</td>"
        f"<td>{esc((c.get('ego') or {}).get('reason',''))}</td></tr>"
        for c in cams)

    alert_cards = "".join(
        f"<div class=card>"
        + (f"<img src='data:image/jpeg;base64,{a['thumbnail_b64']}'>" if a.get("thumbnail_b64") else "")
        + f"<div><b>{lab(a['label'])}</b> <span class=m>{esc(a.get('severity') or '')}</span>"
          f"<div class=m>{esc(a.get('camera_name'))} · {esc(a.get('position'))} · "
          f"t+{a.get('t',0)}s · conf {a.get('confidence')}</div>"
          f"<div class=id>{esc(a.get('event_id'))}</div></div></div>"
        for a in alerts[:12])

    ledger_rows = "".join(
        f"<tr><td>{esc(d.get('event_id'))}</td><td>{esc(d.get('severity'))}</td>"
        f"<td class=n>{d.get('confidence')}</td><td>{esc(d.get('camera_name'))}</td>"
        f"<td>{esc(d.get('position'))}</td><td class=n>t+{d.get('t')}s</td></tr>"
        for d in ledger[:40])

    lim = "".join(f"<li>{esc(l)}</li>" for l in limits)
    m = metrics
    return f"""<!doctype html><html><head><meta charset=utf-8>
<title>The Road Runners — run report</title>
<style>
body{{font:14px system-ui,Segoe UI,Roboto,sans-serif;background:#fff;color:#12212f;margin:0;padding:34px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:13px;letter-spacing:.08em;text-transform:uppercase;
color:#5a6b7d;margin:26px 0 10px}} .m{{color:#5a6b7d;font-size:12px}} .n{{text-align:right}}
table{{border-collapse:collapse;width:100%;font-size:13px}} td,th{{border-bottom:1px solid #e3e9ef;padding:6px 8px;text-align:left}}
th{{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#5a6b7d}}
.kpis{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-top:14px}}
.k{{border:1px solid #e3e9ef;border-radius:8px;padding:10px}} .k b{{font-size:20px}}
.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
.card{{display:flex;gap:9px;border:1px solid #e3e9ef;border-radius:8px;padding:8px}}
.card img{{width:96px;height:64px;object-fit:cover;border-radius:5px}}
.id{{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:#8b98a5}}
li{{margin-bottom:6px;color:#3d4c5a}}
</style></head><body>
<h1>Four-camera edge node — run report</h1>
<div class=m>The Road Runners · SIH 2026 · PS 26124 · {esc(source_label)} ·
generated {time.strftime('%d %b %Y, %H:%M')}</div>
<div class=kpis>
  <div class=k><b>{snapshot.get('events',0)}</b><div class=m>events</div></div>
  <div class=k><b>{snapshot.get('defects',0)}</b><div class=m>road defects</div></div>
  <div class=k><b>{snapshot.get('safety_alerts',0)}</b><div class=m>safety alerts</div></div>
  <div class=k><b>{snapshot.get('cameras_online',0)}/{snapshot.get('cameras_total',0)}</b><div class=m>cameras online</div></div>
  <div class=k><b>{snapshot.get('fps_analysed',0)}</b><div class=m>frames/s analysed</div></div>
  <div class=k><b>×{snapshot.get('compression','—')}</b><div class=m>video in ÷ telemetry out</div></div>
</div>
<h2>Cameras</h2>
<table><tr><th>Camera</th><th>Status</th><th class=n>fps in</th><th class=n>fps analysed</th>
<th class=n>share</th><th class=n>events</th><th class=n>ms/frame</th><th>Ego calibration</th></tr>
{cam_rows}</table>
<h2>Bandwidth measured on this run</h2>
<p>{snapshot.get('ingest_bytes',0):,} bytes arrived from the cameras; {snapshot.get('telemetry_bytes',0):,}
bytes of telemetry were produced. Over a 12-hour shift that projects to
<b>{snapshot.get('projected_mb_12h','—')} MB</b> of telemetry against
<b>{snapshot.get('projected_video_gb_12h','—')} GB</b> of video.</p>
<h2>Alerts — every one verified by the operator before it counts</h2>
<div class=cards>{alert_cards}</div>
<h2>Repair ledger</h2>
<table><tr><th>Event</th><th>Severity</th><th class=n>Conf</th><th>Camera</th><th>Mounted</th><th class=n>Time</th></tr>
{ledger_rows}</table>
<h2>Accuracy — held-out test split only</h2>
<p>mAP50 <b>{m.get('mAP50')}</b> · mAP50-95 <b>{m.get('mAP50_95')}</b> ·
precision <b>{m.get('precision')}</b> · recall <b>{m.get('recall')}</b> on {m.get('n_images')} images.
<span class=m>{esc(m.get('note',''))}</span></p>
<h2>What this node does not do</h2><ul>{lim}</ul>
</body></html>"""
