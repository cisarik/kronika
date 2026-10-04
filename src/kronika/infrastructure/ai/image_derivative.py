"""In-memory image derivatives for VLM provider transport."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from typing import Protocol

from PIL import Image, UnidentifiedImageError

from kronika.application.media_analysis import RepresentativeFrame

VLM_JPEG_MIME_TYPE = "image/jpeg"
VLM_JPEG_MAX_LONG_EDGE = 768
VLM_JPEG_QUALITY = 82
VLM_JPEG_MAX_BYTES = 1_572_864
VLM_JPEG_AGGREGATE_MAX_BYTES = 4_718_592
VLM_JPEG_MAX_FRAMES = 3
VLM_SOURCE_MAX_PIXELS = 1024 * 1024
JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"

IMAGE_DERIVATIVE_FAILED_MESSAGE = "VLM image derivative failed."


class FrameNestImageDerivativeError(RuntimeError):
    """Sanitized error raised when a VLM image derivative cannot be prepared."""


@dataclass(frozen=True, slots=True)
class VlmImageDerivative:
    """One ephemeral VLM transport image derivative."""

    width: int
    height: int
    mime_type: str
    sha256: str
    byte_size: int
    payload: bytes = field(repr=False)

    @classmethod
    def from_payload(
        cls,
        *,
        width: int,
        height: int,
        mime_type: str,
        payload: bytes,
    ) -> "VlmImageDerivative":
        return cls(
            width=width,
            height=height,
            mime_type=mime_type,
            sha256=hashlib.sha256(payload).hexdigest(),
            byte_size=len(payload),
            payload=payload,
        )

    def __post_init__(self) -> None:
        if not isinstance(self.width, int) or self.width <= 0:
            raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
        if not isinstance(self.height, int) or self.height <= 0:
            raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
        if max(self.width, self.height) > VLM_JPEG_MAX_LONG_EDGE:
            raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
        if self.mime_type != VLM_JPEG_MIME_TYPE:
            raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
        if not isinstance(self.payload, bytes) or not self.payload:
            raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
        if not self.payload.startswith(JPEG_SOI) or not self.payload.endswith(JPEG_EOI):
            raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
        if len(self.payload) > VLM_JPEG_MAX_BYTES:
            raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
        if self.byte_size != len(self.payload):
            raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
        if self.sha256 != hashlib.sha256(self.payload).hexdigest():
            raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)


class VlmImageDerivativeEncoder(Protocol):
    """Encoder contract for VLM transport derivatives."""

    def encode_frame(self, frame: RepresentativeFrame) -> VlmImageDerivative:
        """Encode one validated representative PNG frame for VLM transport."""


class PillowVlmImageDerivativeEncoder:
    """Pillow-backed in-memory PNG-to-JPEG encoder for VLM transport."""

    def encode_frame(self, frame: RepresentativeFrame) -> VlmImageDerivative:
        """Encode one validated representative PNG frame as a bounded JPEG."""
        if not isinstance(frame, RepresentativeFrame):
            raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
        return self.encode_png_bytes(frame.payload)

    def encode_png_bytes(self, payload: bytes) -> VlmImageDerivative:
        """Encode one validated PNG byte string as a bounded JPEG derivative."""
        try:
            with Image.open(io.BytesIO(payload)) as source:
                if source.format != "PNG":
                    raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
                source.load()
                width, height = source.size
                _validate_dimensions(width, height)
                working = source.convert("RGB")
                target_size = _target_size(width, height)
                if target_size != source.size:
                    working = working.resize(target_size, Image.Resampling.LANCZOS)
                output = io.BytesIO()
                working.save(
                    output,
                    format="JPEG",
                    quality=VLM_JPEG_QUALITY,
                    subsampling="4:2:0",
                    progressive=False,
                    optimize=False,
                )
        except FrameNestImageDerivativeError:
            raise
        except (OSError, UnidentifiedImageError, ValueError):
            raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE) from None

        jpeg_payload = output.getvalue()
        derivative = _validated_jpeg_derivative(jpeg_payload)
        self.validate_aggregate((derivative,))
        return derivative

    def validate_aggregate(self, derivatives: tuple[VlmImageDerivative, ...]) -> None:
        """Validate aggregate VLM derivative count and byte limits."""
        if not isinstance(derivatives, tuple):
            raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
        if not derivatives or len(derivatives) > VLM_JPEG_MAX_FRAMES:
            raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
        total = 0
        for derivative in derivatives:
            if not isinstance(derivative, VlmImageDerivative):
                raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
            total += derivative.byte_size
        if total > VLM_JPEG_AGGREGATE_MAX_BYTES:
            raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)


def _validate_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
    if width * height > VLM_SOURCE_MAX_PIXELS:
        raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)


def _target_size(width: int, height: int) -> tuple[int, int]:
    long_edge = max(width, height)
    if long_edge <= VLM_JPEG_MAX_LONG_EDGE:
        return (width, height)
    scale = VLM_JPEG_MAX_LONG_EDGE / long_edge
    target_width = max(1, round(width * scale))
    target_height = max(1, round(height * scale))
    return (target_width, target_height)


def _validated_jpeg_derivative(payload: bytes) -> VlmImageDerivative:
    if not payload.startswith(JPEG_SOI) or not payload.endswith(JPEG_EOI):
        raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
    if len(payload) > VLM_JPEG_MAX_BYTES:
        raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
    try:
        with Image.open(io.BytesIO(payload)) as generated:
            if generated.format != "JPEG":
                raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
            generated.load()
            if generated.mode != "RGB":
                raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
            width, height = generated.size
            _validate_dimensions(width, height)
            if max(width, height) > VLM_JPEG_MAX_LONG_EDGE:
                raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE)
    except FrameNestImageDerivativeError:
        raise
    except (OSError, UnidentifiedImageError, ValueError):
        raise FrameNestImageDerivativeError(IMAGE_DERIVATIVE_FAILED_MESSAGE) from None
    return VlmImageDerivative.from_payload(
        width=width,
        height=height,
        mime_type=VLM_JPEG_MIME_TYPE,
        payload=payload,
    )
