"""Catalog, tier, cache-write, expiry and legacy pricing evidence."""

from __future__ import annotations

import pytest

from kronika.domain.research import (
    ResearchUsage,
    ResearchValueError,
    UsageTokenPrices,
    usage_cost_micro_usd,
    usage_cost_micro_usd_or_none,
)
from kronika.infrastructure.ai.research_models import (
    DEFAULT_RESEARCH_MODEL_ID,
    LEGACY_OPENAI_2026_09_26,
    LEGACY_RESEARCH_ADMISSION_VERSION,
    LONG_CONTEXT_THRESHOLD_TOKENS,
    LUNA_MODEL_ID,
    OPENAI_STANDARD_2026_09_30,
    RESEARCH_ADMISSION_PROFILE_VERSION,
    RESEARCH_MODEL_CATALOG,
    SOL_MODEL_ID,
    SOL_VALID_UNTIL,
    TERRA_MODEL_ID,
    known_selectable_model_ids,
    lookup_model,
    model_admissible_now,
    model_has_expired,
    resolve_usage_price_schedule,
)


def _usage(**fields) -> ResearchUsage:
    base = {
        "input_tokens": 10_000,
        "cached_input_tokens": 2_000,
        "output_tokens": 1_000,
        "reasoning_tokens": 0,
        "web_tool_calls": 2,
    }
    base.update(fields)
    return ResearchUsage(**base)


def test_catalog_has_exactly_the_four_confirmed_models() -> None:
    assert known_selectable_model_ids() == (
        "gpt-5.5-2026-04-23",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    )
    assert len(RESEARCH_MODEL_CATALOG) == 4
    default = lookup_model(DEFAULT_RESEARCH_MODEL_ID)
    assert default is not None
    assert default.pinning == "dated_snapshot"
    assert default.valid_until is None
    assert default.price_schedule_version == OPENAI_STANDARD_2026_09_30


def test_unknown_alias_and_arbitrary_models_are_refused() -> None:
    for alias in ("gpt-5.5", "gpt-5.6", "gpt-5.5-pro", "gpt-5.4", "not-a-model"):
        assert lookup_model(alias) is None
        assert resolve_usage_price_schedule(
            "openai-responses", alias, RESEARCH_ADMISSION_PROFILE_VERSION
        ) is None


def test_sol_carries_the_reviewable_valid_until_cutoff() -> None:
    sol = lookup_model(SOL_MODEL_ID)
    assert sol is not None
    assert sol.valid_until == SOL_VALID_UNTIL
    cutoff_ms = 1_800_000_000_000
    before = 1_700_000_000_000
    assert model_has_expired(SOL_MODEL_ID, now_ms=before) is False
    assert model_admissible_now(SOL_MODEL_ID, now_ms=before) is True
    assert model_has_expired(SOL_MODEL_ID, now_ms=cutoff_ms) is True
    assert model_admissible_now(SOL_MODEL_ID, now_ms=cutoff_ms) is False
    assert model_has_expired(DEFAULT_RESEARCH_MODEL_ID, now_ms=cutoff_ms) is False


def test_luna_short_fixture_matches_the_documented_cost() -> None:
    schedule = lookup_model(LUNA_MODEL_ID).schedule
    usage = ResearchUsage(
        input_tokens=10_000,
        cached_input_tokens=2_000,
        output_tokens=1_000,
        reasoning_tokens=0,
        web_tool_calls=2,
        cache_write_input_tokens=3_000,
    )
    assert usage_cost_micro_usd(usage, schedule) == 22_990


def test_luna_long_fixture_matches_the_documented_cost() -> None:
    schedule = lookup_model(LUNA_MODEL_ID).schedule
    usage = ResearchUsage(
        input_tokens=300_000,
        cached_input_tokens=100_000,
        output_tokens=10_000,
        reasoning_tokens=0,
        web_tool_calls=2,
        cache_write_input_tokens=50_000,
    )
    assert usage_cost_micro_usd(usage, schedule) == 127_000


def test_threshold_boundary_selects_short_at_272000_and_long_at_272001() -> None:
    schedule = lookup_model(DEFAULT_RESEARCH_MODEL_ID).schedule
    assert schedule.long_context_threshold_tokens == LONG_CONTEXT_THRESHOLD_TOKENS == 272_000
    short = usage_cost_micro_usd(
        ResearchUsage(272_000, 0, 0, 0, 0), schedule
    )
    long = usage_cost_micro_usd(
        ResearchUsage(272_001, 0, 0, 0, 0), schedule
    )
    # Short input rate is 5 USD/M; long input rate is 10 USD/M.
    assert short == 1_360_000
    assert long == 2_720_010


def test_missing_gpt_5_6_cache_write_is_unknown_accounting() -> None:
    schedule = lookup_model(LUNA_MODEL_ID).schedule
    usage = ResearchUsage(
        input_tokens=10_000,
        cached_input_tokens=2_000,
        output_tokens=1_000,
        reasoning_tokens=0,
        web_tool_calls=2,
    )
    assert usage_cost_micro_usd_or_none(usage, schedule) is None
    with pytest.raises(ResearchValueError):
        usage_cost_micro_usd(usage, schedule)


def test_missing_gpt_5_5_cache_write_stays_derivable() -> None:
    schedule = lookup_model(DEFAULT_RESEARCH_MODEL_ID).schedule
    usage = ResearchUsage(
        input_tokens=10_000,
        cached_input_tokens=2_000,
        output_tokens=1_000,
        reasoning_tokens=0,
        web_tool_calls=2,
    )
    # GPT-5.5 cache-write rate equals the ordinary input rate, so absence is
    # derivable by charging I - R at the ordinary input rate.
    assert usage_cost_micro_usd_or_none(usage, schedule) is not None


def test_legacy_schedule_keeps_the_flat_rates_and_unknown_long_tier() -> None:
    schedule = resolve_usage_price_schedule(
        "openai-responses", DEFAULT_RESEARCH_MODEL_ID, LEGACY_RESEARCH_ADMISSION_VERSION
    )
    assert schedule is not None
    assert schedule.input_micro_usd_per_million == 5_000_000
    assert schedule.cached_input_micro_usd_per_million == 500_000
    assert schedule.output_micro_usd_per_million == 30_000_000
    small = usage_cost_micro_usd_or_none(
        ResearchUsage(1_000, 0, 0, 0, 0), schedule
    )
    assert small == 5_000
    over = usage_cost_micro_usd_or_none(
        ResearchUsage(300_000, 0, 0, 0, 0), schedule
    )
    assert over is None


def test_legacy_tuple_only_resolves_the_default_model() -> None:
    assert (
        resolve_usage_price_schedule(
            "openai-responses", LUNA_MODEL_ID, LEGACY_RESEARCH_ADMISSION_VERSION
        )
        is None
    )
    assert resolve_usage_price_schedule(
        "chatgpt-page", DEFAULT_RESEARCH_MODEL_ID, RESEARCH_ADMISSION_PROFILE_VERSION
    ) is None
    assert resolve_usage_price_schedule(
        "openai-responses", DEFAULT_RESEARCH_MODEL_ID, "unknown-version"
    ) is None


def test_legacy_version_constant_marks_the_original_schedule() -> None:
    assert LEGACY_OPENAI_2026_09_26 == "openai-2026-09-26"
    assert isinstance(
        UsageTokenPrices(1, 1, 1, 1), UsageTokenPrices
    )
    terra = lookup_model(TERRA_MODEL_ID)
    assert terra is not None
    assert terra.short_prices.cache_write_input_micro_usd_per_million == 2_500_000
    assert terra.long_prices.output_micro_usd_per_million == 18_000_000
