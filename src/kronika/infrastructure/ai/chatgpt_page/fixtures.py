"""Deterministic non-private fixtures for later probe trials.

The generator returns bytes in memory. It does not write the repository or a
live state directory.
"""

from __future__ import annotations

import hashlib
import io
import random
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from kronika.infrastructure.ai.chatgpt_page.archive import pack_frames
from kronika.infrastructure.ai.chatgpt_page.envelope import encode_step
from kronika.infrastructure.ai.chatgpt_page.errors import BoundedPreparationError

_LABEL_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass(frozen=True, slots=True)
class GeneratedFixture:
    """One synthetic fixture and its non-path identity."""

    identity: str
    kind: str
    mime_type: str
    payload: bytes
    label: str


@dataclass(frozen=True, slots=True)
class FixtureSet:
    """The synthetic still, GIF, and ZIP fixtures for one seed."""

    seed: int
    still_jpeg: GeneratedFixture
    still_png: GeneratedFixture
    gif_single: GeneratedFixture
    gif_multi: GeneratedFixture
    zip_frames: GeneratedFixture


def incompressible_bytes(seed: int, size: int) -> bytes:
    """Deterministic high-entropy bytes of an exact length."""

    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise BoundedPreparationError("fixture seed must be a non-negative integer")
    if not isinstance(size, int) or isinstance(size, bool) or size < 1:
        raise BoundedPreparationError("payload size must be a positive integer")
    return random.Random(seed).randbytes(size)


def generate_fixtures(seed: int) -> FixtureSet:
    """Build the synthetic set for ``seed``. The same seed returns the same bytes."""

    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise BoundedPreparationError("fixture seed must be a non-negative integer")
    rng = random.Random(seed)
    label = _label(rng, seed)
    still = _labeled_image(rng, (640, 360), label, (240, 240, 240))
    gif_frame = _labeled_image(rng, (320, 180), f"{label}-G", (220, 230, 210))
    multi = tuple(
        _labeled_image(rng, (320, 180), f"{label}-{index}", (200, 210, 230))
        for index in range(1, 4)
    )
    jpeg = encode_step(still, "480q60")
    png = _save_png(still)
    single_gif = _save_gif((gif_frame,))
    multi_gif = _save_gif(multi)
    frame_payloads = tuple(encode_step(frame, "480q60").payload for frame in multi)
    archive = pack_frames(frame_payloads)
    return FixtureSet(
        seed=seed,
        still_jpeg=_fixture("still-jpeg", "image/jpeg", jpeg.payload, label, seed),
        still_png=_fixture("still-png", "image/png", png, label, seed),
        gif_single=_fixture("gif-single", "image/gif", single_gif, f"{label}-G", seed),
        gif_multi=_fixture("gif-multi", "image/gif", multi_gif, label, seed),
        zip_frames=_fixture("zip-frames", "application/zip", archive, label, seed),
    )


def _label(rng: random.Random, seed: int) -> str:
    body = "".join(rng.choice(_LABEL_ALPHABET) for _ in range(6))
    return f"FN{seed % 10000:04d}{body}"


def _labeled_image(
    rng: random.Random,
    size: tuple[int, int],
    label: str,
    background: tuple[int, int, int],
) -> Image.Image:
    image = Image.new("RGB", size, background)
    color = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
    draw = ImageDraw.Draw(image)
    band_width = min(size[0], 8 + 8 * len(label))
    band_height = min(size[1], 28)
    draw.rectangle((0, 0, band_width, band_height), fill=color)
    ink = (0, 0, 0) if sum(color) > 384 else (255, 255, 255)
    draw.text((4, 4), label, fill=ink, font=ImageFont.load_default())
    return image


def _save_png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _save_gif(frames: tuple[Image.Image, ...]) -> bytes:
    output = io.BytesIO()
    first, rest = frames[0], frames[1:]
    first.save(
        output,
        format="GIF",
        save_all=bool(rest),
        append_images=rest,
        duration=100,
        loop=0,
        disposal=2,
    )
    return output.getvalue()


def _fixture(kind: str, mime_type: str, payload: bytes, label: str, seed: int) -> GeneratedFixture:
    digest = hashlib.sha256(payload).hexdigest()
    identity = f"fx-{seed:08x}-{kind}-{digest[:16]}"
    return GeneratedFixture(
        identity=identity,
        kind=kind,
        mime_type=mime_type,
        payload=payload,
        label=label,
    )
