"""SQLAlchemy Core adapter for durable requester-private X claims and assets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kronika.application.ports.x_acquisition import (
    FrameNestXClaimRepositoryError,
    XClaimAlreadyExistsError,
    XClaimConcurrencyConflictError,
    XClaimNotFoundError,
    XClaimSourceIdentityConflictError,
)
from kronika.domain.identities import (
    MediaId,
    XAssetId,
    XPostClaimId,
)
from kronika.domain.media_classification import ContentCategory
from kronika.domain.media_metadata import CanonicalTagKey, MediaDescription, MediaDisplayTitle
from kronika.domain.media_user_alias import MediaUserAliasContent, PendingMediaUserAlias
from kronika.domain.uploads import UploadSessionId
from kronika.domain.x_acquisition import (
    ACTIVE_X_ACQUISITION_STATES,
    LIVE_CATALOG_X_ACQUISITION_STATES,
    SUCCESS_X_ASSET_STATES,
    XAcquisitionState,
    XAsset,
    XAssetState,
    XFailureStage,
    XMediaType,
    XPostClaim,
    XStagingCleanupState,
    ensure_x_asset_transition_allowed,
    ensure_x_transition_allowed,
)
from kronika.infrastructure.persistence.catalog_schema import (
    kronika_records,
    x_assets,
    x_claim_pending_alias_tags,
    x_claim_pending_aliases,
    x_post_claims,
)
from kronika.infrastructure.persistence.engine import (
    run_in_immediate_transaction,
    run_in_transaction,
)

_REPOSITORY_FAILURE_MESSAGE = "X acquisition claim operation failed."
_ACTIVE_POST_VALUES = tuple(state.value for state in ACTIVE_X_ACQUISITION_STATES)
_LIVE_SUCCESS_VALUES = tuple(
    state.value for state in LIVE_CATALOG_X_ACQUISITION_STATES
)
_SUCCESS_POST_VALUES = (
    XAcquisitionState.COMPLETED.value,
    XAcquisitionState.COMPLETED_PARTIAL.value,
    XAcquisitionState.DUPLICATE_RESOLVED.value,
)
_ASSET_SUCCESS_VALUES = tuple(state.value for state in SUCCESS_X_ASSET_STATES)


class SqliteXAcquisitionClaimRepository:
    """Synchronous SQLite adapter with transactional requester ownership."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_or_get_active(
        self,
        claim: XPostClaim,
    ) -> tuple[XPostClaim, bool]:
        if claim.state is not XAcquisitionState.SUBMITTED:
            raise FrameNestXClaimRepositoryError(_REPOSITORY_FAILURE_MESSAGE)

        def operation(
            connection: Connection,
        ) -> tuple[XPostClaim, bool]:
            if _post_row_by_id(connection, claim.id) is not None:
                raise XClaimAlreadyExistsError("claim already exists")
            active = _active_row_by_source(
                connection,
                post_id_key=claim.x_post_id,
                created_by_login_key=claim.created_by_login_key,
            )
            if active is not None:
                return _post_from_row(active), False
            connection.execute(
                insert(x_post_claims).values(_post_values(claim))
            )
            return claim, True

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except XClaimAlreadyExistsError:
            raise
        except IntegrityError as exc:
            try:
                existing = self.find_active_by_post_id(
                    post_id_key=claim.x_post_id,
                    created_by_login_key=claim.created_by_login_key,
                )
            except FrameNestXClaimRepositoryError:
                raise FrameNestXClaimRepositoryError(
                    _REPOSITORY_FAILURE_MESSAGE
                ) from exc
            if existing is not None:
                return existing, False
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def create_post(self, claim: XPostClaim) -> None:
        def operation(connection: Connection) -> None:
            if _post_row_by_id(connection, claim.id) is not None:
                raise XClaimAlreadyExistsError("claim already exists")
            connection.execute(insert(x_post_claims).values(_post_values(claim)))

        try:
            run_in_transaction(self._engine, operation)
        except XClaimAlreadyExistsError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def get_post(self, post_id: XPostClaimId) -> XPostClaim | None:
        def operation(connection: Connection) -> XPostClaim | None:
            row = _post_row_by_id(connection, post_id)
            return None if row is None else _post_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def find_active_by_post_id(
        self,
        *,
        post_id_key: str,
        created_by_login_key: str | None = None,
    ) -> XPostClaim | None:
        def operation(connection: Connection) -> XPostClaim | None:
            row = _active_row_by_source(
                connection,
                post_id_key=post_id_key,
                created_by_login_key=created_by_login_key,
            )
            return None if row is None else _post_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def find_owned_live_successful_by_post_id(
        self,
        *,
        post_id_key: str,
        created_by_login_key: str,
    ) -> XPostClaim | None:
        def operation(connection: Connection) -> XPostClaim | None:
            stmt = (
                select(x_post_claims)
                .where(
                    x_post_claims.c.x_post_id == post_id_key,
                    x_post_claims.c.created_by_login_key == created_by_login_key,
                    x_post_claims.c.state.in_(LIVE_CATALOG_X_ACQUISITION_STATES),
                )
                .order_by(x_post_claims.c.updated_at_ms.desc())
                .limit(1)
            )
            row = connection.execute(stmt).first()
            return None if row is None else _post_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def find_owned_successful_by_post_id(
        self,
        *,
        post_id_key: str,
        created_by_login_key: str,
    ) -> XPostClaim | None:
        def operation(connection: Connection) -> XPostClaim | None:
            stmt = (
                select(x_post_claims)
                .where(
                    x_post_claims.c.x_post_id == post_id_key,
                    x_post_claims.c.created_by_login_key == created_by_login_key,
                    x_post_claims.c.state.in_(_SUCCESS_POST_VALUES),
                )
                .order_by(x_post_claims.c.updated_at_ms.desc())
                .limit(1)
            )
            row = connection.execute(stmt).first()
            return None if row is None else _post_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def find_post_by_upload_id(
        self,
        upload_id: UploadSessionId,
    ) -> XPostClaim | None:
        def operation(connection: Connection) -> XPostClaim | None:
            stmt = (
                select(x_post_claims)
                .select_from(
                    x_post_claims.join(
                        x_assets, x_assets.c.claim_id == x_post_claims.c.id
                    )
                )
                .where(x_assets.c.id == upload_id.to_string())
                .limit(1)
            )
            row = connection.execute(stmt).first()
            return None if row is None else _post_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def find_asset_by_upload_id(
        self,
        upload_id: UploadSessionId,
    ) -> XAsset | None:
        def operation(connection: Connection) -> XAsset | None:
            stmt = select(x_assets).where(x_assets.c.id == upload_id.to_string())
            row = connection.execute(stmt).first()
            return None if row is None else _asset_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def get_owned_post(
        self,
        post_id: XPostClaimId,
        *,
        created_by_login_key: str,
    ) -> XPostClaim | None:
        def operation(connection: Connection) -> XPostClaim | None:
            stmt = select(x_post_claims).where(
                x_post_claims.c.id == post_id.to_string(),
                x_post_claims.c.created_by_login_key == created_by_login_key,
            )
            row = connection.execute(stmt).first()
            return None if row is None else _post_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def list_owned(
        self,
        *,
        created_by_login_key: str,
        limit: int,
        after_created_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[XPostClaim, ...]:
        def operation(connection: Connection) -> tuple[XPostClaim, ...]:
            stmt = select(x_post_claims).where(
                x_post_claims.c.created_by_login_key == created_by_login_key
            )
            if after_created_at_ms is not None:
                if after_id is None:
                    raise FrameNestXClaimRepositoryError(
                        _REPOSITORY_FAILURE_MESSAGE
                    )
                stmt = stmt.where(
                    (x_post_claims.c.created_at_ms < after_created_at_ms)
                    | (
                        (x_post_claims.c.created_at_ms == after_created_at_ms)
                        & (x_post_claims.c.id < after_id)
                    )
                )
            stmt = stmt.order_by(
                x_post_claims.c.created_at_ms.desc(), x_post_claims.c.id.desc()
            ).limit(limit)
            rows = connection.execute(stmt).all()
            return tuple(_post_from_row(row) for row in rows)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def count_active_for_requester(self, *, created_by_login_key: str) -> int:
        return self._count(
            x_post_claims.c.created_by_login_key == created_by_login_key,
            x_post_claims.c.state.in_(ACTIVE_X_ACQUISITION_STATES),
        )

    def count_global_active_ordinary(self) -> int:
        return self._count(
            x_post_claims.c.created_by_login_key.isnot(None),
            x_post_claims.c.state.in_(ACTIVE_X_ACQUISITION_STATES),
        )

    def count_submits_since(
        self,
        *,
        created_by_login_key: str,
        since_ms: int,
    ) -> int:
        return self._count(
            x_post_claims.c.created_by_login_key == created_by_login_key,
            x_post_claims.c.created_at_ms >= since_ms,
        )

    def count_failed_transitions_since(
        self,
        *,
        created_by_login_key: str,
        since_ms: int,
    ) -> int:
        def operation(connection: Connection) -> int:
            stmt = (
                select(func.count())
                .select_from(x_post_claims)
                .where(
                    x_post_claims.c.created_by_login_key == created_by_login_key,
                    x_post_claims.c.state == XAcquisitionState.FAILED.value,
                    x_post_claims.c.completed_at_ms >= since_ms,
                )
            )
            return int(connection.execute(stmt).scalar_one())

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def private_successful_quota(
        self,
        *,
        created_by_login_key: str,
    ) -> tuple[int, int]:
        def operation(connection: Connection) -> tuple[int, int]:
            stmt = (
                select(x_assets.c.media_id, x_assets.c.acquired_bytes)
                .select_from(
                    x_assets.join(x_post_claims, x_assets.c.claim_id == x_post_claims.c.id)
                )
                .where(
                    x_post_claims.c.created_by_login_key == created_by_login_key,
                    x_assets.c.state.in_(_ASSET_SUCCESS_VALUES),
                )
            )
            rows = connection.execute(stmt).all()
            item_count = len(rows)
            byte_count = sum(int(row[1] or 0) for row in rows)
            return item_count, byte_count

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def has_live_requester_media_access(
        self,
        *,
        media_id: MediaId,
        login_key: str,
    ) -> bool:
        def operation(connection: Connection) -> bool:
            stmt = (
                select(func.count())
                .select_from(
                    x_assets.join(x_post_claims, x_assets.c.claim_id == x_post_claims.c.id)
                )
                .where(
                    x_assets.c.media_id == media_id.to_string(),
                    x_assets.c.state.in_(_ASSET_SUCCESS_VALUES),
                    x_post_claims.c.created_by_login_key == login_key,
                )
            )
            return int(connection.execute(stmt).scalar_one()) > 0

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def list_recovery_candidates(
        self,
        *,
        limit: int,
        after_updated_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[XPostClaim, ...]:
        def operation(connection: Connection) -> tuple[XPostClaim, ...]:
            stmt = select(x_post_claims).where(
                x_post_claims.c.state.in_(ACTIVE_X_ACQUISITION_STATES)
            )
            if after_updated_at_ms is not None:
                if after_id is None:
                    raise FrameNestXClaimRepositoryError(
                        _REPOSITORY_FAILURE_MESSAGE
                    )
                stmt = stmt.where(
                    (x_post_claims.c.updated_at_ms > after_updated_at_ms)
                    | (
                        (x_post_claims.c.updated_at_ms == after_updated_at_ms)
                        & (x_post_claims.c.id > after_id)
                    )
                )
            stmt = stmt.order_by(
                x_post_claims.c.updated_at_ms, x_post_claims.c.id
            ).limit(limit)
            rows = connection.execute(stmt).all()
            return tuple(_post_from_row(row) for row in rows)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def list_cleanup_candidates(
        self,
        *,
        limit: int,
    ) -> tuple[XPostClaim, ...]:
        def operation(connection: Connection) -> tuple[XPostClaim, ...]:
            stmt = (
                select(x_post_claims)
                .select_from(
                    x_post_claims.join(x_assets, x_assets.c.claim_id == x_post_claims.c.id)
                )
                .where(
                    x_post_claims.c.state.in_(LIVE_CATALOG_X_ACQUISITION_STATES),
                    x_assets.c.cleanup_state == XStagingCleanupState.PENDING.value,
                )
                .distinct()
                .limit(limit)
            )
            rows = connection.execute(stmt).all()
            return tuple(_post_from_row(row) for row in rows)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def save_post(
        self,
        claim: XPostClaim,
        *,
        expected_state: XAcquisitionState,
        expected_version: int,
    ) -> XPostClaim:
        def operation(connection: Connection) -> XPostClaim:
            row = _post_row_by_id(connection, claim.id)
            if row is None:
                raise XClaimNotFoundError("claim not found")
            if row["state"] != expected_state.value or row["version"] != expected_version:
                raise XClaimConcurrencyConflictError("claim changed")
            ensure_x_transition_allowed(
                XAcquisitionState(row["state"]), claim.state
            )
            connection.execute(
                x_post_claims.update()
                .where(
                    x_post_claims.c.id == claim.id.to_string(),
                    x_post_claims.c.version == expected_version,
                )
                .values(_post_values(claim))
            )
            return claim

        try:
            return run_in_transaction(self._engine, operation)
        except (XClaimNotFoundError, XClaimConcurrencyConflictError):
            raise
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def create_assets(self, assets: tuple[XAsset, ...]) -> None:
        def operation(connection: Connection) -> None:
            for asset in assets:
                if asset.state is not XAssetState.PENDING:
                    raise FrameNestXClaimRepositoryError(
                        _REPOSITORY_FAILURE_MESSAGE
                    )
                value = dict(_asset_values(asset))
                connection.execute(insert(x_assets).values(value))

        try:
            run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def get_asset(self, asset_id: XAssetId) -> XAsset | None:
        def operation(connection: Connection) -> XAsset | None:
            row = _asset_row_by_id(connection, asset_id)
            return None if row is None else _asset_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def list_assets_for_post(
        self,
        post_id: XPostClaimId,
    ) -> tuple[XAsset, ...]:
        def operation(connection: Connection) -> tuple[XAsset, ...]:
            stmt = (
                select(x_assets)
                .where(x_assets.c.claim_id == post_id.to_string())
                .order_by(x_assets.c.ordinal)
            )
            rows = connection.execute(stmt).all()
            return tuple(_asset_from_row(row) for row in rows)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def media_ids_hidden_from_requester(
        self,
        *,
        login_key: str,
        media_ids: tuple[str, ...],
    ) -> frozenset[str]:
        """Bound media owned by someone else must not be linked to this requester."""
        if not media_ids:
            return frozenset()

        def operation(connection: Connection) -> frozenset[str]:
            rows = connection.execute(
                select(kronika_records.c.media_id).where(
                    kronika_records.c.media_id.in_(tuple(media_ids)),
                    kronika_records.c.owner_login_key != login_key,
                )
            ).all()
            return frozenset(str(row[0]) for row in rows)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def save_asset(
        self,
        asset: XAsset,
        *,
        expected_state: XAssetState,
        expected_version: int,
    ) -> XAsset:
        def operation(connection: Connection) -> XAsset:
            row = _asset_row_by_id(connection, asset.id)
            if row is None:
                raise XClaimNotFoundError("asset not found")
            if row["state"] != expected_state.value or row["version"] != expected_version:
                raise XClaimConcurrencyConflictError("asset changed")
            ensure_x_asset_transition_allowed(
                XAssetState(row["state"]), asset.state
            )
            connection.execute(
                x_assets.update()
                .where(
                    x_assets.c.id == asset.id.to_string(),
                    x_assets.c.version == expected_version,
                )
                .values(_asset_values(asset))
            )
            return asset

        try:
            return run_in_transaction(self._engine, operation)
        except (XClaimNotFoundError, XClaimConcurrencyConflictError):
            raise
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def get_pending_alias(self, claim_id: XPostClaimId) -> PendingMediaUserAlias | None:
        def operation(connection: Connection) -> PendingMediaUserAlias | None:
            return _load_pending_alias(connection, claim_id)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def upsert_pending_alias(
        self,
        claim_id: XPostClaimId,
        login_key: str,
        content: MediaUserAliasContent,
        now_ms: int,
    ) -> PendingMediaUserAlias | None:
        def operation(connection: Connection) -> PendingMediaUserAlias | None:
            if content.is_empty():
                _delete_pending_alias(connection, claim_id)
                return None
            current = _load_pending_alias(connection, claim_id)
            claim_id_text = claim_id.to_string()
            if current is None:
                connection.execute(
                    insert(x_claim_pending_aliases).values(
                        claim_id=claim_id_text,
                        login_key=login_key,
                        display_title=_pending_title(content),
                        description=_pending_description(content),
                        created_at_ms=now_ms,
                        updated_at_ms=now_ms,
                    )
                )
                created_at_ms = now_ms
            else:
                connection.execute(
                    update(x_claim_pending_aliases)
                    .where(x_claim_pending_aliases.c.claim_id == claim_id_text)
                    .values(
                        login_key=login_key,
                        display_title=_pending_title(content),
                        description=_pending_description(content),
                        updated_at_ms=now_ms,
                    )
                )
                created_at_ms = current.created_at_ms
            connection.execute(
                delete(x_claim_pending_alias_tags).where(
                    x_claim_pending_alias_tags.c.claim_id == claim_id_text
                )
            )
            for position, key in enumerate(content.tag_keys):
                connection.execute(
                    insert(x_claim_pending_alias_tags).values(
                        claim_id=claim_id_text,
                        tag_key=key.value,
                        position=position,
                    )
                )
            return PendingMediaUserAlias(
                claim_id=claim_id,
                login_key=login_key,
                content=content,
                created_at_ms=created_at_ms,
                updated_at_ms=now_ms,
            )

        try:
            return run_in_transaction(self._engine, operation)
        except IntegrityError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def delete_pending_alias(self, claim_id: XPostClaimId) -> None:
        def operation(connection: Connection) -> None:
            _delete_pending_alias(connection, claim_id)

        try:
            run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    # ------------------------------------------------------------------ helpers
    def _count(self, *conditions: object) -> int:
        def operation(connection: Connection) -> int:
            stmt = select(func.count()).select_from(x_post_claims).where(*conditions)
            return int(connection.execute(stmt).scalar_one())

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestXClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc


def _post_row_by_id(
    connection: Connection,
    post_id: XPostClaimId,
) -> Mapping | None:
    row = connection.execute(
        select(x_post_claims).where(x_post_claims.c.id == post_id.to_string())
    ).first()
    return None if row is None else row._mapping


def _active_row_by_source(
    connection: Connection,
    *,
    post_id_key: str,
    created_by_login_key: str | None,
) -> Mapping | None:
    stmt = select(x_post_claims).where(
        x_post_claims.c.x_post_id == post_id_key,
        x_post_claims.c.state.in_(ACTIVE_X_ACQUISITION_STATES),
    )
    if created_by_login_key is None:
        stmt = stmt.where(x_post_claims.c.created_by_login_key.is_(None))
    else:
        stmt = stmt.where(
            x_post_claims.c.created_by_login_key == created_by_login_key
        )
    row = connection.execute(stmt).first()
    return None if row is None else row._mapping


def _asset_row_by_id(connection: Connection, asset_id: XAssetId) -> Mapping | None:
    row = connection.execute(
        select(x_assets).where(x_assets.c.id == asset_id.to_string())
    ).first()
    return None if row is None else row._mapping


def _post_values(claim: XPostClaim) -> dict:
    return {
        "id": claim.id.to_string(),
        "state": claim.state.value,
        "acquisition_source": claim.acquisition_source.value,
        "submitted_url": claim.submitted_url,
        "canonical_url": claim.canonical_url,
        "x_post_id": claim.x_post_id,
        "extractor_key": claim.extractor_key,
        "created_by_login_key": claim.created_by_login_key,
        "retry_of_claim_id": _optional_str(claim.retry_of_claim_id),
        "resolved_claim_id": _optional_str(claim.resolved_claim_id),
        "source_author_stable_id": claim.source_author_stable_id,
        "source_author_handle": claim.source_author_handle,
        "source_author_display_name": claim.source_author_display_name,
        "source_post_text": claim.source_post_text,
        "source_posted_at_ms": claim.source_posted_at_ms,
        "title": claim.title,
        "extractor_version": claim.extractor_version,
        "discovered_asset_count": claim.discovered_asset_count,
        "success_count": claim.success_count,
        "failure_count": claim.failure_count,
        "created_at_ms": claim.created_at_ms,
        "updated_at_ms": claim.updated_at_ms,
        "completed_at_ms": claim.completed_at_ms,
        "catalog_removed_at_ms": claim.catalog_removed_at_ms,
        "failure_stage": None if claim.failure_stage is None else claim.failure_stage.value,
        "failure_code": claim.failure_code,
        "cleanup_state": claim.cleanup_state.value,
        "cleanup_completed_at_ms": claim.cleanup_completed_at_ms,
        "requested_content_category": (
            None
            if claim.requested_content_category is None
            else claim.requested_content_category.value
        ),
        "version": claim.version,
    }


def _post_from_row(row: Mapping) -> XPostClaim:
    from kronika.domain.x_acquisition import XPostClaim as _Claim

    if hasattr(row, "_mapping"):
        row = row._mapping
    failure_stage = row["failure_stage"]
    cleanup_state = row["cleanup_state"]
    return _Claim(
        id=XPostClaimId.from_string(row["id"]),
        state=XAcquisitionState(row["state"]),
        submitted_url=row["submitted_url"],
        canonical_url=row["canonical_url"],
        x_post_id=row["x_post_id"],
        extractor_key=row["extractor_key"],
        created_by_login_key=row["created_by_login_key"],
        retry_of_claim_id=_optional_post_id(row["retry_of_claim_id"]),
        resolved_claim_id=_optional_post_id(row["resolved_claim_id"]),
        source_author_stable_id=row["source_author_stable_id"],
        source_author_handle=row["source_author_handle"],
        source_author_display_name=row["source_author_display_name"],
        source_post_text=row["source_post_text"],
        source_posted_at_ms=row["source_posted_at_ms"],
        title=row["title"],
        extractor_version=row["extractor_version"],
        discovered_asset_count=int(row["discovered_asset_count"]),
        success_count=int(row["success_count"]),
        failure_count=int(row["failure_count"]),
        created_at_ms=int(row["created_at_ms"]),
        updated_at_ms=int(row["updated_at_ms"]),
        completed_at_ms=row["completed_at_ms"],
        catalog_removed_at_ms=row["catalog_removed_at_ms"],
        failure_stage=None if failure_stage is None else XFailureStage(failure_stage),
        failure_code=row["failure_code"],
        cleanup_state=XStagingCleanupState(cleanup_state),
        cleanup_completed_at_ms=row["cleanup_completed_at_ms"],
        requested_content_category=_optional_category(row["requested_content_category"]),
        version=int(row["version"]),
    )


def _asset_values(asset: XAsset) -> dict:
    return {
        "id": asset.id.to_string(),
        "claim_id": asset.claim_id.to_string(),
        "ordinal": asset.ordinal,
        "media_type": asset.media_type.value,
        "expected_mime": asset.expected_mime,
        "source_media_key": asset.source_media_key,
        "width": asset.width,
        "height": asset.height,
        "duration_seconds": asset.duration_seconds,
        "selected_variant": asset.selected_variant,
        "state": asset.state.value,
        "stage_key": asset.stage_key,
        "acquired_bytes": asset.acquired_bytes,
        "acquired_sha256": asset.acquired_sha256,
        "media_id": _optional_str(asset.media_id),
        "media_location_id": _optional_str(asset.media_location_id),
        "upload_asset_key": asset.upload_asset_key,
        "created_at_ms": asset.created_at_ms,
        "updated_at_ms": asset.updated_at_ms,
        "completed_at_ms": asset.completed_at_ms,
        "failure_stage": None if asset.failure_stage is None else asset.failure_stage.value,
        "failure_code": asset.failure_code,
        "cleanup_state": asset.cleanup_state.value,
        "cleanup_completed_at_ms": asset.cleanup_completed_at_ms,
        "version": asset.version,
    }


def _asset_from_row(row: Mapping) -> XAsset:
    if hasattr(row, "_mapping"):
        row = row._mapping
    failure_stage = row["failure_stage"]
    cleanup_state = row["cleanup_state"]
    return XAsset(
        id=XAssetId.from_string(row["id"]),
        claim_id=XPostClaimId.from_string(row["claim_id"]),
        ordinal=int(row["ordinal"]),
        media_type=XMediaType(row["media_type"]),
        expected_mime=row["expected_mime"],
        source_media_key=row["source_media_key"],
        width=row["width"],
        height=row["height"],
        duration_seconds=row["duration_seconds"],
        selected_variant=row["selected_variant"],
        state=XAssetState(row["state"]),
        stage_key=row["stage_key"],
        acquired_bytes=row["acquired_bytes"],
        acquired_sha256=row["acquired_sha256"],
        media_id=_optional_media_id(row["media_id"]),
        media_location_id=_optional_media_location_id(row["media_location_id"]),
        upload_asset_key=row["upload_asset_key"],
        created_at_ms=int(row["created_at_ms"]),
        updated_at_ms=int(row["updated_at_ms"]),
        completed_at_ms=row["completed_at_ms"],
        failure_stage=None if failure_stage is None else XFailureStage(failure_stage),
        failure_code=row["failure_code"],
        cleanup_state=XStagingCleanupState(cleanup_state),
        cleanup_completed_at_ms=row["cleanup_completed_at_ms"],
        version=int(row["version"]),
    )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_post_id(value: object) -> XPostClaimId | None:
    if value is None:
        return None
    return XPostClaimId.from_string(str(value))


def _optional_category(value: object) -> ContentCategory | None:
    if value is None:
        return None
    return ContentCategory(str(value))


def _optional_media_id(value: object):
    from kronika.domain.identities import MediaId as _MediaId

    if value is None:
        return None
    return _MediaId.from_string(str(value))


def _optional_media_location_id(value: object):
    from kronika.domain.identities import MediaLocationId as _MediaLocationId

    if value is None:
        return None
    return _MediaLocationId.from_string(str(value))


def _load_pending_alias(
    connection: Connection, claim_id: XPostClaimId
) -> PendingMediaUserAlias | None:
    claim_id_text = claim_id.to_string()
    row = (
        connection.execute(
            select(x_claim_pending_aliases).where(
                x_claim_pending_aliases.c.claim_id == claim_id_text
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    tag_rows = connection.execute(
        select(x_claim_pending_alias_tags.c.tag_key)
        .where(x_claim_pending_alias_tags.c.claim_id == claim_id_text)
        .order_by(x_claim_pending_alias_tags.c.position)
    ).fetchall()
    title = row["display_title"]
    description = row["description"]
    content = MediaUserAliasContent(
        display_title=None if title is None else MediaDisplayTitle(title),
        description=None if description is None else MediaDescription(description),
        tag_keys=tuple(CanonicalTagKey(item[0]) for item in tag_rows),
    )
    return PendingMediaUserAlias(
        claim_id=claim_id,
        login_key=str(row["login_key"]),
        content=content,
        created_at_ms=int(row["created_at_ms"]),
        updated_at_ms=int(row["updated_at_ms"]),
    )


def _delete_pending_alias(connection: Connection, claim_id: XPostClaimId) -> None:
    claim_id_text = claim_id.to_string()
    connection.execute(
        delete(x_claim_pending_alias_tags).where(
            x_claim_pending_alias_tags.c.claim_id == claim_id_text
        )
    )
    connection.execute(
        delete(x_claim_pending_aliases).where(
            x_claim_pending_aliases.c.claim_id == claim_id_text
        )
    )


def _pending_title(content: MediaUserAliasContent) -> str | None:
    return None if content.display_title is None else content.display_title.value


def _pending_description(content: MediaUserAliasContent) -> str | None:
    return None if content.description is None else content.description.value