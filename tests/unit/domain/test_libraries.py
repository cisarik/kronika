"""Contract tests for pure-domain library and root-locator types."""

from __future__ import annotations

import ast
from enum import Enum
from pathlib import Path
from typing import Any

import pytest

from kronika.domain import (
    DeviceId,
    FrameNestLibraryError,
    FrameNestLibraryRootError,
    Library,
    LibraryId,
    LibraryPathFlavor,
    LibraryRoot,
    MediaId,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
LIBRARIES_MODULE = REPOSITORY_ROOT / "src" / "kronika" / "domain" / "libraries.py"
INVALID_LIBRARY_MESSAGE = "Invalid Kronika library."
INVALID_ROOT_MESSAGE = "Invalid Kronika library root."
SECRET_REJECTED_PATH = "/secret/rejected/path"
SECRET_REJECTED_NAME = "secret-rejected-name"


def _library(
    *,
    library_id: LibraryId | None = None,
    device_id: DeviceId | None = None,
    display_name: str = "Main Library",
    root: LibraryRoot | None = None,
) -> Library:
    return Library(
        id=library_id or LibraryId.new(),
        device_id=device_id or DeviceId.new(),
        display_name=display_name,
        root=root or LibraryRoot(flavor=LibraryPathFlavor.POSIX, path="/media/main"),
    )


def test_posix_root_round_trips_exactly() -> None:
    root = LibraryRoot(flavor=LibraryPathFlavor.POSIX, path="/")
    assert root.path == "/"
    assert root.flavor == LibraryPathFlavor.POSIX


def test_posix_nested_path_round_trips_exactly() -> None:
    path = "/media/video/library"
    root = LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=path)
    assert root.path == path


def test_windows_drive_root_round_trips_exactly() -> None:
    path = "C:\\Media\\Library"
    root = LibraryRoot(flavor=LibraryPathFlavor.WINDOWS, path=path)
    assert root.path == path


def test_windows_unc_root_round_trips_exactly() -> None:
    path = "\\\\server\\share\\media"
    root = LibraryRoot(flavor=LibraryPathFlavor.WINDOWS, path=path)
    assert root.path == path


@pytest.mark.parametrize(
    ("flavor", "path"),
    [
        (LibraryPathFlavor.POSIX, "media/relative"),
        (LibraryPathFlavor.POSIX, "relative"),
        (LibraryPathFlavor.WINDOWS, "Media\\Relative"),
        (LibraryPathFlavor.WINDOWS, "relative\\path"),
    ],
)
def test_relative_paths_are_rejected(flavor: LibraryPathFlavor, path: str) -> None:
    with pytest.raises(FrameNestLibraryRootError, match=INVALID_ROOT_MESSAGE):
        LibraryRoot(flavor=flavor, path=path)


@pytest.mark.parametrize(
    ("flavor", "path"),
    [
        (LibraryPathFlavor.POSIX, "/media/../secret"),
        (LibraryPathFlavor.POSIX, "/media/./library"),
        (LibraryPathFlavor.WINDOWS, "C:\\Media\\..\\secret"),
        (LibraryPathFlavor.WINDOWS, "C:\\Media\\.\\library"),
    ],
)
def test_dot_segments_are_rejected(flavor: LibraryPathFlavor, path: str) -> None:
    with pytest.raises(FrameNestLibraryRootError, match=INVALID_ROOT_MESSAGE):
        LibraryRoot(flavor=flavor, path=path)


@pytest.mark.parametrize(
    ("flavor", "path"),
    [
        (LibraryPathFlavor.POSIX, "/media//library"),
        (LibraryPathFlavor.WINDOWS, "C:/Media/Library"),
    ],
)
def test_noncanonical_paths_are_rejected_not_normalized(
    flavor: LibraryPathFlavor,
    path: str,
) -> None:
    with pytest.raises(FrameNestLibraryRootError, match=INVALID_ROOT_MESSAGE):
        LibraryRoot(flavor=flavor, path=path)


@pytest.mark.parametrize("invalid_flavor", ["posix", "windows", "macos", None, 1])
def test_invalid_flavors_are_rejected(invalid_flavor: Any) -> None:
    with pytest.raises(FrameNestLibraryRootError, match=INVALID_ROOT_MESSAGE):
        LibraryRoot(flavor=invalid_flavor, path="/media")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "path",
    [
        "",
        " /media",
        "/media ",
        "/media\u0000name",
        "/media\u001fname",
        "/media\u007fname",
        "/" + ("a" * 4096),
    ],
)
def test_invalid_root_path_text_is_rejected(path: str) -> None:
    with pytest.raises(FrameNestLibraryRootError, match=INVALID_ROOT_MESSAGE):
        LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=path)


def test_unicode_root_path_text_is_accepted_when_canonical() -> None:
    path = "/media/Café/日本語"
    root = LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=path)
    assert root.path == path


def test_library_root_is_immutable_and_hashable() -> None:
    first = LibraryRoot(flavor=LibraryPathFlavor.POSIX, path="/media/a")
    same = LibraryRoot(flavor=LibraryPathFlavor.POSIX, path="/media/a")
    other = LibraryRoot(flavor=LibraryPathFlavor.POSIX, path="/media/b")

    assert first == same
    assert first != other
    assert hash(first) == hash(same)
    assert {first, same, other} == {first, other}


def test_library_validates_category_specific_ids() -> None:
    with pytest.raises(FrameNestLibraryError, match=INVALID_LIBRARY_MESSAGE):
        Library(
            id=DeviceId.new(),  # type: ignore[arg-type]
            device_id=DeviceId.new(),
            display_name="Main",
            root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path="/media"),
        )
    with pytest.raises(FrameNestLibraryError, match=INVALID_LIBRARY_MESSAGE):
        Library(
            id=LibraryId.new(),
            device_id=MediaId.new(),  # type: ignore[arg-type]
            display_name="Main",
            root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path="/media"),
        )


@pytest.mark.parametrize("display_name", ["Café", "日本語", "emoji 🎬"])
def test_valid_unicode_display_names_are_accepted(display_name: str) -> None:
    library = _library(display_name=display_name)
    assert library.display_name == display_name


@pytest.mark.parametrize(
    "display_name",
    ["", "   ", " leading", "trailing ", "a" * 121, "bad\u0000name"],
)
def test_invalid_display_names_are_rejected(display_name: str) -> None:
    with pytest.raises(FrameNestLibraryError, match=INVALID_LIBRARY_MESSAGE):
        _library(display_name=display_name)


def test_sanitized_errors_do_not_echo_paths_or_names() -> None:
    with pytest.raises(FrameNestLibraryRootError) as root_error:
        LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=SECRET_REJECTED_PATH + "/..")
    assert str(root_error.value) == INVALID_ROOT_MESSAGE
    assert SECRET_REJECTED_PATH not in str(root_error.value)

    with pytest.raises(FrameNestLibraryError) as library_error:
        _library(display_name=SECRET_REJECTED_NAME + "\u0000")
    assert str(library_error.value) == INVALID_LIBRARY_MESSAGE
    assert SECRET_REJECTED_NAME not in str(library_error.value)


def test_domain_libraries_module_imports_no_infrastructure_or_framework() -> None:
    tree = ast.parse(LIBRARIES_MODULE.read_text(encoding="utf-8"))
    forbidden_roots = {
        "alembic",
        "fastapi",
        "pydantic",
        "pydantic_settings",
        "sqlalchemy",
        "starlette",
        "uvicorn",
        "os",
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
        if root in forbidden_roots or any(
            module.startswith(prefix)
            for prefix in (
                "kronika.infrastructure",
                "kronika.application",
                "kronika.adapters",
                "kronika.configuration",
            )
        ):
            violations.append(module)
        if isinstance(node, ast.ImportFrom) and module == "pathlib":
            if any(alias.name == "Path" for alias in node.names):
                violations.append("pathlib.Path")
    assert violations == []


def test_library_path_flavor_is_explicit_string_enum() -> None:
    assert issubclass(LibraryPathFlavor, Enum)
    assert LibraryPathFlavor.POSIX.value == "posix"
    assert LibraryPathFlavor.WINDOWS.value == "windows"
