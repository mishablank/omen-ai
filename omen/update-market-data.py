#!/usr/bin/env python3
"""Fetch the non-Polymarket data feeds for the AI Crash dashboard into market-data.json.

Sources (all free / unauthenticated):
  - Equity closes (Yahoo chart): NVDA, SOXX, AI-capex basket, SPY benchmark
  - Volatility complex (Yahoo chart): ^VXN, ^VIX, ^VIX3M, ^SKEW, ^VVIX
  - Options skew + IV term structure (CBOE delayed quotes): NVDA, SOXX
  - Credit proxies (Yahoo chart): HYG, LQD, JNK
  - Credit spreads (FRED, keyless CSV): HY OAS, CCC OAS, NFCI
  - Cross-venue (Kalshi public API + Manifold public API)
  - Insider activity (SEC EDGAR Form 4): NVDA, AVGO, ORCL, CRWV
  - Realized GPU spot rent (vast.ai public bundles API): H100 SXM $/GPU-hr

Also:
  --snapshot   append a chain-linkable snapshot (3 Polymarket indexes + gauge) to snapshots.csv
  --alert      compute the crash-pressure gauge server-side and push a Telegram/ntfy
               notification when the regime escalates (state kept in alert-state.json)
  --watch N    refresh every N seconds

Env for --alert (all optional): TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID, and/or NTFY_TOPIC.

No third-party dependencies. Run it from the folder that serves the dashboard.
"""
import urllib.request, urllib.error, urllib.parse, json, datetime, re, sys, os, time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "market-data.json")
SNAP = os.path.join(HERE, "snapshots.csv")
ALERT_STATE = os.path.join(HERE, "alert-state.json")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
SEC_UA = {"User-Agent": "Mikhail Blank blank.mikhail@gmail.com"}

CORE = ["NVDA", "SOXX", "CRWV", "ORCL"]
# breadth basket: hyperscalers, semis, networking, power, neoclouds
BASKET = ["NVDA", "MSFT", "GOOGL", "AMZN", "META", "AVGO", "MU", "ANET",
          "VST", "CEG", "NBIS", "IREN", "CRWV", "ORCL", "SMCI"]
BENCH = ["SPY"]
EQUITY = sorted(set(CORE + BASKET + BENCH))
VOL = {"^VXN": "VXN", "^VIX": "VIX", "^VIX3M": "VIX3M", "^SKEW": "SKEW", "^VVIX": "VVIX"}
CREDIT = ["HYG", "LQD", "JNK"]
FRED = {"BAMLH0A0HYM2": "HY_OAS", "BAMLH0A3HYC": "CCC_OAS", "NFCI": "NFCI"}
SKEW_SYMS = ["NVDA", "SOXX"]
INSIDER_TICKERS = ["NVDA", "AVGO", "ORCL", "CRWV"]
KALSHI_SERIES = {
    "KXACQUIREMISTRAL": "AI lab acquisition (Mistral)",
    "KXRECSSNBER": "US recession (macro backdrop)",
    "KXBIGTECHLAYOFF": "Big tech layoffs",
    "KXOAIANTH": "OpenAI vs Anthropic",
    "KXUSOPENAIANTH": "US stake in OpenAI & Anthropic",
}
MANIFOLD_TERMS = ["AI bubble", "NVIDIA crash", "AI winter"]
POLY_IDS = {
    "bull": ["676829", "653788", "676837", "1087074", "656312", "656313", "2413330", "2109881", "676804", "2487206", "2255930"],
    "crash": ["691340", "676827", "676846"],
    "reg": ["2787889", "2787891", "2787890", "2698575", "676842", "2839991"],
}
BUBBLE_ID = "691340"


def get(url, timeout=25, headers=None, data=None):
    req = urllib.request.Request(url, headers=headers or UA,
                                 data=data.encode() if isinstance(data, str) else data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


def yahoo_series(sym, rng="6mo"):
    j = json.loads(get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"))
    res = j["chart"]["result"][0]
    ts, cl = res["timestamp"], res["indicators"]["quote"][0]["close"]
    return [{"d": datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"), "c": round(c, 2)}
            for t, c in zip(ts, cl) if c is not None]


# ---------- FRED (keyless CSV endpoint) ----------
def parse_fred_csv(text, keep=140):
    """fredgraph.csv -> [{'d': iso date, 'c': float}], skipping missing ('.') observations."""
    out = []
    for line in text.strip().split("\n")[1:]:
        parts = line.split(",")
        if len(parts) != 2 or parts[1] in (".", ""):
            continue
        try:
            out.append({"d": parts[0], "c": float(parts[1])})
        except ValueError:
            continue
    return out[-keep:]


def fred_series(series_id):
    # FRED tarpits browser-spoofing UAs on this endpoint; an honest UA responds instantly
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    return parse_fred_csv(get(url, headers={"User-Agent": "ai-crash-monitor/1.0 (blank.mikhail@gmail.com)"}))


# ---------- CBOE: 25d risk reversal + IV term structure ----------
def cboe_options(sym):
    j = json.loads(get(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"))
    d = j["data"]
    today = datetime.date.today()
    rows = []
    for o in d["options"]:
        m = re.match(rf"{sym}(\d{{6}})([CP])(\d{{8}})", o["option"])
        if not m or o.get("iv") in (None, 0) or o.get("delta") is None:
            continue
        exp = datetime.datetime.strptime(m.group(1), "%y%m%d").date()
        rows.append({"dte": (exp - today).days, "type": m.group(2), "iv": o["iv"], "delta": o["delta"]})
    return d.get("current_price"), rows


def iv_at(exp_rows, typ, target):
    pts = sorted((abs(r["delta"]), r["iv"]) for r in exp_rows if r["type"] == typ)
    for i in range(len(pts) - 1):
        (d0, v0), (d1, v1) = pts[i], pts[i + 1]
        if d0 <= target <= d1 and d1 != d0:
            return v0 + (v1 - v0) * (target - d0) / (d1 - d0)
    return None


def cboe_skew_and_term(sym):
    spot, rows = cboe_options(sym)
    today = datetime.date.today().isoformat()
    dtes = sorted(set(r["dte"] for r in rows if r["dte"] >= 25))
    if not dtes:
        return None, None
    front = dtes[0]
    front_rows = [r for r in rows if r["dte"] == front]
    p25, c25, atm_f = iv_at(front_rows, "P", 0.25), iv_at(front_rows, "C", 0.25), iv_at(front_rows, "P", 0.50)
    skew = {"spot": spot, "dte": front,
            "put25": round(p25, 4) if p25 else None,
            "call25": round(c25, 4) if c25 else None,
            "atm": round(atm_f, 4) if atm_f else None,
            "rr": round(p25 - c25, 4) if (p25 and c25) else None,
            "date": today}
    term = None
    backs = [d for d in dtes if d >= 80]
    if backs and atm_f:
        back = backs[0]
        atm_b = iv_at([r for r in rows if r["dte"] == back], "P", 0.50)
        if atm_b:
            term = {"front_dte": front, "back_dte": back,
                    "iv_front": round(atm_f, 4), "iv_back": round(atm_b, 4),
                    "ratio": round(atm_f / atm_b, 4), "date": today}
    return skew, term


# ---------- Kalshi ----------
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
            for m in e.get("markets", [])[:1]:
                yb, ya = m.get("yes_bid"), m.get("yes_ask")
                price = None
                if yb is not None and ya is not None and (yb or ya):
                    price = (yb + ya) / 2 / 100.0
                elif m.get("last_price"):
                    price = m["last_price"] / 100.0
                out["markets"].append({
                    "theme": theme, "ticker": m.get("ticker"), "title": title,
                    "subtitle": m.get("yes_sub_title") or m.get("subtitle") or "",
                    "price": price, "volume": m.get("volume"),
                    "url": f"https://kalshi.com/markets/{st.lower()}",
                })
            break
    if any(x["price"] is not None for x in out["markets"]):
        out["authed"] = True
    return out


# ---------- Manifold ----------
def manifold():
    seen, out = set(), []
    for term in MANIFOLD_TERMS:
        try:
            j = json.loads(get("https://api.manifold.markets/v0/search-markets?"
                               + urllib.parse.urlencode({"term": term, "limit": 8, "sort": "liquidity"})))
        except Exception:
            continue
        for m in j:
            if (m.get("isResolved") or m.get("outcomeType") != "BINARY"
                    or m.get("token") != "MANA" or m["id"] in seen):
                continue
            if (m.get("uniqueBettorCount") or 0) < 15:
                continue
            seen.add(m["id"])
            out.append({"theme": term, "title": m["question"], "url": m["url"],
                        "price": round(m["probability"], 3),
                        "bettors": m.get("uniqueBettorCount"),
                        "closeTime": m.get("closeTime")})
    out.sort(key=lambda x: -(x["bettors"] or 0))
    return out[:8]


# ---------- SEC EDGAR Form 4 insider activity ----------
def parse_form4_xml(xml_text):
    """Return (sells_usd, buys_usd) for open-market S/P transactions in one Form 4."""
    sells = buys = 0.0
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return 0.0, 0.0
    for tx in root.iter("nonDerivativeTransaction"):
        code = tx.findtext("./transactionCoding/transactionCode")
        if code not in ("S", "P"):
            continue
        sh = tx.findtext("./transactionAmounts/transactionShares/value")
        px = tx.findtext("./transactionAmounts/transactionPricePerShare/value")
        try:
            usd = float(sh) * float(px)
        except (TypeError, ValueError):
            continue
        if code == "S":
            sells += usd
        else:
            buys += usd
    return sells, buys


def edgar_insiders(days=90, per_ticker=15):
    try:
        tickers = json.loads(get("https://www.sec.gov/files/company_tickers.json", headers=SEC_UA))
    except Exception as e:
        print("edgar tickers: FAIL", e)
        return {}
    cik = {v["ticker"]: f"{v['cik_str']:010d}" for v in tickers.values()}
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    out = {}
    for sym in INSIDER_TICKERS:
        if sym not in cik:
            continue
        try:
            sub = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik[sym]}.json", headers=SEC_UA))
            r = sub["filings"]["recent"]
            accs = [(r["accessionNumber"][i], r["filingDate"][i])
                    for i, f in enumerate(r["form"]) if f == "4" and r["filingDate"][i] >= cutoff][:per_ticker]
            sells = buys = 0.0
            n = 0
            for acc, _date in accs:
                acc_nodash = acc.replace("-", "")
                base = f"https://www.sec.gov/Archives/edgar/data/{int(cik[sym])}/{acc_nodash}"
                try:
                    idx = json.loads(get(f"{base}/index.json", headers=SEC_UA, timeout=15))
                    xml_name = next((it["name"] for it in idx["directory"]["item"]
                                     if it["name"].endswith(".xml") and not it["name"].startswith("primary_doc")), None) \
                        or next((it["name"] for it in idx["directory"]["item"] if it["name"].endswith(".xml")), None)
                    if not xml_name:
                        continue
                    s, b = parse_form4_xml(get(f"{base}/{xml_name}", headers=SEC_UA, timeout=15))
                    sells += s; buys += b; n += 1
                except Exception:
                    continue
                time.sleep(0.15)  # stay well under SEC's 10 req/s
            out[sym] = {"window_days": days, "n_filings": len(accs), "n_parsed": n,
                        "sells_usd": round(sells), "buys_usd": round(buys),
                        "net_usd": round(buys - sells)}
            print(f"insiders {sym}: {len(accs)} filings, net ${out[sym]['net_usd']:,}")
        except Exception as e:
            print(f"insiders {sym}: FAIL {e}")
    return out


# ---------- vast.ai realized H100 spot rent ----------
def gpu_spot(prev):
    q = json.dumps({"gpu_name": {"eq": "H100 SXM"}, "rentable": {"eq": True},
                    "type": "ask", "limit": 200, "order": [["dph_total", "asc"]]})
    j = json.loads(get("https://console.vast.ai/api/v0/bundles", data=q,
                       headers={**UA, "Content-Type": "application/json"}))
    per_gpu = sorted(o["dph_total"] / o["num_gpus"] for o in j.get("offers", [])
                     if o.get("num_gpus") and o.get("dph_total"))
    if not per_gpu:
        return None
    n = len(per_gpu)
    med = per_gpu[n // 2] if n % 2 else (per_gpu[n // 2 - 1] + per_gpu[n // 2]) / 2
    today = datetime.date.today().isoformat()
    hist = [h for h in (prev or {}).get("history", []) if h["date"] != today]
    hist.append({"date": today, "median": round(med, 3), "p10": round(per_gpu[n // 10], 3)})
    return {"source": "vast.ai H100 SXM asks", "date": today, "n_offers": n,
            "median_dph": round(med, 3), "min_dph": round(per_gpu[0], 3),
            "p10_dph": round(per_gpu[n // 10], 3), "history": hist[-365:]}


# ---------- server-side gauge (mirrors the dashboard; pred family = bubble only) ----------
def sc(x, lo, hi):
    if x is None:
        return None
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))


def drawdown(series):
    if not series:
        return None
    hi = max(p["c"] for p in series)
    return (series[-1]["c"] / hi - 1) * 100


def mean_or_none(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def poly_prices():
    allids = [i for v in POLY_IDS.values() for i in v]
    qs = "&".join("id=" + i for i in allids)
    arr = json.loads(get(f"https://gamma-api.polymarket.com/markets?{qs}&limit={len(allids)}"))
    price = {}
    for m in arr:
        if m.get("closed"):
            continue
        price[str(m["id"])] = float(json.loads(m.get("outcomePrices") or '["0"]')[0])
    return price


def compute_gauge(data, price):
    bubble = price.get(BUBBLE_ID)
    nrr = (data.get("skew", {}).get("NVDA") or {}).get("rr")
    srr = (data.get("skew", {}).get("SOXX") or {}).get("rr")
    fam = {
        "pred": mean_or_none([sc(bubble * 100 if bubble is not None else None, 0, 40)]),
        "opt": mean_or_none([sc(nrr * 100 if nrr is not None else None, 1, 10),
                             sc(srr * 100 if srr is not None else None, 4, 15)]),
    }
    V = data.get("vol", {})
    ts = (V["VIX"]["last"] / V["VIX3M"]["last"]) if V.get("VIX") and V.get("VIX3M") else None
    fam["vol"] = mean_or_none([sc(ts, 0.82, 1.05), sc((V.get("VXN") or {}).get("last"), 18, 40),
                               sc((V.get("SKEW") or {}).get("last"), 115, 160),
                               sc((V.get("VVIX") or {}).get("last"), 90, 130)])
    C, F = data.get("credit", {}), data.get("fred", {})
    hyig = None
    if C.get("HYG") and C.get("LQD"):
        r = [p["c"] / q["c"] for p, q in zip(C["HYG"], C["LQD"])]
        if r:
            hyig = (r[-1] / max(r) - 1) * 100
    hyg_dd = drawdown(C.get("HYG"))
    oas = (F.get("HY_OAS") or {}).get("last")
    ccc = (F.get("CCC_OAS") or {}).get("last")
    fam["credit"] = mean_or_none([sc(-hyg_dd if hyg_dd is not None else None, 0, 8),
                                  sc(-hyig if hyig is not None else None, 0, 6),
                                  sc(oas, 2.5, 5.0), sc(ccc, 8.5, 14.0)])
    E = data.get("equity", {})
    ndd = drawdown(E.get("NVDA")) if E.get("NVDA") else None
    sdd = drawdown(E.get("SOXX")) if E.get("SOXX") else None
    fam["equity"] = mean_or_none([sc(-ndd if ndd is not None else None, 0, 50),
                                  sc(-sdd if sdd is not None else None, 0, 40)])
    score = mean_or_none(list(fam.values()))
    return score, fam


def compute_regime(gauge, price):
    bubble = (price.get(BUBBLE_ID) or 0) * 100
    crash_vals = [price[i] for i in POLY_IDS["crash"] if i in price]
    level = sum(crash_vals) / len(crash_vals) * 100 if crash_vals else 0
    if (gauge is not None and gauge >= 55) or level >= 40 or bubble >= 25:
        return "stressed"
    if (gauge is not None and gauge >= 35) or level >= 25 or bubble >= 15:
        return "elevated"
    return "calm"


# ---------- snapshots ----------
def append_snapshot(data=None):
    try:
        price = poly_prices()
    except Exception as e:
        print("  snapshot skipped:", e)
        return
    row = {"date": datetime.date.today().isoformat()}
    for side, ids in POLY_IDS.items():
        vals = [price[i] for i in ids if i in price]
        row[side] = round(sum(vals) / len(vals) * 100, 2) if vals else ""
        row[side + "_n"] = len(vals)
    gauge = ""
    if data:
        g, _ = compute_gauge(data, price)
        gauge = round(g, 1) if g is not None else ""
    row["gauge"] = gauge
    row["comp"] = ",".join(sorted(price.keys()))
    header = ["date", "bull", "bull_n", "crash", "crash_n", "reg", "reg_n", "gauge", "comp"]
    existing = {}
    if os.path.exists(SNAP):
        with open(SNAP) as f:
            lines = f.read().strip().split("\n")
        old_header = lines[0].split(",") if lines else []
        for line in lines[1:]:
            parts = line.split(",", len(old_header) - 1)
            if parts and parts[0]:
                d = dict(zip(old_header, parts))
                existing[parts[0]] = ",".join(str(d.get(h, "")) for h in header)
    existing[row["date"]] = ",".join(str(row[h]) for h in header)
    with open(SNAP, "w") as f:
        f.write(",".join(header) + "\n")
        for d in sorted(existing):
            f.write(existing[d] + "\n")
    print(f"  snapshot: bull {row['bull']} crash {row['crash']} reg {row['reg']} gauge {gauge} -> {SNAP}")


# ---------- alerting ----------
def send_alert(title, body):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    sent = False
    if tok and chat:
        try:
            get(f"https://api.telegram.org/bot{tok}/sendMessage",
                data=urllib.parse.urlencode({"chat_id": chat, "text": f"{title}\n{body}"}))
            sent = True
        except Exception as e:
            print("telegram alert failed:", e)
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=body.encode(),
                                         headers={"Title": title, "Priority": "high"})
            urllib.request.urlopen(req, timeout=15).read()
            sent = True
        except Exception as e:
            print("ntfy alert failed:", e)
    if not sent:
        print("alert (no channel configured):", title, "|", body)


def check_alert(data):
    try:
        price = poly_prices()
    except Exception as e:
        print("alert skipped:", e)
        return
    gauge, fam = compute_gauge(data, price)
    regime = compute_regime(gauge, price)
    prev = {}
    if os.path.exists(ALERT_STATE):
        try:
            prev = json.load(open(ALERT_STATE))
        except Exception:
            prev = {}
    rank = {"calm": 0, "elevated": 1, "stressed": 2}
    if rank[regime] > rank.get(prev.get("regime", "calm"), 0):
        gtxt = f"{gauge:.0f}" if gauge is not None else "?"
        bubble = (price.get(BUBBLE_ID) or 0) * 100
        send_alert(f"AI Crash Monitor: regime -> {regime.upper()}",
                   f"Gauge {gtxt}/100 · bubble market {bubble:.1f}% · families: "
                   + ", ".join(f"{k} {v:.0f}" for k, v in fam.items() if v is not None))
    json.dump({"regime": regime, "gauge": gauge, "at": datetime.datetime.utcnow().isoformat() + "Z"},
              open(ALERT_STATE, "w"))
    print(f"regime: {regime} (gauge {gauge and round(gauge, 1)})")


# ---------- build ----------
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
            "basket": BASKET, "bench": "SPY",
            "equity": {}, "vol": {}, "skew": {}, "term": {}, "credit": {},
            "fred": {}, "kalshi": {}, "manifold": [], "insiders": {}, "gpu": None}

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
            sk, term = cboe_skew_and_term(sym)
            if sk:
                hist = (prev.get("skew", {}).get(sym, {}) or {}).get("history", [])
                hist = [h for h in hist if h["date"] != sk["date"]]
                hist.append({"date": sk["date"], "rr": sk["rr"], "atm": sk["atm"]})
                sk["history"] = hist[-120:]
                data["skew"][sym] = sk
                print(f"skew {sym}: RR={sk['rr']} (dte {sk['dte']})")
            if term:
                thist = (prev.get("term", {}).get(sym, {}) or {}).get("history", [])
                thist = [h for h in thist if h["date"] != term["date"]]
                thist.append({"date": term["date"], "ratio": term["ratio"]})
                term["history"] = thist[-120:]
                data["term"][sym] = term
                print(f"term {sym}: {term['ratio']} ({term['front_dte']}d/{term['back_dte']}d)")
        except Exception as e:
            print(f"skew {sym}: FAIL {e}")

    for sym in CREDIT:
        try:
            data["credit"][sym] = yahoo_series(sym)
            print(f"credit {sym}: {data['credit'][sym][-1]['c']}")
        except Exception as e:
            print(f"credit {sym}: FAIL {e}")

    for series_id, name in FRED.items():
        try:
            s = fred_series(series_id)
            data["fred"][name] = {"last": s[-1]["c"], "last_date": s[-1]["d"], "series": s}
            print(f"fred {name}: {s[-1]['c']} ({s[-1]['d']})")
        except Exception as e:
            print(f"fred {name}: FAIL {e}")

    try:
        data["kalshi"] = kalshi()
        print(f"kalshi: {len(data['kalshi']['markets'])} markets, authed={data['kalshi']['authed']}")
    except Exception as e:
        print(f"kalshi: FAIL {e}")
        data["kalshi"] = {"authed": False, "markets": [], "note": str(e)}

    try:
        data["manifold"] = manifold()
        print(f"manifold: {len(data['manifold'])} markets")
    except Exception as e:
        print(f"manifold: FAIL {e}")

    try:
        data["gpu"] = gpu_spot(prev.get("gpu"))
        if data["gpu"]:
            print(f"gpu: H100 median ${data['gpu']['median_dph']}/hr ({data['gpu']['n_offers']} offers)")
    except Exception as e:
        print(f"gpu: FAIL {e}")

    try:
        data["insiders"] = edgar_insiders()
    except Exception as e:
        print(f"insiders: FAIL {e}")

    with open(OUT, "w") as f:
        json.dump(data, f)
    print("written:", OUT)
    return data


def main():
    args = sys.argv[1:]
    do_snap = "--snapshot" in args
    do_alert = "--alert" in args
    watch = None
    if "--watch" in args:
        i = args.index("--watch")
        watch = int(args[i + 1]) if i + 1 < len(args) and args[i + 1].isdigit() else 600
    while True:
        try:
            data = build()
            if do_snap:
                append_snapshot(data)
            if do_alert:
                check_alert(data)
        except Exception as e:
            print("build error:", e)
        if not watch:
            break
        print(f"sleeping {watch}s (Ctrl-C to stop)…")
        time.sleep(watch)


if __name__ == "__main__":
    main()
