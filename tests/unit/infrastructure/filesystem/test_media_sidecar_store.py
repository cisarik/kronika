"""Unit tests for the secure adjacent media sidecar filesystem store."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from kronika.application.ports.media_sidecar_store import (
    SIDECAR_LOCATION_NOT_WRITABLE,
    SIDECAR_UNAVAILABLE,
    SIDECAR_UNSAFE_TARGET,
    MediaSidecarStoreError,
    SidecarTargetKind,
    sidecar_filename,
)
from kronika.domain.libraries import LibraryPathFlavor, LibraryRoot
from kronika.domain.identities import LibraryId, MediaId, MediaLocationId
from kronika.domain.media import FrameNestMediaRelativePathError, MediaKind, MediaRelativePath
from kronika.domain.media_classification import AcquisitionSource, ContentCategory
from kronika.domain.media_sidecar import (
    MAX_SIDECAR_BYTES,
    SidecarDocument,
    SidecarLocation,
    decode_media_sidecar,
    encode_media_sidecar,
)
from kronika.infrastructure.filesystem.media_sidecar import FilesystemMediaSidecarStore

PRIVATE_MARKER = "/home/private/secret.mp4"
PAYLOAD_MARKER = "PAYLOAD_MARKER_9f3a"
MEDIA_ID = MediaId.from_string("12345678-1234-4234-9234-123456789abc")
LOCATION_ID = MediaLocationId.from_string("abcdefab-cdef-4abc-8def-abcdefabcdef")
LIBRARY_ID = LibraryId.from_string("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")


def _root(path: Path) -> LibraryRoot:
    return LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=str(path))


def _document(*, relative_path: str = "movies/clip.mp4") -> SidecarDocument:
    return SidecarDocument(
        media_id=MEDIA_ID,
        media_kind=MediaKind.VIDEO,
        display_title=None,
        description=None,
        tag_keys=(),
        tag_definitions=(),
        content_category=ContentCategory.GENERAL,
        acquisition_source=AcquisitionSource.UNKNOWN,
        genre_keys=(),
        creator_attribution_kind=None,
        creator_stable_id=None,
        creator_handle=None,
        creator_display_name=None,
        processed=None,
        created_at_ms=None,
        updated_at_ms=None,
        location=SidecarLocation(
            location_id=LOCATION_ID,
            library_id=LIBRARY_ID,
            relative_path=MediaRelativePath(relative_path),
        ),
    )


def _prepare_media(tmp_path: Path, *, relative: str = "movies/clip.mp4") -> tuple[Path, MediaRelativePath]:
    library = tmp_path / "library"
    media_path = library.joinpath(*relative.split("/"))
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"media-bytes")
    return library, MediaRelativePath(relative)


def _sidecar_path(library: Path, relative: MediaRelativePath) -> Path:
    return library.joinpath(*relative.value.split("/")[:-1], sidecar_filename(relative))


def _expect_error(exc: BaseException, *, error_code: str) -> None:
    assert isinstance(exc, MediaSidecarStoreError)
    assert exc.error_code == error_code
    assert PRIVATE_MARKER not in str(exc)
    assert PAYLOAD_MARKER not in str(exc)


def test_adjacent_filename_uses_complete_media_filename() -> None:
    assert sidecar_filename(MediaRelativePath("movies/clip.mp4")) == "clip.mp4.framenest.json"


def test_create_replace_and_readback_are_byte_identical_mode_0644(tmp_path: Path) -> None:
    library, relative = _prepare_media(tmp_path)
    store = FilesystemMediaSidecarStore()
    payload = encode_media_sidecar(_document())
    store.create_adjacent(_root(library), relative, payload)
    target = _sidecar_path(library, relative)
    assert target.read_bytes() == payload
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    observed = store.observe_adjacent(_root(library), relative)
    assert observed.kind is SidecarTargetKind.REGULAR
    assert observed.payload == payload

    changed = SidecarDocument(
        media_id=MEDIA_ID,
        media_kind=MediaKind.VIDEO,
        display_title=None,
        description=None,
        tag_keys=(),
        tag_definitions=(),
        content_category=ContentCategory.GENERAL,
        acquisition_source=AcquisitionSource.UNKNOWN,
        genre_keys=(),
        creator_attribution_kind=None,
        creator_stable_id=None,
        creator_handle=None,
        creator_display_name=None,
        processed=None,
        created_at_ms=1,
        updated_at_ms=1,
        location=SidecarLocation(
            location_id=LOCATION_ID,
            library_id=LIBRARY_ID,
            relative_path=MediaRelativePath("movies/clip.mp4"),
        ),
    )
    changed_bytes = encode_media_sidecar(changed)
    store.replace_adjacent(_root(library), relative, changed_bytes)
    assert target.read_bytes() == changed_bytes
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_non_canonical_same_identity_regular_file_can_be_replaced(tmp_path: Path) -> None:
    library, relative = _prepare_media(tmp_path)
    canonical = encode_media_sidecar(_document())
    target = _sidecar_path(library, relative)
    parsed = json.loads(canonical)
    pretty = (json.dumps(parsed, indent=2, sort_keys=False) + "\n").encode("utf-8")
    assert pretty != canonical
    assert decode_media_sidecar(pretty).media_id == MEDIA_ID
    target.write_bytes(pretty)
    FilesystemMediaSidecarStore().replace_adjacent(_root(library), relative, canonical)
    assert target.read_bytes() == canonical


def test_malformed_unsupported_foreign_symlink_directory_and_fifo_are_preserved(
    tmp_path: Path,
) -> None:
    library, relative = _prepare_media(tmp_path)
    store = FilesystemMediaSidecarStore()
    target = _sidecar_path(library, relative)

    malformed = b'{"not":"a sidecar",' + PAYLOAD_MARKER.encode() + b"}\n"
    target.write_bytes(malformed)
    observed = store.observe_adjacent(_root(library), relative)
    assert observed.kind is SidecarTargetKind.REGULAR
    assert observed.payload == malformed
    assert target.read_bytes() == malformed

    target.write_bytes(b"keep-me")
    os.remove(target)
    os.symlink("somewhere", target)
    with pytest.raises(MediaSidecarStoreError) as exc_info:
        store.replace_adjacent(_root(library), relative, encode_media_sidecar(_document()))
    _expect_error(exc_info.value, error_code=SIDECAR_UNSAFE_TARGET)
    assert target.is_symlink()

    os.remove(target)
    target.mkdir()
    with pytest.raises(MediaSidecarStoreError) as dir_info:
        store.replace_adjacent(_root(library), relative, encode_media_sidecar(_document()))
    _expect_error(dir_info.value, error_code=SIDECAR_UNSAFE_TARGET)
    assert target.is_dir()
    target.rmdir()

    os.mkfifo(target)
    with pytest.raises(MediaSidecarStoreError) as fifo_info:
        store.replace_adjacent(_root(library), relative, encode_media_sidecar(_document()))
    _expect_error(fifo_info.value, error_code=SIDECAR_UNSAFE_TARGET)
    assert stat.S_ISFIFO(os.lstat(target).st_mode)


def test_source_media_symlink_and_symlink_parent_are_refused(tmp_path: Path) -> None:
    library, relative = _prepare_media(tmp_path)
    media = library.joinpath(*relative.value.split("/"))
    real = media.parent / "real.mp4"
    os.rename(media, real)
    os.symlink(real.name, media)
    store = FilesystemMediaSidecarStore()
    with pytest.raises(MediaSidecarStoreError) as exc_info:
        store.observe_adjacent(_root(library), relative)
    _expect_error(exc_info.value, error_code=SIDECAR_UNSAFE_TARGET)

    other = tmp_path / "other-library"
    other.mkdir()
    nested = other / "movies"
    nested.mkdir()
    (nested / "clip.mp4").write_bytes(b"x")
    alias_parent = library / "alias"
    os.symlink(nested, alias_parent)
    alias_relative = MediaRelativePath("alias/clip.mp4")
    with pytest.raises(MediaSidecarStoreError) as parent_info:
        store.observe_adjacent(_root(library), alias_relative)
    _expect_error(parent_info.value, error_code=SIDECAR_UNSAFE_TARGET)


def test_non_native_root_is_refused(tmp_path: Path) -> None:
    windows_root = LibraryRoot(flavor=LibraryPathFlavor.WINDOWS, path=r"C:\media")
    with pytest.raises(MediaSidecarStoreError) as exc_info:
        FilesystemMediaSidecarStore().observe_adjacent(
            windows_root,
            MediaRelativePath("clip.mp4"),
        )
    _expect_error(exc_info.value, error_code=SIDECAR_UNAVAILABLE)


def test_traversal_and_symlink_root_are_refused(tmp_path: Path) -> None:
    with pytest.raises(FrameNestMediaRelativePathError):
        MediaRelativePath("../secret.mp4")
    library, _relative = _prepare_media(tmp_path)
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"outside")
    injected = object.__new__(MediaRelativePath)
    object.__setattr__(injected, "value", "../secret.mp4")
    with pytest.raises(MediaSidecarStoreError) as traversal_info:
        FilesystemMediaSidecarStore().observe_adjacent(_root(library), injected)
    _expect_error(traversal_info.value, error_code=SIDECAR_UNSAFE_TARGET)
    assert outside.read_bytes() == b"outside"

    alias = tmp_path / "alias-root"
    os.symlink(library, alias)
    with pytest.raises(MediaSidecarStoreError) as root_info:
        FilesystemMediaSidecarStore().observe_adjacent(
            _root(alias),
            MediaRelativePath("movies/clip.mp4"),
        )
    _expect_error(root_info.value, error_code=SIDECAR_UNSAFE_TARGET)


def test_unwritable_parent_is_location_not_writable(tmp_path: Path) -> None:
    library, relative = _prepare_media(tmp_path)
    parent = library.joinpath(*relative.value.split("/")[:-1])
    os.chmod(parent, 0o555)
    try:
        with pytest.raises(MediaSidecarStoreError) as exc_info:
            FilesystemMediaSidecarStore().create_adjacent(
                _root(library),
                relative,
                encode_media_sidecar(_document()),
            )
        _expect_error(exc_info.value, error_code=SIDECAR_LOCATION_NOT_WRITABLE)
    finally:
        os.chmod(parent, 0o755)


def test_bounded_read_rejects_oversize(tmp_path: Path) -> None:
    library, relative = _prepare_media(tmp_path)
    target = _sidecar_path(library, relative)
    target.write_bytes(b"x" * (MAX_SIDECAR_BYTES + 1))
    with pytest.raises(MediaSidecarStoreError) as exc_info:
        FilesystemMediaSidecarStore().observe_adjacent(_root(library), relative)
    _expect_error(exc_info.value, error_code="SIDECAR_MALFORMED")


def test_explicit_symlink_is_unsafe_not_missing(tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4.framenest.json"
    os.symlink("missing-target", path)
    with pytest.raises(MediaSidecarStoreError) as exc_info:
        FilesystemMediaSidecarStore().observe_explicit(str(path))
    _expect_error(exc_info.value, error_code=SIDECAR_UNSAFE_TARGET)


def test_previous_target_survives_temp_validation_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    library, relative = _prepare_media(tmp_path)
    store = FilesystemMediaSidecarStore()
    original = encode_media_sidecar(_document())
    store.create_adjacent(_root(library), relative, original)
    target = _sidecar_path(library, relative)
    planted = target.parent / ".framenest-sidecar.deadbeefdeadbeef.tmp"
    planted.write_bytes(b"do-not-delete")

    def fail_validate(payload: bytes):
        del payload
        raise RuntimeError("injected validation failure")

    monkeypatch.setattr(
        "kronika.infrastructure.filesystem.media_sidecar.decode_media_sidecar",
        fail_validate,
    )
    with pytest.raises(MediaSidecarStoreError):
        store.replace_adjacent(_root(library), relative, encode_media_sidecar(_document(relative_path="movies/clip.mp4")))
    assert target.read_bytes() == original
    assert planted.read_bytes() == b"do-not-delete"
    leftovers = list(target.parent.glob(".framenest-sidecar.*.tmp"))
    assert leftovers == [planted]


def test_previous_target_survives_replace_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    library, relative = _prepare_media(tmp_path)
    store = FilesystemMediaSidecarStore()
    original = encode_media_sidecar(_document())
    store.create_adjacent(_root(library), relative, original)
    target = _sidecar_path(library, relative)

    def fail_replace(*args, **kwargs):
        del args, kwargs
        raise OSError("injected replace failure")

    monkeypatch.setattr(
        "kronika.infrastructure.filesystem.media_sidecar.os.replace",
        fail_replace,
    )
    with pytest.raises(MediaSidecarStoreError):
        store.replace_adjacent(
            _root(library),
            relative,
            encode_media_sidecar(
                SidecarDocument(
                    media_id=MEDIA_ID,
                    media_kind=MediaKind.VIDEO,
                    display_title=None,
                    description=None,
                    tag_keys=(),
                    tag_definitions=(),
                    content_category=ContentCategory.GENERAL,
                    acquisition_source=AcquisitionSource.UNKNOWN,
                    genre_keys=(),
                    creator_attribution_kind=None,
                    creator_stable_id=None,
                    creator_handle=None,
                    creator_display_name=None,
                    processed=None,
                    created_at_ms=3,
                    updated_at_ms=3,
                    location=SidecarLocation(
                        location_id=LOCATION_ID,
                        library_id=LIBRARY_ID,
                        relative_path=MediaRelativePath("movies/clip.mp4"),
                    ),
                )
            ),
        )
    assert target.read_bytes() == original
    assert list(target.parent.glob(".framenest-sidecar.*.tmp")) == []
