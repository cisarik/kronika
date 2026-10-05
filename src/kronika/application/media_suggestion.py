"""Application boundary for provider-neutral media suggestion previews."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Protocol

from kronika.application.library_scan import LibraryScanCandidateKind
from kronika.application.media_analysis import (
    FrameNestMediaAnalysisError,
    MediaAnalysisFailedError,
    MediaAnalysisNotFoundError,
    MediaAnalysisUnavailableError,
    MediaRelativePath,
    PreparedAnalysisResult,
    RepresentativeFrame,
    TechnicalMetadata,
)
from kronika.application.media_content import supported_media_type
from kronika.application.ports.library_repository import LibraryRepository
from kronika.application.ports.media_repository import MediaRepository
from kronika.domain import LibraryId, MediaId, MediaLocationId
from kronika.domain.analysis_identities import (
    accepted_durable_identity,
    is_accepted_durable_identity,
)
from kronika.domain.media import MediaLocationAvailability

if TYPE_CHECKING:
    from kronika.application.ports.media_analysis import LocalMediaAnalysisPreparer
    from kronika.application.ports.media_suggestion import MediaSuggestionProvider

PROMPT_VERSION = "framenest-media-suggestion-v4"
#: The spelling the writer cut will emit. No writer uses it yet; every reader
#: below accepts it so rows and responses written after that cut stay valid.
CANONICAL_PROMPT_VERSION = "kronika-media-suggestion-v4"
ACCEPTED_PROMPT_VERSIONS = accepted_durable_identity(
    PROMPT_VERSION, CANONICAL_PROMPT_VERSION
)

INVALID_SUGGESTION_REQUEST_MESSAGE = "Invalid media suggestion request."
INVALID_SUGGESTION_MESSAGE = "Invalid media suggestion."
LIBRARY_NOT_FOUND_MESSAGE = "Library not found."
SUGGESTION_PREPARATION_UNAVAILABLE_MESSAGE = "Local media preparation is not available."
SUGGESTION_PREPARATION_FAILED_MESSAGE = "Local media preparation failed."
SUGGESTION_PROVIDER_UNAVAILABLE_MESSAGE = "Media suggestion provider is not available."
SUGGESTION_PROVIDER_AUTH_MESSAGE = "Media suggestion provider authentication was rejected."
SUGGESTION_PROVIDER_RATE_LIMITED_MESSAGE = "Media suggestion provider rate limit was reached."
SUGGESTION_PROVIDER_MODEL_UNAVAILABLE_MESSAGE = "Media suggestion provider model is not available."
SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE = "Media suggestion provider response was invalid."
SUGGESTION_PROVIDER_EMPTY_RESPONSE_MESSAGE = (
    "Media suggestion provider returned empty final content."
)
SUGGESTION_PROVIDER_REFUSAL_MESSAGE = "Media suggestion provider refused the request."
SUGGESTION_PROVIDER_PENDING_TIMEOUT_MESSAGE = (
    "Media suggestion provider pending result timed out."
)
SUGGESTION_PROVIDER_FAILED_MESSAGE = "Media suggestion provider request failed."

TITLE_MAX_LENGTH = 120
DESCRIPTION_MAX_LENGTH = 600
COLLECTION_MAX_LENGTH = 40
TAG_MAX_LENGTH = 40
TAG_MIN_COUNT = 1
TAG_MAX_COUNT = 5
EVIDENCE_MAX_COUNT = 12
EVIDENCE_MAX_LENGTH = 240
UNCERTAINTY_MAX_COUNT = 12
UNCERTAINTY_MAX_LENGTH = 240
FILENAME_MAX_LENGTH = 180

_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_FILENAME_FORBIDDEN_PATTERN = re.compile(r'[/\\:*?"<>|]')
_WINDOWS_RESERVED_STEMS = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)


class FrameNestMediaSuggestionError(ValueError):
    """Sanitized error raised when suggestion values are invalid."""


class MediaSuggestionNotFoundError(RuntimeError):
    """Raised when the requested library is not registered."""


class MediaSuggestionPreparationUnavailableError(RuntimeError):
    """Raised when local preparation cannot run."""


class MediaSuggestionPreparationFailedError(RuntimeError):
    """Raised when local preparation fails unexpectedly."""


class MediaSuggestionProviderUnavailableError(RuntimeError):
    """Raised when the provider cannot be reached."""


class MediaSuggestionProviderAuthError(RuntimeError):
    """Raised when provider authentication is rejected."""


class MediaSuggestionProviderRateLimitedError(RuntimeError):
    """Raised when the provider rate limit is reached."""


class MediaSuggestionProviderModelUnavailableError(RuntimeError):
    """Raised when the configured provider model is unavailable."""


class MediaSuggestionProviderInvalidResponseError(RuntimeError):
    """Raised when provider output cannot be validated."""


class MediaSuggestionProviderTruncatedResponseError(MediaSuggestionProviderInvalidResponseError):
    """Raised when truncation evidence shows the final answer was cut off."""


class MediaSuggestionProviderEmptyResponseError(MediaSuggestionProviderInvalidResponseError):
    """Raised when the final answer field is empty without truncation evidence."""


class MediaSuggestionProviderRefusalError(MediaSuggestionProviderInvalidResponseError):
    """Raised when the provider safely signals a refusal."""


class MediaSuggestionProviderPendingTimeoutError(MediaSuggestionProviderUnavailableError):
    """Raised when HTTP 202 polling exceeds the configured pending timeout."""


class MediaSuggestionProviderFailedError(RuntimeError):
    """Raised when an unexpected provider failure occurs."""


def _validate_bounded_text(
    value: object,
    *,
    minimum: int,
    maximum: int,
    message: str,
) -> str:
    if not isinstance(value, str):
        raise FrameNestMediaSuggestionError(message)
    stripped = value.strip()
    if len(stripped) < minimum or len(stripped) > maximum:
        raise FrameNestMediaSuggestionError(message)
    if stripped != value:
        raise FrameNestMediaSuggestionError(message)
    if _CONTROL_CHAR_PATTERN.search(stripped):
        raise FrameNestMediaSuggestionError(message)
    return stripped


def _validate_string_list(
    value: object,
    *,
    minimum_count: int,
    maximum_count: int,
    item_maximum: int,
    message: str,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise FrameNestMediaSuggestionError(message)
    if len(value) < minimum_count or len(value) > maximum_count:
        raise FrameNestMediaSuggestionError(message)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _validate_bounded_text(
            item,
            minimum=1,
            maximum=item_maximum,
            message=message,
        )
        folded = text.casefold()
        if folded in seen:
            raise FrameNestMediaSuggestionError(message)
        seen.add(folded)
        normalized.append(text)
    return tuple(normalized)


def media_basename(relative_path: MediaRelativePath) -> str:
    """Return the basename for one validated relative media path."""
    basename = PurePosixPath(relative_path.value).name
    if not basename or basename in (".", ".."):
        raise FrameNestMediaSuggestionError(INVALID_SUGGESTION_REQUEST_MESSAGE)
    if _CONTROL_CHAR_PATTERN.search(basename) or _FILENAME_FORBIDDEN_PATTERN.search(basename):
        raise FrameNestMediaSuggestionError(INVALID_SUGGESTION_REQUEST_MESSAGE)
    return basename


def source_extension(relative_path: MediaRelativePath) -> str:
    """Return the lowercase source extension including the leading dot."""
    basename = media_basename(relative_path)
    if "." not in basename:
        raise FrameNestMediaSuggestionError(INVALID_SUGGESTION_REQUEST_MESSAGE)
    extension = "." + basename.rsplit(".", 1)[-1].lower()
    if extension == ".":
        raise FrameNestMediaSuggestionError(INVALID_SUGGESTION_REQUEST_MESSAGE)
    return extension


def validate_suggested_filename(value: object, *, required_extension: str) -> str:
    """Validate one provider-suggested basename against source extension policy."""
    message = INVALID_SUGGESTION_MESSAGE
    filename = _validate_bounded_text(value, minimum=1, maximum=FILENAME_MAX_LENGTH, message=message)
    if _FILENAME_FORBIDDEN_PATTERN.search(filename):
        raise FrameNestMediaSuggestionError(message)
    if filename.endswith(" ") or filename.endswith("."):
        raise FrameNestMediaSuggestionError(message)
    if not filename.lower().endswith(required_extension.lower()):
        raise FrameNestMediaSuggestionError(message)
    stem = filename[: -len(required_extension)] if required_extension else filename
    if not stem or stem.casefold() in _WINDOWS_RESERVED_STEMS:
        raise FrameNestMediaSuggestionError(message)
    return filename


def _validate_confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FrameNestMediaSuggestionError(INVALID_SUGGESTION_MESSAGE)
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 1:
        raise FrameNestMediaSuggestionError(INVALID_SUGGESTION_MESSAGE)
    return number


@dataclass(frozen=True, slots=True)
class MediaSuggestionRequest:
    """Provider-neutral prepared suggestion request."""

    basename: str
    candidate_kind: LibraryScanCandidateKind
    technical_metadata: TechnicalMetadata
    representative_frames: tuple[RepresentativeFrame, ...]
    prompt_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "basename",
            _validate_bounded_text(
                self.basename,
                minimum=1,
                maximum=FILENAME_MAX_LENGTH,
                message=INVALID_SUGGESTION_REQUEST_MESSAGE,
            ),
        )
        if not isinstance(self.candidate_kind, LibraryScanCandidateKind):
            raise FrameNestMediaSuggestionError(INVALID_SUGGESTION_REQUEST_MESSAGE)
        if not isinstance(self.technical_metadata, TechnicalMetadata):
            raise FrameNestMediaSuggestionError(INVALID_SUGGESTION_REQUEST_MESSAGE)
        if not isinstance(self.representative_frames, tuple) or not self.representative_frames:
            raise FrameNestMediaSuggestionError(INVALID_SUGGESTION_REQUEST_MESSAGE)
        for frame in self.representative_frames:
            if not isinstance(frame, RepresentativeFrame):
                raise FrameNestMediaSuggestionError(INVALID_SUGGESTION_REQUEST_MESSAGE)
        if not is_accepted_durable_identity(
            self.prompt_version, ACCEPTED_PROMPT_VERSIONS
        ):
            raise FrameNestMediaSuggestionError(INVALID_SUGGESTION_REQUEST_MESSAGE)


@dataclass(frozen=True, slots=True)
class MediaSuggestion:
    """Validated untrusted media suggestion preview."""

    title: str
    description: str
    collection: str
    tags: tuple[str, ...]
    suggested_filename: str
    confidence: float
    evidence: tuple[str, ...]
    uncertainties: tuple[str, ...]
    provider_id: str
    model_id: str
    prompt_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "title",
            _validate_bounded_text(
                self.title,
                minimum=1,
                maximum=TITLE_MAX_LENGTH,
                message=INVALID_SUGGESTION_MESSAGE,
            ),
        )
        object.__setattr__(
            self,
            "description",
            _validate_bounded_text(
                self.description,
                minimum=1,
                maximum=DESCRIPTION_MAX_LENGTH,
                message=INVALID_SUGGESTION_MESSAGE,
            ),
        )
        object.__setattr__(
            self,
            "collection",
            _validate_bounded_text(
                self.collection,
                minimum=1,
                maximum=COLLECTION_MAX_LENGTH,
                message=INVALID_SUGGESTION_MESSAGE,
            ),
        )
        object.__setattr__(
            self,
            "tags",
            _validate_string_list(
                self.tags,
                minimum_count=TAG_MIN_COUNT,
                maximum_count=TAG_MAX_COUNT,
                item_maximum=TAG_MAX_LENGTH,
                message=INVALID_SUGGESTION_MESSAGE,
            ),
        )
        object.__setattr__(
            self,
            "evidence",
            _validate_string_list(
                self.evidence,
                minimum_count=1,
                maximum_count=EVIDENCE_MAX_COUNT,
                item_maximum=EVIDENCE_MAX_LENGTH,
                message=INVALID_SUGGESTION_MESSAGE,
            ),
        )
        object.__setattr__(
            self,
            "uncertainties",
            _validate_string_list(
                self.uncertainties,
                minimum_count=0,
                maximum_count=UNCERTAINTY_MAX_COUNT,
                item_maximum=UNCERTAINTY_MAX_LENGTH,
                message=INVALID_SUGGESTION_MESSAGE,
            ),
        )
        object.__setattr__(self, "confidence", _validate_confidence(self.confidence))
        for field_name in ("provider_id", "model_id", "prompt_version"):
            object.__setattr__(
                self,
                field_name,
                _validate_bounded_text(
                    getattr(self, field_name),
                    minimum=1,
                    maximum=120,
                    message=INVALID_SUGGESTION_MESSAGE,
                ),
            )
        if not is_accepted_durable_identity(
            self.prompt_version, ACCEPTED_PROMPT_VERSIONS
        ):
            raise FrameNestMediaSuggestionError(INVALID_SUGGESTION_MESSAGE)


@dataclass(frozen=True, slots=True)
class MediaSuggestionPreviewResult:
    """Complete non-persistent suggestion preview for one library candidate."""

    library_id: LibraryId
    relative_path: MediaRelativePath
    prepared: PreparedAnalysisResult
    suggestion: MediaSuggestion

    @property
    def sent_frame_count(self) -> int:
        return len(self.prepared.representative_frames)


@dataclass(frozen=True, slots=True)
class ImportedMediaSuggestionPreviewResult:
    """Suggestion preview for one imported catalog location.

    Canonical metadata is not written by this preview. Companion-visible
    generic analysis persistence is an optional injected join.
    """

    media_id: MediaId
    location_id: MediaLocationId
    prepared: PreparedAnalysisResult
    suggestion: MediaSuggestion

    @property
    def sent_frame_count(self) -> int:
        return len(self.prepared.representative_frames)


def build_suggestion_request(prepared: PreparedAnalysisResult) -> MediaSuggestionRequest:
    """Construct one provider-neutral request from a prepared analysis result."""
    return MediaSuggestionRequest(
        basename=media_basename(prepared.relative_path),
        candidate_kind=prepared.candidate_kind,
        technical_metadata=prepared.technical_metadata,
        representative_frames=prepared.representative_frames,
        prompt_version=PROMPT_VERSION,
    )


class PreviewMediaSuggestion:
    """Compose local preparation and provider suggestion for one candidate."""

    def __init__(
        self,
        repository: LibraryRepository,
        preparer: LocalMediaAnalysisPreparer,
        provider: MediaSuggestionProvider,
    ) -> None:
        self._repository = repository
        self._preparer = preparer
        self._provider = provider

    def execute(
        self,
        library_id: LibraryId,
        relative_path: MediaRelativePath,
    ) -> MediaSuggestionPreviewResult:
        library = self._repository.get(library_id)
        if library is None:
            raise MediaSuggestionNotFoundError(LIBRARY_NOT_FOUND_MESSAGE)
        try:
            prepared = self._preparer.prepare(library.root, relative_path)
        except MediaAnalysisNotFoundError:
            raise MediaSuggestionNotFoundError(LIBRARY_NOT_FOUND_MESSAGE) from None
        except MediaAnalysisUnavailableError:
            raise MediaSuggestionPreparationUnavailableError(
                SUGGESTION_PREPARATION_UNAVAILABLE_MESSAGE
            ) from None
        except MediaAnalysisFailedError:
            raise MediaSuggestionPreparationFailedError(
                SUGGESTION_PREPARATION_FAILED_MESSAGE
            ) from None
        except FrameNestMediaAnalysisError:
            raise MediaSuggestionPreparationUnavailableError(
                SUGGESTION_PREPARATION_UNAVAILABLE_MESSAGE
            ) from None
        request = build_suggestion_request(prepared)
        suggestion = self._provider.suggest(request)
        return MediaSuggestionPreviewResult(
            library_id=library_id,
            relative_path=relative_path,
            prepared=prepared,
            suggestion=suggestion,
        )


class ImportedPreviewAnalysisJoin(Protocol):
    """Persist a companion-visible generic run from an already obtained suggestion."""

    def execute(
        self,
        media_id: MediaId,
        location_id: MediaLocationId,
        suggestion: MediaSuggestion,
    ) -> object | None:
        """Record one analyzed run without calling the provider again."""


class PreviewImportedMediaSuggestion:
    """Compose local preparation and provider suggestion for one imported media location."""

    def __init__(
        self,
        media_repository: MediaRepository,
        library_repository: LibraryRepository,
        preparer: LocalMediaAnalysisPreparer,
        provider: MediaSuggestionProvider,
        analysis_join: ImportedPreviewAnalysisJoin | None = None,
    ) -> None:
        self._media_repository = media_repository
        self._library_repository = library_repository
        self._preparer = preparer
        self._provider = provider
        self._analysis_join = analysis_join

    def execute(
        self,
        media_id: MediaId,
        location_id: MediaLocationId,
    ) -> ImportedMediaSuggestionPreviewResult:
        media = self._media_repository.get_media(media_id)
        if media is None:
            raise MediaSuggestionNotFoundError(LIBRARY_NOT_FOUND_MESSAGE)
        location = self._media_repository.get_location(location_id)
        if location is None or location.media_id != media_id:
            raise MediaSuggestionNotFoundError(LIBRARY_NOT_FOUND_MESSAGE)
        if location.availability != MediaLocationAvailability.AVAILABLE:
            raise MediaSuggestionPreparationUnavailableError(
                SUGGESTION_PREPARATION_UNAVAILABLE_MESSAGE
            )
        library = self._library_repository.get(location.library_id)
        if library is None:
            raise MediaSuggestionPreparationUnavailableError(
                SUGGESTION_PREPARATION_UNAVAILABLE_MESSAGE
            )
        extension = source_extension(MediaRelativePath(location.relative_path.value))
        if supported_media_type(media.kind, extension) is None:
            raise FrameNestMediaSuggestionError(INVALID_SUGGESTION_REQUEST_MESSAGE)
        try:
            prepared = self._preparer.prepare(
                library.root,
                MediaRelativePath(location.relative_path.value),
            )
        except MediaAnalysisNotFoundError:
            raise MediaSuggestionNotFoundError(LIBRARY_NOT_FOUND_MESSAGE) from None
        except MediaAnalysisUnavailableError:
            raise MediaSuggestionPreparationUnavailableError(
                SUGGESTION_PREPARATION_UNAVAILABLE_MESSAGE
            ) from None
        except MediaAnalysisFailedError:
            raise MediaSuggestionPreparationFailedError(
                SUGGESTION_PREPARATION_FAILED_MESSAGE
            ) from None
        except FrameNestMediaAnalysisError:
            raise MediaSuggestionPreparationUnavailableError(
                SUGGESTION_PREPARATION_UNAVAILABLE_MESSAGE
            ) from None
        request = build_suggestion_request(prepared)
        suggestion = self._provider.suggest(request)
        if self._analysis_join is not None:
            self._analysis_join.execute(media_id, location_id, suggestion)
        return ImportedMediaSuggestionPreviewResult(
            media_id=media_id,
            location_id=location_id,
            prepared=prepared,
            suggestion=suggestion,
        )
