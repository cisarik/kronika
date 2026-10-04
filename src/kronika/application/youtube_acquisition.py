"""Application service and lifecycle coordinator for YouTube manual ingestion."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
import json
import time

from kronika.application.in_process_lifecycle import (
    IterationFailureLimiter,
    ShutdownDeadline,
    attach_unexpected_runner_observer,
    wait_for_deadline,
)
from kronika.application.ports.upload_publications import (
    UploadPublicationCandidate,
    UploadPublicationRepository,
)
from kronika.application.ports.upload_sessions import UploadSessionRepository
from kronika.application.ports.youtube_acquisition_claims import (
    FrameNestYouTubeClaimRepositoryError,
    YouTubeAcquisitionClaimRepository,
)
from kronika.application.ports.youtube_downloader import (
    YouTubeDownloadError,
    YouTubeDownloader,
    YouTubeDownloaderConfigurationError,
    YouTubeDownloadPlan,
    YouTubeInspection,
    YouTubeInspectionError,
)
from kronika.application.ports.youtube_staging import (
    YouTubeStagingError,
    YouTubeStagingStorage,
)
from kronika.application.upload_catalog import CatalogUploadClassification
from kronika.application.upload_transport import (
    UploadDuplicateResolution,
    UploadTransportError,
    UploadTransportService,
)
from kronika.domain.identities import (
    MediaId,
    MediaLocationId,
    YouTubeAcquisitionClaimId,
)
from kronika.domain.media_classification import (
    AcquisitionSource,
    ContentCategory,
    CreatorAttributionKind,
)
from kronika.domain.media_metadata import (
    MAX_DISPLAY_TITLE_CODE_POINTS,
    FrameNestMediaMetadataError,
    MediaDisplayTitle,
)
from kronika.domain.uploads import (
    UploadDuplicateResolutionMode,
    UploadSession,
    UploadSessionId,
    UploadSessionState,
    UploadStorageKey,
)
from kronika.domain.youtube_acquisition import (
    ACTIVE_YOUTUBE_ACQUISITION_STATES,
    LIVE_CATALOG_YOUTUBE_ACQUISITION_STATES,
    REQUESTER_PHASE_COMPLETED,
    REQUESTER_PHASE_COMPLETED_PRIVATE,
    REQUESTER_PHASE_UNAVAILABLE,
    TERMINAL_YOUTUBE_ACQUISITION_STATES,
    FrameNestYouTubeAcquisitionError,
    YouTubeAcquisitionClaim,
    YouTubeAcquisitionState,
    YouTubeConfirmationMethod,
    YouTubeFailureStage,
    YouTubeStagingCleanupState,
    canonicalize_youtube_url,
    derive_requester_phase,
)
from kronika.structured_logging import get_logger

DEFAULT_FINAL_MEDIA_BYTES = 1_073_741_824
MS_PER_HOUR = 3_600_000
MS_PER_DAY = 86_400_000

DEFAULT_ACQUISITION_POLL_INTERVAL_SECONDS = 0.25
DEFAULT_ACQUISITION_BATCH_SIZE = 32
LOGGER = get_logger("youtube_acquisition")


class YouTubeAcquisitionError(RuntimeError):
    """Sanitized base application failure."""


class YouTubeAcquisitionNotFoundError(YouTubeAcquisitionError):
    """Requested durable claim does not exist."""


class YouTubeAcquisitionStateConflictError(YouTubeAcquisitionError):
    """Requested operation is incompatible with current claim state."""


class YouTubeAcquisitionInfrastructureError(YouTubeAcquisitionError):
    """Required durable or filesystem infrastructure is unavailable."""


class YouTubeAcquisitionInvalidRequestError(YouTubeAcquisitionError):
    """Submitted URL or confirmation data is outside the accepted policy."""


class YouTubeAcquisitionInvalidCursorError(YouTubeAcquisitionInvalidRequestError):
    """Owned-request list cursor is malformed or outside the accepted policy."""


class YouTubeRequestLimitError(YouTubeAcquisitionError):
    """Ordinary requester admission limit was reached."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class YouTubeRequestInsufficientStorageError(YouTubeAcquisitionError):
    """Ordinary requester admission failed the free-space gate."""


@dataclass(frozen=True, slots=True)
class YouTubeClaimSnapshot:
    """Operator-safe durable claim projection."""

    id: str
    state: str
    acquisition_source: str
    youtube_video_id: str
    upload_id: str | None
    upload_state: str | None
    media_id: str | None
    media_location_id: str | None
    result: str | None
    downloaded_size_bytes: int | None
    failure_stage: str | None
    failure_code: str | None
    cleanup_state: str
    retry_of_claim_id: str | None
    resolved_claim_id: str | None
    created_at_ms: int
    updated_at_ms: int
    completed_at_ms: int | None
    version: int
    requester_login_key: str | None = None
    submitted_url: str | None = None
    canonical_url: str | None = None


@dataclass(frozen=True, slots=True)
class YouTubeRequestSnapshot:
    """Ordinary requester-safe request projection."""

    request_id: str
    phase: str
    submitted_url: str
    canonical_url: str
    media_id: str | None
    failure_category: str | None
    failure_code: str | None
    retry_of_request_id: str | None
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class YouTubeClaimSubmission:
    """Submission result distinguishing a newly persisted claim from reuse."""

    snapshot: YouTubeClaimSnapshot
    created: bool


@dataclass(frozen=True, slots=True)
class YouTubeRequestSubmission:
    """Ordinary request submission result."""

    snapshot: YouTubeRequestSnapshot
    created: bool


@dataclass(frozen=True, slots=True)
class YouTubeRequestPage:
    """Owned request list page."""

    items: tuple[YouTubeRequestSnapshot, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class YouTubeRequestLimits:
    """Ordinary admission controls."""

    max_active_per_user: int = 1
    max_global_active: int = 8
    max_submits_per_hour: int = 6
    max_failed_per_24h: int = 10
    max_private_items: int = 20
    max_private_bytes: int = 10_737_418_240
    min_free_space_reserve_bytes: int = 67_108_864
    max_final_media_bytes: int = DEFAULT_FINAL_MEDIA_BYTES
    free_space_bytes: Callable[[], int] | None = None



def default_now_ms() -> int:
    return int(time.time() * 1_000)


class YouTubeAcquisitionService:
    """Create, read, and explicitly retry durable owner-confirmed claims."""

    def __init__(
        self,
        repository: YouTubeAcquisitionClaimRepository,
        upload_repository: UploadSessionRepository,
        staging: YouTubeStagingStorage,
        *,
        now_ms: Callable[[], int] = default_now_ms,
        notifier: object | None = None,
    ) -> None:
        self._repository = repository
        self._upload_repository = upload_repository
        self._staging = staging
        self._now_ms = now_ms
        self._notifier = notifier

    def submit(
        self,
        *,
        submitted_url: str,
        confirmation_method: YouTubeConfirmationMethod,
        created_by_login_key: str | None = None,
    ) -> YouTubeClaimSubmission:
        now_ms = self._now_ms()
        try:
            claim = YouTubeAcquisitionClaim.new(
                submitted_url=submitted_url,
                confirmation_method=confirmation_method,
                now_ms=now_ms,
                created_by_login_key=created_by_login_key,
            )
        except FrameNestYouTubeAcquisitionError as exc:
            raise YouTubeAcquisitionInvalidRequestError(
                "Invalid public YouTube video URL."
            ) from exc
        try:
            existing = self._repository.find_latest_cataloged_by_source_identity(
                extractor_key=claim.extractor_key,
                youtube_video_id=claim.youtube_video_id,
            )
            if (
                existing is not None
                and existing.media_id is not None
                and existing.media_location_id is not None
            ):
                reused = claim.advance(
                    YouTubeAcquisitionState.DUPLICATE_RESOLVED,
                    updated_at_ms=now_ms,
                    resolved_claim_id=existing.id,
                    media_id=existing.media_id,
                    media_location_id=existing.media_location_id,
                    completed_at_ms=now_ms,
                    cleanup_state=YouTubeStagingCleanupState.COMPLETE,
                    cleanup_completed_at_ms=now_ms,
                )
                self._repository.create(reused)
                return YouTubeClaimSubmission(
                    snapshot=self._snapshot(reused),
                    created=True,
                )
            selected, created = self._repository.create_or_get_active(claim)
        except (
            FrameNestYouTubeClaimRepositoryError,
        ) as exc:
            raise YouTubeAcquisitionInfrastructureError(
                "YouTube acquisition is unavailable."
            ) from exc
        if created:
            _notify(self._notifier)
        return YouTubeClaimSubmission(
            snapshot=self._snapshot(selected),
            created=created,
        )

    def get(
        self,
        claim_id: YouTubeAcquisitionClaimId,
        *,
        created_by_login_key: str | None = None,
    ) -> YouTubeClaimSnapshot:
        del created_by_login_key
        try:
            claim = self._repository.get(claim_id)
        except FrameNestYouTubeClaimRepositoryError as exc:
            raise YouTubeAcquisitionInfrastructureError(
                "YouTube acquisition is unavailable."
            ) from exc
        if claim is None:
            raise YouTubeAcquisitionNotFoundError(
                "YouTube acquisition claim not found."
            )
        return self._snapshot(claim)

    def retry(
        self,
        claim_id: YouTubeAcquisitionClaimId,
        *,
        confirmation_method: YouTubeConfirmationMethod,
        created_by_login_key: str | None = None,
    ) -> YouTubeClaimSubmission:
        try:
            original = self._repository.get(claim_id)
            if original is None:
                raise YouTubeAcquisitionNotFoundError(
                    "YouTube acquisition claim not found."
                )
            if original.state is not YouTubeAcquisitionState.FAILED:
                raise YouTubeAcquisitionStateConflictError(
                    "YouTube acquisition claim cannot be retried."
                )
            if original.cleanup_state is YouTubeStagingCleanupState.PENDING:
                self._staging.cleanup(original.staging_key)
                now_ms = self._now_ms()
                cleaned = original.evolve(
                    updated_at_ms=now_ms,
                    cleanup_state=YouTubeStagingCleanupState.COMPLETE,
                    cleanup_completed_at_ms=now_ms,
                )
                original = self._repository.save(
                    cleaned,
                    expected_state=original.state,
                    expected_version=original.version,
                )
            now_ms = self._now_ms()
            retry = YouTubeAcquisitionClaim.new(
                submitted_url=original.submitted_url,
                confirmation_method=confirmation_method,
                now_ms=now_ms,
                retry_of_claim_id=original.id,
                created_by_login_key=created_by_login_key or original.created_by_login_key,
            )
            selected, created = self._repository.create_or_get_active(retry)
        except (
            YouTubeAcquisitionNotFoundError,
            YouTubeAcquisitionStateConflictError,
        ):
            raise
        except (
            FrameNestYouTubeClaimRepositoryError,
            YouTubeStagingError,
        ) as exc:
            raise YouTubeAcquisitionInfrastructureError(
                "YouTube acquisition is unavailable."
            ) from exc
        if created:
            _notify(self._notifier)
        return YouTubeClaimSubmission(
            snapshot=self._snapshot(selected),
            created=created,
        )

    def _snapshot(
        self,
        claim: YouTubeAcquisitionClaim,
    ) -> YouTubeClaimSnapshot:
        upload_state = None
        if claim.upload_id is not None:
            try:
                upload = self._upload_repository.get(claim.upload_id)
            except Exception as exc:
                raise YouTubeAcquisitionInfrastructureError(
                    "YouTube acquisition is unavailable."
                ) from exc
            upload_state = None if upload is None else upload.state.value
        result = None
        if claim.state is YouTubeAcquisitionState.CATALOGED:
            result = "new"
        elif claim.state is YouTubeAcquisitionState.DUPLICATE_RESOLVED:
            result = "reused"
        return YouTubeClaimSnapshot(
            id=claim.id.to_string(),
            state=claim.state.value,
            acquisition_source=claim.acquisition_source.value,
            youtube_video_id=claim.youtube_video_id,
            upload_id=_identity_text(claim.upload_id),
            upload_state=upload_state,
            media_id=_identity_text(claim.media_id),
            media_location_id=_identity_text(claim.media_location_id),
            result=result,
            downloaded_size_bytes=claim.downloaded_size_bytes,
            failure_stage=None
            if claim.failure_stage is None
            else claim.failure_stage.value,
            failure_code=claim.failure_code,
            cleanup_state=claim.cleanup_state.value,
            retry_of_claim_id=_identity_text(claim.retry_of_claim_id),
            resolved_claim_id=_identity_text(claim.resolved_claim_id),
            created_at_ms=claim.created_at_ms,
            updated_at_ms=claim.updated_at_ms,
            completed_at_ms=claim.completed_at_ms,
            version=claim.version,
            requester_login_key=claim.created_by_login_key,
            submitted_url=claim.submitted_url,
            canonical_url=claim.canonical_url,
        )


def _bound_record_owner(publication_repository: object, media_id: object) -> str | None:
    probe = getattr(publication_repository, "bound_record_owner", None)
    if probe is None:
        return None
    owner = probe(media_id)
    if owner is None:
        return None
    return str(owner)


class YouTubeRequestService:
    """Ordinary requester-owned YouTube request admission and inspection."""

    def __init__(
        self,
        repository: YouTubeAcquisitionClaimRepository,
        publication_repository: object,
        staging: YouTubeStagingStorage,
        *,
        limits: YouTubeRequestLimits,
        now_ms: Callable[[], int] = default_now_ms,
        notifier: object | None = None,
    ) -> None:
        self._repository = repository
        self._publication_repository = publication_repository
        self._staging = staging
        self._limits = limits
        self._now_ms = now_ms
        self._notifier = notifier

    def submit(
        self,
        *,
        submitted_url: str,
        confirmation_method: YouTubeConfirmationMethod,
        created_by_login_key: str,
    ) -> YouTubeRequestSubmission:
        if not created_by_login_key:
            raise YouTubeAcquisitionInvalidRequestError(
                "Invalid public YouTube video URL."
            )
        now_ms = self._now_ms()
        try:
            claim = YouTubeAcquisitionClaim.new(
                submitted_url=submitted_url,
                confirmation_method=confirmation_method,
                now_ms=now_ms,
                created_by_login_key=created_by_login_key,
            )
        except FrameNestYouTubeAcquisitionError as exc:
            raise YouTubeAcquisitionInvalidRequestError(
                "Invalid public YouTube video URL."
            ) from exc
        try:
            own_success = self._repository.find_owned_live_successful_by_source_identity(
                extractor_key=claim.extractor_key,
                youtube_video_id=claim.youtube_video_id,
                created_by_login_key=created_by_login_key,
            )
            if own_success is not None:
                return YouTubeRequestSubmission(
                    snapshot=self._request_snapshot(own_success),
                    created=False,
                )
            own_active = self._repository.find_active_by_source_identity(
                extractor_key=claim.extractor_key,
                youtube_video_id=claim.youtube_video_id,
                created_by_login_key=created_by_login_key,
            )
            if own_active is not None:
                return YouTubeRequestSubmission(
                    snapshot=self._request_snapshot(own_active),
                    created=False,
                )
            published = self._repository.find_published_successful_by_source_identity(
                extractor_key=claim.extractor_key,
                youtube_video_id=claim.youtube_video_id,
            )
            if (
                published is not None
                and published.media_id is not None
                and published.media_location_id is not None
            ):
                self._enforce_admission_limits(
                    created_by_login_key=created_by_login_key,
                    now_ms=now_ms,
                    require_private_quota=False,
                )
                reused = claim.advance(
                    YouTubeAcquisitionState.DUPLICATE_RESOLVED,
                    updated_at_ms=now_ms,
                    resolved_claim_id=published.id,
                    media_id=published.media_id,
                    media_location_id=published.media_location_id,
                    completed_at_ms=now_ms,
                    cleanup_state=YouTubeStagingCleanupState.COMPLETE,
                    cleanup_completed_at_ms=now_ms,
                )
                self._repository.create(reused)
                return YouTubeRequestSubmission(
                    snapshot=self._request_snapshot(reused),
                    created=True,
                )
            self._enforce_admission_limits(
                created_by_login_key=created_by_login_key,
                now_ms=now_ms,
                require_private_quota=True,
            )
            selected, created = self._repository.create_or_get_active(claim)
        except YouTubeRequestLimitError:
            raise
        except YouTubeRequestInsufficientStorageError:
            raise
        except FrameNestYouTubeClaimRepositoryError as exc:
            raise YouTubeAcquisitionInfrastructureError(
                "YouTube acquisition is unavailable."
            ) from exc
        if created:
            _notify(self._notifier)
        return YouTubeRequestSubmission(
            snapshot=self._request_snapshot(selected),
            created=created,
        )

    def list_owned(
        self,
        *,
        created_by_login_key: str,
        limit: int = 20,
        cursor: str | None = None,
    ) -> YouTubeRequestPage:
        after_created_at_ms: int | None = None
        after_id: str | None = None
        if cursor is not None:
            after_created_at_ms, after_id = _decode_owned_cursor(cursor)
        try:
            page = self._repository.list_owned(
                created_by_login_key=created_by_login_key,
                limit=limit + 1,
                after_created_at_ms=after_created_at_ms,
                after_id=after_id,
            )
        except FrameNestYouTubeClaimRepositoryError as exc:
            raise YouTubeAcquisitionInfrastructureError(
                "YouTube acquisition is unavailable."
            ) from exc
        items = page[:limit]
        next_cursor = None
        if len(page) > limit:
            last = items[-1]
            next_cursor = _encode_owned_cursor(last.created_at_ms, last.id.to_string())
        return YouTubeRequestPage(
            items=tuple(self._request_snapshot(item) for item in items),
            next_cursor=next_cursor,
        )

    def get_owned(
        self,
        claim_id: YouTubeAcquisitionClaimId,
        *,
        created_by_login_key: str,
    ) -> YouTubeRequestSnapshot:
        try:
            claim = self._repository.get_owned(
                claim_id,
                created_by_login_key=created_by_login_key,
            )
        except FrameNestYouTubeClaimRepositoryError as exc:
            raise YouTubeAcquisitionInfrastructureError(
                "YouTube acquisition is unavailable."
            ) from exc
        if claim is None:
            raise YouTubeAcquisitionNotFoundError(
                "YouTube acquisition request not found."
            )
        return self._request_snapshot(claim)

    def retry(
        self,
        claim_id: YouTubeAcquisitionClaimId,
        *,
        confirmation_method: YouTubeConfirmationMethod,
        created_by_login_key: str,
    ) -> YouTubeRequestSubmission:
        try:
            original = self._repository.get_owned(
                claim_id,
                created_by_login_key=created_by_login_key,
            )
            if original is None:
                raise YouTubeAcquisitionNotFoundError(
                    "YouTube acquisition request not found."
                )
            if original.state is not YouTubeAcquisitionState.FAILED:
                raise YouTubeAcquisitionStateConflictError(
                    "YouTube acquisition request cannot be retried."
                )
            if original.cleanup_state is YouTubeStagingCleanupState.PENDING:
                self._staging.cleanup(original.staging_key)
                now_ms = self._now_ms()
                cleaned = original.evolve(
                    updated_at_ms=now_ms,
                    cleanup_state=YouTubeStagingCleanupState.COMPLETE,
                    cleanup_completed_at_ms=now_ms,
                )
                original = self._repository.save(
                    cleaned,
                    expected_state=original.state,
                    expected_version=original.version,
                )
            now_ms = self._now_ms()
            self._enforce_admission_limits(
                created_by_login_key=created_by_login_key,
                now_ms=now_ms,
                require_private_quota=True,
            )
            retry = YouTubeAcquisitionClaim.new(
                submitted_url=original.submitted_url,
                confirmation_method=confirmation_method,
                now_ms=now_ms,
                retry_of_claim_id=original.id,
                created_by_login_key=created_by_login_key,
            )
            selected, created = self._repository.create_or_get_active(retry)
        except (
            YouTubeAcquisitionNotFoundError,
            YouTubeAcquisitionStateConflictError,
            YouTubeRequestLimitError,
            YouTubeRequestInsufficientStorageError,
        ):
            raise
        except (
            FrameNestYouTubeClaimRepositoryError,
            YouTubeStagingError,
            FrameNestYouTubeAcquisitionError,
        ) as exc:
            raise YouTubeAcquisitionInfrastructureError(
                "YouTube acquisition is unavailable."
            ) from exc
        if created:
            _notify(self._notifier)
        return YouTubeRequestSubmission(
            snapshot=self._request_snapshot(selected),
            created=created,
        )

    def _enforce_admission_limits(
        self,
        *,
        created_by_login_key: str,
        now_ms: int,
        require_private_quota: bool,
    ) -> None:
        if (
            self._repository.count_active_for_requester(
                created_by_login_key=created_by_login_key
            )
            >= self._limits.max_active_per_user
        ):
            raise YouTubeRequestLimitError(
                "YOUTUBE_REQUEST_ACTIVE_LIMIT",
                "Active YouTube request limit reached.",
            )
        if (
            self._repository.count_global_active_ordinary()
            >= self._limits.max_global_active
        ):
            raise YouTubeRequestLimitError(
                "YOUTUBE_REQUEST_GLOBAL_QUEUE_FULL",
                "The YouTube request queue is full.",
            )
        if (
            self._repository.count_submits_since(
                created_by_login_key=created_by_login_key,
                since_ms=now_ms - MS_PER_HOUR,
            )
            >= self._limits.max_submits_per_hour
        ):
            raise YouTubeRequestLimitError(
                "YOUTUBE_REQUEST_RATE_LIMIT",
                "YouTube request rate limit reached.",
            )
        if (
            self._repository.count_failed_transitions_since(
                created_by_login_key=created_by_login_key,
                since_ms=now_ms - MS_PER_DAY,
            )
            >= self._limits.max_failed_per_24h
        ):
            raise YouTubeRequestLimitError(
                "YOUTUBE_REQUEST_FAILED_24H_LIMIT",
                "YouTube failed-request limit reached for the previous 24 hours.",
            )
        if require_private_quota:
            items, bytes_used = self._repository.private_successful_quota(
                created_by_login_key=created_by_login_key
            )
            if (
                items >= self._limits.max_private_items
                or bytes_used >= self._limits.max_private_bytes
            ):
                raise YouTubeRequestLimitError(
                    "YOUTUBE_REQUEST_PRIVATE_QUOTA",
                    "Private YouTube download quota reached.",
                )
        if self._limits.free_space_bytes is not None:
            required = (
                self._limits.min_free_space_reserve_bytes
                + self._limits.max_final_media_bytes
            )
            try:
                available = self._limits.free_space_bytes()
            except Exception as exc:
                raise YouTubeRequestInsufficientStorageError(
                    "Insufficient storage for YouTube request."
                ) from exc
            if available < required:
                raise YouTubeRequestInsufficientStorageError(
                    "Insufficient storage for YouTube request."
                )

    def _request_snapshot(
        self,
        claim: YouTubeAcquisitionClaim,
    ) -> YouTubeRequestSnapshot:
        media_is_published: bool | None = None
        media_id_text: str | None = None
        if (
            claim.state in LIVE_CATALOG_YOUTUBE_ACQUISITION_STATES
            and claim.media_id is not None
        ):
            try:
                media_is_published = bool(
                    self._publication_repository.is_published(claim.media_id)
                )
            except Exception as exc:
                raise YouTubeAcquisitionInfrastructureError(
                    "YouTube acquisition is unavailable."
                ) from exc
            phase = derive_requester_phase(
                claim,
                media_is_published=media_is_published,
            )
            bound_owner = _bound_record_owner(
                self._publication_repository, claim.media_id
            )
            requester = claim.created_by_login_key or ""
            if bound_owner is not None and bound_owner != requester:
                phase = REQUESTER_PHASE_UNAVAILABLE
                media_id_text = None
            elif phase in {
                REQUESTER_PHASE_COMPLETED,
                REQUESTER_PHASE_COMPLETED_PRIVATE,
            }:
                media_id_text = claim.media_id.to_string()
        else:
            phase = derive_requester_phase(claim, media_is_published=None)
        retry_of = None
        if claim.retry_of_claim_id is not None:
            parent = self._repository.get_owned(
                claim.retry_of_claim_id,
                created_by_login_key=claim.created_by_login_key or "",
            )
            if parent is not None:
                retry_of = claim.retry_of_claim_id.to_string()
        return YouTubeRequestSnapshot(
            request_id=claim.id.to_string(),
            phase=phase,
            submitted_url=claim.submitted_url,
            canonical_url=claim.canonical_url,
            media_id=media_id_text,
            failure_category=None
            if claim.failure_stage is None
            else claim.failure_stage.value,
            failure_code=claim.failure_code,
            retry_of_request_id=retry_of,
            created_at_ms=claim.created_at_ms,
            updated_at_ms=claim.updated_at_ms,
        )


def handoff_duplicate_resolution(
    created_by_login_key: str | None,
    *,
    creator_manages_duplicates: Callable[[str], bool] | None = None,
) -> tuple[str | None, UploadDuplicateResolutionMode]:
    """Choose upload duplicate mode for one acquisition handoff.

    A missing creator stays explicit, matching legacy ownerless operator
    claims. A verified login is explicit only when the injected predicate
    says that login may manage duplicates; otherwise it stays silent.
    """
    manages = False
    if created_by_login_key is not None and creator_manages_duplicates is not None:
        manages = bool(creator_manages_duplicates(created_by_login_key))
    if created_by_login_key is None or manages:
        return created_by_login_key, UploadDuplicateResolutionMode.EXPLICIT
    return (
        created_by_login_key,
        UploadDuplicateResolutionMode.SILENT_KEEP_SEPARATE,
    )


class YouTubeAcquisitionCoordinator:
    """Sequential durable acquisition owner for the single-worker server."""

    def __init__(
        self,
        repository: YouTubeAcquisitionClaimRepository,
        downloader: YouTubeDownloader,
        staging: YouTubeStagingStorage,
        transport: UploadTransportService,
        upload_repository: UploadSessionRepository,
        publication_repository: UploadPublicationRepository,
        *,
        validation_coordinator: object | None,
        publication_coordinator: object | None,
        chunk_size_bytes: int,
        now_ms: Callable[[], int] = default_now_ms,
        poll_interval_seconds: float = DEFAULT_ACQUISITION_POLL_INTERVAL_SECONDS,
        batch_size: int = DEFAULT_ACQUISITION_BATCH_SIZE,
        creator_manages_duplicates: Callable[[str], bool] | None = None,
    ) -> None:
        if (
            isinstance(chunk_size_bytes, bool)
            or not isinstance(chunk_size_bytes, int)
            or chunk_size_bytes <= 0
        ):
            raise ValueError("YouTube handoff chunk size must be positive")
        if (
            isinstance(poll_interval_seconds, bool)
            or poll_interval_seconds < 0
        ):
            raise ValueError("YouTube acquisition poll interval is invalid")
        if isinstance(batch_size, bool) or batch_size <= 0:
            raise ValueError("YouTube acquisition batch size must be positive")
        self._repository = repository
        self._downloader = downloader
        self._staging = staging
        self._transport = transport
        self._upload_repository = upload_repository
        self._publication_repository = publication_repository
        self._validation_coordinator = validation_coordinator
        self._publication_coordinator = publication_coordinator
        self._chunk_size_bytes = chunk_size_bytes
        self._now_ms = now_ms
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._batch_size = batch_size
        self._creator_manages_duplicates = creator_manages_duplicates
        self._runner: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._stopping = False
        self._startup_reconciled = False
        self._iteration_failures = IterationFailureLimiter()

    async def start(self) -> None:
        if self._runner is not None:
            if self._runner.done() and not self._stopping:
                raise RuntimeError("YouTube acquisition runner is not active")
            return
        self._stopping = False
        self._wake = asyncio.Event()
        await self._reconcile_startup()
        self._runner = asyncio.create_task(
            self._run(),
            name="framenest-youtube-acquisition-coordinator",
        )
        attach_unexpected_runner_observer(
            self._runner,
            is_expected=lambda: self._stopping,
            log_unexpected=_log_unexpected_runner_death,
        )
        self.notify()

    def notify(self) -> None:
        if self._runner is not None and self._runner.done() and not self._stopping:
            raise RuntimeError("YouTube acquisition runner is not active")
        if self._wake is not None and not self._stopping:
            self._wake.set()

    async def drain(self) -> None:
        if not self._startup_reconciled:
            await self._reconcile_startup()
        await self._drain_once()

    async def shutdown(
        self,
        deadline: ShutdownDeadline | None = None,
    ) -> None:
        self._stopping = True
        binder = getattr(self._downloader, "bind_shutdown_deadline", None)
        if callable(binder):
            binder(deadline)
        interrupt = getattr(self._downloader, "request_interrupt", None)
        if callable(interrupt):
            interrupt()
        if self._wake is not None:
            self._wake.set()
        runner = self._runner
        if runner is not None and not runner.done():
            runner.cancel()
        try:
            if runner is not None:
                try:
                    await wait_for_deadline(runner, deadline)
                except TimeoutError:
                    _safe_log(
                        level="WARNING",
                        event="youtube_acquisition_shutdown_unresolved",
                        operation="youtube_acquisition_shutdown",
                        error_code="YOUTUBE_ACQUISITION_SHUTDOWN_UNRESOLVED",
                        retryable=False,
                    )
                except asyncio.CancelledError:
                    pass
                except Exception:
                    _safe_log(
                        level="WARNING",
                        event="youtube_acquisition_runner_shutdown_fault",
                        operation="youtube_acquisition_shutdown",
                        error_code="YOUTUBE_ACQUISITION_RUNNER_SHUTDOWN_FAULT",
                        retryable=False,
                    )
        finally:
            self._runner = None
            self._wake = None

    @property
    def runner_done(self) -> bool:
        return self._runner is None or self._runner.done()

    async def _run(self) -> None:
        assert self._wake is not None
        while not self._stopping:
            try:
                progressed = await self._drain_once()
            except asyncio.CancelledError:
                if self._stopping:
                    return
                raise
            except Exception:
                progressed = False
                if self._iteration_failures.allow():
                    _safe_log(
                        level="ERROR",
                        event="youtube_acquisition_runner_iteration_failed",
                        operation="youtube_acquisition_run",
                        error_code="YOUTUBE_ACQUISITION_RUNNER_ITERATION_FAILED",
                        retryable=True,
                    )
            if self._stopping:
                return
            if progressed:
                # Durable steps may complete synchronously with a fake adapter or
                # local filesystem I/O. Yield so API requests and downstream
                # lifecycle coordinators cannot be starved by the recovery loop.
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                pass
            finally:
                self._wake.clear()

    async def _reconcile_startup(self) -> None:
        if self._startup_reconciled:
            return
        candidates: list[YouTubeAcquisitionClaim] = []
        after_updated_at_ms: int | None = None
        after_id: str | None = None
        while True:
            page = self._repository.list_recovery_candidates(
                limit=self._batch_size,
                after_updated_at_ms=after_updated_at_ms,
                after_id=after_id,
            )
            if not page:
                break
            candidates.extend(page)
            last = page[-1]
            after_updated_at_ms = last.updated_at_ms
            after_id = last.id.to_string()
        current_version: str | None = None
        for claim in candidates:
            if claim.state is YouTubeAcquisitionState.INSPECTING:
                recovered = claim.advance(
                    YouTubeAcquisitionState.CLAIMED,
                    updated_at_ms=self._now_ms(),
                )
                self._save(claim, recovered)
                continue
            if claim.state in {
                YouTubeAcquisitionState.DOWNLOAD_PENDING,
                YouTubeAcquisitionState.DOWNLOADING,
            }:
                if current_version is None:
                    try:
                        current_version = await self._downloader.attest_version()
                    except YouTubeDownloaderConfigurationError as exc:
                        await self._fail(
                            claim,
                            stage=YouTubeFailureStage.CONFIGURATION,
                            code=exc.code,
                        )
                        continue
                if claim.downloader_version != current_version:
                    try:
                        self._staging.cleanup(claim.staging_key)
                    except YouTubeStagingError:
                        await self._fail(
                            claim,
                            stage=YouTubeFailureStage.CLEANUP,
                            code="STAGING_CLEANUP_FAILED",
                        )
                        continue
                    restarted = claim.advance(
                        YouTubeAcquisitionState.CLAIMED,
                        updated_at_ms=self._now_ms(),
                        upstream_title=None,
                        upstream_channel=None,
                        upstream_channel_id=None,
                        upstream_source_date=None,
                        downloader_name=None,
                        downloader_version=None,
                        extractor_version=None,
                        selected_video_format_id=None,
                        selected_audio_format_id=None,
                        remote_filename=None,
                    )
                    self._save(claim, restarted)
                elif claim.state is YouTubeAcquisitionState.DOWNLOADING:
                    resumed = claim.advance(
                        YouTubeAcquisitionState.DOWNLOAD_PENDING,
                        updated_at_ms=self._now_ms(),
                    )
                    self._save(claim, resumed)
        self._startup_reconciled = True

    async def _drain_once(self) -> bool:
        cleanup_candidates = self._repository.list_cleanup_candidates(
            limit=self._batch_size
        )
        if cleanup_candidates:
            for claim in cleanup_candidates:
                self._cleanup(claim)
            return True
        candidates = self._repository.list_recovery_candidates(
            limit=self._batch_size
        )
        if not candidates:
            return False
        await self._process(candidates[0])
        return True

    async def _process(self, claim: YouTubeAcquisitionClaim) -> None:
        try:
            if claim.state is YouTubeAcquisitionState.CLAIMED:
                await self._inspect(claim)
            elif claim.state is YouTubeAcquisitionState.DOWNLOAD_PENDING:
                await self._download(claim)
            elif claim.state is YouTubeAcquisitionState.DOWNLOADED:
                handoff = claim.advance(
                    YouTubeAcquisitionState.HANDOFF,
                    updated_at_ms=self._now_ms(),
                )
                self._save(claim, handoff)
            elif claim.state is YouTubeAcquisitionState.HANDOFF:
                await self._handoff(claim)
            elif claim.state is YouTubeAcquisitionState.HANDED_OFF:
                await self._project_upload(claim)
            elif claim.state is YouTubeAcquisitionState.DOWNLOADING:
                resumed = claim.advance(
                    YouTubeAcquisitionState.DOWNLOAD_PENDING,
                    updated_at_ms=self._now_ms(),
                )
                self._save(claim, resumed)
            elif claim.state is YouTubeAcquisitionState.INSPECTING:
                recovered = claim.advance(
                    YouTubeAcquisitionState.CLAIMED,
                    updated_at_ms=self._now_ms(),
                )
                self._save(claim, recovered)
        except YouTubeDownloaderConfigurationError as exc:
            await self._fail(
                claim,
                stage=YouTubeFailureStage.CONFIGURATION,
                code=exc.code,
            )
        except YouTubeInspectionError as exc:
            await self._fail(
                claim,
                stage=YouTubeFailureStage.INSPECTION,
                code=exc.code,
            )
        except YouTubeDownloadError as exc:
            await self._fail(
                claim,
                stage=YouTubeFailureStage.DOWNLOAD,
                code=exc.code,
            )
        except UploadTransportError:
            await self._fail(
                claim,
                stage=YouTubeFailureStage.HANDOFF,
                code="UPLOAD_HANDOFF_FAILED",
            )
        except (
            FrameNestYouTubeClaimRepositoryError,
            YouTubeStagingError,
        ):
            return
        except Exception:
            await self._fail(
                claim,
                stage=YouTubeFailureStage.INTERNAL,
                code="INTERNAL_ERROR",
            )

    async def _inspect(self, claim: YouTubeAcquisitionClaim) -> None:
        inspecting = claim.advance(
            YouTubeAcquisitionState.INSPECTING,
            updated_at_ms=self._now_ms(),
        )
        inspecting = self._save(claim, inspecting)
        identity = canonicalize_youtube_url(inspecting.canonical_url)
        inspection = await self._downloader.inspect(identity)
        pending = inspecting.advance(
            YouTubeAcquisitionState.DOWNLOAD_PENDING,
            updated_at_ms=self._now_ms(),
            upstream_title=inspection.title,
            upstream_channel=inspection.channel,
            upstream_channel_id=inspection.channel_id,
            upstream_source_date=inspection.source_date,
            downloader_name="yt-dlp",
            downloader_version=inspection.downloader_version,
            extractor_version=inspection.extractor_version,
            selected_video_format_id=inspection.plan.video_format_id,
            selected_audio_format_id=inspection.plan.audio_format_id,
            remote_filename=inspection.remote_filename,
        )
        self._save(inspecting, pending)

    async def _download(self, claim: YouTubeAcquisitionClaim) -> None:
        identity = canonicalize_youtube_url(claim.canonical_url)
        if (
            claim.downloader_version is None
            or claim.extractor_version is None
            or claim.selected_video_format_id is None
        ):
            raise YouTubeDownloadError("INSPECTION_EVIDENCE_MISSING")
        inspection = YouTubeInspection(
            video_id=claim.youtube_video_id,
            extractor_key=claim.extractor_key,
            title=claim.upstream_title,
            channel=claim.upstream_channel,
            channel_id=claim.upstream_channel_id,
            source_date=claim.upstream_source_date,
            remote_filename=claim.remote_filename,
            duration_seconds=1.0,
            downloader_version=claim.downloader_version,
            extractor_version=claim.extractor_version,
            plan=YouTubeDownloadPlan(
                video_format_id=claim.selected_video_format_id,
                audio_format_id=claim.selected_audio_format_id,
                expected_size_bytes=None,
                has_source_audio=claim.selected_audio_format_id is not None,
                split_streams=claim.selected_audio_format_id is not None,
            ),
        )
        downloading = claim.advance(
            YouTubeAcquisitionState.DOWNLOADING,
            updated_at_ms=self._now_ms(),
        )
        downloading = self._save(claim, downloading)
        result = await self._downloader.download(
            identity,
            inspection,
            staging_key=downloading.staging_key,
        )
        downloaded = downloading.advance(
            YouTubeAcquisitionState.DOWNLOADED,
            updated_at_ms=self._now_ms(),
            downloaded_size_bytes=result.size_bytes,
            downloaded_at_ms=self._now_ms(),
        )
        self._save(downloading, downloaded)

    async def _handoff(self, claim: YouTubeAcquisitionClaim) -> None:
        if claim.downloaded_size_bytes is None:
            raise UploadTransportError("upload handoff failed")
        upload_id = UploadSessionId.from_string(claim.id.to_string())
        upload_storage_key = UploadStorageKey(claim.staging_key)
        created_by_login_key, duplicate_resolution_mode = (
            handoff_duplicate_resolution(
                claim.created_by_login_key,
                creator_manages_duplicates=self._creator_manages_duplicates,
            )
        )
        snapshot = self._transport.create_session(
            display_filename=claim.generated_filename,
            declared_size_bytes=claim.downloaded_size_bytes,
            session_id=upload_id,
            storage_key=upload_storage_key,
            created_by_login_key=created_by_login_key,
            duplicate_resolution_mode=duplicate_resolution_mode,
        )
        current = claim
        if current.upload_id is None:
            linked = current.evolve(
                updated_at_ms=self._now_ms(),
                upload_id=upload_id,
            )
            current = self._save(current, linked)
        reader = self._staging.open_artifact(
            claim.staging_key,
            expected_size_bytes=claim.downloaded_size_bytes,
        )
        try:
            while snapshot.received_size_bytes < claim.downloaded_size_bytes:
                reader.verify_still_consistent()
                reader.seek(snapshot.received_size_bytes)
                chunk = reader.read(
                    min(
                        self._chunk_size_bytes,
                        claim.downloaded_size_bytes
                        - snapshot.received_size_bytes,
                    )
                )
                if not chunk:
                    raise UploadTransportError("upload handoff failed")
                snapshot = await self._transport.receive_chunk(
                    upload_id,
                    upload_offset=snapshot.received_size_bytes,
                    content_length=len(chunk),
                    body=_one_chunk(chunk),
                )
            reader.verify_still_consistent()
        finally:
            reader.close()
        if snapshot.state in {
            UploadSessionState.CREATED.value,
            UploadSessionState.RECEIVING.value,
        }:
            snapshot = await self._transport.complete(upload_id)
        if (
            snapshot.received_size_bytes != claim.downloaded_size_bytes
            or snapshot.state
            not in {state.value for state in UploadSessionState}
            - {
                UploadSessionState.CREATED.value,
                UploadSessionState.RECEIVING.value,
            }
        ):
            raise UploadTransportError("upload handoff failed")
        _notify(self._validation_coordinator)
        handed_off = current.advance(
            YouTubeAcquisitionState.HANDED_OFF,
            updated_at_ms=self._now_ms(),
        )
        handed_off = self._save(current, handed_off)
        self._cleanup(handed_off)

    async def _project_upload(self, claim: YouTubeAcquisitionClaim) -> None:
        if claim.cleanup_state is YouTubeStagingCleanupState.PENDING:
            claim = self._cleanup(claim)
        if claim.upload_id is None:
            raise UploadTransportError("upload handoff failed")
        upload = self._upload_repository.get(claim.upload_id)
        if upload is None:
            raise UploadTransportError("upload handoff failed")
        if upload.state in {
            UploadSessionState.RECEIVED,
            UploadSessionState.VALIDATING,
        }:
            _notify(self._validation_coordinator)
            return
        if upload.state in {
            UploadSessionState.PUBLISH_PENDING,
            UploadSessionState.PUBLISHED,
        }:
            _notify(self._publication_coordinator)
            return
        if upload.state is UploadSessionState.CATALOGED:
            candidate = self._publication_repository.get_candidate(upload.id)
            self._complete_cataloged(claim, candidate)
            return
        if upload.state in {
            UploadSessionState.DUPLICATE_PENDING,
            UploadSessionState.CANCELLED,
        }:
            if await self._resolve_byte_duplicate(claim, upload):
                return
            if upload.state is UploadSessionState.DUPLICATE_PENDING:
                await self._fail(
                    claim,
                    stage=YouTubeFailureStage.DOWNSTREAM,
                    code="BYTE_DUPLICATE_UNRESOLVED",
                )
                return
        failure_code = {
            UploadSessionState.REJECTED: "UPLOAD_REJECTED",
            UploadSessionState.FAILED: "UPLOAD_FAILED",
            UploadSessionState.EXPIRED: "UPLOAD_EXPIRED",
            UploadSessionState.CANCELLED: "UPLOAD_CANCELLED",
        }.get(upload.state, "UPLOAD_STATE_INCONSISTENT")
        await self._fail(
            claim,
            stage=YouTubeFailureStage.DOWNSTREAM,
            code=failure_code,
        )

    async def _resolve_byte_duplicate(
        self,
        claim: YouTubeAcquisitionClaim,
        upload: UploadSession,
    ) -> bool:
        if upload.byte_identity_id is None:
            return False
        canonical = (
            self._publication_repository.find_cataloged_by_byte_identity(
                upload.byte_identity_id,
                exclude_upload_id=upload.id,
            )
        )
        if (
            canonical is None
            or canonical.publication is None
            or canonical.publication.media_id is None
            or canonical.publication.media_location_id is None
        ):
            return False
        if upload.state is UploadSessionState.DUPLICATE_PENDING:
            if self._ordinary_creator(claim.created_by_login_key):
                await self._transport.resolve_duplicate(
                    upload.id,
                    UploadDuplicateResolution.KEEP_SEPARATE,
                )
                _notify(self._publication_coordinator)
                return True
            await self._transport.resolve_duplicate(
                upload.id,
                UploadDuplicateResolution.DISCARD,
            )
        if self._ordinary_creator(claim.created_by_login_key):
            return False
        resolved_claim = self._repository.find_by_upload_id(
            canonical.upload.id
        )
        completed = claim.advance(
            YouTubeAcquisitionState.DUPLICATE_RESOLVED,
            updated_at_ms=self._now_ms(),
            resolved_claim_id=None
            if resolved_claim is None
            else resolved_claim.id,
            media_id=canonical.publication.media_id,
            media_location_id=canonical.publication.media_location_id,
            completed_at_ms=self._now_ms(),
        )
        self._save(claim, completed)
        return True

    def _ordinary_creator(self, login: str | None) -> bool:
        """Ordinary verified logins keep silent separation.

        A missing creator and a creator with duplicate-management authority
        follow the explicit reuse path. An unwired predicate keeps every
        present login ordinary, which is the previous handoff behavior.
        """
        if login is None:
            return False
        if self._creator_manages_duplicates is None:
            return True
        return not bool(self._creator_manages_duplicates(login))

    def _complete_cataloged(
        self,
        claim: YouTubeAcquisitionClaim,
        candidate: UploadPublicationCandidate | None,
    ) -> None:
        if (
            candidate is None
            or candidate.publication is None
            or candidate.publication.media_id is None
            or candidate.publication.media_location_id is None
        ):
            raise YouTubeAcquisitionInfrastructureError(
                "YouTube acquisition is unavailable."
            )
        completed = claim.advance(
            YouTubeAcquisitionState.CATALOGED,
            updated_at_ms=self._now_ms(),
            media_id=candidate.publication.media_id,
            media_location_id=candidate.publication.media_location_id,
            completed_at_ms=self._now_ms(),
        )
        self._save(claim, completed)

    async def _fail(
        self,
        observed: YouTubeAcquisitionClaim,
        *,
        stage: YouTubeFailureStage,
        code: str,
    ) -> None:
        try:
            current = self._repository.get(observed.id)
            if current is None or current.state in TERMINAL_YOUTUBE_ACQUISITION_STATES:
                return
            failed = current.advance(
                YouTubeAcquisitionState.FAILED,
                updated_at_ms=self._now_ms(),
                failure_stage=stage,
                failure_code=code,
                completed_at_ms=self._now_ms(),
            )
            failed = self._save(current, failed)
            try:
                self._cleanup(failed)
            except (
                FrameNestYouTubeClaimRepositoryError,
                YouTubeStagingError,
            ):
                return
        except (
            FrameNestYouTubeClaimRepositoryError,
            FrameNestYouTubeAcquisitionError,
        ):
            return

    def _cleanup(
        self,
        claim: YouTubeAcquisitionClaim,
    ) -> YouTubeAcquisitionClaim:
        if claim.cleanup_state is YouTubeStagingCleanupState.COMPLETE:
            return claim
        self._staging.cleanup(claim.staging_key)
        cleaned = claim.evolve(
            updated_at_ms=self._now_ms(),
            cleanup_state=YouTubeStagingCleanupState.COMPLETE,
            cleanup_completed_at_ms=self._now_ms(),
        )
        return self._save(claim, cleaned)

    def _save(
        self,
        previous: YouTubeAcquisitionClaim,
        updated: YouTubeAcquisitionClaim,
    ) -> YouTubeAcquisitionClaim:
        return self._repository.save(
            updated,
            expected_state=previous.state,
            expected_version=previous.version,
        )


def youtube_classification_for_upload(
    repository: YouTubeAcquisitionClaimRepository,
    upload_id: UploadSessionId,
) -> CatalogUploadClassification | None:
    """Return sparse catalog defaults only for a linked YouTube claim.

    When the claim carries a usable upstream_title and catalog display_title is
    still absent at first catalog handoff, import that title as the initial
    editable display_title. Administrator metadata Save remains canonical.

    New YouTube catalog handoffs seed content_category=youtube and structured
    creator attribution from retained channel identity when present. Duplicate
    and reuse paths never reach this first-catalog insert.
    """
    claim = repository.find_by_upload_id(upload_id)
    if claim is None:
        return None
    creator_kind = None
    creator_stable_id = None
    creator_display_name = None
    if claim.upstream_channel_id is not None or claim.upstream_channel is not None:
        creator_kind = CreatorAttributionKind.YOUTUBE_CHANNEL
        creator_stable_id = claim.upstream_channel_id
        creator_display_name = claim.upstream_channel
    return CatalogUploadClassification(
        content_category=ContentCategory.YOUTUBE,
        acquisition_source=AcquisitionSource.YOUTUBE_MANUAL_CLAIM,
        display_title=_imported_display_title_from_upstream(claim.upstream_title),
        creator_attribution_kind=creator_kind,
        creator_stable_id=creator_stable_id,
        creator_handle=None,
        creator_display_name=creator_display_name,
    )


def _imported_display_title_from_upstream(
    upstream_title: str | None,
) -> MediaDisplayTitle | None:
    """Map claim upstream_title onto catalog display_title bounds, or skip."""
    if upstream_title is None:
        return None
    candidate = upstream_title
    if len(candidate) > MAX_DISPLAY_TITLE_CODE_POINTS:
        candidate = candidate[:MAX_DISPLAY_TITLE_CODE_POINTS].rstrip()
    if not candidate:
        return None
    try:
        return MediaDisplayTitle(candidate)
    except FrameNestMediaMetadataError:
        return None


def automatic_analysis_allowed_for_upload(
    repository: YouTubeAcquisitionClaimRepository,
    upload_id: UploadSessionId,
) -> bool:
    """Fail closed for linked YouTube acquisitions, even when globally enabled."""
    return repository.find_by_upload_id(upload_id) is None


async def _one_chunk(chunk: bytes) -> AsyncIterator[bytes]:
    yield chunk


def _identity_text(value: object) -> str | None:
    if value is None:
        return None
    to_string = getattr(value, "to_string", None)
    if not callable(to_string):
        raise YouTubeAcquisitionInfrastructureError(
            "YouTube acquisition is unavailable."
        )
    return str(to_string())


def _encode_owned_cursor(created_at_ms: int, claim_id: str) -> str:
    payload = json.dumps(
        {"created_at_ms": created_at_ms, "id": claim_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_owned_cursor(cursor: str) -> tuple[int, str]:
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        created_at_ms = payload["created_at_ms"]
        claim_id = payload["id"]
        if (
            isinstance(created_at_ms, bool)
            or not isinstance(created_at_ms, int)
            or created_at_ms < 0
            or not isinstance(claim_id, str)
            or not claim_id
        ):
            raise ValueError("invalid cursor")
        return created_at_ms, claim_id
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise YouTubeAcquisitionInvalidCursorError(
            "Invalid YouTube request cursor."
        ) from exc


def _notify(coordinator: object | None) -> None:
    if coordinator is None:
        return
    try:
        notify = getattr(coordinator, "notify")
        notify()
    except Exception:
        return


def _safe_log(**fields: object) -> None:
    try:
        LOGGER.emit(**fields)
    except Exception:
        return


def _log_unexpected_runner_death() -> None:
    _safe_log(
        level="ERROR",
        event="youtube_acquisition_runner_unexpected_death",
        operation="youtube_acquisition_run",
        error_code="YOUTUBE_ACQUISITION_RUNNER_UNEXPECTED_DEATH",
        retryable=False,
    )
