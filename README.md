# AI Crash Monitor

A single-page dashboard blending Polymarket prediction-market odds with the options,
volatility, credit, equity, GPU-rent and insider-activity signals that historically
precede capex-bubble unwinds — into one 0–100 crash-pressure gauge with a stated regime.

Open `polymarket-ai-index.html`. Polymarket data is fetched live in the browser; everything
else comes from `market-data.json`, produced by `update-market-data.py`.

## What's on the page

- **Crash-Pressure Gauge** — five equally-weighted families (prediction markets, options skew,
  vol complex, credit, equity drawdown), each normalized 0–100, plus a **reconstructed 90-day
  history** with regime bands.
- **Regime chip** — Calm / Elevated / Stressed, and *which threshold fired* (e.g. "bubble market ≥ 15%").
- **Three composite indexes** (Bull / Crash / Regulation) as chain-linked small multiples with
  event annotations, tile sparklines, and risk-direction-aware delta colors.
- **New signal panels** — FRED credit spreads (HY OAS, CCC OAS, NFCI), single-name IV term
  structure, AI-complex breadth (basket vs SPY, % above 50-DMA), SEC Form 4 insider net-selling,
  H100 rent *implied vs realized* (vast.ai), Manifold cross-venue, bubble-market order-book depth.
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
| HY OAS, CCC OAS, NFCI | FRED `fredgraph.csv` (honest UA required) |
| Insider net-selling | SEC EDGAR Form 4 (open-market S/P only) |
| Realized H100 spot rent | vast.ai public bundles API |
| Cross-venue | Kalshi public API, Manifold public API |

## Automated hosting (recommended)

The dashboard's most valuable output is accumulated history. Don't keep it on a laptop.

1. **Push this repo to GitHub** (see below).
2. **GitHub Action** (`.github/workflows/refresh.yml`) runs `update-market-data.py --snapshot --alert`
   every ~30 min and commits `market-data.json` + `snapshots.csv` back. Durable, cross-machine.
3. **Cloudflare Pages** → connect the repo, no build command, output dir `/` (repo root). Every
   data commit redeploys automatically. Same flow as your other sites.

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

## Push to GitHub

```bash
gh repo create ai-crash-monitor --private --source . --remote origin --push
# then in the repo: Settings → Secrets and variables → Actions → add any of the optional secrets
# then Cloudflare Pages → Create project → connect ai-crash-monitor → framework: none → deploy
```

Not investment advice.
