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


def test_us_terms_include_llama_and_meta():
    assert "Llama AI" in ucd.TRENDS_TERMS_US
    assert "Meta AI" in ucd.TRENDS_TERMS_US


def test_trends_batches_share_anchor_and_respect_5_term_cap():
    batches = ucd.trends_batches()
    assert batches[0][:len(ucd.TRENDS_TERMS_US)] == ucd.TRENDS_TERMS_US
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


# ---- consumer-app Western chart presence (iOS Apple RSS + Android Play) ----

def test_app_points_maps_rank_to_top100_scale():
    # top-100 only: #1 -> 100, #100 -> 1, anything past the top-100 -> 0
    assert ucd.app_points(1) == 100
    assert ucd.app_points(82) == 19    # Kimi CA Play snapshot (only real top-100 hit)
    assert ucd.app_points(100) == 1
    assert ucd.app_points(101) == 0
    assert ucd.app_points(194) == 0    # Talkie US Play: charts, but far outside top-100


def test_match_app_identifies_basket_by_title_or_appid():
    assert ucd.match_app("DeepSeek - AI Assistant", "com.deepseek.chat") == "DeepSeek"
    assert ucd.match_app("Kimi", "com.moonshot.kimichat") == "Kimi"
    # Talkie carries no "minimax" in its title/appId; matched via the talkie/weaver pattern
    assert ucd.match_app("Talkie: Creative AI Community", "com.weaver.app.prod") == "MiniMax"
    assert ucd.match_app("ChatGPT", "com.openai.chatgpt") is None
    assert ucd.match_app("TikTok Pro - Events", "com.ss.android.ugc.tiktok.pro") is None


def test_apps_score_is_basket_mean_of_best_per_app_points():
    # Kimi cracks a top-100 (CA #82); the rest only chart in the 101-200 tail
    hits = [
        {"label": "Kimi", "store": "android", "country": "ca", "rank": 82},
        {"label": "Kimi", "store": "android", "country": "gb", "rank": 186},   # worse dup ignored
        {"label": "MiniMax", "store": "android", "country": "br", "rank": 112},
        {"label": "MiniMax", "store": "android", "country": "us", "rank": 194},
    ]
    out = ucd.apps_score(hits)
    # basket of 5: (Kimi 19 + MiniMax 0 + DeepSeek 0 + Qwen 0 + Doubao 0) / 5 = 3.8 -> 4
    assert out["score"] == 4
    assert out["source"] == "app-charts"
    assert out["best"][0]["label"] == "Kimi" and out["best"][0]["rank"] == 82
    assert "Kimi" in out["detail"]  # detail surfaces the top-100 hit


def test_apps_score_zero_when_no_hits():
    out = ucd.apps_score([])
    assert out["score"] == 0
    assert out["source"] == "app-charts"
    assert "no chinese ai app" in out["detail"].lower()


def test_apps_score_rewards_a_top_ranked_app():
    hits = [{"label": "DeepSeek", "store": "ios", "country": "us", "rank": 1}]
    out = ucd.apps_score(hits)
    assert out["score"] == 20  # (100 + 0 + 0 + 0 + 0) / 5


def test_pick_apps_prefers_fresh():
    fresh = {"score": 2, "source": "app-charts"}
    assert ucd.pick_apps(fresh, {}, ucd.MANUAL["apps"]) is fresh


def test_pick_apps_carries_forward_previous_computed_value():
    prev = {"apps": {"score": 3, "source": "app-charts", "asof": "2026-07-18"}}
    got = ucd.pick_apps(None, prev, ucd.MANUAL["apps"])
    assert got == prev["apps"]


def test_pick_apps_falls_back_to_manual():
    # previous judgmental snapshot (no app-charts source) is not carried
    prev = {"apps": {"score": 20, "asof": "2026-01"}}
    got = ucd.pick_apps(None, prev, ucd.MANUAL["apps"])
    assert got == ucd.MANUAL["apps"]
