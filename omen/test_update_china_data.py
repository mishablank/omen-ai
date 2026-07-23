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


def test_pick_github_velocity_prefers_fresh_measurement():
    assert ucd.pick_github_velocity(41.66, {"github_stars_per_day": 99.0}) == 41.7


def test_pick_github_velocity_carries_forward_previous_value():
    prev = {"github_stars_per_day": 32.3}
    assert ucd.pick_github_velocity(None, prev) == 32.3


def test_pick_github_velocity_none_when_never_measured():
    assert ucd.pick_github_velocity(None, {}) is None
    assert ucd.pick_github_velocity(None, None) is None


# ---- new source families (2026-07-20) --------------------------------------------

def test_parse_count_handles_suffixes_and_commas():
    assert ucd.parse_count("117.3M") == 117300000
    assert ucd.parse_count("2.1K") == 2100
    assert ucd.parse_count("5,000") == 5000
    assert ucd.parse_count("1B") == 1000000000


def test_ollama_side_classifies_named_families_only():
    assert ucd.ollama_side("deepseek-r1") == "cn"
    assert ucd.ollama_side("qwen2.5-coder") == "cn"
    assert ucd.ollama_side("llama3.1") == "us"
    assert ucd.ollama_side("gpt-oss") == "us"
    assert ucd.ollama_side("mistral") is None       # FR - outside both baskets
    assert ucd.ollama_side("llava") is None          # academic community model


def test_ollama_models_parses_library_chunks():
    page = ('<a href="/library/deepseek-r1" class="group">'
            '<span >90M</span>\n<span class="hidden sm:flex">&nbsp;Pulls</span></a>'
            '<a href="/library/llama3.1"><span>117.3M</span> <span>&nbsp;Pulls</span></a>')
    got = ucd.ollama_models(page)
    assert got == [{"name": "deepseek-r1", "pulls": 90000000},
                   {"name": "llama3.1", "pulls": 117300000}]


def test_ollama_models_rejects_pageful_of_nothing():
    with pytest.raises(ValueError):
        ucd.ollama_models("<html>redesigned page</html>")


def test_vercel_days_aggregates_token_share_by_date():
    rows = [
        {"date": "2026-07-18", "name": "deepseek", "metric": "tokens", "share_percent": 20.0},
        {"date": "2026-07-18", "name": "anthropic", "metric": "tokens", "share_percent": 30.0},
        {"date": "2026-07-18", "name": "deepseek", "metric": "requests", "share_percent": 99.0},
        {"date": "2026-07-19", "name": "zai", "metric": "tokens", "share_percent": 5.0},
        {"date": "2026-07-19", "name": "somelab", "metric": "tokens", "share_percent": 4.0},
    ]
    got = ucd.vercel_days(rows)
    assert got == [{"d": "2026-07-18", "cn": 20.0, "us": 30.0},
                   {"d": "2026-07-19", "cn": 5.0, "us": 0.0}]


def test_vercel_days_rejects_export_without_token_rows():
    with pytest.raises(ValueError):
        ucd.vercel_days([{"date": "2026-07-18", "name": "x", "metric": "spend", "share_percent": 1}])


def test_arena_summary_counts_cn_orgs_case_insensitively():
    rows = [
        {"model": "claude-fable-5", "org": "anthropic", "rank": 1, "elo": 1507},
        {"model": "kimi-k3", "org": "moonshot", "rank": 10, "elo": 1486},
        {"model": "qwen-max", "org": "Alibaba", "rank": 15, "elo": 1470},
    ]
    got = ucd.arena_summary(rows)
    assert got["best_model"] == "kimi-k3"
    assert got["us_leader"] == "claude-fable-5"
    assert (got["top10"], got["top20"]) == (1, 2)


def test_arena_summary_strips_ai_suffix_from_org():
    rows = [
        {"model": "us", "org": "OpenAI", "rank": 1, "elo": 1500},
        {"model": "k2", "org": "Moonshot AI", "rank": 5, "elo": 1480},
    ]
    assert ucd.arena_summary(rows)["best_org"] == "Moonshot AI"


def test_arena_summary_rejects_all_us_board():
    with pytest.raises(ValueError):
        ucd.arena_summary([{"model": "m", "org": "openai", "rank": 1, "elo": 1500}])


def test_kalshi_price_prefers_dollars_string():
    assert ucd.kalshi_price({"last_price_dollars": "0.1900"}) == 0.19
    assert ucd.kalshi_price({"last_price_dollars": None}) is None
    assert ucd.kalshi_price({}) is None


def test_kalshi_pick_yearend_cn_brands_plus_top_us_reference():
    mkts = [
        {"event_ticker": "KXLLM1-26DEC31", "yes_sub_title": "Kimi", "last_price_dollars": "0.02"},
        {"event_ticker": "KXLLM1-26DEC31", "yes_sub_title": "Qwen", "last_price_dollars": "0.009"},
        {"event_ticker": "KXLLM1-26DEC31", "yes_sub_title": "Claude", "last_price_dollars": "0.61"},
        {"event_ticker": "KXLLM1-26DEC31", "yes_sub_title": "ChatGPT", "last_price_dollars": "0.15"},
        {"event_ticker": "KXLLM1-26JUL20", "yes_sub_title": "Kimi", "last_price_dollars": "0.01"},
    ]
    cn, us_ref = ucd.kalshi_pick(mkts)
    assert [m["yes_sub_title"] for m in cn] == ["Kimi", "Qwen"]
    assert [m["yes_sub_title"] for m in us_ref] == ["Claude"]


def test_parse_finetune_count_reads_model_tree_link():
    page = ('... <a class="x" href="/models?other=base_model:finetune:Qwen/Qwen3-8B">'
            "1,951 models</a> ...")
    assert ucd.parse_finetune_count(page, "Qwen/Qwen3-8B") == 1951
    with pytest.raises(ValueError):
        ucd.parse_finetune_count(page, "meta-llama/Llama-3.1-8B")


def test_aa_best_picks_top_intelligence_index_per_side():
    models = [
        {"name": "GLM-5.2", "model_creator": {"name": "Zhipu AI"},
         "evaluations": {"artificial_analysis_intelligence_index": 51.2}},
        {"name": "DeepSeek V4", "model_creator": {"name": "DeepSeek"},
         "evaluations": {"artificial_analysis_intelligence_index": 49.0}},
        {"name": "Claude Fable 5", "model_creator": {"name": "Anthropic"},
         "evaluations": {"artificial_analysis_intelligence_index": 60.1}},
        {"name": "noscore", "model_creator": {"name": "OpenAI"}, "evaluations": {}},
    ]
    got = ucd.aa_best(models)
    assert (got["cn_best"], got["cn_score"]) == ("GLM-5.2", 51)
    assert (got["us_best"], got["us_score"]) == ("Claude Fable 5", 60)
    assert got["source"] == "aa-api"


def test_aa_best_rejects_one_sided_data():
    with pytest.raises(ValueError):
        ucd.aa_best([{"name": "m", "model_creator": {"name": "Anthropic"},
                      "evaluations": {"artificial_analysis_intelligence_index": 60}}])


def test_pick_aa_carries_forward_api_value_only():
    api_prev = {"artificial_analysis": {"cn_score": 52, "source": "aa-api"}}
    manual_prev = {"artificial_analysis": {"cn_score": 51}}
    fresh = {"cn_score": 55, "source": "aa-api"}
    assert ucd.pick_aa(fresh, api_prev, ucd.MANUAL["artificial_analysis"]) == fresh
    assert ucd.pick_aa(None, api_prev, ucd.MANUAL["artificial_analysis"]) == api_prev["artificial_analysis"]
    assert ucd.pick_aa(None, manual_prev, ucd.MANUAL["artificial_analysis"]) == ucd.MANUAL["artificial_analysis"]


def test_radar_rows_marks_cn_services_and_tolerates_shapes():
    got = ucd.radar_rows({"top_0": [{"rank": 1, "service": "ChatGPT"},
                                    {"rank": 9, "service": "DeepSeek"}]})
    assert got == [{"rank": 1, "name": "ChatGPT", "cn": False},
                   {"rank": 9, "name": "DeepSeek", "cn": True}]
    got2 = ucd.radar_rows({"serviceTop": [{"name": "Kimi"}]})
    assert got2[0]["cn"] is True and got2[0]["rank"] == 1
    with pytest.raises(ValueError):
        ucd.radar_rows({})
