# 2026-07-23 – Recalibrate two China-gauge reference ranges + stop dropping GitHub velocity

(Named with a suffix instead of the usual `UPDATES-<date>.md` because a separate
same-day PR – the dead-events-pipeline removal – already claims that filename.)

## Problem

A data-source audit found two of the Chinese AI Adoption Index's fixed reference
ranges miscalibrated, in opposite directions:

- **GitHub star velocity (w=15)** was normalized 0→2,000 stars/day. The 7-repo
  basket's steady state is ~30/day, so the family sat pinned at ~2/100 – a
  15%-weight constant drag, not a signal. 2,000/day is a launch-spike number.
- **HF download share (w=20)** was normalized 0→70% while the Chinese share
  already reads 68.9% – pinned at ~98/100 with no headroom to signal further
  gains (a ceiling effect; the router and Ollama families also use 70% but trade
  far below it, so they are untouched).

Compounding the first: `update-china-data.py` only emits `github_stars_per_day`
on the ~1 run/day where the star baseline in `china-history.json` has aged past
20h (measuring resets the baseline). Every other refresh dropped the key, so the
page excluded the GitHub family from the gauge for most of the day regardless of
the range.

## Change

- `china-ai-monitor.html`: GitHub velocity range 0→2,000 becomes **0→300/day**
  (steady state ~30/day reads ~10/100; a major-launch surge saturates, which is
  the intent). HF share range 0→70% becomes **0→90%** (today's 68.9% reads
  ~77/100). Methodology footer updated to match.
- `update-china-data.py`: new `pick_github_velocity()` carries the last measured
  velocity forward on runs that can't remeasure – the same carry-forward idiom
  the trends/apps/AA families already use – so the key is always present once
  first measured.
- `test_update_china_data.py`: 3 new tests for the carry (fresh preferred,
  previous carried, None when never measured). 39 pass.

## Calibration note

The gauge is computed at render time and never stored, so no history breaks, but
the headline number steps down ~2–3 points at deploy (HF no longer near-100,
GitHub now a real low reading instead of excluded/2). That step is the
recalibration, not a market move.
