"""Deterministic fixtures and in-image visual labels."""

from __future__ import annotations

import io

from PIL import Image

from kronika.infrastructure.ai.chatgpt_page.fixtures import generate_fixtures, incompressible_bytes


def test_same_seed_is_byte_identical_and_another_seed_differs() -> None:
    first = generate_fixtures(11)
    second = generate_fixtures(11)
    other = generate_fixtures(12)

    assert first.still_jpeg.payload == second.still_jpeg.payload
    assert first.still_png.payload == second.still_png.payload
    assert first.gif_single.payload == second.gif_single.payload
    assert first.gif_multi.payload == second.gif_multi.payload
    assert first.zip_frames.payload == second.zip_frames.payload
    assert first.still_png.payload != other.still_png.payload
    assert first.still_jpeg.identity.startswith("fx-0000000b-still-jpeg-")
    assert first.still_jpeg.label not in first.still_jpeg.identity


def test_png_label_is_painted_inside_the_image() -> None:
    fixture = generate_fixtures(11).still_png
    with Image.open(io.BytesIO(fixture.payload)) as image:
        image.load()
        assert image.mode == "RGB"
        band = [image.getpixel((x, y)) for x in range(0, 40) for y in range(0, 16)]
    assert any(pixel != (240, 240, 240) for pixel in band)
    assert fixture.label.encode("ascii") not in fixture.payload


def test_incompressible_bytes_have_exact_length_and_repeat() -> None:
    payload = incompressible_bytes(5, 128 * 1024)

    assert len(payload) == 128 * 1024
    assert payload == incompressible_bytes(5, 128 * 1024)
    assert payload != incompressible_bytes(6, 128 * 1024)
