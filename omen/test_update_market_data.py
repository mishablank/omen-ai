import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "umd", Path(__file__).parent / "update-market-data.py")
umd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(umd)


def test_parse_fred_csv_skips_missing_and_parses_floats():
    csv = "DATE,BAMLH0A0HYM2\n2026-07-07,2.67\n2026-07-08,.\n2026-07-09,2.70\n"
    out = umd.parse_fred_csv(csv)
    assert out == [{"d": "2026-07-07", "c": 2.67}, {"d": "2026-07-09", "c": 2.70}]


def test_parse_fred_csv_keeps_tail():
    csv = "DATE,X\n" + "\n".join(f"2026-01-{i:02d},{i}" for i in range(1, 31))
    out = umd.parse_fred_csv(csv, keep=5)
    assert len(out) == 5 and out[-1]["c"] == 30.0


FORM4 = """<?xml version="1.0"?>
<ownershipDocument>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>100</value></transactionShares>
        <transactionPricePerShare><value>210.50</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>10</value></transactionShares>
        <transactionPricePerShare><value>200</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>9999</value></transactionShares>
        <transactionPricePerShare><value>1</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_parse_form4_counts_only_open_market_s_and_p():
    sells, buys = umd.parse_form4_xml(FORM4)
    assert sells == 100 * 210.50
    assert buys == 10 * 200


def test_parse_form4_bad_xml_is_zero():
    assert umd.parse_form4_xml("<not xml") == (0.0, 0.0)


def test_bs_prob_below_is_sane_and_monotonic():
    # deeper strikes must be less likely; a rough known value anchors the math
    p50 = umd.bs_prob_below(210.0, 105.0, 0.51, 340)
    p30 = umd.bs_prob_below(210.0, 147.0, 0.46, 340)
    assert p50 is not None and p30 is not None
    assert 0.05 < p50 < 0.20          # ~10% at current NVDA vols
    assert p30 > p50                  # -30% strictly more likely than -50%
    assert umd.bs_prob_below(210.0, 105.0, 0, 340) is None
    assert umd.bs_prob_below(None, 105.0, 0.5, 340) is None


def test_quarterlize_differences_cumulative_flows():
    # calendar-FY filer: Q1 direct, 6mo/9mo/FY cumulative -> quarters by subtraction
    entries = [
        {"start": "2025-01-01", "end": "2025-03-31", "val": 10.0},
        {"start": "2025-01-01", "end": "2025-06-30", "val": 25.0},
        {"start": "2025-01-01", "end": "2025-09-30", "val": 45.0},
        {"start": "2025-01-01", "end": "2025-12-31", "val": 70.0},
    ]
    q = umd.quarterlize(entries)
    assert q["2025Q1"] == 10.0
    assert q["2025Q2"] == 15.0
    assert q["2025Q3"] == 20.0
    assert q["2025Q4"] == 25.0


def test_quarterlize_maps_offset_fiscal_years_to_calendar_quarters():
    # June-FY filer (MSFT-style): fiscal Q2 ends Dec 31 -> calendar Q4
    entries = [
        {"start": "2025-07-01", "end": "2025-09-30", "val": 30.0},
        {"start": "2025-07-01", "end": "2025-12-31", "val": 64.0},
    ]
    q = umd.quarterlize(entries)
    assert q["2025Q3"] == 30.0
    assert q["2025Q4"] == 34.0


def test_gauge_groups_split():
    fam = {"pred": 30.0, "opt": 40.0, "credit": 20.0, "vol": 50.0, "equity": 70.0}
    lead, conf = umd.gauge_groups(fam)
    assert lead == 30.0
    assert conf == 60.0
    lead2, conf2 = umd.gauge_groups({"pred": 10.0, "opt": None, "credit": None,
                                     "vol": None, "equity": None})
    assert lead2 == 10.0 and conf2 is None


def test_quarter_of_maps_fred_quarter_start_dates():
    assert umd.quarter_of("2025-01-01") == "2025Q1"
    assert umd.quarter_of("2025-04-01") == "2025Q2"
    assert umd.quarter_of("2025-07-01") == "2025Q3"
    assert umd.quarter_of("2025-10-15") == "2025Q4"


def test_macro_capex_gdp_shares_and_growth_contribution():
    # fundamentals capex is a single-quarter figure ($B); GDP is SAAR ($B).
    # capex must be annualized (x4) before comparing to SAAR GDP.
    fund = {"quarters": ["2025Q1", "2025Q2"], "capex_b": [50.0, 60.0]}
    gdp = [{"d": "2025-01-01", "c": 29000.0}, {"d": "2025-04-01", "c": 29400.0}]
    m = umd.macro_capex_gdp(fund, gdp)
    assert m["quarters"] == ["2025Q1", "2025Q2"]
    assert m["capex_ann_b"] == [200.0, 240.0]
    # 200/29000 = 0.690%, 240/29400 = 0.816%
    assert round(m["pct_gdp"][0], 3) == 0.690
    assert round(m["pct_gdp"][1], 3) == 0.816
    # growth contribution: d(annualized capex)=40 over d(GDP)=400 -> 10%
    assert m["growth_share"][0] is None          # no prior quarter
    assert round(m["growth_share"][1], 1) == 10.0


def test_macro_capex_gdp_handles_missing_gdp_quarter():
    fund = {"quarters": ["2025Q1", "2025Q2"], "capex_b": [50.0, 60.0]}
    gdp = [{"d": "2025-01-01", "c": 29000.0}]     # Q2 GDP not published yet
    m = umd.macro_capex_gdp(fund, gdp)
    assert m["quarters"] == ["2025Q1"]            # unmatched quarter dropped
    assert m["capex_ann_b"] == [200.0]


def test_macro_capex_gdp_empty_inputs_return_none():
    assert umd.macro_capex_gdp(None, [{"d": "2025-01-01", "c": 1.0}]) is None
    assert umd.macro_capex_gdp({"quarters": [], "capex_b": []}, []) is None


def test_gauge_families_and_regime():
    data = {
        "skew": {"NVDA": {"rr": 0.055}, "SOXX": {"rr": 0.095}},
        "vol": {"VIX": {"last": 20.0}, "VIX3M": {"last": 20.0},
                "VXN": {"last": 29.0}, "SKEW": {"last": 137.5}, "VVIX": {"last": 110.0}},
        "credit": {"HYG": [{"c": 100}, {"c": 96}], "LQD": [{"c": 100}, {"c": 100}]},
        "fred": {"HY_OAS": {"last": 3.75}, "CCC_OAS": {"last": 11.25}},
        "equity": {"NVDA": [{"c": 100}, {"c": 75}], "SOXX": [{"c": 100}, {"c": 80}]},
    }
    price = {umd.BUBBLE_ID: 0.20}
    score, fam = umd.compute_gauge(data, price)
    assert fam["pred"] == 50.0                     # 20% of 0-40 range
    assert round(fam["equity"]) == 50              # -25%/50 and -20%/40 both 50
    assert 0 < score < 100
    assert umd.compute_regime(score, price) in ("calm", "elevated", "stressed")
    # bubble >= 25% forces stressed regardless of gauge
    assert umd.compute_regime(10, {umd.BUBBLE_ID: 0.30}) == "stressed"
    # bubble >= 15% forces at least elevated
    assert umd.compute_regime(10, {umd.BUBBLE_ID: 0.16}) == "elevated"
