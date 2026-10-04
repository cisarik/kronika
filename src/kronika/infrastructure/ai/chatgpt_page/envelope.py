"""Deterministic JPEG envelope for chatgpt-page frame preparation."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from kronika.infrastructure.ai.chatgpt_page.errors import BoundedPreparationError

PER_FRAME_ACCOUNTING_BYTES = 128 * 1024
JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"

ENVELOPE_STEPS: tuple[tuple[str, int, int], ...] = (
    ("480q60", 480, 60),
    ("480q50", 480, 50),
    ("384q50", 384, 50),
)
ENVELOPE_IDS = tuple(step[0] for step in ENVELOPE_STEPS)


@dataclass(frozen=True, slots=True)
class EncodedFrame:
    """One JPEG produced by a single envelope step."""

    payload: bytes
    step_id: str
    width: int
    height: int

    @property
    def byte_length(self) -> int:
        return len(self.payload)


def target_size(width: int, height: int, long_edge_limit: int) -> tuple[int, int]:
    """Scale so the long edge is at most ``long_edge_limit``. Never upscale."""

    if width <= 0 or height <= 0:
        raise BoundedPreparationError("image dimensions must be positive")
    long_edge = max(width, height)
    if long_edge <= long_edge_limit:
        return width, height
    scaled_width = max(1, round(width * long_edge_limit / long_edge))
    scaled_height = max(1, round(height * long_edge_limit / long_edge))
    if scaled_width >= scaled_height and scaled_width > long_edge_limit:
        scaled_width = long_edge_limit
    elif scaled_height > long_edge_limit:
        scaled_height = long_edge_limit
    return scaled_width, scaled_height


def encode_step(image: Image.Image, step_id: str) -> EncodedFrame:
    """Encode one Pillow image at one accepted step."""

    step = _step(step_id)
    return _encode(image, step)


def encode_envelope(
    image: Image.Image,
    *,
    max_bytes: int = PER_FRAME_ACCOUNTING_BYTES,
) -> EncodedFrame:
    """Try the accepted steps in order and return the first that fits ``max_bytes``.

    The step order is fixed: 480 px at quality 60, then 480 px at quality 50,
    then 384 px at quality 50. No other quality, dimension, or frame count is
    substituted.
    """

    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise BoundedPreparationError("max_bytes must be a positive integer")
    for step in ENVELOPE_STEPS:
        encoded = _encode(image, step)
        if encoded.byte_length <= max_bytes:
            return encoded
    raise BoundedPreparationError("no envelope step fits the applicable byte bound")


def _step(step_id: str) -> tuple[str, int, int]:
    for step in ENVELOPE_STEPS:
        if step[0] == step_id:
            return step
    raise BoundedPreparationError("unknown envelope step")


def _encode(image: Image.Image, step: tuple[str, int, int]) -> EncodedFrame:
    step_id, long_edge, quality = step
    try:
        if not isinstance(image, Image.Image):
            raise BoundedPreparationError("encoder input must be a Pillow image")
        source = image.convert("RGB")
        clean = Image.new("RGB", source.size)
        clean.paste(source)
        width, height = target_size(clean.width, clean.height, long_edge)
        if (width, height) != clean.size:
            clean = clean.resize((width, height), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        clean.save(
            output,
            format="JPEG",
            quality=quality,
            subsampling="4:2:0",
            optimize=False,
            progressive=False,
        )
    except BoundedPreparationError:
        raise
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise BoundedPreparationError("jpeg encoding failed") from exc
    payload = output.getvalue()
    if not payload.startswith(JPEG_SOI) or not payload.endswith(JPEG_EOI):
        raise BoundedPreparationError("jpeg encoding failed")
    return EncodedFrame(payload=payload, step_id=step_id, width=width, height=height)
