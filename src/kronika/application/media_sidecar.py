"""Catalog projection, export, validation, and compare for media sidecars."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NoReturn

from kronika.application.ports.library_repository import LibraryRepository
from kronika.application.ports.media_metadata_repository import (
    MediaMetadataMediaNotFoundError,
    MediaMetadataRepository,
    MediaMetadataSnapshot,
)
from kronika.application.ports.media_repository import MediaRepository
from kronika.application.ports.media_sidecar_store import (
    SIDECAR_LOCATION_NOT_WRITABLE,
    SIDECAR_UNAVAILABLE,
    SIDECAR_UNSAFE_TARGET,
    MediaSidecarStore,
    MediaSidecarStoreError,
    SidecarTargetKind,
)
from kronika.domain.identities import MediaId, MediaLocationId
from kronika.domain.media import MediaLocationAvailability
from kronika.domain.media_metadata import MediaCollectionKey, PROCESSED_COLLECTION_KEY
from kronika.domain.media_sidecar import (
    FrameNestMediaSidecarError,
    SidecarDocument,
    SidecarLocation,
    SidecarProcessedState,
    SidecarTagDefinition,
    decode_media_sidecar,
    encode_media_sidecar,
)

SIDECAR_NOT_FOUND = "SIDECAR_NOT_FOUND"
SIDECAR_INCONSISTENT = "SIDECAR_INCONSISTENT"
SIDECAR_IDENTITY_CONFLICT = "SIDECAR_IDENTITY_CONFLICT"
SIDECAR_COMPARE_MATCH = "SIDECAR_COMPARE_MATCH"
SIDECAR_COMPARE_STALE = "SIDECAR_COMPARE_STALE"
SIDECAR_COMPARE_MISMATCH = "SIDECAR_COMPARE_MISMATCH"
SIDECAR_COMPARE_MISSING = "SIDECAR_COMPARE_MISSING"

_NOT_FOUND_MESSAGE = "Media sidecar target was not found."
_UNAVAILABLE_MESSAGE = "Media sidecar is not available."
_INCONSISTENT_MESSAGE = "Media sidecar catalog state is inconsistent."
_IDENTITY_MESSAGE = "Media sidecar identity conflicts."
_UNSAFE_MESSAGE = "Media sidecar target is unsafe."
_NOT_WRITABLE_MESSAGE = "Media sidecar location is not writable."

SidecarExportStatus = Literal["created", "replaced", "unchanged"]
SidecarCompareStatus = Literal["match", "stale", "mismatch", "missing"]


class FrameNestMediaSidecarApplicationError(RuntimeError):
    """Sanitized application error for sidecar projection and operator operations."""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True, slots=True)
class SidecarExportResult:
    """Result of exporting one catalog projection to an adjacent sidecar."""

    status: SidecarExportStatus
    document: SidecarDocument
    payload: bytes


@dataclass(frozen=True, slots=True)
class SidecarCompareResult:
    """Completed read-only comparison of catalog projection and sidecar."""

    status: SidecarCompareStatus
    error_code: str


class MediaSidecarService:
    """Project catalog state and drive sidecar export, validate, and compare."""

    def __init__(
        self,
        media_repository: MediaRepository,
        library_repository: LibraryRepository,
        metadata_repository: MediaMetadataRepository,
        store: MediaSidecarStore,
    ) -> None:
        self._media_repository = media_repository
        self._library_repository = library_repository
        self._metadata_repository = metadata_repository
        self._store = store

    def project(self, media_id: MediaId, location_id: MediaLocationId) -> SidecarDocument:
        """Build one SidecarDocument from catalog-owned state for an explicit location."""
        media = self._media_repository.get_media(media_id)
        if media is None:
            _not_found()
        location = self._media_repository.get_location(location_id)
        if location is None or location.media_id != media_id:
            _not_found()
        if location.availability is not MediaLocationAvailability.AVAILABLE:
            _unavailable()
        library = self._library_repository.get(location.library_id)
        if library is None:
            _unavailable()
        try:
            snapshot = self._metadata_repository.get_media_metadata(media_id)
        except MediaMetadataMediaNotFoundError:
            _not_found()
        return SidecarDocument(
            media_id=media.id,
            media_kind=media.kind,
            display_title=snapshot.display_title,
            description=snapshot.description,
            tag_keys=snapshot.tag_keys,
            tag_definitions=_tag_definitions(self._metadata_repository, snapshot),
            content_category=snapshot.content_category,
            acquisition_source=snapshot.acquisition_source,
            genre_keys=snapshot.genre_keys,
            creator_attribution_kind=snapshot.creator_attribution_kind,
            creator_stable_id=snapshot.creator_stable_id,
            creator_handle=snapshot.creator_handle,
            creator_display_name=snapshot.creator_display_name,
            processed=_processed_state(snapshot),
            created_at_ms=snapshot.created_at_ms,
            updated_at_ms=snapshot.updated_at_ms,
            location=SidecarLocation(
                location_id=location.id,
                library_id=location.library_id,
                relative_path=location.relative_path,
            ),
        )

    def export(self, media_id: MediaId, location_id: MediaLocationId) -> SidecarExportResult:
        """Write or skip the adjacent sidecar for one explicit catalog location."""
        document = self.project(media_id, location_id)
        payload = encode_media_sidecar(document)
        library = self._library_repository.get(document.location.library_id)
        if library is None:
            _unavailable()
        observation = _observe_adjacent(
            self._store,
            library.root,
            document.location.relative_path,
        )
        if observation.kind is SidecarTargetKind.MISSING:
            _create_adjacent(self._store, library.root, document.location.relative_path, payload)
            return SidecarExportResult(status="created", document=document, payload=payload)
        existing = _decode_observed(observation.payload)
        _require_same_identity(existing, media_id, location_id)
        if existing is not None and observation.payload == payload:
            return SidecarExportResult(status="unchanged", document=document, payload=payload)
        _replace_adjacent(self._store, library.root, document.location.relative_path, payload)
        return SidecarExportResult(status="replaced", document=document, payload=payload)

    def compare(self, media_id: MediaId, location_id: MediaLocationId) -> SidecarCompareResult:
        """Compare one catalog projection with its adjacent sidecar without mutation."""
        document = self.project(media_id, location_id)
        library = self._library_repository.get(document.location.library_id)
        if library is None:
            _unavailable()
        observation = _observe_adjacent(
            self._store,
            library.root,
            document.location.relative_path,
        )
        if observation.kind is SidecarTargetKind.MISSING:
            return SidecarCompareResult(status="missing", error_code=SIDECAR_COMPARE_MISSING)
        existing = _decode_observed(observation.payload)
        _require_same_identity(existing, media_id, location_id)
        if _content_equal(existing, document):
            return SidecarCompareResult(status="match", error_code=SIDECAR_COMPARE_MATCH)
        if _revision_older(existing.updated_at_ms, document.updated_at_ms):
            return SidecarCompareResult(status="stale", error_code=SIDECAR_COMPARE_STALE)
        return SidecarCompareResult(status="mismatch", error_code=SIDECAR_COMPARE_MISMATCH)

    def validate_path(self, path: str) -> SidecarDocument:
        """Decode one explicit sidecar path without catalog access."""
        try:
            observation = self._store.observe_explicit(path)
        except MediaSidecarStoreError as exc:
            _raise_store_error(exc)
        if observation.kind is SidecarTargetKind.UNSAFE:
            raise FrameNestMediaSidecarApplicationError(
                _UNSAFE_MESSAGE,
                error_code=SIDECAR_UNSAFE_TARGET,
            )
        if observation.kind is not SidecarTargetKind.REGULAR:
            raise FrameNestMediaSidecarApplicationError(
                _UNAVAILABLE_MESSAGE,
                error_code=SIDECAR_UNAVAILABLE,
            )
        return _decode_observed(observation.payload)


def _tag_definitions(
    metadata_repository: MediaMetadataRepository,
    snapshot: MediaMetadataSnapshot,
) -> tuple[SidecarTagDefinition, ...]:
    definitions: list[SidecarTagDefinition] = []
    for key in snapshot.tag_keys:
        tag = metadata_repository.get_canonical_tag(key)
        if tag is None:
            _inconsistent()
        definitions.append(SidecarTagDefinition(key=tag.key, display_name=tag.display_name))
    return tuple(definitions)


def _processed_state(snapshot: MediaMetadataSnapshot) -> SidecarProcessedState | None:
    if snapshot.collection_key is None and snapshot.processed_at_ms is None:
        return None
    if snapshot.collection_key is None or snapshot.processed_at_ms is None:
        _inconsistent()
    if snapshot.collection_key.value != PROCESSED_COLLECTION_KEY:
        _inconsistent()
    return SidecarProcessedState(
        collection_key=MediaCollectionKey(PROCESSED_COLLECTION_KEY),
        processed_at_ms=snapshot.processed_at_ms,
    )


def _observe_adjacent(store: MediaSidecarStore, root, relative_path):
    try:
        observation = store.observe_adjacent(root, relative_path)
    except MediaSidecarStoreError as exc:
        _raise_store_error(exc)
    if observation.kind is SidecarTargetKind.UNSAFE:
        raise FrameNestMediaSidecarApplicationError(_UNSAFE_MESSAGE, error_code=SIDECAR_UNSAFE_TARGET)
    return observation


def _create_adjacent(store: MediaSidecarStore, root, relative_path, payload: bytes) -> None:
    try:
        store.create_adjacent(root, relative_path, payload)
    except MediaSidecarStoreError as exc:
        _raise_store_error(exc)


def _replace_adjacent(store: MediaSidecarStore, root, relative_path, payload: bytes) -> None:
    try:
        store.replace_adjacent(root, relative_path, payload)
    except MediaSidecarStoreError as exc:
        _raise_store_error(exc)


def _decode_observed(payload: bytes | None) -> SidecarDocument:
    if not isinstance(payload, bytes):
        raise FrameNestMediaSidecarApplicationError(
            "Media sidecar is malformed.",
            error_code="SIDECAR_MALFORMED",
        )
    try:
        return decode_media_sidecar(payload)
    except FrameNestMediaSidecarError as exc:
        raise FrameNestMediaSidecarApplicationError(str(exc), error_code=exc.error_code) from None


def _require_same_identity(
    document: SidecarDocument,
    media_id: MediaId,
    location_id: MediaLocationId,
) -> None:
    if document.media_id != media_id or document.location.location_id != location_id:
        raise FrameNestMediaSidecarApplicationError(
            _IDENTITY_MESSAGE,
            error_code=SIDECAR_IDENTITY_CONFLICT,
        )


def _content_equal(left: SidecarDocument, right: SidecarDocument) -> bool:
    return (
        left.media_id == right.media_id
        and left.media_kind == right.media_kind
        and left.display_title == right.display_title
        and left.description == right.description
        and left.tag_keys == right.tag_keys
        and left.tag_definitions == right.tag_definitions
        and left.content_category == right.content_category
        and left.acquisition_source == right.acquisition_source
        and left.genre_keys == right.genre_keys
        and left.creator_attribution_kind == right.creator_attribution_kind
        and left.creator_stable_id == right.creator_stable_id
        and left.creator_handle == right.creator_handle
        and left.creator_display_name == right.creator_display_name
        and left.processed == right.processed
        and left.location == right.location
        and left.format == right.format
        and left.schema_version == right.schema_version
    )


def _revision_older(sidecar_updated_at_ms: int | None, catalog_updated_at_ms: int | None) -> bool:
    if sidecar_updated_at_ms is None and catalog_updated_at_ms is None:
        return False
    if sidecar_updated_at_ms is None:
        return True
    if catalog_updated_at_ms is None:
        return False
    return sidecar_updated_at_ms < catalog_updated_at_ms


def _raise_store_error(exc: MediaSidecarStoreError) -> NoReturn:
    code = exc.error_code
    if code == SIDECAR_UNSAFE_TARGET:
        raise FrameNestMediaSidecarApplicationError(_UNSAFE_MESSAGE, error_code=code) from None
    if code == SIDECAR_LOCATION_NOT_WRITABLE:
        raise FrameNestMediaSidecarApplicationError(_NOT_WRITABLE_MESSAGE, error_code=code) from None
    if code in {"SIDECAR_MALFORMED", "SIDECAR_UNSUPPORTED"}:
        raise FrameNestMediaSidecarApplicationError(str(exc), error_code=code) from None
    raise FrameNestMediaSidecarApplicationError(_UNAVAILABLE_MESSAGE, error_code=SIDECAR_UNAVAILABLE) from None


def _not_found() -> NoReturn:
    raise FrameNestMediaSidecarApplicationError(_NOT_FOUND_MESSAGE, error_code=SIDECAR_NOT_FOUND)


def _unavailable() -> NoReturn:
    raise FrameNestMediaSidecarApplicationError(_UNAVAILABLE_MESSAGE, error_code=SIDECAR_UNAVAILABLE)


def _inconsistent() -> NoReturn:
    raise FrameNestMediaSidecarApplicationError(_INCONSISTENT_MESSAGE, error_code=SIDECAR_INCONSISTENT)
