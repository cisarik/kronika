"""Pure-domain library entity and device-local root locators."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath

from kronika.domain.identities import DeviceId, LibraryId

INVALID_LIBRARY_MESSAGE = "Invalid Kronika library."
INVALID_ROOT_MESSAGE = "Invalid Kronika library root."
MAX_ROOT_PATH_CODE_POINTS = 4096


class FrameNestLibraryRootError(ValueError):
    """Sanitized error raised when a library root locator is invalid."""


class FrameNestLibraryError(ValueError):
    """Sanitized error raised when library construction is invalid."""


class LibraryPathFlavor(StrEnum):
    """Explicit path flavor for a device-local library root."""

    POSIX = "posix"
    WINDOWS = "windows"


def _validate_text_invariants(value: object, *, error: type[ValueError], message: str) -> str:
    if not isinstance(value, str):
        raise error(message)
    if not value:
        raise error(message)
    if value[0].isspace() or value[-1].isspace():
        raise error(message)
    if len(value) > MAX_ROOT_PATH_CODE_POINTS:
        raise error(message)
    for character in value:
        code_point = ord(character)
        if code_point <= 0x1F or code_point == 0x7F:
            raise error(message)
    return value


def _validate_display_name(value: object) -> str:
    if not isinstance(value, str):
        raise FrameNestLibraryError(INVALID_LIBRARY_MESSAGE)
    if not value:
        raise FrameNestLibraryError(INVALID_LIBRARY_MESSAGE)
    if value[0].isspace() or value[-1].isspace():
        raise FrameNestLibraryError(INVALID_LIBRARY_MESSAGE)
    if len(value) > 120:
        raise FrameNestLibraryError(INVALID_LIBRARY_MESSAGE)
    for character in value:
        code_point = ord(character)
        if code_point <= 0x1F or code_point == 0x7F:
            raise FrameNestLibraryError(INVALID_LIBRARY_MESSAGE)
    return value


def _validate_posix_path(value: str) -> str:
    validated = _validate_text_invariants(
        value,
        error=FrameNestLibraryRootError,
        message=INVALID_ROOT_MESSAGE,
    )
    parsed = PurePosixPath(validated)
    if not parsed.is_absolute():
        raise FrameNestLibraryRootError(INVALID_ROOT_MESSAGE)
    if any(part in (".", "..") for part in parsed.parts):
        raise FrameNestLibraryRootError(INVALID_ROOT_MESSAGE)
    canonical = str(parsed)
    if validated != canonical:
        raise FrameNestLibraryRootError(INVALID_ROOT_MESSAGE)
    return validated


def _validate_windows_path(value: str) -> str:
    validated = _validate_text_invariants(
        value,
        error=FrameNestLibraryRootError,
        message=INVALID_ROOT_MESSAGE,
    )
    parsed = PureWindowsPath(validated)
    if not parsed.is_absolute():
        raise FrameNestLibraryRootError(INVALID_ROOT_MESSAGE)
    if any(part in (".", "..") for part in parsed.parts):
        raise FrameNestLibraryRootError(INVALID_ROOT_MESSAGE)
    canonical = str(parsed)
    if validated != canonical:
        raise FrameNestLibraryRootError(INVALID_ROOT_MESSAGE)
    return validated


def _validate_root_path(flavor: LibraryPathFlavor, value: object) -> str:
    if flavor == LibraryPathFlavor.POSIX:
        if not isinstance(value, str):
            raise FrameNestLibraryRootError(INVALID_ROOT_MESSAGE)
        return _validate_posix_path(value)
    if flavor == LibraryPathFlavor.WINDOWS:
        if not isinstance(value, str):
            raise FrameNestLibraryRootError(INVALID_ROOT_MESSAGE)
        return _validate_windows_path(value)
    raise FrameNestLibraryRootError(INVALID_ROOT_MESSAGE)


@dataclass(frozen=True, slots=True)
class LibraryRoot:
    """Device-local canonical root path in an explicit path flavor."""

    flavor: LibraryPathFlavor
    path: str

    def __post_init__(self) -> None:
        if not isinstance(self.flavor, LibraryPathFlavor):
            raise FrameNestLibraryRootError(INVALID_ROOT_MESSAGE)
        _validate_root_path(self.flavor, self.path)


@dataclass(frozen=True, slots=True)
class Library:
    """An independently registered collection root owned by one device."""

    id: LibraryId
    device_id: DeviceId
    display_name: str
    root: LibraryRoot

    def __post_init__(self) -> None:
        if not isinstance(self.id, LibraryId):
            raise FrameNestLibraryError(INVALID_LIBRARY_MESSAGE)
        if not isinstance(self.device_id, DeviceId):
            raise FrameNestLibraryError(INVALID_LIBRARY_MESSAGE)
        if not isinstance(self.root, LibraryRoot):
            raise FrameNestLibraryError(INVALID_LIBRARY_MESSAGE)
        _validate_display_name(self.display_name)
