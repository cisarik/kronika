"""Immutable research model and price catalog.

The catalog is the single source of truth for which models may be selected,
what they cost, and when their prices stop being admitted. It is deterministic,
network-free and performs no provider discovery. Unknown or alias models are
rejected before configuration persistence, reservation or provider contact.

Historical entries remain here for accounting even after they stop being
selectable, so a persisted request identity always resolves to the same
immutable schedule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from kronika.domain.research import (
    OPENAI_RESPONSES_PROVIDER_ID,
    UsagePriceSchedule,
    UsageTokenPrices,
)

# The four confirmed exact identifiers. The first is the default.
DEFAULT_RESEARCH_MODEL_ID = "gpt-5.5-2026-04-23"
SOL_MODEL_ID = "gpt-5.6-sol"
TERRA_MODEL_ID = "gpt-5.6-terra"
LUNA_MODEL_ID = "gpt-5.6-luna"

PINNING_DATED_SNAPSHOT = "dated_snapshot"
PINNING_UNDATED_IDENTIFIER = "undated_identifier"

OPENAI_STANDARD_2026_09_30 = "openai-standard-2026-09-30"
LEGACY_OPENAI_2026_09_26 = "openai-2026-09-26"

# Persisted request profile version. AI configuration stays schema version 3;
# this value is independent of the configuration schema.
RESEARCH_ADMISSION_PROFILE_VERSION = "s9r-20260930"
LEGACY_RESEARCH_ADMISSION_VERSION = "3"

LONG_CONTEXT_THRESHOLD_TOKENS = 272_000
WEB_SEARCH_MICRO_USD_PER_THOUSAND = 10_000_000
SOL_VALID_UNTIL = "2026-11-22T00:00:00Z"


@dataclass(frozen=True, slots=True)
class ResearchModelEntry:
    """One immutable catalog model with short and long context prices."""

    model_id: str
    display_name: str
    pinning: str
    selectable: bool
    price_schedule_version: str
    valid_until: str | None
    short_prices: UsageTokenPrices
    long_prices: UsageTokenPrices

    @property
    def schedule(self) -> UsagePriceSchedule:
        """Return the full immutable schedule for this model."""
        return UsagePriceSchedule(
            input_micro_usd_per_million=self.short_prices.input_micro_usd_per_million,
            cached_input_micro_usd_per_million=(
                self.short_prices.cached_input_micro_usd_per_million
            ),
            output_micro_usd_per_million=(
                self.short_prices.output_micro_usd_per_million
            ),
            web_search_micro_usd_per_thousand=WEB_SEARCH_MICRO_USD_PER_THOUSAND,
            cache_write_input_micro_usd_per_million=(
                self.short_prices.cache_write_input_micro_usd_per_million
            ),
            long_context_threshold_tokens=LONG_CONTEXT_THRESHOLD_TOKENS,
            long_context=self.long_prices,
        )


def _prices(
    *,
    input_micro_usd_per_million: int,
    cached_input_micro_usd_per_million: int,
    cache_write_input_micro_usd_per_million: int,
    output_micro_usd_per_million: int,
) -> UsageTokenPrices:
    return UsageTokenPrices(
        input_micro_usd_per_million=input_micro_usd_per_million,
        cached_input_micro_usd_per_million=cached_input_micro_usd_per_million,
        cache_write_input_micro_usd_per_million=cache_write_input_micro_usd_per_million,
        output_micro_usd_per_million=output_micro_usd_per_million,
    )


RESEARCH_MODEL_CATALOG: tuple[ResearchModelEntry, ...] = (
    ResearchModelEntry(
        model_id=DEFAULT_RESEARCH_MODEL_ID,
        display_name="GPT-5.5 (2026-04-23)",
        pinning=PINNING_DATED_SNAPSHOT,
        selectable=True,
        price_schedule_version=OPENAI_STANDARD_2026_09_30,
        valid_until=None,
        short_prices=_prices(
            input_micro_usd_per_million=5_000_000,
            cached_input_micro_usd_per_million=500_000,
            cache_write_input_micro_usd_per_million=5_000_000,
            output_micro_usd_per_million=30_000_000,
        ),
        long_prices=_prices(
            input_micro_usd_per_million=10_000_000,
            cached_input_micro_usd_per_million=1_000_000,
            cache_write_input_micro_usd_per_million=10_000_000,
            output_micro_usd_per_million=45_000_000,
        ),
    ),
    ResearchModelEntry(
        model_id=SOL_MODEL_ID,
        display_name="GPT-5.6 Sol",
        pinning=PINNING_UNDATED_IDENTIFIER,
        selectable=True,
        price_schedule_version=OPENAI_STANDARD_2026_09_30,
        valid_until=SOL_VALID_UNTIL,
        short_prices=_prices(
            input_micro_usd_per_million=4_000_000,
            cached_input_micro_usd_per_million=400_000,
            cache_write_input_micro_usd_per_million=5_000_000,
            output_micro_usd_per_million=20_000_000,
        ),
        long_prices=_prices(
            input_micro_usd_per_million=8_000_000,
            cached_input_micro_usd_per_million=800_000,
            cache_write_input_micro_usd_per_million=10_000_000,
            output_micro_usd_per_million=30_000_000,
        ),
    ),
    ResearchModelEntry(
        model_id=TERRA_MODEL_ID,
        display_name="GPT-5.6 Terra",
        pinning=PINNING_UNDATED_IDENTIFIER,
        selectable=True,
        price_schedule_version=OPENAI_STANDARD_2026_09_30,
        valid_until=None,
        short_prices=_prices(
            input_micro_usd_per_million=2_000_000,
            cached_input_micro_usd_per_million=200_000,
            cache_write_input_micro_usd_per_million=2_500_000,
            output_micro_usd_per_million=12_000_000,
        ),
        long_prices=_prices(
            input_micro_usd_per_million=4_000_000,
            cached_input_micro_usd_per_million=400_000,
            cache_write_input_micro_usd_per_million=5_000_000,
            output_micro_usd_per_million=18_000_000,
        ),
    ),
    ResearchModelEntry(
        model_id=LUNA_MODEL_ID,
        display_name="GPT-5.6 Luna",
        pinning=PINNING_UNDATED_IDENTIFIER,
        selectable=True,
        price_schedule_version=OPENAI_STANDARD_2026_09_30,
        valid_until=None,
        short_prices=_prices(
            input_micro_usd_per_million=200_000,
            cached_input_micro_usd_per_million=20_000,
            cache_write_input_micro_usd_per_million=250_000,
            output_micro_usd_per_million=1_200_000,
        ),
        long_prices=_prices(
            input_micro_usd_per_million=400_000,
            cached_input_micro_usd_per_million=40_000,
            cache_write_input_micro_usd_per_million=500_000,
            output_micro_usd_per_million=1_800_000,
        ),
    ),
)

CATALOG_BY_MODEL_ID: dict[str, ResearchModelEntry] = {
    entry.model_id: entry for entry in RESEARCH_MODEL_CATALOG
}


def legacy_2026_09_26_schedule() -> UsagePriceSchedule:
    """Return the original flat schedule retained for legacy version-3 requests.

    It has no long-context tier, so an unfinished legacy request above the
    threshold resolves to unknown accounting rather than a fabricated value.
    """
    return UsagePriceSchedule(
        input_micro_usd_per_million=5_000_000,
        cached_input_micro_usd_per_million=500_000,
        output_micro_usd_per_million=30_000_000,
        web_search_micro_usd_per_thousand=WEB_SEARCH_MICRO_USD_PER_THOUSAND,
        cache_write_input_micro_usd_per_million=5_000_000,
        long_context_threshold_tokens=LONG_CONTEXT_THRESHOLD_TOKENS,
        long_context=None,
    )


def lookup_model(model_id: object) -> ResearchModelEntry | None:
    """Return one catalog entry for an exact identifier, or none."""
    if not isinstance(model_id, str):
        return None
    return CATALOG_BY_MODEL_ID.get(model_id)


def known_selectable_model_ids() -> tuple[str, ...]:
    """Return the exact selectable identifiers in catalog order."""
    return tuple(
        entry.model_id for entry in RESEARCH_MODEL_CATALOG if entry.selectable
    )


def is_known_selectable_model(model_id: object) -> bool:
    """Return whether an exact identifier is a selectable catalog entry."""
    entry = lookup_model(model_id)
    return entry is not None and entry.selectable


def _valid_until_ms(model_id: object) -> int | None:
    entry = lookup_model(model_id)
    if entry is None or entry.valid_until is None:
        return None
    moment = datetime.fromisoformat(entry.valid_until.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1000)


def model_has_expired(model_id: object, *, now_ms: int) -> bool:
    """Return whether a catalog model's pinned price window has closed."""
    cutoff_ms = _valid_until_ms(model_id)
    return cutoff_ms is not None and now_ms >= cutoff_ms


def model_admissible_now(model_id: object, *, now_ms: int) -> bool:
    """Return whether a model may be newly selected or enabled at a moment."""
    return is_known_selectable_model(model_id) and not model_has_expired(
        model_id, now_ms=now_ms
    )


def admission_deadline_within_validity(
    model_id: object,
    *,
    deadline_ms: int,
    now_ms: int,
) -> bool:
    """Return whether a new admission's deadline stays before any cutoff."""
    cutoff_ms = _valid_until_ms(model_id)
    if cutoff_ms is None:
        return True
    return now_ms < cutoff_ms and deadline_ms < cutoff_ms


def resolve_usage_price_schedule(
    provider_id: object,
    model_id: object,
    configuration_version: object,
) -> UsagePriceSchedule | None:
    """Resolve an immutable schedule from a persisted request identity.

    Unknown tuples fail closed with ``None``. The mapping is append-only: a
    future price change adds a new profile version and never overwrites an
    existing tuple.
    """
    if provider_id != OPENAI_RESPONSES_PROVIDER_ID:
        return None
    if configuration_version == RESEARCH_ADMISSION_PROFILE_VERSION:
        entry = lookup_model(model_id)
        return None if entry is None else entry.schedule
    if configuration_version == LEGACY_RESEARCH_ADMISSION_VERSION:
        if model_id == DEFAULT_RESEARCH_MODEL_ID:
            return legacy_2026_09_26_schedule()
        return None
    return None


def serialize_schedule_pricing(
    entry: ResearchModelEntry,
) -> dict[str, object]:
    """Serialize one entry's extended schedule for administrator display."""
    return {
        "web_search_micro_usd_per_thousand": WEB_SEARCH_MICRO_USD_PER_THOUSAND,
        "long_context_threshold_tokens": LONG_CONTEXT_THRESHOLD_TOKENS,
        "short": _serialize_prices(entry.short_prices),
        "long_context": _serialize_prices(entry.long_prices),
    }


def _serialize_prices(prices: UsageTokenPrices) -> dict[str, int]:
    return {
        "input_micro_usd_per_million": prices.input_micro_usd_per_million,
        "cached_input_micro_usd_per_million": (
            prices.cached_input_micro_usd_per_million
        ),
        "cache_write_input_micro_usd_per_million": (
            prices.cache_write_input_micro_usd_per_million
        ),
        "output_micro_usd_per_million": prices.output_micro_usd_per_million,
    }
