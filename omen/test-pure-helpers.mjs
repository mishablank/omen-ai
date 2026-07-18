// Unit tests for the dashboard's pure helpers — the ones with no DOM dependency, so they
// are as testable as any library function. Sliced out of the single HTML file the same way
// test-regime-explainer.mjs does it, because there is no build step and no bundler.
//
//   node omen/test-pure-helpers.mjs        (or: python3 -m pytest, which shells out to it)

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HTML = join(dirname(fileURLToPath(import.meta.url)), "polymarket-ai-index.html");
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
// Build a scope from a slice, injecting stubs and returning the named exports.
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

console.log("pure helpers — mnum / xlink / fetcherStale / viewFromPath / claims watch\n");

/* ---------- mnum: prose needs a real minus sign, not toFixed()'s hyphen ---------- */
{
  const { mnum } = build(slice("const mnum =", "\n\n/* ================= Fetching"), ["mnum"]);
  eq("mnum/negative uses U+2212", mnum(-27), "−27");
  ok("mnum/not a hyphen-minus", !mnum(-27).includes("-"), JSON.stringify(mnum(-27)));
  eq("mnum/positive unsigned", mnum(27), "27");
  eq("mnum/zero unsigned", mnum(0), "0");
  eq("mnum/decimals", mnum(-7.45, 1), "−7.5");
  eq("mnum/rounds toward even at default 0dp", mnum(-0.4), "0"); // -0.4 -> "0", never "−0"
  console.log("  mnum");
}

/* ---------- xlink: §4.1 cross-link markup ---------- */
{
  const { xlink } = build(slice("const xlink =", "\n\n/* ================= Fetching"), ["xlink"]);
  const h = xlink("Levered-AI drawdowns", "markets", "p-breadth");
  ok("xlink/href targets view+anchor", h.includes('href="/polymarket-ai-index/markets#p-breadth"'), h);
  ok("xlink/carries data-nav for the router", h.includes('data-nav="markets"'), h);
  ok("xlink/carries data-anchor for the scroll", h.includes('data-anchor="p-breadth"'), h);
  ok("xlink/keeps the label", h.includes("Levered-AI drawdowns"), h);
  console.log("  xlink");
}

/* ---------- fetcherStale: panel staleness must match the header pill's own line ---------- */
{
  const code = slice("const ageTxt=", "// every panel whose numbers come out of market-data.json");
  const mk = (mkt) => build(code, ["fetcherStale", "FRESH_MS", "ageTxt"], { mkt });
  const iso = (msAgo) => new Date(Date.now() - msAgo).toISOString();

  eq("fetcherStale/no data -> null", mk(null).fetcherStale(), null);
  eq("fetcherStale/no updated field -> null", mk({}).fetcherStale(), null);

  const { FRESH_MS } = mk({});
  eq("fetcherStale/FRESH_MS is the pill's 2h line", FRESH_MS, 2 * 3600000);

  eq("fetcherStale/fresh (1h) -> null", mk({ updated: iso(3600000) }).fetcherStale(), null);
  eq("fetcherStale/just inside the line -> null", mk({ updated: iso(FRESH_MS - 60000) }).fetcherStale(), null);

  const stale = mk({ updated: iso(12 * 3600000) }).fetcherStale();
  ok("fetcherStale/12h -> stale object", stale !== null);
  eq("fetcherStale/12h label", stale.label, "data 12h old");

  // boundary: exactly at the line is stale (>=), not fresh
  ok("fetcherStale/exactly at 2h -> stale", mk({ updated: iso(FRESH_MS) }).fetcherStale() !== null);
  // days roll up
  eq("fetcherStale/3d label", mk({ updated: iso(3 * 86400000) }).fetcherStale().label, "data 3d old");
  console.log("  fetcherStale");
}

/* ---------- viewFromPath: the router's only parser ---------- */
{
  const code = slice("const VIEWS = {", "function applyView(");
  const mk = (pathname) =>
    build(code, ["viewFromPath", "VIEWS", "BASE"], {
      location: { pathname },
      $: () => null, // assembleGpuStory is defined in this slice but never called here
    });

  const cases = [
    ["/polymarket-ai-index", "today"],
    ["/polymarket-ai-index/", "today"],
    ["/polymarket-ai-index/markets", "markets"],
    ["/polymarket-ai-index/markets/", "markets"],
    ["/polymarket-ai-index/gpu", "gpu"],
    ["/polymarket-ai-index/prediction-markets", "prediction-markets"],
    ["/polymarket-ai-index/methodology", "methodology"],
    // unknown or malformed paths must fall back, never throw
    ["/polymarket-ai-index/bogus", "today"],
    ["/polymarket-ai-index/markets/deep", "today"],
    ["/", "today"],
    ["/polymarket-ai-index.html", "today"], // the local static-server path
  ];
  for (const [p, want] of cases) eq(`viewFromPath("${p}")`, mk(p).viewFromPath(), want);

  // every declared view must be reachable by its own path — catches a VIEWS/route drift
  const { VIEWS } = mk("/");
  for (const v of Object.keys(VIEWS)) {
    if (v === "today") continue;
    eq(`viewFromPath/reaches declared view "${v}"`, mk(`/polymarket-ai-index/${v}`).viewFromPath(), v);
  }
  console.log("  viewFromPath");
}

/* ---------- claims watch: yoyPct / ptsChange / probAbove / claimReads ---------- */
{
  const code = slice("/* ================= Claims watch: pure helpers",
                     "/* ================= Claims watch: fetch & render");
  const { yoyPct, ptsChange, probAbove, claimReads, snum } =
    build(code, ["yoyPct", "ptsChange", "probAbove", "claimReads", "snum"]);

  /* snum: signed prose number — U+2212 for negatives (house style), + for positives */
  eq("snum/negative uses U+2212", snum(-0.2), "−0.2");
  eq("snum/positive signed", snum(0.5), "+0.5");
  eq("snum/rounds to zero unsigned", snum(-0.04), "0.0");
  eq("snum/zero unsigned", snum(0), "0.0");

  // monthly FRED-style series builder: {d:"YYYY-MM-01", c}
  const monthly = (y0, m0, vals) => vals.map((c, k) => {
    const m = m0 + k, y = y0 + Math.floor((m - 1) / 12), mm = ((m - 1) % 12) + 1;
    return { d: `${y}-${String(mm).padStart(2, "0")}-01`, c };
  });

  /* yoyPct: last observation vs the one nearest 12 months earlier */
  const ser = monthly(2025, 1, Array.from({ length: 19 }, (_, i) => 100 + i)); // Jan-25..Jul-26
  const y = yoyPct(ser); // 118 vs 106 -> +11.32%
  ok("yoyPct/monthly 19pt", y != null && Math.abs(y - 11.3208) < 0.01, String(y));
  eq("yoyPct/short series -> null", yoyPct(monthly(2026, 1, Array.from({ length: 12 }, (_, i) => 100 + i))), null);
  eq("yoyPct/null -> null", yoyPct(null), null);
  // a series with a years-wide hole must refuse to fake a YoY off a distant point
  const holed = monthly(2020, 1, Array.from({ length: 13 }, (_, i) => 100 + i)).concat([{ d: "2026-07-01", c: 140 }]);
  eq("yoyPct/gap > 45d -> null", yoyPct(holed), null);

  /* ptsChange: level change over the trailing n observations */
  eq("ptsChange/6mo", ptsChange(monthly(2026, 1, [4.0, 4.0, 4.1, 4.1, 4.2, 4.3, 4.5]), 6), 0.5);
  eq("ptsChange/too short -> null", ptsChange(monthly(2026, 1, [4.0, 4.5]), 6), null);
  eq("ptsChange/null -> null", ptsChange(null, 6), null);

  /* probAbove: mass in brackets whose floor is at/above the threshold */
  const gdpBr = [
    { lo: null, hi: 1.0, p: 0.039 }, { lo: 1.0, hi: 1.5, p: 0.195 }, { lo: 1.5, hi: 2.0, p: 0.215 },
    { lo: 2.0, hi: 2.5, p: 0.32 }, { lo: 2.5, hi: 3.0, p: 0.185 }, { lo: 3.0, hi: 3.5, p: 0.025 },
    { lo: 3.5, hi: null, p: 0.053 },
  ];
  ok("probAbove/gdp >= 3%", Math.abs(probAbove(gdpBr, 3.0) - 0.078) < 1e-9, String(probAbove(gdpBr, 3.0)));
  eq("probAbove/no bracket at threshold -> null", probAbove([{ lo: null, hi: 1.0, p: 0.5 }], 3.0), null);
  eq("probAbove/empty -> null", probAbove([], 3.0), null);
  eq("probAbove/null -> null", probAbove(null, 3.0), null);

  /* claimReads: the five singularity claims, banded off the tape */
  const base = { agi: 10.5, arena: 18, optimus: 14.5, robotaxi: 14.5,
                 gdpAbove3: 7.8, gdpLast: 2.0, goodsYoy: 0.8, elecYoy: 4.8, gpuRatio: 0.15,
                 un6: 0.0, lfpr6: 0.1 };
  const rows = claimReads(base);
  eq("claimReads/five rows in stable order", rows.map(r => r.key).join(","), "agi,robots,gdp,deflation,labor");
  for (const r of rows) ok(`claimReads/${r.key} has metrics`, Array.isArray(r.metrics) && r.metrics.length >= 2);

  const by = (inp) => Object.fromEntries(claimReads(inp).map(r => [r.key, r]));
  // calm tape: every claim reads "good" (markets reject the narrative, no displacement, no deflation)
  for (const [k, r] of Object.entries(by(base))) eq(`claimReads/${k} calm -> good`, r.band.cls, "good");
  ok("claimReads/agi verdict carries the odds", by(base).agi.verdict.includes("10.5"));

  // euphoria broadening: odds converging up toward the narrative escalate the band
  eq("claimReads/agi 25% -> warn", by({ ...base, agi: 25 }).agi.band.cls, "warn");
  eq("claimReads/agi 55% -> crit", by({ ...base, agi: 55 }).agi.band.cls, "crit");
  eq("claimReads/robots max(optimus,robotaxi) drives band", by({ ...base, robotaxi: 30 }).robots.band.cls, "warn");
  eq("claimReads/gdp 30% -> warn", by({ ...base, gdpAbove3: 30 }).gdp.band.cls, "warn");
  eq("claimReads/gdp 60% -> crit", by({ ...base, gdpAbove3: 60 }).gdp.band.cls, "crit");

  // deflation: the claim validating on the consumer tape is the escalation
  eq("claimReads/goods deflation -> crit", by({ ...base, goodsYoy: -1.0 }).deflation.band.cls, "crit");
  eq("claimReads/goods disinflation -> warn", by({ ...base, goodsYoy: 0.2 }).deflation.band.cls, "warn");

  // labor: unemployment up while prime-age participation holds = displacement, not replacement
  const disp = by({ ...base, un6: 0.5, lfpr6: 0.0 }).labor;
  eq("claimReads/displacement -> crit", disp.band.cls, "crit");
  ok("claimReads/displacement named", /displacement/i.test(disp.verdict), disp.verdict);
  // verdict prose must carry the house minus, never toFixed()'s hyphen
  const negV = by({ ...base, un6: -0.2 }).labor.verdict;
  ok("claimReads/verdict uses U+2212", negV.includes("−0.2") && !negV.includes("-0.2"), negV);
  eq("claimReads/mild unemployment drift -> warn", by({ ...base, un6: 0.2 }).labor.band.cls, "warn");
  // unemployment up with participation falling too is a demographic/recession mix, not displacement
  eq("claimReads/participation falling too -> warn", by({ ...base, un6: 0.5, lfpr6: -0.5 }).labor.band.cls, "warn");

  // dark inputs: every band null, never a throw
  for (const r of claimReads({})) eq(`claimReads/${r.key} dark -> null band`, r.band, null);
  console.log("  claims watch");
}

console.log(failures ? `\n${failures} assertion(s) FAILED` : "\nall assertions passed");
process.exit(failures ? 1 : 0);
