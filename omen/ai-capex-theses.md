# OMEN — Proposed dashboard theses (from Better Offline, "Why AI Has No ROI")

Source video: Paul Kedrosky on Ed Zitron's *Better Offline* — <https://www.youtube.com/watch?v=wGZboZcSGDY> (Jun 2026).

These are candidate signals for OMEN, mapped from the episode's arguments. OMEN today prices the cycle through *market sentiment* (Polymarket indexes + a crash-pressure gauge on options skew, credit, volatility, drawdowns). Kedrosky's case is that stress shows up first in *fundamentals and financing plumbing* — token unit economics, debt issuance, equity raises, filings — which these rows fill in.

Rows 1–5 are shipped on the [AI CapEx page](ai-capex.html). Rows 6–12 remain backlog.

| # | Thesis (from the episode) | Metric | Data source |
|---|---|---|---|
| 1 | Token deflation vs fixed debt (duration mismatch) | Blended $/1M tokens at a fixed capability tier, YoY % decline; trend vs a fixed "debt service" line | OpenRouter API pricing history; Artificial Analysis |
| 2 | End of subsidized pricing (Copilot-style repricing shocks) | Count/log of vendor repricing events (unbundling, caps, meter switches); effective price per consumer plan | Vendor pricing pages (scraped changelog); OpenAI/Anthropic/GitHub announcements |
| 3 | Hyperscaler debt saturation (now bigger IG issuers than banks) | Trailing-12-month IG bond issuance by MSFT/GOOG/META/AMZN/ORCL vs banks; new-issue spreads | SEC EDGAR (424B/FWP filings); FINRA TRACE |
| 4 | AI-specific credit stress (not just HYG) | Oracle + CoreWeave bond spreads / CDS proxy; neocloud & SPV deal flow (Blue Owl-style vehicles) | FINRA TRACE; EDGAR 8-Ks; bond ETF proxies via Yahoo Finance |
| 5 | Equity raises signal debt capacity running out | Hyperscaler secondary/ATM equity issuance, trailing 12 months (e.g. Google's $80B) | SEC EDGAR 8-K / 424B5 feed |
| 6 | Mega IPOs = blow-off top | OpenAI/Anthropic IPO odds & timing; S-1 filing watch; post-filing "capitalized training cost" flag | Polymarket/Kalshi IPO markets (add to Bear index); EDGAR S-1 RSS |
| 7 | AI capex is eating the economy | AI/data-center capex contribution to GDP growth; hyperscaler capex ÷ operating cash flow | BEA NIPA tables (info-processing investment); existing EDGAR/XBRL capex pipeline |
| 8 | Output is slop, not ROI | GitHub commit/repo growth vs reviews-per-app (the NBER divergence); enterprise AI adoption rate | GH Archive (BigQuery); Census Bureau BTOS AI-use survey; NBER paper series |
| 9 | >1 GW projects don't get finished (stranded assets) | Announced vs under-construction vs energized GW; cancellation/pause tracker | EIA 860M; LBNL interconnection-queue data; Kalshi data-center markets |
| 10 | Behind-the-meter gas buildout at the worst time | Gas-turbine order backlog; behind-the-meter generation announcements tied to data centers | EIA; GE Vernova/Siemens Energy earnings disclosures |
| 11 | Circular financing (stock as scrip) | Share of NVDA revenue from equity-linked counterparties; vendor-financing/related-party disclosures | 10-Q/10-K concentration disclosures via EDGAR XBRL |
| 12 | Small models commoditize the frontier | Capability gap (benchmark delta) between best open-weight small models and frontier, over time | Artificial Analysis API; LMArena leaderboard |

## Integration notes

- **Cheapest wins first**: rows 6 and 9 are largely new Polymarket/Kalshi markets, which slot straight into the existing Bear index and Gamma API pipeline. Row 7 mostly extends the EDGAR capex work OMEN already does.
- **New signal family**: rows 1–5 justify a sixth crash-gauge family ("Financing stress") — the leading indicator Kedrosky argues the market-based signals will lag. OMEN's own disclaimer already admits the bear markets show "no statistically significant lead" over drawdowns.
- **Hardest rows**: 2, 8 and 10 lack clean free APIs and need light scraping or manual/monthly refresh — flag as "curated" rather than live.
- **Shipped subset**: the [AI CapEx page](ai-capex.html) implements rows 3, 4, 5, 7, 9 as curated snapshots.
