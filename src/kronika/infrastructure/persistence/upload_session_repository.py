"""SQLAlchemy Core adapter for durable upload sessions."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kronika.application.ports.upload_sessions import (
    FrameNestUploadSessionRepositoryError,
    IncompleteUploadSessionError,
    InvalidUploadChecksumError,
    InvalidUploadSessionTransitionError,
    InvalidUploadValidationEvidenceError,
    UploadOffsetConflictError,
    UploadSessionAlreadyExistsError,
    UploadSessionConcurrencyConflictError,
    UploadSessionNotFoundError,
    UploadSizeLimitExceededError,
    UploadStorageKeyAlreadyExistsError,
)
from kronika.domain.uploads import (
    COMPLETE_UPLOAD_SESSION_STATES,
    FrameNestUploadChecksumError,
    FrameNestUploadOffsetError,
    FrameNestUploadSessionError,
    FrameNestUploadSessionTransitionError,
    FrameNestUploadValidationEvidenceError,
    UploadDuplicateDisposition,
    UploadDuplicateResolutionMode,
    UploadDisplayFilename,
    UploadSession,
    UploadSessionId,
    UploadSessionState,
    UploadStorageKey,
    UploadValidatedFormat,
    UploadValidatedMediaKind,
    VALIDATED_UPLOAD_SESSION_STATES,
    ensure_upload_session_transition_allowed,
    uses_explicit_duplicate_resolution,
    validate_sha256_checksum_hex,
    validate_upload_failure_code,
    validate_upload_offset_advance,
    validate_upload_validation_evidence,
)
from kronika.domain.identities import FrameNestIdentityError, MediaByteIdentityId
from kronika.domain.media_byte_identities import (
    FrameNestMediaByteIdentityError,
    MediaByteIdentity,
    validate_media_byte_identity_evidence,
)
from kronika.infrastructure.persistence.catalog_schema import (
    media_byte_identities,
    upload_publications,
    upload_sessions,
)
from kronika.infrastructure.persistence.engine import (
    run_in_immediate_transaction,
    run_in_transaction,
)

_REPOSITORY_FAILURE_MESSAGE = "Upload session operation failed."
_FAILURE_STATES = frozenset({UploadSessionState.FAILED, UploadSessionState.REJECTED})


class SqliteUploadSessionRepository:
    """Synchronous SQLite upload-session adapter."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, session: UploadSession) -> None:
        def operation(connection: Connection) -> None:
            if _row_by_id(connection, session.id) is not None:
                raise UploadSessionAlreadyExistsError("upload session already exists")
            if _row_by_storage_key(connection, session.storage_key.value) is not None:
                raise UploadStorageKeyAlreadyExistsError("upload storage key already exists")
            values = _values_from_session(session)
            if session.byte_identity_id is not None:
                identity = _get_or_create_byte_identity(
                    connection,
                    MediaByteIdentity(
                        id=session.byte_identity_id,
                        checksum_algorithm=session.checksum_algorithm,
                        size_bytes=session.declared_size_bytes,
                        checksum_hex=session.checksum_hex,
                        created_at_ms=session.updated_at_ms,
                    ),
                )
                values["byte_identity_id"] = identity.id.to_string()
            connection.execute(insert(upload_sessions).values(values))

        try:
            run_in_transaction(self._engine, operation)
        except (
            UploadSessionAlreadyExistsError,
            UploadStorageKeyAlreadyExistsError,
        ):
            raise
        except IntegrityError as exc:
            raise FrameNestUploadSessionRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc
        except SQLAlchemyError as exc:
            raise FrameNestUploadSessionRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def get(self, session_id: UploadSessionId) -> UploadSession | None:
        def operation(connection: Connection) -> UploadSession | None:
            row = _row_by_id(connection, session_id)
            if row is None:
                return None
            return _session_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestUploadSessionRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestUploadSessionRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def list_startup_validation_candidates(
        self,
        *,
        limit: int,
        after_updated_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[UploadSession, ...]:
        """Return bounded received and startup-abandoned validating uploads."""
        return self._list_validation_candidates(
            states=(UploadSessionState.RECEIVED, UploadSessionState.VALIDATING),
            limit=limit,
            after_updated_at_ms=after_updated_at_ms,
            after_id=after_id,
        )

    def list_runtime_validation_candidates(
        self,
        *,
        limit: int,
        after_updated_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[UploadSession, ...]:
        """Return bounded received uploads in deterministic order."""
        return self._list_validation_candidates(
            states=(UploadSessionState.RECEIVED,),
            limit=limit,
            after_updated_at_ms=after_updated_at_ms,
            after_id=after_id,
        )

    def _list_validation_candidates(
        self,
        *,
        states: tuple[UploadSessionState, ...],
        limit: int,
        after_updated_at_ms: int | None,
        after_id: str | None,
    ) -> tuple[UploadSession, ...]:
        try:
            _validate_positive(limit)
            _validate_candidate_cursor(after_updated_at_ms, after_id)
        except FrameNestUploadSessionError as exc:
            raise FrameNestUploadSessionRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

        def operation(connection: Connection) -> tuple[UploadSession, ...]:
            filters = [
                upload_sessions.c.state.in_(tuple(state.value for state in states)),
            ]
            if after_updated_at_ms is not None:
                assert after_id is not None
                filters.append(
                    or_(
                        upload_sessions.c.updated_at_ms > after_updated_at_ms,
                        and_(
                            upload_sessions.c.updated_at_ms == after_updated_at_ms,
                            upload_sessions.c.id > after_id,
                        ),
                    )
                )
            rows = (
                connection.execute(
                    select(upload_sessions)
                    .where(and_(*filters))
                    .order_by(upload_sessions.c.updated_at_ms.asc(), upload_sessions.c.id.asc())
                    .limit(limit)
                )
                .mappings()
                .all()
            )
            return tuple(_session_from_row(row) for row in rows)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestUploadSessionRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def advance_received_offset(
        self,
        session_id: UploadSessionId,
        *,
        expected_received_size_bytes: int,
        accepted_size_bytes: int,
        expected_version: int,
        updated_at_ms: int,
    ) -> UploadSession:
        try:
            validate_upload_offset_advance(
                current_offset=expected_received_size_bytes,
                accepted_bytes=accepted_size_bytes,
                declared_size_bytes=max(expected_received_size_bytes + accepted_size_bytes, 1),
            )
            _validate_non_negative(expected_version)
            _validate_non_negative(updated_at_ms)
        except (FrameNestUploadOffsetError, FrameNestUploadSessionError) as exc:
            raise UploadOffsetConflictError("upload offset conflict") from exc

        def operation(connection: Connection) -> UploadSession:
            next_offset = expected_received_size_bytes + accepted_size_bytes
            result = connection.execute(
                update(upload_sessions)
                .where(
                    and_(
                        upload_sessions.c.id == session_id.to_string(),
                        upload_sessions.c.state == UploadSessionState.RECEIVING.value,
                        upload_sessions.c.received_size_bytes == expected_received_size_bytes,
                        upload_sessions.c.version == expected_version,
                        upload_sessions.c.declared_size_bytes >= next_offset,
                    )
                )
                .values(
                    received_size_bytes=next_offset,
                    updated_at_ms=updated_at_ms,
                    version=upload_sessions.c.version + 1,
                )
            )
            if result.rowcount == 1:
                return _require_session(connection, session_id)
            row = _row_by_id(connection, session_id)
            _raise_offset_guard_error(
                row,
                expected_received_size_bytes=expected_received_size_bytes,
                expected_version=expected_version,
                next_offset=next_offset,
            )

        try:
            return run_in_transaction(self._engine, operation)
        except (
            UploadSessionNotFoundError,
            InvalidUploadSessionTransitionError,
            UploadOffsetConflictError,
            UploadSizeLimitExceededError,
            UploadSessionConcurrencyConflictError,
        ):
            raise
        except SQLAlchemyError as exc:
            raise FrameNestUploadSessionRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def record_completed_checksum(
        self,
        session_id: UploadSessionId,
        *,
        expected_state: UploadSessionState,
        expected_version: int,
        checksum_hex: str,
        updated_at_ms: int,
    ) -> UploadSession:
        try:
            checksum = validate_sha256_checksum_hex(checksum_hex)
            _validate_non_negative(expected_version)
            _validate_non_negative(updated_at_ms)
        except FrameNestUploadChecksumError as exc:
            raise InvalidUploadChecksumError("invalid upload checksum") from exc
        except FrameNestUploadSessionError as exc:
            raise UploadSessionConcurrencyConflictError(
                "upload session concurrency conflict"
            ) from exc

        def operation(connection: Connection) -> UploadSession:
            result = connection.execute(
                update(upload_sessions)
                .where(
                    and_(
                        upload_sessions.c.id == session_id.to_string(),
                        upload_sessions.c.state == expected_state.value,
                        upload_sessions.c.version == expected_version,
                        upload_sessions.c.checksum_algorithm.is_(None),
                        upload_sessions.c.checksum_hex.is_(None),
                    )
                )
                .values(
                    checksum_algorithm="sha256",
                    checksum_hex=checksum,
                    updated_at_ms=updated_at_ms,
                    version=upload_sessions.c.version + 1,
                )
            )
            if result.rowcount == 1:
                return _require_session(connection, session_id)
            row = _row_by_id(connection, session_id)
            if row is None:
                raise UploadSessionNotFoundError("upload session not found")
            if str(row["state"]) != expected_state.value:
                raise InvalidUploadSessionTransitionError(
                    "invalid upload session transition"
                )
            if int(row["version"]) != expected_version:
                raise UploadSessionConcurrencyConflictError(
                    "upload session concurrency conflict"
                )
            if row["checksum_algorithm"] == "sha256" and row["checksum_hex"] == checksum:
                return _session_from_row(row)
            raise InvalidUploadChecksumError("invalid upload checksum")

        try:
            return run_in_transaction(self._engine, operation)
        except (
            UploadSessionNotFoundError,
            InvalidUploadSessionTransitionError,
            UploadSessionConcurrencyConflictError,
            InvalidUploadChecksumError,
        ):
            raise
        except SQLAlchemyError as exc:
            raise FrameNestUploadSessionRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def transition_state(
        self,
        session_id: UploadSessionId,
        *,
        expected_state: UploadSessionState,
        target_state: UploadSessionState,
        expected_version: int,
        updated_at_ms: int,
        failure_code: str | None = None,
    ) -> UploadSession:
        try:
            ensure_upload_session_transition_allowed(expected_state, target_state)
            _validate_non_negative(expected_version)
            _validate_non_negative(updated_at_ms)
            if failure_code is not None:
                validate_upload_failure_code(failure_code)
                if target_state not in _FAILURE_STATES:
                    raise FrameNestUploadSessionTransitionError(
                        "failure code is valid only for failure states"
                    )
        except FrameNestUploadSessionTransitionError as exc:
            raise InvalidUploadSessionTransitionError(
                "invalid upload session transition"
            ) from exc
        except FrameNestUploadSessionError as exc:
            raise UploadSessionConcurrencyConflictError(
                "upload session concurrency conflict"
            ) from exc

        def operation(connection: Connection) -> UploadSession:
            update_guards = [
                upload_sessions.c.id == session_id.to_string(),
                upload_sessions.c.state == expected_state.value,
                upload_sessions.c.version == expected_version,
            ]
            if _target_requires_complete_upload(target_state):
                update_guards.append(
                    upload_sessions.c.received_size_bytes
                    == upload_sessions.c.declared_size_bytes
                )
            if target_state in VALIDATED_UPLOAD_SESSION_STATES:
                update_guards.extend(
                    [
                        upload_sessions.c.checksum_algorithm == "sha256",
                        upload_sessions.c.checksum_hex.is_not(None),
                        upload_sessions.c.validated_media_kind.is_not(None),
                        upload_sessions.c.validated_format.is_not(None),
                        upload_sessions.c.byte_identity_id.is_not(None),
                    ]
                )
            result = connection.execute(
                update(upload_sessions)
                .where(and_(*update_guards))
                .values(
                    state=target_state.value,
                    updated_at_ms=updated_at_ms,
                    failure_code=failure_code,
                    version=upload_sessions.c.version + 1,
                )
            )
            if result.rowcount == 1:
                return _require_session(connection, session_id)
            row = _row_by_id(connection, session_id)
            if row is None:
                raise UploadSessionNotFoundError("upload session not found")
            if str(row["state"]) != expected_state.value:
                raise InvalidUploadSessionTransitionError(
                    "invalid upload session transition"
                )
            if int(row["version"]) != expected_version:
                raise UploadSessionConcurrencyConflictError(
                    "upload session concurrency conflict"
                )
            if _target_requires_complete_upload(target_state) and int(
                row["received_size_bytes"]
            ) != int(row["declared_size_bytes"]):
                raise IncompleteUploadSessionError("incomplete upload session")
            if target_state in VALIDATED_UPLOAD_SESSION_STATES and not _row_has_validation_evidence(
                row
            ):
                raise InvalidUploadValidationEvidenceError(
                    "invalid upload validation evidence"
                )
            raise UploadSessionConcurrencyConflictError(
                "upload session concurrency conflict"
            )

        try:
            return run_in_transaction(self._engine, operation)
        except (
            UploadSessionNotFoundError,
            IncompleteUploadSessionError,
            InvalidUploadSessionTransitionError,
            UploadSessionConcurrencyConflictError,
            InvalidUploadValidationEvidenceError,
        ):
            raise
        except SQLAlchemyError as exc:
            raise FrameNestUploadSessionRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def start_validation(
        self,
        session_id: UploadSessionId,
        *,
        expected_version: int,
        updated_at_ms: int,
    ) -> UploadSession:
        """Atomically transition one received upload into validation."""
        try:
            _validate_non_negative(expected_version)
            _validate_non_negative(updated_at_ms)
        except FrameNestUploadSessionError as exc:
            raise UploadSessionConcurrencyConflictError(
                "upload session concurrency conflict"
            ) from exc

        def operation(connection: Connection) -> UploadSession:
            result = connection.execute(
                update(upload_sessions)
                .where(
                    and_(
                        upload_sessions.c.id == session_id.to_string(),
                        upload_sessions.c.state == UploadSessionState.RECEIVED.value,
                        upload_sessions.c.version == expected_version,
                        upload_sessions.c.received_size_bytes
                        == upload_sessions.c.declared_size_bytes,
                    )
                )
                .values(
                    state=UploadSessionState.VALIDATING.value,
                    updated_at_ms=updated_at_ms,
                    version=upload_sessions.c.version + 1,
                )
            )
            if result.rowcount == 1:
                return _require_session(connection, session_id)
            row = _row_by_id(connection, session_id)
            if row is None:
                raise UploadSessionNotFoundError("upload session not found")
            if str(row["state"]) != UploadSessionState.RECEIVED.value:
                raise InvalidUploadSessionTransitionError(
                    "invalid upload session transition"
                )
            if int(row["version"]) != expected_version:
                raise UploadSessionConcurrencyConflictError(
                    "upload session concurrency conflict"
                )
            if int(row["received_size_bytes"]) != int(row["declared_size_bytes"]):
                raise IncompleteUploadSessionError("incomplete upload session")
            raise UploadSessionConcurrencyConflictError(
                "upload session concurrency conflict"
            )

        try:
            return run_in_transaction(self._engine, operation)
        except (
            UploadSessionNotFoundError,
            IncompleteUploadSessionError,
            InvalidUploadSessionTransitionError,
            UploadSessionConcurrencyConflictError,
        ):
            raise
        except SQLAlchemyError as exc:
            raise FrameNestUploadSessionRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def complete_validation_success(
        self,
        session_id: UploadSessionId,
        *,
        expected_version: int,
        checksum_hex: str,
        validated_media_kind: UploadValidatedMediaKind,
        validated_format: UploadValidatedFormat,
        updated_at_ms: int,
    ) -> UploadSession:
        """Atomically persist validation evidence, identity, and duplicate disposition."""
        try:
            checksum = validate_sha256_checksum_hex(checksum_hex)
            media_kind, media_format = validate_upload_validation_evidence(
                media_kind=validated_media_kind,
                media_format=validated_format,
            )
            _validate_non_negative(expected_version)
            _validate_non_negative(updated_at_ms)
        except FrameNestUploadChecksumError as exc:
            raise InvalidUploadChecksumError("invalid upload checksum") from exc
        except FrameNestUploadValidationEvidenceError as exc:
            raise InvalidUploadValidationEvidenceError(
                "invalid upload validation evidence"
            ) from exc
        except FrameNestUploadSessionError as exc:
            raise UploadSessionConcurrencyConflictError(
                "upload session concurrency conflict"
            ) from exc

        def operation(connection: Connection) -> UploadSession:
            row = _row_by_id(connection, session_id)
            if row is None:
                raise UploadSessionNotFoundError("upload session not found")
            if _row_has_matching_validation_success_evidence(
                row,
                checksum_hex=checksum,
                validated_media_kind=media_kind,
                validated_format=media_format,
            ):
                if not _row_byte_identity_matches_upload_evidence(connection, row):
                    raise InvalidUploadValidationEvidenceError(
                        "invalid upload validation evidence"
                    )
                return _session_from_row(row)
            if str(row["state"]) in {
                state.value for state in VALIDATED_UPLOAD_SESSION_STATES
            }:
                if row["checksum_algorithm"] != "sha256" or row["checksum_hex"] != checksum:
                    raise InvalidUploadChecksumError("invalid upload checksum")
                raise InvalidUploadValidationEvidenceError(
                    "invalid upload validation evidence"
                )
            if str(row["state"]) != UploadSessionState.VALIDATING.value:
                raise InvalidUploadSessionTransitionError(
                    "invalid upload session transition"
                )
            if int(row["version"]) != expected_version:
                raise UploadSessionConcurrencyConflictError(
                    "upload session concurrency conflict"
                )
            if int(row["received_size_bytes"]) != int(row["declared_size_bytes"]):
                raise IncompleteUploadSessionError("incomplete upload session")
            if row["checksum_algorithm"] is not None or row["checksum_hex"] is not None:
                raise InvalidUploadChecksumError("invalid upload checksum")
            if row["validated_media_kind"] is not None or row["validated_format"] is not None:
                raise InvalidUploadValidationEvidenceError(
                    "invalid upload validation evidence"
                )
            identity = _get_or_create_byte_identity(
                connection,
                MediaByteIdentity(
                    id=MediaByteIdentityId.new(),
                    checksum_algorithm="sha256",
                    size_bytes=int(row["declared_size_bytes"]),
                    checksum_hex=checksum,
                    created_at_ms=updated_at_ms,
                ),
            )
            canonical_exists = connection.execute(
                select(upload_sessions.c.id)
                .select_from(
                    upload_sessions.outerjoin(
                        upload_publications,
                        upload_publications.c.upload_id == upload_sessions.c.id,
                    )
                )
                .where(
                    and_(
                        upload_sessions.c.id != session_id.to_string(),
                        upload_sessions.c.byte_identity_id == identity.id.to_string(),
                        or_(
                            upload_sessions.c.state.in_(
                                (
                                    UploadSessionState.PUBLISH_PENDING.value,
                                    UploadSessionState.PUBLISHED.value,
                                )
                            ),
                            and_(
                                upload_sessions.c.state
                                == UploadSessionState.CATALOGED.value,
                                upload_publications.c.media_id.is_not(None),
                            ),
                        ),
                    )
                )
                .limit(1)
            ).first() is not None
            target_state = UploadSessionState.PUBLISH_PENDING
            duplicate_disposition_value = None
            if canonical_exists:
                if uses_explicit_duplicate_resolution(row["duplicate_resolution_mode"]):
                    target_state = UploadSessionState.DUPLICATE_PENDING
                else:
                    target_state = UploadSessionState.PUBLISH_PENDING
                    duplicate_disposition_value = (
                        UploadDuplicateDisposition.KEEP_SEPARATE.value
                    )
            result = connection.execute(
                update(upload_sessions)
                .where(
                    and_(
                        upload_sessions.c.id == session_id.to_string(),
                        upload_sessions.c.state == UploadSessionState.VALIDATING.value,
                        upload_sessions.c.version == expected_version,
                        upload_sessions.c.received_size_bytes
                        == upload_sessions.c.declared_size_bytes,
                        upload_sessions.c.checksum_algorithm.is_(None),
                        upload_sessions.c.checksum_hex.is_(None),
                        upload_sessions.c.validated_media_kind.is_(None),
                        upload_sessions.c.validated_format.is_(None),
                        upload_sessions.c.byte_identity_id.is_(None),
                    )
                )
                .values(
                    state=target_state.value,
                    checksum_algorithm="sha256",
                    checksum_hex=checksum,
                    validated_media_kind=media_kind.value,
                    validated_format=media_format.value,
                    byte_identity_id=identity.id.to_string(),
                    duplicate_disposition=duplicate_disposition_value,
                    updated_at_ms=updated_at_ms,
                    version=upload_sessions.c.version + 1,
                )
            )
            if result.rowcount == 1:
                return _require_session(connection, session_id)
            raise UploadSessionConcurrencyConflictError(
                "upload session concurrency conflict"
            )

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except (
            UploadSessionNotFoundError,
            IncompleteUploadSessionError,
            InvalidUploadSessionTransitionError,
            UploadSessionConcurrencyConflictError,
            InvalidUploadChecksumError,
            InvalidUploadValidationEvidenceError,
        ):
            raise
        except SQLAlchemyError as exc:
            raise FrameNestUploadSessionRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def record_duplicate_disposition(
        self,
        session_id: UploadSessionId,
        *,
        expected_version: int,
        disposition: UploadDuplicateDisposition,
        updated_at_ms: int,
    ) -> UploadSession:
        """Atomically persist one explicit duplicate disposition and target state."""
        try:
            if not isinstance(disposition, UploadDuplicateDisposition):
                raise FrameNestUploadSessionTransitionError(
                    "invalid upload session transition"
                )
            _validate_non_negative(expected_version)
            _validate_non_negative(updated_at_ms)
        except FrameNestUploadSessionTransitionError as exc:
            raise InvalidUploadSessionTransitionError(
                "invalid upload session transition"
            ) from exc
        except FrameNestUploadSessionError as exc:
            raise UploadSessionConcurrencyConflictError(
                "upload session concurrency conflict"
            ) from exc

        target_state = (
            UploadSessionState.PUBLISH_PENDING
            if disposition is UploadDuplicateDisposition.KEEP_SEPARATE
            else UploadSessionState.CANCELLED
        )

        def operation(connection: Connection) -> UploadSession:
            result = connection.execute(
                update(upload_sessions)
                .where(
                    and_(
                        upload_sessions.c.id == session_id.to_string(),
                        upload_sessions.c.state == UploadSessionState.DUPLICATE_PENDING.value,
                        upload_sessions.c.version == expected_version,
                        upload_sessions.c.duplicate_disposition.is_(None),
                    )
                )
                .values(
                    state=target_state.value,
                    duplicate_disposition=disposition.value,
                    updated_at_ms=updated_at_ms,
                    version=upload_sessions.c.version + 1,
                )
            )
            if result.rowcount == 1:
                return _require_session(connection, session_id)
            row = _row_by_id(connection, session_id)
            if row is None:
                raise UploadSessionNotFoundError("upload session not found")
            if str(row["state"]) != UploadSessionState.DUPLICATE_PENDING.value:
                raise InvalidUploadSessionTransitionError(
                    "invalid upload session transition"
                )
            if row["duplicate_disposition"] is not None:
                raise InvalidUploadSessionTransitionError(
                    "invalid upload session transition"
                )
            if int(row["version"]) != expected_version:
                raise UploadSessionConcurrencyConflictError(
                    "upload session concurrency conflict"
                )
            raise UploadSessionConcurrencyConflictError(
                "upload session concurrency conflict"
            )

        try:
            return run_in_transaction(self._engine, operation)
        except (
            UploadSessionNotFoundError,
            InvalidUploadSessionTransitionError,
            UploadSessionConcurrencyConflictError,
        ):
            raise
        except SQLAlchemyError as exc:
            raise FrameNestUploadSessionRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def get_or_create_byte_identity(
        self,
        identity: MediaByteIdentity,
    ) -> MediaByteIdentity:
        """Race-safely return the canonical exact-byte identity for evidence."""

        def operation(connection: Connection) -> MediaByteIdentity:
            return _get_or_create_byte_identity(connection, identity)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestMediaByteIdentityError as exc:
            raise InvalidUploadChecksumError("invalid upload checksum") from exc
        except SQLAlchemyError as exc:
            raise FrameNestUploadSessionRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def reject_validation(
        self,
        session_id: UploadSessionId,
        *,
        expected_version: int,
        failure_code: str,
        updated_at_ms: int,
    ) -> UploadSession:
        """Atomically reject one validating upload with a sanitized code."""
        try:
            sanitized_failure_code = validate_upload_failure_code(failure_code)
            _validate_non_negative(expected_version)
            _validate_non_negative(updated_at_ms)
        except FrameNestUploadSessionError as exc:
            raise UploadSessionConcurrencyConflictError(
                "upload session concurrency conflict"
            ) from exc

        def operation(connection: Connection) -> UploadSession:
            result = connection.execute(
                update(upload_sessions)
                .where(
                    and_(
                        upload_sessions.c.id == session_id.to_string(),
                        upload_sessions.c.state == UploadSessionState.VALIDATING.value,
                        upload_sessions.c.version == expected_version,
                    )
                )
                .values(
                    state=UploadSessionState.REJECTED.value,
                    failure_code=sanitized_failure_code,
                    updated_at_ms=updated_at_ms,
                    version=upload_sessions.c.version + 1,
                )
            )
            if result.rowcount == 1:
                return _require_session(connection, session_id)
            row = _row_by_id(connection, session_id)
            if row is None:
                raise UploadSessionNotFoundError("upload session not found")
            if str(row["state"]) != UploadSessionState.VALIDATING.value:
                raise InvalidUploadSessionTransitionError(
                    "invalid upload session transition"
                )
            if int(row["version"]) != expected_version:
                raise UploadSessionConcurrencyConflictError(
                    "upload session concurrency conflict"
                )
            raise UploadSessionConcurrencyConflictError(
                "upload session concurrency conflict"
            )

        try:
            return run_in_transaction(self._engine, operation)
        except (
            UploadSessionNotFoundError,
            InvalidUploadSessionTransitionError,
            UploadSessionConcurrencyConflictError,
        ):
            raise
        except SQLAlchemyError as exc:
            raise FrameNestUploadSessionRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc


def _row_by_id(
    connection: Connection,
    session_id: UploadSessionId,
) -> Mapping[str, object] | None:
    return (
        connection.execute(
            select(upload_sessions).where(upload_sessions.c.id == session_id.to_string())
        )
        .mappings()
        .first()
    )


def _row_by_storage_key(
    connection: Connection,
    storage_key: str,
) -> Mapping[str, object] | None:
    return (
        connection.execute(
            select(upload_sessions).where(upload_sessions.c.storage_key == storage_key)
        )
        .mappings()
        .first()
    )


def _require_session(connection: Connection, session_id: UploadSessionId) -> UploadSession:
    row = _row_by_id(connection, session_id)
    if row is None:
        raise UploadSessionNotFoundError("upload session not found")
    return _session_from_row(row)


def _row_has_matching_validation_success_evidence(
    row: Mapping[str, object],
    *,
    checksum_hex: str,
    validated_media_kind: UploadValidatedMediaKind,
    validated_format: UploadValidatedFormat,
) -> bool:
    return (
        str(row["state"])
        in {state.value for state in VALIDATED_UPLOAD_SESSION_STATES}
        and row["checksum_algorithm"] == "sha256"
        and row["checksum_hex"] == checksum_hex
        and row["validated_media_kind"] == validated_media_kind.value
        and row["validated_format"] == validated_format.value
        and row["byte_identity_id"] is not None
    )


def _row_has_validation_evidence(row: Mapping[str, object]) -> bool:
    return (
        row["checksum_algorithm"] == "sha256"
        and row["checksum_hex"] is not None
        and row["validated_media_kind"] is not None
        and row["validated_format"] is not None
        and row["byte_identity_id"] is not None
    )


def _row_byte_identity_matches_upload_evidence(
    connection: Connection,
    row: Mapping[str, object],
) -> bool:
    if row["byte_identity_id"] is None:
        return False
    identity = (
        connection.execute(
            select(media_byte_identities).where(
                media_byte_identities.c.id == str(row["byte_identity_id"])
            )
        )
        .mappings()
        .first()
    )
    return (
        identity is not None
        and identity["checksum_algorithm"] == row["checksum_algorithm"]
        and int(identity["size_bytes"]) == int(row["declared_size_bytes"])
        and identity["checksum_hex"] == row["checksum_hex"]
    )


def _raise_offset_guard_error(
    row: Mapping[str, object] | None,
    *,
    expected_received_size_bytes: int,
    expected_version: int,
    next_offset: int,
) -> None:
    if row is None:
        raise UploadSessionNotFoundError("upload session not found")
    if str(row["state"]) != UploadSessionState.RECEIVING.value:
        raise InvalidUploadSessionTransitionError("invalid upload session transition")
    if int(row["received_size_bytes"]) != expected_received_size_bytes:
        raise UploadOffsetConflictError("upload offset conflict")
    if int(row["version"]) != expected_version:
        raise UploadSessionConcurrencyConflictError("upload session concurrency conflict")
    if next_offset > int(row["declared_size_bytes"]):
        raise UploadSizeLimitExceededError("upload size limit exceeded")
    raise FrameNestUploadSessionRepositoryError(_REPOSITORY_FAILURE_MESSAGE)


def _values_from_session(session: UploadSession) -> dict[str, object]:
    return {
        "id": session.id.to_string(),
        "state": session.state.value,
        "storage_key": session.storage_key.value,
        "display_filename": session.display_filename.value,
        "declared_size_bytes": session.declared_size_bytes,
        "received_size_bytes": session.received_size_bytes,
        "checksum_algorithm": session.checksum_algorithm,
        "checksum_hex": session.checksum_hex,
        "validated_media_kind": None
        if session.validated_media_kind is None
        else session.validated_media_kind.value,
        "validated_format": None
        if session.validated_format is None
        else session.validated_format.value,
        "byte_identity_id": None
        if session.byte_identity_id is None
        else session.byte_identity_id.to_string(),
        "duplicate_disposition": None
        if session.duplicate_disposition is None
        else session.duplicate_disposition.value,
        "created_by_login_key": session.created_by_login_key,
        "duplicate_resolution_mode": session.duplicate_resolution_mode.value,
        "created_at_ms": session.created_at_ms,
        "updated_at_ms": session.updated_at_ms,
        "expires_at_ms": session.expires_at_ms,
        "failure_code": session.failure_code,
        "version": session.version,
    }


def _session_from_row(row: Mapping[str, object]) -> UploadSession:
    try:
        return UploadSession(
            id=UploadSessionId.from_string(str(row["id"])),
            state=UploadSessionState(str(row["state"])),
            storage_key=UploadStorageKey(str(row["storage_key"])),
            display_filename=UploadDisplayFilename(str(row["display_filename"])),
            declared_size_bytes=int(row["declared_size_bytes"]),
            received_size_bytes=int(row["received_size_bytes"]),
            checksum_algorithm=None
            if row["checksum_algorithm"] is None
            else str(row["checksum_algorithm"]),
            checksum_hex=None if row["checksum_hex"] is None else str(row["checksum_hex"]),
            validated_media_kind=None
            if row["validated_media_kind"] is None
            else UploadValidatedMediaKind(str(row["validated_media_kind"])),
            validated_format=None
            if row["validated_format"] is None
            else UploadValidatedFormat(str(row["validated_format"])),
            byte_identity_id=None
            if row["byte_identity_id"] is None
            else MediaByteIdentityId.from_string(str(row["byte_identity_id"])),
            duplicate_disposition=None
            if row["duplicate_disposition"] is None
            else UploadDuplicateDisposition(str(row["duplicate_disposition"])),
            created_by_login_key=None
            if row["created_by_login_key"] is None
            else str(row["created_by_login_key"]),
            duplicate_resolution_mode=_duplicate_resolution_mode_from_row(
                row["duplicate_resolution_mode"]
            ),
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=int(row["updated_at_ms"]),
            expires_at_ms=int(row["expires_at_ms"]),
            failure_code=None if row["failure_code"] is None else str(row["failure_code"]),
            version=int(row["version"]),
        )
    except (FrameNestIdentityError, FrameNestUploadSessionError, ValueError) as exc:
        raise FrameNestUploadSessionRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc


def _duplicate_resolution_mode_from_row(value: object) -> UploadDuplicateResolutionMode:
    if value is None:
        return UploadDuplicateResolutionMode.SILENT_KEEP_SEPARATE
    try:
        return UploadDuplicateResolutionMode(str(value))
    except ValueError:
        return UploadDuplicateResolutionMode.SILENT_KEEP_SEPARATE


def _get_or_create_byte_identity(
    connection: Connection,
    identity: MediaByteIdentity,
) -> MediaByteIdentity:
    validate_media_byte_identity_evidence(
        checksum_algorithm=identity.checksum_algorithm,
        size_bytes=identity.size_bytes,
        checksum_hex=identity.checksum_hex,
    )
    statement = (
        sqlite_insert(media_byte_identities)
        .values(
            id=identity.id.to_string(),
            checksum_algorithm=identity.checksum_algorithm,
            size_bytes=identity.size_bytes,
            checksum_hex=identity.checksum_hex,
            created_at_ms=identity.created_at_ms,
        )
        .on_conflict_do_nothing(
            index_elements=[
                media_byte_identities.c.checksum_algorithm,
                media_byte_identities.c.size_bytes,
                media_byte_identities.c.checksum_hex,
            ]
        )
    )
    connection.execute(statement)
    row = (
        connection.execute(
            select(media_byte_identities).where(
                and_(
                    media_byte_identities.c.checksum_algorithm
                    == identity.checksum_algorithm,
                    media_byte_identities.c.size_bytes == identity.size_bytes,
                    media_byte_identities.c.checksum_hex == identity.checksum_hex,
                )
            )
        )
        .mappings()
        .one()
    )
    return MediaByteIdentity(
        id=MediaByteIdentityId.from_string(str(row["id"])),
        checksum_algorithm=str(row["checksum_algorithm"]),
        size_bytes=int(row["size_bytes"]),
        checksum_hex=str(row["checksum_hex"]),
        created_at_ms=int(row["created_at_ms"]),
    )


def _target_requires_complete_upload(target_state: UploadSessionState) -> bool:
    return target_state in COMPLETE_UPLOAD_SESSION_STATES


def _validate_non_negative(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FrameNestUploadSessionError("invalid concurrency guard")


def _validate_positive(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FrameNestUploadSessionError("invalid bounded retrieval limit")


def _validate_candidate_cursor(
    after_updated_at_ms: object,
    after_id: object,
) -> None:
    if after_updated_at_ms is None and after_id is None:
        return
    if (
        isinstance(after_updated_at_ms, bool)
        or not isinstance(after_updated_at_ms, int)
        or after_updated_at_ms < 0
        or not isinstance(after_id, str)
        or not after_id
    ):
        raise FrameNestUploadSessionError("invalid validation candidate cursor")
