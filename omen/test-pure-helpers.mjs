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

console.log("pure helpers — mnum / xlink / fetcherStale / viewFromPath\n");

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

console.log(failures ? `\n${failures} assertion(s) FAILED` : "\nall assertions passed");
process.exit(failures ? 1 : 0);
