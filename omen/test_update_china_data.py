import importlib.util
import re
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "ucd", Path(__file__).parent / "update-china-data.py")
ucd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ucd)


def tl(*rows):
    return [{"value": list(r)} for r in rows]


def test_parse_gjson_strips_antiscrape_prefix():
    assert ucd.parse_gjson(b")]}',\n{\"a\": 1}") == {"a": 1}


def test_trends_batches_share_anchor_and_respect_5_term_cap():
    batches = ucd.trends_batches()
    assert batches[0][:3] == ucd.TRENDS_TERMS_US
    flat = batches[0] + [t for b in batches[1:] for t in b[1:]]
    assert flat == ucd.TRENDS_TERMS_US + ucd.TRENDS_TERMS_CN
    for b in batches:
        assert len(b) <= 5
        assert b[0] == "ChatGPT"


def test_trends_avgs_averages_per_term():
    assert ucd.trends_avgs(tl([80, 30, 4], [70, 35, 6]), 3) == [75, 32.5, 5]


def test_trends_avgs_rejects_empty_timeline():
    with pytest.raises(ValueError):
        ucd.trends_avgs([], 3)


def test_merge_anchored_rescales_by_shared_anchor():
    batches = [["ChatGPT", "Gemini", "DeepSeek"], ["ChatGPT", "Kimi"]]
    merged = ucd.merge_anchored(batches, [[80, 30, 4], [40, 3]])
    # batch 2 anchor 40 vs 80 -> scale x2
    assert merged == {"ChatGPT": 80, "Gemini": 30, "DeepSeek": 4, "Kimi": 6}


def test_merge_anchored_rejects_zero_anchor():
    with pytest.raises(ValueError):
        ucd.merge_anchored([["ChatGPT", "Gemini"], ["ChatGPT", "Kimi"]],
                           [[80, 30], [0, 3]])


def test_trends_search_consumer_computes_cn_share():
    merged = {t: 0.0 for t in ucd.TRENDS_TERMS_US + ucd.TRENDS_TERMS_CN}
    merged.update({"ChatGPT": 75, "Gemini": 32.5, "Claude": 27.5,
                   "DeepSeek": 4, "Qwen": 1})
    sc = ucd.trends_search_consumer(merged)
    # cn 5 / total 140 = 3.57%
    assert sc["western_share_pct"] == 3.6
    assert sc["source"] == "google-trends"
    assert re.fullmatch(r"\d{4}-\d{2}", sc["asof"])
    assert "Google Trends" in sc["note"]


def test_trends_search_consumer_rejects_all_zero():
    merged = {t: 0 for t in ucd.TRENDS_TERMS_US + ucd.TRENDS_TERMS_CN}
    with pytest.raises(ValueError):
        ucd.trends_search_consumer(merged)


def test_pick_search_consumer_prefers_fresh():
    fresh = {"western_share_pct": 2.7, "source": "google-trends"}
    assert ucd.pick_search_consumer(fresh, {}, ucd.MANUAL["search_consumer"]) is fresh


def test_pick_search_consumer_carries_forward_previous_trends_value():
    prev = {"search_consumer": {"western_share_pct": 3.1, "source": "google-trends", "asof": "2026-06"}}
    got = ucd.pick_search_consumer(None, prev, ucd.MANUAL["search_consumer"])
    assert got == prev["search_consumer"]


def test_pick_search_consumer_falls_back_to_manual():
    # previous value without a trends source (old Goodie snapshot) is not carried
    prev = {"search_consumer": {"western_share_pct": 1, "asof": "2026-04"}}
    got = ucd.pick_search_consumer(None, prev, ucd.MANUAL["search_consumer"])
    assert got == ucd.MANUAL["search_consumer"]
