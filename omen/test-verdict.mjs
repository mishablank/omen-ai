// Unit tests for the landing page's verdict rule — the deterministic mapping from
// (pair direction × crash-pressure regime) to the five headline states. Sliced out of
// index.html the same way test-pure-helpers.mjs slices the monitor, because there is
// no build step and no bundler.
//
//   node omen/test-verdict.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HTML = join(dirname(fileURLToPath(import.meta.url)), "index.html");
const SRC = readFileSync(HTML, "utf8");

// Slice [start, end) out of the source. Fails loudly if a marker moves, rather than
// silently testing nothing.
function slice(start, end) {
  const a = SRC.indexOf(start);
  if (a < 0) throw new Error(`start marker not found: ${start}`);
  const b = SRC.indexOf(end, a);
  if (b < 0) throw new Error(`end marker not found: ${end}`);
  return SRC.slice(a, b);
}
function build(code, names, stubs = {}) {
  const keys = Object.keys(stubs);
  const fn = new Function(...keys, `${code}\nreturn {${names.join(",")}};`);
  return fn(...keys.map((k) => stubs[k]));
}

let failures = 0;
const eq = (name, got, want) => {
  if (got === want) return;
  failures++;
  console.error(`  FAIL ${name}\n    got:  ${JSON.stringify(got)}\n    want: ${JSON.stringify(want)}`);
};
const ok = (name, cond, detail) => {
  if (cond) return;
  failures++;
  console.error(`  FAIL ${name}${detail ? " — " + detail : ""}`);
};

console.log("verdict rule — dirOf / verdictOf / regimeOf / VERDICTS\n");

const { VERDICTS, dirOf, verdictOf, regimeOf } = build(
  slice("/* ================= verdict", "/* ================= render"),
  ["VERDICTS", "dirOf", "verdictOf", "regimeOf"],
);

/* ---------- dirOf: direction bands ---------- */
{
  eq("dirOf/bullish at threshold", dirOf(0.55), "bull");
  eq("dirOf/mixed just under bull threshold", dirOf(0.5499), "mixed");
  eq("dirOf/mixed just above bear threshold", dirOf(0.4501), "mixed");
  eq("dirOf/bearish at threshold", dirOf(0.45), "bear");
  eq("dirOf/deep bull", dirOf(0.9), "bull");
  eq("dirOf/deep bear", dirOf(0.1), "bear");
}

/* ---------- verdictOf: the full 3×3 matrix, every cell ---------- */
{
  // bullish row
  eq("matrix/bull+calm", verdictOf(0.7, "calm"), "riskon");
  eq("matrix/bull+elevated", verdictOf(0.7, "elevated"), "constructive");
  eq("matrix/bull+stressed", verdictOf(0.7, "stressed"), "caution");
  // mixed row
  eq("matrix/mixed+calm", verdictOf(0.5, "calm"), "mixed");
  eq("matrix/mixed+elevated", verdictOf(0.5, "elevated"), "mixed");
  eq("matrix/mixed+stressed", verdictOf(0.5, "stressed"), "caution");
  // bearish row — stress can't soften a bearish read
  eq("matrix/bear+calm", verdictOf(0.3, "calm"), "riskoff");
  eq("matrix/bear+elevated", verdictOf(0.3, "elevated"), "riskoff");
  eq("matrix/bear+stressed", verdictOf(0.3, "stressed"), "riskoff");
}

/* ---------- every reachable verdict key has display metadata ---------- */
{
  const keys = new Set();
  for (const b of [0.7, 0.5, 0.3])
    for (const r of ["calm", "elevated", "stressed"]) keys.add(verdictOf(b, r));
  eq("VERDICTS/five distinct states reachable", keys.size, 5);
  for (const k of keys) {
    ok(`VERDICTS/${k} exists`, !!VERDICTS[k]);
    ok(`VERDICTS/${k} has label+headline+line`, !!(VERDICTS[k]?.label && VERDICTS[k]?.h && typeof VERDICTS[k]?.line === "function"));
  }
  // the line copy must carry the actual numbers a visitor acts on
  const line = VERDICTS.constructive.line(0.695, 40.1, "Elevated");
  ok("VERDICTS/line interpolates bull share", line.includes("69.5"), line);
  ok("VERDICTS/line interpolates gauge", line.includes("40.1"), line);
  ok("VERDICTS/line interpolates regime word", line.includes("Elevated"), line);
}

/* ---------- regimeOf: same trip rules as the monitor ---------- */
{
  eq("regime/deep calm", regimeOf(10, 5, 5), "calm");
  eq("regime/gauge trips elevated at 35", regimeOf(35, 0, 0), "elevated");
  eq("regime/gauge trips stressed at 55", regimeOf(55, 0, 0), "stressed");
  eq("regime/crash basket trips elevated at 25", regimeOf(10, 25, 0), "elevated");
  eq("regime/crash basket trips stressed at 40", regimeOf(10, 40, 0), "stressed");
  eq("regime/bubble market trips elevated at 15%", regimeOf(10, 0, 15), "elevated");
  eq("regime/bubble market alone can NOT trip stressed", regimeOf(10, 0, 99), "elevated");
  eq("regime/just under all thresholds stays calm", regimeOf(34.9, 24.9, 14.9), "calm");
}

/* ---------- the published matrix on the page matches the rule ---------- */
{
  // every cell id rendered in #why must be the cell verdictOf actually selects
  const CELLS = {
    "mx-bull-calm": "riskon", "mx-bull-elevated": "constructive", "mx-bull-stressed": "caution",
    "mx-mixed-calm": "mixed", "mx-mixed-elevated": "mixed", "mx-mixed-stressed": "caution",
    "mx-bear-calm": "riskoff", "mx-bear-elevated": "riskoff", "mx-bear-stressed": "riskoff",
  };
  const SHARE = { bull: 0.7, mixed: 0.5, bear: 0.3 };
  for (const [id, want] of Object.entries(CELLS)) {
    ok(`page/cell ${id} exists in markup`, SRC.includes(`id="${id}"`));
    const [, dir, regime] = id.split("-");
    eq(`page/cell ${id} agrees with verdictOf`, verdictOf(SHARE[dir], regime), want);
    // the visible cell text must be the state's published label
    const m = SRC.match(new RegExp(`id="${id}">([^<]+)<`));
    ok(`page/cell ${id} shows the state label`, !!m && m[1] === VERDICTS[want].label, m && m[1]);
  }
}

if (failures) {
  console.error(`\n${failures} failure(s)`);
  process.exit(1);
}
console.log("all verdict tests passed");
