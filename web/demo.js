/* Guided demo tour — open the site with ?tour=1. Drives the REAL app (real Gemini calls),
   narrates with the browser's speech engine and shows captions, so the walkthrough video
   is a straight screen recording of the live prototype. */
(() => {
  if (!/[?&]tour=1/.test(location.search)) return;
  const q = s => document.querySelector(s);
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const cap = document.createElement('div');
  cap.style.cssText = 'position:fixed;left:50%;bottom:26px;transform:translateX(-50%);max-width:1100px;width:92%;background:rgba(8,12,20,.92);color:#fff;font:600 21px/1.4 "Segoe UI",sans-serif;padding:14px 22px;border-radius:12px;border:1px solid #f5a524;z-index:99999;text-align:center;box-shadow:0 8px 30px #000a';
  const card = document.createElement('div');
  card.style.cssText = 'position:fixed;inset:0;background:#0d1420;z-index:99998;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#e8eef7;font-family:"Segoe UI",sans-serif;text-align:center;padding:40px';
  let voice = null;
  const pickVoice = () => {
    const vs = speechSynthesis.getVoices();
    voice = vs.find(v => /Neerja|Prabhat/i.test(v.name)) || vs.find(v => /en-IN/i.test(v.lang) && /Google|Natural/i.test(v.name)) || vs.find(v => /en-IN/i.test(v.lang)) || vs.find(v => /Google UK English Female/i.test(v.name)) || vs.find(v => /^en/i.test(v.lang));
  };
  speechSynthesis.onvoiceschanged = pickVoice; pickVoice();
  function say(text, lang) {
    cap.textContent = text; if (!cap.isConnected) document.body.appendChild(cap);
    return new Promise(res => {
      const u = new SpeechSynthesisUtterance(text);
      if (lang) { u.lang = lang; const v = speechSynthesis.getVoices().find(v => v.lang.replace('_', '-').startsWith(lang)); if (v) u.voice = v; }
      else if (voice) u.voice = voice;
      u.rate = 1.02; let done = false; const fin = () => { if (!done) { done = true; res(); } };
      u.onend = fin; u.onerror = fin; setTimeout(fin, 900 + text.length * 85);
      speechSynthesis.speak(u);
    });
  }
  const tab = t => q(`#tabs [data-tab="${t}"]`).click();
  const waitFor = async (fn, ms = 30000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (fn()) return true; await sleep(300); } return false; };
  async function typeInto(el, text) { el.value = ''; for (const ch of text) { el.value += ch; await sleep(18); } }
  const scrollTo = (sel, block = 'start') => q(sel)?.scrollIntoView({ behavior: 'smooth', block });
  async function showCard(html, text) { card.innerHTML = html; document.body.appendChild(card); await say(text); }

  async function tour() {
    await sleep(1200);
    await showCard(`<div style="font-size:22px;color:#91a3bb">Build with AI: Code for Communities · Track 1 — AI for Digital Public Infrastructure & Governance</div>
      <div style="font-size:84px;font-weight:700;margin:20px 0">Sadak Samvad <span style="color:#f5a524">सड़क संवाद</span></div>
      <div style="font-size:30px">Citizen voice + bus-camera evidence → <b style="color:#2bb3a3">Gemini</b> → a ranked, funded public-works list for every state</div>
      <div style="font-size:20px;color:#91a3bb;margin-top:30px">Team The Road Runners</div>`,
      'Sadak Samvad. A multilingual AI platform, built as a digital public good, that turns citizen development requests and automatic road evidence from city buses into a ranked, funded list of public works, for every state in India.');
    await say('Today, road and civic complaints sit in fragmented portals, in twenty two languages, and are never compared with where public money is actually being sanctioned. Let us see how Gemini fixes that, live.');
    card.remove();

    // 1. Hindi text report
    tab('citizen'); await sleep(600);
    q('#cDistrict').value = 0; q('#cDistrict').onchange(); q('#cChannel').value = 'whatsapp';
    await say('A citizen in Varanasi writes on WhatsApp, in Hindi, about potholes near the B H U gate.');
    await typeInto(q('#cText'), 'लंका चौराहे से BHU गेट तक सड़क में बहुत गड्ढे हैं, कल रात एक ई-रिक्शा पलट गया। स्कूल बसें भी यहीं से जाती हैं।');
    q('#cSubmit').click();
    await say('Gemini reads it, detects the language, translates it, classifies it, grades the severity, flags that school children are affected, and routes it to the right department.');
    await waitFor(() => q('#say'));
    await sleep(800);
    await say('And it replies to the citizen in Hindi.');
    const reply = q('.reply')?.innerText.replace('🔊', '').replace('💬', '').trim();
    if (reply) await say(reply, 'hi-IN');

    // 2. Odia + photo
    q('#cDistrict').value = 1; q('#cDistrict').onchange(); q('#cChannel').value = 'web';
    await say('Now Bhubaneswar, in Odia, with a photograph.');
    await typeInto(q('#cText'), 'ରସୁଲଗଡ଼ ଛକ ପାଖରେ ରାସ୍ତା ଭାଙ୍ଗିଯାଇଛି, ବର୍ଷାରେ ପାଣି ଜମି ରହୁଛି ଓ ବାଇକ୍ ଖସିପଡୁଛି।');
    try {
      const b = await (await fetch('assets/frames/road_65_c1.jpg')).blob();
      const dt = new DataTransfer(); dt.items.add(new File([b], 'road.jpg', { type: 'image/jpeg' }));
      q('#cPhoto').files = dt.files; q('#cPhoto').dispatchEvent(new Event('change'));
    } catch {}
    await sleep(500); q('#cSubmit').click();
    await say('Gemini is multimodal: it reads the Odia text and inspects the photo together, in a single call, and returns a structured ticket.');
    await waitFor(() => q('#say') && /Odia|ଓଡ଼ିଆ/i.test(q('#cResult').innerText), 30000);
    await sleep(1500);
    await say('Every ticket is written to Firebase in real time, and appears on every official\'s map. Voice notes work the same way, through the microphone button, or an IVR call.');

    // 3. Dashboard
    tab('dash'); await sleep(1500);
    await say('This is the governance dashboard. Citizen requests in orange, in thirteen languages across thirteen states. Bus camera evidence in green. Sanctioned public works, from the investment plan, in blue.');
    await say('Requests and evidence are fused into hotspots, and ranked with a transparent score: citizen demand, severity, camera confirmation, vulnerable users, and district population from the census.');
    q('#rank .hot')?.click(); await sleep(2500);
    await say('The top hotspot in Varanasi is confirmed by both citizens and our bus cameras. Each hotspot also shows whether money is already sanctioned there.');
    scrollTo('#kpis'); await sleep(600);
    await say('And here is the number the track asks for: only a small share of the planned spend lands on the top twenty citizen hotspots, and many of them have no sanctioned work at all. That is misaligned public spending, made visible.');
    q('#fState').value = 'Odisha'; q('#fState').onchange(); await sleep(1500);
    await say('Any state can filter to its own districts. Nothing in the code is specific to one city.');
    q('#fState').value = ''; q('#fState').onchange();

    // 4. Evidence
    tab('evidence'); await sleep(800);
    const v = q('#busVid'); v.currentTime = 0; v.play();
    await say('Where does the camera evidence come from? Our edge node runs on a city bus. Four cameras, one detector on a laptop class box, number plates blurred on the device, and only events, not video, sent to the cloud. This is a real drive in Bhubaneswar.');
    await sleep(3000);
    scrollTo('#gallery', 'center'); await sleep(800);
    const btns = [...document.querySelectorAll('#gallery button')];
    btns[2]?.click(); btns[4]?.click();
    await say('Gemini vision gives a second opinion on each crop: it confirms or overrules the edge model, and writes the work order line an engineer can act on.');
    await waitFor(() => document.getElementById('v4')?.innerText.length > 5, 25000);
    await sleep(2500);

    // 5. Brief
    tab('brief'); await sleep(600);
    q('#bLang').value = 'Hindi';
    await say('Finally, decision support. Gemini writes a one page policy brief from the ranked hotspots, in the language the official reads. Here, in Hindi, for the whole country.');
    q('#bGo').click();
    await waitFor(() => q('#bOut table') || q('#bOut h1') || q('#bOut h2'), 45000);
    await sleep(2500); q('#bOut').scrollIntoView({ behavior: 'smooth', block: 'start' });
    await sleep(3500);
    window.scrollTo({ top: 0, behavior: 'smooth' });
    q('#qIn').value = 'Where do citizen complaints and bus-camera evidence agree?'; q('#qGo').click();
    await say('Officials can also simply ask questions. Answers are grounded only in the platform\'s own data.');
    await waitFor(() => !q('#qOut').innerText.includes('thinking'), 30000);
    await sleep(3500);

    await showCard(`<div style="font-size:54px;font-weight:700">Deployable in weeks · scalable to every state</div>
      <div style="font-size:26px;margin-top:24px;line-height:1.6;max-width:1100px">Gemini multimodal intake in 22 languages · Gemini vision verification · Gemini policy briefs<br>Firebase Hosting + Realtime Database · open source, open schemas · privacy by design<br>Scale path: Cloud Run · WhatsApp Business API · BigQuery · Vertex AI forecasting</div>
      <div style="font-size:34px;color:#f5a524;margin-top:34px">सड़क संवाद — every voice counted, every rupee aligned.</div>`,
      'Sadak Samvad is open source, runs on Firebase and Gemini, and can be piloted with one city and one bus depot in weeks, then extended state by state, and to other BRICS cities with their own languages. Every voice counted. Every rupee aligned. Thank you.');
    cap.remove();
  }
  const go = document.createElement('button');
  go.textContent = '▶ Start guided demo'; go.className = 'btn';
  go.style.cssText = 'position:fixed;right:20px;bottom:20px;z-index:99999';
  go.onclick = () => { go.remove(); tour(); };
  document.body.appendChild(go);
})();
