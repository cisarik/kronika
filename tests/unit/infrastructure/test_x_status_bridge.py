"""Isolated X status-bridge seam tests. Synthetic status only; no network."""

from __future__ import annotations

import json

import pytest

from kronika.domain.x_acquisition import (
    XMediaType,
    X_VARIANT_ANIMATED_GIF_MP4,
    X_VARIANT_PHOTO_JPEG,
    X_VARIANT_PHOTO_PNG,
    X_VARIANT_VIDEO_MP4,
)
from kronika.infrastructure.x.status_bridge import (
    PINNED_YTDLP_VERSION,
    StatusBridgeError,
    attest_pinned_extractor,
    inspect_post,
    inspection_payload,
    isolated_extractor_context,
    normalize_status,
)


POST_ID = "123456789"
SUBMITTED = f"https://x.com/author/status/{POST_ID}"


def _photo_row(media_id: str, *, fmt: str = "jpg", width: int = 640, height: int = 480) -> dict:
    return {
        "id_str": media_id,
        "type": "photo",
        "media_url_https": (
            f"https://pbs.twimg.com/media/{media_id}?format={fmt}&name=small"
        ),
        "original_info": {"width": width, "height": height},
    }


def _video_row(media_id: str, *, animated: bool = False) -> dict:
    return {
        "id_str": media_id,
        "type": "animated_gif" if animated else "video",
        "video_info": {
            "duration_millis": 12000,
            "variants": [
                {
                    "content_type": "video/mp4",
                    "url": f"https://video.twimg.com/ext_tw_video/{media_id}/pu/vid/720.mp4",
                    "bitrate": 1024000,
                }
            ],
        },
        "original_info": {"width": 720, "height": 720},
    }


def _status(*media: dict, text: str = "Watch this clip") -> dict:
    return {
        "id_str": POST_ID,
        "full_text": text,
        "timestamp": 1700000000,
        "user": {
            "id_str": "2222222222222222222",
            "screen_name": "author",
            "name": "Author Name",
        },
        "extended_entities": {"media": list(media)},
    }


def test_attest_pinned_extractor_version_and_seam() -> None:
    assert attest_pinned_extractor() == PINNED_YTDLP_VERSION
    assert PINNED_YTDLP_VERSION == "2026.07.04"


def test_isolated_context_has_empty_cookiejar_and_no_ambient_config() -> None:
    context = isolated_extractor_context()
    cookiejar = context["cookiejar"]
    assert list(cookiejar) == []
    assert context["usenetrc"] is False
    assert context["ignoreconfig"] is True
    assert context["cookiefile"] is None
    assert context["cookiesfrombrowser"] is None
    assert context["plugin_dirs"] == []
    extractor = context["extractor"]
    assert callable(getattr(extractor, "_extract_status"))


def test_photo_only_status_emits_image_asset_without_urls() -> None:
    status = _status(_photo_row("1111111111111111111"), text="A photo")
    inspection = inspect_post(
        POST_ID, SUBMITTED, extract_status=lambda _post_id: status
    )
    assert len(inspection.assets) == 1
    asset = inspection.assets[0]
    assert asset.media_type is XMediaType.IMAGE
    assert asset.source_media_key == "1111111111111111111"
    assert asset.selected_variant == X_VARIANT_PHOTO_JPEG
    assert asset.expected_mime == "image/jpeg"
    payload = json.dumps(inspection_payload(inspection))
    assert "pbs.twimg.com" not in payload
    assert "video.twimg.com" not in payload
    assert "http" not in payload.lower() or "x.com" in payload


def test_mixed_status_preserves_provider_order_and_download_index() -> None:
    status = _status(
        _photo_row("photo-1"),
        _video_row("video-1"),
        _photo_row("photo-2", fmt="png"),
        _video_row("gif-1", animated=True),
        text="Mixed post",
    )
    inspection = inspect_post(
        POST_ID, SUBMITTED, extract_status=lambda _post_id: status
    )
    assert [asset.media_type for asset in inspection.assets] == [
        XMediaType.IMAGE,
        XMediaType.VIDEO,
        XMediaType.IMAGE,
        XMediaType.ANIMATED_GIF,
    ]
    assert [asset.source_media_key for asset in inspection.assets] == [
        "photo-1",
        "video-1",
        "photo-2",
        "gif-1",
    ]
    assert inspection.assets[0].selected_variant == X_VARIANT_PHOTO_JPEG
    assert inspection.assets[1].selected_variant == X_VARIANT_VIDEO_MP4
    assert inspection.assets[2].selected_variant == X_VARIANT_PHOTO_PNG
    assert inspection.assets[3].selected_variant == X_VARIANT_ANIMATED_GIF_MP4
    assert inspection.assets[0].provider_download_index is None
    assert inspection.assets[1].provider_download_index == 0
    assert inspection.assets[3].provider_download_index == 1
    payload = json.dumps(inspection_payload(inspection))
    assert "pbs.twimg.com" not in payload
    assert "video.twimg.com" not in payload


def test_empty_status_is_no_supported_media() -> None:
    with pytest.raises(StatusBridgeError) as ctx:
        inspect_post(
            POST_ID,
            SUBMITTED,
            extract_status=lambda _post_id: _status(),
        )
    assert ctx.value.code == "X_NO_SUPPORTED_MEDIA"


def test_webp_photo_is_not_a_supported_asset() -> None:
    with pytest.raises(StatusBridgeError) as ctx:
        inspect_post(
            POST_ID,
            SUBMITTED,
            extract_status=lambda _post_id: _status(_photo_row("webp-1", fmt="webp")),
        )
    assert ctx.value.code == "X_NO_SUPPORTED_MEDIA"


def test_normalize_status_does_not_embed_raw_urls() -> None:
    inspection = normalize_status(
        _status(_photo_row("media-key"), _video_row("video-key")),
        POST_ID,
        SUBMITTED,
    )
    encoded = json.dumps(inspection_payload(inspection))
    assert "pbs.twimg.com" not in encoded
    assert "media-key" in encoded
    assert inspection.author_handle == "author"
    assert inspection.author_stable_id == "2222222222222222222"
