"""Unit tests for shared sanitized provider failure classification."""

from __future__ import annotations

import pytest

from kronika.application.media_suggestion import (
    MediaSuggestionProviderAuthError,
    MediaSuggestionProviderEmptyResponseError,
    MediaSuggestionProviderFailedError,
    MediaSuggestionProviderInvalidResponseError,
    MediaSuggestionProviderModelUnavailableError,
    MediaSuggestionProviderPendingTimeoutError,
    MediaSuggestionProviderRateLimitedError,
    MediaSuggestionProviderRefusalError,
    MediaSuggestionProviderTruncatedResponseError,
    MediaSuggestionProviderUnavailableError,
)
from kronika.infrastructure.ai.provider_activity import (
    PROVIDER_CATEGORY_AUTHENTICATION_FAILED,
    PROVIDER_CATEGORY_INVALID_RESPONSE,
    PROVIDER_CATEGORY_MODEL_UNAVAILABLE,
    PROVIDER_CATEGORY_PROVIDER_ERROR,
    PROVIDER_CATEGORY_PROVIDER_UNREACHABLE,
    PROVIDER_CATEGORY_RATE_LIMITED,
    PROVIDER_CATEGORY_SUCCESS,
    PROVIDER_ACTIVITY_CATEGORIES,
    classify_provider_exception,
)


@pytest.mark.parametrize(
    ("exc", "category"),
    [
        (MediaSuggestionProviderAuthError("raw"), PROVIDER_CATEGORY_AUTHENTICATION_FAILED),
        (MediaSuggestionProviderRateLimitedError("raw"), PROVIDER_CATEGORY_RATE_LIMITED),
        (MediaSuggestionProviderModelUnavailableError("raw"), PROVIDER_CATEGORY_MODEL_UNAVAILABLE),
        (MediaSuggestionProviderUnavailableError("raw"), PROVIDER_CATEGORY_PROVIDER_UNREACHABLE),
        (MediaSuggestionProviderInvalidResponseError("raw"), PROVIDER_CATEGORY_INVALID_RESPONSE),
        (MediaSuggestionProviderFailedError("raw"), PROVIDER_CATEGORY_PROVIDER_ERROR),
        (RuntimeError("raw"), PROVIDER_CATEGORY_PROVIDER_ERROR),
        (OSError("raw"), PROVIDER_CATEGORY_PROVIDER_ERROR),
    ],
)
def test_classifier_matches_the_provider_taxonomy(exc: Exception, category: str) -> None:
    assert classify_provider_exception(exc) == category


@pytest.mark.parametrize(
    ("exc", "category"),
    [
        (MediaSuggestionProviderEmptyResponseError("raw"), PROVIDER_CATEGORY_INVALID_RESPONSE),
        (MediaSuggestionProviderRefusalError("raw"), PROVIDER_CATEGORY_INVALID_RESPONSE),
        (MediaSuggestionProviderTruncatedResponseError("raw"), PROVIDER_CATEGORY_INVALID_RESPONSE),
        (MediaSuggestionProviderPendingTimeoutError("raw"), PROVIDER_CATEGORY_PROVIDER_UNREACHABLE),
    ],
)
def test_classifier_covers_extended_error_subclasses(exc: Exception, category: str) -> None:
    assert classify_provider_exception(exc) == category


def test_classifier_categories_match_the_safe_status_set() -> None:
    assert PROVIDER_CATEGORY_SUCCESS in PROVIDER_ACTIVITY_CATEGORIES
    assert classify_provider_exception(MediaSuggestionProviderAuthError("raw")) in (
        PROVIDER_ACTIVITY_CATEGORIES
    )
