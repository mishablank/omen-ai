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
  - Google Trends search interest  : Chinese apps' share of US AI-assistant searches
                                     (unofficial API; datacenter IPs often 429 -> the
                                     previous trends value is carried forward)

Artificial Analysis and consumer-app figures have no scrapeable source; edit
MANUAL below when you refresh them by hand.

Usage:
  python3 update-china-data.py                # one shot
  python3 update-china-data.py --watch 86400  # daily loop
After running, redeploy the site folder so the deployed china-data.json updates.
"""
import json, re, csv, html, sys, time, urllib.request, urllib.parse, urllib.error, http.cookiejar
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) omen-china-monitor/1.0"}

# ---- fields with no machine-readable source: update by hand when re-verified ----
MANUAL = {
    "artificial_analysis": {"cn_best": "GLM-5.2", "cn_score": 51,
                            "us_best": "Claude Fable 5", "us_score": 60, "asof": "2026-07-12"},
    # static fallback only - google_trends() overrides this when it succeeds
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

# Hugging Face download share. Fetched here server-side because HF's API is not
# reliably CORS-open to arbitrary browser origins (the live site's client fetch is
# blocked), so the page reads these totals from china-data.json instead.
HF_CN_ORGS = ["deepseek-ai", "Qwen", "moonshotai", "zai-org", "MiniMaxAI", "tencent", "XiaomiMiMo", "stepfun-ai"]
HF_US_ORGS = ["meta-llama", "openai", "google", "microsoft", "nvidia", "allenai"]
HF_US_FAMILY = re.compile(r"llama|gemma|gpt-oss|phi-|phi\d|nemotron|olmo|granite|dbrx", re.I)


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


def huggingface():
    """30-day download totals by lab. Returns the shape the page's renderHF expects."""
    def org_repos(org):
        d = jget(f"https://huggingface.co/api/models?author={org}&sort=downloads&direction=-1&limit=50")
        return [{"id": m["id"], "dl": m.get("downloads") or 0} for m in d]
    cn_orgs, us_orgs = [], []
    for org in HF_CN_ORGS:
        try:
            dl = sum(m["dl"] for m in org_repos(org))
            if dl:
                cn_orgs.append({"org": org, "dl": dl})
        except Exception as e:
            print(f"  hf {org}: {e}", file=sys.stderr)
    for org in HF_US_ORGS:
        try:
            dl = sum(m["dl"] for m in org_repos(org) if HF_US_FAMILY.search(m["id"]))
            if dl:
                us_orgs.append({"org": org, "dl": dl})
        except Exception as e:
            print(f"  hf {org}: {e}", file=sys.stderr)
    cn = sum(o["dl"] for o in cn_orgs)
    us = sum(o["dl"] for o in us_orgs)
    if not cn or not us:
        raise ValueError("HF returned no usable data on one side")
    cn_orgs.sort(key=lambda o: -o["dl"])
    us_orgs.sort(key=lambda o: -o["dl"])
    return {"cnOrgs": cn_orgs, "usOrgs": us_orgs, "cn": cn, "us": us}


def openrouter_week():
    d = jget("https://openrouter.ai/api/frontend/v1/rankings/market-share")["data"]
    last = d[-1]
    cn = sum(v for k, v in last["ys"].items() if k in CN_AUTHORS)
    us = sum(v for k, v in last["ys"].items() if k in US_AUTHORS)
    tot = sum(last["ys"].values())
    return {"week": last["x"], "cn_share": round(cn / tot, 4), "us_share": round(us / tot, 4),
            "spi": round(cn / us, 3) if us else None, "total_tokens": tot}


# Google Trends: Chinese apps' share of US search interest across the main AI
# assistants. The unofficial API caps a comparison at 5 terms, so terms are
# fetched in batches that all share the anchor (ChatGPT) and are rescaled onto
# batch 0's scale via the anchor's ratio.
# "Llama AI" not bare "Llama": the bare string is dominated by the animal.
# "Meta AI" captures the consumer assistant product built on Llama.
TRENDS_TERMS_US = ["ChatGPT", "Gemini", "Claude", "Llama AI", "Meta AI"]
# "Kimi AI"/"GLM AI" not bare "Kimi"/"GLM": the bare strings are dominated by
# homonyms in US search (Kimi Antonelli/Raikkonen, generalized linear models).
TRENDS_TERMS_CN = ["DeepSeek", "Qwen", "Kimi AI", "GLM AI", "MiMo", "MiniMax", "Seed 1.6", "Seed 2.0"]
TRENDS_GEO = "US"
TRENDS_WINDOW = "today 3-m"
TRENDS_BATCH = 5


def parse_gjson(raw):
    """Google APIs prefix JSON bodies with an anti-scrape garbage line."""
    return json.loads(raw[raw.index(b"{"):])


def trends_batches():
    """Split US+CN terms into <=5-term batches, each led by the shared anchor."""
    anchor = TRENDS_TERMS_US[0]
    terms = TRENDS_TERMS_US + TRENDS_TERMS_CN
    batches, rest = [terms[:TRENDS_BATCH]], terms[TRENDS_BATCH:]
    while rest:
        batches.append([anchor] + rest[:TRENDS_BATCH - 1])
        rest = rest[TRENDS_BATCH - 1:]
    return batches


def trends_avgs(timeline, n):
    """timelineData -> per-term mean interest over the window."""
    if not timeline:
        raise ValueError("no timeline points")
    return [sum(p["value"][i] for p in timeline) / len(timeline) for i in range(n)]


def merge_anchored(batches, avg_lists):
    """Rescale every batch onto batch 0's scale via the shared anchor term."""
    merged = dict(zip(batches[0], avg_lists[0]))
    for terms, avgs in zip(batches[1:], avg_lists[1:]):
        if avgs[0] <= 0:
            raise ValueError(f"anchor has zero interest in batch {terms}")
        scale = avg_lists[0][0] / avgs[0]
        merged.update({t: a * scale for t, a in zip(terms[1:], avgs[1:])})
    return merged


def trends_search_consumer(merged):
    """Merged per-term averages -> search_consumer dict (share = CN / total)."""
    us = sum(merged[t] for t in TRENDS_TERMS_US)
    cn = sum(merged[t] for t in TRENDS_TERMS_CN)
    if us + cn <= 0:
        raise ValueError("all-zero interest")
    pct = round(cn / (us + cn) * 100, 1)
    return {"western_share_pct": pct,
            "asof": datetime.now(timezone.utc).strftime("%Y-%m"),
            "source": "google-trends",
            "terms_us": TRENDS_TERMS_US, "terms_cn": TRENDS_TERMS_CN,
            "note": f"Google Trends {TRENDS_GEO}, 90-day avg: {len(TRENDS_TERMS_CN)} Chinese AI terms = "
                    f"{pct}% of AI-assistant search interest vs {'+'.join(TRENDS_TERMS_US)}."}


def google_trends():
    """Fetch interest-over-time via the unofficial explore -> multiline flow.
    Needs the homepage NID cookie (/trends/explore itself 429s cookie-less bots)."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def g(url):
        return opener.open(urllib.request.Request(url, headers=UA), timeout=30).read()

    def fetch_batch(terms):
        req = {"comparisonItem": [{"keyword": t, "geo": TRENDS_GEO, "time": TRENDS_WINDOW}
                                  for t in terms],
               "category": 0, "property": ""}
        q = urllib.parse.urlencode({"hl": "en-US", "tz": "0", "req": json.dumps(req)})
        raw = None
        for wait in (0, 5, 20):  # explore 429s readily; back off before giving up
            if wait:
                time.sleep(wait)
            try:
                raw = g(f"https://trends.google.com/trends/api/explore?{q}")
                break
            except urllib.error.HTTPError as e:
                if e.code != 429 or wait == 20:
                    raise
        widget = next(w for w in parse_gjson(raw)["widgets"] if w["id"] == "TIMESERIES")
        q2 = urllib.parse.urlencode({"hl": "en-US", "tz": "0",
                                     "req": json.dumps(widget["request"]), "token": widget["token"]})
        raw2 = g(f"https://trends.google.com/trends/api/widgetdata/multiline?{q2}")
        return trends_avgs(parse_gjson(raw2)["default"]["timelineData"], len(terms))

    g("https://trends.google.com/?geo=US")
    batches = trends_batches()
    avg_lists = []
    for i, terms in enumerate(batches):
        if i:
            time.sleep(3)  # be gentle - each batch is an explore + multiline pair
        avg_lists.append(fetch_batch(terms))
    return trends_search_consumer(merge_anchored(batches, avg_lists))


def pick_search_consumer(fresh, prev, manual):
    """Fresh trends value, else the previous run's trends value, else MANUAL."""
    if fresh:
        return fresh
    prev_sc = (prev or {}).get("search_consumer", {})
    if prev_sc.get("source") == "google-trends":
        return prev_sc
    return manual


def run():
    hist_path = HERE / "china-history.json"
    hist = json.loads(hist_path.read_text()) if hist_path.exists() else {}
    data_path = HERE / "china-data.json"
    prev = json.loads(data_path.read_text()) if data_path.exists() else {}
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

    print("hugging face downloads ...")
    try:
        out["hf"] = huggingface()
        print(f"  CN {out['hf']['cn']/1e6:.0f}M vs US {out['hf']['us']/1e6:.0f}M / 30d")
    except Exception as e:
        print(f"  FAILED ({e}) - page falls back to its client fetch", file=sys.stderr)

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

    print("google trends search interest ...")
    fresh = None
    try:
        fresh = google_trends()
        print(f"  CN {fresh['western_share_pct']}% of US AI-assistant search interest")
    except Exception as e:
        print(f"  FAILED ({e}) - carrying previous trends value or manual snapshot", file=sys.stderr)
    out["search_consumer"] = pick_search_consumer(fresh, prev, MANUAL["search_consumer"])

    hist_path.write_text(json.dumps(hist))
    data_path.write_text(json.dumps(out, indent=1))
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
