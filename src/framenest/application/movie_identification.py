"""Movie-identification suggestion contract and validation."""

from __future__ import annotations

from dataclasses import dataclass

from framenest.domain.media_classification import (
    IdentificationConfidence,
    MOVIE_GENRE_DISPLAY_NAMES,
    MOVIE_IDENTIFICATION_PROMPT_VERSION,
    MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION,
    MovieGenre,
    MovieIdentificationStatus,
)

INVALID_MOVIE_IDENTIFICATION_MESSAGE = "Invalid movie identification suggestion."
MAX_CANDIDATE_TITLES = 5
MAX_TITLE_LENGTH = 240
MAX_DESCRIPTION_LENGTH = 2000
MAX_TAG_COUNT = 12
MAX_TAG_LENGTH = 64
MAX_EVIDENCE_LENGTH = 500


class FrameNestMovieIdentificationError(ValueError):
    """Sanitized validation failure for movie-identification payloads."""


@dataclass(frozen=True, slots=True)
class LocalMovieHints:
    """Narrow allowlisted local hints; never includes absolute paths."""

    filename_stem: str | None
    container_title: str | None
    duration_ms: int | None
    width: int | None
    height: int | None


@dataclass(frozen=True, slots=True)
class MovieIdentificationSuggestion:
    """Validated movie-identification suggestion for human review only."""

    identified_title: str | None
    release_year: int | None
    identification_status: MovieIdentificationStatus
    confidence: IdentificationConfidence
    candidate_titles: tuple[str, ...]
    genres: tuple[str, ...]
    description: str
    tags: tuple[str, ...]
    evidence_summary: str
    provider_id: str
    model_id: str
    prompt_version: str
    result_schema_version: str
    derivative_count: int
    reasoning_enabled: bool

    def __post_init__(self) -> None:
        if self.prompt_version != MOVIE_IDENTIFICATION_PROMPT_VERSION:
            raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
        if self.result_schema_version != MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION:
            raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
        if not isinstance(self.identification_status, MovieIdentificationStatus):
            raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
        if not isinstance(self.confidence, IdentificationConfidence):
            raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
        if self.identified_title is not None:
            object.__setattr__(
                self,
                "identified_title",
                _bounded_text(self.identified_title, maximum=MAX_TITLE_LENGTH),
            )
        if self.release_year is not None:
            if (
                isinstance(self.release_year, bool)
                or not isinstance(self.release_year, int)
                or self.release_year < 1888
                or self.release_year > 2100
            ):
                raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
        object.__setattr__(
            self,
            "candidate_titles",
            _bounded_string_tuple(
                self.candidate_titles,
                maximum_count=MAX_CANDIDATE_TITLES,
                item_maximum=MAX_TITLE_LENGTH,
                allow_empty=True,
            ),
        )
        object.__setattr__(self, "genres", _validate_genres(self.genres))
        object.__setattr__(
            self,
            "description",
            _bounded_text(self.description, maximum=MAX_DESCRIPTION_LENGTH),
        )
        object.__setattr__(
            self,
            "tags",
            _bounded_string_tuple(
                self.tags,
                maximum_count=MAX_TAG_COUNT,
                item_maximum=MAX_TAG_LENGTH,
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "evidence_summary",
            _bounded_text(self.evidence_summary, maximum=MAX_EVIDENCE_LENGTH),
        )
        for field_name in ("provider_id", "model_id"):
            object.__setattr__(
                self,
                field_name,
                _bounded_text(getattr(self, field_name), maximum=120),
            )
        if (
            isinstance(self.derivative_count, bool)
            or not isinstance(self.derivative_count, int)
            or self.derivative_count < 1
            or self.derivative_count > 16
        ):
            raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
        if not isinstance(self.reasoning_enabled, bool) or not self.reasoning_enabled:
            raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
        if (
            self.identification_status is MovieIdentificationStatus.UNKNOWN
            and self.identified_title is not None
        ):
            raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)


def parse_movie_identification_payload(
    payload: dict[str, object],
    *,
    provider_id: str,
    model_id: str,
    derivative_count: int,
) -> MovieIdentificationSuggestion:
    """Parse and validate one provider JSON object for movie identification."""
    if not isinstance(payload, dict):
        raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
    allowed = {
        "identified_title",
        "release_year",
        "identification_status",
        "confidence",
        "candidate_titles",
        "genres",
        "description",
        "tags",
        "evidence_summary",
    }
    if set(payload) != allowed:
        raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)

    status_raw = payload.get("identification_status")
    confidence_raw = payload.get("confidence")
    try:
        status = MovieIdentificationStatus(status_raw)
        confidence = IdentificationConfidence(confidence_raw)
    except (TypeError, ValueError) as exc:
        raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE) from exc

    title = payload.get("identified_title")
    if title is not None and not isinstance(title, str):
        raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)

    year = payload.get("release_year")
    if year is not None and (not isinstance(year, int) or isinstance(year, bool)):
        raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)

    candidates = payload["candidate_titles"]
    genres = payload["genres"]
    tags = payload["tags"]
    if not isinstance(candidates, list) or not isinstance(genres, list) or not isinstance(tags, list):
        raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
    if not all(isinstance(item, str) for item in candidates + genres + tags):
        raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)

    description = payload["description"]
    evidence = payload["evidence_summary"]
    if not isinstance(description, str) or not isinstance(evidence, str):
        raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)

    return MovieIdentificationSuggestion(
        identified_title=title,
        release_year=year,
        identification_status=status,
        confidence=confidence,
        candidate_titles=tuple(candidates),
        genres=tuple(genres),
        description=description,
        tags=tuple(tags),
        evidence_summary=evidence,
        provider_id=provider_id,
        model_id=model_id,
        prompt_version=MOVIE_IDENTIFICATION_PROMPT_VERSION,
        result_schema_version=MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION,
        derivative_count=derivative_count,
        reasoning_enabled=True,
    )


def serialize_movie_identification_result(suggestion: MovieIdentificationSuggestion) -> dict[str, object]:
    """Serialize a validated suggestion for durable storage without reasoning traces."""
    return {
        "identified_title": suggestion.identified_title,
        "release_year": suggestion.release_year,
        "identification_status": suggestion.identification_status.value,
        "confidence": suggestion.confidence.value,
        "candidate_titles": list(suggestion.candidate_titles),
        "genres": list(suggestion.genres),
        "description": suggestion.description,
        "tags": list(suggestion.tags),
        "evidence_summary": suggestion.evidence_summary,
        "derivative_count": suggestion.derivative_count,
        "reasoning_enabled": True,
    }


def movie_identification_prompt(*, hints: LocalMovieHints) -> str:
    """Build the movie-identification prompt with optional sanitized local hints."""
    hint_lines = [
        f"Filename stem: {hints.filename_stem or 'unavailable'}",
        f"Container title: {hints.container_title or 'unavailable'}",
        f"Duration milliseconds: {hints.duration_ms if hints.duration_ms is not None else 'unknown'}",
        f"Dimensions: "
        f"{hints.width if hints.width is not None else '?'}x"
        f"{hints.height if hints.height is not None else '?'}",
    ]
    genre_names = ", ".join(MOVIE_GENRE_DISPLAY_NAMES[genre] for genre in MovieGenre)
    return f"""You are Kronika's movie identification assistant.

Prompt version: {MOVIE_IDENTIFICATION_PROMPT_VERSION}

You receive one bounded contact sheet composed from local representative frames.
Identify the film only when visual evidence supports it. Returning unknown is
correct and preferred over guessing.

Local hints are optional and may be wrong. Never treat them as ground truth.
Never request or assume access to the original video or audio.

Return exactly one JSON object and no Markdown, commentary, prefix, suffix,
explanation, hidden reasoning, or chain-of-thought. Keys must be only:
identified_title, release_year, identification_status, confidence,
candidate_titles, genres, description, tags, evidence_summary.
All nine keys are required. End any internal reasoning before emitting the
object, and never place the final object only inside reasoning.

Contracts:
- identification_status: identified | unknown | ambiguous
- confidence: high | medium | low | unknown
- identified_title may be null
- release_year may be null or a four-digit year
- candidate_titles: at most {MAX_CANDIDATE_TITLES} strings
- genres: display names from this bounded list only: {genre_names}
- Do not invent a title when uncertain
- Low confidence must not claim a definitive identity
- evidence_summary: brief final evidence only; never include chain-of-thought
- When evidence is insufficient, return this schema-compatible result:
  {{"identified_title":null,"release_year":null,"identification_status":"unknown",
  "confidence":"unknown","candidate_titles":[],"genres":[],
  "description":"Movie could not be identified from the available frames.",
  "tags":[],"evidence_summary":"Insufficient visual evidence."}}

Local hints:
{chr(10).join(hint_lines)}
"""


def _bounded_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
    if value.strip() != value:
        raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
    return value


def _bounded_string_tuple(
    values: object,
    *,
    maximum_count: int,
    item_maximum: int,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        if isinstance(values, list):
            values = tuple(values)
        else:
            raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
    if len(values) > maximum_count:
        raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
    if not values and not allow_empty:
        raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _bounded_text(item, maximum=item_maximum)
        key = text.casefold()
        if key in seen:
            raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
        seen.add(key)
        normalized.append(text)
    return tuple(normalized)


def _validate_genres(values: object) -> tuple[str, ...]:
    display_to_key = {
        display.casefold(): genre for genre, display in MOVIE_GENRE_DISPLAY_NAMES.items()
    }
    key_to_display = {genre.value: display for genre, display in MOVIE_GENRE_DISPLAY_NAMES.items()}
    if not isinstance(values, tuple):
        if isinstance(values, list):
            values = tuple(values)
        else:
            raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
    if len(values) > 8:
        raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
        folded = item.strip().casefold()
        if folded in display_to_key:
            display = MOVIE_GENRE_DISPLAY_NAMES[display_to_key[folded]]
        elif folded in key_to_display:
            display = key_to_display[folded]
        else:
            raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
        if display.casefold() in seen:
            raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
        seen.add(display.casefold())
        normalized.append(display)
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class MovieIdentificationRequest:
    """Provider request carrying one contact-sheet derivative and local hints."""

    basename: str
    contact_sheet: object
    hints: LocalMovieHints
    prompt_version: str = MOVIE_IDENTIFICATION_PROMPT_VERSION

    def __post_init__(self) -> None:
        if self.prompt_version != MOVIE_IDENTIFICATION_PROMPT_VERSION:
            raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
        if not hasattr(self.contact_sheet, "payload") or not hasattr(
            self.contact_sheet, "mime_type"
        ):
            raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
        if not isinstance(self.hints, LocalMovieHints):
            raise FrameNestMovieIdentificationError(INVALID_MOVIE_IDENTIFICATION_MESSAGE)
