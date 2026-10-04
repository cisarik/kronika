"""Unit tests for non-persistent still-frame AI smoke preparation."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from kronika.infrastructure.ai.still_frame_smoke import (
    FrameNestStillFrameSmokeError,
    MAX_STILL_FRAMES,
    build_still_frame_smoke_request,
    prepare_still_frame_smoke_images,
)
from kronika.infrastructure.ai.constants import DEFAULT_MODEL_ID, DEFAULT_PROVIDER_ID
from kronika.infrastructure.ai.nvidia_nim import build_nvidia_request_body


def _write_jpeg(path: Path, *, color: tuple[int, int, int], size: tuple[int, int] = (64, 48)) -> Path:
    image = Image.new("RGB", size, color)
    image.save(path, format="JPEG", quality=85, optimize=False)
    return path


def _write_png(path: Path, *, color: tuple[int, int, int], size: tuple[int, int] = (32, 32)) -> Path:
    image = Image.new("RGB", size, color)
    image.save(path, format="PNG", optimize=False)
    return path


def test_prepare_accepts_one_to_three_jpeg_images(tmp_path: Path) -> None:
    paths = [
        _write_jpeg(tmp_path / "a.jpg", color=(255, 0, 0)),
        _write_jpeg(tmp_path / "b.jpg", color=(0, 255, 0)),
        _write_jpeg(tmp_path / "c.jpg", color=(0, 0, 255)),
    ]

    images = prepare_still_frame_smoke_images(paths)

    assert len(images) == 3
    assert all(image.format_name == "jpeg" for image in images)
    request = build_still_frame_smoke_request(images)
    assert len(request.representative_frames) == 3
    assert request.technical_metadata.has_audio is False


def test_prepare_rejects_empty_and_excessive_counts(tmp_path: Path) -> None:
    path = _write_jpeg(tmp_path / "a.jpg", color=(10, 10, 10))
    with pytest.raises(FrameNestStillFrameSmokeError):
        prepare_still_frame_smoke_images([])
    with pytest.raises(FrameNestStillFrameSmokeError):
        prepare_still_frame_smoke_images([path] * (MAX_STILL_FRAMES + 1))


def test_prepare_rejects_symlink(tmp_path: Path) -> None:
    target = _write_jpeg(tmp_path / "target.jpg", color=(1, 2, 3))
    link = tmp_path / "link.jpg"
    link.symlink_to(target)
    with pytest.raises(FrameNestStillFrameSmokeError):
        prepare_still_frame_smoke_images([link])


def test_prepare_rejects_directory(tmp_path: Path) -> None:
    directory = tmp_path / "dir"
    directory.mkdir()
    with pytest.raises(FrameNestStillFrameSmokeError):
        prepare_still_frame_smoke_images([directory])


def test_prepare_rejects_unsupported_format(tmp_path: Path) -> None:
    path = tmp_path / "x.webp"
    Image.new("RGB", (16, 16), (9, 9, 9)).save(path, format="WEBP")
    with pytest.raises(FrameNestStillFrameSmokeError):
        prepare_still_frame_smoke_images([path])


def test_prepare_rejects_oversized_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_jpeg(tmp_path / "big.jpg", color=(4, 5, 6))
    monkeypatch.setattr(
        "kronika.infrastructure.ai.still_frame_smoke.MAX_BYTES_PER_IMAGE",
        10,
    )
    with pytest.raises(FrameNestStillFrameSmokeError):
        prepare_still_frame_smoke_images([path])


def test_prepare_accepts_png_and_builds_request(tmp_path: Path) -> None:
    path = _write_png(tmp_path / "frame.png", color=(20, 30, 40))
    images = prepare_still_frame_smoke_images([path])
    request = build_still_frame_smoke_request(images)
    assert request.basename.endswith(".jpg")
    assert request.technical_metadata.has_audio is False
    body = build_nvidia_request_body(request, model_id=DEFAULT_MODEL_ID)
    assert body["model"] == DEFAULT_MODEL_ID
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["stream"] is False
    content = body["messages"][0]["content"]
    assert sum(1 for part in content if part.get("type") == "image_url") == 1


def test_smoke_request_enables_multi_image_path(tmp_path: Path) -> None:
    paths = [
        _write_jpeg(tmp_path / "one.jpg", color=(100, 0, 0)),
        _write_jpeg(tmp_path / "two.jpg", color=(0, 100, 0)),
        _write_jpeg(tmp_path / "three.jpg", color=(0, 0, 100)),
    ]
    request = build_still_frame_smoke_request(prepare_still_frame_smoke_images(paths))
    body = build_nvidia_request_body(request, model_id=DEFAULT_MODEL_ID)
    assert body["chat_template_kwargs"]["enable_thinking"] is False
    images = [part for part in body["messages"][0]["content"] if part.get("type") == "image_url"]
    assert len(images) == 3
    for part in images:
        url = part["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        assert len(url) < 2_000_000
