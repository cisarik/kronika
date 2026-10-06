"""Unit tests for portable media sidecar catalog projection and compare."""

from __future__ import annotations

from typing import Any

import pytest

from kronika.application.media_sidecar import (
    FrameNestMediaSidecarApplicationError,
    MediaSidecarService,
    SIDECAR_COMPARE_MATCH,
    SIDECAR_COMPARE_MISMATCH,
    SIDECAR_COMPARE_MISSING,
    SIDECAR_COMPARE_STALE,
    SIDECAR_IDENTITY_CONFLICT,
    SIDECAR_INCONSISTENT,
    SIDECAR_NOT_FOUND,
    SIDECAR_UNAVAILABLE,
)
from kronika.application.ports.media_metadata_repository import (
    MediaMetadataMediaNotFoundError,
    MediaMetadataSnapshot,
)
from kronika.application.ports.media_sidecar_store import (
    SIDECAR_UNSAFE_TARGET,
    SidecarTargetKind,
    SidecarTargetObservation,
    sidecar_filename,
)
from kronika.domain import (
    DeviceId,
    Library,
    LibraryId,
    LibraryPathFlavor,
    LibraryRoot,
    MediaId,
    MediaLocationId,
)
from kronika.domain.media import (
    LogicalMedia,
    MediaKind,
    MediaLocation,
    MediaLocationAvailability,
    MediaRelativePath,
)
from kronika.domain.media_classification import (
    AcquisitionSource,
    ContentCategory,
    CreatorAttributionKind,
    MovieGenre,
)
from kronika.domain.media_metadata import (
    CanonicalTag,
    CanonicalTagDisplayName,
    CanonicalTagKey,
    MediaCollectionKey,
    MediaDescription,
    MediaDisplayTitle,
    PROCESSED_COLLECTION_KEY,
)
from kronika.domain.media_sidecar import (
    SIDECAR_FORMAT,
    SidecarDocument,
    encode_media_sidecar,
)

MEDIA_ID = MediaId.from_string("12345678-1234-4234-9234-123456789abc")
OTHER_MEDIA_ID = MediaId.from_string("99999999-9999-4999-8999-999999999999")
LOCATION_ID = MediaLocationId.from_string("abcdefab-cdef-4abc-8def-abcdefabcdef")
OTHER_LOCATION_ID = MediaLocationId.from_string("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
LIBRARY_ID = LibraryId.from_string("11111111-2222-4333-8444-555555555555")
DEVICE_ID = DeviceId.from_string("22222222-3333-4444-9555-666666666666")
PRIVATE_MARKER = "/home/private/secret.mp4"
PAYLOAD_MARKER = "PAYLOAD_MARKER_9f3a"


class _FakeMediaRepository:
    def __init__(self, media=None, location=None) -> None:
        self._media = media
        self._location = location
        self.write_calls = 0

    def get_media(self, media_id: MediaId) -> LogicalMedia | None:
        if self._media is not None and self._media.id == media_id:
            return self._media
        return None

    def get_location(self, location_id: MediaLocationId) -> MediaLocation | None:
        if self._location is not None and self._location.id == location_id:
            return self._location
        return None

    def add_media(self, media: LogicalMedia) -> None:
        self.write_calls += 1

    def add_location(self, location: MediaLocation) -> None:
        self.write_calls += 1

    def add_media_with_location(self, media: LogicalMedia, location: MediaLocation) -> None:
        self.write_calls += 1


class _FakeLibraryRepository:
    def __init__(self, library=None) -> None:
        self._library = library
        self.write_calls = 0

    def get(self, library_id: LibraryId) -> Library | None:
        if self._library is not None and self._library.id == library_id:
            return self._library
        return None

    def add(self, library: Library) -> None:
        self.write_calls += 1


class _FakeMetadataRepository:
    def __init__(self, snapshot=None, tags: tuple[CanonicalTag, ...] = ()) -> None:
        self._snapshot = snapshot
        self._tags = {tag.key: tag for tag in tags}
        self.write_calls = 0

    def get_media_metadata(self, media_id: MediaId) -> MediaMetadataSnapshot:
        if self._snapshot is not None and self._snapshot.media_id == media_id:
            return self._snapshot
        raise MediaMetadataMediaNotFoundError()

    def get_canonical_tag(self, key: CanonicalTagKey) -> CanonicalTag | None:
        return self._tags.get(key)

    def list_canonical_tags(self) -> tuple[CanonicalTag, ...]:
        return tuple(self._tags.values())

    def create_canonical_tag(self, key: CanonicalTagKey, display_name: CanonicalTagDisplayName, now_ms: int) -> None:
        self.write_calls += 1

    def save_media_metadata(self, *args: Any, **kwargs: Any) -> None:
        self.write_calls += 1


class _FakeStore:
    def __init__(self, observation: SidecarTargetObservation | None = None) -> None:
        self.observation = observation or SidecarTargetObservation(kind=SidecarTargetKind.MISSING)
        self.create_calls: list[bytes] = []
        self.replace_calls: list[bytes] = []
        self.explicit_paths: list[str] = []

    def observe_adjacent(self, root: LibraryRoot, media_relative_path: MediaRelativePath) -> SidecarTargetObservation:
        del root, media_relative_path
        return self.observation

    def create_adjacent(self, root: LibraryRoot, media_relative_path: MediaRelativePath, payload: bytes) -> None:
        del root, media_relative_path
        self.create_calls.append(payload)

    def replace_adjacent(self, root: LibraryRoot, media_relative_path: MediaRelativePath, payload: bytes) -> None:
        del root, media_relative_path
        self.replace_calls.append(payload)

    def observe_explicit(self, path: str) -> SidecarTargetObservation:
        self.explicit_paths.append(path)
        return self.observation


def _media(*, created_at_ms: int = 1, updated_at_ms: int = 999) -> LogicalMedia:
    return LogicalMedia(
        id=MEDIA_ID,
        kind=MediaKind.VIDEO,
        created_at_ms=created_at_ms,
        updated_at_ms=updated_at_ms,
    )


def _location(
    *,
    availability: MediaLocationAvailability = MediaLocationAvailability.AVAILABLE,
    media_id: MediaId | None = None,
    path: str = "movies/clip.mp4",
) -> MediaLocation:
    return MediaLocation(
        id=LOCATION_ID,
        media_id=media_id or MEDIA_ID,
        library_id=LIBRARY_ID,
        relative_path=MediaRelativePath(path),
        availability=availability,
        observed_size_bytes=100,
        observed_mtime_ns=200,
        created_at_ms=5,
        updated_at_ms=6,
    )


def _library() -> Library:
    return Library(
        id=LIBRARY_ID,
        device_id=DEVICE_ID,
        display_name="Videos",
        root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path="/tmp/videos"),
    )


def _empty_snapshot() -> MediaMetadataSnapshot:
    return MediaMetadataSnapshot(
        media_id=MEDIA_ID,
        persisted=False,
        display_title=None,
        description=None,
        tag_keys=(),
        collection_key=None,
        processed_at_ms=None,
        created_at_ms=None,
        updated_at_ms=None,
    )


def _populated_tags() -> tuple[CanonicalTag, ...]:
    return (
        CanonicalTag(
            key=CanonicalTagKey("mathematics"),
            display_name=CanonicalTagDisplayName("Math"),
            created_at_ms=1,
            updated_at_ms=1,
        ),
        CanonicalTag(
            key=CanonicalTagKey("compression"),
            display_name=CanonicalTagDisplayName("Kompresia"),
            created_at_ms=1,
            updated_at_ms=1,
        ),
    )


def _populated_snapshot() -> MediaMetadataSnapshot:
    return MediaMetadataSnapshot(
        media_id=MEDIA_ID,
        persisted=True,
        display_title=MediaDisplayTitle("Žánr: Élégie"),
        description=MediaDescription("Unicode description Žánr\nand 🎬"),
        tag_keys=(CanonicalTagKey("mathematics"), CanonicalTagKey("compression")),
        collection_key=MediaCollectionKey(PROCESSED_COLLECTION_KEY),
        processed_at_ms=500,
        created_at_ms=100,
        updated_at_ms=200,
        content_category=ContentCategory.MOVIE,
        acquisition_source=AcquisitionSource.MANUAL_UPLOAD,
        genre_keys=(MovieGenre.DRAMA, MovieGenre.SCI_FI),
        creator_attribution_kind=CreatorAttributionKind.YOUTUBE_CHANNEL,
        creator_stable_id="UC123",
        creator_handle="examplehandle",
        creator_display_name="Example Channel",
    )


def _service(
    *,
    media=None,
    location=None,
    library=None,
    snapshot=None,
    tags: tuple[CanonicalTag, ...] = (),
    store: _FakeStore | None = None,
) -> tuple[MediaSidecarService, _FakeMediaRepository, _FakeLibraryRepository, _FakeMetadataRepository, _FakeStore]:
    media_repo = _FakeMediaRepository(media=media, location=location)
    library_repo = _FakeLibraryRepository(library=library)
    metadata_repo = _FakeMetadataRepository(snapshot=snapshot, tags=tags)
    sidecar_store = store or _FakeStore()
    service = MediaSidecarService(media_repo, library_repo, metadata_repo, sidecar_store)
    return service, media_repo, library_repo, metadata_repo, sidecar_store


def _ready_service(**overrides: Any):
    values = {
        "media": _media(),
        "location": _location(),
        "library": _library(),
        "snapshot": _empty_snapshot(),
    }
    values.update(overrides)
    return _service(**values)


def _expect_error(exc: BaseException, *, error_code: str) -> None:
    assert isinstance(exc, FrameNestMediaSidecarApplicationError)
    assert exc.error_code == error_code
    message = str(exc)
    assert PRIVATE_MARKER not in message
    assert PAYLOAD_MARKER not in message


def test_sidecar_filename_is_complete_media_filename_plus_suffix() -> None:
    assert sidecar_filename(MediaRelativePath("movies/clip.mp4")) == "clip.mp4.kronika.json"


def test_minimal_projection_uses_metadata_timestamps_not_logical_media() -> None:
    service, *_ = _ready_service()
    document = service.project(MEDIA_ID, LOCATION_ID)
    assert document.media_id == MEDIA_ID
    assert document.media_kind is MediaKind.VIDEO
    assert document.display_title is None
    assert document.description is None
    assert document.tag_keys == ()
    assert document.tag_definitions == ()
    assert document.processed is None
    assert document.created_at_ms is None
    assert document.updated_at_ms is None
    assert document.location.location_id == LOCATION_ID
    assert document.location.library_id == LIBRARY_ID
    assert document.location.relative_path.value == "movies/clip.mp4"
    assert document.format == SIDECAR_FORMAT


def test_fully_populated_projection_preserves_tag_order_and_processed_state() -> None:
    service, *_ = _ready_service(snapshot=_populated_snapshot(), tags=_populated_tags())
    document = service.project(MEDIA_ID, LOCATION_ID)
    assert document.display_title is not None
    assert document.display_title.value == "Žánr: Élégie"
    assert [key.value for key in document.tag_keys] == ["mathematics", "compression"]
    assert [item.key.value for item in document.tag_definitions] == ["mathematics", "compression"]
    assert [item.display_name.value for item in document.tag_definitions] == ["Math", "Kompresia"]
    assert document.processed is not None
    assert document.processed.processed_at_ms == 500
    assert document.created_at_ms == 100
    assert document.updated_at_ms == 200
    assert document.content_category is ContentCategory.MOVIE
    assert document.genre_keys == (MovieGenre.DRAMA, MovieGenre.SCI_FI)


def test_processed_absent_when_collection_is_empty() -> None:
    service, *_ = _ready_service()
    assert service.project(MEDIA_ID, LOCATION_ID).processed is None


def test_missing_media_is_not_found() -> None:
    service, *_ = _service()
    with pytest.raises(FrameNestMediaSidecarApplicationError) as exc_info:
        service.project(MEDIA_ID, LOCATION_ID)
    _expect_error(exc_info.value, error_code=SIDECAR_NOT_FOUND)


def test_missing_location_is_not_found() -> None:
    service, *_ = _service(media=_media())
    with pytest.raises(FrameNestMediaSidecarApplicationError) as exc_info:
        service.project(MEDIA_ID, LOCATION_ID)
    _expect_error(exc_info.value, error_code=SIDECAR_NOT_FOUND)


def test_media_location_identity_mismatch_is_not_found() -> None:
    service, *_ = _service(media=_media(), location=_location(media_id=OTHER_MEDIA_ID))
    with pytest.raises(FrameNestMediaSidecarApplicationError) as exc_info:
        service.project(MEDIA_ID, LOCATION_ID)
    _expect_error(exc_info.value, error_code=SIDECAR_NOT_FOUND)


def test_unavailable_location_is_unavailable() -> None:
    service, *_ = _ready_service(
        location=_location(availability=MediaLocationAvailability.OFFLINE),
    )
    with pytest.raises(FrameNestMediaSidecarApplicationError) as exc_info:
        service.project(MEDIA_ID, LOCATION_ID)
    _expect_error(exc_info.value, error_code=SIDECAR_UNAVAILABLE)


def test_missing_library_is_unavailable() -> None:
    service, *_ = _service(media=_media(), location=_location(), snapshot=_empty_snapshot())
    with pytest.raises(FrameNestMediaSidecarApplicationError) as exc_info:
        service.project(MEDIA_ID, LOCATION_ID)
    _expect_error(exc_info.value, error_code=SIDECAR_UNAVAILABLE)


def test_missing_tag_definition_is_inconsistent() -> None:
    service, *_ = _ready_service(snapshot=_populated_snapshot(), tags=())
    with pytest.raises(FrameNestMediaSidecarApplicationError) as exc_info:
        service.project(MEDIA_ID, LOCATION_ID)
    _expect_error(exc_info.value, error_code=SIDECAR_INCONSISTENT)


def test_projection_does_not_write_repositories() -> None:
    service, media_repo, library_repo, metadata_repo, _store = _ready_service(
        snapshot=_populated_snapshot(),
        tags=_populated_tags(),
    )
    service.project(MEDIA_ID, LOCATION_ID)
    assert media_repo.write_calls == 0
    assert library_repo.write_calls == 0
    assert metadata_repo.write_calls == 0


def test_export_created_then_unchanged_does_not_replace() -> None:
    store = _FakeStore()
    service, *_ = _ready_service(store=store)
    created = service.export(MEDIA_ID, LOCATION_ID)
    assert created.status == "created"
    assert store.create_calls
    intended = store.create_calls[0]
    store.observation = SidecarTargetObservation(kind=SidecarTargetKind.REGULAR, payload=intended)
    unchanged = service.export(MEDIA_ID, LOCATION_ID)
    assert unchanged.status == "unchanged"
    assert store.replace_calls == []


def test_export_replaces_valid_different_same_identity_bytes() -> None:
    service_for_bytes, *_ = _ready_service()
    intended = encode_media_sidecar(service_for_bytes.project(MEDIA_ID, LOCATION_ID))
    different = intended.replace(b"movies/clip.mp4", b"movies/other.mp4", 1)
    store = _FakeStore(
        SidecarTargetObservation(kind=SidecarTargetKind.REGULAR, payload=different),
    )
    service, *_ = _ready_service(store=store)
    result = service.export(MEDIA_ID, LOCATION_ID)
    assert result.status == "replaced"
    assert store.replace_calls == [intended]


def test_compare_missing_match_stale_and_mismatch() -> None:
    service, *_rest = _ready_service(store=_FakeStore())
    missing = service.compare(MEDIA_ID, LOCATION_ID)
    assert missing.status == "missing"
    assert missing.error_code == SIDECAR_COMPARE_MISSING

    projected = service.project(MEDIA_ID, LOCATION_ID)
    match_store = _FakeStore(
        SidecarTargetObservation(
            kind=SidecarTargetKind.REGULAR,
            payload=encode_media_sidecar(projected),
        )
    )
    match = _ready_service(store=match_store)[0].compare(MEDIA_ID, LOCATION_ID)
    assert match.status == "match"
    assert match.error_code == SIDECAR_COMPARE_MATCH


def test_payload_equality_wins_over_misleading_timestamps() -> None:
    catalog = _empty_snapshot()
    sidecar_snapshot = MediaMetadataSnapshot(
        media_id=MEDIA_ID,
        persisted=True,
        display_title=None,
        description=None,
        tag_keys=(),
        collection_key=None,
        processed_at_ms=None,
        created_at_ms=1,
        updated_at_ms=2,
    )
    sidecar_service, *_ = _ready_service(snapshot=sidecar_snapshot)
    sidecar_bytes = encode_media_sidecar(sidecar_service.project(MEDIA_ID, LOCATION_ID))
    store = _FakeStore(SidecarTargetObservation(kind=SidecarTargetKind.REGULAR, payload=sidecar_bytes))
    result = _ready_service(snapshot=catalog, store=store)[0].compare(MEDIA_ID, LOCATION_ID)
    assert result.status == "match"
    assert result.error_code == SIDECAR_COMPARE_MATCH


def test_stale_versus_mismatch_nullable_revision_ordering() -> None:
    catalog = MediaMetadataSnapshot(
        media_id=MEDIA_ID,
        persisted=True,
        display_title=MediaDisplayTitle("Catalog"),
        description=None,
        tag_keys=(),
        collection_key=None,
        processed_at_ms=None,
        created_at_ms=20,
        updated_at_ms=20,
    )
    older = MediaMetadataSnapshot(
        media_id=MEDIA_ID,
        persisted=True,
        display_title=MediaDisplayTitle("Sidecar"),
        description=None,
        tag_keys=(),
        collection_key=None,
        processed_at_ms=None,
        created_at_ms=10,
        updated_at_ms=10,
    )
    older_bytes = encode_media_sidecar(_ready_service(snapshot=older)[0].project(MEDIA_ID, LOCATION_ID))
    stale = _ready_service(
        snapshot=catalog,
        store=_FakeStore(SidecarTargetObservation(kind=SidecarTargetKind.REGULAR, payload=older_bytes)),
    )[0].compare(MEDIA_ID, LOCATION_ID)
    assert stale.status == "stale"
    assert stale.error_code == SIDECAR_COMPARE_STALE

    newer = MediaMetadataSnapshot(
        media_id=MEDIA_ID,
        persisted=True,
        display_title=MediaDisplayTitle("Sidecar"),
        description=None,
        tag_keys=(),
        collection_key=None,
        processed_at_ms=None,
        created_at_ms=30,
        updated_at_ms=30,
    )
    newer_bytes = encode_media_sidecar(_ready_service(snapshot=newer)[0].project(MEDIA_ID, LOCATION_ID))
    mismatch = _ready_service(
        snapshot=catalog,
        store=_FakeStore(SidecarTargetObservation(kind=SidecarTargetKind.REGULAR, payload=newer_bytes)),
    )[0].compare(MEDIA_ID, LOCATION_ID)
    assert mismatch.status == "mismatch"
    assert mismatch.error_code == SIDECAR_COMPARE_MISMATCH

    null_revision = MediaMetadataSnapshot(
        media_id=MEDIA_ID,
        persisted=False,
        display_title=MediaDisplayTitle("Sidecar"),
        description=None,
        tag_keys=(),
        collection_key=None,
        processed_at_ms=None,
        created_at_ms=None,
        updated_at_ms=None,
    )
    null_bytes = encode_media_sidecar(_ready_service(snapshot=null_revision)[0].project(MEDIA_ID, LOCATION_ID))
    stale_null = _ready_service(
        snapshot=catalog,
        store=_FakeStore(SidecarTargetObservation(kind=SidecarTargetKind.REGULAR, payload=null_bytes)),
    )[0].compare(MEDIA_ID, LOCATION_ID)
    assert stale_null.status == "stale"

    equal_null_catalog = _empty_snapshot()
    mismatch_both_null = _ready_service(
        snapshot=equal_null_catalog,
        store=_FakeStore(SidecarTargetObservation(kind=SidecarTargetKind.REGULAR, payload=null_bytes)),
    )[0].compare(MEDIA_ID, LOCATION_ID)
    assert mismatch_both_null.status == "mismatch"
    assert mismatch_both_null.error_code == SIDECAR_COMPARE_MISMATCH


def test_foreign_identity_is_conflict_not_compare_result() -> None:
    foreign = _ready_service(location=_location())[0].project(MEDIA_ID, LOCATION_ID)
    # Encode then swap media_id in JSON would be malformed identity after decode.
    # Build a document for a different media via a second catalog identity.
    other_media = LogicalMedia(id=OTHER_MEDIA_ID, kind=MediaKind.VIDEO, created_at_ms=1, updated_at_ms=1)
    other_location = MediaLocation(
        id=OTHER_LOCATION_ID,
        media_id=OTHER_MEDIA_ID,
        library_id=LIBRARY_ID,
        relative_path=MediaRelativePath("movies/clip.mp4"),
        availability=MediaLocationAvailability.AVAILABLE,
        observed_size_bytes=1,
        observed_mtime_ns=1,
        created_at_ms=1,
        updated_at_ms=1,
    )
    other_snapshot = MediaMetadataSnapshot(
        media_id=OTHER_MEDIA_ID,
        persisted=False,
        display_title=None,
        description=None,
        tag_keys=(),
        collection_key=None,
        processed_at_ms=None,
        created_at_ms=None,
        updated_at_ms=None,
    )
    foreign_bytes = encode_media_sidecar(
        _service(
            media=other_media,
            location=other_location,
            library=_library(),
            snapshot=other_snapshot,
        )[0].project(OTHER_MEDIA_ID, OTHER_LOCATION_ID)
    )
    store = _FakeStore(SidecarTargetObservation(kind=SidecarTargetKind.REGULAR, payload=foreign_bytes))
    with pytest.raises(FrameNestMediaSidecarApplicationError) as exc_info:
        _ready_service(store=store)[0].compare(MEDIA_ID, LOCATION_ID)
    _expect_error(exc_info.value, error_code=SIDECAR_IDENTITY_CONFLICT)
    with pytest.raises(FrameNestMediaSidecarApplicationError) as export_info:
        _ready_service(store=store)[0].export(MEDIA_ID, LOCATION_ID)
    _expect_error(export_info.value, error_code=SIDECAR_IDENTITY_CONFLICT)


def test_compare_unsafe_target_is_error_not_missing() -> None:
    store = _FakeStore(SidecarTargetObservation(kind=SidecarTargetKind.UNSAFE))
    with pytest.raises(FrameNestMediaSidecarApplicationError) as exc_info:
        _ready_service(store=store)[0].compare(MEDIA_ID, LOCATION_ID)
    _expect_error(exc_info.value, error_code=SIDECAR_UNSAFE_TARGET)
    assert store.replace_calls == []
    assert store.create_calls == []


def test_export_refuses_malformed_and_unsupported_existing_sidecar() -> None:
    malformed_store = _FakeStore(
        SidecarTargetObservation(kind=SidecarTargetKind.REGULAR, payload=b"{}\n"),
    )
    with pytest.raises(FrameNestMediaSidecarApplicationError) as malformed_info:
        _ready_service(store=malformed_store)[0].export(MEDIA_ID, LOCATION_ID)
    _expect_error(malformed_info.value, error_code="SIDECAR_MALFORMED")
    assert malformed_store.replace_calls == []
    assert malformed_store.create_calls == []

    unsupported_store = _FakeStore(
        SidecarTargetObservation(
            kind=SidecarTargetKind.REGULAR,
            payload=b'{"format":"other-sidecar","schema_version":1}\n',
        ),
    )
    with pytest.raises(FrameNestMediaSidecarApplicationError) as unsupported_info:
        _ready_service(store=unsupported_store)[0].export(MEDIA_ID, LOCATION_ID)
    _expect_error(unsupported_info.value, error_code="SIDECAR_UNSUPPORTED")
    assert unsupported_store.replace_calls == []


def test_validate_does_not_touch_catalog_writes() -> None:
    service, media_repo, library_repo, metadata_repo, store = _ready_service()
    payload = encode_media_sidecar(service.project(MEDIA_ID, LOCATION_ID))
    store.observation = SidecarTargetObservation(kind=SidecarTargetKind.REGULAR, payload=payload)
    media_repo.write_calls = 0
    library_repo.write_calls = 0
    metadata_repo.write_calls = 0
    document = service.validate_path("/tmp/clip.mp4.framenest.json")
    assert document.media_id == MEDIA_ID
    assert store.explicit_paths == ["/tmp/clip.mp4.framenest.json"]
    assert media_repo.write_calls == 0
    assert library_repo.write_calls == 0
    assert metadata_repo.write_calls == 0
