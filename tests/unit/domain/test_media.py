"""Contract tests for pure-domain logical media and physical locations."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from kronika.domain import LibraryId, MediaId, MediaLocationId
from kronika.domain.media import (
    FrameNestMediaError,
    FrameNestMediaLocationError,
    MediaKind,
    MediaLocation,
    MediaLocationAvailability,
    MediaRelativePath,
    LogicalMedia,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DOMAIN_MEDIA_MODULE = REPOSITORY_ROOT / "src" / "kronika" / "domain" / "media.py"
INVALID_MEDIA_MESSAGE = "Invalid Kronika media."
INVALID_LOCATION_MESSAGE = "Invalid Kronika media location."
INVALID_PATH_MESSAGE = "Invalid Kronika media relative path."


def test_valid_logical_media_construction() -> None:
    media_id = MediaId.new()
    media = LogicalMedia(
        id=media_id,
        kind=MediaKind.VIDEO,
        created_at_ms=10,
        updated_at_ms=20,
    )

    assert media.id == media_id
    assert media.kind == MediaKind.VIDEO
    assert media.created_at_ms == 10
    assert media.updated_at_ms == 20
    with pytest.raises(AttributeError):
        media.kind = MediaKind.ANIMATED_IMAGE  # type: ignore[misc]


def test_valid_location_construction_and_derived_filename() -> None:
    relative_path = MediaRelativePath("clips/reactions/wave.gif")
    location = MediaLocation(
        id=MediaLocationId.new(),
        media_id=MediaId.new(),
        library_id=LibraryId.new(),
        relative_path=relative_path,
        availability=MediaLocationAvailability.AVAILABLE,
        observed_size_bytes=123,
        observed_mtime_ns=456,
        created_at_ms=10,
        updated_at_ms=20,
    )

    assert location.relative_path.value == "clips/reactions/wave.gif"
    assert location.filename == "wave.gif"
    assert location.observed_size_bytes == 123
    assert location.observed_mtime_ns == 456


def test_valid_location_accepts_absent_optional_observations() -> None:
    location = MediaLocation(
        id=MediaLocationId.new(),
        media_id=MediaId.new(),
        library_id=LibraryId.new(),
        relative_path=MediaRelativePath("clip.mp4"),
        availability=MediaLocationAvailability.UNVERIFIED,
        observed_size_bytes=None,
        observed_mtime_ns=None,
        created_at_ms=0,
        updated_at_ms=0,
    )

    assert location.observed_size_bytes is None
    assert location.observed_mtime_ns is None


@pytest.mark.parametrize("kind", ["video", "animated_image", "image"])
def test_media_kind_values_are_stable(kind: str) -> None:
    assert MediaKind(kind).value == kind


@pytest.mark.parametrize("availability", ["available", "offline", "missing", "unverified", "archived"])
def test_availability_values_are_stable(availability: str) -> None:
    assert MediaLocationAvailability(availability).value == availability


@pytest.mark.parametrize("invalid_kind", ["gif", "image", "Video", None, 1])
def test_media_kind_validation(invalid_kind: Any) -> None:
    with pytest.raises(FrameNestMediaError, match=INVALID_MEDIA_MESSAGE):
        LogicalMedia(
            id=MediaId.new(),
            kind=invalid_kind,  # type: ignore[arg-type]
            created_at_ms=0,
            updated_at_ms=0,
        )


@pytest.mark.parametrize("invalid_availability", ["online", "local", "Available", None, 1])
def test_availability_validation(invalid_availability: Any) -> None:
    with pytest.raises(FrameNestMediaLocationError, match=INVALID_LOCATION_MESSAGE):
        MediaLocation(
            id=MediaLocationId.new(),
            media_id=MediaId.new(),
            library_id=LibraryId.new(),
            relative_path=MediaRelativePath("clip.mp4"),
            availability=invalid_availability,  # type: ignore[arg-type]
            observed_size_bytes=None,
            observed_mtime_ns=None,
            created_at_ms=0,
            updated_at_ms=0,
        )


@pytest.mark.parametrize(
    ("raw_path", "normalized"),
    [
        ("clip.mp4", "clip.mp4"),
        ("clips/reaction.gif", "clips/reaction.gif"),
        ("clips\\reaction.gif", "clips/reaction.gif"),
    ],
)
def test_relative_path_normalization(raw_path: str, normalized: str) -> None:
    path = MediaRelativePath(raw_path)

    assert path.value == normalized
    assert str(path) == normalized
    assert path.filename == normalized.rsplit("/", maxsplit=1)[-1]


@pytest.mark.parametrize(
    "rejected",
    [
        "",
        "/absolute/clip.mp4",
        "C:\\absolute\\clip.mp4",
        "\\\\server\\share\\clip.mp4",
        "clips/../secret.mp4",
        "clips/./clip.mp4",
        "../clip.mp4",
        "./clip.mp4",
        "clips//clip.mp4",
        "clips/\u0000secret.mp4",
        ".",
        "..",
    ],
)
def test_invalid_relative_paths_are_rejected(rejected: str) -> None:
    with pytest.raises(ValueError) as exc_info:
        MediaRelativePath(rejected)

    assert str(exc_info.value) == INVALID_PATH_MESSAGE
    if rejected and rejected != ".":
        assert rejected not in str(exc_info.value)


@pytest.mark.parametrize("invalid_size", [-1, True, "1"])
def test_optional_size_validation(invalid_size: Any) -> None:
    with pytest.raises(FrameNestMediaLocationError, match=INVALID_LOCATION_MESSAGE):
        MediaLocation(
            id=MediaLocationId.new(),
            media_id=MediaId.new(),
            library_id=LibraryId.new(),
            relative_path=MediaRelativePath("clip.mp4"),
            availability=MediaLocationAvailability.AVAILABLE,
            observed_size_bytes=invalid_size,  # type: ignore[arg-type]
            observed_mtime_ns=None,
            created_at_ms=0,
            updated_at_ms=0,
        )


@pytest.mark.parametrize("invalid_time", [-1, True, "1"])
def test_optional_modification_time_validation(invalid_time: Any) -> None:
    with pytest.raises(FrameNestMediaLocationError, match=INVALID_LOCATION_MESSAGE):
        MediaLocation(
            id=MediaLocationId.new(),
            media_id=MediaId.new(),
            library_id=LibraryId.new(),
            relative_path=MediaRelativePath("clip.mp4"),
            availability=MediaLocationAvailability.AVAILABLE,
            observed_size_bytes=None,
            observed_mtime_ns=invalid_time,  # type: ignore[arg-type]
            created_at_ms=0,
            updated_at_ms=0,
        )


@pytest.mark.parametrize("invalid_timestamp", [-1, True, "1"])
def test_entity_timestamp_validation(invalid_timestamp: Any) -> None:
    with pytest.raises(FrameNestMediaError, match=INVALID_MEDIA_MESSAGE):
        LogicalMedia(
            id=MediaId.new(),
            kind=MediaKind.VIDEO,
            created_at_ms=invalid_timestamp,  # type: ignore[arg-type]
            updated_at_ms=0,
        )


def test_media_domain_module_imports_no_infrastructure_or_framework() -> None:
    tree = ast.parse(DOMAIN_MEDIA_MODULE.read_text(encoding="utf-8"))
    forbidden_roots = {
        "alembic",
        "fastapi",
        "pydantic",
        "pydantic_settings",
        "sqlalchemy",
        "starlette",
        "uvicorn",
        "os",
        "subprocess",
        "kronika.infrastructure",
        "kronika.application",
        "kronika.adapters",
        "kronika.configuration",
    }
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module = node.names[0].name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
        else:
            continue
        root = module.split(".")[0]
        if root in forbidden_roots or any(module.startswith(prefix) for prefix in forbidden_roots):
            violations.append(module)
    assert violations == []
