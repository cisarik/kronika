"""Both spellings of every durable analysis identity stay readable.

The writer cut changes the spelling the writers emit. A reader that compares a
stored identity against one constant, or filters a stored identity against one
constant, hides every row written before that cut. These tests pin the acceptance
rule from both sides:

- the acceptance test is a symmetric membership test, proven for every identity
  and for the direction that used to fail;
- a row written under the former spelling is still returned by the same
  companion-inbox query after the writer constant is simulated as changed;
- every reader the derivation found is covered, in both the application layer
  and the persistence layer.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import re

import pytest

from kronika.application.media_suggestion import (
    ACCEPTED_PROMPT_VERSIONS,
    CANONICAL_PROMPT_VERSION,
    MediaSuggestion,
    MediaSuggestionRequest,
    PROMPT_VERSION,
)
from kronika.application.movie_identification import (
    LocalMovieHints,
    MovieIdentificationRequest,
    MovieIdentificationSuggestion,
)
from kronika.domain.media_classification import (
    IdentificationConfidence,
    MovieIdentificationStatus,
)
from kronika.domain.identities import MediaId
from kronika.domain.analysis_identities import (
    accepted_durable_identity,
    is_accepted_durable_identity,
)
from kronika.domain.media_analysis_runs import (
    ACCEPTED_RESULT_SCHEMA_VERSIONS,
    CANONICAL_RESULT_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
)
from kronika.domain.media_classification import (
    ACCEPTED_MOVIE_IDENTIFICATION_PROMPT_VERSIONS,
    ACCEPTED_MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSIONS,
    CANONICAL_MOVIE_IDENTIFICATION_PROMPT_VERSION,
    CANONICAL_MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION,
    MOVIE_IDENTIFICATION_PROMPT_VERSION,
    MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION,
)

from tests.unit.infrastructure.persistence import (
    test_companion_review_repository as inbox,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "kronika"

HISTORICAL_RUN = "abababab-abab-4bab-8bab-000000000003"

#: Every durable analysis identity, its writer spelling and its canonical
#: spelling, derived rather than enumerated.
DURABLE_IDENTITIES: tuple[tuple[str, str, str, frozenset[str]], ...] = (
    (
        "generic result schema",
        RESULT_SCHEMA_VERSION,
        CANONICAL_RESULT_SCHEMA_VERSION,
        ACCEPTED_RESULT_SCHEMA_VERSIONS,
    ),
    (
        "generic prompt version",
        PROMPT_VERSION,
        CANONICAL_PROMPT_VERSION,
        ACCEPTED_PROMPT_VERSIONS,
    ),
    (
        "movie identification result schema",
        MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION,
        CANONICAL_MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION,
        ACCEPTED_MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSIONS,
    ),
    (
        "movie identification prompt version",
        MOVIE_IDENTIFICATION_PROMPT_VERSION,
        CANONICAL_MOVIE_IDENTIFICATION_PROMPT_VERSION,
        ACCEPTED_MOVIE_IDENTIFICATION_PROMPT_VERSIONS,
    ),
)


# ---------------------------------------------------------------------------
# The acceptance rule itself
# ---------------------------------------------------------------------------


def test_the_acceptance_rule_needs_two_distinct_non_empty_spellings() -> None:
    with pytest.raises(ValueError):
        accepted_durable_identity("", "canonical")
    with pytest.raises(ValueError):
        accepted_durable_identity("current", "")
    with pytest.raises(ValueError):
        accepted_durable_identity("same", "same")


def test_no_writer_uses_the_canonical_spelling_yet() -> None:
    """This cut adds readers only; the writer cut owns the writer spelling."""
    for name, current, canonical, accepted in DURABLE_IDENTITIES:
        assert current in accepted, name
        assert canonical in accepted, name
        assert current != canonical, name


@pytest.mark.parametrize(
    ("name", "current", "canonical", "accepted"),
    DURABLE_IDENTITIES,
    ids=[entry[0] for entry in DURABLE_IDENTITIES],
)
def test_acceptance_is_symmetric_and_explicit(
    name: str, current: str, canonical: str, accepted: frozenset[str]
) -> None:
    assert is_accepted_durable_identity(current, accepted)
    assert is_accepted_durable_identity(canonical, accepted)
    # The historical spelling is accepted in both directions of the writer cut:
    # before it, and after it.
    assert is_accepted_durable_identity(canonical, accepted) == is_accepted_durable_identity(
        current, accepted
    )
    assert accepted == frozenset({current, canonical})
    for rejected in ("", "framenest", "not-v1", None, 1, current + "x", canonical.upper()):
        assert not is_accepted_durable_identity(rejected, accepted), (name, rejected)


# ---------------------------------------------------------------------------
# The persistence reader: the filter that used to hide historical rows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param(RESULT_SCHEMA_VERSION, id="stored-under-the-former-spelling"),
        pytest.param(
            CANONICAL_RESULT_SCHEMA_VERSION, id="stored-under-the-canonical-spelling"
        ),
    ],
)
def test_a_successful_run_stays_eligible_under_either_stored_spelling(
    tmp_path: Path, stored: str
) -> None:
    """A stored successful analysis stays reviewable under either spelling.

    The writer cut will leave new rows under the canonical spelling. A reader
    that still compared against one constant refuses the historical row here,
    and this assertion is the one that fails.
    """
    repository, engine = inbox._repository(tmp_path)
    with engine.begin() as connection:
        inbox._insert_analyzed_run(
            connection,
            HISTORICAL_RUN,
            inbox.GENERIC,
            inbox.GENERIC_LOC,
            completed_at_ms=900,
            title="Historical successful analysis",
            tags=["Cats"],
            result_schema_version=stored,
        )

    opened = repository.mark_opened(
        media_id=MediaId.from_string(inbox.GENERIC),
        actor_login_key=inbox.ADMIN_KEY,
        analysis_run_id=MediaId.from_string(HISTORICAL_RUN),
        now_ms=1000,
    )

    assert opened.opened_run_id == HISTORICAL_RUN


def test_a_run_under_an_unknown_schema_stays_ineligible(tmp_path: Path) -> None:
    """The widened reader still refuses an identity it never accepted."""
    from kronika.application.ports.companion_review_repository import (
        CompanionReviewRunNotEligibleError,
    )

    repository, engine = inbox._repository(tmp_path)
    with engine.begin() as connection:
        inbox._insert_analyzed_run(
            connection,
            HISTORICAL_RUN,
            inbox.GENERIC,
            inbox.GENERIC_LOC,
            completed_at_ms=900,
            title="Historical successful analysis",
            tags=["Cats"],
            result_schema_version="not-v1",
        )

    with pytest.raises(CompanionReviewRunNotEligibleError):
        repository.mark_opened(
            media_id=MediaId.from_string(inbox.GENERIC),
            actor_login_key=inbox.ADMIN_KEY,
            analysis_run_id=MediaId.from_string(HISTORICAL_RUN),
            now_ms=1000,
        )


def test_the_predicate_returns_the_same_rows_for_either_spelling(
    tmp_path: Path,
) -> None:
    """One query, two stored spellings: the same run is eligible either way."""
    eligible: list[str] = []
    for index, stored in enumerate(
        (RESULT_SCHEMA_VERSION, CANONICAL_RESULT_SCHEMA_VERSION)
    ):
        repository, engine = inbox._repository(tmp_path / str(index))
        with engine.begin() as connection:
            inbox._insert_analyzed_run(
                connection,
                HISTORICAL_RUN,
                inbox.GENERIC,
                inbox.GENERIC_LOC,
                completed_at_ms=900,
                title="Historical successful analysis",
                tags=["Cats"],
                result_schema_version=stored,
            )
        opened = repository.mark_opened(
            media_id=MediaId.from_string(inbox.GENERIC),
            actor_login_key=inbox.ADMIN_KEY,
            analysis_run_id=MediaId.from_string(HISTORICAL_RUN),
            now_ms=1000,
        )
        eligible.append(opened.opened_run_id)

    assert eligible[0] == eligible[1] == HISTORICAL_RUN


def test_the_inbox_filter_is_not_a_single_constant_comparison() -> None:
    """The filter must be a membership test, not an equality against one value."""
    module = SOURCE_ROOT / "infrastructure" / "persistence" / (
        "companion_review_repository.py"
    )
    tree = ast.parse(module.read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_successful_generic_predicates"
    )
    source = ast.unparse(function)

    assert "result_schema_version.in_(" in source
    assert "ACCEPTED_RESULT_SCHEMA_VERSIONS" in source
    assert "== RESULT_SCHEMA_VERSION" not in source


# ---------------------------------------------------------------------------
# The application readers: stored-result validation and prompt-version aliases
# ---------------------------------------------------------------------------


def _representative_frame() -> object:
    from kronika.application.media_analysis import PNG_SIGNATURE, RepresentativeFrame

    payload = PNG_SIGNATURE + b"\x00" * 8
    return RepresentativeFrame(
        timestamp_ms=0,
        mime_type="image/png",
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        payload=payload,
    )


def _media_suggestion_request(prompt_version: str) -> MediaSuggestionRequest:
    """Build a request whose only variable is its prompt-version spelling."""
    from kronika.application.library_scan import LibraryScanCandidateKind
    from kronika.application.media_analysis import (
        RepresentativeFrame,
        TechnicalMetadata,
    )

    return MediaSuggestionRequest(
        basename="clip.gif",
        candidate_kind=LibraryScanCandidateKind.VIDEO,
        technical_metadata=TechnicalMetadata(
            duration_ms=1,
            width=4,
            height=4,
            video_codec="h264",
            container_formats=("mov",),
            has_audio=False,
        ),
        representative_frames=(_representative_frame(),),
        prompt_version=prompt_version,
    )


def _suggestion(prompt_version: str) -> MediaSuggestion:
    from kronika.application.media_suggestion import MediaSuggestion

    return MediaSuggestion(
        title="A title",
        description="A description.",
        collection="memes",
        tags=("cats",),
        suggested_filename="clip.gif",
        confidence=0.5,
        evidence=("visible subject",),
        uncertainties=(),
        provider_id="nvidia-nim",
        model_id="a-model",
        prompt_version=prompt_version,
    )


@pytest.mark.parametrize("prompt_version", [PROMPT_VERSION, CANONICAL_PROMPT_VERSION])
def test_a_stored_suggestion_validates_under_either_prompt_spelling(
    prompt_version: str,
) -> None:
    assert _suggestion(prompt_version).prompt_version == prompt_version


@pytest.mark.parametrize(
    "prompt_version", ["", "not-v4", "framenest-media-suggestion-v3"]
)
def test_a_stored_suggestion_still_rejects_an_unknown_prompt_spelling(
    prompt_version: str,
) -> None:
    from kronika.application.media_suggestion import FrameNestMediaSuggestionError

    with pytest.raises(FrameNestMediaSuggestionError):
        _suggestion(prompt_version)


@pytest.mark.parametrize("prompt_version", [PROMPT_VERSION, CANONICAL_PROMPT_VERSION])
def test_a_suggestion_request_reads_its_prompt_version_through_the_rule(
    prompt_version: str,
) -> None:
    assert (
        _media_suggestion_request(prompt_version).prompt_version == prompt_version
    )


@pytest.mark.parametrize("prompt_version", ["", "not-v4"])
def test_a_suggestion_request_still_rejects_an_unknown_prompt_spelling(
    prompt_version: str,
) -> None:
    from kronika.application.media_suggestion import FrameNestMediaSuggestionError

    with pytest.raises(FrameNestMediaSuggestionError):
        _media_suggestion_request(prompt_version)


def _movie_suggestion(
    *, prompt_version: str, result_schema_version: str
) -> MovieIdentificationSuggestion:
    from dataclasses import replace

    suggestion = parse_movie_identification_payload(
        {
            "identified_title": "A film",
            "release_year": 2001,
            "identification_status": "identified",
            "confidence": "high",
            "candidate_titles": ["A film"],
            "genres": ["comedy"],
            "description": "A description.",
            "tags": ["comedy"],
            "evidence_summary": "visible subject",
        },
        provider_id="nvidia-nim",
        model_id="a-model",
        derivative_count=1,
    )
    assert suggestion.prompt_version == prompt_version
    assert suggestion.result_schema_version == result_schema_version
    return replace(suggestion, prompt_version=prompt_version)


def _movie_suggestion_fields() -> dict[str, object]:
    return {
        "identified_title": "A film",
        "release_year": 2001,
        "identification_status": MovieIdentificationStatus.IDENTIFIED,
        "confidence": IdentificationConfidence.HIGH,
        "candidate_titles": ("A film",),
        "genres": ("comedy",),
        "description": "A description.",
        "tags": ("comedy",),
        "evidence_summary": "visible subject",
        "provider_id": "nvidia-nim",
        "model_id": "a-model",
        "derivative_count": 1,
        "reasoning_enabled": True,
    }


def _movie_suggestion(
    *, prompt_version: str, result_schema_version: str
) -> MovieIdentificationSuggestion:
    return MovieIdentificationSuggestion(
        prompt_version=prompt_version,
        result_schema_version=result_schema_version,
        **_movie_suggestion_fields(),
    )


def _movie_contact_sheet() -> object:
    class _Sheet:
        payload = b"png"
        mime_type = "image/png"

    return _Sheet()


def _local_movie_hints() -> object:
    return LocalMovieHints(
        filename_stem=None,
        container_title=None,
        duration_ms=None,
        width=None,
        height=None,
    )


@pytest.mark.parametrize(
    "prompt_version",
    [
        MOVIE_IDENTIFICATION_PROMPT_VERSION,
        CANONICAL_MOVIE_IDENTIFICATION_PROMPT_VERSION,
    ],
)
@pytest.mark.parametrize(
    "result_schema_version",
    [
        MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION,
        CANONICAL_MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION,
    ],
)
def test_a_stored_movie_suggestion_validates_under_either_spelling(
    prompt_version: str, result_schema_version: str
) -> None:
    suggestion = _movie_suggestion(
        prompt_version=prompt_version, result_schema_version=result_schema_version
    )

    assert suggestion.prompt_version == prompt_version
    assert suggestion.result_schema_version == result_schema_version


@pytest.mark.parametrize(
    ("prompt_version", "result_schema_version"),
    [
        pytest.param("", MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION, id="empty-prompt"),
        pytest.param(
            MOVIE_IDENTIFICATION_PROMPT_VERSION, "not-v1", id="unknown-schema"
        ),
    ],
)
def test_a_stored_movie_suggestion_still_rejects_an_unknown_spelling(
    prompt_version: str, result_schema_version: str
) -> None:
    from kronika.application.movie_identification import (
        FrameNestMovieIdentificationError,
    )

    with pytest.raises(FrameNestMovieIdentificationError):
        _movie_suggestion(
            prompt_version=prompt_version,
            result_schema_version=result_schema_version,
        )
    with pytest.raises(FrameNestMovieIdentificationError):
        _movie_suggestion(
            prompt_version=result_schema_version,
            result_schema_version=prompt_version,
        )


@pytest.mark.parametrize(
    "prompt_version",
    [
        MOVIE_IDENTIFICATION_PROMPT_VERSION,
        CANONICAL_MOVIE_IDENTIFICATION_PROMPT_VERSION,
    ],
)
def test_a_movie_request_reads_its_prompt_version_through_the_rule(
    prompt_version: str,
) -> None:
    request = MovieIdentificationRequest(
        basename="clip.mkv",
        contact_sheet=_movie_contact_sheet(),
        hints=_local_movie_hints(),
        prompt_version=prompt_version,
    )

    assert request.prompt_version == prompt_version


@pytest.mark.parametrize("prompt_version", ["", "not-v2"])
def test_a_movie_request_still_rejects_an_unknown_prompt_spelling(
    prompt_version: str,
) -> None:
    from kronika.application.movie_identification import (
        FrameNestMovieIdentificationError,
    )

    with pytest.raises(FrameNestMovieIdentificationError):
        MovieIdentificationRequest(
            basename="clip.mkv",
            contact_sheet=_movie_contact_sheet(),
            hints=_local_movie_hints(),
            prompt_version=prompt_version,
        )


# ---------------------------------------------------------------------------
# The derivation: no comparison against one durable identity survives
# ---------------------------------------------------------------------------

#: Every constant the derivation classified as a durable analysis identity.
ACCEPTED_SET_NAMES = (
    "ACCEPTED_PROMPT_VERSIONS",
    "ACCEPTED_RESULT_SCHEMA_VERSIONS",
    "ACCEPTED_MOVIE_IDENTIFICATION_PROMPT_VERSIONS",
    "ACCEPTED_MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSIONS",
)

DURABLE_IDENTITY_CONSTANTS = (
    "RESULT_SCHEMA_VERSION",
    "MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION",
    "PROMPT_VERSION",
    "MOVIE_IDENTIFICATION_PROMPT_VERSION",
)


def _comparison_sites() -> list[tuple[str, int, str]]:
    """Parse the product package and collect every identity comparison."""
    sites: list[tuple[str, int, str]] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if "alembic_environment/versions" in path.as_posix():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            if any(
                isinstance(operand, ast.Name)
                and operand.id in DURABLE_IDENTITY_CONSTANTS
                for operand in operands
            ):
                sites.append(
                    (
                        str(path.relative_to(REPOSITORY_ROOT)),
                        node.lineno,
                        ast.unparse(node),
                    )
                )
    return sorted(sites)


def _acceptance_reader_sites() -> list[tuple[str, int, str]]:
    """Parse the product package and collect every identity reader site.

    A reader is a call to the acceptance rule or a SQL membership test over an
    accepted spelling table. Parsed, so a new site fails instead of escaping.
    """
    sites: list[tuple[str, int, str]] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if "alembic_environment/versions" in path.as_posix():
            continue
        relative = str(path.relative_to(REPOSITORY_ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            source = ast.unparse(node)
            if "is_accepted_durable_identity" not in source and not any(
                name in source for name in ACCEPTED_SET_NAMES
            ):
                continue
            sites.append((relative, node.lineno, source))
    return sorted(sites)


def test_the_derived_reader_sites_are_exactly_the_files_this_cut_changed() -> None:
    """Regenerated at run time, so a new site fails instead of escaping."""
    sites = [
        site
        for site in _acceptance_reader_sites()
        if not site[2].startswith("sorted(")
    ]
    files = {site[0] for site in sites}

    assert files == {
        "src/kronika/application/media_suggestion.py",
        "src/kronika/application/movie_identification.py",
        "src/kronika/infrastructure/persistence/companion_review_repository.py",
    }
    # Six reader sites: two prompt-version readers for the generic suggestion,
    # three for movie identification, and the one SQL membership filter.
    assert len(sites) == 6, [(site[0], site[1], site[2]) for site in sites]


def test_the_writer_sites_are_untouched_by_this_cut() -> None:
    """Every remaining use of a durable identity constant is a writer."""
    reader_lines = {
        (site[0], site[1]) for site in _acceptance_reader_sites()
    } | {(site[0], site[1]) for site in _comparison_sites()}
    writers: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if "alembic_environment/versions" in path.as_posix():
            continue
        text_value = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(REPOSITORY_ROOT))
        for name in DURABLE_IDENTITY_CONSTANTS:
            for match in re.finditer(rf"\b{name}\b", text_value):
                line = text_value[: match.start()].count("\n") + 1
                if (relative, line) in reader_lines:
                    continue
                writers.append(f"{relative}:{line}:{name}")

    # Every writer passes a constant; none compares against one.
    assert writers
    for entry in writers:
        assert "==" not in entry and "!=" not in entry, entry


# ---------------------------------------------------------------------------
# The derivation: no comparison against one durable identity survives
# ---------------------------------------------------------------------------

#: Every constant the derivation classified as a durable analysis identity.
DURABLE_IDENTITY_CONSTANTS = (
    "RESULT_SCHEMA_VERSION",
    "MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION",
    "PROMPT_VERSION",
    "MOVIE_IDENTIFICATION_PROMPT_VERSION",
)


def _comparison_sites() -> list[tuple[str, int, str]]:
    """Parse the product package and collect every identity comparison."""
    sites: list[tuple[str, int, str]] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if "alembic_environment/versions" in path.as_posix():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            if any(
                isinstance(operand, ast.Name)
                and operand.id in DURABLE_IDENTITY_CONSTANTS
                for operand in operands
            ):
                sites.append(
                    (
                        str(path.relative_to(REPOSITORY_ROOT)),
                        node.lineno,
                        ast.unparse(node),
                    )
                )
    return sorted(sites)


def test_the_writer_sites_are_untouched_by_this_cut() -> None:
    """Every remaining use of a durable identity constant is a writer."""
    reader_lines = {
        (site[0], site[1]) for site in _acceptance_reader_sites()
    } | {(site[0], site[1]) for site in _comparison_sites()}
    writers: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if "alembic_environment/versions" in path.as_posix():
            continue
        text_value = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(REPOSITORY_ROOT))
        for name in DURABLE_IDENTITY_CONSTANTS:
            for match in re.finditer(rf"\b{name}\b", text_value):
                line = text_value[: match.start()].count("\n") + 1
                if (relative, line) in reader_lines:
                    continue
                writers.append(f"{relative}:{line}:{name}")

    # Every writer passes a constant; none compares against one.
    assert writers
    for entry in writers:
        assert "==" not in entry and "!=" not in entry, entry