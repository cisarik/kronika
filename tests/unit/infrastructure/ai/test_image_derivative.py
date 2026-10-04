"""Unit tests for VLM image derivative encoding."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image, ImageCms, PngImagePlugin

from kronika.application.media_analysis import build_representative_frame
from kronika.infrastructure.ai.image_derivative import (
    FrameNestImageDerivativeError,
    JPEG_SOI,
    JPEG_EOI,
    VLM_JPEG_MAX_LONG_EDGE,
    PillowVlmImageDerivativeEncoder,
)


def _png_bytes(
    size: tuple[int, int],
    *,
    color: tuple[int, int, int] = (120, 70, 30),
    metadata: bool = False,
) -> bytes:
    image = Image.new("RGB", size, color)
    output = io.BytesIO()
    pnginfo = PngImagePlugin.PngInfo()
    if metadata:
        pnginfo.add_text("Comment", "must not be copied")
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        image.save(output, format="PNG", pnginfo=pnginfo, icc_profile=profile)
    else:
        image.save(output, format="PNG")
    return output.getvalue()


def _decode_jpeg(payload: bytes) -> Image.Image:
    with Image.open(io.BytesIO(payload)) as image:
        image.load()
        return image.copy()


def test_valid_png_converts_to_valid_rgb_jpeg_with_markers() -> None:
    frame = build_representative_frame(timestamp_ms=100, payload=_png_bytes((1200, 600)))
    derivative = PillowVlmImageDerivativeEncoder().encode_frame(frame)

    assert derivative.mime_type == "image/jpeg"
    assert derivative.payload.startswith(JPEG_SOI)
    assert derivative.payload.endswith(JPEG_EOI)
    reopened = _decode_jpeg(derivative.payload)
    assert reopened.format is None
    assert reopened.mode == "RGB"
    assert max(derivative.width, derivative.height) <= VLM_JPEG_MAX_LONG_EDGE
    assert (derivative.width, derivative.height) == reopened.size


def test_aspect_ratio_is_preserved_within_integer_resize_tolerance() -> None:
    frame = build_representative_frame(timestamp_ms=100, payload=_png_bytes((1200, 800)))
    derivative = PillowVlmImageDerivativeEncoder().encode_frame(frame)

    assert derivative.width == 768
    assert abs((derivative.width / derivative.height) - (1200 / 800)) < 0.01


def test_smaller_input_is_not_upscaled() -> None:
    frame = build_representative_frame(timestamp_ms=100, payload=_png_bytes((320, 240)))
    derivative = PillowVlmImageDerivativeEncoder().encode_frame(frame)

    assert (derivative.width, derivative.height) == (320, 240)


def test_identical_input_produces_identical_bytes_digest_and_size() -> None:
    frame = build_representative_frame(timestamp_ms=100, payload=_png_bytes((640, 360)))
    encoder = PillowVlmImageDerivativeEncoder()

    first = encoder.encode_frame(frame)
    second = encoder.encode_frame(frame)

    assert first.payload == second.payload
    assert first.sha256 == hashlib.sha256(first.payload).hexdigest()
    assert first.byte_size == len(first.payload)
    assert first == second


def test_source_metadata_is_not_copied_to_jpeg() -> None:
    frame = build_representative_frame(
        timestamp_ms=100,
        payload=_png_bytes((640, 360), metadata=True),
    )
    derivative = PillowVlmImageDerivativeEncoder().encode_frame(frame)

    with Image.open(io.BytesIO(derivative.payload)) as image:
        assert "comment" not in {key.casefold() for key in image.info}
        assert "icc_profile" not in image.info
        assert "exif" not in image.info


@pytest.mark.parametrize("payload", [b"not-a-png", JPEG_SOI + b"jpeg" + JPEG_EOI])
def test_malformed_or_non_png_input_is_rejected(payload: bytes) -> None:
    with pytest.raises(FrameNestImageDerivativeError):
        PillowVlmImageDerivativeEncoder().encode_png_bytes(payload)


def test_oversized_output_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = build_representative_frame(timestamp_ms=100, payload=_png_bytes((640, 360)))
    monkeypatch.setattr("kronika.infrastructure.ai.image_derivative.VLM_JPEG_MAX_BYTES", 1)

    with pytest.raises(FrameNestImageDerivativeError):
        PillowVlmImageDerivativeEncoder().encode_frame(frame)


def test_payload_is_absent_from_repr() -> None:
    frame = build_representative_frame(timestamp_ms=100, payload=_png_bytes((640, 360)))
    derivative = PillowVlmImageDerivativeEncoder().encode_frame(frame)

    assert "payload" not in repr(derivative)
    assert derivative.payload.hex() not in repr(derivative)


def test_encoding_creates_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = build_representative_frame(timestamp_ms=100, payload=_png_bytes((640, 360)))
    monkeypatch.chdir(tmp_path)

    before = sorted(tmp_path.iterdir())
    PillowVlmImageDerivativeEncoder().encode_frame(frame)
    after = sorted(tmp_path.iterdir())

    assert after == before


def test_aggregate_derivative_bounds_are_enforced() -> None:
    frame = build_representative_frame(timestamp_ms=100, payload=_png_bytes((640, 360)))
    encoder = PillowVlmImageDerivativeEncoder()
    derivatives = tuple(encoder.encode_frame(frame) for _ in range(3))

    assert sum(item.byte_size for item in derivatives) < 4_718_592
    with pytest.raises(FrameNestImageDerivativeError):
        encoder.validate_aggregate(derivatives * 200)
