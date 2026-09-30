// Deployment configuration. GEMINI_API_KEY here must be a browser key restricted to this
// site's domain (HTTP referrer) and to the Generative Language API. Never commit a real key:
// config.js is git-ignored; config.example.js is the template.
window.SS_CONFIG = {
  GEMINI_API_KEY: "",
  // tried in order; the first model that exists for this key is used
  GEMINI_MODELS: ["gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.8-flash", "gemini-flash-latest"],
  // Firebase Realtime Database URL for shared live citizen reports (optional)
  FIREBASE_DB_URL: ""
};
