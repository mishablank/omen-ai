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
