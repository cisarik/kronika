"""Application port for the local device registry."""

from __future__ import annotations

from typing import Protocol

from kronika.domain import Device, DeviceId


class DeviceAlreadyExistsError(RuntimeError):
    """Raised when a device with the same identity is already registered."""


class FrameNestDeviceRepositoryError(RuntimeError):
    """Sanitized error raised when device repository operations fail."""


class DeviceRepository(Protocol):
    """Persistence-independent device registry contract."""

    def add(self, device: Device) -> None:
        """Register one valid device."""

    def get(self, device_id: DeviceId) -> Device | None:
        """Return one device by identity, or None when absent."""

    def list_all(self) -> tuple[Device, ...]:
        """Return all registered devices in deterministic order."""
