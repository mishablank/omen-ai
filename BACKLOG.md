# Backlog

## China AI Monitor — Community Mentions (w=10)

**Status:** Open
**Component:** `china-ai-monitor.html`, `update-china-data.py`
**Priority:** Medium

### Problem

The "Community mentions" family in the Chinese AI Adoption Index is hardcoded to `null`:

```js
IDX.social = { w:10, val: null, detail: "Reddit/X mentions - no public API, not tracked" };
```

It is excluded from the weighted composite and displayed as an empty row with a dash. Reddit and X/Twitter have no public, key-free, CORS-accessible API for counting model mentions, so this slot was never wired to a data source — unlike OpenRouter, HuggingFace, GitHub, LMArena, and Polymarket, which are all fetched live or via the updater script.

### Acceptance Criteria

- [ ] Implement a data source for Reddit and/or X mention counts of Chinese AI models (DeepSeek, Qwen, GLM, Kimi, MiniMax, MiMo).
- [ ] If using OAuth APIs (Reddit API, X API), fetch server-side and store results in `china-data.json` via `update-china-data.py` — same pattern as LMArena/GitHub snapshots.
- [ ] Normalize the mention volume to a 0–100 score with a documented reference range.
- [ ] Assign the computed value to `IDX.social.val` in the page JS so the row renders a real score and is included in the weighted composite (weight renormalization adjusts automatically).
- [ ] Update the methodology footer text to reflect the new live source instead of "not tracked, no public API."

## China AI Monitor — SerpApi fallback for Android app charts (w=10, apps family)

**Status:** Open
**Component:** `update-app-charts.mjs`, `.github/workflows/refresh.yml`
**Priority:** Low

### Problem

The consumer-app family now pulls Android chart presence from `update-app-charts.mjs`, which uses `google-play-scraper` — a reverse-engineered scrape of Play's internal `batchexecute` endpoint. Google ships no key-free charts API, so this is the only free option, but the payload shape breaks every year or two and the whole family then degrades to iOS-only (Apple RSS) until the library is patched. iOS presence alone understates Chinese-app reach because Android is the larger install base in most non-US Western markets.

### Acceptance Criteria

- [ ] Add SerpApi's Google Play engine (`engine=google_play`, `store=apps`) as a keyed fallback for `update-app-charts.mjs`: try `google-play-scraper` first, fall back to SerpApi when it returns zero countries, gate on a `SERPAPI_KEY` secret (surface it in `refresh.yml`'s job env like `XAI_API_KEY`).
- [ ] SerpApi's free tier is ~100 searches/month; one daily 10-country pull ≈ 300/month, so either cap the fallback to the core markets (US/GB/DE/JP) or run it only when the primary scraper is down. Document the quota math in a comment.
- [ ] Keep the output schema identical (`{ hits: [{label, store:"android", country, rank, appId, title}] }`) so `android_hits()` in `update-china-data.py` needs no change.
- [ ] No behavioural change when `SERPAPI_KEY` is unset — the primary scraper path must stay the default.
