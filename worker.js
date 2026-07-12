// OMEN Worker.
//
// Static assets (HTML, images, favicon) are served straight from ./omen by the
// [assets] binding. The three *data* files, however, must never be the copy that
// was bundled at deploy time — they'd go stale between deploys. For those paths
// (listed under assets.run_worker_first in wrangler.jsonc) this Worker runs first
// and streams the object live from the R2 bucket the GitHub Action writes to, so
// the dashboard is always current with no redeploy.
//
// On an R2 miss (e.g. before the first Action upload) it falls back to the bundled
// asset via env.ASSETS, so the site never hard-breaks during bootstrap.

const DATA_FILES = {
  "/market-data.json": { key: "market-data.json", type: "application/json" },
  "/snapshots.csv":    { key: "snapshots.csv",    type: "text/csv" },
  "/influencers.json": { key: "influencers.json", type: "application/json" },
};

// Edge-cache briefly: data refreshes on the order of tens of minutes, so ~60s keeps
// R2 read volume tiny while the dashboard still reads as live.
const CACHE_CONTROL = "public, max-age=0, s-maxage=60, must-revalidate";

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const spec = DATA_FILES[url.pathname];

    if (spec && env.DATA) {
      try {
        const obj = await env.DATA.get(spec.key);
        if (obj) {
          const headers = new Headers();
          obj.writeHttpMetadata(headers); // carries any stored content-type/etag
          headers.set("content-type", `${spec.type}; charset=utf-8`);
          headers.set("cache-control", CACHE_CONTROL);
          if (obj.httpEtag) headers.set("etag", obj.httpEtag);
          headers.set("x-omen-source", "r2");
          // honour conditional requests so the edge/browser can 304
          const inm = request.headers.get("if-none-match");
          if (inm && obj.httpEtag && inm === obj.httpEtag) {
            return new Response(null, { status: 304, headers });
          }
          return new Response(obj.body, { headers });
        }
      } catch (e) {
        // fall through to the bundled asset on any R2 error
      }
    }

    // everything else — and any R2 miss/error — is served from the bundled assets
    return env.ASSETS.fetch(request);
  },
};
