"""Application service and lifecycle coordinator for requester-private X.

X acquisition is a bounded requester-private candidate. It reuses the proven
YouTube requester-ownership, admission-limit, recovery, staging and
catalog-handoff discipline without generalizing the YouTube implementation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import functools
import hashlib
import time

from framenest.application.in_process_lifecycle import (
    IterationFailureLimiter,
    ShutdownDeadline,
    attach_unexpected_runner_observer,
    shutdown_executor_backed_coordinator,
)

from framenest.application.media_user_alias import apply_alias_content_to_media
from framenest.application.ports.media_metadata_repository import (
    MediaMetadataRepository,
)
from framenest.application.ports.media_user_alias_repository import (
    AliasTagNotFoundError,
    MediaUserAliasRepository,
)
from framenest.application.ports.upload_publications import (
    UploadPublicationCandidate,
    UploadPublicationRepository,
)
from framenest.application.ports.upload_sessions import UploadSessionRepository
from framenest.application.ports.x_acquisition import (
    FrameNestXClaimRepositoryError,
    XAcquisitionClaimRepository,
    XClaimConcurrencyConflictError,
    XClaimNotFoundError,
    XClaimSourceIdentityConflictError,
)
from framenest.application.ports.x_extractor import (
    XExtractionError,
    XExtractionInterrupted,
    XExtractor,
    XExtractorConfigurationError,
)
from framenest.application.upload_catalog import CatalogUploadClassification
from framenest.application.upload_transport import (
    UploadTransportError,
    UploadTransportService,
)
from framenest.domain.identities import MediaId, MediaLocationId, XPostClaimId
from framenest.domain.media_classification import (
    AcquisitionSource,
    ContentCategory,
    CreatorAttributionKind,
)
from framenest.domain.media_metadata import (
    MAX_DISPLAY_TITLE_CODE_POINTS,
    FrameNestMediaMetadataError,
    MediaDisplayTitle,
)
from framenest.domain.media_user_alias import MediaUserAliasContent
from framenest.domain.identity_access import (
    ROLE_ADMIN,
    IdentityMappingEntry,
    normalize_login,
)
from framenest.domain.uploads import (
    UploadDuplicateResolutionMode,
    UploadSessionId,
    UploadSessionState,
    UploadStorageKey,
)
from framenest.domain.x_acquisition import (
    ACTIVE_X_ACQUISITION_STATES,
    LIVE_CATALOG_X_ACQUISITION_STATES,
    KNOWN_X_FAILURE_CODES,
    MAX_ASSETS_PER_POST,
    MAX_CLAIM_BYTES,
    MAX_CLAIM_STAGING_FOOTPRINT_BYTES,
    REQUESTER_PHASE_COMPLETED,
    SUCCESS_X_ACQUISITION_STATES,
    SUCCESS_X_ASSET_STATES,
    TERMINAL_X_ACQUISITION_STATES,
    FrameNestXAcquisitionError,
    XAcquisitionState,
    XAsset,
    XAssetState,
    XFailureStage,
    XMediaType,
    XPostClaim,
    XStagingCleanupState,
    accept_x_post_url,
    default_x_category,
    derive_x_requester_phase,
    ensure_x_asset_transition_allowed,
    ensure_x_transition_allowed,
    is_claim_wide_x_failure,
    is_retryable_x_failure,
    normalize_x_creator,
    x_title_from_post_post,
)
from framenest.structured_logging import get_logger

DEFAULT_ACQUISITION_POLL_INTERVAL_SECONDS = 0.25
DEFAULT_ACQUISITION_BATCH_SIZE = 32
MS_PER_HOUR = 3_600_000
MS_PER_DAY = 86_400_000
LOGGER = get_logger("x_acquisition")


class XAcquisitionError(RuntimeError):
    """Sanitized base application failure."""


class XAcquisitionNotFoundError(XAcquisitionError):
    """Requested durable claim or asset does not exist."""


class XAcquisitionStateConflictError(XAcquisitionError):
    """Requested operation is incompatible with current durable state."""


class XAcquisitionNotRetryableError(XAcquisitionStateConflictError):
    """Claim state does not permit a retry."""


class XAcquisitionNoRetryableAssetsError(XAcquisitionStateConflictError):
    """Claim has no assets eligible for retry."""


class XAcquisitionInfrastructureError(XAcquisitionError):
    """Required durable or filesystem infrastructure is unavailable."""


class XAcquisitionInvalidRequestError(XAcquisitionError):
    """Submitted URL is outside the accepted X post policy."""


class XAcquisitionInvalidCursorError(XAcquisitionInvalidRequestError):
    """Owned-request list cursor is malformed or outside the accepted policy."""


class XAcquisitionInvalidRequesterIdentityError(XAcquisitionInvalidRequestError):
    """Requester login identity cannot be normalized."""


class XAcquisitionCategoryConflictError(XAcquisitionError):
    """Same-requester Save requested a category that conflicts with live catalog truth."""


class XRequestLimitError(XAcquisitionError):
    """Ordinary requester admission limit was reached."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class XRequestInsufficientStorageError(XAcquisitionError):
    """Ordinary requester admission failed the free-space gate."""


@dataclass(frozen=True, slots=True)
class XAssetSnapshot:
    """Sanitized requester/admin asset projection."""

    asset_id: str
    ordinal: int
    media_type: str
    state: str
    acquired_bytes: int | None
    media_id: str | None
    failure_stage: str | None
    failure_code: str | None
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class XClaimSnapshot:
    """Sanitized durable projection shared by requester and administration."""

    claim_id: str
    state: str
    phase: str
    x_post_id: str
    submitted_url: str
    canonical_url: str
    title: str | None
    source_author_handle: str | None
    source_author_display_name: str | None
    discovered_asset_count: int
    success_count: int
    failure_count: int
    failure_stage: str | None
    failure_code: str | None
    retry_of_claim_id: str | None
    created_at_ms: int
    updated_at_ms: int
    completed_at_ms: int | None
    requested_content_category: str | None = None
    can_retry: bool = False
    submission_result: str | None = None
    assets: tuple[XAssetSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class XRequestSubmission:
    """Sanitized requester submit-result projection."""

    request_id: str
    submission_result: str
    phase: str
    x_post_id: str
    submitted_url: str


@dataclass(frozen=True, slots=True)
class XRequestPage:
    """Bounded requester claim listing."""

    items: tuple[XClaimSnapshot, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class XRequestLimits:
    """Bounded admission defaults for the X requester candidate."""

    max_assets_per_post: int = MAX_ASSETS_PER_POST
    max_video_duration_seconds: int = 300
    max_asset_bytes: int = 1_073_741_824
    max_claim_bytes: int = MAX_CLAIM_BYTES
    max_claim_staging_footprint_bytes: int = MAX_CLAIM_STAGING_FOOTPRINT_BYTES
    max_active_per_requester: int = 1
    max_global_active: int = 8
    max_submits_per_hour: int = 6
    max_failed_per_24h: int = 10
    min_free_space_reserve_bytes: int = 67_108_864
    free_space_bytes: Callable[[], int] | None = None


def default_now_ms() -> int:
    return int(time.time() * 1000)


class XAcquisitionAdministrationService:
    """Administrator review of requester-private X claims and resulting media."""

    def __init__(
        self,
        repository: XAcquisitionClaimRepository,
        *,
        now_ms: Callable[[], int] = default_now_ms,
    ) -> None:
        self._repository = repository
        self._now_ms = now_ms

    def get(self, claim_id: XPostClaimId) -> XClaimSnapshot:
        claim = self._repository.get_post(claim_id)
        if claim is None:
            raise XAcquisitionNotFoundError("X claim not found")
        assets = self._repository.list_assets_for_post(claim.id)
        return _administration_snapshot(claim, assets)

    def list(self) -> tuple[XClaimSnapshot, ...]:
        raise XAcquisitionInfrastructureError(
            "X administration listing is not enabled in this candidate."
        )


class XAcquisitionRequestService:
    """Ordinary requester admission and own-claim access."""

    def __init__(
        self,
        repository: XAcquisitionClaimRepository,
        *,
        limits: XRequestLimits,
        now_ms: Callable[[], int] = default_now_ms,
        alias_repository: MediaUserAliasRepository | None = None,
        metadata_repository: MediaMetadataRepository | None = None,
    ) -> None:
        self._repository = repository
        self._limits = limits
        self._now_ms = now_ms
        self._alias_repository = alias_repository
        self._metadata_repository = metadata_repository

    def submit(
        self,
        url: str,
        *,
        login_key: str,
        alias: MediaUserAliasContent | None = None,
        content_category: ContentCategory | None = None,
    ) -> XRequestSubmission:
        identity = accept_x_post_url(url)
        requester = _normalize_requester(login_key)
        if alias is not None:
            self._validate_alias_tags(alias)
        requested = content_category

        owned_successful = self._repository.find_owned_successful_by_post_id(
            post_id_key=identity.post_id,
            created_by_login_key=requester,
        )
        if owned_successful is not None:
            self._reject_category_conflict(owned_successful, requested, live_catalog=True)
            self._store_submit_alias(owned_successful.id, requester, alias)
            if alias is not None:
                self._apply_alias_to_successful_assets(
                    owned_successful, requester, alias
                )
            return _submission(
                owned_successful, submission_result="reuse", requester=requester
            )

        active = self._repository.find_active_by_post_id(
            post_id_key=identity.post_id,
            created_by_login_key=requester,
        )
        if active is not None:
            self._reject_category_conflict(active, requested, live_catalog=False)
            self._store_submit_alias(active.id, requester, alias)
            return _submission(active, submission_result="active_reuse", requester=requester)

        self._enforce_admission_limits(requester)

        claim = XPostClaim.new(
            submitted_url=url,
            now_ms=self._now_ms(),
            created_by_login_key=requester,
            requested_content_category=requested,
        )
        claim, created = self._repository.create_or_get_active(claim)
        if not created:
            self._reject_category_conflict(claim, requested, live_catalog=False)
            _submission_result = "active_reuse"
        else:
            _submission_result = "new"
        self._store_submit_alias(claim.id, requester, alias)
        return XRequestSubmission(
            request_id=claim.id.to_string(),
            submission_result=_submission_result,
            phase=derive_x_requester_phase(claim),
            x_post_id=claim.x_post_id,
            submitted_url=claim.submitted_url,
        )

    def _reject_category_conflict(
        self,
        claim: XPostClaim,
        requested: ContentCategory | None,
        *,
        live_catalog: bool,
    ) -> None:
        if requested is None:
            return
        if live_catalog:
            live = self._live_catalog_categories(claim)
            if live is None:
                raise XAcquisitionCategoryConflictError(
                    "Requested category conflicts with the existing Kronika save."
                )
            if len(live) != 1 or next(iter(live)) != requested:
                raise XAcquisitionCategoryConflictError(
                    "Requested category conflicts with the existing Kronika save."
                )
            return
        if claim.requested_content_category != requested:
            raise XAcquisitionCategoryConflictError(
                "Requested category conflicts with the existing Kronika save."
            )

    def _live_catalog_categories(self, claim: XPostClaim) -> set[ContentCategory] | None:
        """Return live canonical categories, or None when they cannot be confirmed."""
        assets = self._repository.list_assets_for_post(claim.id)
        hidden = _hidden_media_ids(
            self._repository,
            claim.created_by_login_key or "",
            tuple(
                item.media_id.to_string()
                for item in assets
                if item.media_id is not None
            ),
        )
        live: set[ContentCategory] = set()
        found = False
        for asset in assets:
            if asset.state is not XAssetState.CATALOGED or asset.media_id is None:
                continue
            if asset.media_id.to_string() in hidden:
                continue
            found = True
            if self._metadata_repository is None:
                return None
            snapshot = self._metadata_repository.get_media_metadata(asset.media_id)
            if not snapshot.persisted:
                return None
            live.add(snapshot.content_category)
        if not found:
            if claim.requested_content_category is None:
                return None
            return {claim.requested_content_category}
        return live

    def list_owned(
        self,
        *,
        login_key: str,
        limit: int = 20,
        cursor: str | None = None,
    ) -> XRequestPage:
        requester = _normalize_requester(login_key)
        after_created_at_ms: int | None = None
        after_id: str | None = None
        if cursor:
            try:
                encoded, raw = cursor.split(":", 1)
                after_created_at_ms = int(encoded)
                after_id = raw
            except (ValueError, AttributeError):
                raise XAcquisitionInvalidCursorError(
                    "Invalid X request cursor."
                ) from None
        claims = self._repository.list_owned(
            created_by_login_key=requester,
            limit=limit + 1,
            after_created_at_ms=after_created_at_ms,
            after_id=after_id,
        )
        has_more = len(claims) > limit
        page = claims[:limit]
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = f"{last.created_at_ms}:{last.id.to_string()}"
        items = tuple(
            _requester_snapshot(claim, self._repository)
            for claim in page
        )
        return XRequestPage(items=items, next_cursor=next_cursor)

    def get_owned(self, claim_id: XPostClaimId, *, login_key: str) -> XClaimSnapshot:
        requester = _normalize_requester(login_key)
        claim = self._repository.get_owned_post(claim_id, created_by_login_key=requester)
        if claim is None:
            raise XAcquisitionNotFoundError("X claim not found")
        return _requester_snapshot(claim, self._repository)

    def retry(self, claim_id: XPostClaimId, *, login_key: str) -> XClaimSnapshot:
        requester = _normalize_requester(login_key)
        claim = self._repository.get_owned_post(claim_id, created_by_login_key=requester)
        if claim is None:
            raise XAcquisitionNotFoundError("X claim not found")
        if claim.state is not XAcquisitionState.FAILED and not (
            claim.state is XAcquisitionState.COMPLETED_PARTIAL
            and claim.failure_count > 0
        ):
            raise XAcquisitionNotRetryableError(
                "X claim is not retryable in its current state."
            )
        assets = self._repository.list_assets_for_post(claim.id)
        retryable = [a for a in assets if _asset_retryable(a)]
        # No discovered assets means extraction itself failed: re-extract.
        if not assets and claim.state is XAcquisitionState.FAILED:
            queued = claim.advance(
                XAcquisitionState.QUEUED, updated_at_ms=self._now_ms(),
                completed_at_ms=None, failure_stage=None, failure_code=None,
            )
            self._save_claim(claim, queued)
            return _requester_snapshot(queued, self._repository)
        if not retryable:
            raise XAcquisitionNoRetryableAssetsError(
                "X claim has no retryable assets."
            )
        # Reset retryable assets to pending for re-acquisition. Successful
        # cataloged assets are preserved and never re-downloaded.
        for asset in retryable:
            reset = asset.advance(
                XAssetState.PENDING, updated_at_ms=self._now_ms(),
                failure_stage=None, failure_code=None,
            )
            self._save_asset(asset, reset)
        # Resume acquisition directly so existing assets are not re-extracted
        # and successful cataloged assets are not duplicated.
        resumed = claim.advance(
            XAcquisitionState.ACQUIRING, updated_at_ms=self._now_ms(),
            completed_at_ms=None, failure_stage=None, failure_code=None,
        )
        updated = self._save_claim(claim, resumed)
        return _requester_snapshot(updated, self._repository)

    def _enforce_admission_limits(self, requester: str) -> None:
        if self._limits.max_active_per_requester >= 0:
            active = self._repository.count_active_for_requester(
                created_by_login_key=requester
            )
            if active >= self._limits.max_active_per_requester:
                raise XRequestLimitError(
                    "X_REQUEST_ACTIVE_LIMIT",
                    "You already have an active X request.",
                )
        global_active = self._repository.count_global_active_ordinary()
        if global_active >= self._limits.max_global_active:
            raise XRequestLimitError(
                "X_REQUEST_GLOBAL_QUEUE_FULL", "X request queue is full."
            )
        since = self._now_ms() - MS_PER_HOUR
        submits = self._repository.count_submits_since(
            created_by_login_key=requester, since_ms=since
        )
        if submits >= self._limits.max_submits_per_hour:
            raise XRequestLimitError(
                "X_REQUEST_RATE_LIMIT", "Too many X requests this hour."
            )
        failed_since = self._now_ms() - MS_PER_DAY
        failed_count = self._repository.count_failed_transitions_since(
            created_by_login_key=requester, since_ms=failed_since
        )
        if failed_count >= self._limits.max_failed_per_24h:
            raise XRequestLimitError(
                "X_REQUEST_FAILED_24H_LIMIT", "Too many failed X requests."
            )
        if self._limits.free_space_bytes is not None:
            available = self._limits.free_space_bytes()
            if available <= self._limits.min_free_space_reserve_bytes:
                raise XRequestInsufficientStorageError(
                    "X staging has insufficient free space."
                )

    def _save_claim(
        self, previous: XPostClaim, updated: XPostClaim
    ) -> XPostClaim:
        return self._repository.save_post(
            updated,
            expected_state=previous.state,
            expected_version=previous.version,
        )

    def _save_asset(self, previous: XAsset, updated: XAsset) -> XAsset:
        return self._repository.save_asset(
            updated,
            expected_state=previous.state,
            expected_version=previous.version,
        )

    def _validate_alias_tags(self, content: MediaUserAliasContent) -> None:
        if content.is_empty():
            return
        if self._alias_repository is None:
            raise XAcquisitionInfrastructureError("X alias overlay is unavailable.")
        if not self._alias_repository.canonical_tag_keys_exist(content.tag_keys):
            raise AliasTagNotFoundError()

    def _store_submit_alias(
        self,
        claim_id: XPostClaimId,
        requester: str,
        alias: MediaUserAliasContent | None,
    ) -> None:
        if alias is None:
            return
        self._repository.upsert_pending_alias(
            claim_id, requester, alias, self._now_ms()
        )

    def _apply_alias_to_successful_assets(
        self,
        claim: XPostClaim,
        requester: str,
        alias: MediaUserAliasContent,
    ) -> None:
        if self._alias_repository is None:
            raise XAcquisitionInfrastructureError("X alias overlay is unavailable.")
        now_ms = self._now_ms()
        assets = self._repository.list_assets_for_post(claim.id)
        hidden = _hidden_media_ids(
            self._repository,
            requester,
            tuple(
                item.media_id.to_string()
                for item in assets
                if item.media_id is not None
            ),
        )
        for asset in assets:
            if asset.state not in SUCCESS_X_ASSET_STATES or asset.media_id is None:
                continue
            if asset.media_id.to_string() in hidden:
                continue
            apply_alias_content_to_media(
                self._alias_repository,
                media_id=asset.media_id,
                login_key=requester,
                content=alias,
                now_ms=now_ms,
            )


class XAcquisitionCoordinator:
    """Single-worker async state machine for requester-private X claims."""

    def __init__(
        self,
        repository: XAcquisitionClaimRepository,
        extractor: XExtractor,
        staging: object,
        transport: UploadTransportService,
        upload_repository: UploadSessionRepository,
        publication_repository: UploadPublicationRepository,
        validation_coordinator: object,
        publication_coordinator: object,
        *,
        chunk_size_bytes: int = 1_048_576,
        poll_interval_seconds: float = DEFAULT_ACQUISITION_POLL_INTERVAL_SECONDS,
        batch_size: int = DEFAULT_ACQUISITION_BATCH_SIZE,
        now_ms: Callable[[], int] = default_now_ms,
        executor: ThreadPoolExecutor | None = None,
        alias_repository: MediaUserAliasRepository | None = None,
    ) -> None:
        self._repository = repository
        self._extractor = extractor
        self._staging = staging
        self._transport = transport
        self._upload_repository = upload_repository
        self._publication_repository = publication_repository
        self._validation_coordinator = validation_coordinator
        self._publication_coordinator = publication_coordinator
        self._chunk_size_bytes = chunk_size_bytes
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_size = batch_size
        self._now_ms = now_ms
        self._wake = asyncio.Event()
        self._runner_task: asyncio.Task | None = None
        self._shutdown_requested = False
        self._draining = False
        self._executor = executor
        self._owns_executor = executor is None
        self._iteration_failures = IterationFailureLimiter()
        self._alias_repository = alias_repository

    async def start(self) -> None:
        if self._runner_task is None:
            self._shutdown_requested = False
            self._ensure_executor()
            self._runner_task = asyncio.create_task(self._run())
            attach_unexpected_runner_observer(
                self._runner_task,
                is_expected=lambda: self._shutdown_requested,
                log_unexpected=_log_unexpected_runner_death,
            )

    def notify(self) -> None:
        if self._runner_task is not None and self._runner_task.done() and not self._shutdown_requested:
            raise RuntimeError("X acquisition runner is not active")
        self._wake.set()

    async def drain(self) -> None:
        await self._drain_once()

    async def shutdown(
        self,
        deadline: ShutdownDeadline | None = None,
    ) -> None:
        self._shutdown_requested = True
        binder = getattr(self._extractor, "bind_shutdown_deadline", None)
        if callable(binder):
            binder(deadline)
        interrupt = getattr(self._extractor, "request_interrupt", None)
        if callable(interrupt):
            interrupt()
        self._wake.set()
        runner = self._runner_task
        cancellation: asyncio.CancelledError | None = None
        try:
            cancellation = await shutdown_executor_backed_coordinator(
                runner=runner,
                executor=self._executor,
                owns_executor=self._owns_executor,
                deadline=deadline,
                log_runner_fault=lambda: _safe_log(
                    level="WARNING",
                    event="x_acquisition_runner_shutdown_fault",
                    operation="x_acquisition_shutdown",
                    error_code="X_ACQUISITION_RUNNER_SHUTDOWN_FAULT",
                    retryable=False,
                ),
                log_unresolved=lambda: _safe_log(
                    level="WARNING",
                    event="x_acquisition_executor_unresolved",
                    operation="x_acquisition_shutdown",
                    error_code="X_ACQUISITION_EXECUTOR_UNRESOLVED",
                    retryable=False,
                ),
            )
        finally:
            self._runner_task = None
            if self._owns_executor:
                self._executor = None
        if cancellation is not None:
            raise cancellation

    @property
    def runner_done(self) -> bool:
        task = self._runner_task
        if task is None:
            return True
        return task.done()

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="framenest-x-acquisition",
            )
            self._owns_executor = True
        return self._executor

    async def _run_blocking(self, func, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            functools.partial(func, *args, **kwargs),
        )

    async def _run(self) -> None:
        while not self._shutdown_requested:
            try:
                progress = await self._drain_once()
            except asyncio.CancelledError:
                if self._shutdown_requested:
                    return
                raise
            except Exception:
                progress = False
                if self._iteration_failures.allow():
                    _safe_log(
                        level="ERROR",
                        event="x_acquisition_runner_iteration_failed",
                        operation="x_acquisition_run",
                        error_code="X_ACQUISITION_RUNNER_ITERATION_FAILED",
                        retryable=True,
                    )
            if self._shutdown_requested:
                return
            if progress:
                await asyncio.sleep(0)
            else:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), self._poll_interval_seconds)
                except asyncio.TimeoutError:
                    pass

    async def _drain_once(self) -> bool:
        self._draining = True
        try:
            cleanup = self._repository.list_cleanup_candidates(limit=self._batch_size)
            for claim in cleanup:
                await self._reconcile_cleanup(claim)
            candidates = self._repository.list_recovery_candidates(limit=self._batch_size)
            if not candidates:
                return False
            for claim in candidates:
                await self._process(claim)
            return True
        finally:
            self._draining = False

    async def _process(self, claim: XPostClaim) -> None:
        try:
            if claim.state is XAcquisitionState.SUBMITTED:
                await self._queue(claim)
            elif claim.state is XAcquisitionState.QUEUED:
                await self._extract(claim)
            elif claim.state is XAcquisitionState.EXTRACTING:
                await self._extract(claim)
            elif claim.state is XAcquisitionState.ACQUIRING:
                await self._acquire(claim)
            elif claim.state is XAcquisitionState.HANDING_OFF:
                await self._handoff(claim)
        except XExtractionInterrupted:
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._fail(claim, stage=XFailureStage.INTERNAL, code=_code_from_exception(exc))

    async def _queue(self, claim: XPostClaim) -> None:
        queued = claim.advance(XAcquisitionState.QUEUED, updated_at_ms=self._now_ms())
        self._save(claim, queued)

    async def _extract(self, claim: XPostClaim) -> None:
        current = self._repository.get_post(claim.id)
        if current is None or current.state in TERMINAL_X_ACQUISITION_STATES:
            return
        if current.state is XAcquisitionState.QUEUED:
            current = self._save(
                current,
                current.advance(
                    XAcquisitionState.EXTRACTING, updated_at_ms=self._now_ms()
                ),
            )
        try:
            inspection = await self._run_blocking(
                self._extractor.inspect,
                post_id=current.x_post_id,
                submitted_url=current.submitted_url,
            )
        except XExtractionInterrupted:
            raise
        except XExtractionError as exc:
            stage = _stage_for_extractor_code(exc.code)
            await self._fail_with_assets(
                current, stage=stage, code=exc.code
            )
            return
        if inspection.failure_code is not None:
            await self._fail_with_assets(
                current, stage=XFailureStage.EXTRACTION, code=inspection.failure_code
            )
            return
        if not inspection.assets or len(inspection.assets) > MAX_ASSETS_PER_POST:
            code = (
                "X_TOO_MANY_ASSETS"
                if len(inspection.assets) > MAX_ASSETS_PER_POST
                else "X_NO_SUPPORTED_MEDIA"
            )
            await self._fail_with_assets(
                current, stage=XFailureStage.EXTRACTION, code=code
            )
            return
        canonical_url = current.canonical_url
        if inspection.canonical_url is not None:
            try:
                validated = accept_x_post_url(inspection.canonical_url)
                canonical_url = validated.canonical_url
            except FrameNestXAcquisitionError:
                canonical_url = current.canonical_url
        stable_id, handle, display = normalize_x_creator(
            stable_id=inspection.author_stable_id,
            handle=inspection.author_handle,
            display_name=inspection.author_display_name,
        )
        post_text = _bound_post_text(inspection.post_text)
        title = current.title
        creator_handle = handle or current.source_author_handle
        media_type_label = _media_type_label(inspection.assets[0].media_type)
        derived_title = x_title_from_post_post(
            post_text,
            creator_handle=creator_handle,
            media_type_label=media_type_label,
            post_id=current.x_post_id,
            ordinal=0,
        )
        ev = current.advance(
            XAcquisitionState.ACQUIRING,
            updated_at_ms=self._now_ms(),
            canonical_url=canonical_url,
            source_author_stable_id=stable_id,
            source_author_handle=handle,
            source_author_display_name=display,
            source_post_text=post_text,
            source_posted_at_ms=inspection.posted_at_ms,
            extractor_version=inspection.extractor_version,
            discovered_asset_count=len(inspection.assets),
            title=derived_title,
        )
        ev = self._save(current, ev)
        assets = tuple(
            XAsset.new(
                claim_id=ev.id,
                ordinal=desc.ordinal,
                media_type=desc.media_type,
                expected_mime=desc.expected_mime,
                now_ms=self._now_ms(),
                source_media_key=desc.source_media_key,
                width=desc.width,
                height=desc.height,
                duration_seconds=desc.duration_seconds,
                selected_variant=desc.selected_variant,
            )
            for desc in inspection.assets
        )
        self._repository.create_assets(assets)

    async def _acquire(self, claim: XPostClaim) -> None:
        assets = self._repository.list_assets_for_post(claim.id)
        interrupted = [a for a in assets if a.state is XAssetState.ACQUIRING]
        for asset in interrupted:
            clearer = getattr(self._staging, "clear", None)
            if callable(clearer):
                try:
                    clearer(asset.stage_key)
                except Exception:
                    _safe_log(
                        level="WARNING",
                        event="x_acquisition_staging_clear_failed",
                        operation="x_acquisition_recovery",
                        error_code="X_STAGING_CLEANUP_FAILED",
                        retryable=True,
                    )
                    return
        assets = self._repository.list_assets_for_post(claim.id)
        pending = [
            a
            for a in assets
            if a.state
            in (XAssetState.PENDING, XAssetState.EXTRACTED, XAssetState.ACQUIRING)
        ]
        if not pending:
            await self._advance_to_handoff(claim, assets)
            return
        asset = pending[0]
        if asset.state is XAssetState.PENDING:
            asset = self._save_asset(
                asset,
                asset.advance(XAssetState.EXTRACTED, updated_at_ms=self._now_ms()),
            )
        if asset.state is XAssetState.EXTRACTED:
            acquiring = asset.advance(
                XAssetState.ACQUIRING, updated_at_ms=self._now_ms()
            )
            acquiring = self._save_asset(asset, acquiring)
        else:
            acquiring = asset
        try:
            result = await self._acquire_one(claim, acquiring)
        except XExtractionInterrupted:
            return
        except Exception as exc:
            await self._fail_asset(
                claim, acquiring, stage=XFailureStage.ACQUISITION,
                code=_code_from_exception(exc),
            )
            return
        staged = acquiring.advance(
            XAssetState.STAGED,
            updated_at_ms=self._now_ms(),
            acquired_bytes=result.size_bytes,
            acquired_sha256=result.sha256,
        )
        self._save_asset(acquiring, staged)
        await self._advance_to_handoff(claim, self._repository.list_assets_for_post(claim.id))

    async def _advance_to_handoff(self, claim: XPostClaim, assets: tuple[XAsset, ...]) -> None:
        if any(a.state in _AWAITING_ACQUISITION_ASSET_STATES for a in assets):
            return
        current = self._repository.get_post(claim.id)
        if current is None or current.state not in ACTIVE_X_ACQUISITION_STATES:
            return
        handoff_target = current.advance(
            XAcquisitionState.HANDING_OFF, updated_at_ms=self._now_ms()
        )
        self._save(current, handoff_target)

    async def _acquire_one(self, claim: XPostClaim, asset: XAsset) -> _Acquired:
        result = await self._run_blocking(
            self._extractor.download,
            post_id=claim.x_post_id,
            ordinal=asset.ordinal,
            media_type=asset.media_type.value,
            expected_mime=asset.expected_mime,
            source_media_key=asset.source_media_key,
            selected_variant=asset.selected_variant,
            stage_key=asset.stage_key,
            submitted_url=claim.submitted_url,
            staging=self._staging,
        )
        if (
            not isinstance(result.size_bytes, int)
            or result.size_bytes <= 0
            or not isinstance(result.sha256, str)
            or len(result.sha256) != 64
        ):
            raise XAcquisitionInfrastructureError("X acquisition produced no bytes.")
        if result.size_bytes > 1_073_741_824:
            raise _AssetTooLarge()
        return _Acquired(size_bytes=result.size_bytes, sha256=result.sha256)

    async def _handoff(self, claim: XPostClaim) -> None:
        assets = self._repository.list_assets_for_post(claim.id)
        for asset in assets:
            if asset.state is XAssetState.STAGED:
                await self._handoff_asset(claim, asset)
                return
        for asset in assets:
            if asset.state is XAssetState.HANDING_OFF:
                await self._project_asset(claim, asset)
                return
        failed = [a for a in assets if a.state is XAssetState.FAILED]
        success = [a for a in assets if a.state is XAssetState.CATALOGED]
        current = self._repository.get_post(claim.id)
        if current is None or current.state not in ACTIVE_X_ACQUISITION_STATES:
            return
        updated = current.evolve(
            updated_at_ms=self._now_ms(),
            failure_count=len(failed),
            success_count=len(success),
        )
        if failed and not success:
            updated = updated.advance(
                XAcquisitionState.FAILED,
                updated_at_ms=self._now_ms(),
                failure_stage=XFailureStage.ACQUISITION,
                failure_code="X_MULTI_ASSET_FAILED" if len(assets) > 1 else (failed[0].failure_code or "X_NO_SUPPORTED_MEDIA"),
                completed_at_ms=self._now_ms(),
            )
        elif failed and success:
            updated = updated.advance(
                XAcquisitionState.COMPLETED_PARTIAL,
                updated_at_ms=self._now_ms(),
                completed_at_ms=self._now_ms(),
            )
        else:
            updated = updated.advance(
                XAcquisitionState.COMPLETED,
                updated_at_ms=self._now_ms(),
                completed_at_ms=self._now_ms(),
            )
        self._save(current, updated)
        await self._cleanup_claim(updated)

    async def _handoff_asset(self, claim: XPostClaim, asset: XAsset) -> None:
        if asset.acquired_bytes is None:
            raise XAcquisitionInfrastructureError("X staged asset has no bytes.")
        upload_id = UploadSessionId.from_string(asset.id.to_string())
        upload_storage_key = UploadStorageKey(asset.stage_key)
        requester = claim.created_by_login_key
        # Every X claim has a requester: identity is required at claim creation
        # (submit normalizes login_key and fails closed on missing identity), so
        # this selection never produces EXPLICIT for a real X handoff. Under
        # SILENT_KEEP_SEPARATE, ordinary byte duplicates keep-separate atomically
        # during validation success and DUPLICATE_PENDING is never observed
        # downstream, which is why the YouTube-style duplicate auto-resolve is
        # not mirrored in the X projection.
        duplicate_mode = (
            UploadDuplicateResolutionMode.SILENT_KEEP_SEPARATE
            if requester is not None
            else UploadDuplicateResolutionMode.EXPLICIT
        )
        snapshot = self._transport.create_session(
            display_filename=f"x-{claim.x_post_id}-{asset.ordinal}.{_extension_for_mime(asset.expected_mime)}",
            declared_size_bytes=asset.acquired_bytes,
            session_id=upload_id,
            storage_key=upload_storage_key,
            created_by_login_key=requester,
            duplicate_resolution_mode=duplicate_mode,
        )
        current = asset
        if current.upload_asset_key is None:
            linked = current.advance(
                XAssetState.HANDING_OFF,
                updated_at_ms=self._now_ms(),
                upload_asset_key=upload_id.to_string(),
            )
            current = self._save_asset(asset, linked)
        reader = self._staging.open_artifact(
            asset.stage_key, expected_size_bytes=asset.acquired_bytes
        )
        try:
            while snapshot.received_size_bytes < asset.acquired_bytes:
                reader.verify_still_consistent()
                reader.seek(snapshot.received_size_bytes)
                chunk = reader.read(
                    min(
                        self._chunk_size_bytes,
                        asset.acquired_bytes - snapshot.received_size_bytes,
                    )
                )
                if not chunk:
                    raise UploadTransportError("X upload handoff failed")
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
            snapshot.received_size_bytes != asset.acquired_bytes
            or snapshot.state
            not in {state.value for state in UploadSessionState}
            - {UploadSessionState.CREATED.value, UploadSessionState.RECEIVING.value}
        ):
            raise UploadTransportError("X upload handoff failed")
        _notify(self._validation_coordinator)

    async def _project_asset(self, claim: XPostClaim, asset: XAsset) -> None:
        if asset.upload_asset_key is None:
            raise XAcquisitionInfrastructureError("X asset has no upload linkage.")
        if asset.cleanup_state is XStagingCleanupState.PENDING:
            asset = self._clean_asset_staging(asset)
        upload_id = UploadSessionId.from_string(asset.upload_asset_key)
        upload = self._upload_repository.get(upload_id)
        if upload is None:
            raise XAcquisitionInfrastructureError("X upload handoff failed")
        if upload.state in {UploadSessionState.RECEIVED, UploadSessionState.VALIDATING}:
            _notify(self._validation_coordinator)
            return
        if upload.state in {
            UploadSessionState.PUBLISH_PENDING,
            UploadSessionState.PUBLISHED,
        }:
            _notify(self._publication_coordinator)
            return
        if upload.state is UploadSessionState.CATALOGED:
            candidate = self._publication_repository.get_candidate(upload_id)
            self._complete_asset(claim, asset, candidate)
            return
        if (
            upload.state is UploadSessionState.CANCELLED
            or upload.state is UploadSessionState.DUPLICATE_PENDING
        ):
            # The DUPLICATE_PENDING arm is currently unreachable for X assets:
            # X handoff always creates sessions as SILENT_KEEP_SEPARATE because
            # every X claim has a requester (identity is required at claim
            # creation), while EXPLICIT mode requires requester is None.
            # Ordinary duplicates keep-separate atomically, so
            # DUPLICATE_PENDING is never an observed X state; this arm remains
            # a fail-closed guard in case the duplicate-resolution mode policy
            # ever changes.
            await self._fail_asset(
                claim, asset, stage=XFailureStage.DOWNSTREAM,
                code="X_CATALOG_HANDOFF_FAILED",
            )
            return
        await self._fail_asset(
            claim, asset, stage=XFailureStage.DOWNSTREAM,
            code="X_CATALOG_HANDOFF_FAILED",
        )

    def _complete_asset(
        self,
        claim: XPostClaim,
        asset: XAsset,
        candidate: UploadPublicationCandidate | None,
    ) -> None:
        if (
            candidate is None
            or candidate.publication is None
            or candidate.publication.media_id is None
            or candidate.publication.media_location_id is None
        ):
            raise XAcquisitionInfrastructureError("X catalog handoff failed.")
        completed = asset.advance(
            XAssetState.CATALOGED,
            updated_at_ms=self._now_ms(),
            media_id=candidate.publication.media_id,
            media_location_id=candidate.publication.media_location_id,
            completed_at_ms=self._now_ms(),
        )
        self._save_asset(asset, completed)
        self._apply_pending_alias(claim, completed)

    def _apply_pending_alias(self, claim: XPostClaim, asset: XAsset) -> None:
        if self._alias_repository is None or asset.media_id is None:
            return
        if claim.created_by_login_key is None:
            return
        pending = self._repository.get_pending_alias(claim.id)
        if pending is None:
            return
        apply_alias_content_to_media(
            self._alias_repository,
            media_id=asset.media_id,
            login_key=claim.created_by_login_key,
            content=pending.content,
            now_ms=self._now_ms(),
        )

    async def _reconcile_cleanup(self, claim: XPostClaim) -> None:
        assets = self._repository.list_assets_for_post(claim.id)
        for asset in assets:
            if asset.cleanup_state is XStagingCleanupState.PENDING and asset.state in (
                XAssetState.CATALOGED, XAssetState.FAILED,
            ):
                self._clean_asset_staging(asset)
        updated_assets = self._repository.list_assets_for_post(claim.id)
        if all(
            a.cleanup_state is XStagingCleanupState.COMPLETE
            or a.state in ACTIVE_X_ASSET_STATES
            for a in updated_assets
        ):
            if claim.cleanup_state is XStagingCleanupState.PENDING:
                cleaned = claim.evolve(
                    updated_at_ms=self._now_ms(),
                    cleanup_state=XStagingCleanupState.COMPLETE,
                    cleanup_completed_at_ms=self._now_ms(),
                )
                self._save(claim, cleaned)

    def _clean_asset_staging(self, asset: XAsset) -> XAsset:
        try:
            self._staging.clear(asset.stage_key)
        except Exception:
            return asset
        cleaned = asset.evolve(
            updated_at_ms=self._now_ms(),
            cleanup_state=XStagingCleanupState.COMPLETE,
            cleanup_completed_at_ms=self._now_ms(),
        )
        try:
            return self._save_asset(asset, cleaned)
        except Exception:
            # Same-state cleanup flags cannot use the transition-checked save path.
            # Keep the persisted version so a later legal transition can succeed.
            return asset

    async def _cleanup_claim(self, claim: XPostClaim) -> None:
        for asset in self._repository.list_assets_for_post(claim.id):
            if (
                asset.state in (XAssetState.CATALOGED, XAssetState.FAILED)
                and asset.cleanup_state is XStagingCleanupState.PENDING
            ):
                self._clean_asset_staging(asset)
        assets = self._repository.list_assets_for_post(claim.id)
        if all(
            a.cleanup_state is XStagingCleanupState.COMPLETE or a.state in ACTIVE_X_ASSET_STATES
            for a in assets
        ) and claim.cleanup_state is XStagingCleanupState.PENDING:
            cleaned = claim.evolve(
                updated_at_ms=self._now_ms(),
                cleanup_state=XStagingCleanupState.COMPLETE,
                cleanup_completed_at_ms=self._now_ms(),
            )
            self._save(claim, cleaned)

    async def _fail(
        self, claim: XPostClaim, *, stage: XFailureStage, code: str
    ) -> None:
        try:
            current = self._repository.get_post(claim.id)
            if current is None or current.state in TERMINAL_X_ACQUISITION_STATES:
                return
            failed = current.advance(
                XAcquisitionState.FAILED,
                updated_at_ms=self._now_ms(),
                failure_stage=stage,
                failure_code=code,
                completed_at_ms=self._now_ms(),
            )
            self._save(current, failed)
            await self._cleanup_claim(failed)
        except (FrameNestXClaimRepositoryError, FrameNestXAcquisitionError):
            return

    async def _fail_with_assets(
        self, claim: XPostClaim, *, stage: XFailureStage, code: str
    ) -> None:
        await self._fail(claim, stage=stage, code=code)

    async def _fail_asset(
        self,
        claim: XPostClaim,
        asset: XAsset,
        *,
        stage: XFailureStage,
        code: str,
    ) -> None:
        failed = asset.advance(
            XAssetState.FAILED,
            updated_at_ms=self._now_ms(),
            failure_stage=stage,
            failure_code=code,
        )
        self._save_asset(asset, failed)
        if is_claim_wide_x_failure(code):
            await self._fail(claim, stage=stage, code=code)
        else:
            await self._advance_to_handoff(claim, self._repository.list_assets_for_post(claim.id))

    def _save(self, previous: XPostClaim, updated: XPostClaim) -> XPostClaim:
        return self._repository.save_post(
            updated,
            expected_state=previous.state,
            expected_version=previous.version,
        )

    def _save_asset(self, previous: XAsset, updated: XAsset) -> XAsset:
        return self._repository.save_asset(
            updated,
            expected_state=previous.state,
            expected_version=previous.version,
        )


@dataclass(frozen=True, slots=True)
class _Acquired:
    size_bytes: int
    sha256: str


class _AssetTooLarge(XAcquisitionError):
    pass


def x_classification_for_upload(
    repository: XAcquisitionClaimRepository,
    upload_id: UploadSessionId,
) -> CatalogUploadClassification | None:
    """Return sparse catalog defaults for one source X asset upload."""
    asset = repository.find_asset_by_upload_id(upload_id)
    if asset is None:
        return None
    claim = repository.get_post(asset.claim_id)
    if claim is None:
        return None
    creator_kind = None
    creator_stable_id = None
    creator_handle = None
    creator_display_name = None
    if claim.source_author_stable_id is not None or claim.source_author_handle is not None:
        creator_kind = CreatorAttributionKind.X_AUTHOR
        creator_stable_id = claim.source_author_stable_id
        creator_handle = claim.source_author_handle
        creator_display_name = claim.source_author_display_name
    pending = repository.get_pending_alias(claim.id)
    pending_content = None if pending is None else pending.content
    display_title = (
        pending_content.display_title
        if pending_content is not None and pending_content.display_title is not None
        else _imported_display_title(claim.title)
    )
    description = None if pending_content is None else pending_content.description
    tag_keys = () if pending_content is None else pending_content.tag_keys
    return CatalogUploadClassification(
        content_category=(
            claim.requested_content_category
            if claim.requested_content_category is not None
            else default_x_category(asset.media_type)
        ),
        acquisition_source=AcquisitionSource.X_MANUAL_CLAIM,
        display_title=display_title,
        creator_attribution_kind=creator_kind,
        creator_stable_id=creator_stable_id,
        creator_handle=creator_handle,
        creator_display_name=creator_display_name,
        description=description,
        tag_keys=tag_keys,
    )


def automatic_analysis_allowed_for_upload(
    repository: XAcquisitionClaimRepository,
    upload_id: UploadSessionId,
    identity_mapping: Mapping[str, IdentityMappingEntry],
) -> bool:
    """Allow automatic analysis only for administrator-owned linked X uploads.

    This helper does not read FRAMENEST_AUTOMATIC_MEDIA_ANALYSIS_ENABLED.
    The scheduler ``enabled`` flag remains the enqueue gate.
    """
    try:
        if repository.find_asset_by_upload_id(upload_id) is None:
            return True
        claim = repository.find_post_by_upload_id(upload_id)
        if claim is None or claim.created_by_login_key is None:
            return False
        login_key = normalize_login(claim.created_by_login_key)
        entry = identity_mapping.get(login_key)
        if entry is None:
            return False
        return entry.role == ROLE_ADMIN
    except Exception:
        return False


def _imported_display_title(title: str | None) -> MediaDisplayTitle | None:
    if title is None:
        return None
    candidate = title[:MAX_DISPLAY_TITLE_CODE_POINTS].rstrip()
    if not candidate:
        return None
    try:
        return MediaDisplayTitle(candidate)
    except FrameNestMediaMetadataError:
        return None


def _administration_snapshot(
    claim: XPostClaim, assets: tuple[XAsset, ...]
) -> XClaimSnapshot:
    return XClaimSnapshot(
        claim_id=claim.id.to_string(),
        state=claim.state.value,
        phase=derive_x_requester_phase(claim),
        x_post_id=claim.x_post_id,
        submitted_url=claim.submitted_url,
        canonical_url=claim.canonical_url,
        title=claim.title,
        source_author_handle=claim.source_author_handle,
        source_author_display_name=claim.source_author_display_name,
        discovered_asset_count=claim.discovered_asset_count,
        success_count=claim.success_count,
        failure_count=claim.failure_count,
        failure_stage=None if claim.failure_stage is None else claim.failure_stage.value,
        failure_code=claim.failure_code,
        retry_of_claim_id=None if claim.retry_of_claim_id is None else claim.retry_of_claim_id.to_string(),
        created_at_ms=claim.created_at_ms,
        updated_at_ms=claim.updated_at_ms,
        completed_at_ms=claim.completed_at_ms,
        requested_content_category=(
            None
            if claim.requested_content_category is None
            else claim.requested_content_category.value
        ),
        can_retry=_retry_eligible(claim, assets),
        assets=tuple(_asset_snapshot(a) for a in assets),
    )


def _retry_eligible(
    claim: XPostClaim, assets: tuple[XAsset, ...]
) -> bool:
    """Requester retry truth derived from durable claim/asset state.

    Mirrors XAcquisitionRequestService.retry eligibility: a claim is retryable
    only when its terminal state is retryable and a failed/incomplete asset (or,
    for a FAILED claim, an absent discovery) remains to be re-acquired.
    Successful cataloged assets are never the basis for retry.
    """
    if claim.state is XAcquisitionState.COMPLETED_PARTIAL:
        if claim.failure_count <= 0:
            return False
        return any(_asset_retryable(a) for a in assets)
    if claim.state is XAcquisitionState.FAILED:
        if not assets:
            return True
        return any(_asset_retryable(a) for a in assets)
    return False


def _requester_snapshot(
    claim: XPostClaim, repository: XAcquisitionClaimRepository
) -> XClaimSnapshot:
    assets = repository.list_assets_for_post(claim.id)
    snapshot = _administration_snapshot(claim, assets)
    hidden = _hidden_media_ids(
        repository,
        claim.created_by_login_key or "",
        tuple(
            asset.media_id.to_string()
            for asset in assets
            if asset.media_id is not None
        ),
    )
    if not hidden:
        return snapshot
    return replace(
        snapshot,
        assets=tuple(
            replace(item, media_id=None) if item.media_id in hidden else item
            for item in snapshot.assets
        ),
    )


def _hidden_media_ids(
    repository: object,
    login_key: str,
    media_ids: tuple[str, ...],
) -> frozenset[str]:
    hide = getattr(repository, "media_ids_hidden_from_requester", None)
    if hide is None or not media_ids:
        return frozenset()
    return frozenset(hide(login_key=login_key, media_ids=media_ids))


def _asset_snapshot(asset: XAsset) -> XAssetSnapshot:
    return XAssetSnapshot(
        asset_id=asset.id.to_string(),
        ordinal=asset.ordinal,
        media_type=asset.media_type.value,
        state=asset.state.value,
        acquired_bytes=asset.acquired_bytes,
        media_id=None if asset.media_id is None else asset.media_id.to_string(),
        failure_stage=None if asset.failure_stage is None else asset.failure_stage.value,
        failure_code=asset.failure_code,
        created_at_ms=asset.created_at_ms,
        updated_at_ms=asset.updated_at_ms,
    )


def _submission(
    claim: XPostClaim, *, submission_result: str, requester: str
) -> XRequestSubmission:
    return XRequestSubmission(
        request_id=claim.id.to_string(),
        submission_result=submission_result,
        phase=derive_x_requester_phase(claim),
        x_post_id=claim.x_post_id,
        submitted_url=claim.submitted_url,
    )


def _normalize_requester(login_key: str) -> str:
    from framenest.domain.identity_access import normalize_login

    try:
        return normalize_login(login_key)
    except Exception as exc:
        raise XAcquisitionInvalidRequesterIdentityError(
            "Invalid requester identity."
        ) from exc


def _asset_retryable(asset: XAsset) -> bool:
    if asset.state is XAssetState.FAILED:
        return is_retryable_x_failure(asset.failure_code)
    if asset.state in ACTIVE_X_ASSET_STATES:
        return True
    return False


def _bound_post_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    from framenest.domain.media_metadata import MAX_DISPLAY_TITLE_CODE_POINTS

    return value.strip()[:MAX_DISPLAY_TITLE_CODE_POINTS]


def _media_type_label(media_type: XMediaType) -> str:
    return {XMediaType.VIDEO: "video", XMediaType.ANIMATED_GIF: "video", XMediaType.IMAGE: "image"}[media_type]


def _stage_for_extractor_code(code: str) -> XFailureStage:
    if code in {"X_AUTHENTICATION_REQUIRED", "X_POST_UNAVAILABLE", "X_POST_DELETED", "X_POST_PROTECTED"}:
        return XFailureStage.EXTRACTION
    return XFailureStage.EXTRACTION


def _code_from_exception(exc: Exception) -> str:
    if isinstance(exc, _AssetTooLarge):
        return "X_MEDIA_TOO_LARGE"
    if isinstance(exc, XExtractionError):
        return exc.code
    if isinstance(exc, XExtractorConfigurationError):
        return "X_EXTRACTOR_UNAVAILABLE"
    if isinstance(exc, UploadTransportError):
        return "X_CATALOG_HANDOFF_FAILED"
    if isinstance(exc, FrameNestXClaimRepositoryError):
        return "X_STAGING_FAILED"
    return "X_EXTRACTOR_FAILED"


def _extension_for_mime(expected_mime: str) -> str:
    return {
        "video/mp4": "mp4",
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
    }.get(expected_mime, "bin")


def _one_chunk(chunk: bytes) -> AsyncIterator[bytes]:
    async def _iterator() -> AsyncIterator[bytes]:
        yield chunk

    return _iterator()


def _notify(coordinator: object) -> None:
    notify = getattr(coordinator, "notify", None)
    if callable(notify):
        notify()


def _safe_log(**fields: object) -> None:
    try:
        LOGGER.emit(**fields)
    except Exception:
        return


def _log_unexpected_runner_death() -> None:
    _safe_log(
        level="ERROR",
        event="x_acquisition_runner_unexpected_death",
        operation="x_acquisition_run",
        error_code="X_ACQUISITION_RUNNER_UNEXPECTED_DEATH",
        retryable=False,
    )


ACTIVE_X_ASSET_STATES = frozenset(
    {
        XAssetState.PENDING,
        XAssetState.EXTRACTED,
        XAssetState.ACQUIRING,
        XAssetState.STAGED,
        XAssetState.HANDING_OFF,
    }
)

_AWAITING_ACQUISITION_ASSET_STATES = frozenset(
    {
        XAssetState.PENDING,
        XAssetState.EXTRACTED,
        XAssetState.ACQUIRING,
    }
)