"""Domain evidence for server-owned publication readiness."""

from kronika.domain.content_publication import (
    derive_content_publication_readiness,
)


def test_readiness_uses_only_trimmed_persisted_title_description_and_tags() -> None:
    ready = derive_content_publication_readiness(
        display_title="A title",
        description="A description",
        canonical_tag_count=1,
    )
    manual_without_ai = derive_content_publication_readiness(
        display_title="Manual title",
        description="Manual description",
        canonical_tag_count=2,
    )

    assert ready.ready is True
    assert ready.missing_fields == ()
    assert manual_without_ai.ready is True
    assert manual_without_ai.missing_fields == ()


def test_readiness_missing_fields_are_stable_and_whitespace_is_missing() -> None:
    result = derive_content_publication_readiness(
        display_title=" \t",
        description="\n ",
        canonical_tag_count=0,
    )

    assert result.ready is False
    assert result.missing_fields == ("display_title", "description", "tags")


def test_readiness_is_independent_of_processed_analysis_and_publication_state() -> None:
    incomplete = derive_content_publication_readiness(
        display_title=None,
        description=None,
        canonical_tag_count=1,
    )

    assert incomplete.ready is False
    assert incomplete.missing_fields == ("display_title", "description")
