#!/usr/bin/env python3
"""Refresh capex-data.json for the AI CapEx live tape (ai-capex.html).

Stdlib only, same pattern as update-china-data.py. Feeds the "live tape"
section of the otherwise hand-curated fundamentals page:

  - TSMC monthly revenue (TWSE OpenAPI, keyless)  : realized AI-hardware demand
  - Issuance velocity (SEC EDGAR full-text search): FWP/424B debt events by the
    AI-capex issuers, S-1s and Form Ds mentioning "artificial intelligence"
  - Ramp AI Index (public CSV)                    : paid AI adoption by US firms
  - Anthropic Economic Index (HF dataset meta)    : release freshness only
  - EIA-860M generator pipeline (optional)        : planned vs under-construction
    vs operating nameplate GW; needs EIA_API_KEY (free, eia.gov/opendata)

Not automated, kept in MANUAL below (no free machine-readable source):
  - Korea 20-day semiconductor exports (customs.go.kr press releases)
  - Taiwan MOEA export orders
  - PJM capacity-auction clears, LBNL interconnection-queue totals
  - Anthropic Economic Index headline split (freshness is live, numbers manual)
FINRA TRACE single-name spreads stay manual: the free Query API tier only
carries market aggregates, not the per-CUSIP prints the credit panel needs.

Usage:
  python3 update-capex-data.py                # one shot
  python3 update-capex-data.py --watch 21600  # 6h loop
"""
import csv
import datetime
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "capex-data.json"
SNAP = HERE / "capex-snapshots.csv"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) omen-capex-tape/1.0"}
SEC_UA = {"User-Agent": "Mikhail Blank blank.mikhail@gmail.com"}

TWSE_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TSMC_CODE = "2330"

EFTS = "https://efts.sec.gov/LATEST/search-index"
# the AI-capex debt issuers the thesis-i panel tracks: big-5 + CoreWeave
DEBT_CIKS = ["0000789019", "0001652044", "0001018724",   # MSFT GOOGL AMZN
             "0001326801", "0001341439", "0001769628"]   # META ORCL CRWV
DEBT_FORMS = "FWP,424B2,424B5"
EFTS_WINDOW_DAYS = 90

RAMP_CSV_URL = "https://ramp.com/data/ai-index/adoptionHeadline.csv"
RAMP_SERIES = "Ramp AI Index"
RAMP_BTOS = "U.S. Census BTOS estimate"

AEI_META_URL = "https://huggingface.co/api/datasets/Anthropic/EconomicIndex"

# EIA-860M via the v2 API; statuses per the 860M inventory codes.
EIA_860M_URL = "https://api.eia.gov/v2/electricity/operating-generator-capacity/data/"
EIA_STATUS_GROUPS = {
    "operating_gw": {"OP"},
    "planned_gw": {"P", "L", "T"},
    "under_construction_gw": {"U", "V", "TS"},
}

# fields with no machine-readable source: update by hand when re-verified
MANUAL = {
    "korea": {"chip_exports_yoy_pct": None, "asof": None,
              "note": "Korea Customs 20-day export release (~1st/11th/21st); "
                      "no keyless API - update by hand from the release coverage.",
              "src": "https://www.customs.go.kr"},
    "moea_orders": {"asof": None,
                    "note": "Taiwan MOEA export orders - manual; stats site blocks bots.",
                    "src": "https://www.moea.gov.tw"},
    "queues": {"lbnl_active_gw": 2600, "asof": "2023-12",
               "note": "LBNL Queued Up: active US interconnection requests, all fuels.",
               "src": "https://emp.lbl.gov/queues"},
    "pjm_capacity_auction": {"clears_usd_mw_day": {"2025/26": 269.92, "2026/27": 329.17},
                             "asof": "2025-07",
                             "note": "PJM base residual auction clearing prices.",
                             "src": "https://www.pjm.com"},
    "aei_headline": {"aug_pct": 57, "auto_pct": 43, "asof": "2025-02",
                     "note": "Anthropic Economic Index first report: augmentation vs "
                             "automation share of Claude usage."},
}


def get(url, timeout=30, headers=None):
    req = urllib.request.Request(url, headers=headers or UA)
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


def jget(url, timeout=30, headers=None):
    return json.loads(get(url, timeout, headers))


def rnd(x, nd=1):
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


# ---------- TSMC (TWSE OpenAPI) ----------

def roc_ym(s):
    """ROC calendar 'YYYMM' -> ISO 'YYYY-MM' (ROC year + 1911)."""
    m = re.fullmatch(r"(\d{2,3})(\d{2})", s or "")
    if not m:
        return None
    return f"{int(m.group(1)) + 1911}-{m.group(2)}"


def parse_tsmc_rows(rows):
    row = next((r for r in rows if r.get("公司代號") == TSMC_CODE), None)
    if not row:
        return None
    raw = rnd(row.get("營業收入-當月營收"), 6)  # thousand NTD
    rev = rnd(raw / 1e6, 1) if raw else None    # -> B NTD
    if not rev:
        return None
    return {"asof": roc_ym(row.get("資料年月")),
            "rev_ntd_b": rev,
            "mom_pct": rnd(row.get("營業收入-上月比較增減(%)")),
            "yoy_pct": rnd(row.get("營業收入-去年同月增減(%)")),
            "ytd_yoy_pct": rnd(row.get("累計營業收入-前期比較增減(%)")),
            "note": row.get("備註", "")}


def fetch_tsmc():
    return parse_tsmc_rows(jget(TWSE_REVENUE_URL, headers={**UA, "Accept": "application/json"}))


# ---------- Issuance velocity (EDGAR full-text search) ----------

def efts_windows(today, days=EFTS_WINDOW_DAYS):
    cur_end = today
    cur_start = today - datetime.timedelta(days=days - 1)
    prev_end = cur_start - datetime.timedelta(days=1)
    prev_start = prev_end - datetime.timedelta(days=days - 1)
    return ((cur_start.isoformat(), cur_end.isoformat()),
            (prev_start.isoformat(), prev_end.isoformat()))


def parse_efts_total(payload):
    try:
        return int(payload["hits"]["total"]["value"])
    except (KeyError, TypeError, ValueError):
        return None


def efts_count(window, q="", forms=None, ciks=None):
    params = {"q": q, "startdt": window[0], "enddt": window[1]}
    if forms:
        params["forms"] = forms
    if ciks:
        params["ciks"] = ",".join(ciks)
    url = f"{EFTS}?{urllib.parse.urlencode(params)}"
    n = parse_efts_total(jget(url, headers=SEC_UA))
    time.sleep(0.15)  # stay far under SEC's 10 req/s
    return n


def fetch_issuance(today=None):
    cur, prev = efts_windows(today or datetime.date.today())
    out = {"days": EFTS_WINDOW_DAYS}
    for label, window in (("cur", cur), ("prev", prev)):
        out[label] = {
            "from": window[0], "to": window[1],
            "debt": efts_count(window, forms=DEBT_FORMS, ciks=DEBT_CIKS),
            "s1_ai": efts_count(window, q='"artificial intelligence"', forms="S-1"),
            "formd_ai": efts_count(window, q='"artificial intelligence"', forms="D"),
        }
    return out


# ---------- Ramp AI Index ----------

def parse_ramp_csv(text, keep=24):
    rows = list(csv.DictReader(io.StringIO(text)))
    series, btos = [], []
    for r in rows:
        pct = rnd(r.get("adoption_rate_pct"))
        ym = (r.get("date_month") or "")[:7]
        if pct is None or not re.fullmatch(r"\d{4}-\d{2}", ym):
            continue
        if r.get("series") == RAMP_SERIES:
            series.append((ym, pct, rnd(r.get("mom_change_pp")), rnd(r.get("yoy_change_pp"))))
        elif r.get("series") == RAMP_BTOS:
            btos.append((ym, pct))
    if not series:
        return None
    series.sort()
    btos.sort()
    ym, pct, mom, yoy = series[-1]
    return {"asof": ym, "adoption_pct": pct, "mom_pp": mom, "yoy_pp": yoy,
            "btos_pct": btos[-1][1] if btos else None,
            "btos_asof": btos[-1][0] if btos else None,
            "series": [[m, p] for m, p, _, _ in series[-keep:]]}


def fetch_ramp():
    return parse_ramp_csv(get(RAMP_CSV_URL))


# ---------- Anthropic Economic Index ----------

def parse_aei(meta):
    releases = set()
    for s in meta.get("siblings", []):
        m = re.match(r"release_(\d{4})_(\d{2})_(\d{2})/", s.get("rfilename", ""))
        if m:
            releases.add("-".join(m.groups()))
    return {"last_modified": (meta.get("lastModified") or "")[:10] or None,
            "latest_release": max(releases) if releases else None}


def fetch_aei():
    return parse_aei(jget(AEI_META_URL))


# ---------- EIA-860M (optional, needs EIA_API_KEY) ----------

def parse_eia_860m(payload):
    data = (payload.get("response") or {}).get("data") or []
    if not data:
        return None
    latest = max(r.get("period", "") for r in data)
    if not latest:
        return None
    sums = {k: 0.0 for k in EIA_STATUS_GROUPS}
    for r in data:
        if r.get("period") != latest:
            continue
        for key, codes in EIA_STATUS_GROUPS.items():
            if r.get("status") in codes:
                try:
                    sums[key] += float(r.get("nameplate-capacity-mw") or 0)
                except ValueError:
                    pass
    out = {k: rnd(v / 1000, 1) for k, v in sums.items()}  # MW -> GW
    out["asof"] = latest
    return out


EIA_PAGE = 5000
EIA_MAX_PAGES = 10  # 860M has ~25k+ generator rows per month; one page truncates it
EIA_LOOKBACK_MONTHS = 12  # bound the server-side sort to a recent window (see eia_start_period)
EIA_TIMEOUT = 60          # the windowed query is quick, but 30s (default) tripped on the raw one


def eia_start_period(ref=None):
    """EIA monthly 'start' (YYYY-MM) EIA_LOOKBACK_MONTHS back from ref (default today).

    Without it the API sorts every generator-month back to 2015 before returning
    the latest period, which reliably exceeds the request timeout on CI. 860M lags
    ~2 months, so a one-year window comfortably contains the latest available period
    while cutting the rows the server must sort by ~100x."""
    d = ref or datetime.date.today()
    m = d.month - 1 - EIA_LOOKBACK_MONTHS
    return f"{d.year + m // 12:04d}-{m % 12 + 1:02d}"


def fetch_eia(key):
    # key goes in the X-Api-Key header, not the query string, so it never
    # lands in proxy logs or error output that echoes the URL
    rows = []
    start = eia_start_period()
    for page in range(EIA_MAX_PAGES):
        params = [("frequency", "monthly"), ("data[0]", "nameplate-capacity-mw"),
                  ("start", start),
                  ("length", str(EIA_PAGE)), ("offset", str(page * EIA_PAGE)),
                  ("sort[0][column]", "period"), ("sort[0][direction]", "desc")]
        j = jget(f"{EIA_860M_URL}?{urllib.parse.urlencode(params)}",
                 timeout=EIA_TIMEOUT, headers={**UA, "X-Api-Key": key})
        batch = (j.get("response") or {}).get("data") or []
        rows += batch
        # stop once past the latest period (sorted desc) or on a short page
        if len(batch) < EIA_PAGE or (rows and batch[-1].get("period") != rows[0].get("period")):
            break
    return parse_eia_860m({"response": {"data": rows}})


# ---------- assembly ----------

def snapshot_row(payload):
    tsmc = payload.get("tsmc") or {}
    ramp = payload.get("ramp") or {}
    cur = (payload.get("issuance") or {}).get("cur") or {}
    blank = lambda v: "" if v is None else v
    return [payload.get("updated"),
            blank(tsmc.get("rev_ntd_b")), blank(tsmc.get("yoy_pct")),
            blank(ramp.get("adoption_pct")),
            blank(cur.get("debt")), blank(cur.get("s1_ai")), blank(cur.get("formd_ai"))]


def append_snapshot(payload):
    new = not SNAP.exists()
    with SNAP.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["updated", "tsmc_rev_ntd_b", "tsmc_yoy_pct",
                        "ramp_adoption_pct", "debt_90d", "s1_ai_90d", "formd_ai_90d"])
        w.writerow(snapshot_row(payload))


def load_prev():
    """Last good capex-data.json, so a transient upstream failure keeps the
    previous value on the page instead of blanking the panel (matches the
    carry-forward convention in update-china-data.py)."""
    try:
        return json.loads(OUT.read_text())
    except (OSError, ValueError):
        return {}


def refresh():
    prev = load_prev()
    payload = {"updated": datetime.datetime.now(datetime.timezone.utc)
               .strftime("%Y-%m-%dT%H:%M:%SZ")}
    live_ok = False
    for name, fn in (("tsmc", fetch_tsmc), ("issuance", fetch_issuance),
                     ("ramp", fetch_ramp), ("aei", fetch_aei)):
        try:
            payload[name] = fn()
        except Exception as e:
            print(f"  {name}: FAILED {e}", file=sys.stderr)
            payload[name] = None
        if payload[name] is None:
            payload[name] = prev.get(name)  # carry last good value forward
        else:
            live_ok = True
    eia_key = os.environ.get("EIA_API_KEY", "")
    if eia_key:
        try:
            payload["eia"] = fetch_eia(eia_key)
        except Exception as e:
            print(f"  eia: FAILED {e}", file=sys.stderr)
            payload["eia"] = None
        if payload["eia"] is None:
            payload["eia"] = prev.get("eia")
    else:
        payload["eia"] = None
    payload["manual"] = MANUAL
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    # only append a snapshot when at least one live feed refreshed, so a total
    # outage doesn't write a blank-columned row into the durable history
    if live_ok:
        append_snapshot(payload)
    print(f"wrote {OUT.name}: tsmc={bool(payload['tsmc'])} "
          f"issuance={bool(payload['issuance'])} ramp={bool(payload['ramp'])} "
          f"aei={bool(payload['aei'])} eia={'set' if payload['eia'] else 'off'} "
          f"snapshot={'appended' if live_ok else 'skipped (all feeds down)'}")


if __name__ == "__main__":
    if "--watch" in sys.argv:
        every = int(sys.argv[sys.argv.index("--watch") + 1])
        while True:
            refresh()
            time.sleep(every)
    else:
        refresh()
