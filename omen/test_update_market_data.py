import importlib.util
from pathlib import Path

import pytest

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


def test_kalshi_mid_reads_dollar_denominated_fields():
    # regression: Kalshi renamed quote fields to *_dollars and they are already
    # dollars (0.23), not cents. The old yes_bid/yes_ask read returned None and
    # silently emptied the whole cross-venue panel.
    m = {"yes_bid_dollars": "0.2300", "yes_ask_dollars": "0.2600"}
    price, spread = umd.kalshi_mid(m)
    assert round(price, 4) == 0.245
    assert round(spread, 4) == 0.03


def test_kalshi_mid_rejects_one_sided_book():
    # bid 0 / ask 1 is an empty book quoted at the bounds, not a 50c market
    assert umd.kalshi_mid({"yes_bid_dollars": "0.0000", "yes_ask_dollars": "0.9900"}) == (None, None)
    assert umd.kalshi_mid({"yes_bid_dollars": "0.2200", "yes_ask_dollars": "1.0000"}) == (None, None)
    assert umd.kalshi_mid({}) == (None, None)


def test_kalshi_last_price_falls_back_for_display():
    # cross-venue table may show a last print when the book is one-sided
    assert umd.kalshi_price({"yes_bid_dollars": "0.2300", "yes_ask_dollars": "0.2600"}) == 0.245
    assert umd.kalshi_price({"yes_bid_dollars": "0.0000", "yes_ask_dollars": "0.9900",
                             "last_price_dollars": "0.2100"}) == 0.21
    assert umd.kalshi_price({"last_price_dollars": "0.0000"}) is None


def test_kalshi_ladder_filters_wide_spreads_and_enforces_monotonic_survival():
    markets = [
        {"floor_strike": 2.0, "yes_bid_dollars": "0.94", "yes_ask_dollars": "0.98"},
        # non-monotonic print: survival cannot rise with strike -> clamped down
        {"floor_strike": 2.5, "yes_bid_dollars": "0.97", "yes_ask_dollars": "0.99"},
        # too wide to trust
        {"floor_strike": 2.7, "yes_bid_dollars": "0.10", "yes_ask_dollars": "0.90"},
        # one-sided -> dropped
        {"floor_strike": 2.8, "yes_bid_dollars": "0.00", "yes_ask_dollars": "0.99"},
        {"floor_strike": 3.0, "yes_bid_dollars": "0.03", "yes_ask_dollars": "0.07"},
    ]
    rows = umd.kalshi_ladder(markets)
    assert [r["k"] for r in rows] == [2.0, 2.5, 3.0]
    assert rows[0]["p"] == 0.96
    assert rows[1]["p"] == 0.96          # clamped from 0.98 to preserve monotonicity
    assert rows[2]["p"] == 0.05


def test_implied_median_interpolates_the_fifty_percent_crossing():
    rows = [{"k": 2.0, "p": 0.8}, {"k": 3.0, "p": 0.4}]
    # survival falls 0.8 -> 0.4 across $1.00; 50% sits 3/4 of the way: $2.75
    assert round(umd.implied_median(rows), 4) == 2.75
    # exact hit at a strike returns that strike
    assert umd.implied_median([{"k": 2.0, "p": 0.5}, {"k": 3.0, "p": 0.2}]) == 2.0


def test_implied_median_none_when_crossing_outside_ladder():
    # entire ladder above 50% -> median is beyond the highest strike, unknowable
    assert umd.implied_median([{"k": 2.0, "p": 0.9}, {"k": 3.0, "p": 0.7}]) is None
    assert umd.implied_median([{"k": 2.0, "p": 0.2}]) is None
    assert umd.implied_median([]) is None


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
    # A lone market can raise Elevated but never trips Stressed on its own. Hold the rest
    # of the crash sleeve cold so these exercise the single-market path, not the sleeve
    # average (which stays well under 25 here).
    cold = {i: 0.02 for i in umd.BEAR_SLEEVES["mkt"][1:]}
    # bubble >= 15% forces at least elevated...
    assert umd.compute_regime(10, dict(cold, **{umd.BUBBLE_ID: 0.16})) == "elevated"
    # ...but even a very hot single market is capped at elevated, not stressed
    assert umd.compute_regime(10, dict(cold, **{umd.BUBBLE_ID: 0.30})) == "elevated"
    assert umd.compute_regime(10, dict(cold, **{umd.BUBBLE_ID: 0.50})) == "elevated"
    # a cold single market with a calm gauge and cold sleeve stays calm
    assert umd.compute_regime(10, dict(cold, **{umd.BUBBLE_ID: 0.10})) == "calm"


def test_bear_basket_is_the_union_of_its_sleeves():
    assert umd.POLY_IDS["bear"] == umd.BEAR_SLEEVES["mkt"] + umd.BEAR_SLEEVES["gov"]
    assert len(umd.POLY_IDS["bear"]) == 9
    assert len(set(umd.POLY_IDS["bear"])) == 9
    assert umd.BUBBLE_ID in umd.BEAR_SLEEVES["mkt"]
    # the two short-side sleeves are disjoint, so the union is a clean 3 + 6
    assert not set(umd.BEAR_SLEEVES["mkt"]) & set(umd.BEAR_SLEEVES["gov"])
    assert (len(umd.BEAR_SLEEVES["mkt"]), len(umd.BEAR_SLEEVES["gov"])) == (3, 6)


def test_bear_level_is_the_flat_mean_of_all_nine_not_the_mean_of_sleeve_means():
    # sleeves are unequal (3 vs 6), so a mean-of-means would differ from the flat mean –
    # this pins the composite to the equal-weight union the methodology promises
    price = {i: 0.10 for i in umd.BEAR_SLEEVES["mkt"]}
    price.update({i: 0.40 for i in umd.BEAR_SLEEVES["gov"]})
    assert umd.index_level(price, "bear") == pytest.approx(30.0)   # (3*10 + 6*40)/9
    assert umd.sleeve_level(price, "mkt") == pytest.approx(10.0)
    assert umd.sleeve_level(price, "gov") == pytest.approx(40.0)


def test_index_level_skips_markets_missing_from_the_price_map():
    price = {umd.BEAR_SLEEVES["mkt"][0]: 0.20, umd.BEAR_SLEEVES["gov"][0]: 0.60}
    assert umd.index_level(price, "bear") == pytest.approx(40.0)   # mean of the 2 present
    assert umd.index_level({}, "bear") is None


def test_regime_reads_the_mkt_sleeve_not_the_bear_composite():
    # the gauge is about priced *crash* risk: its thresholds must keep firing off the
    # old crash basket (= the MKT sleeve). A hot GOV sleeve lifts Bear but must not
    # move the regime, or the merge would silently retune the gauge.
    price = {i: 0.02 for i in umd.BEAR_SLEEVES["mkt"]}
    price.update({i: 0.95 for i in umd.BEAR_SLEEVES["gov"]})
    assert umd.index_level(price, "bear") > 60      # composite is way past the 40 trip
    assert umd.compute_regime(10, price) == "calm"  # ...but crash risk is not priced
    # and the MKT sleeve still trips the bands on its own. Hold the bubble market cold
    # so these exercise the sleeve-level rule, not the separate bubble-market rule.
    cold_bubble = {umd.BUBBLE_ID: 0.10}
    stressed = dict(cold_bubble, **{i: 0.60 for i in umd.BEAR_SLEEVES["mkt"][1:]})
    assert umd.sleeve_level(stressed, "mkt") == pytest.approx(43.33, abs=0.01)
    assert umd.compute_regime(10, stressed) == "stressed"      # level >= 40
    elevated = dict(cold_bubble, **{i: 0.35 for i in umd.BEAR_SLEEVES["mkt"][1:]})
    assert umd.sleeve_level(elevated, "mkt") == pytest.approx(26.67, abs=0.01)
    assert umd.compute_regime(10, elevated) == "elevated"      # 25 <= level < 40


def test_snapshot_row_keeps_crash_and_reg_as_sleeve_provenance():
    price = {i: 0.10 for i in umd.BEAR_SLEEVES["mkt"]}
    price.update({i: 0.40 for i in umd.BEAR_SLEEVES["gov"]})
    price.update({i: 0.50 for i in umd.POLY_IDS["bull"]})
    row = umd.snapshot_row(price)
    assert row["bear"] == pytest.approx(30.0)
    assert row["bear_n"] == 9
    # crash/reg columns live on as the sleeve reads, so the stored series stays comparable
    assert row["crash"] == pytest.approx(10.0)
    assert row["crash_n"] == 3
    assert row["reg"] == pytest.approx(40.0)
    assert row["reg_n"] == 6
    # the fallback backfill formula in the spec must reproduce the flat union
    assert (row["crash_n"] * row["crash"] + row["reg_n"] * row["reg"]) / (
        row["crash_n"] + row["reg_n"]) == pytest.approx(row["bear"])


def test_snapshot_header_is_bear_plus_legacy_columns():
    assert umd.SNAP_HEADER == ["date", "bull", "bull_n", "bear", "bear_n",
                               "crash", "crash_n", "reg", "reg_n",
                               "gauge", "lead", "conf", "comp"]
