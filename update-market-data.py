#!/usr/bin/env python3
"""Fetch the non-Polymarket data feeds for the AI Crash dashboard into market-data.json.

Sources (all free / unauthenticated):
  - Equity closes (Yahoo chart): NVDA, SOXX, CRWV, ORCL
  - Volatility complex (Yahoo chart): ^VXN, ^VIX, ^VIX3M, ^SKEW, ^VVIX
  - Options skew (CBOE delayed quotes): NVDA, SOXX 25-delta risk reversal
  - Credit (Yahoo chart): HYG, LQD, JNK
  - Cross-venue (Kalshi public API): overlapping AI/macro market metadata

Also (optional) appends a chain-linkable snapshot of the three Polymarket indexes to
snapshots.csv so the composite survives constituent turnover across machines.

Usage:
  python3 update-market-data.py                # one shot
  python3 update-market-data.py --watch 600     # refresh every 600s (live mode)
  python3 update-market-data.py --snapshot      # also append Polymarket index snapshot

No third-party dependencies. Run it from the folder that serves the dashboard.
"""
import urllib.request, urllib.error, json, datetime, re, sys, os, time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "market-data.json")
LEGACY = os.path.join(HERE, "equity-data.json")
SNAP = os.path.join(HERE, "snapshots.csv")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

EQUITY = ["NVDA", "SOXX", "CRWV", "ORCL"]
VOL = {"^VXN": "VXN", "^VIX": "VIX", "^VIX3M": "VIX3M", "^SKEW": "SKEW", "^VVIX": "VVIX"}
CREDIT = ["HYG", "LQD", "JNK"]
SKEW_SYMS = ["NVDA", "SOXX"]
# Kalshi series that genuinely overlap the dashboard's themes
KALSHI_SERIES = {
    "KXACQUIREMISTRAL": "AI lab acquisition (Mistral)",
    "KXRECSSNBER": "US recession (macro backdrop)",
    "KXBIGTECHLAYOFF": "Big tech layoffs",
    "KXOAIANTH": "OpenAI vs Anthropic",
    "KXUSOPENAIANTH": "US stake in OpenAI & Anthropic",
}


def get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


def yahoo_series(sym, rng="6mo"):
    j = json.loads(get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"))
    res = j["chart"]["result"][0]
    ts, cl = res["timestamp"], res["indicators"]["quote"][0]["close"]
    return [{"d": datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"), "c": round(c, 2)}
            for t, c in zip(ts, cl) if c is not None]


def cboe_skew(sym):
    """25-delta risk reversal (put IV - call IV) from the nearest expiry >= 25 DTE."""
    j = json.loads(get(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"))
    d = j["data"]
    spot = d.get("current_price")
    today = datetime.date.today()
    rows = []
    for o in d["options"]:
        m = re.match(rf"{sym}(\d{{6}})([CP])(\d{{8}})", o["option"])
        if not m or o.get("iv") in (None, 0) or o.get("delta") is None:
            continue
        exp = datetime.datetime.strptime(m.group(1), "%y%m%d").date()
        rows.append({"dte": (exp - today).days, "type": m.group(2),
                     "iv": o["iv"], "delta": o["delta"]})
    dtes = sorted(set(r["dte"] for r in rows if r["dte"] >= 25))
    if not dtes:
        return None
    dte = dtes[0]
    exp_rows = [r for r in rows if r["dte"] == dte]

    def iv_at(typ, target):
        pts = sorted((abs(r["delta"]), r["iv"]) for r in exp_rows if r["type"] == typ)
        for i in range(len(pts) - 1):
            (d0, v0), (d1, v1) = pts[i], pts[i + 1]
            if d0 <= target <= d1 and d1 != d0:
                return v0 + (v1 - v0) * (target - d0) / (d1 - d0)
        return None

    p25, c25, atm = iv_at("P", 0.25), iv_at("C", 0.25), iv_at("P", 0.50)
    return {"spot": spot, "dte": dte,
            "put25": round(p25, 4) if p25 else None,
            "call25": round(c25, 4) if c25 else None,
            "atm": round(atm, 4) if atm else None,
            "rr": round(p25 - c25, 4) if (p25 and c25) else None,
            "date": today.isoformat()}


def kalshi():
    B = "https://api.elections.kalshi.com/trade-api/v2"
    out = {"authed": False, "note": "Public Kalshi feed exposes market metadata but no quotes; add authenticated access for live cross-venue prices.", "markets": []}
    for st, theme in KALSHI_SERIES.items():
        try:
            j = json.loads(get(B + f"/events?with_nested_markets=true&series_ticker={st}", timeout=20))
        except Exception:
            continue
        for e in j.get("events", []):
            title = e.get("title", "")
            # prefer the current-year / soonest event
            for m in e.get("markets", [])[:1]:
                yb, ya = m.get("yes_bid"), m.get("yes_ask")
                price = None
                if yb is not None and ya is not None:
                    price = (yb + ya) / 2 / 100.0
                elif m.get("last_price") is not None:
                    price = m["last_price"] / 100.0
                out["markets"].append({
                    "theme": theme, "ticker": m.get("ticker"), "title": title,
                    "subtitle": m.get("yes_sub_title") or m.get("subtitle") or "",
                    "price": price, "volume": m.get("volume"),
                    "url": f"https://kalshi.com/markets/{st.lower()}",
                })
            break  # one representative event per series
    if any(x["price"] is not None for x in out["markets"]):
        out["authed"] = True
    return out


def append_snapshot():
    """Compute the 3 Polymarket indexes (equal weight, raw) and append to snapshots.csv."""
    IDS = {
        "bull": ["676829", "653788", "676837", "1087074", "656312", "656313", "2413330", "2109881", "676804", "2487206", "2255930"],
        "crash": ["691340", "676827", "676846"],
        "reg": ["2787889", "2787891", "2787890", "2698575", "676842", "2839991"],
    }
    allids = [i for v in IDS.values() for i in v]
    qs = "&".join("id=" + i for i in allids)
    try:
        arr = json.loads(get(f"https://gamma-api.polymarket.com/markets?{qs}&limit={len(allids)}"))
    except Exception as e:
        print("  snapshot skipped:", e)
        return
    price = {}
    for m in arr:
        if m.get("closed"):
            continue
        price[str(m["id"])] = float(json.loads(m.get("outcomePrices") or '["0"]')[0])
    row = {"date": datetime.date.today().isoformat()}
    for side, ids in IDS.items():
        vals = [price[i] for i in ids if i in price]
        row[side] = round(sum(vals) / len(vals) * 100, 2) if vals else ""
        row[side + "_n"] = len(vals)
    row["comp"] = ",".join(sorted(price.keys()))
    header = ["date", "bull", "bull_n", "crash", "crash_n", "reg", "reg_n", "comp"]
    existing = {}
    if os.path.exists(SNAP):
        with open(SNAP) as f:
            for i, line in enumerate(f):
                if i == 0:
                    continue
                parts = line.rstrip("\n").split(",", len(header) - 1)
                if parts and parts[0]:
                    existing[parts[0]] = line.rstrip("\n")
    existing[row["date"]] = ",".join(str(row[h]) for h in header)
    with open(SNAP, "w") as f:
        f.write(",".join(header) + "\n")
        for d in sorted(existing):
            f.write(existing[d] + "\n")
    print(f"  snapshot: bull {row['bull']} crash {row['crash']} reg {row['reg']} -> {SNAP}")


def build():
    prev = {}
    if os.path.exists(OUT):
        try:
            with open(OUT) as f:
                prev = json.load(f)
        except Exception:
            prev = {}
    now = datetime.datetime.utcnow()
    data = {"updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updated_date": now.strftime("%Y-%m-%d"),
            "equity": {}, "vol": {}, "skew": {}, "credit": {}, "kalshi": {}}

    for sym in EQUITY:
        try:
            data["equity"][sym] = yahoo_series(sym)
            print(f"equity {sym}: {len(data['equity'][sym])} pts")
        except Exception as e:
            print(f"equity {sym}: FAIL {e}")

    for ysym, name in VOL.items():
        try:
            s = yahoo_series(ysym, "3mo")
            data["vol"][name] = {"last": s[-1]["c"], "series": s}
            print(f"vol {name}: {s[-1]['c']}")
        except Exception as e:
            print(f"vol {name}: FAIL {e}")

    for sym in SKEW_SYMS:
        try:
            sk = cboe_skew(sym)
            if sk:
                # carry forward prior history so skew *change* is observable across runs
                hist = (prev.get("skew", {}).get(sym, {}) or {}).get("history", [])
                hist = [h for h in hist if h["date"] != sk["date"]]
                hist.append({"date": sk["date"], "rr": sk["rr"], "atm": sk["atm"]})
                sk["history"] = hist[-120:]
                data["skew"][sym] = sk
                print(f"skew {sym}: RR={sk['rr']} (dte {sk['dte']})")
        except Exception as e:
            print(f"skew {sym}: FAIL {e}")

    for sym in CREDIT:
        try:
            data["credit"][sym] = yahoo_series(sym)
            print(f"credit {sym}: {data['credit'][sym][-1]['c']}")
        except Exception as e:
            print(f"credit {sym}: FAIL {e}")

    try:
        data["kalshi"] = kalshi()
        print(f"kalshi: {len(data['kalshi']['markets'])} markets, authed={data['kalshi']['authed']}")
    except Exception as e:
        print(f"kalshi: FAIL {e}")
        data["kalshi"] = {"authed": False, "markets": [], "note": str(e)}

    with open(OUT, "w") as f:
        json.dump(data, f)
    # legacy file the older dashboard expected
    with open(LEGACY, "w") as f:
        json.dump({"updated": data["updated_date"],
                   "series": {k: data["equity"].get(k, []) for k in ("NVDA", "SOXX")}}, f)
    print("written:", OUT)
    return data


def main():
    args = sys.argv[1:]
    do_snap = "--snapshot" in args
    watch = None
    if "--watch" in args:
        i = args.index("--watch")
        watch = int(args[i + 1]) if i + 1 < len(args) and args[i + 1].isdigit() else 600
    while True:
        try:
            build()
            if do_snap:
                append_snapshot()
        except Exception as e:
            print("build error:", e)
        if not watch:
            break
        print(f"sleeping {watch}s (Ctrl-C to stop)…")
        time.sleep(watch)


if __name__ == "__main__":
    main()
