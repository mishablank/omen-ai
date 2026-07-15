// Verdict-block assertion (§2.1): for every reachable regime state the three lines must
// render from live rule data, with no placeholders and none of the jargon §2.2 bans.
//
// The dashboard is one HTML file with inline JS and no build step, so this slices the regime
// block out of the source and evaluates it against stubbed inputs. Nothing else is loaded —
// if the slice markers ever move, the test fails loudly rather than silently passing.
//
//   node omen/test-regime-explainer.mjs        (or: python3 -m pytest, which shells out to it)

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HTML = join(dirname(fileURLToPath(import.meta.url)), "polymarket-ai-index.html");
const START = "const GAUGE_BANDS=";
const END = "/* ================= Historical gauge reconstruction";

function loadRegimeBlock() {
  const src = readFileSync(HTML, "utf8");
  const a = src.indexOf(START), b = src.indexOf(END);
  if (a < 0 || b < 0 || b <= a) throw new Error("regime block markers not found in " + HTML);
  return src.slice(a, b);
}
const BLOCK = loadRegimeBlock();

// Build the regime functions with every dependency stubbed: `sig` and the level are the
// scenario's inputs, and localStorage carries whatever prior snapshot the case wants.
function build({ gauge, z, level, stack, snaps = {} }) {
  const sig = {
    gauge: gauge == null ? { score: null } : { score: gauge },
    bearMkt: { z },
    stack: stack.map((s) => ({ name: s.name, plain: s.plain, v: s.v, isDelta: !!s.isDelta })),
  };
  const localStorage = { getItem: () => JSON.stringify(snaps) };
  const factory = new Function(
    "sig", "indexNowFixed", "migrateSnaps", "SNAP_KEY", "localStorage",
    BLOCK + "\nreturn { computeRegime, regimeExplainer, gaugeBand, regimeRules };"
  );
  return factory(sig, () => level, (s) => s, "snap-key", localStorage);
}

/* ---------- assertions ---------- */
let failures = 0;
const check = (name, cond, detail) => {
  if (cond) return;
  failures++;
  console.error(`  FAIL ${name}${detail ? " — " + detail : ""}`);
};

const PLACEHOLDER = /undefined|NaN|\[object|\$\{|\bnull\b/;
// §2.2: no methodology, no z-scores, no family names, no "single-signal trip"
const BANNED = [
  "single-signal trip", "z-score", "z score", "blended gauge (",
  "Prediction markets", "Options skew", "Volatility complex", "Credit stress",
  "Equity drawdown", "Macro & China", "chain-link", "normaliz", "FRED", "N(−d2)",
];

function assertLines(label, ex, expect) {
  const lines = { say: ex.say, trig: ex.trig, ctx: ex.ctx };
  for (const [k, v] of Object.entries(lines)) {
    check(`${label}/${k} non-empty`, typeof v === "string" && v.trim().length > 0);
    check(`${label}/${k} no placeholder`, !PLACEHOLDER.test(v), JSON.stringify(v));
    for (const b of BANNED) {
      check(`${label}/${k} bans "${b}"`, !v.toLowerCase().includes(b.toLowerCase()), JSON.stringify(v));
    }
  }
  if (expect.sayHas) check(`${label}/say says "${expect.sayHas}"`, ex.say.includes(expect.sayHas), ex.say);
  if (expect.trigHas) check(`${label}/trig says "${expect.trigHas}"`, ex.trig.includes(expect.trigHas), ex.trig);
  if (expect.ctxHas) check(`${label}/ctx says "${expect.ctxHas}"`, ex.ctx.includes(expect.ctxHas), ex.ctx);
  if (expect.broad !== undefined) check(`${label}/broad=${expect.broad}`, ex.broad === expect.broad);
}

/* ---------- scenarios: every reachable regime state ---------- */
// bubble sentiment is a singular subject; the recession row is plural. Both shapes matter:
// the verdict block's verbs have to agree with whichever row happens to be the trigger.
const stackAt = (v) => [
  { name: "Bubble sentiment (extreme euphoria)", plain: "bubble sentiment", v },
  { name: "Funding window: IPO odds 7d change", plain: "the AI funding window", v: -9, isDelta: true },
  { name: "US recession probability", plain: "US recession odds", pl: true, v: 5 },
];

const CASES = [
  {
    label: "calm",
    inp: { gauge: 20, z: 0, level: 10, stack: stackAt(8) },
    regime: "calm",
    // gauge 20/35 is a larger fraction of its threshold than sentiment 8/15, so it is the
    // closest rule — the calm lines name whichever rule is actually nearest to firing
    expect: { sayHas: "Regime: Calm – no rule has tripped.", trigHas: "Closest rule: the blended gauge at 20/100, below the 35/100 rule threshold.", ctxHas: "(Calm) – broad markets are quiet too. It turns Elevated if the blended gauge crosses 35/100.", broad: false },
  },
  {
    // same calm regime, but sentiment is now the nearest rule — the line must follow the data
    label: "calm-closest-is-sentiment",
    inp: { gauge: 10, z: 0, level: 3, stack: stackAt(14) },
    regime: "calm",
    expect: { trigHas: "Closest rule: bubble sentiment at 14.0%, below the 15% rule threshold.", ctxHas: "It turns Elevated if bubble sentiment crosses 15%." },
  },
  {
    // the live case the plan was written against: one rule trips while the gauge stays Calm
    label: "stressed-single-trip",
    inp: { gauge: 24, z: 0, level: 10, stack: stackAt(32.3) },
    regime: "stressed",
    expect: {
      sayHas: "Regime: Stressed – tripped by a single rule, not broad pressure.",
      trigHas: "Trigger: bubble sentiment at 32.3%, above the 25% rule threshold.",
      ctxHas: "The blended gauge is 24/100 (Calm) – broad markets are not confirming. This clears if bubble sentiment closes below 25%; it escalates if the blended gauge crosses 35.",
      broad: false,
    },
  },
  {
    // plural trigger: the verb has to agree ("odds close", not "odds closes")
    label: "stressed-single-trip-plural",
    inp: { gauge: 24, z: 0, level: 41, stack: stackAt(8) },
    regime: "stressed",
    expect: { trigHas: "Trigger: crash-market odds at 41.0%, above the 40% rule threshold.", ctxHas: "This clears if crash-market odds close below 40%" },
  },
  {
    label: "elevated-single-trip",
    inp: { gauge: 24, z: 0, level: 10, stack: stackAt(18) },
    regime: "elevated",
    expect: { sayHas: "tripped by a single rule, not broad pressure", trigHas: "above the 15% rule threshold", ctxHas: "it escalates if the blended gauge crosses 35", broad: false },
  },
  {
    label: "elevated-broad",
    inp: { gauge: 40, z: 0, level: 26, stack: stackAt(18) },
    regime: "elevated",
    expect: { sayHas: "3 rules have tripped and the broad gauge agrees", trigHas: "+1 more.", ctxHas: "it escalates if the blended gauge crosses 55", broad: true },
  },
  {
    label: "stressed-broad",
    inp: { gauge: 60, z: 2.5, level: 45, stack: stackAt(40) },
    regime: "stressed",
    expect: { sayHas: "4 rules have tripped and the broad gauge agrees", ctxHas: "(Stressed) – broad markets are confirming", broad: true },
  },
  {
    // gauge dark (market-data.json not fetched): must not leak a placeholder value
    label: "gauge-dark",
    inp: { gauge: null, z: 0, level: 10, stack: stackAt(18) },
    regime: "elevated",
    expect: { sayHas: "tripped by a single rule", ctxHas: "The blended gauge has not loaded yet." },
  },
  {
    // z-only trip: the rule has no lay unit, so the lines must state it without a number
    label: "elevated-z-only",
    inp: { gauge: 20, z: 1.8, level: 10, stack: stackAt(8) },
    regime: "elevated",
    expect: { trigHas: "Trigger: crash-market odds are climbing unusually fast.", ctxHas: "This clears if that climb slows back to normal" },
  },
];

console.log("regime_explainer — three lines across every reachable state\n");
for (const c of CASES) {
  const { computeRegime, regimeExplainer } = build(c.inp);
  const reg = computeRegime();
  check(`${c.label}/regime=${c.regime}`, reg.r === c.regime, `got ${reg.r}`);
  assertLines(c.label, regimeExplainer(reg), c.expect);
  console.log(`  ${c.label} (${reg.r}, ${reg.trips.length} trip(s))`);
}

/* ---------- since-yesterday clause ---------- */
{
  const yday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  const { computeRegime, regimeExplainer } = build({
    gauge: 24, z: 0, level: 10, stack: stackAt(32.3),
    snaps: { [yday]: { gauge: 21, regime: "stressed" } },
  });
  const ex = regimeExplainer(computeRegime());
  check("since/gauge delta", ex.since.includes("Gauge +3 vs yesterday"), ex.since);
  check("since/regime unchanged", ex.since.includes("regime unchanged"), ex.since);
  console.log("  since-yesterday:", JSON.stringify(ex.since));
}
{
  // no prior snapshot: the clause is omitted silently, not rendered empty
  const { computeRegime, regimeExplainer } = build({ gauge: 24, z: 0, level: 10, stack: stackAt(32.3) });
  check("since/omitted with no prior day", regimeExplainer(computeRegime()).since === "");
}
{
  const yday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  const { computeRegime, regimeExplainer } = build({
    gauge: 24, z: 0, level: 10, stack: stackAt(32.3),
    snaps: { [yday]: { gauge: 30, regime: "elevated" } },
  });
  const ex = regimeExplainer(computeRegime());
  check("since/negative delta", ex.since.includes("Gauge −6 vs yesterday"), ex.since);
  check("since/regime changed", ex.since.includes("regime was elevated"), ex.since);
}

console.log(failures ? `\n${failures} assertion(s) FAILED` : "\nall assertions passed");
process.exit(failures ? 1 : 0);
