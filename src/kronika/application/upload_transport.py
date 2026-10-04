"""Application-owned resumable upload transport into quarantine storage."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import Lock
import time
import uuid

from kronika.application.ports.library_repository import LibraryRepository
from kronika.application.ports.quarantine_storage import (
    QuarantineStateInconsistentError,
    QuarantineStorage,
    QuarantineStorageInsufficientSpaceError,
    QuarantineStorageUnavailableError,
    QuarantineWriteFailedError,
)
from kronika.application.ports.upload_sessions import (
    FrameNestUploadSessionRepositoryError,
    IncompleteUploadSessionError,
    InvalidUploadSessionTransitionError,
    UploadOffsetConflictError,
    UploadSessionAlreadyExistsError,
    UploadSessionConcurrencyConflictError,
    UploadSessionNotFoundError,
    UploadSessionRepository,
    UploadSizeLimitExceededError,
    UploadStorageKeyAlreadyExistsError,
)
from kronika.application.root_paths import roots_overlap
from kronika.domain import LibraryPathFlavor
from kronika.domain.uploads import (
    FrameNestUploadSessionError,
    UploadDuplicateDisposition,
    UploadDuplicateResolutionMode,
    UploadDisplayFilename,
    UploadSession,
    UploadSessionId,
    UploadSessionState,
    UploadStorageKey,
    uses_explicit_duplicate_resolution,
)

UPLOAD_FAILED_STORAGE_INCONSISTENT = "QUARANTINE_STATE_INCONSISTENT"


class UploadTransportError(RuntimeError):
    """Base sanitized upload transport error."""


class UploadCapabilityNotConfiguredError(UploadTransportError):
    """Raised when upload transport is disabled or unavailable."""


class UploadSessionNotFoundTransportError(UploadTransportError):
    """Raised when the requested upload session is absent."""


class UploadInvalidMetadataError(UploadTransportError):
    """Raised when create-session metadata is invalid."""


class UploadTooLargeError(UploadTransportError):
    """Raised when declared total upload size exceeds policy."""


class UploadChunkTooLargeError(UploadTransportError):
    """Raised when a PATCH chunk exceeds policy."""


class UploadInvalidOffsetError(UploadTransportError):
    """Raised when a supplied upload offset is malformed."""


class UploadOffsetConflictTransportError(UploadTransportError):
    """Raised when the supplied offset differs from the authoritative offset."""

    def __init__(self, current_offset: int) -> None:
        super().__init__("upload offset conflict")
        self.current_offset = current_offset


class UploadSessionStateConflictError(UploadTransportError):
    """Raised when the session state does not permit the requested mutation."""


class UploadSessionExpiredError(UploadTransportError):
    """Raised when a mutation observes an expired non-terminal session."""


class UploadInsufficientStorageError(UploadTransportError):
    """Raised when quarantine storage has insufficient free space."""


class UploadBodyLengthMismatchError(UploadTransportError):
    """Raised when streamed bytes do not match Content-Length."""


class UploadQuarantineUnavailableError(UploadTransportError):
    """Raised when quarantine storage cannot be used safely."""


class UploadQuarantineStateInconsistentError(UploadTransportError):
    """Raised when persisted and filesystem state cannot be reconciled."""


class UploadConcurrencyConflictError(UploadTransportError):
    """Raised when optimistic repository guards reject a mutation."""


class UploadDuplicateResolution(StrEnum):
    """Explicit user dispositions for one exact-byte duplicate upload."""

    KEEP_SEPARATE = "keep_separate"
    DISCARD = "discard"


@dataclass(frozen=True, slots=True)
class UploadTransportLimits:
    max_total_bytes: int
    max_patch_bytes: int
    session_ttl_seconds: int
    min_free_space_reserve_bytes: int


@dataclass(frozen=True, slots=True)
class UploadSessionSnapshot:
    id: str
    state: str
    display_filename: str
    declared_size_bytes: int
    received_size_bytes: int
    expires_at: int
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class UploadCapabilitySnapshot:
    uploads_enabled: bool
    max_total_size_bytes: int
    max_chunk_size_bytes: int
    session_ttl_seconds: int


@dataclass(slots=True)
class _SessionLockEntry:
    lock: asyncio.Lock
    references: int = 0


def default_now_ms() -> int:
    """Return current wall-clock milliseconds for durable upload timestamps."""
    return int(time.time() * 1000)


class UploadSessionLockRegistry:
    """Bounded in-process per-session lock registry for one Uvicorn worker."""

    def __init__(self) -> None:
        self._locks: dict[str, _SessionLockEntry] = {}
        self._guard = Lock()

    @asynccontextmanager
    async def lease(self, session_id: UploadSessionId) -> AsyncIterator[None]:
        key = session_id.to_string()
        with self._guard:
            entry = self._locks.get(key)
            if entry is None:
                entry = _SessionLockEntry(asyncio.Lock())
                self._locks[key] = entry
            entry.references += 1
        acquired = False
        try:
            await entry.lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                entry.lock.release()
            with self._guard:
                entry.references -= 1
                if entry.references == 0 and not entry.lock.locked():
                    self._locks.pop(key, None)

    def size(self) -> int:
        """Return current registry entries for bounded diagnostics and tests."""
        with self._guard:
            return len(self._locks)


class UploadTransportService:
    """Coordinate durable upload sessions with server-owned quarantine bytes."""

    def __init__(
        self,
        repository: UploadSessionRepository,
        storage: QuarantineStorage | None,
        library_repository: LibraryRepository,
        limits: UploadTransportLimits,
        *,
        quarantine_root: Path | None,
        preview_cache_root: Path | None = None,
        now_ms: Callable[[], int] = default_now_ms,
        locks: UploadSessionLockRegistry | None = None,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._library_repository = library_repository
        self._limits = limits
        self._quarantine_root = quarantine_root
        self._preview_cache_root = preview_cache_root
        self._now_ms = now_ms
        self._locks = locks or UploadSessionLockRegistry()

    def create_session(
        self,
        *,
        display_filename: object,
        declared_size_bytes: object,
        session_id: UploadSessionId | None = None,
        storage_key: UploadStorageKey | None = None,
        created_by_login_key: str | None = None,
        duplicate_resolution_mode: UploadDuplicateResolutionMode = (
            UploadDuplicateResolutionMode.EXPLICIT
        ),
    ) -> UploadSessionSnapshot:
        storage = self._require_storage()
        try:
            filename = UploadDisplayFilename(display_filename)
            if isinstance(declared_size_bytes, bool) or not isinstance(declared_size_bytes, int):
                raise FrameNestUploadSessionError("invalid upload size")
            if declared_size_bytes <= 0:
                raise FrameNestUploadSessionError("invalid upload size")
            if session_id is not None and not isinstance(
                session_id, UploadSessionId
            ):
                raise FrameNestUploadSessionError("invalid upload identity")
            if storage_key is not None and not isinstance(
                storage_key, UploadStorageKey
            ):
                raise FrameNestUploadSessionError("invalid upload identity")
            if not isinstance(
                duplicate_resolution_mode, UploadDuplicateResolutionMode
            ):
                raise FrameNestUploadSessionError("invalid upload identity")
        except FrameNestUploadSessionError as exc:
            raise UploadInvalidMetadataError("invalid upload metadata") from exc
        if declared_size_bytes > self._limits.max_total_bytes:
            raise UploadTooLargeError("upload too large")
        self._ensure_root_separation()
        self._ensure_space(declared_size_bytes)
        now_ms = self._now_ms()
        session = UploadSession(
            id=session_id or UploadSessionId.new(),
            state=UploadSessionState.CREATED,
            storage_key=storage_key or UploadStorageKey(uuid.uuid4().hex),
            display_filename=filename,
            declared_size_bytes=declared_size_bytes,
            received_size_bytes=0,
            checksum_algorithm=None,
            checksum_hex=None,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
            expires_at_ms=now_ms + self._limits.session_ttl_seconds * 1000,
            failure_code=None,
            version=0,
            created_by_login_key=created_by_login_key,
            duplicate_resolution_mode=duplicate_resolution_mode,
        )
        try:
            self._repository.create(session)
        except (
            UploadSessionAlreadyExistsError,
            UploadStorageKeyAlreadyExistsError,
        ) as exc:
            if session_id is None or storage_key is None:
                raise UploadQuarantineUnavailableError(
                    "upload storage unavailable"
                ) from exc
            try:
                existing = self._repository.get(session_id)
            except FrameNestUploadSessionRepositoryError as load_exc:
                raise UploadQuarantineUnavailableError(
                    "upload storage unavailable"
                ) from load_exc
            if (
                existing is None
                or existing.storage_key != storage_key
                or existing.display_filename != filename
                or existing.declared_size_bytes != declared_size_bytes
                or existing.created_by_login_key != created_by_login_key
                or existing.duplicate_resolution_mode != duplicate_resolution_mode
            ):
                raise UploadQuarantineUnavailableError(
                    "upload storage unavailable"
                ) from exc
            return _snapshot(existing)
        except FrameNestUploadSessionRepositoryError as exc:
            raise UploadQuarantineUnavailableError("upload storage unavailable") from exc
        return _snapshot(session)

    def get_capability(self) -> UploadCapabilitySnapshot:
        uploads_enabled = False
        try:
            self._require_storage()
            self._ensure_root_separation()
            uploads_enabled = True
        except UploadTransportError:
            uploads_enabled = False
        return UploadCapabilitySnapshot(
            uploads_enabled=uploads_enabled,
            max_total_size_bytes=self._limits.max_total_bytes,
            max_chunk_size_bytes=self._limits.max_patch_bytes,
            session_ttl_seconds=self._limits.session_ttl_seconds,
        )

    def get_status(self, session_id: UploadSessionId) -> UploadSessionSnapshot:
        session = self._load(session_id)
        return _snapshot(session)

    async def receive_chunk(
        self,
        session_id: UploadSessionId,
        *,
        upload_offset: int,
        content_length: int,
        body: AsyncIterator[bytes],
    ) -> UploadSessionSnapshot:
        storage = self._require_storage()
        if upload_offset < 0:
            raise UploadInvalidOffsetError("invalid upload offset")
        if content_length <= 0:
            raise UploadBodyLengthMismatchError("upload body length mismatch")
        if content_length > self._limits.max_patch_bytes:
            raise UploadChunkTooLargeError("upload chunk too large")
        self._ensure_space(content_length)

        async with self._locks.lease(session_id):
            session = self._load(session_id)
            session = self._expire_if_needed(session)
            if session.received_size_bytes != upload_offset:
                raise UploadOffsetConflictTransportError(session.received_size_bytes)
            if session.state is UploadSessionState.CREATED:
                if upload_offset != 0:
                    raise UploadOffsetConflictTransportError(session.received_size_bytes)
                session = self._transition(
                    session,
                    UploadSessionState.RECEIVING,
                    failure_code=None,
                )
            if session.state is not UploadSessionState.RECEIVING:
                raise UploadSessionStateConflictError("upload session state conflict")
            if upload_offset + content_length > session.declared_size_bytes:
                raise UploadChunkTooLargeError("upload chunk too large")

            self._reconcile_file_before_write(storage, session)
            original_offset = session.received_size_bytes
            writer = storage.open_writer(
                session.storage_key,
                offset=original_offset,
                create=original_offset == 0 and storage.file_size(session.storage_key) is None,
            )
            actual = 0
            try:
                async for chunk in body:
                    if not chunk:
                        continue
                    actual += len(chunk)
                    if actual > content_length:
                        raise UploadBodyLengthMismatchError("upload body length mismatch")
                    written = writer.write(chunk)
                    if written != len(chunk):
                        raise UploadQuarantineUnavailableError("upload storage unavailable")
                if actual != content_length:
                    raise UploadBodyLengthMismatchError("upload body length mismatch")
                writer.flush_and_fsync()
                updated = self._repository.advance_received_offset(
                    session.id,
                    expected_received_size_bytes=original_offset,
                    accepted_size_bytes=actual,
                    expected_version=session.version,
                    updated_at_ms=self._now_ms(),
                )
            except (
                UploadBodyLengthMismatchError,
                UploadQuarantineUnavailableError,
                UploadChunkTooLargeError,
            ) as exc:
                self._recover_failed_patch(storage, writer, session, original_offset, exc)
            except QuarantineWriteFailedError as exc:
                self._recover_failed_patch(
                    storage,
                    writer,
                    session,
                    original_offset,
                    UploadQuarantineUnavailableError("upload storage unavailable"),
                )
            except UploadOffsetConflictError as exc:
                self._recover_failed_patch(
                    storage,
                    writer,
                    session,
                    original_offset,
                    UploadOffsetConflictTransportError(original_offset),
                    allow_advanced_offset_conflict=True,
                )
            except UploadSizeLimitExceededError as exc:
                self._recover_failed_patch(
                    storage,
                    writer,
                    session,
                    original_offset,
                    UploadChunkTooLargeError("upload chunk too large"),
                )
            except (
                UploadSessionConcurrencyConflictError,
                InvalidUploadSessionTransitionError,
                FrameNestUploadSessionRepositoryError,
            ) as exc:
                self._recover_failed_patch(
                    storage,
                    writer,
                    session,
                    original_offset,
                    UploadConcurrencyConflictError("upload concurrency conflict"),
                )
            except Exception as exc:
                self._recover_failed_patch(
                    storage,
                    writer,
                    session,
                    original_offset,
                    UploadBodyLengthMismatchError("upload body length mismatch"),
                )
            try:
                writer.close()
            except Exception as exc:
                self._recover_failed_patch(
                    storage,
                    writer,
                    session,
                    original_offset,
                    UploadQuarantineUnavailableError("upload storage unavailable"),
                    writer_close_attempted=True,
                )
            return _snapshot(updated)

    async def complete(self, session_id: UploadSessionId) -> UploadSessionSnapshot:
        storage = self._require_storage()
        async with self._locks.lease(session_id):
            session = self._load(session_id)
            session = self._expire_if_needed(session)
            if session.state is UploadSessionState.RECEIVED:
                self._ensure_complete_file(storage, session)
                return _snapshot(session)
            if session.state is not UploadSessionState.RECEIVING:
                raise UploadSessionStateConflictError("upload session state conflict")
            if session.received_size_bytes != session.declared_size_bytes:
                self._ensure_file_matches_received_size(storage, session)
                raise UploadSessionStateConflictError("upload session state conflict")
            self._ensure_complete_file(storage, session)
            updated = self._transition(session, UploadSessionState.RECEIVED, failure_code=None)
            return _snapshot(updated)

    async def cancel(self, session_id: UploadSessionId) -> UploadSessionSnapshot:
        storage = self._require_storage()
        async with self._locks.lease(session_id):
            session = self._load(session_id)
            return _snapshot(self._cancel_owned(storage, session))

    async def resolve_duplicate(
        self,
        session_id: UploadSessionId,
        resolution: UploadDuplicateResolution,
    ) -> UploadSessionSnapshot:
        """Apply one explicit, idempotent disposition to a duplicate upload."""
        storage = self._require_storage()
        async with self._locks.lease(session_id):
            session = self._load(session_id)
            # Privacy-safe sessions never expose duplicate-resolution outcomes:
            # reject before branching on disposition or explicit idempotency.
            if not uses_explicit_duplicate_resolution(
                session.duplicate_resolution_mode
            ):
                raise UploadSessionStateConflictError("upload session state conflict")
            if resolution is UploadDuplicateResolution.KEEP_SEPARATE:
                if (
                    session.duplicate_disposition
                    is UploadDuplicateDisposition.KEEP_SEPARATE
                    and session.state
                    in {
                        UploadSessionState.PUBLISH_PENDING,
                        UploadSessionState.PUBLISHED,
                        UploadSessionState.CATALOGED,
                    }
                ):
                    return _snapshot(session)
                if (
                    session.state is not UploadSessionState.DUPLICATE_PENDING
                    or session.duplicate_disposition is not None
                ):
                    raise UploadSessionStateConflictError("upload session state conflict")
                kept = self._record_duplicate_disposition(
                    session,
                    UploadDuplicateDisposition.KEEP_SEPARATE,
                )
                return _snapshot(kept)
            if resolution is UploadDuplicateResolution.DISCARD:
                if (
                    session.state is UploadSessionState.CANCELLED
                    and session.duplicate_disposition
                    is UploadDuplicateDisposition.DISCARD
                ):
                    self._remove_cancelled_file(storage, session)
                    return _snapshot(session)
                if (
                    session.state is not UploadSessionState.DUPLICATE_PENDING
                    or session.duplicate_disposition is not None
                ):
                    raise UploadSessionStateConflictError("upload session state conflict")
                discarded = self._record_duplicate_disposition(
                    session,
                    UploadDuplicateDisposition.DISCARD,
                )
                self._remove_cancelled_file(storage, discarded)
                return _snapshot(discarded)
            raise UploadSessionStateConflictError("upload session state conflict")

    def _cancel_owned(
        self,
        storage: QuarantineStorage,
        session: UploadSession,
    ) -> UploadSession:
        if session.state is UploadSessionState.CANCELLED:
            self._remove_cancelled_file(storage, session)
            return session
        if session.state in {
            UploadSessionState.EXPIRED,
            UploadSessionState.FAILED,
            UploadSessionState.CATALOGED,
            UploadSessionState.REJECTED,
        }:
            raise UploadSessionStateConflictError("upload session state conflict")
        cancelled = self._transition(session, UploadSessionState.CANCELLED, failure_code=None)
        self._remove_cancelled_file(storage, cancelled)
        return cancelled

    def _load(self, session_id: UploadSessionId) -> UploadSession:
        try:
            session = self._repository.get(session_id)
        except FrameNestUploadSessionRepositoryError as exc:
            raise UploadQuarantineUnavailableError("upload storage unavailable") from exc
        if session is None:
            raise UploadSessionNotFoundTransportError("upload session not found")
        return session

    def load_session(self, session_id: UploadSessionId) -> UploadSession:
        """Return one durable upload session or raise the transport not-found error."""
        return self._load(session_id)

    def _require_storage(self) -> QuarantineStorage:
        storage = self._storage
        if storage is None or not storage.root_available:
            raise UploadCapabilityNotConfiguredError("upload capability not configured")
        return storage

    def _ensure_root_separation(self) -> None:
        if self._quarantine_root is None:
            raise UploadCapabilityNotConfiguredError("upload capability not configured")
        if self._preview_cache_root is not None and roots_overlap(
            self._quarantine_root,
            self._preview_cache_root,
        ):
            raise UploadCapabilityNotConfiguredError("upload capability not configured")
        for library in self._library_repository.list_all():
            if library.root.flavor is not LibraryPathFlavor.POSIX:
                continue
            if roots_overlap(self._quarantine_root, Path(library.root.path)):
                raise UploadCapabilityNotConfiguredError("upload capability not configured")

    def _ensure_space(self, requested_bytes: int) -> None:
        try:
            available = self._require_storage().available_bytes()
        except (
            QuarantineStorageUnavailableError,
            QuarantineStorageInsufficientSpaceError,
        ) as exc:
            raise UploadInsufficientStorageError("insufficient quarantine storage") from exc
        if requested_bytes + self._limits.min_free_space_reserve_bytes > available:
            raise UploadInsufficientStorageError("insufficient quarantine storage")

    def _expire_if_needed(self, session: UploadSession) -> UploadSession:
        if session.state in {
            UploadSessionState.CATALOGED,
            UploadSessionState.REJECTED,
            UploadSessionState.CANCELLED,
            UploadSessionState.EXPIRED,
            UploadSessionState.FAILED,
        }:
            return session
        if self._now_ms() <= session.expires_at_ms:
            return session
        try:
            expired = self._transition(session, UploadSessionState.EXPIRED, failure_code=None)
        except UploadTransportError:
            raise
        raise UploadSessionExpiredError(expired.state.value)

    def _transition(
        self,
        session: UploadSession,
        target: UploadSessionState,
        *,
        failure_code: str | None,
    ) -> UploadSession:
        try:
            return self._repository.transition_state(
                session.id,
                expected_state=session.state,
                target_state=target,
                expected_version=session.version,
                updated_at_ms=self._now_ms(),
                failure_code=failure_code,
            )
        except UploadSessionNotFoundError as exc:
            raise UploadSessionNotFoundTransportError("upload session not found") from exc
        except IncompleteUploadSessionError as exc:
            raise UploadQuarantineStateInconsistentError("quarantine state inconsistent") from exc
        except InvalidUploadSessionTransitionError as exc:
            raise UploadSessionStateConflictError("upload session state conflict") from exc
        except UploadSessionConcurrencyConflictError as exc:
            raise UploadConcurrencyConflictError("upload concurrency conflict") from exc
        except FrameNestUploadSessionRepositoryError as exc:
            raise UploadQuarantineUnavailableError("upload storage unavailable") from exc

    def _record_duplicate_disposition(
        self,
        session: UploadSession,
        disposition: UploadDuplicateDisposition,
    ) -> UploadSession:
        try:
            return self._repository.record_duplicate_disposition(
                session.id,
                expected_version=session.version,
                disposition=disposition,
                updated_at_ms=self._now_ms(),
            )
        except UploadSessionNotFoundError as exc:
            raise UploadSessionNotFoundTransportError("upload session not found") from exc
        except InvalidUploadSessionTransitionError as exc:
            raise UploadSessionStateConflictError("upload session state conflict") from exc
        except UploadSessionConcurrencyConflictError as exc:
            raise UploadConcurrencyConflictError("upload concurrency conflict") from exc
        except FrameNestUploadSessionRepositoryError as exc:
            raise UploadQuarantineUnavailableError("upload storage unavailable") from exc

    def _reconcile_file_before_write(
        self,
        storage: QuarantineStorage,
        session: UploadSession,
    ) -> None:
        try:
            size = storage.file_size(session.storage_key)
        except QuarantineStateInconsistentError as exc:
            raise UploadQuarantineStateInconsistentError("quarantine state inconsistent") from exc
        except QuarantineStorageUnavailableError as exc:
            raise UploadQuarantineUnavailableError("upload storage unavailable") from exc
        if size is None:
            if session.received_size_bytes == 0:
                return
            self._fail_inconsistent_session(session)
        if size == session.received_size_bytes:
            return
        if size > session.received_size_bytes:
            try:
                storage.truncate(session.storage_key, session.received_size_bytes)
            except QuarantineWriteFailedError as exc:
                raise UploadQuarantineUnavailableError("upload storage unavailable") from exc
            return
        self._fail_inconsistent_session(session)

    def _ensure_complete_file(
        self,
        storage: QuarantineStorage,
        session: UploadSession,
    ) -> None:
        try:
            size = storage.file_size(session.storage_key)
        except QuarantineStateInconsistentError as exc:
            self._fail_inconsistent_session(session)
            raise UploadQuarantineStateInconsistentError("quarantine state inconsistent") from exc
        if size != session.declared_size_bytes or size != session.received_size_bytes:
            if size is not None and size > session.received_size_bytes:
                storage.truncate(session.storage_key, session.received_size_bytes)
            self._fail_inconsistent_session(session)

    def _ensure_file_matches_received_size(
        self,
        storage: QuarantineStorage,
        session: UploadSession,
    ) -> None:
        try:
            size = storage.file_size(session.storage_key)
        except QuarantineStateInconsistentError as exc:
            self._fail_inconsistent_session(session)
            raise UploadQuarantineStateInconsistentError("quarantine state inconsistent") from exc
        except QuarantineStorageUnavailableError as exc:
            raise UploadQuarantineUnavailableError("upload storage unavailable") from exc
        if size != session.received_size_bytes:
            if size is not None and size > session.received_size_bytes:
                storage.truncate(session.storage_key, session.received_size_bytes)
            self._fail_inconsistent_session(session)

    def _fail_inconsistent_session(self, session: UploadSession) -> None:
        try:
            self._transition(
                session,
                UploadSessionState.FAILED,
                failure_code=UPLOAD_FAILED_STORAGE_INCONSISTENT,
            )
        except UploadSessionStateConflictError:
            pass
        raise UploadQuarantineStateInconsistentError("quarantine state inconsistent")

    def _remove_cancelled_file(
        self,
        storage: QuarantineStorage,
        session: UploadSession,
    ) -> None:
        try:
            storage.remove(session.storage_key)
        except QuarantineWriteFailedError as exc:
            raise UploadQuarantineUnavailableError("upload storage unavailable") from exc

    def _recover_failed_patch(
        self,
        storage: QuarantineStorage,
        writer: object,
        session: UploadSession,
        original_offset: int,
        original_error: UploadTransportError,
        *,
        allow_advanced_offset_conflict: bool = False,
        writer_close_attempted: bool = False,
    ) -> None:
        close_attempted = writer_close_attempted
        try:
            current = self._repository.get(session.id)
        except FrameNestUploadSessionRepositoryError as exc:
            current = None
            load_error: Exception | None = exc
        else:
            load_error = None

        if current is not None and current.storage_key == session.storage_key:
            if current.received_size_bytes > original_offset:
                self._recover_advanced_patch(
                    storage,
                    writer,
                    current,
                    close_attempted=close_attempted,
                )
            if current.received_size_bytes != original_offset:
                try:
                    if not close_attempted:
                        close_attempted = True
                        _close_writer(writer)
                    if allow_advanced_offset_conflict:
                        raise UploadOffsetConflictTransportError(current.received_size_bytes)
                    raise UploadQuarantineStateInconsistentError(
                        "quarantine state inconsistent"
                    )
                except UploadOffsetConflictTransportError:
                    raise
                except Exception as exc:
                    self._fail_session_after_unverified_rollback(session.id)
                    raise UploadQuarantineStateInconsistentError(
                        "quarantine state inconsistent"
                    ) from exc

        try:
            if current is None or current.storage_key != session.storage_key:
                if not close_attempted:
                    close_attempted = True
                    _close_writer(writer)
                raise UploadQuarantineStateInconsistentError(
                    "quarantine state inconsistent"
                )

            writer.truncate_and_fsync(original_offset)
            size = storage.file_size(session.storage_key)
            if size != original_offset:
                raise UploadQuarantineStateInconsistentError("quarantine state inconsistent")
            after = self._repository.get(session.id)
            if after is None or after.received_size_bytes != original_offset:
                raise UploadQuarantineStateInconsistentError("quarantine state inconsistent")
            close_attempted = True
            _close_writer(writer)
        except UploadOffsetConflictTransportError:
            raise
        except Exception as exc:
            if not close_attempted:
                try:
                    _close_writer(writer)
                except Exception:
                    pass
            self._fail_session_after_unverified_rollback(session.id)
            raise UploadQuarantineStateInconsistentError(
                "quarantine state inconsistent"
            ) from load_error or exc
        raise original_error

    def _recover_advanced_patch(
        self,
        storage: QuarantineStorage,
        writer: object,
        current: UploadSession,
        *,
        close_attempted: bool,
    ) -> None:
        close_error: Exception | None = None
        if not close_attempted:
            try:
                _close_writer(writer)
            except Exception as exc:
                close_error = exc

        try:
            size = storage.file_size(current.storage_key)
        except (QuarantineStateInconsistentError, QuarantineStorageUnavailableError) as exc:
            raise UploadQuarantineStateInconsistentError(
                "quarantine state inconsistent"
            ) from close_error or exc

        if size == current.received_size_bytes:
            raise UploadOffsetConflictTransportError(current.received_size_bytes)
        if size is None or size < current.received_size_bytes:
            self._fail_authoritative_inconsistent_session(current)
        if size > current.received_size_bytes:
            try:
                storage.truncate(current.storage_key, current.received_size_bytes)
                verified_size = storage.file_size(current.storage_key)
            except (
                QuarantineWriteFailedError,
                QuarantineStateInconsistentError,
                QuarantineStorageUnavailableError,
            ) as exc:
                raise UploadQuarantineStateInconsistentError(
                    "quarantine state inconsistent"
                ) from close_error or exc
            if verified_size != current.received_size_bytes:
                raise UploadQuarantineStateInconsistentError(
                    "quarantine state inconsistent"
                )
            raise UploadOffsetConflictTransportError(current.received_size_bytes)

        raise UploadQuarantineStateInconsistentError("quarantine state inconsistent")

    def _fail_authoritative_inconsistent_session(self, current: UploadSession) -> None:
        try:
            self._transition(
                current,
                UploadSessionState.FAILED,
                failure_code=UPLOAD_FAILED_STORAGE_INCONSISTENT,
            )
        except UploadTransportError:
            pass
        raise UploadQuarantineStateInconsistentError("quarantine state inconsistent")

    def _fail_session_after_unverified_rollback(self, session_id: UploadSessionId) -> None:
        try:
            current = self._repository.get(session_id)
        except FrameNestUploadSessionRepositoryError:
            return
        if current is None or current.state in {
            UploadSessionState.CANCELLED,
            UploadSessionState.EXPIRED,
            UploadSessionState.FAILED,
            UploadSessionState.CATALOGED,
            UploadSessionState.REJECTED,
            UploadSessionState.PUBLISHED,
        }:
            return
        try:
            self._transition(
                current,
                UploadSessionState.FAILED,
                failure_code=UPLOAD_FAILED_STORAGE_INCONSISTENT,
            )
        except UploadTransportError:
            pass


def _snapshot(session: UploadSession) -> UploadSessionSnapshot:
    return UploadSessionSnapshot(
        id=session.id.to_string(),
        state=session.state.value,
        display_filename=session.display_filename.value,
        declared_size_bytes=session.declared_size_bytes,
        received_size_bytes=session.received_size_bytes,
        expires_at=session.expires_at_ms,
        failure_code=session.failure_code,
    )


def _close_writer(writer: object) -> None:
    writer.close()
