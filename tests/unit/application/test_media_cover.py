"""Application service tests for the first durable manual cover workflow."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

import pytest
from PIL import Image

from kronika.application.media_analysis import (
    MediaAnalysisFailedError,
    MediaAnalysisUnavailableError,
    MediaRelativePath,
    RepresentativeFrame,
    build_representative_frame,
)
from kronika.application.media_cover import (
    CoverConflictError,
    CoverFailedError,
    CoverMediaNotFoundError,
    CoverService,
    CoverSourceChangedError,
    CoverSourceUnavailableError,
    CoverTimestampInvalidError,
)
from kronika.application.ports.cover_source_analysis import CoverSourceProbe
from kronika.application.ports.cover_storage import (
    CoverArtifact,
    CoverStorageError,
    CoverThumbnailImage,
    OpenedCoverThumbnail,
)
from kronika.domain.identities import (
    DeviceId,
    LibraryId,
    MediaId,
    MediaLocationId,
)
from kronika.domain.libraries import Library, LibraryPathFlavor, LibraryRoot
from kronika.domain.media import (
    LogicalMedia,
    MediaKind,
    MediaLocation,
    MediaLocationAvailability,
    MediaRelativePath as DomainMediaRelativePath,
)
from kronika.domain.media_cover import (
    COVER_ARTIFACT_MEDIA_TYPE,
    COVER_ARTIFACT_PROFILE,
    SOURCE_OBSERVATION_ALGORITHM,
    TIMELESS_IMAGE_TIMESTAMP_MS,
    CoverSourceKind,
    MediaCover,
    source_reference_for_location,
)

MEDIA_ID = MediaId.from_string("11111111-1111-4111-8111-111111111111")
LOCATION_ID = MediaLocationId.from_string("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
LIBRARY_ID = LibraryId.from_string("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
DEVICE_ID = DeviceId.from_string("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
DISTANT_ID = MediaId.from_string("22222222-2222-4222-8222-222222222222")


def _png_frame() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (320, 180), (40, 90, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def _frame(timestamp_ms: int) -> RepresentativeFrame:
    return build_representative_frame(timestamp_ms=timestamp_ms, payload=_png_frame())


class _FakeMediaRepository:
    def __init__(self, media: LogicalMedia | None = None, location: MediaLocation | None = None):
        self._media = media
        self._location = location

    def get_media(self, media_id):
        return self._media if self._media is not None and media_id == self._media.id else None

    def get_location(self, location_id):
        if self._location is not None and location_id == self._location.id:
            return self._location
        return None

    def list_locations_for_media(self, media_id):
        if self._location is not None and self._location.media_id == media_id:
            return (self._location,)
        return ()


class _FakeLibraryRepository:
    def __init__(self, library: Library):
        self._library = library

    def get(self, library_id):
        return self._library if library_id == self._library.id else None

    def list_all(self):
        return (self._library,)


class _FakeAnalyzer:
    page = 0

    def __init__(self, *, fail_extract: bool = False, change_after_extract: bool = False):
        self._fail_extract = fail_extract
        self._change_after_extract = change_after_extract
        self.probe_calls = 0
        self.extract_calls = 0

    def probe(self, root, relative_path, kind):
        self.probe_calls += 1
        self.page += 1
        return CoverSourceProbe(
            duration_ms=2000,
            source_size_bytes=1234,
            source_mtime_ns=99 if not (self._change_after_extract and self.page >= 2) else 999,
        )

    def extract_frame(self, root, relative_path, kind, timestamp_ms):
        self.extract_calls += 1
        if self._fail_extract:
            raise MediaAnalysisFailedError("failed")
        return _frame(timestamp_ms)


class _FakeEncoder:
    def encode_artifact_frame(self, frame: RepresentativeFrame) -> CoverArtifact:
        payload = frame.payload + b"-artifact"
        return CoverArtifact(
            profile=COVER_ARTIFACT_PROFILE,
            media_type=COVER_ARTIFACT_MEDIA_TYPE,
            digest=hashlib.sha256(payload).hexdigest(),
            width=320,
            height=180,
            byte_size=len(payload),
            payload=payload,
        )

    def encode_thumbnail(self, artifact_payload: bytes) -> CoverThumbnailImage:
        payload = artifact_payload + b"-thumb"
        return CoverThumbnailImage(
            media_type=COVER_ARTIFACT_MEDIA_TYPE,
            byte_size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            payload=payload,
        )


class _FakeStorage:
    def __init__(self, *, fail_publish: bool = False):
        self._artifacts: dict[tuple[str, str], bytes] = {}
        self._fail_publish = fail_publish

    def publish(self, *, media_id, artifact):
        if self._fail_publish:
            raise CoverStorageError("failed")
        self._artifacts[(media_id.to_string(), artifact.digest)] = artifact.payload

    def artifact_valid(self, *, media_id, digest):
        return (media_id.to_string(), digest) in self._artifacts

    def read_bytes(self, *, media_id, digest):
        try:
            return self._artifacts[(media_id.to_string(), digest)]
        except KeyError:
            raise CoverStorageError("missing")


class _FakeThumbnailCache:
    algorithm = "cover-thumbnail-jpeg-v1"

    def __init__(self, *, fail_publish: bool = False):
        self._images: dict[str, CoverThumbnailImage] = {}
        self._fail_publish = fail_publish

    def key_for(self, *, media_id, artifact_digest):
        return f"{self.algorithm}/{media_id.to_string()}/{artifact_digest}.jpg"

    def contains(self, cache_key):
        return cache_key in self._images

    def contains_many(self, cache_keys):
        return {key for key in cache_keys if key in self._images}

    def publish(self, cache_key, image):
        if self._fail_publish:
            raise CoverStorageError("failed")
        self._images[cache_key] = image

    def open(self, cache_key):
        try:
            image = self._images[cache_key]
        except KeyError:
            raise CoverStorageError("missing") from None
        return OpenedCoverThumbnail(
            media_type=image.media_type,
            byte_size=image.byte_size,
            payload=image.payload,
            close=lambda: None,
        )


class _FakeCoverRepo:
    """Bounded in-memory replica of the CAS/idempotency semantics."""

    def __init__(self):
        self._covers: dict[str, MediaCover] = {}

    def get(self, media_id):
        return self._covers.get(media_id.to_string())

    def list_by_media(self, media_ids):
        return tuple(self._covers[id.to_string()] for id in media_ids if id.to_string() in self._covers)

    def list_all(self):
        return tuple(sorted(self._covers.values(), key=lambda c: c.media_id.to_string()))

    def set_cover(self, draft, expected_revision):
        from kronika.application.ports.media_cover_repository import (
            MediaCoverConflictError,
            MediaCoverSetResult,
        )

        media_id_text = draft.media_id.to_string()
        current = self._covers.get(media_id_text)
        if current is None:
            if expected_revision != 0:
                raise MediaCoverConflictError("conflict")
            created = _cover_from_draft(draft, revision=1)
            self._covers[media_id_text] = created
            return MediaCoverSetResult(outcome="created", cover=created)
        if expected_revision != current.revision:
            raise MediaCoverConflictError("conflict")
        if draft.same_payload(current):
            return MediaCoverSetResult(outcome="unchanged", cover=current)
        replaced = _cover_from_draft(draft, revision=current.revision + 1)
        self._covers[media_id_text] = replaced
        return MediaCoverSetResult(outcome="replaced", cover=replaced)


def _cover_from_draft(draft, *, revision: int) -> MediaCover:
    return MediaCover(
        media_id=draft.media_id,
        source_location_id=draft.source_location_id,
        source_reference=draft.source_reference,
        source_kind=draft.source_kind,
        source_timestamp_ms=draft.source_timestamp_ms,
        source_size_bytes=draft.source_size_bytes,
        source_mtime_ns=draft.source_mtime_ns,
        source_duration_ms=draft.source_duration_ms,
        source_observation_version=draft.source_observation_version,
        source_observation_digest=draft.source_observation_digest,
        artifact_profile=draft.artifact_profile,
        artifact_media_type=draft.artifact_media_type,
        artifact_digest=draft.artifact_digest,
        artifact_width=draft.artifact_width,
        artifact_height=draft.artifact_height,
        artifact_byte_size=draft.artifact_byte_size,
        revision=revision,
        accepted_at_ms=draft.accepted_at_ms,
    )


def _make_service(
    *,
    media_kind=MediaKind.VIDEO,
    availability=MediaLocationAvailability.AVAILABLE,
    extension=".mp4",
    analyzer=None,
    storage=None,
    thumbnails=None,
    now_ms: int = 1000,
):
    media = LogicalMedia(id=MEDIA_ID, kind=media_kind, created_at_ms=1, updated_at_ms=1)
    location = MediaLocation(
        id=LOCATION_ID,
        media_id=MEDIA_ID,
        library_id=LIBRARY_ID,
        relative_path=DomainMediaRelativePath(f"items/clip{extension}"),
        availability=availability,
        observed_size_bytes=1234,
        observed_mtime_ns=99,
        created_at_ms=1,
        updated_at_ms=1,
    )
    library = Library(
        id=LIBRARY_ID,
        device_id=DEVICE_ID,
        display_name="Movies",
        root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path="/media/movies"),
    )
    return CoverService(
        _FakeMediaRepository(media, location),
        _FakeLibraryRepository(library),
        analyzer or _FakeAnalyzer(),
        _FakeEncoder(),
        storage or _FakeStorage(),
        thumbnails or _FakeThumbnailCache(),
        _FakeCoverRepo(),
        now_ms=lambda: now_ms,
    ), media, location


def test_timeline_is_server_authoritative_and_opaque() -> None:
    service, _, _ = _make_service()
    timeline = service.timeline(MEDIA_ID, LOCATION_ID)
    assert timeline.duration_ms == 2000
    assert len(timeline.source_version) == 64
    assert "/" not in timeline.source_version


def test_unknown_or_mismatched_media_location_is_not_found() -> None:
    service, _, _ = _make_service()
    with pytest.raises(CoverMediaNotFoundError):
        service.timeline(MEDIA_ID, DISTANT_ID)
    with pytest.raises(CoverMediaNotFoundError):
        service.timeline(DISTANT_ID, LOCATION_ID)


def test_unsupported_or_unavailable_source_is_rejected() -> None:
    service, _, _ = _make_service(media_kind=MediaKind.VIDEO, extension=".mkv")
    with pytest.raises(CoverSourceUnavailableError):
        service.timeline(MEDIA_ID, LOCATION_ID)

    service2, _, _ = _make_service(media_kind=MediaKind.IMAGE, extension=".webp")
    with pytest.raises(CoverSourceUnavailableError):
        service2.timeline(MEDIA_ID, LOCATION_ID)

    service3, _, _ = _make_service(availability=MediaLocationAvailability.OFFLINE)
    with pytest.raises(CoverSourceUnavailableError):
        service3.timeline(MEDIA_ID, LOCATION_ID)


def test_preview_requires_expected_source_version() -> None:
    service, _, _ = _make_service()
    timeline = service.timeline(MEDIA_ID, LOCATION_ID)
    preview = service.preview(
        MEDIA_ID,
        LOCATION_ID,
        500,
        expected_source_version=timeline.source_version,
    )
    assert preview.media_type == "image/png"
    assert preview.payload

    with pytest.raises(CoverSourceChangedError):
        service.preview(MEDIA_ID, LOCATION_ID, 500, expected_source_version="0" * 64)


def test_preview_rejects_out_of_range_timestamp() -> None:
    service, _, _ = _make_service()
    timeline = service.timeline(MEDIA_ID, LOCATION_ID)
    with pytest.raises(CoverTimestampInvalidError):
        service.preview(MEDIA_ID, LOCATION_ID, 2000, expected_source_version=timeline.source_version)


def test_accept_creates_replaces_and_is_idempotent() -> None:
    service, _, _ = _make_service()
    timeline = service.timeline(MEDIA_ID, LOCATION_ID)
    created = service.accept(
        MEDIA_ID,
        LOCATION_ID,
        timestamp_ms=500,
        expected_revision=0,
        expected_source_version=timeline.source_version,
    )
    assert created.status == "created"
    assert created.revision == 1
    assert created.timestamp_ms == 500
    assert created.thumbnail_state == "ready"

    state = service.admin_state(MEDIA_ID)
    assert state.has_cover is True
    assert state.revision == 1
    assert state.thumbnail_state == "ready"

    unchanged = service.accept(
        MEDIA_ID,
        LOCATION_ID,
        timestamp_ms=500,
        expected_revision=1,
        expected_source_version=timeline.source_version,
    )
    assert unchanged.status == "unchanged"
    assert unchanged.revision == 1

    replaced = service.accept(
        MEDIA_ID,
        LOCATION_ID,
        timestamp_ms=1500,
        expected_revision=1,
        expected_source_version=timeline.source_version,
    )
    assert replaced.status == "replaced"
    assert replaced.revision == 2

    assert service.cover_ready_map((MEDIA_ID.to_string(),))[MEDIA_ID.to_string()] is True
    assert service.cover_ready_map((DISTANT_ID.to_string(),))[DISTANT_ID.to_string()] is False


def test_stale_revision_conflict_preserves_prior_cover() -> None:
    service, _, _ = _make_service()
    timeline = service.timeline(MEDIA_ID, LOCATION_ID)
    service.accept(MEDIA_ID, LOCATION_ID, timestamp_ms=500, expected_revision=0, expected_source_version=timeline.source_version)
    service.accept(MEDIA_ID, LOCATION_ID, timestamp_ms=1500, expected_revision=1, expected_source_version=timeline.source_version)
    with pytest.raises(CoverConflictError):
        service.accept(MEDIA_ID, LOCATION_ID, timestamp_ms=900, expected_revision=1, expected_source_version=timeline.source_version)
    state = service.admin_state(MEDIA_ID)
    assert state.revision == 2
    assert state.timestamp_ms == 1500


def test_changed_source_before_accept_is_rejected() -> None:
    service, _, _ = _make_service()
    with pytest.raises(CoverSourceChangedError):
        service.accept(MEDIA_ID, LOCATION_ID, timestamp_ms=500, expected_revision=0, expected_source_version="f" * 64)


def test_source_change_during_extraction_is_rejected_without_mutation() -> None:
    service, _, _ = _make_service(analyzer=_FakeAnalyzer(change_after_extract=True))
    timeline = service.timeline(MEDIA_ID, LOCATION_ID)
    with pytest.raises(CoverSourceChangedError):
        service.accept(MEDIA_ID, LOCATION_ID, timestamp_ms=500, expected_revision=0, expected_source_version=timeline.source_version)
    state = service.admin_state(MEDIA_ID)
    assert state.has_cover is False


def test_extraction_failure_leaves_no_cover() -> None:
    service, _, _ = _make_service(analyzer=_FakeAnalyzer(fail_extract=True))
    timeline = service.timeline(MEDIA_ID, LOCATION_ID)
    with pytest.raises(CoverSourceUnavailableError):
        service.accept(MEDIA_ID, LOCATION_ID, timestamp_ms=500, expected_revision=0, expected_source_version=timeline.source_version)
    assert service.admin_state(MEDIA_ID).has_cover is False


def test_artifact_publish_failure_leaves_no_cover() -> None:
    service, _, _ = _make_service(storage=_FakeStorage(fail_publish=True))
    timeline = service.timeline(MEDIA_ID, LOCATION_ID)
    with pytest.raises(CoverFailedError):
        service.accept(MEDIA_ID, LOCATION_ID, timestamp_ms=500, expected_revision=0, expected_source_version=timeline.source_version)
    assert service.admin_state(MEDIA_ID).has_cover is False


def test_thumbnail_failure_keeps_cover_successful_but_degraded() -> None:
    service, _, _ = _make_service(thumbnails=_FakeThumbnailCache(fail_publish=True))
    timeline = service.timeline(MEDIA_ID, LOCATION_ID)
    result = service.accept(MEDIA_ID, LOCATION_ID, timestamp_ms=500, expected_revision=0, expected_source_version=timeline.source_version)
    assert result.status == "created"
    assert result.thumbnail_state == "missing"
    state = service.admin_state(MEDIA_ID)
    assert state.has_cover is True
    assert state.artifact_state == "available"
    assert state.thumbnail_state == "missing"
    with pytest.raises(CoverMediaNotFoundError):
        service.open_thumbnail(MEDIA_ID)


def test_open_thumbnail_and_missing_derivative() -> None:
    service, _, _ = _make_service()
    timeline = service.timeline(MEDIA_ID, LOCATION_ID)
    service.accept(MEDIA_ID, LOCATION_ID, timestamp_ms=500, expected_revision=0, expected_source_version=timeline.source_version)
    opened = service.open_thumbnail(MEDIA_ID)
    assert opened.media_type == COVER_ARTIFACT_MEDIA_TYPE
    assert opened.byte_size > 0
    opened.close()

    with pytest.raises(CoverMediaNotFoundError):
        service.open_thumbnail(DISTANT_ID)


class _TwoPhaseAnalyzer:
    """Probe/extract fake whose observed source identity changes on request."""

    def __init__(self) -> None:
        self.changed = False
        self.calls = 0

    def probe(self, root, relative_path, kind):
        self.calls += 1
        if self.changed:
            return CoverSourceProbe(
                duration_ms=None,
                source_size_bytes=4322,
                source_mtime_ns=7777,
            )
        return CoverSourceProbe(
            duration_ms=None,
            source_size_bytes=4321,
            source_mtime_ns=1234,
        )

    def extract_frame(self, root, relative_path, kind, timestamp_ms):
        self.calls += 1
        if self.changed:
            return _frame(0)
        return _frame(0)


def test_timeless_image_timeline_reports_zero_duration_and_no_temporal_contract() -> None:
    service, _, _ = _make_service(media_kind=MediaKind.IMAGE, extension=".png")
    timeline = service.timeline(MEDIA_ID, LOCATION_ID)
    assert timeline.media_kind is MediaKind.IMAGE
    assert timeline.duration_ms == 0
    assert len(timeline.source_version) == 64
    assert "/" not in timeline.source_version


def test_timeless_image_preview_and_accept_use_canonical_zero_timestamp() -> None:
    service, _, _ = _make_service(media_kind=MediaKind.IMAGE, extension=".png")
    timeline = service.timeline(MEDIA_ID, LOCATION_ID)
    preview = service.preview(
        MEDIA_ID,
        LOCATION_ID,
        TIMELESS_IMAGE_TIMESTAMP_MS,
        expected_source_version=timeline.source_version,
    )
    assert preview.media_type == "image/png"
    assert preview.payload

    created = service.accept(
        MEDIA_ID,
        LOCATION_ID,
        timestamp_ms=TIMELESS_IMAGE_TIMESTAMP_MS,
        expected_revision=0,
        expected_source_version=timeline.source_version,
    )
    assert created.status == "created"
    assert created.revision == 1
    assert created.timestamp_ms == TIMELESS_IMAGE_TIMESTAMP_MS
    assert created.thumbnail_state == "ready"

    state = service.admin_state(MEDIA_ID)
    assert state.has_cover is True
    assert state.source_kind == CoverSourceKind.IMAGE.value
    assert state.timestamp_ms == TIMELESS_IMAGE_TIMESTAMP_MS


def test_timeless_image_rejects_nonzero_or_invalid_timestamp() -> None:
    service, _, _ = _make_service(media_kind=MediaKind.IMAGE, extension=".png")
    timeline = service.timeline(MEDIA_ID, LOCATION_ID)
    with pytest.raises(CoverTimestampInvalidError):
        service.preview(
            MEDIA_ID,
            LOCATION_ID,
            500,
            expected_source_version=timeline.source_version,
        )
    with pytest.raises(CoverTimestampInvalidError):
        service.accept(
            MEDIA_ID,
            LOCATION_ID,
            timestamp_ms=500,
            expected_revision=0,
            expected_source_version=timeline.source_version,
        )
    assert service.admin_state(MEDIA_ID).has_cover is False


def test_timeless_image_accept_is_idempotent_and_original_facts_are_sanitized() -> None:
    service, _, _ = _make_service(media_kind=MediaKind.IMAGE, extension=".png")
    timeline = service.timeline(MEDIA_ID, LOCATION_ID)
    created = service.accept(
        MEDIA_ID,
        LOCATION_ID,
        timestamp_ms=TIMELESS_IMAGE_TIMESTAMP_MS,
        expected_revision=0,
        expected_source_version=timeline.source_version,
    )
    assert created.status == "created"

    unchanged = service.accept(
        MEDIA_ID,
        LOCATION_ID,
        timestamp_ms=TIMELESS_IMAGE_TIMESTAMP_MS,
        expected_revision=1,
        expected_source_version=timeline.source_version,
    )
    assert unchanged.status == "unchanged"
    assert unchanged.revision == 1

    state = service.admin_state(MEDIA_ID)
    assert state.source_reference == f"location:{LOCATION_ID.to_string()}"
    assert "/" not in (state.source_reference or "")
    assert state.artifact_state == "available"


def test_timeless_image_replaces_old_cover_under_correct_revision_after_source_change() -> None:
    analyzer = _TwoPhaseAnalyzer()
    service, _, _ = _make_service(
        media_kind=MediaKind.IMAGE,
        extension=".png",
        analyzer=analyzer,
    )
    first = service.timeline(MEDIA_ID, LOCATION_ID)
    created = service.accept(
        MEDIA_ID,
        LOCATION_ID,
        timestamp_ms=TIMELESS_IMAGE_TIMESTAMP_MS,
        expected_revision=0,
        expected_source_version=first.source_version,
    )
    assert created.status == "created"
    assert created.revision == 1

    analyzer.changed = True
    second = service.timeline(MEDIA_ID, LOCATION_ID)
    assert second.source_version != first.source_version

    replaced = service.accept(
        MEDIA_ID,
        LOCATION_ID,
        timestamp_ms=TIMELESS_IMAGE_TIMESTAMP_MS,
        expected_revision=1,
        expected_source_version=second.source_version,
    )
    assert replaced.status == "replaced"
    assert replaced.revision == 2

    with pytest.raises(CoverConflictError):
        service.accept(
            MEDIA_ID,
            LOCATION_ID,
            timestamp_ms=TIMELESS_IMAGE_TIMESTAMP_MS,
            expected_revision=1,
            expected_source_version=second.source_version,
        )
    assert service.admin_state(MEDIA_ID).revision == 2


def test_timeless_image_source_change_before_accept_is_rejected() -> None:
    service, _, _ = _make_service(media_kind=MediaKind.IMAGE, extension=".png")
    timeline = service.timeline(MEDIA_ID, LOCATION_ID)
    with pytest.raises(CoverSourceChangedError):
        service.accept(
            MEDIA_ID,
            LOCATION_ID,
            timestamp_ms=TIMELESS_IMAGE_TIMESTAMP_MS,
            expected_revision=0,
            expected_source_version="f" * 64,
        )
    assert service.admin_state(MEDIA_ID).has_cover is False


def test_timeless_image_persisted_cover_uses_image_kind_and_no_duration() -> None:
    service, _, _ = _make_service(media_kind=MediaKind.IMAGE, extension=".jpg")
    timeline = service.timeline(MEDIA_ID, LOCATION_ID)
    service.accept(
        MEDIA_ID,
        LOCATION_ID,
        timestamp_ms=TIMELESS_IMAGE_TIMESTAMP_MS,
        expected_revision=0,
        expected_source_version=timeline.source_version,
    )
    state = service.admin_state(MEDIA_ID)
    assert state.has_cover is True
    assert state.source_kind == CoverSourceKind.IMAGE.value
    assert state.timestamp_ms == TIMELESS_IMAGE_TIMESTAMP_MS


def test_source_facts_are_sanitized() -> None:
    service, _, _ = _make_service()
    timeline = service.timeline(MEDIA_ID, LOCATION_ID)
    service.accept(MEDIA_ID, LOCATION_ID, timestamp_ms=500, expected_revision=0, expected_source_version=timeline.source_version)
    state = service.admin_state(MEDIA_ID)
    assert state.source_reference == f"location:{LOCATION_ID.to_string()}"
    assert "/" not in (state.source_reference or "")
