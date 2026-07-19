// Fetch Android Play top-free charts and record where the flagship Chinese AI apps
// appear. Writes app-charts.json, which update-china-data.py folds into the "apps"
// family of the China AI Adoption Index. Play has no key-free charts API, so this
// uses google-play-scraper (reverse-engineered web endpoint). iOS is handled by
// update-china-data.py directly via Apple's keyless RSS.
//
// Best-effort: a per-country failure is skipped; if EVERY country fails, we exit
// non-zero WITHOUT writing, so the Python side keeps the previous file / falls back.
//
// Run: (cd omen && npm install --no-save google-play-scraper@10 && node update-app-charts.mjs)
import gplay from "google-play-scraper";
import { writeFileSync } from "node:fs";

const DEPTH = 200; // keep in sync with APP_REF_DEPTH in update-china-data.py
// Keep countries + patterns in sync with APP_COUNTRIES / APP_BASKET in update-china-data.py.
const COUNTRIES = ["us", "gb", "de", "fr", "jp", "in", "br", "ca", "au", "kr"];
const BASKET = [
  ["DeepSeek", /deepseek/i],
  ["Qwen", /\bqwen\b|tongyi/i],
  ["Doubao", /doubao|\bcici\b/i],
  ["Kimi", /\bkimi\b|kimichat/i],
  ["MiniMax", /talkie|hailuo|minimax|weaver\.app/i],
];

function label(title, appId) {
  const text = `${title || ""} ${appId || ""}`;
  for (const [name, rx] of BASKET) if (rx.test(text)) return name;
  return null;
}

const hits = [];
let ok = 0;
for (const country of COUNTRIES) {
  try {
    const list = await gplay.list({
      collection: gplay.collection.TOP_FREE,
      category: gplay.category.APPLICATION,
      country,
      num: DEPTH,
    });
    ok++;
    list.forEach((a, i) => {
      const lbl = label(a.title, a.appId);
      if (lbl) hits.push({ label: lbl, store: "android", country, rank: i + 1, appId: a.appId, title: a.title });
    });
  } catch (e) {
    console.error(`  ${country}: FAILED (${e.message})`);
  }
}

if (ok === 0) {
  console.error("all Play queries failed - not writing app-charts.json");
  process.exit(1);
}

const out = {
  updated: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
  source: "google-play-scraper",
  store: "android",
  depth: DEPTH,
  countries_ok: ok,
  hits,
};
writeFileSync(new URL("./app-charts.json", import.meta.url), JSON.stringify(out, null, 1) + "\n");
console.log(`wrote app-charts.json: ${hits.length} CN-app hits across ${ok}/${COUNTRIES.length} markets`);
