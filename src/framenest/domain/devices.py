"""Pure-domain device entity for the local device registry."""

from __future__ import annotations

from dataclasses import dataclass

from framenest.domain.identities import DeviceId

INVALID_DEVICE_MESSAGE = "Invalid Kronika device."


class FrameNestDeviceError(ValueError):
    """Sanitized error raised when device construction is invalid."""


def _validate_display_name(value: object) -> str:
    if not isinstance(value, str):
        raise FrameNestDeviceError(INVALID_DEVICE_MESSAGE)
    if not value:
        raise FrameNestDeviceError(INVALID_DEVICE_MESSAGE)
    if value[0].isspace() or value[-1].isspace():
        raise FrameNestDeviceError(INVALID_DEVICE_MESSAGE)
    if len(value) > 120:
        raise FrameNestDeviceError(INVALID_DEVICE_MESSAGE)
    for character in value:
        code_point = ord(character)
        if code_point <= 0x1F or code_point == 0x7F:
            raise FrameNestDeviceError(INVALID_DEVICE_MESSAGE)
    return value


@dataclass(frozen=True, slots=True)
class Device:
    """A machine or storage-capable host known to FrameNest."""

    id: DeviceId
    display_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, DeviceId):
            raise FrameNestDeviceError(INVALID_DEVICE_MESSAGE)
        _validate_display_name(self.display_name)
