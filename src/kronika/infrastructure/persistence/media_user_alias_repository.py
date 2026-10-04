"""SQLAlchemy Core adapter for per-user media alias overlays."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kronika.application.ports.media_user_alias_repository import (
    AliasTagNotFoundError,
    FrameNestMediaUserAliasRepositoryError,
    MediaUserAliasMediaNotFoundError,
)
from kronika.domain import FrameNestIdentityError, MediaId
from kronika.domain.media_metadata import CanonicalTagKey, MediaDescription, MediaDisplayTitle
from kronika.domain.media_user_alias import (
    FrameNestMediaUserAliasError,
    MediaUserAlias,
    MediaUserAliasContent,
)
from kronika.infrastructure.persistence.catalog_schema import (
    canonical_tags,
    logical_media,
    media_user_alias_tags,
    media_user_aliases,
)
from kronika.infrastructure.persistence.engine import run_in_transaction

_REPOSITORY_FAILURE_MESSAGE = "Media user alias operation failed."


class SqliteMediaUserAliasRepository:
    """Synchronous SQLite overlay repository backed by SQLAlchemy Core."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_alias(self, media_id: MediaId, login_key: str) -> MediaUserAlias | None:
        def operation(connection: Connection) -> MediaUserAlias | None:
            return _load_alias(connection, media_id, login_key)

        try:
            return run_in_transaction(self._engine, operation)
        except (FrameNestMediaUserAliasError, FrameNestIdentityError) as exc:
            raise FrameNestMediaUserAliasRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc
        except SQLAlchemyError as exc:
            raise FrameNestMediaUserAliasRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def list_aliases_for_login(
        self, login_key: str, media_ids: tuple[MediaId, ...]
    ) -> dict[str, MediaUserAlias]:
        def operation(connection: Connection) -> dict[str, MediaUserAlias]:
            return _list_aliases_for_login(connection, login_key, media_ids)

        try:
            return run_in_transaction(self._engine, operation)
        except (FrameNestMediaUserAliasError, FrameNestIdentityError) as exc:
            raise FrameNestMediaUserAliasRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc
        except SQLAlchemyError as exc:
            raise FrameNestMediaUserAliasRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def canonical_tag_display_names(self, tag_keys: tuple[str, ...]) -> dict[str, str]:
        def operation(connection: Connection) -> dict[str, str]:
            return _canonical_tag_display_names(connection, tag_keys)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestMediaUserAliasRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def list_aliases_for_media(self, media_id: MediaId) -> tuple[MediaUserAlias, ...]:
        def operation(connection: Connection) -> tuple[MediaUserAlias, ...]:
            return _list_aliases_for_media(connection, media_id)

        try:
            return run_in_transaction(self._engine, operation)
        except MediaUserAliasMediaNotFoundError:
            raise
        except (FrameNestMediaUserAliasError, FrameNestIdentityError) as exc:
            raise FrameNestMediaUserAliasRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc
        except SQLAlchemyError as exc:
            raise FrameNestMediaUserAliasRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def upsert_alias(
        self,
        media_id: MediaId,
        login_key: str,
        content: MediaUserAliasContent,
        now_ms: int,
    ) -> MediaUserAlias | None:
        def operation(connection: Connection) -> MediaUserAlias | None:
            if not _media_exists(connection, media_id):
                raise MediaUserAliasMediaNotFoundError()
            _assert_tags_exist(connection, content.tag_keys)
            if content.is_empty():
                _delete_alias(connection, media_id, login_key)
                return None
            current = _load_alias(connection, media_id, login_key)
            media_id_text = media_id.to_string()
            if current is None:
                connection.execute(
                    insert(media_user_aliases).values(
                        media_id=media_id_text,
                        login_key=login_key,
                        display_title=_title_value(content),
                        description=_description_value(content),
                        created_at_ms=now_ms,
                        updated_at_ms=now_ms,
                    )
                )
                created_at_ms = now_ms
            else:
                connection.execute(
                    update(media_user_aliases)
                    .where(
                        media_user_aliases.c.media_id == media_id_text,
                        media_user_aliases.c.login_key == login_key,
                    )
                    .values(
                        display_title=_title_value(content),
                        description=_description_value(content),
                        updated_at_ms=now_ms,
                    )
                )
                created_at_ms = current.created_at_ms
            connection.execute(
                delete(media_user_alias_tags).where(
                    media_user_alias_tags.c.media_id == media_id_text,
                    media_user_alias_tags.c.login_key == login_key,
                )
            )
            _insert_alias_tags(connection, media_id_text, login_key, content.tag_keys)
            return MediaUserAlias(
                media_id=media_id,
                login_key=login_key,
                content=content,
                created_at_ms=created_at_ms,
                updated_at_ms=now_ms,
            )

        try:
            return run_in_transaction(self._engine, operation)
        except (AliasTagNotFoundError, MediaUserAliasMediaNotFoundError):
            raise
        except (FrameNestMediaUserAliasError, FrameNestIdentityError, IntegrityError) as exc:
            raise FrameNestMediaUserAliasRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc
        except SQLAlchemyError as exc:
            raise FrameNestMediaUserAliasRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def delete_alias(self, media_id: MediaId, login_key: str) -> None:
        def operation(connection: Connection) -> None:
            _delete_alias(connection, media_id, login_key)

        try:
            run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestMediaUserAliasRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def canonical_tag_keys_exist(self, tag_keys: tuple[CanonicalTagKey, ...]) -> bool:
        def operation(connection: Connection) -> bool:
            try:
                _assert_tags_exist(connection, tag_keys)
            except AliasTagNotFoundError:
                return False
            return True

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestMediaUserAliasRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc


def _media_exists(connection: Connection, media_id: MediaId) -> bool:
    return (
        connection.execute(
            select(logical_media.c.id).where(logical_media.c.id == media_id.to_string())
        ).first()
        is not None
    )


def _assert_tags_exist(
    connection: Connection, tag_keys: tuple[CanonicalTagKey, ...]
) -> None:
    for key in tag_keys:
        row = connection.execute(
            select(canonical_tags.c.key).where(canonical_tags.c.key == key.value)
        ).first()
        if row is None:
            raise AliasTagNotFoundError()


def _list_aliases_for_login(
    connection: Connection, login_key: str, media_ids: tuple[MediaId, ...]
) -> dict[str, MediaUserAlias]:
    if not media_ids:
        return {}
    media_id_texts = [media_id.to_string() for media_id in media_ids]
    rows = (
        connection.execute(
            select(media_user_aliases).where(
                media_user_aliases.c.login_key == login_key,
                media_user_aliases.c.media_id.in_(media_id_texts),
            )
        )
        .mappings()
        .all()
    )
    aliases: dict[str, MediaUserAlias] = {}
    for row in rows:
        media_id = MediaId.from_string(str(row["media_id"]))
        aliases[media_id.to_string()] = _alias_from_row(connection, media_id, row)
    return aliases


def _canonical_tag_display_names(
    connection: Connection, tag_keys: tuple[str, ...]
) -> dict[str, str]:
    unique_keys = tuple(dict.fromkeys(tag_keys))
    if not unique_keys:
        return {}
    rows = connection.execute(
        select(canonical_tags.c.key, canonical_tags.c.display_name).where(
            canonical_tags.c.key.in_(unique_keys)
        )
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def _list_aliases_for_media(
    connection: Connection, media_id: MediaId
) -> tuple[MediaUserAlias, ...]:
    if not _media_exists(connection, media_id):
        raise MediaUserAliasMediaNotFoundError()
    media_id_text = media_id.to_string()
    rows = (
        connection.execute(
            select(media_user_aliases)
            .where(media_user_aliases.c.media_id == media_id_text)
            .order_by(media_user_aliases.c.login_key)
        )
        .mappings()
        .all()
    )
    return tuple(_alias_from_row(connection, media_id, row) for row in rows)


def _load_alias(
    connection: Connection, media_id: MediaId, login_key: str
) -> MediaUserAlias | None:
    media_id_text = media_id.to_string()
    row = (
        connection.execute(
            select(media_user_aliases).where(
                media_user_aliases.c.media_id == media_id_text,
                media_user_aliases.c.login_key == login_key,
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return _alias_from_row(connection, media_id, row)


def _alias_from_row(
    connection: Connection,
    media_id: MediaId,
    row: Mapping[str, object],
) -> MediaUserAlias:
    media_id_text = media_id.to_string()
    login_key = str(row["login_key"])
    tag_rows = connection.execute(
        select(media_user_alias_tags.c.tag_key)
        .where(
            media_user_alias_tags.c.media_id == media_id_text,
            media_user_alias_tags.c.login_key == login_key,
        )
        .order_by(media_user_alias_tags.c.position)
    ).fetchall()
    title = row["display_title"]
    description = row["description"]
    content = MediaUserAliasContent(
        display_title=None if title is None else MediaDisplayTitle(title),
        description=None if description is None else MediaDescription(description),
        tag_keys=tuple(CanonicalTagKey(item[0]) for item in tag_rows),
    )
    return MediaUserAlias(
        media_id=media_id,
        login_key=login_key,
        content=content,
        created_at_ms=int(row["created_at_ms"]),
        updated_at_ms=int(row["updated_at_ms"]),
    )


def _delete_alias(connection: Connection, media_id: MediaId, login_key: str) -> None:
    media_id_text = media_id.to_string()
    connection.execute(
        delete(media_user_alias_tags).where(
            media_user_alias_tags.c.media_id == media_id_text,
            media_user_alias_tags.c.login_key == login_key,
        )
    )
    connection.execute(
        delete(media_user_aliases).where(
            media_user_aliases.c.media_id == media_id_text,
            media_user_aliases.c.login_key == login_key,
        )
    )


def _insert_alias_tags(
    connection: Connection,
    media_id_text: str,
    login_key: str,
    tag_keys: tuple[CanonicalTagKey, ...],
) -> None:
    for position, key in enumerate(tag_keys):
        connection.execute(
            insert(media_user_alias_tags).values(
                media_id=media_id_text,
                login_key=login_key,
                tag_key=key.value,
                position=position,
            )
        )


def _title_value(content: MediaUserAliasContent) -> str | None:
    return None if content.display_title is None else content.display_title.value


def _description_value(content: MediaUserAliasContent) -> str | None:
    return None if content.description is None else content.description.value
