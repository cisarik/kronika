"""YtDlpXExtractor tests using synthetic status and a local fake yt-dlp."""

from __future__ import annotations

import json
from pathlib import Path
import threading
import time

import pytest

from kronika.application.ports.x_extractor import XExtractionError, XExtractionInterrupted
from kronika.domain.x_acquisition import (
    XMediaType,
    XNormalizedInspection,
    X_VARIANT_PHOTO_JPEG,
    X_VARIANT_VIDEO_MP4,
)
from kronika.infrastructure.x.downloader import YtDlpXExtractor
from kronika.infrastructure.x.staging import ARTIFACT_FILENAME, FilesystemXStaging
from kronika.infrastructure.x.status_bridge import PhotoHttpResult


POST_ID = "123456789"
SUBMITTED = f"https://x.com/author/status/{POST_ID}"


def _photo_row(media_id: str, *, fmt: str = "jpg") -> dict:
    return {
        "id_str": media_id,
        "type": "photo",
        "media_url_https": f"https://pbs.twimg.com/media/{media_id}?format={fmt}&name=small",
        "original_info": {"width": 640, "height": 480},
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


def _write_fake_executable(
    path: Path,
    *,
    exit_code: int = 0,
    printed_id: str = "1111111111111111111",
    delay: float = 0.0,
    stdout_json: str | None = None,
) -> Path:
    json_literal = "None" if stdout_json is None else "'''" + stdout_json + "'''"
    header = (
        "#!/usr/bin/env python3\n"
        "import sys, time, json\n"
        "time.sleep(%.2f)\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['-I', '-m']:\n"
        "    payload = %s\n"
        "    if payload is not None:\n"
        "        sys.stdout.write(payload)\n"
        "    elif 'inspect' in args:\n"
        "        sys.stdout.write(json.dumps({'error_code': 'X_EXTRACTOR_FAILED'}))\n"
        "        sys.exit(2)\n"
        "    sys.exit(%d)\n"
        "if '--version' in args:\n"
        "    sys.stdout.write('2026.07.04\\n')\n"
        "    sys.exit(%d)\n"
        "if '--output' in args:\n"
        "    name = args[args.index('--output') + 1]\n"
        "    with open(name, 'wb') as f:\n"
        "        f.write(b'\\x00\\x00\\x00\\x18ftypmp42fake')\n"
        "    sys.stdout.write(%r + '\\n')\n"
        "    sys.exit(%d)\n"
        "sys.exit(%d)\n"
    )
    path.write_text(
        header
        % (
            delay,
            json_literal,
            exit_code,
            exit_code,
            printed_id,
            exit_code,
            exit_code,
        )
    )
    path.chmod(0o755)
    return path


def _jpeg_bytes() -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (2, 2), (1, 2, 3)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_inspect_one_video_from_synthetic_status() -> None:
    extractor = YtDlpXExtractor(
        extract_status=lambda _post_id: _status(_video_row("1111111111111111111"))
    )
    inspection = extractor.inspect(post_id=POST_ID, submitted_url=SUBMITTED)
    assert isinstance(inspection, XNormalizedInspection)
    assert inspection.assets[0].media_type is XMediaType.VIDEO
    assert inspection.assets[0].source_media_key == "1111111111111111111"
    assert inspection.assets[0].selected_variant == X_VARIANT_VIDEO_MP4
    assert inspection.author_stable_id == "2222222222222222222"
    assert inspection.author_handle == "author"
    assert inspection.canonical_url == "https://x.com/author/status/123456789"


def test_inspect_photo_only_post_emits_image() -> None:
    extractor = YtDlpXExtractor(
        extract_status=lambda _post_id: _status(_photo_row("photo-1"), text="A photo")
    )
    inspection = extractor.inspect(post_id=POST_ID, submitted_url=SUBMITTED)
    assert inspection.assets[0].media_type is XMediaType.IMAGE
    assert inspection.assets[0].selected_variant == X_VARIANT_PHOTO_JPEG


def test_inspect_mixed_post_includes_photos_and_videos() -> None:
    extractor = YtDlpXExtractor(
        extract_status=lambda _post_id: _status(
            _photo_row("photo-1"),
            _video_row("video-1"),
            _video_row("gif-1", animated=True),
        )
    )
    inspection = extractor.inspect(post_id=POST_ID, submitted_url=SUBMITTED)
    assert [asset.media_type for asset in inspection.assets] == [
        XMediaType.IMAGE,
        XMediaType.VIDEO,
        XMediaType.ANIMATED_GIF,
    ]
    assert inspection.assets[1].provider_download_index == 0
    assert inspection.assets[2].provider_download_index == 1


def test_inspect_too_many_assets() -> None:
    extractor = YtDlpXExtractor(
        extract_status=lambda _post_id: _status(
            *[_video_row(f"video-{index}") for index in range(5)]
        )
    )
    with pytest.raises(XExtractionError) as ctx:
        extractor.inspect(post_id=POST_ID, submitted_url=SUBMITTED)
    assert ctx.value.code == "X_TOO_MANY_ASSETS"


def test_inspect_no_supported_media() -> None:
    extractor = YtDlpXExtractor(extract_status=lambda _post_id: _status())
    with pytest.raises(XExtractionError) as ctx:
        extractor.inspect(post_id=POST_ID, submitted_url=SUBMITTED)
    assert ctx.value.code == "X_NO_SUPPORTED_MEDIA"


def test_inspect_protected_post() -> None:
    def _protected(_post_id: str) -> dict:
        status = _status(_video_row("111"))
        status["user"]["protected"] = True
        return status

    extractor = YtDlpXExtractor(extract_status=_protected)
    with pytest.raises(XExtractionError) as ctx:
        extractor.inspect(post_id=POST_ID, submitted_url=SUBMITTED)
    assert ctx.value.code == "X_POST_PROTECTED"


def test_inspect_malformed_status() -> None:
    extractor = YtDlpXExtractor(extract_status=lambda _post_id: "not-a-status")
    with pytest.raises(XExtractionError) as ctx:
        extractor.inspect(post_id=POST_ID, submitted_url=SUBMITTED)
    assert ctx.value.code in {"X_EXTRACTOR_MALFORMED", "X_POST_UNAVAILABLE"}


def test_inspect_command_uses_isolated_status_bridge() -> None:
    extractor = YtDlpXExtractor()
    argv = extractor.inspect_argv(POST_ID)
    assert argv[1:4] == ["-I", "-m", "kronika.infrastructure.x.status_bridge"]
    assert "inspect" in argv
    joined = " ".join(argv)
    assert "--cookies" not in joined
    assert "--netrc" not in joined
    assert "--cookies-from-browser" not in joined
    assert "pbs.twimg.com" not in joined


def test_inspect_timeout_via_bridge_subprocess(tmp_path: Path) -> None:
    exe = _write_fake_executable(tmp_path / "fake_bridge", delay=2.0)
    extractor = YtDlpXExtractor(bridge_executable=str(exe), inspect_timeout_seconds=0.2)
    with pytest.raises(XExtractionError) as ctx:
        extractor.inspect(post_id=POST_ID, submitted_url=SUBMITTED)
    assert ctx.value.code == "X_DOWNLOAD_TIMEOUT"


def test_download_video_matches_source_key_not_ordinal(tmp_path: Path) -> None:
    staging_root = tmp_path / "xroot"
    staging_root.mkdir(mode=0o700)
    staging = FilesystemXStaging(staging_root)
    exe = _write_fake_executable(
        tmp_path / "fake_x", printed_id="video-1"
    )
    argv_log = tmp_path / "argv.json"

    def _logged_exe() -> Path:
        script = (
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "args = sys.argv[1:]\n"
            "with open(r'%s', 'w') as fh:\n"
            "    json.dump(args, fh)\n"
            "name = args[args.index('--output') + 1]\n"
            "open(name, 'wb').write(b'fakebytes')\n"
            "sys.stdout.write('video-1\\n')\n"
            % argv_log
        )
        path = tmp_path / "fake_x_argv"
        path.write_text(script)
        path.chmod(0o755)
        return path

    extractor = YtDlpXExtractor(
        executable=str(_logged_exe()),
        extract_status=lambda _post_id: _status(
            _photo_row("photo-1"),
            _video_row("video-1"),
        ),
    )
    result = extractor.download(
        post_id=POST_ID,
        ordinal=99,
        media_type="video",
        expected_mime="video/mp4",
        source_media_key="video-1",
        selected_variant=X_VARIANT_VIDEO_MP4,
        stage_key="a" * 32,
        submitted_url=SUBMITTED,
        staging=staging,
    )
    assert result.size_bytes > 0
    argv = json.loads(argv_log.read_text())
    assert argv[argv.index("--output") + 1] == ARTIFACT_FILENAME
    assert argv[argv.index("--playlist-items") + 1] == "1"
    assert argv[-1] == "https://x.com/author/status/123456789"
    assert "video-1" not in argv


def test_download_missing_key_is_source_changed(tmp_path: Path) -> None:
    staging = FilesystemXStaging(tmp_path / "xroot")
    (tmp_path / "xroot").mkdir(mode=0o700)
    extractor = YtDlpXExtractor(
        extract_status=lambda _post_id: _status(_video_row("video-1"))
    )
    with pytest.raises(XExtractionError) as ctx:
        extractor.download(
            post_id=POST_ID,
            ordinal=0,
            media_type="video",
            expected_mime="video/mp4",
            source_media_key="missing-key",
            selected_variant=X_VARIANT_VIDEO_MP4,
            stage_key="b" * 32,
            submitted_url=SUBMITTED,
            staging=staging,
        )
    assert ctx.value.code == "X_SOURCE_MEDIA_CHANGED"


def test_download_requires_selected_variant(tmp_path: Path) -> None:
    staging = FilesystemXStaging(tmp_path / "xroot")
    (tmp_path / "xroot").mkdir(mode=0o700)
    extractor = YtDlpXExtractor(
        extract_status=lambda _post_id: _status(_video_row("video-1"))
    )
    with pytest.raises(XExtractionError) as ctx:
        extractor.download(
            post_id=POST_ID,
            ordinal=0,
            media_type="video",
            expected_mime="video/mp4",
            source_media_key="video-1",
            selected_variant=None,
            stage_key="c" * 32,
            submitted_url=SUBMITTED,
            staging=staging,
        )
    assert ctx.value.code == "X_SOURCE_MEDIA_CHANGED"


def test_download_rejects_submitted_url_mismatched_post_id(tmp_path: Path) -> None:
    staging = FilesystemXStaging(tmp_path / "xroot")
    (tmp_path / "xroot").mkdir(mode=0o700)
    extractor = YtDlpXExtractor(
        extract_status=lambda _post_id: _status(_video_row("video-1"))
    )
    with pytest.raises(XExtractionError) as ctx:
        extractor.download(
            post_id="111111111",
            ordinal=0,
            media_type="video",
            expected_mime="video/mp4",
            source_media_key="video-1",
            selected_variant=X_VARIANT_VIDEO_MP4,
            stage_key="d" * 32,
            submitted_url=SUBMITTED,
            staging=staging,
        )
    assert ctx.value.code == "X_URL_INVALID_POST_ID"


def test_download_id_mismatch_deletes_artifact(tmp_path: Path) -> None:
    staging_root = tmp_path / "xroot"
    staging_root.mkdir(mode=0o700)
    staging = FilesystemXStaging(staging_root)
    exe = _write_fake_executable(tmp_path / "fake_x", printed_id="other-id")
    extractor = YtDlpXExtractor(
        executable=str(exe),
        extract_status=lambda _post_id: _status(_video_row("video-1")),
    )
    with pytest.raises(XExtractionError) as ctx:
        extractor.download(
            post_id=POST_ID,
            ordinal=0,
            media_type="video",
            expected_mime="video/mp4",
            source_media_key="video-1",
            selected_variant=X_VARIANT_VIDEO_MP4,
            stage_key="e" * 32,
            submitted_url=SUBMITTED,
            staging=staging,
        )
    assert ctx.value.code == "X_SOURCE_MEDIA_CHANGED"
    claim_dir = staging_root / ("e" * 32)
    assert not (claim_dir / ARTIFACT_FILENAME).exists()


def test_download_photo_via_injected_transport(tmp_path: Path) -> None:
    staging_root = tmp_path / "xroot"
    staging_root.mkdir(mode=0o700)
    staging = FilesystemXStaging(staging_root)
    jpeg = _jpeg_bytes()

    def _transport(_ip: str, _host: str, _target: str, _timeout: float) -> PhotoHttpResult:
        return PhotoHttpResult(
            status=200,
            headers={"content-type": "image/jpeg", "content-length": str(len(jpeg))},
            body=jpeg,
        )

    extractor = YtDlpXExtractor(
        extract_status=lambda _post_id: _status(_photo_row("photo-1"), text="A photo"),
        photo_transport=_transport,
        photo_resolver=lambda _host, _port: [("8.8.8.8", 443)],
    )
    result = extractor.download(
        post_id=POST_ID,
        ordinal=0,
        media_type="image",
        expected_mime="image/jpeg",
        source_media_key="photo-1",
        selected_variant=X_VARIANT_PHOTO_JPEG,
        stage_key="f" * 32,
        submitted_url=SUBMITTED,
        staging=staging,
    )
    artifact = staging_root / ("f" * 32) / ARTIFACT_FILENAME
    assert artifact.read_bytes() == jpeg
    assert result.size_bytes == len(jpeg)


def test_attest_version_with_fake_executable(tmp_path: Path) -> None:
    exe = _write_fake_executable(tmp_path / "fake_x")
    extractor = YtDlpXExtractor(executable=str(exe))
    assert extractor.attest_version() == "2026.07.04"


def test_request_interrupt_stops_owned_process_group(tmp_path: Path) -> None:
    exe = _write_fake_executable(tmp_path / "fake_bridge", delay=5.0)
    extractor = YtDlpXExtractor(bridge_executable=str(exe), inspect_timeout_seconds=8.0)
    raised: list[BaseException] = []

    def run_inspect() -> None:
        try:
            extractor.inspect(post_id=POST_ID, submitted_url=SUBMITTED)
        except BaseException as exc:
            raised.append(exc)

    worker = threading.Thread(target=run_inspect)
    worker.start()
    time.sleep(0.15)
    extractor.request_interrupt()
    worker.join(timeout=5)
    assert worker.is_alive() is False
    assert raised
    assert isinstance(raised[0], XExtractionInterrupted) or (
        isinstance(raised[0], XExtractionError) and raised[0].code == "X_DOWNLOAD_TIMEOUT"
    )
