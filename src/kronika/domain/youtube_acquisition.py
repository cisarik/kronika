"""Pure-domain YouTube manual-acquisition identity, provenance, and lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import re
import unicodedata
from urllib.parse import parse_qsl, urlsplit

from kronika.domain.identities import (
    MediaId,
    MediaLocationId,
    YouTubeAcquisitionClaimId,
)
from kronika.domain.identity_access import MAX_LOGIN_LENGTH, normalize_login
from kronika.domain.media_classification import AcquisitionSource
from kronika.domain.uploads import UploadSessionId

INVALID_YOUTUBE_URL_MESSAGE = "Invalid public YouTube video URL."
INVALID_YOUTUBE_CLAIM_MESSAGE = "Invalid YouTube acquisition claim."
INVALID_YOUTUBE_TRANSITION_MESSAGE = "Invalid YouTube acquisition transition."

MAX_SUBMITTED_URL_CODE_POINTS = 2_048
MAX_UPSTREAM_TITLE_CODE_POINTS = 500
MAX_UPSTREAM_CHANNEL_CODE_POINTS = 200
MAX_UPSTREAM_CHANNEL_ID_CODE_POINTS = 128
MAX_REMOTE_FILENAME_CODE_POINTS = 500
MAX_VERSION_CODE_POINTS = 120
MAX_FORMAT_ID_CODE_POINTS = 120

YOUTUBE_EXTRACTOR_KEY = "Youtube"
YOUTUBE_DOWNLOADER_NAME = "yt-dlp"

_VIDEO_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{11}")
_STAGING_KEY_PATTERN = re.compile(r"[0-9a-f]{32}")
_FAILURE_CODE_PATTERN = re.compile(r"[A-Z0-9_]{1,80}")
_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,119}")
_FORMAT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,119}")
_SOURCE_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

_YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})
_WATCH_QUERY_KEYS = frozenset({"v", "t", "si", "feature"})
_PATH_QUERY_KEYS = frozenset({"t", "si", "feature"})


class FrameNestYouTubeAcquisitionError(ValueError):
    """Sanitized base error for invalid YouTube acquisition data."""


class FrameNestYouTubeUrlError(FrameNestYouTubeAcquisitionError):
    """Raised when a submitted YouTube URL is outside the accepted policy."""


class FrameNestYouTubeClaimError(FrameNestYouTubeAcquisitionError):
    """Raised when durable claim provenance is internally inconsistent."""


class FrameNestYouTubeTransitionError(FrameNestYouTubeClaimError):
    """Raised when a claim state transition is not legal."""


class YouTubeAcquisitionState(StrEnum):
    """Durable source-specific acquisition states."""

    CLAIMED = "claimed"
    INSPECTING = "inspecting"
    DOWNLOAD_PENDING = "download_pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    HANDOFF = "handoff"
    HANDED_OFF = "handed_off"
    DUPLICATE_RESOLVED = "duplicate_resolved"
    CATALOGED = "cataloged"
    CATALOG_REMOVED = "catalog_removed"
    FAILED = "failed"


ACTIVE_YOUTUBE_ACQUISITION_STATES = frozenset(
    {
        YouTubeAcquisitionState.CLAIMED,
        YouTubeAcquisitionState.INSPECTING,
        YouTubeAcquisitionState.DOWNLOAD_PENDING,
        YouTubeAcquisitionState.DOWNLOADING,
        YouTubeAcquisitionState.DOWNLOADED,
        YouTubeAcquisitionState.HANDOFF,
        YouTubeAcquisitionState.HANDED_OFF,
    }
)

TERMINAL_YOUTUBE_ACQUISITION_STATES = frozenset(
    {
        YouTubeAcquisitionState.DUPLICATE_RESOLVED,
        YouTubeAcquisitionState.CATALOGED,
        YouTubeAcquisitionState.CATALOG_REMOVED,
        YouTubeAcquisitionState.FAILED,
    }
)

LIVE_CATALOG_YOUTUBE_ACQUISITION_STATES = frozenset(
    {
        YouTubeAcquisitionState.DUPLICATE_RESOLVED,
        YouTubeAcquisitionState.CATALOGED,
    }
)

REQUESTER_PHASE_QUEUED = "queued"
REQUESTER_PHASE_PROCESSING = "processing"
REQUESTER_PHASE_DOWNLOADING = "downloading"
REQUESTER_PHASE_FAILED = "failed"
REQUESTER_PHASE_COMPLETED = "completed"
REQUESTER_PHASE_COMPLETED_PRIVATE = "completed_private"
REQUESTER_PHASE_UNAVAILABLE = "unavailable"

_REQUESTER_PHASE_BY_ACTIVE_STATE: dict[YouTubeAcquisitionState, str] = {
    YouTubeAcquisitionState.CLAIMED: REQUESTER_PHASE_QUEUED,
    YouTubeAcquisitionState.INSPECTING: REQUESTER_PHASE_PROCESSING,
    YouTubeAcquisitionState.DOWNLOAD_PENDING: REQUESTER_PHASE_DOWNLOADING,
    YouTubeAcquisitionState.DOWNLOADING: REQUESTER_PHASE_DOWNLOADING,
    YouTubeAcquisitionState.DOWNLOADED: REQUESTER_PHASE_PROCESSING,
    YouTubeAcquisitionState.HANDOFF: REQUESTER_PHASE_PROCESSING,
    YouTubeAcquisitionState.HANDED_OFF: REQUESTER_PHASE_PROCESSING,
}

_ALLOWED_TRANSITIONS: dict[
    YouTubeAcquisitionState, frozenset[YouTubeAcquisitionState]
] = {
    YouTubeAcquisitionState.CLAIMED: frozenset(
        {
            YouTubeAcquisitionState.INSPECTING,
            YouTubeAcquisitionState.DUPLICATE_RESOLVED,
            YouTubeAcquisitionState.FAILED,
        }
    ),
    YouTubeAcquisitionState.INSPECTING: frozenset(
        {
            YouTubeAcquisitionState.CLAIMED,
            YouTubeAcquisitionState.DOWNLOAD_PENDING,
            YouTubeAcquisitionState.FAILED,
        }
    ),
    YouTubeAcquisitionState.DOWNLOAD_PENDING: frozenset(
        {
            YouTubeAcquisitionState.CLAIMED,
            YouTubeAcquisitionState.DOWNLOADING,
            YouTubeAcquisitionState.FAILED,
        }
    ),
    YouTubeAcquisitionState.DOWNLOADING: frozenset(
        {
            YouTubeAcquisitionState.CLAIMED,
            YouTubeAcquisitionState.DOWNLOAD_PENDING,
            YouTubeAcquisitionState.DOWNLOADED,
            YouTubeAcquisitionState.FAILED,
        }
    ),
    YouTubeAcquisitionState.DOWNLOADED: frozenset(
        {
            YouTubeAcquisitionState.HANDOFF,
            YouTubeAcquisitionState.FAILED,
        }
    ),
    YouTubeAcquisitionState.HANDOFF: frozenset(
        {
            YouTubeAcquisitionState.HANDED_OFF,
            YouTubeAcquisitionState.FAILED,
        }
    ),
    YouTubeAcquisitionState.HANDED_OFF: frozenset(
        {
            YouTubeAcquisitionState.DUPLICATE_RESOLVED,
            YouTubeAcquisitionState.CATALOGED,
            YouTubeAcquisitionState.FAILED,
        }
    ),
    YouTubeAcquisitionState.DUPLICATE_RESOLVED: frozenset(
        {
            YouTubeAcquisitionState.CATALOG_REMOVED,
        }
    ),
    YouTubeAcquisitionState.CATALOGED: frozenset(
        {
            YouTubeAcquisitionState.CATALOG_REMOVED,
        }
    ),
    YouTubeAcquisitionState.CATALOG_REMOVED: frozenset(),
    YouTubeAcquisitionState.FAILED: frozenset(),
}


class YouTubeConfirmationMethod(StrEnum):
    """How the owner explicitly confirmed one acquisition."""

    INTERACTIVE = "interactive"
    YES_FLAG = "yes_flag"


class YouTubeStagingCleanupState(StrEnum):
    """Durable status of exact claim-owned staging cleanup."""

    PENDING = "pending"
    COMPLETE = "complete"


class YouTubeFailureStage(StrEnum):
    """Sanitized bounded stage associated with a terminal failure code."""

    CONFIGURATION = "configuration"
    INSPECTION = "inspection"
    DOWNLOAD = "download"
    STAGING = "staging"
    HANDOFF = "handoff"
    DOWNSTREAM = "downstream"
    CLEANUP = "cleanup"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class YouTubeSourceIdentity:
    """Canonical public YouTube video identity derived from a supported URL."""

    video_id: str
    canonical_url: str
    extractor_key: str = YOUTUBE_EXTRACTOR_KEY

    def __post_init__(self) -> None:
        if (
            not isinstance(self.video_id, str)
            or _VIDEO_ID_PATTERN.fullmatch(self.video_id) is None
            or self.canonical_url
            != f"https://www.youtube.com/watch?v={self.video_id}"
            or self.extractor_key != YOUTUBE_EXTRACTOR_KEY
        ):
            raise FrameNestYouTubeUrlError(INVALID_YOUTUBE_URL_MESSAGE)


def canonicalize_youtube_url(value: object) -> YouTubeSourceIdentity:
    """Validate one cookie-free public video URL and return canonical identity."""
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > MAX_SUBMITTED_URL_CODE_POINTS
        or _has_control_character(value)
    ):
        raise FrameNestYouTubeUrlError(INVALID_YOUTUBE_URL_MESSAGE)
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except (UnicodeError, ValueError):
        raise FrameNestYouTubeUrlError(INVALID_YOUTUBE_URL_MESSAGE) from None
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or hostname is None
        or (port is not None and port != 443)
        or parsed.fragment
    ):
        raise FrameNestYouTubeUrlError(INVALID_YOUTUBE_URL_MESSAGE)
    host = hostname.lower()
    if parsed.netloc.lower() not in {host, f"{host}:443"}:
        raise FrameNestYouTubeUrlError(INVALID_YOUTUBE_URL_MESSAGE)
    try:
        query_pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=8,
        )
    except ValueError:
        raise FrameNestYouTubeUrlError(INVALID_YOUTUBE_URL_MESSAGE) from None
    if len({key for key, _value in query_pairs}) != len(query_pairs):
        raise FrameNestYouTubeUrlError(INVALID_YOUTUBE_URL_MESSAGE)
    query = dict(query_pairs)

    video_id: str | None = None
    if host == "youtu.be":
        if any(key not in _PATH_QUERY_KEYS for key in query):
            raise FrameNestYouTubeUrlError(INVALID_YOUTUBE_URL_MESSAGE)
        path_parts = parsed.path.split("/")
        if len(path_parts) == 2:
            video_id = path_parts[1]
        elif len(path_parts) == 3 and path_parts[2] == "":
            video_id = path_parts[1]
    elif host in _YOUTUBE_HOSTS and parsed.path == "/watch":
        if any(key not in _WATCH_QUERY_KEYS for key in query) or "v" not in query:
            raise FrameNestYouTubeUrlError(INVALID_YOUTUBE_URL_MESSAGE)
        video_id = query["v"]
    elif host in _YOUTUBE_HOSTS:
        if any(key not in _PATH_QUERY_KEYS for key in query):
            raise FrameNestYouTubeUrlError(INVALID_YOUTUBE_URL_MESSAGE)
        path_parts = parsed.path.split("/")
        if len(path_parts) in {3, 4} and path_parts[1] == "shorts":
            if len(path_parts) == 3 or path_parts[3] == "":
                video_id = path_parts[2]

    if video_id is None or _VIDEO_ID_PATTERN.fullmatch(video_id) is None:
        raise FrameNestYouTubeUrlError(INVALID_YOUTUBE_URL_MESSAGE)
    return YouTubeSourceIdentity(
        video_id=video_id,
        canonical_url=f"https://www.youtube.com/watch?v={video_id}",
    )


@dataclass(frozen=True, slots=True)
class YouTubeAcquisitionClaim:
    """Immutable snapshot of one durable source-specific acquisition claim."""

    id: YouTubeAcquisitionClaimId
    state: YouTubeAcquisitionState
    submitted_url: str
    canonical_url: str
    youtube_video_id: str
    extractor_key: str
    confirmation_method: YouTubeConfirmationMethod
    confirmed_at_ms: int
    staging_key: str
    generated_filename: str
    created_at_ms: int
    updated_at_ms: int
    acquisition_source: AcquisitionSource = AcquisitionSource.YOUTUBE_MANUAL_CLAIM
    retry_of_claim_id: YouTubeAcquisitionClaimId | None = None
    resolved_claim_id: YouTubeAcquisitionClaimId | None = None
    upload_id: UploadSessionId | None = None
    media_id: MediaId | None = None
    media_location_id: MediaLocationId | None = None
    upstream_title: str | None = None
    upstream_channel: str | None = None
    upstream_channel_id: str | None = None
    upstream_source_date: str | None = None
    downloader_name: str | None = None
    downloader_version: str | None = None
    extractor_version: str | None = None
    selected_video_format_id: str | None = None
    selected_audio_format_id: str | None = None
    remote_filename: str | None = None
    downloaded_size_bytes: int | None = None
    downloaded_at_ms: int | None = None
    completed_at_ms: int | None = None
    catalog_removed_at_ms: int | None = None
    failure_stage: YouTubeFailureStage | None = None
    failure_code: str | None = None
    cleanup_state: YouTubeStagingCleanupState = YouTubeStagingCleanupState.PENDING
    cleanup_completed_at_ms: int | None = None
    version: int = 0
    created_by_login_key: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, YouTubeAcquisitionClaimId):
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        if not isinstance(self.state, YouTubeAcquisitionState):
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        identity = canonicalize_youtube_url(self.submitted_url)
        if (
            identity.canonical_url != self.canonical_url
            or identity.video_id != self.youtube_video_id
            or identity.extractor_key != self.extractor_key
            or self.acquisition_source is not AcquisitionSource.YOUTUBE_MANUAL_CLAIM
        ):
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        if not isinstance(self.confirmation_method, YouTubeConfirmationMethod):
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        _require_non_negative(self.confirmed_at_ms)
        _require_non_negative(self.created_at_ms)
        _require_non_negative(self.updated_at_ms)
        _require_non_negative(self.version)
        if (
            self.confirmed_at_ms < self.created_at_ms
            or self.updated_at_ms < self.created_at_ms
        ):
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        if (
            not isinstance(self.staging_key, str)
            or _STAGING_KEY_PATTERN.fullmatch(self.staging_key) is None
            or self.generated_filename != f"youtube-{self.youtube_video_id}.mp4"
        ):
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        _validate_optional_claim_id(self.retry_of_claim_id, self.id)
        _validate_optional_claim_id(self.resolved_claim_id, self.id)
        if self.upload_id is not None and not isinstance(self.upload_id, UploadSessionId):
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        if self.media_id is not None and not isinstance(self.media_id, MediaId):
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        if self.media_location_id is not None and not isinstance(
            self.media_location_id, MediaLocationId
        ):
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        if (self.media_id is None) != (self.media_location_id is None):
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        _validate_created_by_login_key(self.created_by_login_key)
        _validate_advisory(
            self.upstream_title, MAX_UPSTREAM_TITLE_CODE_POINTS, allow_newline=False
        )
        _validate_advisory(
            self.upstream_channel, MAX_UPSTREAM_CHANNEL_CODE_POINTS, allow_newline=False
        )
        _validate_advisory(
            self.upstream_channel_id,
            MAX_UPSTREAM_CHANNEL_ID_CODE_POINTS,
            allow_newline=False,
        )
        if (
            self.upstream_source_date is not None
            and (
                not isinstance(self.upstream_source_date, str)
                or _SOURCE_DATE_PATTERN.fullmatch(self.upstream_source_date) is None
            )
        ):
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        _validate_token(self.downloader_name, _VERSION_PATTERN)
        _validate_token(self.downloader_version, _VERSION_PATTERN)
        _validate_token(self.extractor_version, _VERSION_PATTERN)
        _validate_token(self.selected_video_format_id, _FORMAT_ID_PATTERN)
        _validate_token(self.selected_audio_format_id, _FORMAT_ID_PATTERN)
        _validate_advisory(
            self.remote_filename, MAX_REMOTE_FILENAME_CODE_POINTS, allow_newline=False
        )
        _validate_optional_non_negative(self.downloaded_size_bytes, positive=True)
        _validate_optional_non_negative(self.downloaded_at_ms)
        _validate_optional_non_negative(self.completed_at_ms)
        _validate_optional_non_negative(self.catalog_removed_at_ms)
        _validate_optional_non_negative(self.cleanup_completed_at_ms)
        if not isinstance(self.cleanup_state, YouTubeStagingCleanupState):
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        if (
            self.cleanup_state is YouTubeStagingCleanupState.PENDING
            and self.cleanup_completed_at_ms is not None
        ) or (
            self.cleanup_state is YouTubeStagingCleanupState.COMPLETE
            and self.cleanup_completed_at_ms is None
        ):
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        if (self.failure_stage is None) != (self.failure_code is None):
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        if self.failure_stage is not None and not isinstance(
            self.failure_stage, YouTubeFailureStage
        ):
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        if self.failure_code is not None and (
            not isinstance(self.failure_code, str)
            or _FAILURE_CODE_PATTERN.fullmatch(self.failure_code) is None
        ):
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        _validate_state_payload(self)

    @classmethod
    def new(
        cls,
        *,
        submitted_url: str,
        confirmation_method: YouTubeConfirmationMethod,
        now_ms: int,
        retry_of_claim_id: YouTubeAcquisitionClaimId | None = None,
        created_by_login_key: str | None = None,
    ) -> YouTubeAcquisitionClaim:
        """Create a new owner-confirmed claim from authoritative URL parsing."""
        identity = canonicalize_youtube_url(submitted_url)
        claim_id = YouTubeAcquisitionClaimId.new()
        owner = None
        if created_by_login_key is not None:
            try:
                owner = normalize_login(created_by_login_key)
            except Exception as exc:
                raise FrameNestYouTubeClaimError(
                    INVALID_YOUTUBE_CLAIM_MESSAGE
                ) from exc
        return cls(
            id=claim_id,
            state=YouTubeAcquisitionState.CLAIMED,
            submitted_url=submitted_url,
            canonical_url=identity.canonical_url,
            youtube_video_id=identity.video_id,
            extractor_key=identity.extractor_key,
            confirmation_method=confirmation_method,
            confirmed_at_ms=now_ms,
            staging_key=claim_id.value.hex,
            generated_filename=f"youtube-{identity.video_id}.mp4",
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
            retry_of_claim_id=retry_of_claim_id,
            created_by_login_key=owner,
        )

    def advance(
        self,
        target_state: YouTubeAcquisitionState,
        *,
        updated_at_ms: int,
        **changes: object,
    ) -> YouTubeAcquisitionClaim:
        """Return one validated optimistic state transition snapshot."""
        ensure_youtube_transition_allowed(self.state, target_state)
        _reject_ownership_mutation(self, changes)
        return replace(
            self,
            state=target_state,
            updated_at_ms=updated_at_ms,
            version=self.version + 1,
            **changes,
        )

    def mark_catalog_removed(self, *, now_ms: int) -> YouTubeAcquisitionClaim:
        """Detach active catalog linkage after administrator catalog removal."""
        if self.state not in LIVE_CATALOG_YOUTUBE_ACQUISITION_STATES:
            raise FrameNestYouTubeTransitionError(INVALID_YOUTUBE_TRANSITION_MESSAGE)
        if self.media_id is None or self.media_location_id is None:
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        if (
            isinstance(now_ms, bool)
            or not isinstance(now_ms, int)
            or now_ms < 0
            or (
                self.completed_at_ms is not None and now_ms < self.completed_at_ms
            )
        ):
            raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
        return self.advance(
            YouTubeAcquisitionState.CATALOG_REMOVED,
            updated_at_ms=now_ms,
            media_id=None,
            media_location_id=None,
            catalog_removed_at_ms=now_ms,
        )

    def evolve(
        self,
        *,
        updated_at_ms: int,
        **changes: object,
    ) -> YouTubeAcquisitionClaim:
        """Return one same-state optimistic metadata or cleanup update."""
        _reject_ownership_mutation(self, changes)
        return replace(
            self,
            updated_at_ms=updated_at_ms,
            version=self.version + 1,
            **changes,
        )


def ensure_youtube_transition_allowed(
    source: YouTubeAcquisitionState,
    target: YouTubeAcquisitionState,
) -> None:
    """Reject states outside the source-specific durable transition graph."""
    if (
        not isinstance(source, YouTubeAcquisitionState)
        or not isinstance(target, YouTubeAcquisitionState)
        or target not in _ALLOWED_TRANSITIONS[source]
    ):
        raise FrameNestYouTubeTransitionError(INVALID_YOUTUBE_TRANSITION_MESSAGE)


def derive_requester_phase(
    claim: YouTubeAcquisitionClaim,
    *,
    media_is_published: bool | None,
) -> str:
    """Derive the sanitized ordinary-requester phase from durable claim truth."""
    if claim.state is YouTubeAcquisitionState.FAILED:
        return REQUESTER_PHASE_FAILED
    if claim.state is YouTubeAcquisitionState.CATALOG_REMOVED:
        return REQUESTER_PHASE_UNAVAILABLE
    if claim.state in ACTIVE_YOUTUBE_ACQUISITION_STATES:
        return _REQUESTER_PHASE_BY_ACTIVE_STATE[claim.state]
    if claim.state in LIVE_CATALOG_YOUTUBE_ACQUISITION_STATES:
        if claim.media_id is None or media_is_published is None:
            return REQUESTER_PHASE_UNAVAILABLE
        if media_is_published:
            return REQUESTER_PHASE_COMPLETED
        return REQUESTER_PHASE_COMPLETED_PRIVATE
    raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)


def normalize_advisory_text(value: object, *, maximum: int) -> str | None:
    """Return bounded NFC advisory text, or None for absent/blank upstream data."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized:
        return None
    _validate_advisory(normalized, maximum, allow_newline=False)
    return normalized


def _validate_state_payload(claim: YouTubeAcquisitionClaim) -> None:
    downloaded_states = {
        YouTubeAcquisitionState.DOWNLOADED,
        YouTubeAcquisitionState.HANDOFF,
        YouTubeAcquisitionState.HANDED_OFF,
        YouTubeAcquisitionState.CATALOGED,
    }
    if claim.state in downloaded_states and (
        claim.downloaded_size_bytes is None or claim.downloaded_at_ms is None
    ):
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
    if claim.state in {
        YouTubeAcquisitionState.HANDED_OFF,
        YouTubeAcquisitionState.CATALOGED,
    } and claim.upload_id is None:
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
    if claim.state is YouTubeAcquisitionState.CATALOGED and (
        claim.media_id is None
        or claim.completed_at_ms is None
        or claim.failure_code is not None
        or claim.catalog_removed_at_ms is not None
    ):
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
    if claim.state is YouTubeAcquisitionState.DUPLICATE_RESOLVED and (
        claim.media_id is None
        or claim.completed_at_ms is None
        or claim.failure_code is not None
        or claim.catalog_removed_at_ms is not None
    ):
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
    if claim.state is YouTubeAcquisitionState.CATALOG_REMOVED and (
        claim.media_id is not None
        or claim.media_location_id is not None
        or claim.completed_at_ms is None
        or claim.catalog_removed_at_ms is None
        or claim.catalog_removed_at_ms < claim.completed_at_ms
        or claim.failure_code is not None
    ):
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
    if claim.state is YouTubeAcquisitionState.FAILED and (
        claim.failure_code is None
        or claim.completed_at_ms is None
        or claim.catalog_removed_at_ms is not None
    ):
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
    if claim.state not in TERMINAL_YOUTUBE_ACQUISITION_STATES and (
        claim.completed_at_ms is not None
        or claim.failure_code is not None
        or claim.catalog_removed_at_ms is not None
    ):
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
    if (
        claim.state is not YouTubeAcquisitionState.CATALOG_REMOVED
        and claim.catalog_removed_at_ms is not None
    ):
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)


def _has_control_character(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _validate_advisory(
    value: object,
    maximum: int,
    *,
    allow_newline: bool,
) -> None:
    if value is None:
        return
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > maximum
        or value != unicodedata.normalize("NFC", value)
        or any(
            unicodedata.category(character) == "Cc"
            and not (allow_newline and character == "\n")
            for character in value
        )
    ):
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)


def _validate_token(value: object, pattern: re.Pattern[str]) -> None:
    if value is not None and (
        not isinstance(value, str) or pattern.fullmatch(value) is None
    ):
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)


def _validate_optional_claim_id(
    value: YouTubeAcquisitionClaimId | None,
    own_id: YouTubeAcquisitionClaimId,
) -> None:
    if value is not None and (
        not isinstance(value, YouTubeAcquisitionClaimId) or value == own_id
    ):
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)


def _require_non_negative(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
    return value


def _validate_optional_non_negative(
    value: object,
    *,
    positive: bool = False,
) -> None:
    if value is None:
        return
    _require_non_negative(value)
    if positive and value == 0:
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)


def _validate_created_by_login_key(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value or len(value) > MAX_LOGIN_LENGTH:
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
    try:
        normalized = normalize_login(value)
    except Exception as exc:
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE) from exc
    if value != normalized:
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)


def _reject_ownership_mutation(
    claim: YouTubeAcquisitionClaim,
    changes: dict[str, object],
) -> None:
    if "created_by_login_key" not in changes:
        return
    if changes["created_by_login_key"] != claim.created_by_login_key:
        raise FrameNestYouTubeClaimError(INVALID_YOUTUBE_CLAIM_MESSAGE)
