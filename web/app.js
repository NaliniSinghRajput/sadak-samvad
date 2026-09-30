/* Sadak Samvad — client app. No framework; Leaflet for maps, Gemini for understanding,
   Firebase Realtime Database (REST) for shared live citizen reports when configured. */
const CFG = window.SS_CONFIG || {};
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const LANGS = { as: 'Assamese', bn: 'Bengali', brx: 'Bodo', doi: 'Dogri', gu: 'Gujarati', hi: 'Hindi', kn: 'Kannada', ks: 'Kashmiri', kok: 'Konkani', mai: 'Maithili', ml: 'Malayalam', mni: 'Manipuri', mr: 'Marathi', ne: 'Nepali', or: 'Odia', pa: 'Punjabi', sa: 'Sanskrit', sat: 'Santali', sd: 'Sindhi', ta: 'Tamil', te: 'Telugu', ur: 'Urdu', en: 'English' };
const CATS = { pothole: 'Pothole / road surface', waterlogging: 'Waterlogging', drainage: 'Drainage / sewer', streetlight: 'Streetlight', footpath: 'Footpath / pedestrian safety', new_road: 'New / missing road access', traffic_signal: 'Traffic signal / junction', bridge_culvert: 'Bridge / culvert', garbage: 'Garbage / sanitation', other: 'Other' };
const DEPT = { pothole: 'PWD / Municipal roads', waterlogging: 'Municipal drainage', drainage: 'Jal Nigam / Municipal drainage', streetlight: 'Municipal electrical', footpath: 'Municipal roads & encroachment', new_road: 'PWD / PMGSY / Urban development', traffic_signal: 'Traffic police & Smart City SPV', bridge_culvert: 'PWD bridges', garbage: 'Municipal sanitation', other: 'Municipal commissioner office' };

const S = { seed: null, ev: null, reqs: [], live: [], hot: [], loc: null, audio: null, photo: null };

/* ---------- tabs ---------- */
$$('#tabs button').forEach(b => b.onclick = () => {
  $$('#tabs button').forEach(x => x.classList.toggle('on', x === b));
  $$('.tab').forEach(t => t.classList.toggle('on', t.id === b.dataset.tab));
  if (b.dataset.tab === 'dash') setTimeout(() => { bigMap.invalidateSize(); }, 50);
  if (b.dataset.tab === 'citizen') setTimeout(() => miniMap.invalidateSize(), 50);
  history.replaceState(null, '', '#' + b.dataset.tab);
});

/* ---------- Gemini status ---------- */
async function checkAI() {
  const p = $('#aiStatus');
  if (!Gemini.hasKey()) { p.textContent = 'Gemini: add key'; p.className = 'pill bad'; return; }
  p.innerHTML = '<span class="spin"></span>Gemini…';
  try { const m = await Gemini.ping(); p.textContent = 'Gemini live · ' + m; p.className = 'pill ok'; }
  catch (e) { p.textContent = 'Gemini: ' + e.message.slice(0, 40); p.className = 'pill bad'; p.title = e.message; }
}
$('#aiStatus').onclick = () => $('#keyDlg').showModal();
$('#keySave').onclick = () => { const k = $('#keyIn').value.trim(); if (k) { Gemini.setKey(k); checkAI(); } };

/* ---------- maps ---------- */
const tiles = () => L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap contributors' });
const bigMap = L.map('map', { preferCanvas: true }).setView([22.5, 81], 5); tiles().addTo(bigMap);
const miniMap = L.map('cMap').setView([25.3176, 82.9739], 11); tiles().addTo(miniMap);
const layers = { wk: L.layerGroup().addTo(bigMap), cit: L.layerGroup().addTo(bigMap), cam: L.layerGroup().addTo(bigMap), hot: L.layerGroup().addTo(bigMap) };
let miniPin = null;
miniMap.on('click', e => setLoc(e.latlng.lat, e.latlng.lng, 'picked on map'));
function setLoc(lat, lon, how) {
  S.loc = { lat, lon }; if (miniPin) miniPin.remove();
  miniPin = L.marker([lat, lon]).addTo(miniMap); miniMap.setView([lat, lon], 14);
  $('#cLocTxt').textContent = `Location: ${lat.toFixed(5)}, ${lon.toFixed(5)} (${how})`;
}

/* ---------- data ---------- */
async function loadData() {
  [S.seed, S.ev] = await Promise.all([fetch('data/citizen_seed.json').then(r => r.json()), fetch('data/evidence.json').then(r => r.json())]);
  S.reqs = S.seed.requests.slice();
  await loadLive();
  initSelectors(); renderAll(); renderEvidence();
}
async function loadLive() {
  if (!CFG.FIREBASE_DB_URL) return;
  try {
    const j = await fetch(CFG.FIREBASE_DB_URL.replace(/\/$/, '') + '/sadak/reports.json?orderBy="$key"&limitToLast=300').then(r => r.json());
    S.live = Object.values(j || {}).filter(r => r && r.lat && r.category);
    S.reqs = S.seed.requests.concat(S.live);
  } catch (e) { console.warn('live db', e); }
}
async function pushLive(r) {
  if (!CFG.FIREBASE_DB_URL) return false;
  try { const res = await fetch(CFG.FIREBASE_DB_URL.replace(/\/$/, '') + '/sadak/reports.json', { method: 'POST', body: JSON.stringify(r) }); return res.ok; }
  catch { return false; }
}

function initSelectors() {
  const ds = S.seed.districts;
  $('#cDistrict').innerHTML = ds.map((d, i) => `<option value="${i}">${esc(d.name)} — ${esc(d.state)}</option>`).join('');
  $('#cDistrict').onchange = () => { const d = ds[+$('#cDistrict').value]; S.loc = null; if (miniPin) miniPin.remove(); miniMap.setView([d.lat, d.lon], 11); $('#cLocTxt').textContent = 'Location: district centre (tap the map or use your location to refine)'; renderRecent(); };
  const states = [...new Set(ds.map(d => d.state))].sort();
  $('#fState').innerHTML += states.map(s => `<option>${esc(s)}</option>`).join('');
  $('#bScope').innerHTML += states.map(s => `<option value="state:${esc(s)}">${esc(s)} (state)</option>`).join('') + ds.map(d => `<option value="district:${esc(d.name)}">${esc(d.name)} (district)</option>`).join('');
  $('#fCat').innerHTML += Object.entries(CATS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('');
  $('#bLang').innerHTML = Object.entries(LANGS).map(([k, v]) => `<option value="${v}" ${k === 'en' ? 'selected' : ''}>${v}</option>`).join('');
  ['#fState', '#fCat'].forEach(s => $(s).onchange = renderAll);
  $('#lCit').onchange = e => e.target.checked ? layers.cit.addTo(bigMap) : layers.cit.remove();
  $('#lCam').onchange = e => e.target.checked ? layers.cam.addTo(bigMap) : layers.cam.remove();
  $('#lWk').onchange = e => e.target.checked ? layers.wk.addTo(bigMap) : layers.wk.remove();
  $('#lHot').onchange = e => e.target.checked ? layers.hot.addTo(bigMap) : layers.hot.remove();
}

/* ---------- hotspot / priority engine ---------- */
const CELL = 0.012; // ~1.3 km grid
function districtOf(lat, lon) { let b = null, bd = 1e9; for (const d of S.seed.districts) { const q = (d.lat - lat) ** 2 + (d.lon - lon) ** 2; if (q < bd) { bd = q; b = d; } } return b; }
function computeHotspots(reqs, ev) {
  const cells = new Map();
  const key = (lat, lon) => Math.round(lat / CELL) + ':' + Math.round(lon / CELL);
  const get = (lat, lon) => { const k = key(lat, lon); if (!cells.has(k)) cells.set(k, { k, lat: 0, lon: 0, n: 0, reqs: [], cam: [], cats: {}, langs: new Set() }); return cells.get(k); };
  for (const r of reqs) { const c = get(r.lat, r.lon); c.reqs.push(r); c.lat += r.lat; c.lon += r.lon; c.n++; c.cats[r.category] = (c.cats[r.category] || 0) + 1; c.langs.add(r.lang); }
  for (const e of ev) { if (e.label !== 'pothole') continue; const c = get(e.lat, e.lon); c.cam.push(e); if (!c.n) { c.lat += e.lat; c.lon += e.lon; c.n0 = 1; } }
  const out = [];
  for (const c of cells.values()) {
    const m = c.n || 1; c.lat /= m; c.lon /= m;
    if (!c.reqs.length && c.cam.length < 3) continue;
    const d = districtOf(c.lat, c.lon);
    const sev = c.reqs.length ? c.reqs.reduce((a, r) => a + r.severity, 0) / c.reqs.length : (c.cam.some(e => e.severity === 'severe') ? 4 : 3);
    const vul = c.reqs.length ? c.reqs.filter(r => r.vulnerable).length / c.reqs.length : 0.3;
    const top = Object.entries(c.cats).sort((a, b) => b[1] - a[1])[0]?.[0] || 'pothole';
    const comp = {
      demand: 0.35 * Math.min(1, c.reqs.length / 8),
      severity: 0.25 * (sev / 5),
      camera: 0.20 * Math.min(1, c.cam.length / 5),
      vulnerable: 0.10 * vul,
      population: 0.10 * Math.min(1, (d?.pop2011 || 0) / 1e7)
    };
    const score = Object.values(comp).reduce((a, b) => a + b, 0);
    const near = (S.seed.works || []).filter(w => Math.hypot(w.lat - c.lat, (w.lon - c.lon) * Math.cos(c.lat * Math.PI / 180)) < 0.0135);
    out.push({ ...c, works: near, funded: near.reduce((a, w) => a + w.cost_lakh, 0), district: d?.name, state: d?.state, pop: d?.pop2011, sev, top, comp, score: Math.round(score * 100), langs: [...c.langs] });
  }
  return out.sort((a, b) => b.score - a.score);
}

/* ---------- render dashboard ---------- */
const COMPCOL = { demand: '#f5a524', severity: '#e5534b', camera: '#2bb3a3', vulnerable: '#a371f7', population: '#58a6ff' };
function filtered() {
  const st = $('#fState').value, ct = $('#fCat').value;
  return { reqs: S.reqs.filter(r => (!st || r.state === st) && (!ct || r.category === ct)), ev: S.ev.events.filter(e => (!st || e.state === st) && (!ct || ct === 'pothole')) };
}
function renderAll() {
  const { reqs, ev } = filtered();
  S.hot = computeHotspots(reqs, ev);
  layers.cit.clearLayers(); layers.cam.clearLayers(); layers.hot.clearLayers();
  for (const r of reqs) L.circleMarker([r.lat, r.lon], { radius: 5, color: '#f5a524', weight: 1, fillOpacity: .75 })
    .bindPopup(`<b>${esc(CATS[r.category] || r.category)}</b> · sev ${r.severity}<br>${esc(r.text)}<br><i>${esc(r.summary_en)}</i><br><small>${esc(LANGS[r.lang] || r.lang)} · ${esc(r.channel)} · ${r.synthetic ? 'sample (synthetic)' : 'LIVE citizen report'}</small>`).addTo(layers.cit);
  for (const e of ev) L.circleMarker([e.lat, e.lon], { radius: 4, color: '#2bb3a3', weight: 1, fillOpacity: .9 })
    .bindPopup(`<b>Bus camera: ${esc(e.label.replace(/_/g, ' '))}</b> (${esc(e.severity || '')}, conf ${e.conf})<br>${esc(e.drive)}<br><small>position: ${esc(e.pos)}</small>`).addTo(layers.cam);
  layers.wk.clearLayers();
  const st0 = $('#fState').value, wks = (S.seed.works || []).filter(w => !st0 || w.state === st0);
  for (const w of wks) L.marker([w.lat, w.lon], { icon: L.divIcon({ className: '', html: '<div style="width:11px;height:11px;background:#58a6ff;border:1px solid #fff"></div>' }) })
    .bindPopup(`<b>Sanctioned work ${w.id}</b> (sample)<br>${esc(w.scheme)} · ${esc(CATS[w.category])}<br>₹${w.cost_lakh} lakh · ${esc(w.status)}`).addTo(layers.wk);
  const top20 = S.hot.slice(0, 20), inTop = new Set(top20.flatMap(h => h.works.map(w => w.id)));
  const tot = wks.reduce((a, w) => a + w.cost_lakh, 0), aligned = wks.filter(w => inTop.has(w.id)).reduce((a, w) => a + w.cost_lakh, 0);
  S.align = { total_lakh: tot, aligned_lakh: aligned, pct: tot ? Math.round(100 * aligned / tot) : 0, unfunded_top20: top20.filter(h => !h.works.length).length };
  S.hot.slice(0, 40).forEach((h, i) => L.circle([h.lat, h.lon], { radius: 500 + h.score * 12, color: h.score > 60 ? '#e5534b' : '#f5a524', weight: 2, fillOpacity: .12 })
    .bindPopup(`<b>#${i + 1} · score ${h.score}</b><br>${esc(h.district)}, ${esc(h.state)}<br>${h.reqs.length} citizen requests, ${h.cam.length} camera detections`).addTo(layers.hot));
  const langs = new Set(reqs.map(r => r.lang)), states = new Set(reqs.map(r => r.state));
  const conf = S.hot.filter(h => h.cam.length && h.reqs.length).length;
  $('#kpis').innerHTML = [
    [reqs.length, 'citizen requests' + (S.live.length ? ` (${S.live.length} live)` : '')], [langs.size, 'languages understood'], [states.size, 'states'],
    [ev.length, 'bus-camera events'], [S.hot.length, 'hotspots'], [conf, 'citizen + camera confirmed'],
    [S.align.pct + '%', `of ₹${(S.align.total_lakh / 100).toFixed(1)} cr planned spend lands on top-20 hotspots`], [S.align.unfunded_top20, 'top-20 hotspots with NO sanctioned work']
  ].map(([b, s]) => `<div class="kpi"><b>${b}</b><span>${s}</span></div>`).join('');
  $('#rank').innerHTML = S.hot.slice(0, 25).map((h, i) => `<div class="hot" data-i="${i}">
    <div class="t"><b>#${i + 1} ${esc(h.district)}</b><span class="sev ${h.score > 60 ? 's5' : h.score > 45 ? 's4' : 's3'}">${h.score}</span></div>
    <div class="small muted">${esc(h.state)} · ${esc(CATS[h.top])} · ${h.reqs.length} requests · ${h.cam.length} camera hits · ${h.langs.map(l => LANGS[l] || l).join(', ') || '—'}</div>
    <div class="bar">${Object.entries(h.comp).map(([k, v]) => `<i title="${k} ${(v * 100).toFixed(0)}" style="width:${v * 100}%;background:${COMPCOL[k]}"></i>`).join('')}</div>
    <div class="small muted">→ ${esc(DEPT[h.top])} · ${h.works.length ? `<span class="tag" style="background:#1f3b5c">funded ₹${h.funded}L (${esc(h.works[0].scheme)})</span>` : '<span class="tag" style="background:#6b2b28">UNFUNDED GAP</span>'}</div></div>`).join('') +
    `<div class="small muted">${Object.entries(COMPCOL).map(([k, c]) => `<span class="tag" style="background:${c};color:#111">${k}</span>`).join('')}</div>`;
  $$('#rank .hot').forEach(el => el.onclick = () => { const h = S.hot[+el.dataset.i]; bigMap.setView([h.lat, h.lon], 14); });
  renderRecent();
}
function renderRecent() {
  if (!S.seed) return;
  const d = S.seed.districts[+$('#cDistrict').value || 0];
  const rs = S.reqs.filter(r => r.district === d.name).slice(-8).reverse();
  $('#cRecent').innerHTML = rs.map(r => `<li><span class="sev s${r.severity}">${r.severity}</span> <b>${esc(CATS[r.category] || r.category)}</b> — ${esc(r.summary_en)} <span class="tag">${esc(LANGS[r.lang] || r.lang)}</span>${r.synthetic ? '' : '<span class="tag" style="background:#2a5b35">live</span>'}</li>`).join('') || '<li class="muted">No reports yet.</li>';
}

/* ---------- citizen intake ---------- */
let rec = null, chunks = [];
$('#cMic').onclick = async () => {
  if (rec && rec.state === 'recording') { rec.stop(); return; }
  try {
    const st = await navigator.mediaDevices.getUserMedia({ audio: true });
    rec = new MediaRecorder(st); chunks = [];
    rec.ondataavailable = e => chunks.push(e.data);
    rec.onstop = () => { st.getTracks().forEach(t => t.stop()); S.audio = new Blob(chunks, { type: rec.mimeType || 'audio/webm' }); $('#cMic').textContent = '🎙 Re-record'; $('#cMic').classList.remove('rec'); showMedia(); };
    rec.start(); $('#cMic').textContent = '⏹ Stop recording'; $('#cMic').classList.add('rec');
  } catch (e) { alertBox('Microphone not available: ' + e.message); }
};
$('#cPhoto').onchange = e => { S.photo = e.target.files[0] || null; showMedia(); };
$('#cLoc').onclick = () => navigator.geolocation?.getCurrentPosition(p => setLoc(p.coords.latitude, p.coords.longitude, 'device GPS'), e => alertBox('Location: ' + e.message));
function showMedia() {
  $('#cMedia').innerHTML = (S.photo ? `<img src="${URL.createObjectURL(S.photo)}">` : '') + (S.audio ? `<audio controls src="${URL.createObjectURL(S.audio)}"></audio>` : '');
}
function alertBox(m) { $('#cResult').className = 'result'; $('#cResult').innerHTML = `<span style="color:var(--bad)">${esc(m)}</span>`; }
const EXAMPLES = [
  { d: 0, t: 'लंका चौराहे से BHU गेट तक सड़क में बहुत गड्ढे हैं, कल रात एक ई-रिक्शा पलट गया। स्कूल बसें भी यहीं से जाती हैं।' },
  { d: 1, t: 'ରସୁଲଗଡ଼ ଛକ ପାଖରେ ରାସ୍ତା ଭାଙ୍ଗିଯାଇଛି, ବର୍ଷାରେ ପାଣି ଜମି ରହୁଛି ଓ ବାଇକ୍ ଖସିପଡୁଛି।' },
  { d: 5, t: 'வேளச்சேரி பிரதான சாலையில் மழைநீர் வடிகால் உடைந்து, சாலையில் பெரிய பள்ளம் ஏற்பட்டுள்ளது.' },
  { d: 4, t: 'আমাদের পাড়ার রাস্তায় কোনো আলো নেই, সন্ধ্যার পর মেয়েরা বাড়ি ফিরতে ভয় পায়।' }
];
let exI = 0;
$('#cExample').onclick = () => { const x = EXAMPLES[exI++ % EXAMPLES.length]; $('#cDistrict').value = x.d; $('#cDistrict').onchange(); $('#cText').value = x.t; };

const INTAKE_SCHEMA = {
  type: 'OBJECT', properties: {
    language_code: { type: 'STRING', description: 'ISO 639 code of the citizen language, e.g. hi, or, ta, bn, en' },
    transcript_original: { type: 'STRING', description: 'What the citizen said/wrote, in the original script (transcribe audio if present)' },
    translation_en: { type: 'STRING' },
    category: { type: 'STRING', enum: Object.keys(CATS) },
    severity: { type: 'INTEGER', description: '1 (minor) to 5 (danger to life / blocks access)' },
    summary_en: { type: 'STRING', description: 'One-line actionable summary for an official' },
    department: { type: 'STRING' },
    vulnerable_affected: { type: 'BOOLEAN', description: 'children, elderly, women safety, disabled, patients' },
    photo_findings: { type: 'STRING', description: 'What the photo shows; empty if no photo' },
    landmark: { type: 'STRING', description: 'Any place/landmark named, else empty' },
    reply_to_citizen: { type: 'STRING', description: 'Short, warm acknowledgement IN THE CITIZEN\'S LANGUAGE AND SCRIPT with the department it is routed to' },
    confidence: { type: 'NUMBER' }
  }, required: ['language_code', 'transcript_original', 'translation_en', 'category', 'severity', 'summary_en', 'department', 'vulnerable_affected', 'reply_to_citizen']
};
$('#cSubmit').onclick = async () => {
  const text = $('#cText').value.trim();
  if (!text && !S.audio && !S.photo) return alertBox('Type, speak or add a photo first.');
  const d = S.seed.districts[+$('#cDistrict').value];
  const btn = $('#cSubmit'); btn.disabled = true;
  $('#cResult').className = 'result'; $('#cResult').innerHTML = '<span class="spin"></span>Gemini is reading your report…';
  try {
    const parts = [{ text: `Citizen report from ${d.name}, ${d.state}, India. Local languages: ${d.langs.map(l => LANGS[l]).join(', ')}.\nDepartments to route to (choose best fit): ${JSON.stringify(DEPT)}\nTyped text: ${text || '(none)'}` }];
    if (S.audio) parts.push({ text: 'Voice note follows (transcribe it):' }, { inline_data: { mime_type: S.audio.type.split(';')[0] || 'audio/webm', data: await blobToB64(S.audio) } });
    if (S.photo) parts.push({ text: 'Photo follows (describe the defect you see):' }, { inline_data: { mime_type: S.photo.type || 'image/jpeg', data: await fileToB64(S.photo) } });
    const r = await Gemini.generate(parts, { json: true, schema: INTAKE_SCHEMA, system: 'You are the intake officer of an Indian public-grievance platform for road and civic infrastructure. Understand reports in any of the 22 scheduled Indian languages or English, from text, voice or photos. Be factual; never invent details not present in the input.' });
    const loc = S.loc || { lat: d.lat + (Math.random() - .5) * .02, lon: d.lon + (Math.random() - .5) * .02 };
    const rec = { id: 'LV-' + Date.now().toString(36), synthetic: false, district: d.name, state: d.state, lat: +loc.lat.toFixed(5), lon: +loc.lon.toFixed(5), lang: r.language_code?.slice(0, 3) || 'en', channel: $('#cChannel').value, text: r.transcript_original || text, category: CATS[r.category] ? r.category : 'other', severity: Math.max(1, Math.min(5, r.severity | 0)), summary_en: r.summary_en, vulnerable: !!r.vulnerable_affected, days_open: 0, ts: new Date().toISOString(), model: r._model };
    S.reqs.push(rec); S.live.push(rec);
    const saved = await pushLive(rec);
    renderAll();
    $('#cResult').innerHTML = `<div class="kv">
      <b>Language</b><span>${esc(LANGS[rec.lang] || r.language_code)}</span>
      <b>Heard / read</b><span>${esc(r.transcript_original)}</span>
      <b>English</b><span>${esc(r.translation_en)}</span>
      <b>Category</b><span><span class="tag">${esc(CATS[rec.category])}</span></span>
      <b>Severity</b><span><span class="sev s${rec.severity}">${rec.severity}</span> / 5 ${rec.vulnerable ? '<span class="tag">vulnerable users affected</span>' : ''}</span>
      <b>Routed to</b><span>${esc(r.department)}</span>
      <b>For the official</b><span>${esc(r.summary_en)}</span>
      ${r.photo_findings ? `<b>Photo</b><span>${esc(r.photo_findings)}</span>` : ''}
      ${r.landmark ? `<b>Landmark</b><span>${esc(r.landmark)}</span>` : ''}
      <b>Ticket</b><span>${rec.id} · ${saved ? 'saved to live database' : 'added to this session'} · ${esc(r._model)}</span></div>
      <div class="reply">💬 ${esc(r.reply_to_citizen)} <button class="btn ghost" id="say" style="padding:3px 9px">🔊</button></div>`;
    $('#say').onclick = () => { const u = new SpeechSynthesisUtterance(r.reply_to_citizen); u.lang = (r.language_code || 'hi') + '-IN'; speechSynthesis.speak(u); };
    S.audio = null; S.photo = null; showMedia(); $('#cMic').textContent = '🎙 Speak (record)';
  } catch (e) { alertBox('Gemini error: ' + e.message); }
  btn.disabled = false;
};

/* ---------- evidence ---------- */
function renderEvidence() {
  const ev = S.ev.events, st = S.ev.stats;
  const vns = ev.filter(e => e.city === 'Varanasi').length, bbs = ev.filter(e => e.city === 'Bhubaneswar' && e.label === 'pothole').length;
  $('#evKpis').innerHTML = [[st.bhubaneswar_events.toLocaleString(), 'detections, Bhubaneswar 4-cam drive'], [bbs, 'potholes flagged (Bhubaneswar)'], [vns, 'GPS-tagged potholes (Varanasi)'], [st.route_km + ' km', 'route driven'], ['4,308 : 1', 'video in : data out'], ['on-device', 'number-plate blurring']]
    .map(([b, s]) => `<div class="kpi"><b>${b}</b><span>${s}</span></div>`).join('');
  const items = ev.filter(e => e.thumb).slice(0, 24).map(e => ({ src: 'data:image/jpeg;base64,' + e.thumb, e }));
  ['road_65_c1', 'road_85_c1', 'road_45_c2', 'road_25_c1'].forEach(f => items.unshift({ src: `assets/frames/${f}.jpg`, e: { id: f, label: 'camera frame', severity: '', conf: '', camera: 'Bhubaneswar drive', city: 'Bhubaneswar' } }));
  $('#gallery').innerHTML = items.map((it, i) => `<div class="gi"><img src="${it.src}" alt=""><div><b>${esc(it.e.label.replace(/_/g, ' '))}</b> ${it.e.severity ? '· ' + esc(it.e.severity) : ''} ${it.e.conf ? '· YOLO ' + it.e.conf : ''}</div><div class="small muted">${esc(it.e.camera || '')} ${esc(it.e.id)}</div><button class="btn ghost" data-i="${i}" style="margin-top:6px;padding:4px 10px">✨ Gemini verify</button><div class="v small" id="v${i}"></div></div>`).join('');
  $$('#gallery button').forEach(b => b.onclick = () => verify(items[+b.dataset.i], +b.dataset.i, b));
}
const VERIFY_SCHEMA = { type: 'OBJECT', properties: { defect_present: { type: 'BOOLEAN' }, defect_type: { type: 'STRING' }, severity_1to5: { type: 'INTEGER' }, hazard_to: { type: 'STRING' }, work_order: { type: 'STRING', description: 'One line an engineer can act on' }, agrees_with_edge_model: { type: 'BOOLEAN' }, notes: { type: 'STRING' } }, required: ['defect_present', 'defect_type', 'severity_1to5', 'work_order', 'agrees_with_edge_model'] };
async function verify(it, i, btn) {
  btn.disabled = true; $('#v' + i).innerHTML = '<span class="spin"></span>asking Gemini…';
  try {
    const b64 = it.src.startsWith('data:') ? it.src.split(',')[1] : await urlToB64(it.src);
    const r = await Gemini.generate([{ text: `This image was captured by a bus-mounted road camera in ${it.e.city}, India. The on-bus edge model labelled it "${it.e.label}"${it.e.severity ? ' (' + it.e.severity + ')' : ''}. Boxes/labels drawn on the image are the edge model's. Independently inspect the ROAD SURFACE and infrastructure: is there a defect a municipal engineer should fix?` }, { inline_data: { mime_type: 'image/jpeg', data: b64 } }], { json: true, schema: VERIFY_SCHEMA });
    $('#v' + i).innerHTML = `${r.defect_present ? '✅' : '❌'} <b>${esc(r.defect_type)}</b> <span class="sev s${r.severity_1to5}">${r.severity_1to5}</span> ${r.agrees_with_edge_model ? '<span class="tag">agrees with edge</span>' : '<span class="tag" style="background:#6b2b28">overrules edge</span>'}<br>🛠 ${esc(r.work_order)}`;
  } catch (e) { $('#v' + i).innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
  btn.disabled = false;
}

/* ---------- brief & ask ---------- */
function aggregate(scope) {
  let hs = computeHotspots(S.reqs, S.ev.events);
  if (scope.startsWith('state:')) hs = hs.filter(h => h.state === scope.slice(6));
  if (scope.startsWith('district:')) hs = hs.filter(h => h.district === scope.slice(9));
  const byD = {};
  for (const r of S.reqs) { const k = r.district; byD[k] ??= { district: k, state: r.state, requests: 0, categories: {}, avg_sev: 0 }; byD[k].requests++; byD[k].categories[r.category] = (byD[k].categories[r.category] || 0) + 1; byD[k].avg_sev += r.severity; }
  Object.values(byD).forEach(d => d.avg_sev = +(d.avg_sev / d.requests).toFixed(2));
  return {
    note: 'Citizen requests in this demo are synthetic samples plus any live reports; camera evidence is real (Varanasi 29 Aug 2026, Bhubaneswar 19 Sep 2026). Populations: Census 2011.',
    planned_spend_alignment: S.align,
    sanctioned_works_sample: (S.seed.works || []).length,
    districts: S.seed.districts.map(d => ({ ...d, ...(byD[d.name] || {}) })),
    top_hotspots: hs.slice(0, 15).map((h, i) => ({ rank: i + 1, score: h.score, district: h.district, state: h.state, lat: +h.lat.toFixed(4), lon: +h.lon.toFixed(4), main_issue: h.top, citizen_requests: h.reqs.length, camera_detections: h.cam.length, avg_severity: +h.sev.toFixed(1), languages: h.langs, sample_summaries: h.reqs.slice(0, 3).map(r => r.summary_en), planned_works_nearby: h.works.map(w => `${w.scheme} ₹${w.cost_lakh}L ${w.status}`), score_components: Object.fromEntries(Object.entries(h.comp).map(([k, v]) => [k, +(v * 100).toFixed(1)])), department: DEPT[h.top] }))
  };
}
$('#bGo').onclick = async () => {
  const scope = $('#bScope').value, lang = $('#bLang').value, b = $('#bGo'); b.disabled = true;
  $('#bOut').className = 'md'; $('#bOut').innerHTML = '<span class="spin"></span>Gemini is drafting the brief…';
  try {
    const data = aggregate(scope);
    const r = await Gemini.generate([{ text: `Write a one-page policy brief in ${lang} for ${scope ? scope.split(':')[1] : 'the Ministry of Housing & Urban Affairs / MoRTH and all State Urban Development Departments'}.\nUse ONLY this data:\n${JSON.stringify(data)}\n\nStructure (markdown): Title; 3-line situation summary; table "Priority works" (rank, location, issue, why — cite requests/camera/severity, department, suggested timeline 7/30/90 days); "Patterns across states"; "Recommended decisions this week" (3-5 bullets); "How to scale" (1 short paragraph); a one-line data caveat. Be concrete and numeric. Do not invent numbers.` }], { temperature: 0.4, system: 'You are a senior public-infrastructure policy analyst in India.' });
    $('#bOut').innerHTML = marked.parse(r.text) + `<p class="small muted">Generated by ${esc(r._model)} from ${data.top_hotspots.length} hotspots.</p>`;
  } catch (e) { $('#bOut').innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
  b.disabled = false;
};
const QS = ['Which 3 districts need urgent monsoon drainage work, and why?', 'Where do citizen complaints and bus-camera evidence agree?', 'Summarise Odisha for the Chief Engineer in Odia', 'What should Varanasi Nagar Nigam fix first this week?'];
$('#qChips').innerHTML = QS.map(q => `<button>${esc(q)}</button>`).join('');
$$('#qChips button').forEach(b => b.onclick = () => { $('#qIn').value = b.textContent; $('#qGo').click(); });
$('#qGo').onclick = async () => {
  const q = $('#qIn').value.trim(); if (!q) return; const b = $('#qGo'); b.disabled = true;
  $('#qOut').className = 'md'; $('#qOut').innerHTML = '<span class="spin"></span>thinking…';
  try {
    const r = await Gemini.generate([{ text: `Data:\n${JSON.stringify(aggregate(''))}\n\nQuestion: ${q}` }], { system: 'Answer only from the data given; be concise, cite numbers and ranks; reply in the language the question asks for (default English). If the data cannot answer, say so.' });
    $('#qOut').innerHTML = marked.parse(r.text);
  } catch (e) { $('#qOut').innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
  b.disabled = false;
};

/* ---------- about ---------- */
fetch('about.md').then(r => r.text()).then(t => $('#aboutMd').innerHTML = marked.parse(t));

/* ---------- boot ---------- */
loadData().then(() => { const h = location.hash.slice(1); if (h) $(`#tabs [data-tab="${h}"]`)?.click(); });
checkAI();
