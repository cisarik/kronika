"""Pure-domain requester-private X post acquisition identity and lifecycle.

X acquisition is deliberately source-specific. It shares the proven YouTube
acquisition recovery, requester-ownership and catalog-handoff discipline but
keeps bounded X-specific post and asset identities, a validated numeric post
ID, a normalized extractor port and an explicit per-asset media model.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
import re
import unicodedata
from urllib.parse import urlsplit

from kronika.domain.identities import (
    MediaId,
    MediaLocationId,
    XAssetId,
    XPostClaimId,
)
from kronika.domain.identity_access import MAX_LOGIN_LENGTH, normalize_login
from kronika.domain.media_classification import (
    AcquisitionSource,
    ContentCategory,
)
from kronika.domain.media_metadata import MAX_DISPLAY_TITLE_CODE_POINTS

INVALID_X_URL_MESSAGE = "Invalid public X post URL."
INVALID_X_CLAIM_MESSAGE = "Invalid X acquisition claim."
INVALID_X_ASSET_MESSAGE = "Invalid X acquisition asset."
INVALID_X_TRANSITION_MESSAGE = "Invalid X acquisition transition."

MAX_SUBMITTED_URL_CODE_POINTS = 2_048
MAX_POST_TEXT_CODE_POINTS = 500
MAX_POST_ID_DIGITS = 19
MAX_HANDLE_CODE_POINTS = 64
MAX_AUTHOR_STABLE_ID_CODE_POINTS = 128
MAX_AUTHOR_DISPLAY_NAME_CODE_POINTS = 128
MAX_SOURCE_MEDIA_KEY_CODE_POINTS = 300
MAX_SELECTED_VARIANT_CODE_POINTS = 4_096
MAX_EXTRACTOR_VERSION_CODE_POINTS = 64

MAX_ASSETS_PER_POST = 4
MAX_VIDEO_DURATION_SECONDS = 300
MAX_ASSET_BYTES = 1_073_741_824
MAX_CLAIM_BYTES = 1_073_741_824
MAX_CLAIM_STAGING_FOOTPRINT_BYTES = 2_147_483_648

X_EXTRACTOR_KEY = "X"
X_DOWNLOADER_NAME = "yt-dlp"
X_MEDIA_EXTENSION_BY_TYPE = {
    "video": ".mp4",
    "animated_gif": ".mp4",
    "image": ".jpg",
}

_X_HOSTS = frozenset({"x.com", "www.x.com", "twitter.com", "www.twitter.com"})
_POST_ID_PATTERN = re.compile(rf"[0-9]{{1,{MAX_POST_ID_DIGITS}}}")
_STAGING_KEY_PATTERN = re.compile(r"[0-9a-f]{32}")
_FAILURE_CODE_PATTERN = re.compile(r"[A-Z0-9_]{1,80}")
_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")
_STANDARD_MIME_PATTERN = re.compile(r"[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+")


class FrameNestXAcquisitionError(ValueError):
    """Sanitized base error for invalid X acquisition data."""


class FrameNestXUrlError(FrameNestXAcquisitionError):
    """Raised when a submitted X URL is outside the accepted policy."""


class FrameNestXClaimError(FrameNestXAcquisitionError):
    """Raised when durable claim provenance is internally inconsistent."""


class FrameNestXAssetError(FrameNestXAcquisitionError):
    """Raised when durable asset provenance is internally inconsistent."""


class FrameNestXTransitionError(FrameNestXClaimError):
    """Raised when a claim or asset state transition is not legal."""


class XAcquisitionState(StrEnum):
    """Durable requester-private post-level acquisition states."""

    SUBMITTED = "submitted"
    QUEUED = "queued"
    EXTRACTING = "extracting"
    ACQUIRING = "acquiring"
    HANDING_OFF = "handing_off"
    COMPLETED = "completed"
    COMPLETED_PARTIAL = "completed_partial"
    FAILED = "failed"
    DUPLICATE_RESOLVED = "duplicate_resolved"
    CATALOG_REMOVED = "catalog_removed"


class XAssetState(StrEnum):
    """Durable per-asset acquisition states."""

    PENDING = "pending"
    EXTRACTED = "extracted"
    ACQUIRING = "acquiring"
    STAGED = "staged"
    HANDING_OFF = "handing_off"
    CATALOGED = "cataloged"
    FAILED = "failed"


ACTIVE_X_ACQUISITION_STATES = frozenset(
    {
        XAcquisitionState.SUBMITTED,
        XAcquisitionState.QUEUED,
        XAcquisitionState.EXTRACTING,
        XAcquisitionState.ACQUIRING,
        XAcquisitionState.HANDING_OFF,
    }
)

TERMINAL_X_ACQUISITION_STATES = frozenset(
    {
        XAcquisitionState.COMPLETED,
        XAcquisitionState.COMPLETED_PARTIAL,
        XAcquisitionState.FAILED,
        XAcquisitionState.DUPLICATE_RESOLVED,
        XAcquisitionState.CATALOG_REMOVED,
    }
)

LIVE_CATALOG_X_ACQUISITION_STATES = frozenset(
    {
        XAcquisitionState.COMPLETED,
        XAcquisitionState.COMPLETED_PARTIAL,
    }
)

SUCCESS_X_ACQUISITION_STATES = frozenset(
    {
        XAcquisitionState.COMPLETED,
        XAcquisitionState.COMPLETED_PARTIAL,
        XAcquisitionState.DUPLICATE_RESOLVED,
    }
)

REQUESTER_PHASE_QUEUED = "queued"
REQUESTER_PHASE_PROCESSING = "processing"
REQUESTER_PHASE_FAILED = "failed"
REQUESTER_PHASE_COMPLETED = "completed"
REQUESTER_PHASE_COMPLETED_PRIVATE = "completed_private"
REQUESTER_PHASE_UNAVAILABLE = "unavailable"

_REQUESTER_PHASE_BY_ACTIVE_STATE: dict[XAcquisitionState, str] = {
    XAcquisitionState.SUBMITTED: REQUESTER_PHASE_QUEUED,
    XAcquisitionState.QUEUED: REQUESTER_PHASE_QUEUED,
    XAcquisitionState.EXTRACTING: REQUESTER_PHASE_PROCESSING,
    XAcquisitionState.ACQUIRING: REQUESTER_PHASE_PROCESSING,
    XAcquisitionState.HANDING_OFF: REQUESTER_PHASE_PROCESSING,
}

_ALLOWED_POST_TRANSITIONS: dict[
    XAcquisitionState, frozenset[XAcquisitionState]
] = {
    XAcquisitionState.SUBMITTED: frozenset(
        {XAcquisitionState.QUEUED, XAcquisitionState.DUPLICATE_RESOLVED,
         XAcquisitionState.FAILED}
    ),
    XAcquisitionState.QUEUED: frozenset(
        {XAcquisitionState.EXTRACTING, XAcquisitionState.FAILED}
    ),
    XAcquisitionState.EXTRACTING: frozenset(
        {XAcquisitionState.ACQUIRING, XAcquisitionState.FAILED}
    ),
    XAcquisitionState.ACQUIRING: frozenset(
        {XAcquisitionState.HANDING_OFF, XAcquisitionState.FAILED}
    ),
    XAcquisitionState.HANDING_OFF: frozenset(
        {XAcquisitionState.COMPLETED, XAcquisitionState.COMPLETED_PARTIAL,
         XAcquisitionState.DUPLICATE_RESOLVED, XAcquisitionState.FAILED}
    ),
    XAcquisitionState.COMPLETED: frozenset({XAcquisitionState.CATALOG_REMOVED}),
    XAcquisitionState.COMPLETED_PARTIAL: frozenset(
        {XAcquisitionState.CATALOG_REMOVED, XAcquisitionState.QUEUED,
         XAcquisitionState.ACQUIRING}
    ),
    XAcquisitionState.DUPLICATE_RESOLVED: frozenset(
        {XAcquisitionState.CATALOG_REMOVED}
    ),
    XAcquisitionState.FAILED: frozenset(
        {XAcquisitionState.QUEUED, XAcquisitionState.ACQUIRING}
    ),
    XAcquisitionState.CATALOG_REMOVED: frozenset(),
}

_ALLOWED_ASSET_TRANSITIONS: dict[XAssetState, frozenset[XAssetState]] = {
    XAssetState.PENDING: frozenset({XAssetState.EXTRACTED, XAssetState.FAILED}),
    XAssetState.EXTRACTED: frozenset({XAssetState.ACQUIRING, XAssetState.FAILED}),
    XAssetState.ACQUIRING: frozenset({XAssetState.STAGED, XAssetState.FAILED}),
    XAssetState.STAGED: frozenset({XAssetState.HANDING_OFF, XAssetState.FAILED}),
    XAssetState.HANDING_OFF: frozenset(
        {XAssetState.CATALOGED, XAssetState.FAILED}
    ),
    XAssetState.CATALOGED: frozenset(),
    # Retry: an eligible failed asset is deliberately reset to pending.
    XAssetState.FAILED: frozenset({XAssetState.PENDING}),
}

ACTIVE_X_ASSET_STATES = frozenset(
    {
        XAssetState.PENDING,
        XAssetState.EXTRACTED,
        XAssetState.ACQUIRING,
        XAssetState.STAGED,
        XAssetState.HANDING_OFF,
    }
)

SUCCESS_X_ASSET_STATES = frozenset({XAssetState.CATALOGED})


class XStagingCleanupState(StrEnum):
    """Durable status of exact asset staging cleanup."""

    PENDING = "pending"
    COMPLETE = "complete"


class XFailureStage(StrEnum):
    """Sanitized bounded stage associated with a terminal failure code."""

    CONFIGURATION = "configuration"
    EXTRACTION = "extraction"
    ACQUISITION = "acquisition"
    STAGING = "staging"
    HANDOFF = "handoff"
    DOWNSTREAM = "downstream"
    CLEANUP = "cleanup"
    INTERNAL = "internal"


class XMediaType(StrEnum):
    """Normalized supported source media type."""

    VIDEO = "video"
    ANIMATED_GIF = "animated_gif"
    IMAGE = "image"


X_VARIANT_PHOTO_JPEG = "x-photo-orig-jpeg-v1"
X_VARIANT_PHOTO_PNG = "x-photo-orig-png-v1"
X_VARIANT_VIDEO_MP4 = "x-video-default-mp4-v1"
X_VARIANT_ANIMATED_GIF_MP4 = "x-animated-gif-mp4-v1"
X_VARIANT_ANIMATED_GIF_LITERAL = "x-animated-gif-literal-gif-v1"

SUPPORTED_X_VARIANTS: frozenset[str] = frozenset(
    {
        X_VARIANT_PHOTO_JPEG,
        X_VARIANT_PHOTO_PNG,
        X_VARIANT_VIDEO_MP4,
        X_VARIANT_ANIMATED_GIF_MP4,
        X_VARIANT_ANIMATED_GIF_LITERAL,
    }
)

_X_FAILURE_RETRYABLE: frozenset[str] = frozenset(
    {
        "X_RATE_LIMITED",
        "X_EXTRACTOR_UNAVAILABLE",
        "X_EXTRACTOR_FAILED",
        "X_DOWNLOAD_TIMEOUT",
        "X_STAGING_FAILED",
        "X_INSUFFICIENT_DISK",
        "X_CLEANUP_FAILED",
        "X_CATALOG_HANDOFF_FAILED",
    }
)

#: Stable sanitized codes considered terminal and not retryable.
TERMINAL_X_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "X_URL_UNSUPPORTED",
        "X_URL_INVALID_POST_ID",
        "X_HOST_UNSUPPORTED",
        "X_POST_UNAVAILABLE",
        "X_POST_DELETED",
        "X_POST_PROTECTED",
        "X_AUTHENTICATION_REQUIRED",
        "X_EXTRACTOR_MALFORMED",
        "X_EXTERNAL_LINK_DENIED",
        "X_NO_SUPPORTED_MEDIA",
        "X_MEDIA_TYPE_UNSUPPORTED",
        "X_TOO_MANY_ASSETS",
        "X_MEDIA_TOO_LARGE",
        "X_CLAIM_TOO_LARGE",
        "X_DURATION_TOO_LONG",
        "X_DIMENSIONS_TOO_LARGE",
        "X_CODEC_UNSUPPORTED",
        "X_DUPLICATE_CLAIM",
        "X_SOURCE_MEDIA_CHANGED",
        "X_PARTIAL_MULTI_ASSET",
        "X_MULTI_ASSET_FAILED",
    }
)

CLAIM_WIDE_X_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "X_URL_UNSUPPORTED",
        "X_URL_INVALID_POST_ID",
        "X_HOST_UNSUPPORTED",
        "X_POST_UNAVAILABLE",
        "X_POST_DELETED",
        "X_POST_PROTECTED",
        "X_AUTHENTICATION_REQUIRED",
        "X_EXTRACTOR_MALFORMED",
        "X_EXTERNAL_LINK_DENIED",
        "X_NO_SUPPORTED_MEDIA",
        "X_TOO_MANY_ASSETS",
        "X_CLAIM_TOO_LARGE",
        "X_DUPLICATE_CLAIM",
        "X_PARTIAL_MULTI_ASSET",
        "X_MULTI_ASSET_FAILED",
    }
)

KNOWN_X_FAILURE_CODES = _X_FAILURE_RETRYABLE | TERMINAL_X_FAILURE_CODES


def is_retryable_x_failure(code: object) -> bool:
    if not isinstance(code, str):
        return False
    return code in _X_FAILURE_RETRYABLE


def is_claim_wide_x_failure(code: object) -> bool:
    if not isinstance(code, str):
        return False
    return code in CLAIM_WIDE_X_FAILURE_CODES


@dataclass(frozen=True, slots=True)
class XSourceIdentity:
    """Canonical public X post identity derived from a supported URL."""

    post_id: str
    submitted_url: str
    canonical_url: str
    extractor_key: str = X_EXTRACTOR_KEY

    def __post_init__(self) -> None:
        if (
            not isinstance(self.post_id, str)
            or _POST_ID_PATTERN.fullmatch(self.post_id) is None
            or not isinstance(self.submitted_url, str)
            or _POST_ID_PATTERN.fullmatch(self.post_id) is None
            or self.extractor_key != X_EXTRACTOR_KEY
        ):
            raise FrameNestXUrlError(INVALID_X_URL_MESSAGE)
        if not isinstance(self.canonical_url, str):
            raise FrameNestXUrlError(INVALID_X_URL_MESSAGE)
        # Canonical URL is either an extractor-returned allowlisted URL bound to
        # the same post ID or a normalized safe display URL derived from input.
        parsed = _split_url(self.canonical_url)
        if parsed is None:
            raise FrameNestXUrlError(INVALID_X_URL_MESSAGE)
        host, _port, handle, raw_post_id = parsed
        if handle is None or handle.startswith("i"):
            raise FrameNestXUrlError(INVALID_X_URL_MESSAGE)
        if raw_post_id is None or raw_post_id != self.post_id:
            raise FrameNestXUrlError(INVALID_X_URL_MESSAGE)
        if host not in _X_HOSTS:
            raise FrameNestXUrlError(INVALID_X_URL_MESSAGE)


def accept_x_post_url(url: str) -> XSourceIdentity:
    """Validate one public X post URL and return a canonical source identity."""
    parsed = _split_url(url)
    if parsed is None:
        raise FrameNestXUrlError(INVALID_X_URL_MESSAGE)
    host, port, handle, post_id = parsed
    if post_id is None or _POST_ID_PATTERN.fullmatch(post_id) is None:
        raise FrameNestXUrlError(INVALID_X_URL_MESSAGE)
    if handle is None or not _HANDLE_PATTERN.fullmatch(handle):
        raise FrameNestXUrlError(INVALID_X_URL_MESSAGE)
    safe_handle = handle.rstrip("_")
    return XSourceIdentity(
        post_id=post_id,
        submitted_url=url,
        canonical_url=(
            f"https://x.com/{safe_handle}/status/{post_id}"
        ),
    )


@dataclass(frozen=True, slots=True)
class XNormalizedAssetDescriptor:
    """Normalized source asset surfaced by the extractor port."""

    ordinal: int
    media_type: XMediaType
    expected_mime: str
    source_media_key: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: int | None = None
    selected_variant: str | None = None
    provider_download_index: int | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class XNormalizedInspection:
    """Normalized extractor inspection result (never raw provider output)."""

    post_id: str
    canonical_url: str | None
    post_text: str | None
    posted_at_ms: int | None
    author_stable_id: str | None
    author_handle: str | None
    author_display_name: str | None
    assets: tuple[XNormalizedAssetDescriptor, ...]
    extractor_version: str | None
    warnings: tuple[str, ...] = ()
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class XPostClaim:
    """Immutable snapshot of one durable requester-private post claim."""

    id: XPostClaimId
    state: XAcquisitionState
    submitted_url: str
    canonical_url: str
    x_post_id: str
    extractor_key: str
    created_at_ms: int
    updated_at_ms: int
    acquisition_source: AcquisitionSource = AcquisitionSource.X_MANUAL_CLAIM
    created_by_login_key: str | None = None
    retry_of_claim_id: XPostClaimId | None = None
    resolved_claim_id: XPostClaimId | None = None
    source_author_stable_id: str | None = None
    source_author_handle: str | None = None
    source_author_display_name: str | None = None
    source_post_text: str | None = None
    source_posted_at_ms: int | None = None
    title: str | None = None
    extractor_version: str | None = None
    discovered_asset_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    completed_at_ms: int | None = None
    catalog_removed_at_ms: int | None = None
    failure_stage: XFailureStage | None = None
    failure_code: str | None = None
    cleanup_state: XStagingCleanupState = XStagingCleanupState.PENDING
    cleanup_completed_at_ms: int | None = None
    requested_content_category: ContentCategory | None = None
    version: int = field(default=0, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.id, XPostClaimId):
            raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
        if not isinstance(self.state, XAcquisitionState):
            raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
        identity = accept_x_post_url(self.submitted_url)
        if (
            identity.post_id != self.x_post_id
            or identity.extractor_key != self.extractor_key
            or self.acquisition_source is not AcquisitionSource.X_MANUAL_CLAIM
        ):
            raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
        _validate_display_url(self.canonical_url, self.x_post_id)
        _require_non_negative(self.created_at_ms)
        _require_non_negative(self.updated_at_ms)
        _require_non_negative(self.version)
        if self.updated_at_ms < self.created_at_ms:
            raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
        if not isinstance(self.extractor_key, str) or not self.extractor_key:
            raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
        _validate_optional_post_id(self.retry_of_claim_id, self.id)
        _validate_optional_post_id(self.resolved_claim_id, self.id)
        _validate_created_by_login_key(self.created_by_login_key)
        _validate_advisory(
            self.source_author_stable_id,
            MAX_AUTHOR_STABLE_ID_CODE_POINTS,
            allow_newline=False,
        )
        _validate_advisory(
            self.source_author_display_name,
            MAX_AUTHOR_DISPLAY_NAME_CODE_POINTS,
            allow_newline=False,
        )
        if self.source_author_handle is not None:
            _validate_advisory(
                self.source_author_handle,
                MAX_HANDLE_CODE_POINTS,
                allow_newline=False,
            )
            if "@" in self.source_author_handle or self.source_author_handle != self.source_author_handle.lower():
                raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
        _validate_advisory(self.source_post_text, MAX_POST_TEXT_CODE_POINTS, allow_newline=True)
        _validate_optional_non_negative(self.source_posted_at_ms)
        _validate_advisory(self.title, MAX_DISPLAY_TITLE_CODE_POINTS, allow_newline=False)
        _validate_token(self.extractor_version, _VERSION_PATTERN)
        _validate_count(self.discovered_asset_count, maximum=MAX_ASSETS_PER_POST)
        _validate_count(self.success_count, maximum=MAX_ASSETS_PER_POST)
        _validate_count(self.failure_count, maximum=MAX_ASSETS_PER_POST)
        if self.success_count + self.failure_count > self.discovered_asset_count:
            raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
        _validate_optional_non_negative(self.completed_at_ms)
        _validate_optional_non_negative(self.catalog_removed_at_ms)
        if (self.failure_stage is None) != (self.failure_code is None):
            raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
        if self.failure_stage is not None and not isinstance(self.failure_stage, XFailureStage):
            raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
        if self.failure_code is not None and (
            not isinstance(self.failure_code, str)
            or _FAILURE_CODE_PATTERN.fullmatch(self.failure_code) is None
        ):
            raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
        if (
            self.requested_content_category is not None
            and not isinstance(self.requested_content_category, ContentCategory)
        ):
            raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
        if not isinstance(self.cleanup_state, XStagingCleanupState):
            raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
        if (
            self.cleanup_state is XStagingCleanupState.PENDING
            and self.cleanup_completed_at_ms is not None
        ) or (
            self.cleanup_state is XStagingCleanupState.COMPLETE
            and self.cleanup_completed_at_ms is None
        ):
            raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
        if self.state not in TERMINAL_X_ACQUISITION_STATES and (
            self.completed_at_ms is not None
            or self.failure_code is not None
            or self.catalog_removed_at_ms is not None
        ):
            raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)

    @classmethod
    def new(
        cls,
        *,
        submitted_url: str,
        now_ms: int,
        retry_of_claim_id: XPostClaimId | None = None,
        created_by_login_key: str | None = None,
        requested_content_category: ContentCategory | None = None,
    ) -> XPostClaim:
        identity = accept_x_post_url(submitted_url)
        claim_id = XPostClaimId.new()
        owner = None
        if created_by_login_key is not None:
            try:
                owner = normalize_login(created_by_login_key)
            except Exception as exc:
                raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE) from exc
        return cls(
            id=claim_id,
            state=XAcquisitionState.SUBMITTED,
            submitted_url=submitted_url,
            canonical_url=identity.canonical_url,
            x_post_id=identity.post_id,
            extractor_key=identity.extractor_key,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
            retry_of_claim_id=retry_of_claim_id,
            created_by_login_key=owner,
            title=default_x_title(identity.post_id),
            requested_content_category=requested_content_category,
        )

    def advance(
        self,
        target_state: XAcquisitionState,
        *,
        updated_at_ms: int,
        **changes: object,
    ) -> XPostClaim:
        ensure_x_transition_allowed(self.state, target_state)
        return replace(
            self,
            state=target_state,
            updated_at_ms=updated_at_ms,
            version=self.version + 1,
            **changes,
        )

    def mark_catalog_removed(self, *, now_ms: int) -> XPostClaim:
        if self.state not in LIVE_CATALOG_X_ACQUISITION_STATES:
            raise FrameNestXTransitionError(INVALID_X_TRANSITION_MESSAGE)
        _require_non_negative(now_ms)
        if self.completed_at_ms is not None and now_ms < self.completed_at_ms:
            raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
        return self.advance(
            XAcquisitionState.CATALOG_REMOVED,
            updated_at_ms=now_ms,
            catalog_removed_at_ms=now_ms,
        )

    def evolve(self, *, updated_at_ms: int, **changes: object) -> XPostClaim:
        return replace(
            self,
            updated_at_ms=updated_at_ms,
            version=self.version + 1,
            **changes,
        )


@dataclass(frozen=True, slots=True)
class XAsset:
    """Immutable snapshot of one durable source media within a claim."""

    id: XAssetId
    claim_id: XPostClaimId
    ordinal: int
    media_type: XMediaType
    expected_mime: str
    stage_key: str
    created_at_ms: int
    updated_at_ms: int
    source_media_key: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: int | None = None
    selected_variant: str | None = None
    state: XAssetState = XAssetState.PENDING
    acquired_bytes: int | None = None
    acquired_sha256: str | None = None
    media_id: MediaId | None = None
    media_location_id: MediaLocationId | None = None
    upload_asset_key: str | None = None
    completed_at_ms: int | None = None
    failure_stage: XFailureStage | None = None
    failure_code: str | None = None
    cleanup_state: XStagingCleanupState = XStagingCleanupState.PENDING
    cleanup_completed_at_ms: int | None = None
    version: int = field(default=0, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.id, XAssetId):
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        if not isinstance(self.claim_id, XPostClaimId):
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or not (0 <= self.ordinal < MAX_ASSETS_PER_POST):
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        if not isinstance(self.media_type, XMediaType):
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        _validate_advisory(self.expected_mime, 120, allow_newline=False)
        if _STANDARD_MIME_PATTERN.fullmatch(self.expected_mime) is None:
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        if not isinstance(self.stage_key, str) or _STAGING_KEY_PATTERN.fullmatch(self.stage_key) is None:
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        _require_non_negative(self.created_at_ms)
        _require_non_negative(self.updated_at_ms)
        _require_non_negative(self.version)
        if self.updated_at_ms < self.created_at_ms:
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        _validate_advisory(self.source_media_key, MAX_SOURCE_MEDIA_KEY_CODE_POINTS, allow_newline=False)
        _validate_optional_non_negative(self.width, positive=False)
        _validate_optional_non_negative(self.height, positive=False)
        _validate_optional_non_negative(self.duration_seconds, positive=False)
        if self.duration_seconds is not None and self.duration_seconds > MAX_VIDEO_DURATION_SECONDS:
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        _validate_advisory(self.selected_variant, MAX_SELECTED_VARIANT_CODE_POINTS, allow_newline=False)
        if not isinstance(self.state, XAssetState):
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        _validate_optional_non_negative(self.acquired_bytes, positive=True)
        if self.acquired_sha256 is not None and (
            not isinstance(self.acquired_sha256, str)
            or _SHA256_PATTERN.fullmatch(self.acquired_sha256) is None
        ):
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        if self.media_id is not None and not isinstance(self.media_id, MediaId):
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        if self.media_location_id is not None and not isinstance(self.media_location_id, MediaLocationId):
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        if (self.media_id is None) != (self.media_location_id is None):
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        _validate_advisory(self.upload_asset_key, MAX_SOURCE_MEDIA_KEY_CODE_POINTS, allow_newline=False)
        _validate_optional_non_negative(self.completed_at_ms)
        if (self.failure_stage is None) != (self.failure_code is None):
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        if self.failure_stage is not None and not isinstance(self.failure_stage, XFailureStage):
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        if self.failure_code is not None and (
            not isinstance(self.failure_code, str)
            or _FAILURE_CODE_PATTERN.fullmatch(self.failure_code) is None
        ):
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        if not isinstance(self.cleanup_state, XStagingCleanupState):
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        if (
            self.cleanup_state is XStagingCleanupState.PENDING
            and self.cleanup_completed_at_ms is not None
        ) or (
            self.cleanup_state is XStagingCleanupState.COMPLETE
            and self.cleanup_completed_at_ms is None
        ):
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        if self.state is XAssetState.CATALOGED and (
            self.media_id is None
            or self.media_location_id is None
            or self.acquired_bytes is None
            or self.acquired_sha256 is None
            or self.completed_at_ms is None
        ):
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        if self.state is XAssetState.FAILED and self.failure_code is None:
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)
        if self.state in SUCCESS_X_ASSET_STATES and self.failure_code is not None:
            raise FrameNestXAssetError(INVALID_X_ASSET_MESSAGE)

    @classmethod
    def new(
        cls,
        *,
        claim_id: XPostClaimId,
        ordinal: int,
        media_type: XMediaType,
        expected_mime: str,
        now_ms: int,
        source_media_key: str | None = None,
        width: int | None = None,
        height: int | None = None,
        duration_seconds: int | None = None,
        selected_variant: str | None = None,
    ) -> XAsset:
        asset_id = XAssetId.new()
        return cls(
            id=asset_id,
            claim_id=claim_id,
            ordinal=ordinal,
            media_type=media_type,
            expected_mime=expected_mime,
            stage_key=asset_id.value.hex,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
            source_media_key=source_media_key,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            selected_variant=selected_variant,
        )

    def advance(
        self,
        target_state: XAssetState,
        *,
        updated_at_ms: int,
        **changes: object,
    ) -> XAsset:
        ensure_x_asset_transition_allowed(self.state, target_state)
        return replace(
            self,
            state=target_state,
            updated_at_ms=updated_at_ms,
            version=self.version + 1,
            **changes,
        )

    def evolve(self, *, updated_at_ms: int, **changes: object) -> XAsset:
        return replace(
            self,
            updated_at_ms=updated_at_ms,
            version=self.version + 1,
            **changes,
        )


def ensure_x_transition_allowed(source: XAcquisitionState, target: XAcquisitionState) -> None:
    if (
        not isinstance(source, XAcquisitionState)
        or not isinstance(target, XAcquisitionState)
        or target not in _ALLOWED_POST_TRANSITIONS[source]
    ):
        raise FrameNestXTransitionError(INVALID_X_TRANSITION_MESSAGE)


def ensure_x_asset_transition_allowed(source: XAssetState, target: XAssetState) -> None:
    if (
        not isinstance(source, XAssetState)
        or not isinstance(target, XAssetState)
        or target not in _ALLOWED_ASSET_TRANSITIONS[source]
    ):
        raise FrameNestXTransitionError(INVALID_X_TRANSITION_MESSAGE)


def derive_x_requester_phase(claim: XPostClaim) -> str:
    """Derive the sanitized requester phase from durable post truth."""
    if claim.state is XAcquisitionState.FAILED:
        return REQUESTER_PHASE_FAILED
    if claim.state is XAcquisitionState.CATALOG_REMOVED:
        return REQUESTER_PHASE_UNAVAILABLE
    if claim.state in ACTIVE_X_ACQUISITION_STATES:
        return _REQUESTER_PHASE_BY_ACTIVE_STATE[claim.state]
    if claim.state in LIVE_CATALOG_X_ACQUISITION_STATES:
        return REQUESTER_PHASE_COMPLETED
    if claim.state is XAcquisitionState.DUPLICATE_RESOLVED:
        return REQUESTER_PHASE_COMPLETED
    raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)


def default_x_category(media_type: XMediaType) -> ContentCategory:
    """Deterministic default content category for one normalized asset."""
    if media_type is XMediaType.IMAGE:
        return ContentCategory.GENERAL
    return ContentCategory.MEME


def parse_x_requested_content_category(value: object) -> ContentCategory:
    """Parse one explicit Save-time canonical category."""
    if isinstance(value, ContentCategory):
        return value
    if isinstance(value, str):
        try:
            return ContentCategory(value)
        except ValueError as exc:
            raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE) from exc
    raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)


def normalize_x_creator(
    *,
    stable_id: object,
    handle: object,
    display_name: object,
) -> tuple[str | None, str | None, str | None]:
    """Normalize source author data returning (stable_id, handle, display)."""
    normalized_stable = normalize_x_author_text(stable_id, MAX_AUTHOR_STABLE_ID_CODE_POINTS)
    normalized_handle = normalize_x_author_text(handle, MAX_HANDLE_CODE_POINTS)
    normalized_display = normalize_x_author_text(
        display_name, MAX_AUTHOR_DISPLAY_NAME_CODE_POINTS
    )
    if normalized_handle is not None:
        normalized_handle = normalized_handle.lower().lstrip("@")
        if not normalized_handle:
            normalized_handle = None
    return normalized_stable, normalized_handle, normalized_display


def normalize_x_author_text(value: object, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized:
        return None
    if len(normalized) > maximum or any(
        unicodedata.category(character) == "Cc" for character in normalized
    ):
        raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
    return normalized


def default_x_title(post_id: str) -> str:
    """Deterministic bounded fallback title based on post identity."""
    return f"X post {post_id}"


def x_title_from_post_post(
    post_text: object,
    *,
    creator_handle: object,
    media_type_label: str,
    post_id: str,
    ordinal: int,
) -> str:
    """Build the deterministic initial display title for one resulting item."""
    label = None
    if isinstance(post_text, str) and post_text.strip():
        normalized = _first_useful_sentence(post_text)
        if normalized:
            label = normalized
    if label is None and creator_handle:
        label = f"{_bounded_creator_label(creator_handle)} {media_type_label}"
    if label is None:
        label = f"X post {post_id}"
    if ordinal > 0:
        label = f"{label} ({ordinal + 1})"
    return unicodedata.normalize("NFC", label)[:MAX_DISPLAY_TITLE_CODE_POINTS]


def _first_useful_sentence(value: str) -> str | None:
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()
    if not normalized:
        return None
    meaningful = re.sub(
        r"(?i)https?://\S+|(^|\s)#[A-Za-z0-9_]+|(^|\s)@[A-Za-z0-9_]+",
        " ",
        normalized,
    ).strip()
    if not meaningful:
        return None
    sentence = re.split(r"[.!?](?:\s|$)", normalized, maxsplit=1)[0].strip()
    if not sentence or (len(sentence) > 3 and not re.search(r"[a-zA-Z0-9]", sentence)):
        return None
    return sentence[:MAX_DISPLAY_TITLE_CODE_POINTS]


def _bounded_creator_label(handle: str) -> str:
    cleaned = normalize_x_author_text(handle, MAX_HANDLE_CODE_POINTS)
    if cleaned is None:
        return "creator"
    return cleaned


# Helpers ----------------------------------------------------------------------

def _split_url(value: object) -> tuple[str, int | None, str | None, str | None] | None:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > MAX_SUBMITTED_URL_CODE_POINTS
        or _has_control_character(value)
    ):
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except (UnicodeError, ValueError):
        return None
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or hostname is None
        or (port is not None and port != 443)
        or parsed.fragment
        or parsed.query
    ):
        return None
    host = hostname.lower()
    if host not in _X_HOSTS:
        return None
    if parsed.netloc.lower() not in {host, f"{host}:443"}:
        return None
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) != 3 or path_parts[1] != "status":
        return None
    handle = path_parts[0]
    post_id = path_parts[2]
    return host, None, handle, post_id


def _validate_display_url(value: str, post_id: str) -> None:
    if not isinstance(value, str) or not value:
        raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
    parsed = _split_url(value)
    if parsed is None:
        raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
    if parsed[2] is None or parsed[2].startswith("i"):
        raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
    if parsed[3] != post_id:
        raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)


def _has_control_character(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _validate_advisory(value: object, maximum: int, *, allow_newline: bool) -> None:
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
        raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)


def _validate_token(value: object, pattern: re.Pattern[str]) -> None:
    if value is not None and (
        not isinstance(value, str) or pattern.fullmatch(value) is None
    ):
        raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)


def _validate_optional_post_id(value: XPostClaimId | None, own_id: XPostClaimId) -> None:
    if value is not None and (
        not isinstance(value, XPostClaimId) or value == own_id
    ):
        raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)


def _require_non_negative(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
    return value


def _validate_optional_non_negative(value: object, *, positive: bool = False) -> None:
    if value is None:
        return
    _require_non_negative(value)
    if positive and value == 0:
        raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)


def _validate_count(value: object, *, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
    if value < 0 or value > maximum:
        raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)


def _validate_created_by_login_key(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value or len(value) > MAX_LOGIN_LENGTH:
        raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)
    try:
        normalized = normalize_login(value)
    except Exception as exc:
        raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE) from exc
    if value != normalized:
        raise FrameNestXClaimError(INVALID_X_CLAIM_MESSAGE)


_HANDLE_PATTERN = re.compile(r"[A-Za-z0-9_]{1,64}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")