"""Application port for provider-neutral media suggestion generation."""

from __future__ import annotations

from typing import Protocol

from kronika.application.media_suggestion import MediaSuggestion, MediaSuggestionRequest


class MediaSuggestionProvider(Protocol):
    """Infrastructure-independent suggestion contract."""

    def suggest(self, request: MediaSuggestionRequest) -> MediaSuggestion:
        """Return one validated suggestion preview for a prepared request."""


class MediaSuggestionConnectionTester(Protocol):
    """Infrastructure-independent provider connection test contract."""

    def test_connection(self) -> None:
        """Run one explicit text-only provider connection test."""
