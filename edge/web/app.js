/* ══════════════════════════════════════════════════════════════════════
   The Road Runners — v3 multi-camera command dashboard (frontend)
   Team The Road Runners · SIH 2026 · PS 26124

   No framework, no bundler, no CDN. One file, plain DOM, canvas for the
   charts. That is deliberate: a build step is one more thing that can fail
   ten minutes before a demo, and a CDN tag would break the "runs with the
   network cable pulled out" claim.

   WHAT THE BROWSER IS GIVEN, AND WHAT IT DOES WITH IT
     preview   the camera's own JPEG, untouched  -> <img>
     analysis  boxes as FRACTIONS of the frame   -> drawn on a <canvas> over it
     stats     health, rates, bandwidth, shares  -> tiles and charts
   Boxes arrive as fractions so the overlay is correct at any tile size and
   the server never has to know how big this window is.
   ══════════════════════════════════════════════════════════════════════ */
(() => {
"use strict";

const $  = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => [...r.querySelectorAll(s)];
const fmt = (n, d=0) => (n === null || n === undefined || Number.isNaN(n)) ? "—"
  : Number(n).toLocaleString(undefined, {minimumFractionDigits:d, maximumFractionDigits:d});
const bytes = b => b >= 1e9 ? (b/1e9).toFixed(2)+" GB"
                 : b >= 1e6 ? (b/1e6).toFixed(1)+" MB"
                 : b >= 1e3 ? (b/1e3).toFixed(0)+" kB" : b+" B";

const CSS = getComputedStyle(document.documentElement);
const C = k => CSS.getPropertyValue(k).trim();
const PAL = {
  ink:C("--ink"), ink2:C("--ink-2"), ink3:C("--ink-3"), line:C("--line"),
  teal:C("--teal"), good:C("--good"), warning:C("--warning"),
  serious:C("--serious"), critical:C("--critical"), info:C("--info"),
  navy:C("--navy-900"), navy7:C("--navy-700"),
  series:[C("--s1"), C("--s3"), C("--s4"), C("--s5"), C("--s2")],
};
// Severity never travels as colour alone: icon + word + a distinct lightness.
const SEV = {
  severe:   {c:PAL.critical, ico:"⬣", word:"SEVERE"},
  moderate: {c:PAL.serious,  ico:"▲", word:"MODERATE"},
  minor:    {c:PAL.good,     ico:"●", word:"MINOR"},
  info:     {c:PAL.info,     ico:"◆", word:"NOTED"},
};

const S = {
  vocab:null, sources:null, running:false, mode:"live",
  cams:{},            // id -> {meta, det:[], last:0, health:{}}
  order:[],
  stats:null, events:[], alerts:[], ledger:[], timeline:[],
  t0:0, selectedVideo:null, focus:null, ws:null,
  pos:null,              // position summary + track + defects, from stats
  mapPins:[], mapHover:null, mapFilter:"defects",
};

/* ── boot ───────────────────────────────────────────────────────────── */
async function boot(){
  S.vocab = await (await fetch("/api/vocab")).json();
  S.sources = await (await fetch("/api/sources")).json();
  renderCameraRows();
  renderVideoList();
  renderLimits();
  wireUi();
  connect();
  setInterval(tickClock, 1000);
}

/* ── setup: live camera rows ────────────────────────────────────────── */
const POSITIONS = ["Front", "Rear", "Left", "Right", "Front-left", "Front-right", "Interior"];

function renderCameraRows(){
  const tb = $("#camRows"); tb.innerHTML = "";
  S.sources.cameras.forEach((c, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="text" class="c-name" value="${c.name || "Camera "+(i+1)}" style="font-family:var(--font)"></td>
      <td><select class="c-pos">${POSITIONS.map(p =>
            `<option ${p===c.position?"selected":""}>${p}</option>`).join("")}</select></td>
      <td><select class="c-kind">
            <option value="mjpeg" ${c.kind!=="device"?"selected":""}>Phone / IP camera</option>
            <option value="device" ${c.kind==="device"?"selected":""}>This laptop's webcam</option>
          </select></td>
      <td class="addr"><input type="text" class="c-url" placeholder="10.211.146.5  or  10.211.146.5:8081/live"
            value="${c.kind === "device" ? (c.index ?? 0) : (c.url || "")}"></td>
      <td><button class="btn small c-test">Test</button></td>
      <td><span class="probe wait c-res">not tested</span></td>`;
    tr.dataset.id = c.id || ("cam" + (i+1));
    tb.appendChild(tr);
    $(".c-test", tr).onclick = () => probeRow(tr);
    $(".c-kind", tr).onchange = e => {
      const dev = e.target.value === "device";
      const box = $(".c-url", tr);
      box.placeholder = dev ? "0   (camera index on this laptop)"
                            : "10.211.146.5  or  10.211.146.5:8081/live";
      if(dev && !/^\d+$/.test(box.value.trim())) box.value = "0";
    };
  });
}

function rowsToSpecs(){
  return $$("#camRows tr").map(tr => {
    const kind = $(".c-kind", tr).value;
    const v = $(".c-url", tr).value.trim();
    const base = {id: tr.dataset.id,
                  name: $(".c-name", tr).value.trim() || tr.dataset.id,
                  position: $(".c-pos", tr).value, kind};
    // A webcam on THIS laptop needs no server and no address — just its index.
    return kind === "device" ? {...base, index: Number(v || 0), url: ""}
                             : {...base, url: v};
  });
}

async function probeRow(tr){
  const res = $(".c-res", tr), url = $(".c-url", tr).value.trim();
  const kind = $(".c-kind", tr).value;
  if(!url && kind !== "device"){ res.className = "probe bad c-res"; res.textContent = "no address"; return; }
  res.className = "probe wait c-res"; res.textContent = "testing…";
  try{
    const endpoint = kind === "device" ? "/api/probe_device" : "/api/probe";
    const body = kind === "device" ? {index: Number(url || 0)} : {url};
    const r = await (await fetch(endpoint, {method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)})).json();
    if(r.ok){
      res.className = "probe ok c-res";
      res.textContent = `✓ ${r.width}×${r.height}, ${Math.round(r.frame_bytes/1024)} kB, ${r.ms} ms`;
    }else{
      res.className = "probe bad c-res"; res.textContent = "✕ " + r.reason;
    }
  }catch(e){ res.className = "probe bad c-res"; res.textContent = "✕ " + e; }
}

/* ── setup: recorded videos ─────────────────────────────────────────── */
function renderVideoList(){
  const host = $("#videoList"); host.innerHTML = "";
  if(!S.sources.videos.length){
    host.innerHTML = `<p class="muted">No videos found. Upload one below.</p>`;
  }
  S.sources.videos.forEach(v => {
    const d = document.createElement("div");
    d.className = "vidcard";
    d.innerHTML = `<div class="t">${v.label}</div><div class="n">${v.note}</div>
      <span class="tag">${v.layout === "quad" ? "4-CAMERA GRID" : "SINGLE CAMERA"}</span>
      <span class="tag">${bytes(v.bytes)}</span>`;
    d.onclick = async () => {
      $$(".vidcard").forEach(x => x.classList.remove("sel"));
      d.classList.add("sel");
      S.selectedVideo = v;
      $("#btnStartFile").disabled = false;
      // Start inside the stretch where all four cameras are live.
      if(v.start_s) $("#startAt").value = Math.round(v.start_s);
      const r = await (await fetch("/api/sidecars?video=" + encodeURIComponent(v.path))).json();
      const sel = $("#filePos");
      sel.innerHTML = `<option value="none">None — no GPS track for this drive</option>` +
        r.sidecars.map(sc => `<option value="${sc.path}">${sc.name} — ${sc.why}</option>`).join("");
      $("#filePosNote").textContent = r.sidecars.length
        ? "A GPS track puts every defect on the map."
        : "No GPS track found for this drive — the map will say so rather than invent one.";
    };
    host.appendChild(d);
  });
}

/* ── ui wiring ──────────────────────────────────────────────────────── */
function wireUi(){
  $$(".tab").forEach(t => t.onclick = () => {
    $$(".tab").forEach(x => x.classList.remove("active"));
    t.classList.add("active");
    $("#pane-live").classList.toggle("hidden", t.dataset.tab !== "live");
    $("#pane-file").classList.toggle("hidden", t.dataset.tab !== "file");
  });
  $("#livePos").onchange = e => {
    const on = e.target.value === "phone";
    $("#livePosHost").classList.toggle("hidden", !on);
    if(on){
      const sel = $("#livePosCam");
      sel.innerHTML = rowsToSpecs().map((c, i) =>
        `<option value="${i}">${c.name} (${c.position})</option>`).join("");
    }
  };
  $("#btnTestGps").onclick = async () => {
    const i = Number($("#livePosCam").value || 0);
    const host = (rowsToSpecs()[i] || {}).url || "";
    const res = $("#gpsResult");
    res.className = "probe wait"; res.textContent = "testing…";
    const r = await (await fetch("/api/probe_gps", {method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify({host})})).json();
    if(r.ok){ res.className = "probe ok"; res.textContent = `✓ ${r.lat}, ${r.lon}`; }
    else { res.className = "probe bad"; res.textContent = "✕ " + r.reason; }
  };
  $("#btnMapBig").onclick = () => { $("#mapModal").classList.remove("hidden"); drawMap(true); };
  $$(".chipbtn").forEach(b => b.onclick = () => {
    $$(".chipbtn").forEach(x => x.classList.remove("on"));
    b.classList.add("on");
    S.mapFilter = b.dataset.mapfilter;
    drawMap(false);
    if(!$("#mapModal").classList.contains("hidden")) drawMap(true);
  });
  $("#btnCloseMap").onclick = () => $("#mapModal").classList.add("hidden");
  $("#mapCanvas").addEventListener("mousemove", e => mapHover(e, $("#mapCanvas")));
  $("#mapCanvas").addEventListener("mouseleave", () => hideTip());
  $("#mapBig").addEventListener("mousemove", e => mapHover(e, $("#mapBig")));
  $("#mapBig").addEventListener("mouseleave", () => hideTip());
  $("#btnTestAll").onclick = () => $$("#camRows tr").forEach(probeRow);
  $("#btnSaveCams").onclick = async () => {
    await fetch("/api/cameras", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({cameras: rowsToSpecs()})});
    toast("Addresses saved. They will be here next time.");
  };
  $("#btnStartLive").onclick = () => {
    const cams = rowsToSpecs().filter(c => c.url || c.kind === "device");
    if(!cams.length){ toast("Type at least one camera address first."); return; }
    const pos = $("#livePos").value === "phone"
      ? {kind:"phone", host:(cams[Number($("#livePosCam").value || 0)] || {}).url || ""}
      : {kind:"none"};
    start({mode:"live", cameras:cams, work_width:Number($("#workWidth").value),
           position:pos});
  };
  $("#btnStartFile").onclick = () => {
    if(!S.selectedVideo) return;
    const sc = $("#filePos").value;
    start({mode:"recorded", path:S.selectedVideo.path, layout:S.selectedVideo.layout,
           start_s:Number($("#startAt").value || 0), end_s:S.selectedVideo.end_s || null,
           loop:$("#loopVid").checked,
           work_width:Number($("#workWidth").value || 640),
           position: sc && sc !== "none"
             ? {kind:"sidecar", path:sc, offset_s:Number($("#startAt").value || 0)}
             : {kind:"none"}});
  };
  $("#fileInput").onchange = async e => {
    const f = e.target.files[0]; if(!f) return;
    $("#uploadNote").textContent = `Uploading ${f.name}…`;
    const fd = new FormData(); fd.append("file", f);
    const r = await (await fetch("/api/upload", {method:"POST", body:fd})).json();
    S.sources = await (await fetch("/api/sources")).json();
    renderVideoList();
    $("#uploadNote").textContent = `${r.name} uploaded — pick it in the list above.`;
  };
  $("#btnStop").onclick = async () => {
    await fetch("/api/stop", {method:"POST"});
    S.running = false; setBadge("STANDBY", "");
    toast("Stopped. The session file is kept in sessions\\.");
  };
  $("#btnPause").onclick = async () => {
    const r = await (await fetch("/api/pause", {method:"POST"})).json();
    $("#btnPause").textContent = r.paused ? "Resume" : "Pause";
  };
  $("#btnNew").onclick = () => {
    $("#live").classList.add("hidden"); $("#setup").classList.remove("hidden");
    $("#btnNew").classList.add("hidden");
  };
  $("#btnLimits").onclick = () => $("#limitsModal").classList.remove("hidden");
  $("#btnCloseLimits").onclick = () => $("#limitsModal").classList.add("hidden");

  document.addEventListener("keydown", e => {
    if(e.key === "Escape"){ $("#limitsModal").classList.add("hidden"); setFocus(null); }
    if(e.key === "p" || e.key === "P") document.body.classList.toggle("present");
    if(e.key === "f" || e.key === "F"){
      if(document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen();
    }
    if(["1","2","3","4"].includes(e.key)){
      const id = S.order[Number(e.key)-1];
      if(id) setFocus(S.focus === id ? null : id);
    }
  });
  window.addEventListener("resize", () => { drawAll(true); });
  document.addEventListener("visibilitychange", () => { if(!document.hidden) drawAll(true); });
}

async function start(payload){
  $("#bootStatus").classList.remove("hidden");
  $("#bootText").textContent = "Starting…";
  const r = await (await fetch("/api/start", {method:"POST",
    headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)})).json();
  if(!r.ok){ $("#bootText").textContent = r.reason || "Could not start."; return; }
  S.mode = payload.mode;
}

/* ── websocket ──────────────────────────────────────────────────────── */
function connect(){
  const ws = new WebSocket(`ws://${location.host}/ws`);
  S.ws = ws;
  ws.onopen = () => setLink(true);
  ws.onmessage = ev => handle(JSON.parse(ev.data));
  ws.onclose = () => { setLink(false); setTimeout(connect, 1200); };
  ws.onerror = () => setLink(false);
}

/* A dashboard that has quietly lost its data link looks exactly like one where
   nothing is happening. Say which it is. */
function setLink(ok){
  const el = $("#linkChip");
  if(!el) return;
  el.classList.toggle("hidden", ok);
}

function handle(m){
  switch(m.type){
    case "status":
      $("#bootStatus").classList.remove("hidden");
      $("#bootText").textContent = m.message; break;
    case "started":   onStarted(m); break;
    case "resync":    onResync(m); break;
    case "preview":   onPreview(m); break;
    case "analysis":  onAnalysis(m); break;
    case "event":     onEvent(m.event); break;
    case "stats":     onStats(m); break;
    case "calibration": onCalibration(m); break;
    case "error":
      $("#bootStatus").classList.remove("hidden");
      $("#bootText").textContent = "⚠ " + m.message;
      toast("⚠ " + m.message); break;
  }
}

function onStarted(m){
  S.running = true; S.t0 = Date.now(); S.order = m.cameras.map(c => c.id);
  S.cams = {}; S.events = []; S.alerts = []; S.ledger = []; S.timeline = [];
  m.cameras.forEach(c => S.cams[c.id] = {meta:c, det:[], health:{}, lastEvent:null, ego:null});
  buildTiles(m.cameras);
  $("#setup").classList.add("hidden");
  $("#live").classList.remove("hidden");
  $("#bootStatus").classList.add("hidden");
  $("#btnStop").classList.remove("hidden");
  $("#btnPause").classList.remove("hidden");
  $("#btnNew").classList.remove("hidden");
  $("#sourceLabel").textContent = m.source_label || "";
  setBadge(m.mode === "live" ? "LIVE FEED" : "RECORDED FEED",
           m.mode === "live" ? "live" : "recorded");
  $("#schedNote").textContent = S.vocab.scheduler_note;
  const md = m.models || {};
  $("#nodeNote").innerHTML =
    `Detectors: <b>${md.coco || "—"}</b>${md.pothole ? " + <b>" + md.pothole + "</b>" : ""} ·
     working width ${m.work_width} px · every message written to
     <span class="mono">sessions\\${m.session || "—"}</span> as it is sent.`;
}

/* A browser refresh in the middle of a run must not lose the run. The server
   replays what it has: the same 'started' payload, then the events so far. */
function onResync(m){
  onStarted(m.started);
  S.events = m.events || [];
  S.alerts = (m.alerts || []).slice().reverse();
  S.ledger = (m.ledger || []).slice().reverse();
  S.timeline = m.timeline || [];
  renderAlerts(); renderLedger();
}

function setBadge(text, cls){
  $("#feedText").textContent = text;
  $("#feedBadge").className = "feed-badge " + cls;
}

/* ── camera tiles ───────────────────────────────────────────────────── */
function buildTiles(cams){
  const g = $("#camGrid"); g.innerHTML = "";
  cams.forEach(c => {
    const el = document.createElement("div");
    el.className = "tile"; el.id = "tile-" + c.id;
    el.innerHTML = `
      <div class="tile-head">
        <span class="dot"></span>
        <span class="nm">${c.name}</span>
        <span class="pos">${c.position || "—"}</span>
        <span class="stats"><span class="t-in">—</span><span class="t-ms">—</span></span>
      </div>
      <div class="tile-body">
        <img alt="${c.name}">
        <canvas></canvas>
        <div class="tile-empty">Waiting for the first frame…</div>
      </div>
      <div class="tile-foot">
        <span class="chip ego-learn t-ego">calibrating</span>
        <span class="chip t-fresh">—</span>
        <span class="t-last muted"></span>
      </div>`;
    el.querySelector(".tile-body").onclick = () => setFocus(S.focus === c.id ? null : c.id);
    g.appendChild(el);
    // The picture comes straight from the server as MJPEG, so the browser
    // decodes it in its own pipeline and this script never touches a frame.
    const img = $("img", el);
    img.src = `/api/stream/${c.id}?run=${Date.now()}`;
    img.onload = () => {
      $(".tile-empty", el).classList.add("hidden");
      const ar = img.naturalWidth / Math.max(img.naturalHeight, 1);
      const cam = S.cams[c.id];
      if(img.naturalWidth && cam && (!cam.ar || Math.abs(ar - cam.ar) > 0.02)){
        cam.ar = ar;
        $(".tile-body", el).style.aspectRatio = ar.toFixed(4);
      }
      drawBoxes(c.id);
    };
    img.onerror = () => { $(".tile-empty", el).classList.remove("hidden"); };
    // Belt and braces: some browsers fire `load` only once for a multipart
    // stream, and older ones not at all. Poll briefly for the first frame so a
    // tile can never sit behind a "waiting" overlay that is no longer true.
    let tries = 0;
    const settle = setInterval(() => {
      if(++tries > 60){ clearInterval(settle); return; }
      if(!img.naturalWidth) return;
      clearInterval(settle);
      $(".tile-empty", el).classList.add("hidden");
      const ar = img.naturalWidth / Math.max(img.naturalHeight, 1);
      const cam = S.cams[c.id];
      if(cam && (!cam.ar || Math.abs(ar - cam.ar) > 0.02)){
        cam.ar = ar;
        $(".tile-body", el).style.aspectRatio = ar.toFixed(4);
      }
      drawBoxes(c.id);
    }, 500);
  });
}

function setFocus(id){
  S.focus = id;
  $$(".tile").forEach(t => t.classList.toggle("focus", t.id === "tile-" + id));
}

/* Previews no longer travel on the socket — the tiles are <img> elements
   pointed at /api/stream/<camera>. This handler stays only so that an older
   server, if one is ever pointed at this page, still shows a picture. */
function onPreview(m){
  const tile = $("#tile-" + m.camera); if(!tile) return;
  const img = $("img", tile);
  if(img.src.startsWith("/api/stream") || img.src.includes("/api/stream")) return;
  img.src = "data:image/jpeg;base64," + m.jpeg;
  $(".tile-empty", tile).classList.add("hidden");
}

function onAnalysis(m){
  const cam = S.cams[m.camera]; if(!cam) return;
  cam.det = m.detections; cam.lastAnalysis = Date.now();
  drawBoxes(m.camera);
}

/* Boxes are drawn from FRACTIONS of the frame, so the overlay is right at
   any tile size and the server never has to know this window's geometry. */
function drawBoxes(camId){
  const tile = $("#tile-" + camId); if(!tile) return;
  const cam = S.cams[camId];
  const img = $("img", tile), cv = $("canvas", tile);
  if(!img.naturalWidth) return;
  const r = img.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  cv.width = Math.max(2, Math.round(r.width * dpr));
  cv.height = Math.max(2, Math.round(r.height * dpr));
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, r.width, r.height);

  // object-fit: contain — find the letterboxed picture inside the element
  const ar = img.naturalWidth / img.naturalHeight;
  let w = r.width, h = r.width / ar;
  if(h > r.height){ h = r.height; w = r.height * ar; }
  const ox = (r.width - w) / 2, oy = (r.height - h) / 2;

  (cam.det || []).forEach(d => {
    const [fx, fy, fw, fh] = d.bbox;
    const x = ox + fx * w, y = oy + fy * h, bw = fw * w, bh = fh * h;
    const col = d.cat === "defect" ? (SEV[d.sev] || SEV.minor).c
              : d.cat === "vru" || d.cat === "animal_road" ? PAL.critical
              : d.cat === "candidate" ? PAL.ink3
              : d.cat === "pedestrian" || d.cat === "animal" ? PAL.good
              : PAL.series[0];
    ctx.lineWidth = (d.cat === "defect" || d.cat === "vru" || d.cat === "animal_road") ? 3 : 2;
    ctx.strokeStyle = col;
    ctx.strokeRect(x, y, bw, bh);
    const label = d.cat === "defect" ? `${(d.sev||"").toUpperCase()} POTHOLE ${d.conf.toFixed(2)}`
                : d.cat === "vru" ? "VULNERABLE ROAD USER"
                : d.cat === "animal_road" ? "ANIMAL IN CARRIAGEWAY"
                : d.cat === "candidate" ? "surface anomaly"
                : `${d.label} ${d.conf.toFixed(2)}`;
    ctx.font = "600 11px system-ui, sans-serif";
    const tw = ctx.measureText(label).width + 10;
    ctx.fillStyle = col;
    ctx.fillRect(x, Math.max(0, y - 15), tw, 15);
    ctx.fillStyle = "#fff";
    ctx.fillText(label, x + 5, Math.max(11, y - 4));
  });
}

function onCalibration(m){
  const tile = $("#tile-" + m.camera); if(!tile) return;
  const e = m.ego || {};
  const chip = $(".t-ego", tile);
  if(e.ego_present){
    chip.className = "chip ego-ok t-ego";
    chip.textContent = `ego mask ${(e.mask_frac*100).toFixed(0)}%`;
  }else{
    chip.className = "chip ego-none t-ego";
    chip.textContent = "no ego vehicle in view";
  }
  chip.title = e.reason || "";
  S.cams[m.camera].ego = e;
}

/* ── events ─────────────────────────────────────────────────────────── */
function onEvent(ev){
  S.events.push(ev);
  if(S.events.length > 2500) S.events = S.events.slice(-2000);
  S.timeline.push({t:ev.t, camera:ev.camera, severity:ev.severity, label:ev.label});
  if(S.timeline.length > 4000) S.timeline = S.timeline.slice(-3000);
  const cam = S.cams[ev.camera];
  if(cam) cam.lastEvent = ev;

  const isAlert = ev.severity || ["vru_in_carriageway","animal_in_carriageway",
                                  "road_damage_candidate"].includes(ev.label);
  if(isAlert){ S.alerts.unshift(ev); S.alerts = S.alerts.slice(0, 40); renderAlerts(); }
  if(ev.label === "pothole"){ S.ledger.unshift(ev); S.ledger = S.ledger.slice(0, 60); renderLedger(); }

  const tile = $("#tile-" + ev.camera);
  if(tile){
    const sev = SEV[ev.severity] || SEV.info;
    $(".t-last", tile).innerHTML =
      `<span class="sev-ico sev-${ev.severity||"info"}">${sev.ico}</span>${prettyLabel(ev.label)} · ${ev.confidence.toFixed(2)}`;
  }
}

const prettyLabel = l => ({
  vru_in_carriageway: "VRU in carriageway",
  animal_in_carriageway: "Animal in carriageway",
  road_damage_candidate: "Surface anomaly",
  pothole: "Pothole",
}[l] || l);

function renderAlerts(){
  const host = $("#alertList");
  $("#alertCount").textContent = `${S.alerts.length} shown · ${S.events.length} events total`;
  host.innerHTML = S.alerts.map(a => {
    const sev = SEV[a.severity] || SEV.info;
    const cls = a.severity || "info";
    return `<div class="alert ${cls}">
      ${a.thumbnail_b64 ? `<img src="data:image/jpeg;base64,${a.thumbnail_b64}">` : ""}
      <div>
        <div class="a-lab"><span class="sev-ico sev-${cls}">${sev.ico}</span>${prettyLabel(a.label)}
          <span class="muted">${sev.word}</span></div>
        <div class="a-meta">${a.camera_name} · ${a.position} · t+${a.t.toFixed(1)}s · conf ${a.confidence.toFixed(2)}</div>
        <div class="a-meta">${a.event_id}</div>
      </div></div>`;
  }).join("");
}

function renderLedger(){
  $("#ledger").innerHTML = S.ledger.map(d => {
    const sev = SEV[d.severity] || SEV.minor;
    return `<div class="lrow">
      ${d.thumbnail_b64 ? `<img src="data:image/jpeg;base64,${d.thumbnail_b64}">` : ""}
      <div>
        <div><span class="sev-ico sev-${d.severity||"minor"}">${sev.ico}</span>
          <b>${sev.word}</b> pothole · conf ${d.confidence.toFixed(2)}</div>
        <div class="lm">${d.camera_name} · ${d.position} · t+${d.t.toFixed(1)}s</div>
        <div class="lid">${d.event_id} — verify before dispatch</div>
      </div></div>`;
  }).join("") || `<p class="muted">No road damage classified yet.</p>`;
}

/* ── stats, KPIs, charts ────────────────────────────────────────────── */
function onStats(m){
  S.stats = m;
  m.cameras.forEach(c => {
    const cam = S.cams[c.id]; if(cam) cam.health = c;
    const tile = $("#tile-" + c.id); if(!tile) return;
    $(".dot", tile).className = "dot " + (c.status || "");
    $(".t-in", tile).textContent = `${fmt(c.fps_in,1)} fps in`;
    $(".t-ms", tile).textContent = `${fmt(c.ms_per_frame)} ms`;
    const fresh = $(".t-fresh", tile);
    const s = c.since_analysis_s;
    fresh.textContent = s === null || s === undefined ? "—" : `analysed ${s.toFixed(1)}s ago`;
    fresh.className = "chip t-fresh" + (s > 4 ? " stale" : "");
    if(c.status === "reconnecting" || c.status === "error"){
      $(".tile-empty", tile).classList.remove("hidden");
      $(".tile-empty", tile).textContent = c.detail || "Camera offline.";
    }
    const chip = $(".t-ego", tile);
    if(c.ego && c.ego.state !== "done" && !c.ego.ego_present && chip.className.includes("ego-learn")){
      chip.textContent = `calibrating · ${c.ego.pairs_used || 0} pairs`;
    }
  });
  S.pos = m.position || null;
  renderKpis(m);
  drawAll();
}

function renderKpis(m){
  const online = `${m.cameras_online}/${m.cameras_total}`;
  const k = [
    {v:fmt(m.events), l:"events", s:`${fmt(m.events_per_min,1)}/min`},
    {v:fmt(m.defects), l:"road defects",
     s:`${fmt((m.severity_defects || m.severity).severe)} severe`},
    {v:fmt(m.safety_alerts), l:"safety alerts", s:"VRU + animal"},
    {v:online, l:"cameras online", s:`${fmt(m.frames_analysed)} frames analysed`, hot:m.cameras_online === m.cameras_total},
    {v:fmt(m.fps_analysed,2), l:"fps analysed", s:`${fmt(m.ms_per_frame)} ms/frame`},
    {v:m.compression ? "×" + fmt(m.compression) : "—", l:"bandwidth saved", s:"video in ÷ telemetry out", hot:true},
    (m.waterlogging_enabled === false
      ? {v:"off", l:"waterlogging", s:"failed our own dry road"}
      : {v:fmt(m.waterlogging), l:"waterlogging", s:`${fmt(m.waterlogging_severe)} severe by extent`}),
    {v:m.congestion ? m.congestion.index : "—", l:"congestion index",
     s:m.congestion ? `${m.congestion.level} · derived, not sensed` : "no reading yet",
     hot:!!(m.congestion && m.congestion.index >= 70)},
    {v:fmt(m.plates_redacted), l:"plates blurred", s:"blurred in the preview and before storage", hot:true},
  ];
  $("#kpis").innerHTML = k.map(x =>
    `<div class="kpi ${x.hot ? "hot" : ""}"><div class="v">${x.v}</div>
     <div class="l">${x.l}</div><div class="s">${x.s}</div></div>`).join("");
}

let lastDraw = 0;
function drawAll(force){
  if(!S.stats) return;
  // A hidden tab does not need charts, and redrawing four canvases twice a
  // second on a machine that is also running the detector is work nobody sees.
  if(document.hidden) return;
  const now = performance.now();
  if(!force && now - lastDraw < 900) return;
  lastDraw = now;
  drawBandwidth(S.stats);
  drawSchedule(S.stats);
  drawTimeline();
  drawMix(S.stats);
  drawMap(false);
  if(!$("#mapModal").classList.contains("hidden")) drawMap(true);
  S.order.forEach(drawBoxes);
}

function fitCanvas(cv){
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || 320, h = Number(cv.getAttribute("height")) || 120;
  cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return {ctx, w, h};
}

/* Two magnitudes three orders of magnitude apart cannot share a bar length
   honestly, so they do not pretend to: the video bar is the full width, the
   telemetry bar is drawn to scale (usually a sliver) and BOTH are labelled
   with their real value. The ratio is the headline. */
function drawBandwidth(m){
  const {ctx, w, h} = fitCanvas($("#bwChart"));
  const inKbps = m.ingest_kbps || 0, outKbps = (m.telemetry_bps || 0) / 1024;
  const barH = 26, x0 = 8, top = 18, gap = 34, maxW = w - 16;
  const draw = (y, frac, col, name, val) => {
    ctx.fillStyle = "rgba(27,58,92,.45)";
    roundRect(ctx, x0, y, maxW, barH, 4); ctx.fill();
    ctx.fillStyle = col;
    roundRect(ctx, x0, y, Math.max(3, maxW * frac), barH, 4); ctx.fill();
    ctx.fillStyle = PAL.ink; ctx.font = "700 12px system-ui, sans-serif";
    ctx.fillText(name, x0 + 9, y + 17);
    ctx.textAlign = "right";
    ctx.fillStyle = PAL.ink2; ctx.font = "600 12px ui-monospace, monospace";
    ctx.fillText(val, x0 + maxW - 9, y + 17);
    ctx.textAlign = "left";
  };
  const frac = inKbps > 0 ? Math.min(1, outKbps / inKbps) : 0;
  draw(top, 1, "rgba(217,89,38,.75)", "Camera video into the node", fmt(inKbps,0) + " kB/s");
  draw(top + gap, frac, PAL.teal, "Telemetry out of the node", fmt(outKbps,2) + " kB/s");
  ctx.fillStyle = PAL.ink3; ctx.font = "11px system-ui, sans-serif";
  ctx.fillText("both bars drawn to the same scale",
               x0, top + gap + barH + 14);
  $("#bwLines").innerHTML = `
    <div>Seen at the door: <b>${bytes(m.ingest_bytes)}</b> from ${m.cameras_online} camera(s)</div>
    <div>Sent as telemetry: <b>${bytes(m.telemetry_bytes)}</b> — ${m.compression ? "×"+fmt(m.compression)+" smaller" : "—"}</div>
    <div>Projected over a 12-hour shift: <b>${fmt(m.projected_mb_12h,1)} MB</b> of telemetry
         vs <b>${fmt(m.projected_video_gb_12h,1)} GB</b> of video</div>`;
}

/* Identity, not magnitude: each camera keeps its colour whatever its share. */
function drawSchedule(m){
  const {ctx, w, h} = fitCanvas($("#schedChart"));
  const rows = m.cameras;
  const rowH = Math.min(30, (h - 16) / Math.max(rows.length, 1));
  const labelW = 88, maxW = w - labelW - 132;
  rows.forEach((c, i) => {
    const y = 8 + i * rowH;
    const col = PAL.series[i % PAL.series.length];
    ctx.fillStyle = PAL.ink2; ctx.font = "600 11.5px system-ui, sans-serif";
    ctx.fillText(c.name.length > 13 ? c.name.slice(0,12)+"…" : c.name, 0, y + 14);
    ctx.fillStyle = "rgba(27,58,92,.45)";
    roundRect(ctx, labelW, y + 3, maxW, rowH - 10, 3); ctx.fill();
    ctx.fillStyle = c.status === "live" ? col : "rgba(111,134,158,.4)";
    roundRect(ctx, labelW, y + 3, Math.max(2, maxW * (c.share || 0)), rowH - 10, 3); ctx.fill();
    ctx.fillStyle = PAL.ink; ctx.font = "600 11px ui-monospace, monospace";
    ctx.fillText(`${((c.share||0)*100).toFixed(0)}%`, labelW + maxW + 6, y + 14);
    ctx.fillStyle = PAL.ink3; ctx.font = "10.5px ui-monospace, monospace";
    ctx.fillText(`${fmt(c.fps_analysed,2)}/s · ${fmt(c.events)} ev`,
                 labelW + maxW + 40, y + 14);
  });
}

/* Swimlanes: one row per camera, one tick per event, newest on the right.
   Severity carries icon + word in the legend, so the colour is never alone. */
function drawTimeline(){
  const cv = $("#timeline");
  const {ctx, w, h} = fitCanvas(cv);
  const cams = S.order, lanes = cams.length || 1;
  const now = S.stats ? S.stats.uptime_s : 0;
  const span = 120;                    // seconds visible
  const t1 = Math.max(now, span), t0 = t1 - span;
  const labelW = 92, plotW = w - labelW - 8, laneH = (h - 26) / lanes;

  ctx.strokeStyle = "rgba(27,58,92,.6)"; ctx.lineWidth = 1;
  for(let s = 0; s <= span; s += 30){
    const x = labelW + plotW * (s / span);
    ctx.beginPath(); ctx.moveTo(x, 4); ctx.lineTo(x, h - 20); ctx.stroke();
    ctx.fillStyle = PAL.ink3; ctx.font = "10px ui-monospace, monospace";
    ctx.fillText(`-${span - s}s`, x + 3, h - 8);
  }
  cams.forEach((cid, i) => {
    const y = 6 + i * laneH;
    ctx.fillStyle = PAL.ink2; ctx.font = "600 11px system-ui, sans-serif";
    const cam = S.cams[cid];
    ctx.fillText((cam.meta.name || cid).slice(0, 12), 0, y + laneH / 2 + 4);
    ctx.fillStyle = "rgba(27,58,92,.35)";
    ctx.fillRect(labelW, y, plotW, laneH - 4);
  });
  S.timeline.forEach(e => {
    if(e.t < t0) return;
    const i = cams.indexOf(e.camera); if(i < 0) return;
    const x = labelW + plotW * ((e.t - t0) / span);
    const y = 6 + i * laneH;
    const sev = SEV[e.severity] || SEV.info;
    ctx.fillStyle = sev.c;
    const hgt = e.severity ? laneH - 8 : (laneH - 8) * 0.45;
    ctx.fillRect(x, y + (laneH - 4 - hgt) / 2, e.severity ? 3 : 2, hgt);
  });
  // "now" edge
  ctx.strokeStyle = PAL.teal; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(labelW + plotW, 4); ctx.lineTo(labelW + plotW, h - 20); ctx.stroke();

  $("#timelineLegend").innerHTML = Object.entries(SEV).map(([k, v]) =>
    `<span><i style="background:${v.c}"></i>${v.ico} ${v.word.toLowerCase()}</span>`).join("")
    + `<span class="muted">tick height marks a graded event; thin ticks are ungraded detections</span>`;
}

/* One series, so no legend box: the title names it. Direct value labels. */
function drawMix(m){
  const {ctx, w, h} = fitCanvas($("#mixChart"));
  const entries = Object.entries(m.labels || {})
    .filter(([k]) => !["road_damage_candidate"].includes(k))
    .sort((a, b) => b[1] - a[1]).slice(0, 7);
  if(!entries.length){
    ctx.fillStyle = PAL.ink3; ctx.font = "12px system-ui, sans-serif";
    ctx.fillText("Nothing counted yet.", 4, 20); return;
  }
  const max = Math.max(...entries.map(e => e[1]));
  const labelW = 104, maxW = w - labelW - 46, rowH = Math.min(22, (h - 8) / entries.length);
  entries.forEach(([k, v], i) => {
    const y = 4 + i * rowH;
    ctx.fillStyle = PAL.ink2; ctx.font = "600 11.5px system-ui, sans-serif";
    ctx.fillText(prettyLabel(k), 0, y + rowH / 2 + 4);
    ctx.fillStyle = "rgba(47,227,208,.22)";
    roundRect(ctx, labelW, y + 3, maxW, rowH - 9, 3); ctx.fill();
    ctx.fillStyle = PAL.teal;
    roundRect(ctx, labelW, y + 3, Math.max(2, maxW * v / max), rowH - 9, 3); ctx.fill();
    ctx.fillStyle = PAL.ink; ctx.font = "600 11px ui-monospace, monospace";
    ctx.fillText(String(v), labelW + maxW + 6, y + rowH / 2 + 4);
  });
}


/* ── the map ────────────────────────────────────────────────────────────
   Drawn on a canvas from our own coordinates. No tiles, no API key, no
   network: a node that has to boot inside a bus should not need a map
   provider to draw a line between two points, and a demo venue's Wi-Fi is
   not a dependency worth having.

   The projection is a local equirectangular one — longitude scaled by
   cos(latitude) — which over a few kilometres is accurate to well under the
   GPS error it is drawing, and is forty lines instead of a geospatial stack.
   The scale bar is computed from the same numbers, so what you measure off
   the screen is what the data says.                                        */
function drawMap(big){
  const cv = big ? $("#mapBig") : $("#mapCanvas");
  if(!cv) return;
  const {ctx, w, h} = fitCanvas(cv);
  const pos = S.pos || {};
  const track = pos.track || [];
  const pins = (pos.defects || []).filter(d =>
    S.mapFilter === "all" ? true : d.label === "pothole");
  const where = big ? "#mapBigSource" : "#mapSource";
  const legend = big ? "#mapBigLegend" : "#mapLegend";

  const srcName = {phone:"phone GNSS, live", sidecar:"recorded GPS track",
                   none:"no position feed"}[pos.kind] || "—";
  $(where).textContent = srcName;

  const allPins = pos.defects || [];
  if(!pins.length && allPins.length && S.mapFilter === "defects" && !big){
    $("#mapEmpty").textContent =
      `No pothole has been geo-tagged yet. ${allPins.length} safety alert(s) are on the map ` +
      `under "All hazards".`;
  }
  if(!track.length && !pins.length){
    ctx.fillStyle = PAL.ink3;
    ctx.font = "12.5px system-ui, sans-serif";
    ctx.fillText("No position on this run.", 14, 26);
    $(legend).innerHTML = "";
    $("#mapEmpty").textContent = pos.detail ||
      "Turn on Location in IP Webcam on one phone and give its address in the " +
      "live tab, or attach a recorded GPS track to a recorded drive.";
    if(big) $("#mapBigNote").textContent = $("#mapEmpty").textContent;
    return;
  }
  $("#mapEmpty").textContent = "";

  // -- fit the bounds of everything we are going to draw -----------------
  const lats = track.map(p => p[0]).concat(pins.map(p => p.lat));
  const lons = track.map(p => p[1]).concat(pins.map(p => p.lon));
  const lat0 = (Math.min(...lats) + Math.max(...lats)) / 2;
  const k = Math.cos(lat0 * Math.PI / 180);
  const X = lon => lon * k, Y = lat => -lat;
  let x0 = Math.min(...lons.map(X)), x1 = Math.max(...lons.map(X));
  let y0 = Math.min(...lats.map(Y)), y1 = Math.max(...lats.map(Y));
  const padDeg = 0.00025;                       // ~25 m, so a single point still has a frame
  x0 -= padDeg; x1 += padDeg; y0 -= padDeg; y1 += padDeg;
  const pad = 26;
  const sx = (w - pad * 2) / Math.max(x1 - x0, 1e-9);
  const sy = (h - pad * 2) / Math.max(y1 - y0, 1e-9);
  const s = Math.min(sx, sy);
  const ox = pad + ((w - pad * 2) - (x1 - x0) * s) / 2;
  const oy = pad + ((h - pad * 2) - (y1 - y0) * s) / 2;
  const px = (lat, lon) => [ox + (X(lon) - x0) * s, oy + (Y(lat) - y0) * s];

  // -- the driven track ---------------------------------------------------
  if(track.length > 1){
    ctx.strokeStyle = "rgba(47,227,208,.55)";
    ctx.lineWidth = 3; ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.beginPath();
    track.forEach((p, i) => {
      const [x, y] = px(p[0], p[1]);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
    const [sx0, sy0] = px(track[0][0], track[0][1]);
    ctx.fillStyle = PAL.ink2;
    ctx.beginPath(); ctx.arc(sx0, sy0, 4, 0, 7); ctx.fill();
    ctx.font = "600 10.5px system-ui, sans-serif";
    ctx.fillText("START", sx0 + 7, sy0 + 4);
    const [ex, ey] = px(track[track.length-1][0], track[track.length-1][1]);
    ctx.fillStyle = PAL.teal;
    ctx.beginPath(); ctx.arc(ex, ey, 5, 0, 7); ctx.fill();
    ctx.fillText("NOW", ex + 8, ey + 4);
  }

  // -- the defects --------------------------------------------------------
  // Severity carries icon + word in the legend and in the tooltip; the colour
  // is the third channel, never the only one.
  // CLUSTER WHAT OVERLAPS, AND SAY SO. Several defects within a few metres are
  // several defects — drawing them on top of each other would hide the count and
  // make a busy stretch look like one pin, so they merge into one mark carrying
  // the number, ranked by the worst severity in the group.
  S.mapPins = [];
  const cell = 15, groups = new Map();
  pins.forEach(d => {
    const [x, y] = px(d.lat, d.lon);
    const key = Math.round(x / cell) + ":" + Math.round(y / cell);
    if(!groups.has(key)) groups.set(key, {x, y, items:[]});
    const g = groups.get(key);
    g.items.push(d);
    g.x = (g.x * (g.items.length - 1) + x) / g.items.length;
    g.y = (g.y * (g.items.length - 1) + y) / g.items.length;
  });
  const rank = {severe:3, moderate:2, minor:1};
  groups.forEach(g => {
    const worst = g.items.slice().sort((a, b) =>
      (rank[b.severity] || 0) - (rank[a.severity] || 0))[0];
    const sev = SEV[worst.severity] || (worst.label === "pothole" ? SEV.minor : SEV.info);
    const n = g.items.length;
    const r = (worst.severity === "severe" ? 7 : worst.severity === "moderate" ? 6 : 5)
              + Math.min(5, n > 1 ? 2 + Math.log2(n) : 0);
    ctx.beginPath(); ctx.arc(g.x, g.y, r + 2.5, 0, 7);
    ctx.fillStyle = "rgba(5,16,28,.9)"; ctx.fill();       // ring keeps pins separable
    ctx.beginPath(); ctx.arc(g.x, g.y, r, 0, 7);
    ctx.fillStyle = sev.c; ctx.fill();
    if(g.items.some(d => d.gps_quality === "interp")){    // never hidden, always marked
      ctx.strokeStyle = PAL.ink3; ctx.lineWidth = 1.5; ctx.stroke();
    }
    if(n > 1){
      ctx.fillStyle = "#fff";
      ctx.font = "800 10px system-ui, sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(String(n), g.x, g.y + 0.5);
      ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
    }
    S.mapPins.push({x: g.x, y: g.y, r: r + 4, d: worst, items: g.items});
  });

  // -- scale bar, from the same numbers -----------------------------------
  const mPerPx = 111320 / s;                    // degrees of latitude -> metres
  let barM = [10, 20, 50, 100, 200, 500, 1000, 2000].find(v => v / mPerPx > 52) || 2000;
  const barPx = barM / mPerPx;
  const bx = w - pad - barPx, by = h - 16;
  ctx.strokeStyle = PAL.ink3; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(bx, by); ctx.lineTo(bx + barPx, by);
  ctx.moveTo(bx, by - 4); ctx.lineTo(bx, by + 4);
  ctx.moveTo(bx + barPx, by - 4); ctx.lineTo(bx + barPx, by + 4);
  ctx.stroke();
  ctx.fillStyle = PAL.ink3; ctx.font = "10.5px ui-monospace, monospace";
  ctx.fillText(barM >= 1000 ? (barM/1000) + " km" : barM + " m", bx, by - 8);
  // north arrow
  ctx.strokeStyle = PAL.ink3; ctx.beginPath();
  ctx.moveTo(pad - 8, 30); ctx.lineTo(pad - 8, 12); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(pad - 12, 17); ctx.lineTo(pad - 8, 11);
  ctx.lineTo(pad - 4, 17); ctx.fillStyle = PAL.ink3; ctx.fill();
  ctx.fillText("N", pad - 11, 42);

  const counts = {severe:0, moderate:0, minor:0, other:0};
  pins.forEach(d => counts[d.severity in counts ? d.severity : "other"]++);
  $(legend).innerHTML =
    Object.entries(SEV).filter(([k]) => k !== "info").map(([k, v]) =>
      `<span><i style="background:${v.c}"></i>${v.ico} ${v.word.toLowerCase()} ${counts[k]||0}</span>`).join("")
    + `<span class="muted">${pos.geotagged || 0} of ${(pos.geotagged||0)+(pos.no_fix||0)} events carry a position</span>`
    + `<span class="grow"></span>`
    + `<a class="btn ghost small" href="/api/export/defects.geojson" target="_blank">GeoJSON</a>`;
  if(big){
    $("#mapBigNote").textContent =
      (pos.accuracy_note || "") + " Only a real fix geo-tags a defect; a pin with a grey ring " +
      "came from a position that was already stale, and is drawn for context rather than dispatch.";
  }
}

let tipEl = null;
function mapHover(e, cv){
  const r = cv.getBoundingClientRect();
  const mx = e.clientX - r.left, my = e.clientY - r.top;
  const hit = S.mapPins.find(p => Math.hypot(p.x - mx, p.y - my) <= p.r + 3);
  if(!hit) return hideTip();
  const d = hit.d, sev = SEV[d.severity] || SEV.info;
  const many = (hit.items || []).length > 1;
  if(!tipEl){ tipEl = document.createElement("div"); tipEl.className = "maptip"; document.body.appendChild(tipEl); }
  tipEl.innerHTML = `<b><span class="sev-ico sev-${d.severity||"info"}">${sev.ico}</span>
    ${prettyLabel(d.label)}</b> ${d.severity ? sev.word : ""}
    ${many ? `<div class="m">${hit.items.length} events within about 15 m — worst shown</div>` : ""}
    <div class="m">${d.camera_name} · ${d.mounted} · t+${d.t.toFixed(1)}s · conf ${d.confidence}</div>
    <div class="m">${d.lat.toFixed(6)}, ${d.lon.toFixed(6)} · ${d.gps_quality === "interp" ? "stale fix" : "real fix"}</div>
    <div class="m">${d.event_id}</div>`;
  tipEl.style.left = (e.clientX + 14) + "px";
  tipEl.style.top = (e.clientY + 14) + "px";
  tipEl.style.display = "block";
}
function hideTip(){ if(tipEl) tipEl.style.display = "none"; }

function roundRect(ctx, x, y, w, h, r){
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/* ── limits & capabilities ──────────────────────────────────────────── */
function renderLimits(){
  const groups = {};
  S.vocab.capabilities.forEach(c => (groups[c.group] = groups[c.group] || []).push(c));
  $("#capTable").innerHTML = `<table class="captable">` + Object.entries(groups).map(([g, rows]) =>
    `<tr><td class="capgroup" colspan="3">${g}</td></tr>` + rows.map(r =>
      `<tr><td class="state"><span class="state-${r.state}">${r.state}</span></td>
       <td style="width:230px"><b>${r.name}</b></td><td class="muted">${r.note}</td></tr>`).join("")
  ).join("") + `</table>`;
  const m = S.vocab.metrics;
  $("#metrics").innerHTML = `<p class="lead">
    ${m.split}, ${m.n_images} images — mAP50 <b>${m.mAP50}</b>, mAP50-95 <b>${m.mAP50_95}</b>,
    precision <b>${m.precision}</b>, recall <b>${m.recall}</b>.<br>
    ${Object.entries(m.per_class).map(([k, v]) => `${k}: <b>${v}</b>`).join(" · ")}<br>
    <span class="muted">${m.note}</span></p>` +
    (S.vocab.metrics_extra || []).map(x => `<p class="lead">
      <b>${x.name}</b> — ${x.split}, ${x.n_images} images: mAP50 <b>${x.mAP50}</b>,
      mAP50-95 <b>${x.mAP50_95}</b>, precision <b>${x.precision}</b>, recall <b>${x.recall}</b>.
      ${x.ocr_text_rate === undefined ? "" :
        `<br>OCR readability on ground-truth crops: text returned
         <b>${(x.ocr_text_rate*100).toFixed(0)}%</b>, plate-shaped
         <b>${(x.ocr_plausible_rate*100).toFixed(0)}%</b> — not character accuracy.`}
      <br><span class="muted">${x.note}</span></p>`).join("");
  $("#limitList").innerHTML = S.vocab.limits.map(l => `<li>${l}</li>`).join("");
  $("#attribution").textContent = S.vocab.attribution;
}

/* ── misc ───────────────────────────────────────────────────────────── */
function tickClock(){
  const d = new Date();
  const up = S.running ? ` · up ${Math.floor((Date.now() - S.t0)/1000)}s` : "";
  $("#clock").textContent = d.toLocaleTimeString() + up;
}
let toastT;
function toast(msg){
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.add("hidden"), 3600);
}

boot();
})();
