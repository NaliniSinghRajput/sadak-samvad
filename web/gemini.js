// Thin client for the Gemini API (Google AI Studio / generativelanguage.googleapis.com).
// The key is a browser key restricted to this site's domain (HTTP-referrer restriction)
// and to the Generative Language API only. A visitor may also supply their own key.
const Gemini = (() => {
  const cfg = window.SS_CONFIG || {};
  const LS = 'ss_gemini_key';
  let model = null;
  const key = () => { try { return localStorage.getItem(LS) || cfg.GEMINI_API_KEY || ''; } catch { return cfg.GEMINI_API_KEY || ''; } };
  const setKey = k => { try { localStorage.setItem(LS, k); } catch {} model = null; };
  const models = () => cfg.GEMINI_MODELS || ['gemini-2.5-flash', 'gemini-flash-latest'];

  async function call(model, body) {
    const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'x-goog-api-key': key() }, body: JSON.stringify(body)
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(j.error?.message || ('HTTP ' + r.status)); e.status = r.status; throw e; }
    const txt = (j.candidates?.[0]?.content?.parts || []).map(p => p.text || '').join('');
    return { txt, model };
  }

  // parts: array of {text} | {inline_data:{mime_type,data}}; opts: {system, json:true, schema, temperature}
  async function generate(parts, opts = {}) {
    if (!key()) throw new Error('No Gemini API key configured');
    const body = {
      contents: [{ role: 'user', parts }],
      generationConfig: { temperature: opts.temperature ?? 0.3 }
    };
    if (opts.system) body.systemInstruction = { parts: [{ text: opts.system }] };
    if (opts.json) { body.generationConfig.responseMimeType = 'application/json'; if (opts.schema) body.generationConfig.responseSchema = opts.schema; }
    const list = model ? [model, ...models().filter(m => m !== model)] : models();
    let last;
    for (let round = 0; round < 3; round++) {
    if (round) await new Promise(r => setTimeout(r, 1500 * round));
    for (const m of list) {
      try { const out = await call(m, body); model = m; return opts.json ? { ...JSON.parse(out.txt), _model: m } : { text: out.txt, _model: m }; }
      catch (e) {
        last = e;
        const modelProblem = [404, 429, 500, 503].includes(e.status) || (e.status === 400 && /model/i.test(e.message) && !/key/i.test(e.message));
        if (!modelProblem) throw e;   // wrong key etc. -> surface it; busy/missing model -> try the next one
        if (model === m) model = null;
      }
    }
    }
    throw last;
  }

  async function ping() { const r = await generate([{ text: 'Reply with the single word OK.' }], { temperature: 0 }); return r._model; }
  return { generate, ping, setKey, hasKey: () => !!key(), current: () => model };
})();

async function fileToB64(file) {
  const buf = await file.arrayBuffer(); let s = ''; const b = new Uint8Array(buf);
  for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
  return btoa(s);
}
async function blobToB64(blob) { return fileToB64(blob); }
async function urlToB64(url) { const r = await fetch(url); return blobToB64(await r.blob()); }
