"""Byte-deterministic stored ZIP packs of ordered JPEG frames."""

from __future__ import annotations

import struct
import zlib
from collections.abc import Sequence

from kronika.infrastructure.ai.chatgpt_page.errors import BoundedPreparationError

ZIP_STORED = 0
LOCAL_HEADER_SIGNATURE = 0x04034B50
CENTRAL_HEADER_SIGNATURE = 0x02014B50
EOCD_SIGNATURE = 0x06054B50
FIXED_EOCD_BYTES = 22
PER_ENTRY_FIXED_BYTES = 76
# DOS date for 1980-01-01 and time 00:00:00. ZIP epoch; independent of the host clock.
FIXED_DOS_TIME = 0
FIXED_DOS_DATE = (1 << 5) | 1
FRAME_NAME_WIDTH = 4
MAX_FRAME_COUNT = 10_000 - 1


def frame_name(index: int) -> str:
    """Return the ASCII member name for one 1-based chronological frame."""

    if not isinstance(index, int) or isinstance(index, bool) or not 1 <= index <= MAX_FRAME_COUNT:
        raise BoundedPreparationError("frame index is outside 1..9999")
    return f"frame-{index:0{FRAME_NAME_WIDTH}d}.jpg"


def zip_overhead(names: Sequence[str]) -> int:
    """Exact stored-ZIP overhead for these ASCII names and no extra fields."""

    total = FIXED_EOCD_BYTES
    for name in names:
        encoded = _ascii_name(name)
        total += PER_ENTRY_FIXED_BYTES + 2 * len(encoded)
    return total


def zip_overhead_for_count(count: int) -> int:
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise BoundedPreparationError("frame count must be a non-negative integer")
    if count > MAX_FRAME_COUNT:
        raise BoundedPreparationError("frame count is outside 0..9999")
    return zip_overhead(tuple(frame_name(index) for index in range(1, count + 1)))


def pack_frames(frames: Sequence[bytes]) -> bytes:
    """Pack ordered frame payloads as ``frame-0001.jpg`` upward.

    The archive uses stored compression, a fixed timestamp, and no directories,
    comments, extra fields, encryption, or manifest. Identical input produces
    identical bytes.
    """

    if not isinstance(frames, Sequence) or isinstance(frames, (bytes, str)) or not frames:
        raise BoundedPreparationError("frames must be a non-empty sequence of byte strings")
    names = tuple(frame_name(index) for index in range(1, len(frames) + 1))
    payloads: list[bytes] = []
    for payload in frames:
        if not isinstance(payload, bytes) or not payload:
            raise BoundedPreparationError("each frame payload must be non-empty bytes")
        payloads.append(payload)

    local_parts: list[bytes] = []
    central_parts: list[bytes] = []
    offset = 0
    for name, payload in zip(names, payloads, strict=True):
        encoded_name = _ascii_name(name)
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        size = len(payload)
        local = _local_header(encoded_name, crc, size) + payload
        central_parts.append(_central_header(encoded_name, crc, size, offset))
        local_parts.append(local)
        offset += len(local)
    local_blob = b"".join(local_parts)
    central_blob = b"".join(central_parts)
    eocd = struct.pack(
        "<IHHHHIIH",
        EOCD_SIGNATURE,
        0,
        0,
        len(payloads),
        len(payloads),
        len(central_blob),
        len(local_blob),
        0,
    )
    archive = local_blob + central_blob + eocd
    expected = sum(len(payload) for payload in payloads) + zip_overhead(names)
    if len(archive) != expected:
        raise BoundedPreparationError("archive length does not match the overhead formula")
    return archive


def _ascii_name(name: str) -> bytes:
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or name.endswith("."):
        raise BoundedPreparationError("zip member name is not a bare ASCII filename")
    try:
        encoded = name.encode("ascii")
    except UnicodeEncodeError as exc:
        raise BoundedPreparationError("zip member name is not a bare ASCII filename") from exc
    if encoded != name.encode("ascii") or b" " in encoded:
        raise BoundedPreparationError("zip member name is not a bare ASCII filename")
    return encoded


def _local_header(name: bytes, crc: int, size: int) -> bytes:
    return struct.pack(
        "<IHHHHHIIIHH",
        LOCAL_HEADER_SIGNATURE,
        10,
        0,
        ZIP_STORED,
        FIXED_DOS_TIME,
        FIXED_DOS_DATE,
        crc,
        size,
        size,
        len(name),
        0,
    ) + name


def _central_header(name: bytes, crc: int, size: int, offset: int) -> bytes:
    return struct.pack(
        "<IHHHHHHIIIHHHHHII",
        CENTRAL_HEADER_SIGNATURE,
        0,
        10,
        0,
        ZIP_STORED,
        FIXED_DOS_TIME,
        FIXED_DOS_DATE,
        crc,
        size,
        size,
        len(name),
        0,
        0,
        0,
        0,
        0,
        offset,
    ) + name
