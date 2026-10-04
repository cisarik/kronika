"""Shared sanitized classification of AI provider activity failures."""

from __future__ import annotations

from kronika.application.media_suggestion import (
    MediaSuggestionProviderAuthError,
    MediaSuggestionProviderFailedError,
    MediaSuggestionProviderInvalidResponseError,
    MediaSuggestionProviderModelUnavailableError,
    MediaSuggestionProviderRateLimitedError,
    MediaSuggestionProviderUnavailableError,
)

PROVIDER_CATEGORY_SUCCESS = "success"
PROVIDER_CATEGORY_AUTHENTICATION_FAILED = "authentication_failed"
PROVIDER_CATEGORY_RATE_LIMITED = "rate_limited_or_quota_exhausted"
PROVIDER_CATEGORY_MODEL_UNAVAILABLE = "model_unavailable"
PROVIDER_CATEGORY_PROVIDER_UNREACHABLE = "provider_unreachable"
PROVIDER_CATEGORY_INVALID_RESPONSE = "invalid_response"
PROVIDER_CATEGORY_PROVIDER_ERROR = "provider_error"

PROVIDER_ACTIVITY_CATEGORIES = frozenset(
    {
        PROVIDER_CATEGORY_SUCCESS,
        PROVIDER_CATEGORY_AUTHENTICATION_FAILED,
        PROVIDER_CATEGORY_RATE_LIMITED,
        PROVIDER_CATEGORY_MODEL_UNAVAILABLE,
        PROVIDER_CATEGORY_PROVIDER_UNREACHABLE,
        PROVIDER_CATEGORY_INVALID_RESPONSE,
        PROVIDER_CATEGORY_PROVIDER_ERROR,
    }
)


def classify_provider_exception(exc: Exception) -> str:
    """Return the sanitized activity category for one provider failure."""
    if isinstance(exc, MediaSuggestionProviderAuthError):
        return PROVIDER_CATEGORY_AUTHENTICATION_FAILED
    if isinstance(exc, MediaSuggestionProviderRateLimitedError):
        return PROVIDER_CATEGORY_RATE_LIMITED
    if isinstance(exc, MediaSuggestionProviderModelUnavailableError):
        return PROVIDER_CATEGORY_MODEL_UNAVAILABLE
    if isinstance(exc, MediaSuggestionProviderUnavailableError):
        return PROVIDER_CATEGORY_PROVIDER_UNREACHABLE
    if isinstance(exc, MediaSuggestionProviderInvalidResponseError):
        return PROVIDER_CATEGORY_INVALID_RESPONSE
    if isinstance(exc, MediaSuggestionProviderFailedError):
        return PROVIDER_CATEGORY_PROVIDER_ERROR
    return PROVIDER_CATEGORY_PROVIDER_ERROR
