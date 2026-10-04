"""Bounded pbs.twimg.com photo transport tests. Injected DNS/HTTP only."""

from __future__ import annotations

from pathlib import Path

import pytest
from io import BytesIO

from PIL import Image

from kronika.domain.x_acquisition import X_VARIANT_PHOTO_JPEG, X_VARIANT_PHOTO_PNG
from kronika.infrastructure.x.status_bridge import (
    PHOTO_MAX_BYTES,
    PhotoHttpResult,
    StatusBridgeError,
    fetch_pbs_photo,
    validate_pbs_photo_url,
)


def _jpeg_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (2, 2), (1, 2, 3)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (2, 2), (4, 5, 6)).save(buffer, format="PNG")
    return buffer.getvalue()


def _webp_bytes() -> bytes:
    return b"RIFF\x18\x00\x00\x00WEBPVP8L\x0b\x00\x00\x00\x2f"


def _public_resolver(_host: str, _port: int) -> list[tuple[str, int]]:
    return [("8.8.8.8", 443)]


def _loopback_resolver(_host: str, _port: int) -> list[tuple[str, int]]:
    return [("127.0.0.1", 443)]


def _transport(status: int, payload: bytes, content_type: str, **headers: str):
    merged = {"content-type": content_type, "content-length": str(len(payload))}
    merged.update({key.lower(): value for key, value in headers.items()})

    def _call(_ip: str, _host: str, _target: str, _timeout: float) -> PhotoHttpResult:
        return PhotoHttpResult(status=status, headers=merged, body=payload)

    return _call


def test_valid_orig_jpeg_url_is_accepted() -> None:
    parsed = validate_pbs_photo_url(
        "https://pbs.twimg.com/media/ABC123?format=jpg&name=orig",
        expected_variant=X_VARIANT_PHOTO_JPEG,
    )
    assert parsed.hostname == "pbs.twimg.com"


def test_foreign_host_is_rejected() -> None:
    with pytest.raises(StatusBridgeError) as ctx:
        validate_pbs_photo_url(
            "https://video.twimg.com/media/ABC123?format=jpg&name=orig",
            expected_variant=X_VARIANT_PHOTO_JPEG,
        )
    assert ctx.value.code == "X_MEDIA_TYPE_UNSUPPORTED"


def test_redirect_is_rejected(tmp_path: Path) -> None:
    destination = tmp_path / "artifact.bin"
    with pytest.raises(StatusBridgeError) as ctx:
        fetch_pbs_photo(
            "https://pbs.twimg.com/media/ABC123?format=jpg&name=orig",
            destination,
            expected_variant=X_VARIANT_PHOTO_JPEG,
            resolver=_public_resolver,
            transport=_transport(302, b"", "image/jpeg", location="https://evil.example/x"),
        )
    assert ctx.value.code == "X_MEDIA_TYPE_UNSUPPORTED"
    assert not destination.exists()
    assert not (tmp_path / "artifact.bin.part").exists()


def test_jpeg_and_png_success(tmp_path: Path) -> None:
    jpeg = _jpeg_bytes()
    png = _png_bytes()
    jpeg_dest = tmp_path / "jpeg.bin"
    png_dest = tmp_path / "png.bin"
    jpeg_hash = fetch_pbs_photo(
        "https://pbs.twimg.com/media/JPEG1?format=jpg&name=orig",
        jpeg_dest,
        expected_variant=X_VARIANT_PHOTO_JPEG,
        resolver=_public_resolver,
        transport=_transport(200, jpeg, "image/jpeg"),
    )
    png_hash = fetch_pbs_photo(
        "https://pbs.twimg.com/media/PNG1?format=png&name=orig",
        png_dest,
        expected_variant=X_VARIANT_PHOTO_PNG,
        resolver=_public_resolver,
        transport=_transport(200, png, "image/png"),
    )
    assert jpeg_dest.read_bytes() == jpeg
    assert png_dest.read_bytes() == png
    assert len(jpeg_hash) == 64
    assert len(png_hash) == 64


def test_webp_magic_is_rejected(tmp_path: Path) -> None:
    destination = tmp_path / "artifact.bin"
    with pytest.raises(StatusBridgeError) as ctx:
        fetch_pbs_photo(
            "https://pbs.twimg.com/media/WEBP1?format=jpg&name=orig",
            destination,
            expected_variant=X_VARIANT_PHOTO_JPEG,
            resolver=_public_resolver,
            transport=_transport(200, _webp_bytes(), "image/jpeg"),
        )
    assert ctx.value.code == "X_MEDIA_TYPE_UNSUPPORTED"
    assert not destination.exists()


def test_mime_mismatch_is_rejected(tmp_path: Path) -> None:
    destination = tmp_path / "artifact.bin"
    with pytest.raises(StatusBridgeError) as ctx:
        fetch_pbs_photo(
            "https://pbs.twimg.com/media/JPEG1?format=jpg&name=orig",
            destination,
            expected_variant=X_VARIANT_PHOTO_JPEG,
            resolver=_public_resolver,
            transport=_transport(200, _jpeg_bytes(), "image/png"),
        )
    assert ctx.value.code == "X_MEDIA_TYPE_UNSUPPORTED"


def test_malformed_bytes_are_rejected(tmp_path: Path) -> None:
    destination = tmp_path / "artifact.bin"
    with pytest.raises(StatusBridgeError) as ctx:
        fetch_pbs_photo(
            "https://pbs.twimg.com/media/JPEG1?format=jpg&name=orig",
            destination,
            expected_variant=X_VARIANT_PHOTO_JPEG,
            resolver=_public_resolver,
            transport=_transport(200, b"not-an-image", "image/jpeg"),
        )
    assert ctx.value.code == "X_MEDIA_TYPE_UNSUPPORTED"
    assert not destination.exists()


def test_oversize_content_length_is_rejected(tmp_path: Path) -> None:
    destination = tmp_path / "artifact.bin"
    with pytest.raises(StatusBridgeError) as ctx:
        fetch_pbs_photo(
            "https://pbs.twimg.com/media/JPEG1?format=jpg&name=orig",
            destination,
            expected_variant=X_VARIANT_PHOTO_JPEG,
            resolver=_public_resolver,
            transport=_transport(
                200,
                _jpeg_bytes(),
                "image/jpeg",
                **{"content-length": str(PHOTO_MAX_BYTES + 1)},
            ),
        )
    assert ctx.value.code == "X_MEDIA_TOO_LARGE"


def test_non_global_dns_is_rejected(tmp_path: Path) -> None:
    destination = tmp_path / "artifact.bin"
    with pytest.raises(StatusBridgeError) as ctx:
        fetch_pbs_photo(
            "https://pbs.twimg.com/media/JPEG1?format=jpg&name=orig",
            destination,
            expected_variant=X_VARIANT_PHOTO_JPEG,
            resolver=_loopback_resolver,
            transport=_transport(200, _jpeg_bytes(), "image/jpeg"),
        )
    assert ctx.value.code == "X_MEDIA_TYPE_UNSUPPORTED"
    assert not destination.exists()


def test_timeout_is_retryable_code(tmp_path: Path) -> None:
    destination = tmp_path / "artifact.bin"

    def _timeout(_ip: str, _host: str, _target: str, _timeout: float) -> PhotoHttpResult:
        raise TimeoutError("connect timed out")

    with pytest.raises(StatusBridgeError) as ctx:
        fetch_pbs_photo(
            "https://pbs.twimg.com/media/JPEG1?format=jpg&name=orig",
            destination,
            expected_variant=X_VARIANT_PHOTO_JPEG,
            resolver=_public_resolver,
            transport=_timeout,
        )
    assert ctx.value.code == "X_DOWNLOAD_TIMEOUT"
    assert not destination.exists()


def test_partial_file_is_removed_on_failure(tmp_path: Path) -> None:
    destination = tmp_path / "artifact.bin"
    partial = tmp_path / "artifact.bin.part"
    jpeg = _jpeg_bytes()

    def _fail_after_prefix(
        _ip: str, _host: str, _target: str, _timeout: float
    ) -> PhotoHttpResult:
        return PhotoHttpResult(
            status=200,
            headers={"content-type": "image/jpeg"},
            chunks=(jpeg[:3],),
        )

    # First chunk is magic-valid; a later empty completion with too-small body still
    # writes a partial that cleanup must remove when magic later fails on short body.
    def _raise_midstream(
        _ip: str, _host: str, _target: str, _timeout: float
    ) -> PhotoHttpResult:
        raise OSError("connection reset")

    with pytest.raises(StatusBridgeError):
        fetch_pbs_photo(
            "https://pbs.twimg.com/media/JPEG1?format=jpg&name=orig",
            destination,
            expected_variant=X_VARIANT_PHOTO_JPEG,
            resolver=_public_resolver,
            transport=_raise_midstream,
        )
    assert not destination.exists()
    assert not partial.exists()
