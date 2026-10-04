"""Stored ZIP naming, order, determinism, and exact overhead."""

from __future__ import annotations

import io
import zipfile

import pytest

from kronika.infrastructure.ai.chatgpt_page.archive import (
    frame_name,
    pack_frames,
    zip_overhead,
    zip_overhead_for_count,
)
from kronika.infrastructure.ai.chatgpt_page.errors import BoundedPreparationError


def test_overhead_matches_the_fixed_formula() -> None:
    names = ("frame-0001.jpg", "frame-0002.jpg")

    assert frame_name(1) == "frame-0001.jpg"
    assert len(frame_name(1)) == 14
    assert zip_overhead(names) == 22 + sum(76 + 2 * len(name) for name in names)
    assert zip_overhead_for_count(2) == zip_overhead(names)


def test_archive_is_deterministic_and_matches_overhead() -> None:
    frames = (b"\xff\xd8jpeg-one\xff\xd9", b"\xff\xd8jpeg-two\xff\xd9", b"\xff\xd8jpeg-three\xff\xd9")
    first = pack_frames(frames)
    second = pack_frames(frames)

    assert first == second
    assert len(first) == sum(len(frame) for frame in frames) + zip_overhead_for_count(3)
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == ["frame-0001.jpg", "frame-0002.jpg", "frame-0003.jpg"]
        assert archive.comment == b""
        assert [archive.read(name) for name in archive.namelist()] == list(frames)
        for info in archive.infolist():
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.extra == b""
            assert info.flag_bits == 0
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert "/" not in info.filename


def test_order_follows_the_input_and_names_stay_chronological() -> None:
    forward = pack_frames((b"aaa", b"bbb"))
    reverse = pack_frames((b"bbb", b"aaa"))

    assert forward != reverse
    with zipfile.ZipFile(io.BytesIO(reverse)) as archive:
        assert archive.namelist() == ["frame-0001.jpg", "frame-0002.jpg"]
        assert archive.read("frame-0001.jpg") == b"bbb"


def test_empty_or_invalid_frames_are_rejected() -> None:
    with pytest.raises(BoundedPreparationError):
        pack_frames(())
    with pytest.raises(BoundedPreparationError):
        pack_frames((b"",))
    with pytest.raises(BoundedPreparationError):
        frame_name(0)
