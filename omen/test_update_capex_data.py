import datetime
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "ucd", Path(__file__).parent / "update-capex-data.py")
ucd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ucd)


def test_roc_ym_converts_republic_calendar():
    assert ucd.roc_ym("11506") == "2026-06"
    assert ucd.roc_ym("9912") == "2010-12"
    assert ucd.roc_ym("bogus") is None
    assert ucd.roc_ym("") is None


TSMC_ROWS = [
    {"公司代號": "2317", "資料年月": "11506", "營業收入-當月營收": "1"},
    {"公司代號": "2330", "資料年月": "11506",
     "營業收入-當月營收": "442679969",
     "營業收入-上月比較增減(%)": "6.164589232380731",
     "營業收入-去年同月增減(%)": "67.86685548491262",
     "累計營業收入-前期比較增減(%)": "35.613194655616326",
     "備註": "因先進製程產品需求增加所致。"},
]


def test_parse_tsmc_rows_picks_2330_and_scales_to_billions():
    out = ucd.parse_tsmc_rows(TSMC_ROWS)
    assert out["asof"] == "2026-06"
    # 442,679,969 thousand NTD -> 442.7B NTD
    assert out["rev_ntd_b"] == 442.7
    assert out["mom_pct"] == 6.2
    assert out["yoy_pct"] == 67.9
    assert out["ytd_yoy_pct"] == 35.6
    assert "先進製程" in out["note"]


def test_parse_tsmc_rows_missing_symbol_is_none():
    assert ucd.parse_tsmc_rows([{"公司代號": "2317", "資料年月": "11506"}]) is None
    assert ucd.parse_tsmc_rows([]) is None


def test_efts_windows_are_contiguous_and_iso():
    cur, prev = ucd.efts_windows(datetime.date(2026, 7, 19), days=90)
    assert cur == ("2026-04-21", "2026-07-19")
    assert prev == ("2026-01-21", "2026-04-20")
    # contiguous: prev ends the day before cur starts
    assert (datetime.date.fromisoformat(cur[0])
            - datetime.date.fromisoformat(prev[1])).days == 1


def test_parse_efts_total_reads_hit_count():
    assert ucd.parse_efts_total({"hits": {"total": {"value": 17}}}) == 17
    assert ucd.parse_efts_total({"hits": {}}) is None
    assert ucd.parse_efts_total({}) is None


RAMP_CSV = """date_month,series,adoption_rate_pct,mom_change_pp,yoy_change_pp
2026-04-01,Ramp AI Index,53.10,0.60,13.00
2026-04-01,U.S. Census BTOS estimate,19.80,0.30,
2026-05-01,Ramp AI Index,54.17,1.07,12.80
2026-05-01,U.S. Census BTOS estimate,20.05,0.25,
2026-06-01,Ramp AI Index,54.95,0.78,12.23
2026-06-01,U.S. Census BTOS estimate,20.6,0.55,
"""


def test_parse_ramp_csv_headline_and_contrast_series():
    out = ucd.parse_ramp_csv(RAMP_CSV)
    assert out["asof"] == "2026-06"
    assert out["adoption_pct"] == 55.0
    assert out["mom_pp"] == 0.8
    assert out["yoy_pp"] == 12.2
    assert out["btos_pct"] == 20.6
    assert out["btos_asof"] == "2026-06"
    assert out["series"][-1] == ["2026-06", 55.0]
    assert [ym for ym, _ in out["series"]] == ["2026-04", "2026-05", "2026-06"]


def test_parse_ramp_csv_series_tail_capped():
    rows = ["date_month,series,adoption_rate_pct,mom_change_pp,yoy_change_pp"]
    for m in range(1, 13):
        rows.append(f"2025-{m:02d}-01,Ramp AI Index,{40 + m}.0,0.5,10.0")
    out = ucd.parse_ramp_csv("\n".join(rows), keep=6)
    assert len(out["series"]) == 6
    assert out["series"][-1] == ["2025-12", 52.0]
    assert out["btos_pct"] is None


def test_parse_ramp_csv_garbage_is_none():
    assert ucd.parse_ramp_csv("not,a,ramp\n1,2,3\n") is None
    assert ucd.parse_ramp_csv("") is None


AEI_META = {
    "lastModified": "2026-06-26T23:21:00.000Z",
    "siblings": [
        {"rfilename": "README.md"},
        {"rfilename": "release_2025_02_10/automation_vs_augmentation.csv"},
        {"rfilename": "release_2025_09_15/data.csv"},
        {"rfilename": "release_2025_03_27/README.md"},
    ],
}


def test_parse_aei_latest_release_and_modified():
    out = ucd.parse_aei(AEI_META)
    assert out["latest_release"] == "2025-09-15"
    assert out["last_modified"] == "2026-06-26"


def test_parse_aei_no_releases():
    out = ucd.parse_aei({"lastModified": "2026-06-26T23:21:00.000Z", "siblings": []})
    assert out["latest_release"] is None


EIA_PAYLOAD = {
    "response": {
        "data": [
            {"period": "2026-05", "status": "OP", "nameplate-capacity-mw": "1000"},
            {"period": "2026-05", "status": "OP", "nameplate-capacity-mw": "500"},
            {"period": "2026-05", "status": "P", "nameplate-capacity-mw": "300"},
            {"period": "2026-05", "status": "L", "nameplate-capacity-mw": "200"},
            {"period": "2026-05", "status": "U", "nameplate-capacity-mw": "120"},
            {"period": "2026-05", "status": "V", "nameplate-capacity-mw": "80"},
            {"period": "2026-04", "status": "OP", "nameplate-capacity-mw": "999999"},
        ]
    }
}


def test_parse_eia_860m_groups_latest_period_by_status():
    out = ucd.parse_eia_860m(EIA_PAYLOAD)
    assert out["asof"] == "2026-05"
    assert out["operating_gw"] == 1.5
    assert out["planned_gw"] == 0.5
    assert out["under_construction_gw"] == 0.2


def test_parse_eia_860m_empty_is_none():
    assert ucd.parse_eia_860m({"response": {"data": []}}) is None
    assert ucd.parse_eia_860m({}) is None


def test_snapshot_row_flattens_payload():
    payload = {
        "updated": "2026-07-19T12:00:00Z",
        "tsmc": {"asof": "2026-06", "rev_ntd_b": 442.7, "yoy_pct": 67.9},
        "ramp": {"asof": "2026-06", "adoption_pct": 55.0},
        "issuance": {"cur": {"debt": 9, "s1_ai": 4, "formd_ai": 8}},
    }
    row = ucd.snapshot_row(payload)
    assert row == ["2026-07-19T12:00:00Z", 442.7, 67.9, 55.0, 9, 4, 8]


def test_snapshot_row_tolerates_missing_blocks():
    row = ucd.snapshot_row({"updated": "2026-07-19T12:00:00Z"})
    assert row[0] == "2026-07-19T12:00:00Z"
    assert row[1:] == ["", "", "", "", "", ""]


def test_append_snapshot_creates_header_then_appends(tmp_path, monkeypatch):
    monkeypatch.setattr(ucd, "SNAP", tmp_path / "capex-snapshots.csv")
    payload = {"updated": "2026-07-19T12:00:00Z",
               "tsmc": {"rev_ntd_b": 442.7, "yoy_pct": 67.9},
               "ramp": {"adoption_pct": 55.0},
               "issuance": {"cur": {"debt": 29, "s1_ai": 391, "formd_ai": 8}}}
    ucd.append_snapshot(payload)
    ucd.append_snapshot(payload)
    lines = (tmp_path / "capex-snapshots.csv").read_text().splitlines()
    assert lines[0] == ("updated,tsmc_rev_ntd_b,tsmc_yoy_pct,"
                       "ramp_adoption_pct,debt_90d,s1_ai_90d,formd_ai_90d")
    assert len(lines) == 3 and lines[1] == lines[2]
    assert lines[1] == "2026-07-19T12:00:00Z,442.7,67.9,55.0,29,391,8"


def test_refresh_survives_failing_fetchers_and_writes_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ucd, "OUT", tmp_path / "capex-data.json")
    monkeypatch.setattr(ucd, "SNAP", tmp_path / "capex-snapshots.csv")
    monkeypatch.setattr(ucd, "fetch_tsmc", lambda: {"asof": "2026-06", "rev_ntd_b": 442.7})
    monkeypatch.setattr(ucd, "fetch_issuance", lambda: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(ucd, "fetch_ramp", lambda: None)
    monkeypatch.setattr(ucd, "fetch_aei", lambda: {"latest_release": "2026-06-26"})
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    ucd.refresh()
    import json
    d = json.loads((tmp_path / "capex-data.json").read_text())
    assert d["tsmc"]["rev_ntd_b"] == 442.7
    assert d["issuance"] is None          # failed fetcher -> null, not a crash
    assert d["eia"] is None               # no key -> gated off
    assert d["manual"] == ucd.MANUAL  # structural — MANUAL values are hand-refreshed
    assert "issuance: FAILED down" in capsys.readouterr().err
    assert (tmp_path / "capex-snapshots.csv").exists()


def test_parse_ramp_csv_unordered_rows_pick_latest_month():
    text = ("date_month,series,adoption_rate_pct,mom_change_pp,yoy_change_pp\n"
            "2026-06-01,Ramp AI Index,55.0,0.8,12.2\n"
            "2026-06-01,U.S. Census BTOS estimate,20.6,0.55,\n"
            "2026-05-01,Ramp AI Index,54.2,1.1,12.8\n"
            "2026-05-01,U.S. Census BTOS estimate,20.0,0.25,\n")
    out = ucd.parse_ramp_csv(text)
    assert out["asof"] == "2026-06"
    assert out["btos_asof"] == "2026-06"
    assert out["btos_pct"] == 20.6


def test_parse_tsmc_rows_malformed_revenue_is_none():
    rows = [{"公司代號": "2330", "資料年月": "11506", "營業收入-當月營收": ""}]
    assert ucd.parse_tsmc_rows(rows) is None
    rows[0]["營業收入-當月營收"] = "N/A"
    assert ucd.parse_tsmc_rows(rows) is None


def test_fetch_issuance_carries_none_counts_without_crashing(monkeypatch):
    monkeypatch.setattr(ucd, "efts_count", lambda *a, **k: None)
    out = ucd.fetch_issuance(datetime.date(2026, 7, 19))
    assert out["cur"]["debt"] is None and out["prev"]["s1_ai"] is None


def test_refresh_eia_failure_is_gated_to_null(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ucd, "OUT", tmp_path / "capex-data.json")
    monkeypatch.setattr(ucd, "SNAP", tmp_path / "capex-snapshots.csv")
    for n in ("fetch_tsmc", "fetch_issuance", "fetch_ramp", "fetch_aei"):
        monkeypatch.setattr(ucd, n, lambda: None)
    monkeypatch.setenv("EIA_API_KEY", "x")
    monkeypatch.setattr(ucd, "fetch_eia", lambda k: (_ for _ in ()).throw(OSError("down")))
    ucd.refresh()
    import json
    assert json.loads((tmp_path / "capex-data.json").read_text())["eia"] is None
    assert "eia: FAILED down" in capsys.readouterr().err


def test_parse_eia_860m_rows_without_period_is_none():
    payload = {"response": {"data": [{"status": "OP", "nameplate-capacity-mw": "1000"}]}}
    assert ucd.parse_eia_860m(payload) is None


def test_fetch_eia_paginates_until_short_page(monkeypatch):
    pages = [
        {"response": {"data": [{"period": "2026-05", "status": "OP",
                                "nameplate-capacity-mw": "1000"}] * ucd.EIA_PAGE}},
        {"response": {"data": [{"period": "2026-05", "status": "P",
                                "nameplate-capacity-mw": "500"}] * 3}},
    ]
    calls = []
    monkeypatch.setattr(ucd, "jget", lambda url, **kw: (calls.append(url), pages[len(calls) - 1])[1])
    out = ucd.fetch_eia("test-key")
    assert len(calls) == 2                       # short second page stops the loop
    assert "offset=5000" in calls[1]
    assert "api_key" not in calls[0]             # key rides the X-Api-Key header only
    assert out["operating_gw"] == 5000.0         # 5000 rows x 1000 MW -> 5000 GW
    assert out["planned_gw"] == 1.5


def test_eia_start_period_is_lookback_months_back():
    import datetime as dt
    # a recent window keeps EIA from sorting its full multi-year table (the timeout cause)
    assert ucd.eia_start_period(dt.date(2026, 7, 15)) == "2025-07"
    assert ucd.eia_start_period(dt.date(2026, 1, 31)) == "2025-01"   # crosses the year


def test_fetch_eia_bounds_the_server_sort_and_lifts_timeout(monkeypatch):
    seen = {}
    def fake_jget(url, **kw):
        seen["url"], seen["timeout"] = url, kw.get("timeout")
        return {"response": {"data": [{"period": "2026-05", "status": "OP",
                                       "nameplate-capacity-mw": "10"}]}}   # short -> one call
    monkeypatch.setattr(ucd, "jget", fake_jget)
    ucd.fetch_eia("k")
    # the recent 'start' filter is what stops EIA sorting every month back to 2015
    assert "start=" in seen["url"]
    # timeout lifted above the 30s default that was tripping on the heavy query
    assert seen["timeout"] is not None and seen["timeout"] >= 60


def test_refresh_carries_forward_prev_on_failure(tmp_path, monkeypatch):
    out = tmp_path / "capex-data.json"
    import json
    out.write_text(json.dumps({
        "tsmc": {"asof": "2026-05", "rev_ntd_b": 400.0},
        "ramp": {"asof": "2026-05", "adoption_pct": 54.0},
        "issuance": {"cur": {"debt": 20}}, "aei": {"latest_release": "old"},
    }))
    monkeypatch.setattr(ucd, "OUT", out)
    monkeypatch.setattr(ucd, "SNAP", tmp_path / "capex-snapshots.csv")
    monkeypatch.setattr(ucd, "fetch_tsmc", lambda: {"asof": "2026-06", "rev_ntd_b": 442.7})
    monkeypatch.setattr(ucd, "fetch_issuance", lambda: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(ucd, "fetch_ramp", lambda: None)
    monkeypatch.setattr(ucd, "fetch_aei", lambda: None)
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    ucd.refresh()
    d = json.loads(out.read_text())
    assert d["tsmc"]["rev_ntd_b"] == 442.7          # live value wins
    assert d["issuance"]["cur"]["debt"] == 20        # failed -> last good carried
    assert d["ramp"]["adoption_pct"] == 54.0         # None -> last good carried
    assert d["aei"]["latest_release"] == "old"


def test_refresh_skips_snapshot_when_all_feeds_down(tmp_path, monkeypatch):
    monkeypatch.setattr(ucd, "OUT", tmp_path / "capex-data.json")
    monkeypatch.setattr(ucd, "SNAP", tmp_path / "capex-snapshots.csv")
    for n in ("fetch_tsmc", "fetch_issuance", "fetch_ramp", "fetch_aei"):
        monkeypatch.setattr(ucd, n, lambda: None)
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    ucd.refresh()
    assert not (tmp_path / "capex-snapshots.csv").exists()  # no blank history row


def test_every_worker_data_path_is_run_worker_first():
    """Guard the class of bug the red team caught: a DATA_FILES path missing from
    wrangler run_worker_first serves the stale bundled asset, not the live R2 copy."""
    import json
    import re
    root = Path(__file__).resolve().parents[1]
    worker = (root / "worker.js").read_text()
    block = re.search(r"const DATA_FILES\s*=\s*\{(.*?)\};", worker, re.S).group(1)
    data_paths = set(re.findall(r'"(/[^"]+)":', block))
    wrangler_raw = (root / "wrangler.jsonc").read_text()
    wrangler = json.loads(re.sub(r"//[^\n]*", "", wrangler_raw))
    rwf = set(wrangler["assets"]["run_worker_first"])
    missing = data_paths - rwf
    assert not missing, f"DATA_FILES paths absent from run_worker_first: {missing}"
