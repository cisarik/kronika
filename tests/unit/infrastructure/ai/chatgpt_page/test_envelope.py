"""Envelope dimensions, step order, metadata removal, and bounded failure."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from kronika.infrastructure.ai.chatgpt_page.envelope import (
    ENVELOPE_STEPS,
    encode_envelope,
    encode_step,
    target_size,
)
from kronika.infrastructure.ai.chatgpt_page.errors import BoundedPreparationError


def _image(size: tuple[int, int], color: tuple[int, int, int] = (20, 40, 60)) -> Image.Image:
    image = Image.new("RGB", size, color)
    image.info["comment"] = b"must-not-leak-into-jpeg"
    image.info["icc_profile"] = b"icc-canary-must-not-leak"
    return image


def _noise(size: tuple[int, int], seed: int) -> Image.Image:
    import random

    rng = random.Random(seed)
    image = Image.new("RGB", size)
    pixels = [tuple(rng.randrange(256) for _ in range(3)) for _ in range(size[0] * size[1])]
    image.putdata(pixels)
    return image


def test_primary_step_uses_long_edge_and_preserves_aspect() -> None:
    encoded = encode_step(_image((1200, 800)), "480q60")

    assert encoded.step_id == "480q60"
    assert (encoded.width, encoded.height) == (480, 320)
    assert encoded.byte_length == len(encoded.payload)
    assert encoded.payload.startswith(b"\xff\xd8")
    assert encoded.payload.endswith(b"\xff\xd9")
    with Image.open(io.BytesIO(encoded.payload)) as reopened:
        reopened.load()
        assert reopened.size == (480, 320)
        assert reopened.mode == "RGB"


def test_portrait_long_edge_is_the_height() -> None:
    encoded = encode_step(_image((800, 1200)), "384q50")

    assert (encoded.width, encoded.height) == target_size(800, 1200, 384)
    assert max(encoded.width, encoded.height) == 384
    assert abs((encoded.width / encoded.height) - (800 / 1200)) < 0.02


def test_smaller_image_is_not_upscaled() -> None:
    encoded = encode_step(_image((200, 100)), "480q60")

    assert (encoded.width, encoded.height) == (200, 100)


def test_metadata_canaries_are_absent_from_the_jpeg() -> None:
    encoded = encode_step(_image((640, 360)), "480q60")

    assert b"must-not-leak-into-jpeg" not in encoded.payload
    assert b"icc-canary-must-not-leak" not in encoded.payload
    with Image.open(io.BytesIO(encoded.payload)) as reopened:
        assert not reopened.getexif()


def test_identical_input_is_byte_identical() -> None:
    image = _noise((640, 360), 7)
    first = encode_step(image, "480q60")
    second = encode_step(image, "480q60")

    assert first.payload == second.payload
    assert encode_envelope(image).payload == first.payload


def test_step_down_order_is_fixed() -> None:
    image = _noise((960, 540), 3)
    sizes = [encode_step(image, step_id).byte_length for step_id, _, _ in ENVELOPE_STEPS]
    assert sizes[0] > sizes[2]
    selected = encode_envelope(image, max_bytes=sizes[1])

    assert selected.step_id == "480q50"
    assert selected.byte_length <= sizes[1]


def test_no_step_that_fits_raises_without_a_substitute() -> None:
    image = _image((64, 64))
    with pytest.raises(BoundedPreparationError, match="no envelope step fits"):
        encode_envelope(image, max_bytes=1)
    assert [step[0] for step in ENVELOPE_STEPS] == ["480q60", "480q50", "384q50"]
