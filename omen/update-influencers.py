#!/usr/bin/env python3
"""Auto-score the KOL board into influencers.json.

Reads the roster (name, org, cat, url handle) below, asks Grok's Live Search to read
each voice's recent public posts, and scores each -100 (max bearish on the AI trade)
… +100 (max bullish) with a one-line evidence-based take. Writes influencers.json,
which the dashboard loads in preference to its inline fallback.

Requires XAI_API_KEY. If it is missing, this script is a no-op (the dashboard keeps
its curated editorial snapshot) — that keeps the whole pipeline degrading gracefully.

Usage:
  XAI_API_KEY=... python3 update-influencers.py
"""
import os, sys, json, datetime, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "influencers.json")
MODEL = os.environ.get("XAI_MODEL", "grok-4")

# Roster: identity is fixed here; score + take are generated. handle drives Live Search.
ROSTER = [
    {"name": "Cathie Wood",          "org": "ARK Invest",          "cat": "tech",  "handle": "CathieDWood",   "url": "https://x.com/CathieDWood"},
    {"name": "Marc Andreessen",      "org": "a16z",                "cat": "tech",  "handle": "pmarca",        "url": "https://x.com/pmarca"},
    {"name": "Dan Ives",             "org": "Wedbush Securities",  "cat": "tech",  "handle": "DivesTech",     "url": "https://x.com/DivesTech"},
    {"name": "Tom Lee",              "org": "Fundstrat",           "cat": "macro", "handle": "fundstrat",     "url": "https://x.com/fundstrat"},
    {"name": "Raoul Pal",            "org": "Real Vision",         "cat": "macro", "handle": "RaoulGMI",      "url": "https://x.com/RaoulGMI"},
    {"name": "David Sacks",          "org": "Craft / AI & crypto czar", "cat": "tech", "handle": "DavidSacks", "url": "https://x.com/DavidSacks"},
    {"name": "Chamath Palihapitiya", "org": "Social Capital",      "cat": "tech",  "handle": "chamath",       "url": "https://x.com/chamath"},
    {"name": "Torsten Sløk",         "org": "Apollo Global",       "cat": "macro", "handle": None,            "url": "https://www.apolloacademy.com/"},
    {"name": "Jim Covello",          "org": "Goldman Sachs",       "cat": "tech",  "handle": None,            "url": "https://www.goldmansachs.com/insights"},
    {"name": "Michael Green",        "org": "Simplify Asset Mgmt", "cat": "macro", "handle": "profplum99",    "url": "https://x.com/profplum99"},
    {"name": "Jim Chanos",           "org": "Chanos & Co.",        "cat": "macro", "handle": "RealJimChanos", "url": "https://x.com/RealJimChanos"},
    {"name": "Gary Marcus",          "org": "NYU / author",        "cat": "tech",  "handle": "GaryMarcus",    "url": "https://x.com/GaryMarcus"},
    {"name": "Michael Burry",        "org": "Scion Asset Mgmt",    "cat": "macro", "handle": "michaeljburry", "url": "https://x.com/michaeljburry"},
    {"name": "Ed Zitron",            "org": "Where's Your Ed At",  "cat": "tech",  "handle": "edzitron",      "url": "https://x.com/edzitron"},
]

RUBRIC = """You score public figures on their current stance toward the "AI trade" —
US-listed AI/semiconductor/AI-infrastructure equities and the AI capex buildout.

Scale (integer): +100 = maximally bullish (AI boom durable, buy every dip); +50 = clearly bullish;
0 = neutral/mixed; -50 = clearly bearish (bubble, capex won't earn its cost); -100 = maximally bearish
(actively short / calls it a fraud). Anchor to their stance on the AI *trade/valuations*, not on AI
capability or their politics.

Return STRICT JSON: {"score": <int -100..100>, "take": "<=160 chars, evidence-based, no hype"}."""


def post(url, payload, headers, timeout=90):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def score_one(person, key):
    who = f'{person["name"]} ({person["org"]})'
    src = f'their recent X/Twitter posts from @{person["handle"]}' if person["handle"] else \
          f'their recent public commentary and interviews'
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": RUBRIC},
            {"role": "user", "content": f"Score {who} based on {src} over roughly the last 60 days. "
                                        f"If you cannot find recent material, infer from their well-known standing stance and say so in the take."},
        ],
        "search_parameters": {"mode": "on", "return_citations": False,
                              "sources": [{"type": "x"}, {"type": "web"}, {"type": "news"}],
                              "from_date": (datetime.date.today() - datetime.timedelta(days=75)).isoformat()},
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    j = post("https://api.x.ai/v1/chat/completions", payload, {"Authorization": f"Bearer {key}"})
    content = j["choices"][0]["message"]["content"]
    obj = json.loads(content)
    score = max(-100, min(100, int(round(float(obj["score"])))))
    take = str(obj.get("take", "")).strip()[:200]
    return score, take


def main():
    key = os.environ.get("XAI_API_KEY")
    if not key:
        print("XAI_API_KEY not set — leaving influencers.json untouched (dashboard uses its curated fallback).")
        return 0
    out = []
    for p in ROSTER:
        try:
            score, take = score_one(p, key)
            out.append({"name": p["name"], "org": p["org"], "cat": p["cat"],
                        "url": p["url"], "score": score, "take": take})
            print(f"{p['name']:>22}: {score:+4d}  {take[:70]}")
        except Exception as e:
            print(f"{p['name']:>22}: FAIL {e}")
    if len(out) < len(ROSTER) * 0.6:
        print(f"Only {len(out)}/{len(ROSTER)} scored — refusing to overwrite influencers.json with a thin roster.")
        return 1
    doc = {"asof": datetime.date.today().isoformat(), "source": f"x.ai Live Search ({MODEL})",
           "influencers": sorted(out, key=lambda x: -x["score"])}
    with open(OUT, "w") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
    print(f"wrote {OUT}: {len(out)} voices, asof {doc['asof']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
