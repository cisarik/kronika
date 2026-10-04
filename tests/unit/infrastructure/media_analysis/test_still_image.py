"""Unit tests for Pillow-backed still-image analysis preparation."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

from kronika.application.library_scan import LibraryScanCandidateKind
from kronika.application.media_analysis import (
    MediaAnalysisFailedError,
    MediaRelativePath,
)
from kronika.infrastructure.media_analysis.still_image import prepare_still_image_analysis


def _write_image(path: Path, *, fmt: str, size: tuple[int, int] = (32, 24)) -> None:
    buffer = BytesIO()
    Image.new("RGB", size, (20, 40, 60)).save(buffer, format=fmt)
    path.write_bytes(buffer.getvalue())


def test_prepare_still_jpeg_produces_one_frame_without_ffmpeg(tmp_path: Path) -> None:
    image_path = tmp_path / "still.jpeg"
    _write_image(image_path, fmt="JPEG")

    prepared = prepare_still_image_analysis(
        image_path,
        MediaRelativePath("still.jpeg"),
    )

    assert prepared.candidate_kind is LibraryScanCandidateKind.IMAGE
    assert len(prepared.representative_frames) == 1
    assert prepared.technical_metadata.video_codec == "still"
    assert prepared.technical_metadata.duration_ms is None
    assert prepared.ffmpeg_version == "unused"
    assert "still.jpeg" not in prepared.representative_frames[0].payload.decode(
        "latin1",
        errors="ignore",
    )


def test_prepare_still_png_rejects_extension_mismatch(tmp_path: Path) -> None:
    image_path = tmp_path / "still.png"
    _write_image(image_path, fmt="JPEG")

    with pytest.raises(MediaAnalysisFailedError):
        prepare_still_image_analysis(image_path, MediaRelativePath("still.png"))


def test_prepare_still_image_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real.jpeg"
    link = tmp_path / "link.jpeg"
    _write_image(target, fmt="JPEG")
    link.symlink_to(target)

    with pytest.raises(Exception):
        prepare_still_image_analysis(link, MediaRelativePath("link.jpeg"))
