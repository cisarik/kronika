"""Isolation, policy, and fake-process evidence for the yt-dlp boundary."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import signal
import sys
import time
from typing import Any

import pytest

from kronika.application.ports.youtube_downloader import (
    YouTubeDownloadError,
    YouTubeDownloaderConfigurationError,
    YouTubeDownloadPlan,
    YouTubeInspection,
    YouTubeInspectionError,
)
from kronika.domain.youtube_acquisition import canonicalize_youtube_url
from kronika.infrastructure.youtube.downloader import YtDlpYouTubeDownloader
from kronika.infrastructure.youtube.staging import FilesystemYouTubeStaging
from kronika.application.in_process_lifecycle import ShutdownDeadline

VIDEO_ID = "AbCdEf123_-"
STAGING_KEY = "2" * 32


class _FakeStream:
    def __init__(self, payload: bytes = b"") -> None:
        self._payload = payload

    async def read(self, _size: int) -> bytes:
        payload, self._payload = self._payload, b""
        return payload


class _ImmediateProcess:
    _next_pid = 45_000

    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> None:
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        self.stdout = _FakeStream(stdout)
        self.stderr = _FakeStream(stderr)
        self._returncode = returncode

    async def wait(self) -> int:
        return self._returncode


class _HangingProcess(_ImmediateProcess):
    def __init__(self) -> None:
        super().__init__()
        self.finished = asyncio.Event()

    async def wait(self) -> int:
        await self.finished.wait()
        return self._returncode


class _RecordingFactory:
    def __init__(
        self,
        processes: list[_ImmediateProcess],
        *,
        on_spawn: Any = None,
    ) -> None:
        self._processes = processes
        self._on_spawn = on_spawn
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    async def __call__(self, *argv: str, **kwargs: object) -> _ImmediateProcess:
        self.calls.append((argv, dict(kwargs)))
        if self._on_spawn is not None:
            self._on_spawn(argv, kwargs)
        return self._processes.pop(0)


def _private_staging(tmp_path: Path) -> FilesystemYouTubeStaging:
    root = tmp_path / "youtube"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return FilesystemYouTubeStaging(root)


def _inspection_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": VIDEO_ID,
        "extractor_key": "Youtube",
        "_type": "video",
        "is_live": False,
        "live_status": "not_live",
        "availability": "public",
        "age_limit": 0,
        "duration": 61.25,
        "title": "Synthetic title",
        "channel": "Synthetic channel",
        "channel_id": "channel-id",
        "upload_date": "20260701",
        "_filename": "Remote title.mp4",
        "formats": [
            {
                "format_id": "137",
                "ext": "mp4",
                "video_ext": "mp4",
                "vcodec": "avc1.640028",
                "acodec": "none",
                "height": 1080,
                "filesize": 100,
            },
            {
                "format_id": "140",
                "ext": "m4a",
                "audio_ext": "m4a",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "abr": 128,
                "filesize": 20,
            },
        ],
    }
    payload.update(overrides)
    return payload


def test_attestation_and_inspection_use_exact_isolated_argv_and_environment(
    tmp_path: Path,
) -> None:
    factory = _RecordingFactory(
        [
            _ImmediateProcess(stdout=b"2026.07.04\n"),
            _ImmediateProcess(
                stdout=json.dumps(_inspection_payload()).encode("utf-8")
            ),
        ]
    )
    downloader = YtDlpYouTubeDownloader(
        _private_staging(tmp_path),
        process_factory=factory,
    )
    identity = canonicalize_youtube_url(
        f"https://youtu.be/{VIDEO_ID}"
    )

    inspection = asyncio.run(downloader.inspect(identity))

    assert inspection.video_id == VIDEO_ID
    assert inspection.extractor_key == "Youtube"
    assert inspection.source_date == "2026-07-01"
    assert inspection.plan == YouTubeDownloadPlan(
        video_format_id="137",
        audio_format_id="140",
        expected_size_bytes=120,
        has_source_audio=True,
        split_streams=True,
    )
    version_argv, version_kwargs = factory.calls[0]
    inspect_argv, inspect_kwargs = factory.calls[1]
    assert version_argv == (
        sys.executable,
        "-I",
        "-m",
        "yt_dlp",
        "--version",
    )
    assert inspect_argv[:4] == (
        sys.executable,
        "-I",
        "-m",
        "yt_dlp",
    )
    assert inspect_argv[-2:] == ("--", identity.canonical_url)
    assert "--ignore-config" in inspect_argv
    assert "--no-playlist" in inspect_argv
    assert not any("cookie" in argument.lower() for argument in inspect_argv)
    for kwargs in (version_kwargs, inspect_kwargs):
        assert "shell" not in kwargs
        assert kwargs["start_new_session"] is True
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert set(environment) == {
            "PATH",
            "LANG",
            "LC_ALL",
            "PYTHONIOENCODING",
            "PYTHONUTF8",
        }
        assert "HOME" not in environment
        assert "HTTP_PROXY" not in environment


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"extractor_key": "Generic"}, "UNSUPPORTED_MEDIA"),
        ({"id": "Other12345_"}, "UNSUPPORTED_MEDIA"),
        ({"live_status": "is_live"}, "UNSUPPORTED_MEDIA"),
        ({"live_status": "post_live"}, "UNSUPPORTED_MEDIA"),
        ({"availability": "needs_auth"}, "UNSUPPORTED_MEDIA"),
        ({"age_limit": 18}, "AUTHENTICATED_MEDIA_REQUIRED"),
        ({"duration": 21_601}, "DURATION_LIMIT"),
        (
            {
                "formats": [
                    {
                        "format_id": "audio",
                        "ext": "m4a",
                        "vcodec": "none",
                        "acodec": "mp4a.40.2",
                    }
                ]
            },
            "NO_VISUAL_STREAM",
        ),
    ],
)
def test_inspection_rejects_unsupported_outcomes(
    tmp_path: Path,
    overrides: dict[str, object],
    code: str,
) -> None:
    factory = _RecordingFactory(
        [
            _ImmediateProcess(stdout=b"2026.07.04\n"),
            _ImmediateProcess(
                stdout=json.dumps(_inspection_payload(**overrides)).encode()
            ),
        ]
    )
    downloader = YtDlpYouTubeDownloader(
        _private_staging(tmp_path),
        process_factory=factory,
    )
    identity = canonicalize_youtube_url(
        f"https://www.youtube.com/watch?v={VIDEO_ID}"
    )

    with pytest.raises(YouTubeInspectionError) as exc_info:
        asyncio.run(downloader.inspect(identity))
    assert exc_info.value.code == code
    assert VIDEO_ID not in str(exc_info.value)


def test_fake_download_resumes_partial_and_produces_fixed_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = _private_staging(tmp_path)
    claim_directory = staging.prepare(STAGING_KEY)
    partial = claim_directory / "artifact.mp4.part"
    partial.write_bytes(b"partial")
    observed_partial: list[bytes] = []

    def on_spawn(_argv: tuple[str, ...], kwargs: dict[str, object]) -> None:
        cwd = Path(str(kwargs["cwd"]))
        observed_partial.append((cwd / "artifact.mp4.part").read_bytes())
        (cwd / "artifact.mp4.part").unlink()
        (cwd / "artifact.mp4").write_bytes(b"synthetic-final")

    factory = _RecordingFactory(
        [_ImmediateProcess()],
        on_spawn=on_spawn,
    )
    monkeypatch.setattr(
        "kronika.infrastructure.youtube.downloader.shutil.which",
        lambda *_args, **_kwargs: "/usr/bin/ffmpeg",
    )
    downloader = YtDlpYouTubeDownloader(
        staging,
        process_factory=factory,
    )
    identity = canonicalize_youtube_url(f"https://youtu.be/{VIDEO_ID}")
    inspection = YouTubeInspection(
        video_id=VIDEO_ID,
        extractor_key="Youtube",
        title=None,
        channel=None,
        channel_id=None,
        source_date=None,
        remote_filename=None,
        duration_seconds=10,
        downloader_version="2026.7.4",
        extractor_version="2026.7.4",
        plan=YouTubeDownloadPlan(
            video_format_id="137",
            audio_format_id="140",
            expected_size_bytes=None,
            has_source_audio=True,
            split_streams=True,
        ),
    )

    result = asyncio.run(
        downloader.download(
            identity,
            inspection,
            staging_key=STAGING_KEY,
        )
    )

    assert observed_partial == [b"partial"]
    assert result.size_bytes == len(b"synthetic-final")
    argv, kwargs = factory.calls[0]
    assert "--continue" in argv
    assert argv[argv.index("--format") + 1] == "137+140"
    assert argv[argv.index("--output") + 1] == "artifact.mp4"
    assert "--recode-video" not in argv
    assert "--exec" not in argv
    assert kwargs["cwd"] == str(claim_directory)


def test_timeout_terminates_process_group_then_uses_bounded_kill_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _HangingProcess()
    factory = _RecordingFactory([process])
    signals: list[signal.Signals] = []

    def fake_killpg(pid: int, sent_signal: signal.Signals) -> None:
        assert pid == process.pid
        signals.append(sent_signal)
        if sent_signal == signal.SIGKILL:
            process.finished.set()

    monkeypatch.setattr(
        "kronika.infrastructure.youtube.downloader.os.killpg",
        fake_killpg,
    )
    monkeypatch.setattr(
        "kronika.infrastructure.youtube.downloader.TERMINATE_GRACE_SECONDS",
        0.01,
    )
    downloader = YtDlpYouTubeDownloader(
        _private_staging(tmp_path),
        process_factory=factory,
        inspection_timeout_seconds=0.01,
    )

    with pytest.raises(YouTubeDownloaderConfigurationError) as exc_info:
        asyncio.run(downloader.attest_version())

    assert exc_info.value.code == "DOWNLOADER_ATTESTATION_TIMEOUT"
    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_intermediate_staging_limit_is_enforced_before_subprocess(
    tmp_path: Path,
) -> None:
    staging = _private_staging(tmp_path)
    claim_directory = staging.prepare(STAGING_KEY)
    (claim_directory / "artifact.mp4.part").write_bytes(b"12345")
    factory = _RecordingFactory([_ImmediateProcess()])
    downloader = YtDlpYouTubeDownloader(
        staging,
        process_factory=factory,
        max_staging_size_bytes=4,
        free_space_reserve_bytes=0,
    )
    identity = canonicalize_youtube_url(f"https://youtu.be/{VIDEO_ID}")
    inspection = YouTubeInspection(
        video_id=VIDEO_ID,
        extractor_key="Youtube",
        title=None,
        channel=None,
        channel_id=None,
        source_date=None,
        remote_filename=None,
        duration_seconds=10,
        downloader_version="2026.7.4",
        extractor_version="2026.7.4",
        plan=YouTubeDownloadPlan(
            video_format_id="18",
            audio_format_id=None,
            expected_size_bytes=None,
            has_source_audio=True,
            split_streams=False,
        ),
    )

    with pytest.raises(YouTubeDownloadError) as exc_info:
        asyncio.run(
            downloader.download(
                identity,
                inspection,
                staging_key=STAGING_KEY,
            )
        )
    assert exc_info.value.code == "STAGING_SIZE_LIMIT"
    assert factory.calls == []


def test_shutdown_term_kill_respects_remaining_budget_and_reaps_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _HangingProcess()
    factory = _RecordingFactory([process])
    signals: list[signal.Signals] = []

    def fake_killpg(pid: int, sent_signal: signal.Signals) -> None:
        assert pid == process.pid
        signals.append(sent_signal)
        if sent_signal == signal.SIGKILL:
            process.finished.set()

    monkeypatch.setattr(
        "kronika.infrastructure.youtube.downloader.os.killpg",
        fake_killpg,
    )
    downloader = YtDlpYouTubeDownloader(
        _private_staging(tmp_path),
        process_factory=factory,
        inspection_timeout_seconds=0.05,
    )
    downloader.bind_shutdown_deadline(ShutdownDeadline(0.2))

    started = time.monotonic()
    with pytest.raises(YouTubeDownloaderConfigurationError) as exc_info:
        asyncio.run(downloader.attest_version())
    elapsed = time.monotonic() - started
    assert exc_info.value.code == "DOWNLOADER_ATTESTATION_TIMEOUT"
    assert elapsed < 0.5
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert process.finished.is_set()
