#!/usr/bin/env python3
"""Fetch the non-Polymarket data feeds for the AI Crash dashboard into market-data.json.

Sources (all free / unauthenticated unless noted):
  - Equity closes (Yahoo chart): NVDA, SOXX, AI-capex basket, SPY benchmark
  - Volatility complex (Yahoo chart): ^VXN, ^VIX, ^VIX3M, ^SKEW, ^VVIX
  - Options skew + IV term structure (CBOE delayed quotes): NVDA, SOXX
  - LEAPS-implied 1y tail probabilities (same CBOE chains, N(-d2)): NVDA, SOXX
  - Credit proxies (Yahoo chart): HYG, LQD, JNK
  - Credit spreads (FRED, keyless CSV): HY OAS, CCC OAS, NFCI
  - Hyperscaler capex fundamentals (SEC XBRL companyconcept): MSFT, GOOGL, AMZN, META, ORCL
  - Cross-venue (Kalshi public API + Manifold public API + Metaculus, token optional)
  - Insider activity (SEC EDGAR Form 4): NVDA, AVGO, ORCL, CRWV
  - Realized GPU spot rent (vast.ai public bundles API): H100 SXM $/GPU-hr
  - Kalshi GPU compute markets (H100/H200/B200/A100): second venue on the same
    rents, settled on the Ornn index — the cross-venue basis check for vast.ai

Optional env: METACULUS_TOKEN enables the Metaculus forecaster-crowd panel
(create a free account at metaculus.com, token from the profile page).

Also:
  --snapshot   append a chain-linkable snapshot (2 Polymarket indexes + gauge) to snapshots.csv
  --alert      compute the crash-pressure gauge server-side and push a Telegram/ntfy
               notification when the regime escalates (state kept in alert-state.json)
  --watch N    refresh every N seconds

Env for --alert (all optional): TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID, and/or NTFY_TOPIC.

No third-party dependencies. Run it from the folder that serves the dashboard.
"""
import urllib.request, urllib.error, urllib.parse, json, datetime, re, sys, os, time, math
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "market-data.json")
SNAP = os.path.join(HERE, "snapshots.csv")
BUNDLE = os.path.join(HERE, "market-data.js")
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
# HYG/LQD/JNK are the leveraged-credit proxies; BIZD (VanEck BDC ETF) is the
# private-credit / direct-lending channel Kedrosky flags — where pensions and
# insurers hold AI-data-center / neocloud debt that never touches the HY index.
CREDIT = ["HYG", "LQD", "JNK", "BIZD"]
# most debt-dependent AI-infra names (neoclouds + capex-heavy power/DB); their
# equity is the market's read on financing risk before it shows in credit spreads.
LEVERED_AI = ["CRWV", "ORCL", "NBIS", "IREN"]
# power/electricity: XLU proxies data-center power-demand pull (kept OUT of the
# breadth basket so it never moves that signal); ELEC_CPI is residents' bills.
POWER_PROXY = ["XLU"]
FRED = {"BAMLH0A0HYM2": "HY_OAS", "BAMLH0A3HYC": "CCC_OAS", "NFCI": "NFCI",
        "GDP": "GDP", "CUSR0000SEHF01": "ELEC_CPI",
        # claims-watch tape (singularity claims panel): core goods CPI (deflation claim),
        # unemployment + prime-age LFPR (displacement claim), realized real GDP growth SAAR
        "CUSR0000SACL1E": "CORE_GOODS_CPI", "UNRATE": "UNRATE",
        "LNS11300060": "LFPR_PRIME", "A191RL1Q225SBEA": "GDP_GROWTH"}
SKEW_SYMS = ["NVDA", "SOXX"]
# LEAPS tail: drawdown levels per symbol; the last one is the bubble-market trigger level
TAIL_LEVELS = {"NVDA": [-30, -50], "SOXX": [-25, -40]}
TAIL_RATE = 0.04          # risk-free rate for d2
TAIL_MIN_DTE = 250        # only expiries at least this far out qualify as "1y"
INSIDER_TICKERS = ["NVDA", "AVGO", "ORCL", "CRWV"]
# hyperscaler / AI-capex fundamentals via SEC XBRL (calendar-quarter aggregation)
FUND_CIKS = {"MSFT": "0000789019", "GOOGL": "0001652044", "AMZN": "0001018724",
             "META": "0001326801", "ORCL": "0001341439"}
FUND_TAGS = {"capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
                       "PaymentsToAcquireProductiveAssets"],       # AMZN's tag since 2017
             "ocf": ["NetCashProvidedByUsedInOperatingActivities",
                     "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
             # D&A cash-flow addback; firms use different tags, sec_concept merges them.
             # Depreciation lagging capex = cost recognition trailing the cash spend,
             # the accounting tell of the fast-obsolescing GPU buildout.
             "dep": ["DepreciationDepletionAndAmortization",
                     "DepreciationAmortizationAndAccretionNet",
                     "DepreciationAmortizationAndImpairment",
                     "DepreciationAndAmortization", "Depreciation"]}
METACULUS_TERMS = ["AI bubble", "AI winter", "artificial general intelligence"]
KALSHI_SERIES = {
    "KXACQUIREMISTRAL": "AI lab acquisition (Mistral)",
    "KXRECSSNBER": "US recession (macro backdrop)",
    "KXBIGTECHLAYOFF": "Big tech layoffs",
    "KXOAIANTH": "OpenAI vs Anthropic",
    "KXUSOPENAIANTH": "US stake in OpenAI & Anthropic",
}
MANIFOLD_TERMS = ["AI bubble", "NVIDIA crash", "AI winter"]
# Kalshi GPU compute markets (launched 2026-07-14): the second venue pricing the
# same GPU rents Polymarket brackets do, settled on the Ornn index rather than
# vast.ai's ask tape — so the two venues disagree partly on basis, not just view.
#   *W   weekly  — directional "price to beat"; its strike IS the Ornn reference
#                  print at open_time (not live), which is how we read Ornn for free.
#   *MON monthly — terminal ladder on the month-end value; the only real forward point.
#   *MAX yearly  — resolves "above $X BY Dec 31" (running max, upward-biased).
#                  Deliberately NOT fetched: it is not comparable to a terminal forward.
KALSHI_GPU = {
    "H100": {"label": "H100 SXM", "weekly": "KXH100W", "monthly": "KXH100MON"},
    "H200": {"label": "H200", "weekly": "KXH200W", "monthly": "KXH200MON"},
    "B200": {"label": "B200", "weekly": "KXB200W", "monthly": "KXB200MON"},
    "A100": {"label": "A100 SXM4", "weekly": "KXA100W", "monthly": "KXA100MON"},
}
# a ladder strike wider than this is a quote, not a price
KALSHI_MAX_SPREAD = 0.15
# Bear (OMN-X) is the short side: the union of two sleeves, priced as one flat
# equal-weight basket of 9. The sleeves are series-identical to the indexes Bear
# replaced – MKT to the old AI-Crash, GOV to the old AI-Regulation – which is what
# lets the crash-pressure gauge and the lead-lag study keep reading MKT unchanged.
BEAR_SLEEVES = {
    "mkt": ["691340", "676827", "676846"],
    "gov": ["2787889", "2787891", "2787890", "2698575", "676842", "2839991"],
}
POLY_IDS = {
    "bull": ["676829", "653788", "676837", "1087074", "656312", "656313", "2413330", "2109881", "676804", "2487206", "2255930"],
    "bear": BEAR_SLEEVES["mkt"] + BEAR_SLEEVES["gov"],
}
BUBBLE_ID = "691340"


def index_level(price, side):
    """100 x the equal-weight mean of the constituents we have a live price for."""
    vals = [price[i] for i in POLY_IDS[side] if i in price]
    return sum(vals) / len(vals) * 100 if vals else None


def sleeve_level(price, sleeve):
    vals = [price[i] for i in BEAR_SLEEVES[sleeve] if i in price]
    return sum(vals) / len(vals) * 100 if vals else None


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
        rows.append({"dte": (exp - today).days, "type": m.group(2), "iv": o["iv"],
                     "delta": o["delta"], "strike": int(m.group(3)) / 1000.0})
    return d.get("current_price"), rows


def iv_at(exp_rows, typ, target):
    pts = sorted((abs(r["delta"]), r["iv"]) for r in exp_rows if r["type"] == typ)
    for i in range(len(pts) - 1):
        (d0, v0), (d1, v1) = pts[i], pts[i + 1]
        if d0 <= target <= d1 and d1 != d0:
            return v0 + (v1 - v0) * (target - d0) / (d1 - d0)
    return None


def cboe_skew_and_term(sym, preloaded=None):
    spot, rows = preloaded if preloaded else cboe_options(sym)
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


# ---------- LEAPS-implied tail probabilities ----------
def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_prob_below(spot, strike, iv, dte, r=TAIL_RATE):
    """Risk-neutral P(S_T < K) = N(-d2). Overstates real-world crash odds a bit
    (risk premium), which makes it a conservative ceiling for comparison."""
    if not spot or not strike or not iv or iv <= 0 or dte <= 0:
        return None
    t = dte / 365.0
    d2 = (math.log(spot / strike) + (r - iv * iv / 2) * t) / (iv * math.sqrt(t))
    return norm_cdf(-d2)


def options_tail(sym, spot, rows, prev):
    """1y-ish LEAPS-implied probability of finishing below each drawdown level."""
    if not spot:
        return None
    dtes = sorted(set(r["dte"] for r in rows if r["dte"] >= TAIL_MIN_DTE))
    if not dtes:
        return None
    dte = min(dtes, key=lambda d: abs(d - 365))
    puts = [r for r in rows if r["dte"] == dte and r["type"] == "P" and r.get("iv")]
    if not puts:
        return None
    today = datetime.date.today().isoformat()
    levels = []
    for pct in TAIL_LEVELS.get(sym, [-30, -50]):
        target = spot * (1 + pct / 100.0)
        best = min(puts, key=lambda r: abs(r["strike"] - target))
        if abs(best["strike"] - target) > 0.12 * target:
            levels.append({"pct": pct, "strike": None, "iv": None, "p": None})
            continue
        p = bs_prob_below(spot, best["strike"], best["iv"], dte)
        levels.append({"pct": pct, "strike": best["strike"], "iv": round(best["iv"], 4),
                       "p": round(p, 4) if p is not None else None})
    trig = levels[-1]["p"] if levels else None
    hist = [h for h in ((prev or {}).get("history") or []) if h["date"] != today]
    if trig is not None:
        hist.append({"date": today, "p_trig": trig})
    return {"date": today, "dte": dte, "spot": spot, "levels": levels,
            "trigger_pct": TAIL_LEVELS.get(sym, [-30, -50])[-1], "history": hist[-365:]}


# ---------- SEC XBRL fundamentals (hyperscaler capex vs operating cash flow) ----------
def quarterlize(entries):
    """XBRL cash-flow entries are cumulative from fiscal-year start (Q1, 6mo, 9mo, FY).
    Return {calendar 'YYYYQn' of the period END: single-quarter value} by differencing
    successive cumulatives within each fiscal-year group."""
    ded = {}
    for e in entries:
        if e.get("start") and e.get("end") and e.get("val") is not None:
            ded[(e["start"], e["end"])] = e["val"]     # later filings overwrite earlier
    groups = {}
    for (start, end), val in ded.items():
        groups.setdefault(start, []).append((end, val))
    out = {}
    for start, evs in groups.items():
        evs.sort()
        d0 = datetime.date.fromisoformat(start)
        prev_end, prev_val = None, 0.0
        for end, val in evs:
            d1 = datetime.date.fromisoformat(end)
            span = (d1 - (datetime.date.fromisoformat(prev_end) if prev_end else d0)).days
            if 75 <= span <= 105:                      # a clean single quarter
                q = f"{d1.year}Q{(d1.month - 1) // 3 + 1}"
                out[q] = val - prev_val
            prev_end, prev_val = end, val
    return out


def sec_concept(cik, tags):
    """Merge quarterly values across candidate tags (companies switch tags over time,
    e.g. AMZN moved capex to PaymentsToAcquireProductiveAssets in 2017)."""
    out = {}
    for tag in tags:
        try:
            j = json.loads(get(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json",
                               headers=SEC_UA))
            entries = j.get("units", {}).get("USD", [])
            if entries:
                out.update(quarterlize(entries))
        except Exception:
            continue
    return out


def fundamentals():
    """Aggregate quarterly capex and operating cash flow across the AI-capex filers.
    Capex/OCF is the classic capex-bubble fundamental (dot-com telecoms ran >100%)."""
    per = {}
    for sym, cik in FUND_CIKS.items():
        capex = sec_concept(cik, FUND_TAGS["capex"])
        ocf = sec_concept(cik, FUND_TAGS["ocf"])
        dep = sec_concept(cik, FUND_TAGS["dep"])
        if capex:
            per[sym] = {"capex": capex, "ocf": ocf, "dep": dep}
            print(f"fundamentals {sym}: {len(capex)} quarters (dep {len(dep)})")
        time.sleep(0.15)
    if not per:
        return None
    allq = sorted(set(q for v in per.values() for q in v["capex"]))[-10:]
    quarters, capex, ocf, dep, count = [], [], [], [], []
    for q in allq:
        syms = [s for s in per if q in per[s]["capex"]]
        if len(syms) < len(per) - 1:     # allow one laggard (off-cycle fiscal years)
            continue
        quarters.append(q)
        capex.append(round(sum(per[s]["capex"][q] for s in syms) / 1e9, 2))
        o = [per[s]["ocf"].get(q) for s in syms if per[s]["ocf"].get(q) is not None]
        ocf.append(round(sum(o) / 1e9, 2) if len(o) == len(syms) else None)
        # depreciation aggregated over the same firms present for capex this quarter;
        # None unless every one of them reported a D&A line (so dep/capex is comparable)
        d = [per[s]["dep"].get(q) for s in syms if per[s]["dep"].get(q) is not None]
        dep.append(round(sum(d) / 1e9, 2) if len(d) == len(syms) else None)
        count.append(len(syms))
    if not quarters:
        return None
    return {"names": list(per.keys()), "quarters": quarters, "capex_b": capex,
            "ocf_b": ocf, "dep_b": dep, "n_firms": count,
            "asof": datetime.date.today().isoformat()}


def quarter_of(iso_date):
    """Map a FRED quarterly observation date (quarter START, e.g. 2025-04-01)
    to a calendar quarter label matching the fundamentals panel ('2025Q2')."""
    d = datetime.date.fromisoformat(iso_date)
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def macro_capex_gdp(fund, gdp_series):
    """Combined AI-capex as a share of the economy and of GDP growth (Kedrosky's
    scale argument). fund['capex_b'] is single-quarter capex ($B); FRED GDP is a
    seasonally-adjusted ANNUAL rate, so capex is annualized (x4) before the ratio."""
    if not fund or not fund.get("quarters") or not gdp_series:
        return None
    gdp = {quarter_of(p["d"]): p["c"] for p in gdp_series if p.get("c")}
    quarters, capex_ann, gdp_b, pct = [], [], [], []
    for q, cq in zip(fund["quarters"], fund["capex_b"]):
        if q not in gdp:
            continue
        ann = round(cq * 4, 2)
        quarters.append(q)
        capex_ann.append(ann)
        gdp_b.append(gdp[q])
        pct.append(round(ann / gdp[q] * 100, 3))
    if not quarters:
        return None
    growth = [None]
    for i in range(1, len(quarters)):
        dg = gdp_b[i] - gdp_b[i - 1]
        dc = capex_ann[i] - capex_ann[i - 1]
        growth.append(round(dc / dg * 100, 1) if dg > 0 else None)
    return {"quarters": quarters, "capex_ann_b": capex_ann, "gdp_b": gdp_b,
            "pct_gdp": pct, "growth_share": growth,
            "asof": datetime.date.today().isoformat()}


# ---------- Metaculus (optional token; forecaster crowd, no capital at risk) ----------
def metaculus():
    tok = os.environ.get("METACULUS_TOKEN")
    if not tok:
        return {"enabled": False, "note": "Set METACULUS_TOKEN (free account) to enable the forecaster-crowd panel.", "questions": []}
    seen, out = set(), []
    for term in METACULUS_TERMS:
        try:
            j = json.loads(get("https://www.metaculus.com/api/posts/?"
                               + urllib.parse.urlencode({"search": term, "limit": 6,
                                                         "statuses": "open", "forecast_type": "binary"}),
                               headers={"User-Agent": "omen-ai/1.0", "Authorization": f"Token {tok}"}))
        except Exception as e:
            print(f"metaculus '{term}': FAIL {e}")
            continue
        for post in j.get("results", []):
            pid = post.get("id")
            if pid in seen:
                continue
            q = post.get("question") or {}
            prob = None
            try:
                agg = (q.get("aggregations") or {}).get("recency_weighted") or {}
                latest = agg.get("latest") or {}
                centers = latest.get("centers") or []
                prob = centers[0] if centers else latest.get("means", [None])[0]
            except Exception:
                prob = None
            if prob is None:
                prob = q.get("community_prediction") if isinstance(q.get("community_prediction"), (int, float)) else None
            n = post.get("nr_forecasters") or q.get("nr_forecasters")
            if prob is None:
                continue
            seen.add(pid)
            out.append({"theme": term, "title": post.get("title") or q.get("title"),
                        "url": f"https://www.metaculus.com/questions/{pid}/",
                        "prob": round(float(prob), 3), "forecasters": n})
    out.sort(key=lambda x: -(x["forecasters"] or 0))
    return {"enabled": True, "questions": out[:8]}


# ---------- Kalshi ----------
KALSHI_B = "https://api.elections.kalshi.com/trade-api/v2"


def _fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def kalshi_mid(m):
    """(mid, spread) in probability from a Kalshi market, or (None, None).

    Kalshi's quote fields are *_dollars and already denominated in dollars
    (0.23 = 23c), not cents. A book quoted at the bounds (bid 0 / ask 1) is
    empty, not a coin flip, so it yields no price.
    """
    b, a = _fnum(m.get("yes_bid_dollars")), _fnum(m.get("yes_ask_dollars"))
    if b is None or a is None or b <= 0.0 or a >= 1.0 or a < b:
        return None, None
    return (b + a) / 2, a - b


def kalshi_price(m):
    """Mid where the book is two-sided, else the last print. Display only."""
    mid, _ = kalshi_mid(m)
    if mid is not None:
        return mid
    last = _fnum(m.get("last_price_dollars"))
    return last if last else None


def kalshi_ladder(markets, max_spread=KALSHI_MAX_SPREAD):
    """Strike ladder as a survival curve P(value > strike), cleaned.

    Drops one-sided and wide books, then enforces monotonicity: survival cannot
    increase with strike, so a higher print is a stale/thin quote, not news.
    """
    rows = []
    for m in markets:
        k = _fnum(m.get("floor_strike"))
        mid, spread = kalshi_mid(m)
        if k is None or mid is None or spread > max_spread:
            continue
        rows.append({"k": k, "p": mid, "spread": round(spread, 4)})
    rows.sort(key=lambda r: r["k"])
    last = 1.0
    for r in rows:
        r["p"] = round(min(r["p"], last), 4)
        last = r["p"]
    return rows


def implied_median(rows):
    """Strike where the survival curve crosses 50%, linearly interpolated.

    None when the crossing lies outside the quoted strikes — the median is then
    simply unknown, and inventing one from the edge of the ladder is the exact
    artifact this guards against.
    """
    for i in range(len(rows) - 1):
        k1, p1 = rows[i]["k"], rows[i]["p"]
        k2, p2 = rows[i + 1]["k"], rows[i + 1]["p"]
        if p1 >= 0.5 >= p2 and p1 != p2:
            return k1 + (p1 - 0.5) * (k2 - k1) / (p1 - p2)
    return None


def kalshi_markets(series_ticker):
    j = json.loads(get(KALSHI_B + f"/markets?series_ticker={series_ticker}&status=open&limit=200",
                       timeout=20))
    return j.get("markets") or []


def kalshi_gpu():
    """GPU compute prices from Kalshi, the second venue on the same underlying.

    Returns per-chip: the Ornn reference print (weekly strike), the month-end
    implied median where the ladder can carry one, and the ladder itself.
    """
    out = {"source": "Kalshi public API (settles on Ornn index)", "chips": []}
    for chip, cfg in KALSHI_GPU.items():
        row = {"chip": chip, "label": cfg["label"], "ref": None, "ref_date": None,
               "implied": None, "strikes": 0, "expiry": None, "url": None, "note": None}
        try:
            wk = kalshi_markets(cfg["weekly"])
        except Exception:
            wk = []
        # the weekly directional strike is set at the Ornn print when the market
        # opens, so it dates to open_time — it is a reference, never a live spot.
        for m in wk:
            k = _fnum(m.get("floor_strike"))
            if k is None:
                continue
            row["ref"] = k
            row["ref_date"] = (m.get("open_time") or "")[:10]
            row["ref_above"] = kalshi_price(m)
            row["url"] = f"https://kalshi.com/markets/{cfg['weekly'].lower()}"
            break
        try:
            mo = kalshi_markets(cfg["monthly"])
        except Exception:
            mo = []
        if mo:
            row["expiry"] = (mo[0].get("close_time") or "")[:10]
            lad = kalshi_ladder(mo)
            row["strikes"] = len(lad)
            row["implied"] = implied_median(lad)
            if row["implied"] is None:
                row["note"] = ("book too thin to imply a month-end median"
                               if len(lad) < 3 else "median sits outside the quoted strikes")
        out["chips"].append(row)
    return out


def kalshi():
    out = {"authed": False, "note": "", "markets": []}
    for st, theme in KALSHI_SERIES.items():
        try:
            j = json.loads(get(KALSHI_B + f"/events?with_nested_markets=true&series_ticker={st}",
                               timeout=20))
        except Exception:
            continue
        for e in j.get("events", []):
            title = e.get("title", "")
            for m in e.get("markets", [])[:1]:
                out["markets"].append({
                    "theme": theme, "ticker": m.get("ticker"), "title": title,
                    "subtitle": m.get("yes_sub_title") or m.get("subtitle") or "",
                    "price": kalshi_price(m),
                    "volume": _fnum(m.get("volume_fp")),
                    "url": f"https://kalshi.com/markets/{st.lower()}",
                })
            break
    if any(x["price"] is not None for x in out["markets"]):
        out["authed"] = True
    else:
        out["note"] = "No live quotes on the tracked Kalshi series right now."
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


def gauge_groups(fam):
    """Split the five families into ex-ante vs coincident sub-scores.
    Leading = priced before the fact (prediction markets, options skew, credit);
    Confirming = moves with or after prices (vol complex, equity drawdown)."""
    lead = mean_or_none([fam.get("pred"), fam.get("opt"), fam.get("credit")])
    conf = mean_or_none([fam.get("vol"), fam.get("equity")])
    return lead, conf


def compute_regime(gauge, price):
    bubble = (price.get(BUBBLE_ID) or 0) * 100
    # deliberately the MKT sleeve, not the Bear composite: these bands are calibrated to
    # priced *crash* risk, and MKT is the old crash basket unchanged. Reading the
    # composite here would let regulatory odds trip a crash regime.
    level = sleeve_level(price, "mkt") or 0
    # Stressed requires a broad or confirmed metric — the blended gauge (mean of all
    # families) or the crash basket average. A single market (the bubble-burst market
    # included) can raise Elevated but never trips red on its own, so an escalation alert
    # can't fire on one market spiking. Matches the two pages' regime rules.
    if (gauge is not None and gauge >= 55) or level >= 40:
        return "stressed"
    if (gauge is not None and gauge >= 35) or level >= 25 or bubble >= 15:
        return "elevated"
    return "calm"


# ---------- snapshots ----------
# `crash`/`reg` are kept past the Bear merge: they are exactly the MKT/GOV sleeve reads,
# so the stored series stays comparable across the merge date and `bear` can be
# backfilled from them for the rows that predate the column.
SNAP_HEADER = ["date", "bull", "bull_n", "bear", "bear_n", "crash", "crash_n", "reg", "reg_n",
               "gauge", "lead", "conf", "comp"]


def snapshot_row(price):
    """One snapshot row: Bear as the flat 9-market union, sleeves alongside it."""
    row = {"date": datetime.date.today().isoformat()}
    for side, ids in POLY_IDS.items():
        lvl = index_level(price, side)
        row[side] = round(lvl, 2) if lvl is not None else ""
        row[side + "_n"] = len([i for i in ids if i in price])
    for sleeve, col in (("mkt", "crash"), ("gov", "reg")):
        lvl = sleeve_level(price, sleeve)
        row[col] = round(lvl, 2) if lvl is not None else ""
        row[col + "_n"] = len([i for i in BEAR_SLEEVES[sleeve] if i in price])
    return row


def backfill_bear(d):
    """Bear for a pre-merge row: the flat union rebuilt from the two sleeve levels.
    Counts are the live membership at that timestamp, so this is the same equal-weight
    mean the composite computes now – the merge introduces no splice step."""
    if d.get("bear") or not (d.get("crash") and d.get("reg")):
        return d.get("bear", "")
    try:
        cn, rn = int(d["crash_n"]), int(d["reg_n"])
        if cn + rn == 0:
            return ""
        return round((cn * float(d["crash"]) + rn * float(d["reg"])) / (cn + rn), 2)
    except (ValueError, KeyError):
        return ""


def append_snapshot(data=None):
    try:
        price = poly_prices()
    except Exception as e:
        print("  snapshot skipped:", e)
        return
    row = snapshot_row(price)
    gauge = lead = conf = ""
    if data:
        g, fam = compute_gauge(data, price)
        gauge = round(g, 1) if g is not None else ""
        gl, gc = gauge_groups(fam)
        lead = round(gl, 1) if gl is not None else ""
        conf = round(gc, 1) if gc is not None else ""
    row["gauge"] = gauge
    row["lead"] = lead
    row["conf"] = conf
    row["comp"] = ",".join(sorted(price.keys()))
    existing = {}
    if os.path.exists(SNAP):
        with open(SNAP) as f:
            lines = f.read().strip().split("\n")
        old_header = lines[0].split(",") if lines else []
        for line in lines[1:]:
            parts = line.split(",", len(old_header) - 1)
            if parts and parts[0]:
                d = dict(zip(old_header, parts))
                d["bear"] = backfill_bear(d)
                if d["bear"] != "" and not d.get("bear_n"):
                    d["bear_n"] = int(d.get("crash_n") or 0) + int(d.get("reg_n") or 0)
                existing[parts[0]] = ",".join(str(d.get(h, "")) for h in SNAP_HEADER)
    existing[row["date"]] = ",".join(str(row[h]) for h in SNAP_HEADER)
    with open(SNAP, "w") as f:
        f.write(",".join(SNAP_HEADER) + "\n")
        for d in sorted(existing):
            f.write(existing[d] + "\n")
    print(f"  snapshot: bull {row['bull']} bear {row['bear']} "
          f"(mkt {row['crash']} · gov {row['reg']}) gauge {gauge} -> {SNAP}")


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
        lead, conf = gauge_groups(fam)
        send_alert(f"AI Crash Monitor: regime -> {regime.upper()}",
                   f"Gauge {gtxt}/100 (leading {lead and round(lead)} / confirming {conf and round(conf)}) "
                   f"· bubble market {bubble:.1f}% · families: "
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
            "equity": {}, "vol": {}, "skew": {}, "term": {}, "tail": {}, "credit": {},
            "fred": {}, "kalshi": {}, "kalshi_gpu": None, "manifold": [], "metaculus": None,
            "fundamentals": None, "macro": None, "insiders": {}, "gpu": None}

    for sym in EQUITY + POWER_PROXY:
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
            chain = cboe_options(sym)          # one download per symbol, reused below
            sk, term = cboe_skew_and_term(sym, chain)
            try:
                tail = options_tail(sym, chain[0], chain[1], prev.get("tail", {}).get(sym))
                if tail:
                    data["tail"][sym] = tail
                    trig = tail["levels"][-1]
                    print(f"tail {sym}: P({trig['pct']}% @ {tail['dte']}d) = "
                          f"{trig['p'] * 100:.1f}%" if trig["p"] is not None else f"tail {sym}: no quote at trigger")
            except Exception as e:
                print(f"tail {sym}: FAIL {e}")
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
        data["kalshi_gpu"] = kalshi_gpu()
        for c in data["kalshi_gpu"]["chips"]:
            imp = f"${c['implied']:.2f}" if c["implied"] else f"n/a ({c['note']})"
            print(f"kalshi_gpu {c['chip']}: ref ${c['ref']} ({c['ref_date']}) "
                  f"month-end {imp}, {c['strikes']} usable strikes")
    except Exception as e:
        print(f"kalshi_gpu: FAIL {e}")
        data["kalshi_gpu"] = None

    try:
        data["manifold"] = manifold()
        print(f"manifold: {len(data['manifold'])} markets")
    except Exception as e:
        print(f"manifold: FAIL {e}")

    try:
        data["metaculus"] = metaculus()
        print(f"metaculus: {len(data['metaculus']['questions'])} questions, enabled={data['metaculus']['enabled']}")
    except Exception as e:
        print(f"metaculus: FAIL {e}")

    try:
        data["fundamentals"] = fundamentals()
        if data["fundamentals"]:
            f = data["fundamentals"]
            print(f"fundamentals: {len(f['quarters'])} quarters, latest capex ${f['capex_b'][-1]}B")
    except Exception as e:
        print(f"fundamentals: FAIL {e}")

    try:
        gdp = (data["fred"].get("GDP") or {}).get("series")
        data["macro"] = macro_capex_gdp(data.get("fundamentals"), gdp)
        if data["macro"]:
            m = data["macro"]
            print(f"macro: capex {m['pct_gdp'][-1]}% of GDP, {m['growth_share'][-1]}% of GDP growth ({m['quarters'][-1]})")
    except Exception as e:
        print(f"macro: FAIL {e}")

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

    # server-side gauge + regime, embedded so the landing page and the monitor
    # can never disagree about the headline regime
    try:
        price = poly_prices()
        score, fam = compute_gauge(data, price)
        lead, conf = gauge_groups(fam)
        mkt_level = sleeve_level(price, "mkt")
        data["server_gauge"] = {
            "score": round(score, 1) if score is not None else None,
            "lead": round(lead, 1) if lead is not None else None,
            "conf": round(conf, 1) if conf is not None else None,
            "fam": {k: (round(v, 1) if v is not None else None) for k, v in fam.items()},
            "regime": compute_regime(score, price),
            "bubble": price.get(BUBBLE_ID),
            # gauge context: still the crash basket (= Bear's MKT sleeve), not the composite
            "crash_level": round(mkt_level, 2) if mkt_level is not None else None,
            "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
        print(f"server gauge: {data['server_gauge']['score']} ({data['server_gauge']['regime']}) "
              f"lead {data['server_gauge']['lead']} conf {data['server_gauge']['conf']}")
    except Exception as e:
        print(f"server gauge: FAIL {e}")

    with open(OUT, "w") as f:
        json.dump(data, f)
    print("written:", OUT)
    return data


def write_bundle():
    """Emit market-data.js so the dashboard works when opened directly via file://.
    Browsers block fetch() of sibling files under file://, but a <script src> tag loads
    fine; the page falls back to these globals when fetch fails."""
    try:
        data_txt = open(OUT).read()
    except OSError:
        return
    try:
        snap_txt = open(SNAP).read()
    except OSError:
        snap_txt = ""
    with open(BUNDLE, "w") as f:
        f.write("window.__MARKET_DATA__=" + data_txt + ";\n")
        f.write("window.__SNAPSHOTS_CSV__=" + json.dumps(snap_txt) + ";\n")
    print("written:", BUNDLE)


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
        write_bundle()
        if not watch:
            break
        print(f"sleeping {watch}s (Ctrl-C to stop)…")
        time.sleep(watch)


if __name__ == "__main__":
    main()
