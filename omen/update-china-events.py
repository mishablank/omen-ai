#!/usr/bin/env python3
"""Refresh china-events.json — the *recent* half of the China AI event timeline.

The timeline in china-ai-monitor.html has two halves:

  * a hand-curated historical base (the `TIMELINE` const in the page) — the
    trustworthy, editorially-checked events with precise stock reactions; and
  * a rolling set of *recent* catalysts generated here by Grok Live Search and
    written to china-events.json, which the page merges on top of the base.

The page works with no china-events.json (it just shows the curated base), so
this script fails safe: if XAI_API_KEY is missing, the call errors, or the model
returns nothing usable, the existing file is left untouched — the page never
regresses below the curated timeline.

Guardrails (this is a public, finance-adjacent page — do not put invented numbers
on it):
  * every event carries a source URL from the model's live search;
  * the prompt forbids invented stock-move figures — a specific % only if actually
    reported, else a qualitative note;
  * output is validated (ISO date in-window, non-empty headline) and anything
    malformed is dropped;
  * events are unioned with the previous file (deduped) so a catalyst added last
    week doesn't vanish once it ages out of the search window.

A 24h gate means the (paid) model call runs at most once per ~day even though the
CI cron fires every 30 min; pass --force to override.

Usage:
  XAI_API_KEY=... python3 update-china-events.py
  XAI_API_KEY=... python3 update-china-events.py --force
  python3 update-china-events.py --sample     # write a fixed sample (no API), for UI testing
"""
import json, os, re, sys, urllib.request, datetime
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "china-events.json"
MODEL = os.environ.get("XAI_MODEL", "grok-4")

# The newest curated event in china-ai-monitor.html's TIMELINE const. Recent auto
# events must be strictly AFTER this date so the two halves don't overlap. Keep this
# in sync if you extend the curated base.
SINCE = "2026-07-07"
# how many days of look-back the model searches (>= a comfortable margin past SINCE)
WINDOW_DAYS = 45
# retention: auto events older than this are dropped from the rolling file
RETAIN_DAYS = 150
MAX_EVENTS = 12
# regenerate at most this often (hours) — the CI cron fires every 30 min
MIN_AGE_HOURS = 20

SYSTEM = (
    "You track catalysts in the Chinese AI-model industry (DeepSeek, Qwen/Alibaba, "
    "GLM/Z.ai, Kimi/Moonshot, MiniMax, MiMo/Xiaomi, Hunyuan/Tencent, Ernie/Baidu, "
    "StepFun, ByteDance) and the reaction of US AI-exposed equities (NVDA, SOXX/SMH, "
    "AVGO, MU, ANET, CRWV, ORCL, SMCI, MSFT, META). You are precise and conservative: "
    "you never invent numbers or events."
)


def _prompt(since, today):
    return (
        f"Using live web/news/X search, list the notable Chinese AI-model catalysts "
        f"published strictly after {since} and up to {today} — major model launches "
        f"or benchmark claims, price cuts, open-weight releases, enterprise-switching "
        f"reports, chip/hardware news, and any that visibly moved US AI stocks.\n\n"
        f"Return a JSON object: {{\"events\": [ {{\"d\", \"t\", \"rx\", \"src\"}} ] }}.\n"
        f"  d   = ISO date YYYY-MM-DD the catalyst broke (must be after {since}).\n"
        f"  t   = one-sentence headline of what happened (<= 180 chars, plain text).\n"
        f"  rx  = the US-stock reaction. State a specific % move ONLY if it was actually "
        f"reported by a source; otherwise write a qualitative note like 'no clear "
        f"indexed move'. NEVER invent a number.\n"
        f"  src = a single source URL you actually found for this event.\n\n"
        f"Rules: only include events you can back with a real cited source in the window. "
        f"If unsure, omit it. Order oldest first. Return at most {MAX_EVENTS} events. "
        f"If there are genuinely none, return an empty list."
    )


def post(url, payload, headers, timeout=120):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_events(key, since, today):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": _prompt(since, today)},
        ],
        "search_parameters": {"mode": "on", "return_citations": True,
                              "sources": [{"type": "web"}, {"type": "news"}, {"type": "x"}],
                              "from_date": (datetime.date.fromisoformat(today)
                                            - datetime.timedelta(days=WINDOW_DAYS)).isoformat()},
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    j = post("https://api.x.ai/v1/chat/completions", payload,
             {"Authorization": f"Bearer {key}"})
    content = j["choices"][0]["message"]["content"]
    obj = json.loads(content)
    return obj.get("events", []) if isinstance(obj, dict) else []


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def clean_str(s, n):
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def valid_event(e, since):
    """Return a sanitized event dict, or None if it fails validation."""
    if not isinstance(e, dict):
        return None
    d = clean_str(e.get("d"), 10)
    if not _DATE.match(d):
        return None
    try:
        dt = datetime.date.fromisoformat(d)
    except ValueError:
        return None
    if not (dt > datetime.date.fromisoformat(since)):
        return None                       # must be strictly after the curated base
    if dt > datetime.date.today() + datetime.timedelta(days=1):
        return None                       # no future-dated events
    t = clean_str(e.get("t"), 200)
    if len(t) < 8:
        return None
    src = clean_str(e.get("src"), 300)
    if src and not src.startswith(("http://", "https://")):
        src = ""
    return {"d": d, "t": t, "rx": clean_str(e.get("rx"), 200), "src": src}


def dedupe_merge(existing, fresh, since):
    """Union existing + fresh, dedupe by (date, headline-prefix), retain window, cap."""
    floor = datetime.date.today() - datetime.timedelta(days=RETAIN_DAYS)
    seen, out = set(), []
    for e in list(existing) + list(fresh):
        v = valid_event(e, since)
        if not v or datetime.date.fromisoformat(v["d"]) < floor:
            continue
        key = (v["d"], v["t"][:40].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    out.sort(key=lambda x: x["d"])
    return out[-MAX_EVENTS:]


def load_existing():
    try:
        return json.loads(OUT.read_text())
    except Exception:
        return {}


def is_fresh(doc):
    ts = doc.get("generated_at")
    if not ts:
        return False
    try:
        gen = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    age_h = (datetime.datetime.now(datetime.timezone.utc) - gen).total_seconds() / 3600
    return age_h < MIN_AGE_HOURS


SAMPLE = [
    {"d": "2026-07-16",
     "t": "Kimi K3 (Moonshot) launches — 2.8T-param MoE, 1M context; claims parity with Fable 5, beats Opus 4.8 / GPT-5.6.",
     "rx": "No clear indexed move on day one; open weights promised by Jul 27 — a fresh commoditization/substitution catalyst to watch.",
     "src": "https://techcrunch.com/2026/07/16/moonshots-upcoming-kimi-3-is-expected-to-close-the-gap-with-anthropics-opus-4-8/"},
]


def main():
    args = set(sys.argv[1:])
    today = datetime.date.today().isoformat()
    existing = load_existing()
    existing_events = existing.get("events", [])

    if "--sample" in args:
        events = dedupe_merge(existing_events, SAMPLE, SINCE)
        source = "sample (no API)"
    else:
        key = os.environ.get("XAI_API_KEY")
        if not key:
            print("XAI_API_KEY not set — leaving china-events.json untouched "
                  "(page shows its curated timeline).")
            return 0
        if is_fresh(existing) and "--force" not in args:
            print(f"china-events.json is < {MIN_AGE_HOURS}h old — skipping (use --force).")
            return 0
        try:
            raw = fetch_events(key, SINCE, today)
        except Exception as e:
            print(f"Grok call failed ({e}) — leaving china-events.json untouched.")
            return 0
        fresh = [v for v in (valid_event(e, SINCE) for e in raw) if v]
        print(f"model returned {len(raw)} events, {len(fresh)} valid after filtering.")
        if not fresh and not existing_events:
            print("nothing valid and no prior events — leaving file untouched.")
            return 0
        events = dedupe_merge(existing_events, fresh, SINCE)
        source = f"x.ai Live Search ({MODEL})"

    doc = {
        "asof": today,
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
                         .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": source,
        "since": SINCE,
        "events": events,
    }
    OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUT.name}: {len(events)} recent event(s), asof {today}")
    for e in events:
        print(f"  {e['d']}  {e['t'][:72]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
