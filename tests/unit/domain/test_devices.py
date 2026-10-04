"""Contract tests for the pure-domain Device entity."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from framenest.domain import Device, DeviceId, FrameNestDeviceError, LibraryId, MediaId

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DOMAIN_DEVICES_MODULE = REPOSITORY_ROOT / "src" / "framenest" / "domain" / "devices.py"
EXPECTED_ERROR_MESSAGE = "Invalid Kronika device."
CANONICAL_UUID4_TEXT = "12345678-1234-4234-9234-123456789abc"
SECOND_CANONICAL_UUID4_TEXT = "abcdefab-cdef-4abc-8def-abcdefabcdef"


def _device(display_name: str = "Studio Mac") -> Device:
    return Device(id=DeviceId.new(), display_name=display_name)


def test_valid_device_id_and_display_name_create_immutable_device() -> None:
    device_id = DeviceId.from_string(CANONICAL_UUID4_TEXT)
    device = Device(id=device_id, display_name="Studio Mac")

    assert device.id == device_id
    assert device.display_name == "Studio Mac"
    with pytest.raises(AttributeError):
        device.display_name = "Other"  # type: ignore[misc]


def test_device_equality_and_hashing() -> None:
    device_id = DeviceId.from_string(CANONICAL_UUID4_TEXT)
    first = Device(id=device_id, display_name="Studio Mac")
    same = Device(id=device_id, display_name="Studio Mac")
    other = Device(id=DeviceId.from_string(SECOND_CANONICAL_UUID4_TEXT), display_name="Studio Mac")

    assert first == same
    assert first != other
    assert hash(first) == hash(same)
    assert {first, same, other} == {first, other}


@pytest.mark.parametrize(
    "invalid_id",
    [
        CANONICAL_UUID4_TEXT,
        DeviceId.from_string(CANONICAL_UUID4_TEXT).value,
        MediaId.new(),
        LibraryId.new(),
        None,
        123,
        object(),
    ],
)
def test_invalid_id_types_are_rejected(invalid_id: Any) -> None:
    with pytest.raises(FrameNestDeviceError, match=EXPECTED_ERROR_MESSAGE):
        Device(id=invalid_id, display_name="Studio Mac")


@pytest.mark.parametrize(
    "display_name",
    [
        "",
        "   ",
        "\t",
        " leading",
        "trailing ",
        " both ",
        "a" * 121,
        "valid\u0000name",
        "valid\u001fname",
        "valid\u007fname",
    ],
)
def test_invalid_display_names_are_rejected(display_name: str) -> None:
    with pytest.raises(FrameNestDeviceError, match=EXPECTED_ERROR_MESSAGE):
        Device(id=DeviceId.new(), display_name=display_name)


@pytest.mark.parametrize(
    "display_name",
    [
        "Café",
        "日本語",
        "emoji 🎬",
        "Mixed Café 日本語 🎬",
    ],
)
def test_valid_unicode_display_names_are_accepted(display_name: str) -> None:
    device = Device(id=DeviceId.new(), display_name=display_name)
    assert device.display_name == display_name


def test_display_name_is_not_silently_normalized() -> None:
    device = Device(id=DeviceId.new(), display_name="Café")
    assert device.display_name == "Café"
    assert device.display_name != "café"


def test_device_error_does_not_echo_invalid_input() -> None:
    invalid_name = "secret-invalid-name \u0000"

    with pytest.raises(FrameNestDeviceError) as exc_info:
        Device(id=DeviceId.new(), display_name=invalid_name)

    rendered = str(exc_info.value)
    assert rendered == EXPECTED_ERROR_MESSAGE
    assert invalid_name not in rendered


def test_domain_devices_module_imports_no_infrastructure_or_framework() -> None:
    tree = ast.parse(DOMAIN_DEVICES_MODULE.read_text(encoding="utf-8"))
    forbidden_roots = {
        "alembic",
        "fastapi",
        "pydantic",
        "pydantic_settings",
        "sqlalchemy",
        "starlette",
        "uvicorn",
        "framenest.infrastructure",
        "framenest.application",
        "framenest.adapters",
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
