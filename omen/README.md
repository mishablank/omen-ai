# AI Crash Monitor

A dashboard blending Polymarket prediction-market odds with the options,
volatility, credit, equity, GPU-rent and insider-activity signals that historically
precede capex-bubble unwinds, into one 0–100 crash-pressure gauge with a stated regime.
The landing "Today" page summarizes the read in one viewport; four deep-dive views
(Market signals, GPU & fundamentals, Prediction markets, Methodology) hold the detail,
served from the same document at `/polymarket-ai-index/<view>`.

Open `polymarket-ai-index.html`. Polymarket data is fetched live in the browser; everything
else comes from `market-data.json`, produced by `update-market-data.py`.

## What's on the page

- **Crash-Pressure Gauge** — five equally-weighted families (prediction markets, options skew,
  vol complex, credit, equity drawdown), each normalized 0–100, plus a **reconstructed 90-day
  history** with regime bands. Families are also split into a **leading** sub-score (prediction
  markets, options skew, credit – priced before the fact) and a **confirming** sub-score
  (vol complex, equity drawdown – moves with/after prices); only the leading side can warn.
- **Regime chip** — Calm / Elevated / Stressed, and *which threshold fired* (e.g. "bubble market ≥ 15%").
  The fetcher embeds the same server-side gauge + regime into `market-data.json`
  (`server_gauge`), which the landing page reads — so the two pages can't disagree.
- **Two composite indexes** (Bull / Bear) as chain-linked small multiples with
  event annotations, tile sparklines, and risk-direction-aware delta colors. Each carries two
  sleeve tags with sub-readings: Bull splits **TECH** (technology-progress odds) vs **CAP**
  (capital-markets odds), since tech progress can survive a financial unwind; Bear splits
  **MKT** (the unwind itself) vs **GOV** (the regulatory clampdown). A sleeve is a read on the
  basket, not a sub-index – Bear is the flat equal-weight mean of all 9 constituents, not the
  mean of its two unequal sleeves. The monitor also carries China AI and Macro indexes.
- **LEAPS tail panel** — risk-neutral P(NVDA −50% / SOXX −40% in ~1y) from long-dated CBOE
  puts via N(−d2), as a deep-market cross-check on the thin Polymarket bubble book.
- **Fundamentals panel** — combined quarterly capex and operating cash flow for
  MSFT/GOOGL/AMZN/META/ORCL from SEC XBRL (the one non-market-priced anchor; capex/OCF is the
  classic capex-bubble metric).
- **Signal panels** — FRED credit spreads (HY OAS, CCC OAS, NFCI), single-name IV term
  structure, AI-complex breadth (basket vs SPY, % above 50-DMA), SEC Form 4 insider net-selling,
  H100 rent *implied vs realized* (vast.ai), Kalshi/Metaculus/Manifold cross-venue,
  bubble-market order-book depth, per-index concentration (effective N).
- **Influencer board** — curated editorial snapshot, or auto-scored (see below).

## Local use

```bash
python3 update-market-data.py --snapshot     # writes market-data.json + appends snapshots.csv
python3 -m http.server 8844                   # serve over HTTP (fetch() needs it), then open
#   http://localhost:8844/polymarket-ai-index.html
python3 update-market-data.py --watch 600 --snapshot   # optional: refresh every 10 min
```

Tests: `python3 -m pytest` (covers FRED parsing, Form 4 parsing, and the server-side gauge/regime).

## Data sources (all free / keyless)

| Panel | Source |
|---|---|
| Prediction markets, order book | Polymarket Gamma + CLOB |
| Equity / vol / credit proxies | Yahoo Finance chart API |
| 25Δ risk reversal + IV term structure | CBOE delayed quotes |
| LEAPS-implied 1y tail (N(−d2)) | CBOE delayed quotes (same chains) |
| HY OAS, CCC OAS, NFCI | FRED `fredgraph.csv` (honest UA required) |
| Hyperscaler capex / OCF fundamentals | SEC XBRL `companyconcept` (honest UA required) |
| Insider net-selling | SEC EDGAR Form 4 (open-market S/P only) |
| Realized H100 spot rent | vast.ai public bundles API |
| Cross-venue | Kalshi public API, Manifold public API, Metaculus (needs free `METACULUS_TOKEN`) |
| AI CapEx live tape (`ai-capex.html`) | `update-capex-data.py` → `capex-data.json`: TSMC monthly revenue (TWSE OpenAPI), EDGAR full-text issuance counts, Ramp AI Index CSV, Anthropic Economic Index freshness (HF API); EIA-860M generator pipeline needs free `EIA_API_KEY`. Korea 20-day exports, LBNL queues, PJM auction clears stay hand-updated in its `MANUAL` dict |

## Hosting: Cloudflare Worker + R2

The dashboard's most valuable output is accumulated history. Don't keep it on a laptop.
The site is deployed as a **Cloudflare Worker with static assets**, and the data files are
served **live from an R2 bucket** so they're always current with no redeploy.

- Static files live in `omen/` and are served by the `[assets]` binding (`wrangler.jsonc`).
- `worker.js` runs first for `/market-data.json`, `/snapshots.csv` and `/influencers.json`
  (via `assets.run_worker_first`) and streams them from the R2 bucket `omen-data`, falling
  back to the bundled copy only on a miss — so nothing breaks before the first upload.
- **GitHub Action** (`.github/workflows/refresh.yml`) runs `omen/update-market-data.py
  --snapshot --alert` every ~30 min, commits the data back (durable history) **and uploads
  it to R2** (when the Cloudflare secrets are set), which the live Worker picks up instantly.

### One-time R2 setup (run from the repo root, where `wrangler.jsonc` lives)

```bash
npx wrangler r2 bucket create omen-data          # create the bucket (must exist before deploy)
npx wrangler deploy                              # deploy worker.js + the R2/ASSETS bindings
# seed R2 now so the site is fresh immediately:
npx wrangler r2 object put omen-data/market-data.json --file omen/market-data.json --content-type application/json --remote
npx wrangler r2 object put omen-data/snapshots.csv     --file omen/snapshots.csv     --content-type text/csv           --remote
# let CI keep R2 fresh — add an R2-Edit API token + account id as Action secrets:
gh secret set CLOUDFLARE_API_TOKEN  -R mishablank/ai-crash-monitor
gh secret set CLOUDFLARE_ACCOUNT_ID -R mishablank/ai-crash-monitor
```

Without the two `CLOUDFLARE_*` secrets the upload step skips cleanly and the Worker serves the
bundled copy (i.e. previous behaviour).

### Alerts (optional, work with the browser closed)

The `--alert` flag computes the gauge server-side and pushes when the regime *escalates*.
Set repo **Secrets → Actions**:

- Telegram: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`, **or**
- ntfy.sh: `NTFY_TOPIC` (any unguessable string; subscribe to it in the ntfy app).

State is deduped in `alert-state.json` so you get one ping per escalation, not one per run.

### Auto-scored influencers (optional)

Set `XAI_API_KEY` as a repo secret. `update-influencers.py` then reads each voice's recent
posts via Grok Live Search, scores −100…+100 with evidence, and writes `influencers.json`,
which the dashboard prefers over its inline fallback. Without the key, the curated snapshot stands.

## Repo

Lives at `mishablank/ai-crash-monitor` (private). Push data/code as usual:

```bash
git pull --rebase origin main   # take the bot's data commits, replay local work on top
git push
```

Optional Action secrets (Settings → Secrets and variables → Actions): the two `CLOUDFLARE_*`
above for R2, plus `TELEGRAM_*` / `NTFY_TOPIC` for alerts, `XAI_API_KEY` for influencer scoring,
and `METACULUS_TOKEN` for the Metaculus cross-venue panel (free account; token on the profile page).

Not investment advice.
