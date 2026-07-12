#!/usr/bin/env python3
"""Refresh china-data.json for the China AI Substitution Monitor.

Stdlib only, same pattern as update-market-data.py. The page works without
this file (it embeds a dated snapshot and fetches the fast-moving data live,
client-side); this script refreshes the slow-moving fields that have no
CORS-open API:

  - LMArena leaderboard (arena.ai) : best Chinese model rank/Elo, top-10/20 counts
  - GitHub star velocity           : server-side baseline in china-history.json
  - BABA / KWEB contrast series    : Yahoo chart API
  - OpenRouter weekly share        : appended to china-snapshots.csv (durable history)

Artificial Analysis, search-interest and consumer-app figures have no scrapeable
source; edit MANUAL below when you refresh them by hand.

Usage:
  python3 update-china-data.py                # one shot
  python3 update-china-data.py --watch 86400  # daily loop
After running, redeploy the site folder so the deployed china-data.json updates.
"""
import json, re, csv, html, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) omen-china-monitor/1.0"}

# ---- fields with no machine-readable source: update by hand when re-verified ----
MANUAL = {
    "artificial_analysis": {"cn_best": "GLM-5.2", "cn_score": 51,
                            "us_best": "Claude Fable 5", "us_score": 60, "asof": "2026-07-12"},
    "search_consumer": {"western_share_pct": 1, "asof": "2026-04",
                        "note": "Goodie AI-referral report: DeepSeek+Qwen <1% of Western AI referral traffic."},
    "apps": {"score": 20, "asof": "2026-01",
             "note": "Qwen app >200M MAU, Doubao >100M DAU, DeepSeek ~82M WAU - overwhelmingly domestic."},
}

CN_ARENA_ORGS = ("Alibaba", "DeepSeek", "Z.ai", "Zhipu", "Moonshot", "MiniMax", "Xiaomi",
                 "Tencent", "StepFun", "Baidu", "ByteDance", "01.AI", "iFlytek", "Meituan")
GH_REPOS = ["deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1", "QwenLM/Qwen3", "zai-org/GLM-5",
            "MoonshotAI/Kimi-K2", "MiniMax-AI/MiniMax-M2", "XiaomiMiMo/MiMo"]
CN_AUTHORS = {"deepseek", "qwen", "z-ai", "thudm", "moonshotai", "minimax", "xiaomi", "tencent",
              "stepfun", "baidu", "bytedance-seed", "baai", "inclusionai", "01-ai", "internlm", "openbmb"}
US_AUTHORS = {"openai", "anthropic", "google", "meta-llama", "x-ai", "nvidia", "microsoft", "amazon",
              "perplexity", "poolside", "inception", "liquid", "allenai", "ibm-granite", "openrouter"}


def get(url, timeout=30):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def jget(url, timeout=30):
    return json.loads(get(url, timeout))


def arena():
    """Parse the server-rendered arena.ai text leaderboard."""
    page = get("https://arena.ai/leaderboard/text", timeout=60).decode("utf-8", "replace")
    rows = []
    for chunk in page.split("<tr")[1:]:
        m = re.search(r'title="([^"]+)"', chunk)
        if not m:
            continue
        org = re.search(r'truncate text-xs">([^<]+)<', chunk)
        rank = re.search(r">(\d{1,3})<", chunk)
        elo = re.search(r">(\d{4})<", chunk)
        if org and rank and elo:
            rows.append({"model": html.unescape(m.group(1)), "org": org.group(1).split("·")[0].strip(),
                         "rank": int(rank.group(1)), "elo": int(elo.group(1))})
    if not rows:
        raise ValueError("no leaderboard rows parsed")
    rows.sort(key=lambda r: r["rank"])
    cn = [r for r in rows if r["org"] in CN_ARENA_ORGS]
    if not cn:
        raise ValueError("no Chinese models found on leaderboard")
    best, leader = cn[0], rows[0]
    return {"best_model": best["model"], "best_org": best["org"], "best_rank": best["rank"],
            "best_score": best["elo"], "us_leader": leader["model"], "us_leader_score": leader["elo"],
            "top10": sum(1 for r in cn if r["rank"] <= 10), "top20": sum(1 for r in cn if r["rank"] <= 20),
            "asof": datetime.now(timezone.utc).strftime("%Y-%m-%d")}


def github_velocity(hist):
    """Stars now vs last run -> stars/day across the basket."""
    now = time.time()
    stars = {}
    for repo in GH_REPOS:
        try:
            stars[repo] = jget(f"https://api.github.com/repos/{repo}")["stargazers_count"]
        except Exception as e:
            print(f"  github {repo}: {e}", file=sys.stderr)
    total = sum(stars.values())
    prev = hist.get("github")
    per_day = None
    if prev and prev.get("total") and now - prev["t"] >= 20 * 3600:
        # baseline is old enough to measure a stable rate; measure, then reset it
        per_day = (total - prev["total"]) / ((now - prev["t"]) / 86400)
        hist["github"] = {"t": now, "total": total, "stars": stars}
    elif not prev:
        # seed the baseline once; keep it until it ages past 20h so frequent CI
        # runs don't reset it to "now" every time and never produce a velocity
        hist["github"] = {"t": now, "total": total, "stars": stars}
    return per_day, stars


def yahoo_series(sym):
    d = jget(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=6mo&interval=1d")
    r = d["chart"]["result"][0]
    ts, cl = r["timestamp"], r["indicators"]["quote"][0]["close"]
    return [{"d": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), "c": round(c, 2)}
            for t, c in zip(ts, cl) if c is not None]


def openrouter_week():
    d = jget("https://openrouter.ai/api/frontend/v1/rankings/market-share")["data"]
    last = d[-1]
    cn = sum(v for k, v in last["ys"].items() if k in CN_AUTHORS)
    us = sum(v for k, v in last["ys"].items() if k in US_AUTHORS)
    tot = sum(last["ys"].values())
    return {"week": last["x"], "cn_share": round(cn / tot, 4), "us_share": round(us / tot, 4),
            "spi": round(cn / us, 3) if us else None, "total_tokens": tot}


def run():
    hist_path = HERE / "china-history.json"
    hist = json.loads(hist_path.read_text()) if hist_path.exists() else {}
    out = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "snapshot_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), **MANUAL}

    print("arena.ai leaderboard ...")
    try:
        out["lmarena"] = arena()
        print(f"  best CN: {out['lmarena']['best_model']} #{out['lmarena']['best_rank']}")
    except Exception as e:
        print(f"  FAILED ({e}) - page keeps its embedded snapshot", file=sys.stderr)

    print("github velocity ...")
    per_day, stars = github_velocity(hist)
    if per_day is not None:
        out["github_stars_per_day"] = round(per_day, 1)
        print(f"  +{per_day:.0f} stars/day across basket")
    else:
        print("  baseline stored; velocity available from next run (>20h apart)")

    print("yahoo BABA/KWEB ...")
    try:
        out["equity_extra"] = {s: yahoo_series(s) for s in ("BABA", "KWEB")}
    except Exception as e:
        print(f"  FAILED ({e})", file=sys.stderr)

    print("openrouter weekly share ...")
    try:
        wk = openrouter_week()
        out["openrouter_week"] = wk
        csv_path = HERE / "china-snapshots.csv"
        new = not csv_path.exists()
        with open(csv_path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["date", "or_week", "cn_share", "us_share", "spi", "total_tokens"])
            w.writerow([out["snapshot_date"], wk["week"], wk["cn_share"], wk["us_share"], wk["spi"], wk["total_tokens"]])
        print(f"  CN {wk['cn_share']:.1%} / US {wk['us_share']:.1%} / SPI {wk['spi']}x (wk {wk['week']})")
    except Exception as e:
        print(f"  FAILED ({e})", file=sys.stderr)

    hist_path.write_text(json.dumps(hist))
    (HERE / "china-data.json").write_text(json.dumps(out, indent=1))
    print(f"wrote china-data.json ({out['updated']})")


if __name__ == "__main__":
    if "--watch" in sys.argv:
        every = int(sys.argv[sys.argv.index("--watch") + 1])
        while True:
            try:
                run()
            except Exception as e:
                print(f"run failed: {e}", file=sys.stderr)
            time.sleep(every)
    else:
        run()
