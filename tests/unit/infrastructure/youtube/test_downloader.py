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
from kronika.application.ports.youtube_staging import YouTubeStagingError
from kronika.domain.youtube_acquisition import canonicalize_youtube_url
from kronika.infrastructure.youtube.downloader import (
    INSPECTION_FORMATS_TEMPLATE,
    INSPECTION_METADATA_TEMPLATE,
    INSPECTION_STDERR_LIMIT_BYTES,
    INSPECTION_STDOUT_LIMIT_BYTES,
    MAX_STDERR_BYTES,
    MAX_STDOUT_BYTES,
    YtDlpYouTubeDownloader,
    _CompletedProcess,
    _capture_stream,
    _download_failure_code,
)
from kronika.infrastructure.youtube.staging import FilesystemYouTubeStaging
from kronika.application.in_process_lifecycle import ShutdownDeadline

VIDEO_ID = "AbCdEf123_-"
STAGING_KEY = "2" * 32


class _FakeStream:
    def __init__(self, payload: bytes = b"") -> None:
        self._payload = payload
        self.reads = 0

    async def read(self, _size: int) -> bytes:
        self.reads += 1
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
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> None:
        super().__init__(stdout=stdout, stderr=stderr, returncode=returncode)
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


_METADATA_KEYS = (
    "extractor_key",
    "id",
    "_type",
    "is_live",
    "live_status",
    "availability",
    "age_limit",
    "duration",
    "title",
    "channel",
    "channel_id",
    "upload_date",
    "_filename",
    "filename",
)
_FORMAT_KEYS = (
    "format_id",
    "ext",
    "video_ext",
    "audio_ext",
    "vcodec",
    "acodec",
    "height",
    "width",
    "fps",
    "tbr",
    "abr",
    "asr",
    "filesize",
    "filesize_approx",
    "has_drm",
)


def _inspection_stdout(payload: dict[str, object]) -> bytes:
    metadata = {key: payload[key] for key in _METADATA_KEYS if key in payload}
    formats = payload.get("formats", [])
    if isinstance(formats, list):
        projected_formats: list[object] = []
        for item in formats:
            if isinstance(item, dict):
                projected_formats.append(
                    {key: item[key] for key in _FORMAT_KEYS if key in item}
                )
            else:
                projected_formats.append(item)
    else:
        projected_formats = formats
    rendered = (
        json.dumps(metadata, separators=(",", ":"))
        + "\n"
        + json.dumps(projected_formats, separators=(",", ":"))
        + "\n"
    )
    return rendered.encode("utf-8")


def _stdout_of_length(payload: dict[str, object], length: int) -> bytes:
    encoded = _inspection_stdout(payload)
    deficit = length - len(encoded)
    if deficit < 0:
        raise AssertionError(f"projection exceeds {length}")
    titled = dict(payload)
    title = titled.get("title")
    titled["title"] = (title if isinstance(title, str) else "T") + ("." * deficit)
    encoded = _inspection_stdout(titled)
    if len(encoded) != length:
        raise AssertionError(len(encoded))
    return encoded


def _record_killpg(
    monkeypatch: pytest.MonkeyPatch,
    signals: list[signal.Signals],
) -> None:
    def fake_killpg(_pid: int, sent_signal: signal.Signals) -> None:
        signals.append(sent_signal)

    monkeypatch.setattr(
        "kronika.infrastructure.youtube.downloader.os.killpg",
        fake_killpg,
    )


def test_attestation_and_inspection_use_exact_isolated_argv_and_environment(
    tmp_path: Path,
) -> None:
    factory = _RecordingFactory(
        [
            _ImmediateProcess(stdout=b"2026.07.04\n"),
            _ImmediateProcess(stdout=_inspection_stdout(_inspection_payload())),
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
    assert inspect_argv == (
        sys.executable,
        "-I",
        "-m",
        "yt_dlp",
        "--ignore-config",
        "--no-playlist",
        "--no-warnings",
        "--quiet",
        "--no-cache-dir",
        "--socket-timeout",
        "30",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--simulate",
        "--no-write-info-json",
        "--print",
        INSPECTION_METADATA_TEMPLATE,
        "--print",
        INSPECTION_FORMATS_TEMPLATE,
        "--",
        identity.canonical_url,
    )
    assert INSPECTION_METADATA_TEMPLATE == (
        "%(.{extractor_key,id,_type,is_live,live_status,availability,"
        "age_limit,duration,title,channel,channel_id,upload_date,"
        "_filename,filename})j"
    )
    assert INSPECTION_FORMATS_TEMPLATE == (
        "%(formats.:.{format_id,ext,video_ext,audio_ext,vcodec,acodec,"
        "height,width,fps,tbr,abr,asr,filesize,filesize_approx,has_drm})j"
    )
    assert "--dump-single-json" not in inspect_argv
    assert "--skip-download" not in inspect_argv
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
                stdout=_inspection_stdout(_inspection_payload(**overrides))
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


def _identity() -> object:
    return canonicalize_youtube_url(f"https://youtu.be/{VIDEO_ID}")


def _run_inspection(
    tmp_path: Path,
    process: _ImmediateProcess,
    *,
    monkeypatch: pytest.MonkeyPatch | None = None,
    signals: list[signal.Signals] | None = None,
    **kwargs: object,
) -> object:
    if monkeypatch is not None and signals is not None:
        _record_killpg(monkeypatch, signals)
    factory = _RecordingFactory(
        [_ImmediateProcess(stdout=b"2026.07.04\n"), process]
    )
    downloader = YtDlpYouTubeDownloader(
        _private_staging(tmp_path),
        process_factory=factory,
        **kwargs,
    )
    return asyncio.run(downloader.inspect(_identity()))


def test_at_cap_two_line_inspection_keeps_the_existing_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[signal.Signals] = []
    stdout = _stdout_of_length(
        _inspection_payload(),
        INSPECTION_STDOUT_LIMIT_BYTES,
    )
    inspection = _run_inspection(
        tmp_path,
        _ImmediateProcess(
            stdout=stdout,
            stderr=b"e" * INSPECTION_STDERR_LIMIT_BYTES,
        ),
        monkeypatch=monkeypatch,
        signals=signals,
    )

    assert isinstance(inspection, YouTubeInspection)
    assert inspection.plan == YouTubeDownloadPlan(
        video_format_id="137",
        audio_format_id="140",
        expected_size_bytes=120,
        has_source_audio=True,
        split_streams=True,
    )
    assert signals == []


@pytest.mark.parametrize(
    ("stdout_over", "stderr_over", "returncode"),
    [
        (1, 0, 0),
        (0, 1, 0),
        (1, 1, 0),
        (1, 0, 1),
    ],
)
def test_inspection_overflow_is_output_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout_over: int,
    stderr_over: int,
    returncode: int,
) -> None:
    signals: list[signal.Signals] = []
    valid = _inspection_stdout(_inspection_payload())
    stdout = (
        b"x" * (INSPECTION_STDOUT_LIMIT_BYTES + stdout_over)
        if stdout_over
        else valid
    )
    stderr = b"e" * (INSPECTION_STDERR_LIMIT_BYTES + stderr_over)
    with pytest.raises(YouTubeInspectionError) as exc_info:
        _run_inspection(
            tmp_path,
            _ImmediateProcess(
                stdout=stdout,
                stderr=stderr,
                returncode=returncode,
            ),
            monkeypatch=monkeypatch,
            signals=signals,
        )
    assert exc_info.value.code == "INSPECTION_OUTPUT_LIMIT"


def test_far_over_inspection_retains_the_cap_and_kills_the_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _HangingProcess(
        stdout=b"x" * (INSPECTION_STDOUT_LIMIT_BYTES + 100_000)
    )
    signals: list[signal.Signals] = []
    retained: list[tuple[int, bool]] = []

    async def spy(
        stream: object,
        limit: int,
        *,
        stop_after_limit: bool = False,
    ) -> tuple[bytes, bool]:
        data, overflow = await _capture_stream(
            stream,
            limit,
            stop_after_limit=stop_after_limit,
        )
        if limit == INSPECTION_STDOUT_LIMIT_BYTES:
            retained.append((len(data), overflow))
        return data, overflow

    def fake_killpg(pid: int, sent_signal: signal.Signals) -> None:
        assert pid == process.pid
        signals.append(sent_signal)
        if sent_signal == signal.SIGKILL:
            process.finished.set()

    monkeypatch.setattr(
        "kronika.infrastructure.youtube.downloader._capture_stream",
        spy,
    )
    monkeypatch.setattr(
        "kronika.infrastructure.youtube.downloader.os.killpg",
        fake_killpg,
    )
    monkeypatch.setattr(
        "kronika.infrastructure.youtube.downloader.TERMINATE_GRACE_SECONDS",
        0.01,
    )
    with pytest.raises(YouTubeInspectionError) as exc_info:
        _run_inspection(
            tmp_path,
            process,
            inspection_timeout_seconds=2.0,
        )
    assert exc_info.value.code == "INSPECTION_OUTPUT_LIMIT"
    assert retained == [(INSPECTION_STDOUT_LIMIT_BYTES, True)]
    assert process.stdout.reads == 1
    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_inspection_termination_failure_is_not_relabeled_as_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_terminate(_self: object, _process: object) -> None:
        raise YouTubeDownloadError("SUBPROCESS_TERMINATION_FAILED")

    monkeypatch.setattr(
        YtDlpYouTubeDownloader,
        "_terminate_process_group",
        fail_terminate,
    )
    with pytest.raises(YouTubeInspectionError) as exc_info:
        _run_inspection(
            tmp_path,
            _ImmediateProcess(
                stdout=b"x" * (INSPECTION_STDOUT_LIMIT_BYTES + 1)
            ),
            inspection_timeout_seconds=1.0,
        )
    assert exc_info.value.code == "INSPECTION_OUTPUT_LIMIT"


def test_inspection_nonzero_exit_without_overflow_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[signal.Signals] = []
    with pytest.raises(YouTubeInspectionError) as exc_info:
        _run_inspection(
            tmp_path,
            _ImmediateProcess(
                stdout=_inspection_stdout(_inspection_payload()),
                returncode=1,
            ),
            monkeypatch=monkeypatch,
            signals=signals,
        )
    assert exc_info.value.code == "INSPECTION_FAILED"
    assert signals == []


def test_hanging_inspection_times_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _HangingProcess()
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
    with pytest.raises(YouTubeInspectionError) as exc_info:
        _run_inspection(
            tmp_path,
            process,
            inspection_timeout_seconds=0.05,
        )
    assert exc_info.value.code == "INSPECTION_TIMEOUT"
    assert signals == [signal.SIGTERM, signal.SIGKILL]


@pytest.mark.parametrize(
    "stdout",
    [
        b'{"extractor_key":"Youtube"\n[]\n',
        b"not-json\n[]\n",
        b"",
        json.dumps(_inspection_payload()).encode("utf-8"),
        b"NA\n[]\n",
        b"\xff\n[]\n",
        b"[]\n[]\n",
        json.dumps(
            {"id": VIDEO_ID, "formats": []},
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n[]\n",
        _inspection_stdout(_inspection_payload()).splitlines()[0] + b"\n{}\n",
    ],
)
def test_malformed_projection_is_invalid_output(
    tmp_path: Path,
    stdout: bytes,
) -> None:
    with pytest.raises(YouTubeInspectionError) as exc_info:
        _run_inspection(tmp_path, _ImmediateProcess(stdout=stdout))
    assert exc_info.value.code == "INSPECTION_INVALID_OUTPUT"
    assert VIDEO_ID not in str(exc_info.value)


def test_formats_line_na_selects_no_visual_stream(tmp_path: Path) -> None:
    payload = _inspection_payload()
    metadata = {key: payload[key] for key in _METADATA_KEYS if key in payload}
    stdout = json.dumps(metadata, separators=(",", ":")).encode("utf-8") + b"\nNA\n"
    with pytest.raises(YouTubeInspectionError) as exc_info:
        _run_inspection(tmp_path, _ImmediateProcess(stdout=stdout))
    assert exc_info.value.code == "NO_VISUAL_STREAM"


@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
def test_download_output_limit_waits_for_exit_without_kill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stream_name: str,
) -> None:
    signals: list[signal.Signals] = []
    _record_killpg(monkeypatch, signals)
    process = _ImmediateProcess(
        stdout=b"x" * (MAX_STDOUT_BYTES + 1 if stream_name == "stdout" else 0),
        stderr=b"e" * (MAX_STDERR_BYTES + 1 if stream_name == "stderr" else 0),
    )
    factory = _RecordingFactory([process])
    downloader = YtDlpYouTubeDownloader(
        _private_staging(tmp_path),
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
    assert exc_info.value.code == "DOWNLOAD_OUTPUT_LIMIT"
    assert signals == []
    overflowing = process.stdout if stream_name == "stdout" else process.stderr
    assert overflowing.reads == 2


def _download_inspection() -> YouTubeInspection:
    return YouTubeInspection(
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


def _run_test_download(downloader: YtDlpYouTubeDownloader):
    async def bounded():
        return await asyncio.wait_for(
            downloader.download(
                canonicalize_youtube_url(f"https://youtu.be/{VIDEO_ID}"),
                _download_inspection(),
                staging_key=STAGING_KEY,
            ),
            timeout=2,
        )

    return asyncio.run(bounded())


_FORMAT_ERROR = (
    b"Requested format is not available. Use --list-formats for a list of "
    b"available formats"
)
_AUTH_HINT = b"Use --cookies-from-browser or --cookies for the authentication."
_HTTP_ERROR = b"ERROR: unable to download video data: HTTP Error 403: Forbidden\n"


@pytest.mark.parametrize(
    ("stderr", "returncode", "code"),
    [
        (b"ERROR: " + _FORMAT_ERROR, 1, "DOWNLOAD_FORMAT_UNAVAILABLE"),
        (f"\r\nERROR: [youtube] {VIDEO_ID}: ".encode() + _FORMAT_ERROR + b"\r\n\r\n",
         1, "DOWNLOAD_FORMAT_UNAVAILABLE"),
        (b"ERROR: Postprocessing: synthetic HTTP Error 403: timed out", 1,
         "DOWNLOAD_POSTPROCESS_FAILED"),
        *[(b"ERROR: " + prefix + b"synthetic HTTP Error 403: timed out", 1,
           "DOWNLOAD_STORAGE_FAILED") for prefix in (
            b"unable to open for writing: ", b"unable to write data: ",
            b"Unable to rename file: ", b"Unable to remove file: ",
        )],
        (b"ERROR: Login details are needed to download this content. synthetic", 1,
         "DOWNLOAD_AUTH_REQUIRED"),
        (b"ERROR: Sign In to continue. " + _AUTH_HINT + b" HTTP Error 403: synthetic",
         1, "DOWNLOAD_AUTH_REQUIRED"),
        (_HTTP_ERROR, 1, "DOWNLOAD_HTTP_403"),
        (f" \r\nERROR: [youtube] {VIDEO_ID}: ".encode()
         + b"HTTP Error 403: synthetic\r\n \n", 1, "DOWNLOAD_HTTP_403"),
        *[(b"ERROR: \r" + prefix + b"synthetic TiMeD OuT", 1,
           "DOWNLOAD_NETWORK_TIMEOUT") for prefix in (
            b"unable to download video data: ", b"[download] Got error: ",
            b"Unable to download webpage: ", b"Unable to download API page: ",
        )],
        (b"ERROR: unknown synthetic failure", 1, "DOWNLOAD_FAILED"),
        (b"", 1, "DOWNLOAD_FAILED"),
        (b"ERROR: ", 1, "DOWNLOAD_FAILED"),
        (b"ERROR: [youtube] ZyXwVu987_-: " + _FORMAT_ERROR, 1, "DOWNLOAD_FAILED"),
        (b"ERROR: [other] " + _FORMAT_ERROR, 1, "DOWNLOAD_FAILED"),
        (b"WARNING: synthetic\n" + _HTTP_ERROR, 1, "DOWNLOAD_FAILED"),
        (_HTTP_ERROR + _HTTP_ERROR, 1, "DOWNLOAD_FAILED"),
        (_HTTP_ERROR + b"synthetic continuation\n", 1, "DOWNLOAD_FAILED"),
        ("ERROR: Postprocessing: synthetic\u2028continuation".encode(), 1,
         "DOWNLOAD_FAILED"),
        ("ERROR: Postprocessing: synthetic\u0085continuation".encode(), 1,
         "DOWNLOAD_FAILED"),
        (b"ERROR: first\n\nHTTP Error 403: synthetic", 1, "DOWNLOAD_FAILED"),
        (b"HTTP Error 403: synthetic", 1, "DOWNLOAD_FAILED"),
        (b" ERROR: HTTP Error 403: synthetic", 1, "DOWNLOAD_FAILED"),
        (b"ERROR: sign in without authentication hint", 1, "DOWNLOAD_FAILED"),
        (b"ERROR: " + _AUTH_HINT, 1, "DOWNLOAD_FAILED"),
        (b"ERROR: unknown timed out", 1, "DOWNLOAD_FAILED"),
        (b"ERROR: " + _FORMAT_ERROR + b" changed", 1, "DOWNLOAD_FAILED"),
        (b"ERROR: PO token was not provided", 1, "DOWNLOAD_FAILED"),
        (_HTTP_ERROR + b"\xff", 1, "DOWNLOAD_FAILED"),
        (b"\x1b[31m" + _HTTP_ERROR, 1, "DOWNLOAD_FAILED"),
        *[(_HTTP_ERROR + bytes([control]), 1, "DOWNLOAD_FAILED")
          for control in (*range(10), 11, 12, *range(14, 32), 127)],
        (_HTTP_ERROR, 2, "DOWNLOAD_FAILED"),
        (_HTTP_ERROR, -signal.SIGTERM, "DOWNLOAD_FAILED"),
        (_HTTP_ERROR, -signal.SIGKILL, "DOWNLOAD_FAILED"),
    ],
    ids=lambda value: None if not isinstance(value, bytes) else "synthetic-stderr",
)
def test_download_nonzero_classifies_only_supported_terminal_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    stderr: bytes,
    returncode: int,
    code: str,
) -> None:
    staging = _private_staging(tmp_path)
    monkeypatch.setattr(staging, "open_artifact", lambda _key: pytest.fail("artifact handoff"))
    factory = _RecordingFactory([_ImmediateProcess(stderr=stderr, returncode=returncode)])
    with pytest.raises(YouTubeDownloadError) as exc_info:
        _run_test_download(YtDlpYouTubeDownloader(staging, process_factory=factory))
    assert exc_info.value.code == code
    assert str(exc_info.value) == "YouTube downloader operation failed."
    assert len(factory.calls) == 1
    assert capsys.readouterr() == ("", "")
    assert caplog.records == []


@pytest.mark.parametrize("success", [False, True])
def test_download_classifier_ignores_stdout_and_success_stderr(
    tmp_path: Path, success: bool,
) -> None:
    staging = _private_staging(tmp_path)
    process = _ImmediateProcess(
        stdout=_HTTP_ERROR, stderr=_HTTP_ERROR if success else b"ERROR: unknown",
        returncode=0 if success else 1,
    )

    def artifact(_argv, kwargs):
        (Path(kwargs["cwd"]) / "artifact.mp4").write_bytes(b"synthetic")

    factory = _RecordingFactory([process], on_spawn=artifact)
    downloader = YtDlpYouTubeDownloader(staging, process_factory=factory)
    if success:
        assert _run_test_download(downloader).size_bytes == len(b"synthetic")
    else:
        with pytest.raises(YouTubeDownloadError) as exc_info:
            _run_test_download(downloader)
        assert exc_info.value.code == "DOWNLOAD_FAILED"
    # Direct helper bound, including callers without capture overflow flags.
    assert _download_failure_code(
        _CompletedProcess(1, b"", _HTTP_ERROR + b"x" * MAX_STDERR_BYTES, False, False),
        video_id=VIDEO_ID,
    ) == "DOWNLOAD_FAILED"
    at_cap = _HTTP_ERROR.rstrip(b"\n")
    at_cap += b" " * (MAX_STDERR_BYTES - len(at_cap))
    assert _download_failure_code(
        _CompletedProcess(1, b"", at_cap, False, False), video_id=VIDEO_ID,
    ) == "DOWNLOAD_HTTP_403"
    assert len(factory.calls) == 1


@pytest.mark.parametrize("streams", ["stdout", "stderr", "both"])
@pytest.mark.parametrize("returncode", [0, 1, -signal.SIGTERM])
def test_download_output_overflow_precedes_exit_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, streams: str, returncode: int,
) -> None:
    process = _ImmediateProcess(
        stdout=b"x" * (MAX_STDOUT_BYTES + 33) if streams != "stderr" else b"",
        stderr=_HTTP_ERROR + b"x" * MAX_STDERR_BYTES if streams != "stdout" else _HTTP_ERROR,
        returncode=returncode,
    )
    retained: dict[int, tuple[int, bool]] = {}

    async def capture(stream, limit, **kwargs):
        data, overflow = await _capture_stream(stream, limit, **kwargs)
        retained[limit] = (len(data), overflow)
        return data, overflow

    monkeypatch.setattr("kronika.infrastructure.youtube.downloader._capture_stream", capture)
    signals: list[signal.Signals] = []
    _record_killpg(monkeypatch, signals)
    staging = _private_staging(tmp_path)
    monkeypatch.setattr(staging, "open_artifact", lambda _key: pytest.fail("artifact handoff"))
    factory = _RecordingFactory([process])
    with pytest.raises(YouTubeDownloadError) as exc_info:
        _run_test_download(YtDlpYouTubeDownloader(staging, process_factory=factory))
    assert exc_info.value.code == "DOWNLOAD_OUTPUT_LIMIT"
    assert retained[MAX_STDOUT_BYTES] == (
        MAX_STDOUT_BYTES if streams != "stderr" else 0, streams != "stderr",
    )
    assert retained[MAX_STDERR_BYTES] == (
        MAX_STDERR_BYTES if streams != "stdout" else len(_HTTP_ERROR), streams != "stdout",
    )
    assert process.stdout.reads == (1 if streams == "stderr" else 2)
    assert process.stderr.reads == 2
    assert len(factory.calls) == 1
    assert signals == []


@pytest.mark.parametrize("kill_required", [False, True])
def test_download_timeout_terminates_group_and_preserves_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kill_required: bool,
) -> None:
    process = _HangingProcess(stderr=_HTTP_ERROR)
    signals: list[signal.Signals] = []

    def killpg(pid, sent_signal):
        assert pid == process.pid
        signals.append(sent_signal)
        if not kill_required or sent_signal == signal.SIGKILL:
            process.finished.set()

    monkeypatch.setattr("kronika.infrastructure.youtube.downloader.os.killpg", killpg)
    monkeypatch.setattr("kronika.infrastructure.youtube.downloader.TERMINATE_GRACE_SECONDS", 0.01)
    monkeypatch.setattr("kronika.infrastructure.youtube.downloader.KILL_GRACE_SECONDS", 0.01)
    factory = _RecordingFactory([process])
    with pytest.raises(YouTubeDownloadError) as exc_info:
        _run_test_download(YtDlpYouTubeDownloader(
            _private_staging(tmp_path), process_factory=factory, download_timeout_seconds=0.01,
        ))
    assert exc_info.value.code == "DOWNLOAD_TIMEOUT"
    assert signals == ([signal.SIGTERM, signal.SIGKILL] if kill_required else [signal.SIGTERM])
    assert process.finished.is_set()
    assert len(factory.calls) == 1


@pytest.mark.parametrize(("failure", "running"), [
    ("prepare", False), ("usage", False), ("size", False), ("reserve", False),
    ("usage", True), ("size", True), ("reserve", True),
])
def test_download_staging_failures_preserve_existing_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, running: bool,
) -> None:
    staging = _private_staging(tmp_path)
    process = _HangingProcess(stderr=_HTTP_ERROR)
    factory = _RecordingFactory([process])

    def usage(_key):
        if not running or factory.calls:
            if failure == "usage":
                raise YouTubeStagingError("synthetic staging error")
            if failure == "size":
                return 5
        return 0

    def available():
        return 0 if failure == "reserve" and (not running or factory.calls) else 10

    def prepare(_key):
        raise YouTubeStagingError("synthetic preparation error")

    monkeypatch.setattr(staging, "usage_bytes", usage)
    monkeypatch.setattr(staging, "available_bytes", available)
    if failure == "prepare":
        monkeypatch.setattr(staging, "prepare", prepare)
    signals: list[signal.Signals] = []

    def killpg(pid, sent_signal):
        assert pid == process.pid
        signals.append(sent_signal)
        process.finished.set()

    monkeypatch.setattr("kronika.infrastructure.youtube.downloader.os.killpg", killpg)
    with pytest.raises(YouTubeDownloadError) as exc_info:
        _run_test_download(YtDlpYouTubeDownloader(
            staging, process_factory=factory, max_staging_size_bytes=4,
            free_space_reserve_bytes=1, download_timeout_seconds=1,
        ))
    assert exc_info.value.code == {
        "prepare": "STAGING_UNAVAILABLE", "usage": "STAGING_UNAVAILABLE",
        "size": "STAGING_SIZE_LIMIT", "reserve": "STAGING_SPACE_RESERVE",
    }[failure]
    assert len(factory.calls) == int(running)
    assert signals == ([signal.SIGTERM] if running else [])


@pytest.mark.parametrize("artifact", ["missing", "invalid", "exact", "above", "changed"])
def test_download_artifact_validation_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str,
) -> None:
    staging = _private_staging(tmp_path)
    readers = []
    original_open = staging.open_artifact

    class ReaderProbe:
        def __init__(self, reader):
            self.reader = reader
            self.size_bytes = reader.size_bytes
            self.closed = False

        def verify_still_consistent(self):
            if artifact == "changed":
                raise YouTubeStagingError("synthetic changed artifact")
            self.reader.verify_still_consistent()

        def close(self):
            self.closed = True
            self.reader.close()

    def open_artifact(key):
        reader = ReaderProbe(original_open(key))
        readers.append(reader)
        return reader

    def on_spawn(_argv, kwargs):
        path = Path(kwargs["cwd"]) / "artifact.mp4"
        if artifact == "invalid":
            path.mkdir()
        elif artifact != "missing":
            path.write_bytes(b"12345" if artifact == "above" else b"1234")

    monkeypatch.setattr(staging, "open_artifact", open_artifact)
    factory = _RecordingFactory([_ImmediateProcess()], on_spawn=on_spawn)
    downloader = YtDlpYouTubeDownloader(staging, process_factory=factory, max_final_size_bytes=4)
    if artifact == "exact":
        assert _run_test_download(downloader).size_bytes == 4
    else:
        with pytest.raises(YouTubeDownloadError) as exc_info:
            _run_test_download(downloader)
        assert exc_info.value.code == (
            "FINAL_SIZE_LIMIT" if artifact == "above" else "DOWNLOAD_ARTIFACT_INVALID"
        )
    assert len(factory.calls) == 1
    assert len(readers) == int(artifact not in {"missing", "invalid"})
    assert all(reader.closed for reader in readers)


_FINAL_CAP = 1_073_741_824


def _visual(
    format_id: str,
    height: int,
    filesize: int,
    **extra: object,
) -> dict[str, object]:
    item: dict[str, object] = {
        "format_id": format_id,
        "ext": "mp4",
        "video_ext": "mp4",
        "vcodec": "avc1.640028",
        "acodec": "none",
        "height": height,
        "width": 1280,
        "fps": 30,
        "tbr": 1000,
        "filesize": filesize,
        "url": f"https://example.invalid/{format_id}",
        "fragments": [{"url": f"https://example.invalid/{format_id}/0"}],
    }
    item.update(extra)
    return item


def _audio(format_id: str, abr: int, filesize: int) -> dict[str, object]:
    return {
        "format_id": format_id,
        "ext": "m4a",
        "audio_ext": "m4a",
        "vcodec": "none",
        "acodec": "mp4a.40.2",
        "abr": abr,
        "asr": 44100,
        "filesize": filesize,
        "url": f"https://example.invalid/{format_id}",
        "fragments": [{"url": f"https://example.invalid/{format_id}/0"}],
    }


def _combined(format_id: str, height: int, filesize: int) -> dict[str, object]:
    return {
        "format_id": format_id,
        "ext": "mp4",
        "video_ext": "mp4",
        "audio_ext": "mp4",
        "vcodec": "avc1.640028",
        "acodec": "mp4a.40.2",
        "height": height,
        "width": 1280,
        "fps": 30,
        "tbr": 2000,
        "filesize": filesize,
        "url": f"https://example.invalid/{format_id}",
        "fragments": [{"url": f"https://example.invalid/{format_id}/0"}],
    }


def _drm() -> dict[str, object]:
    return {
        "format_id": "drm1",
        "ext": "mp4",
        "video_ext": "mp4",
        "vcodec": "avc1.640032",
        "acodec": "none",
        "height": 4320,
        "filesize": 10,
        "has_drm": True,
        "url": "https://example.invalid/drm",
        "fragments": [{"url": "https://example.invalid/drm/0"}],
    }


def _equivalence_formats(mode: str) -> list[dict[str, object]]:
    if mode == "split":
        return [
            _visual("136", 720, 400),
            _drm(),
            _visual("303", 2160, _FINAL_CAP),
            _visual("137", 1080, 1000, filesize_approx=1),
            _audio("140", 128, 20),
            _audio("141", 256, 40),
            _combined("22", 720, 5000),
        ]
    if mode == "combined":
        return [
            _visual("136", 720, _FINAL_CAP),
            _drm(),
            _visual("303", 2160, _FINAL_CAP),
            _visual("137", 1080, _FINAL_CAP),
            _audio("140", 128, _FINAL_CAP),
            _audio("141", 256, _FINAL_CAP),
            _combined("299", 1080, _FINAL_CAP + 1),
            _combined("22", 720, 800),
        ]
    if mode == "silent":
        return [
            _visual("136", 720, 100),
            _drm(),
            _visual("303", 2160, _FINAL_CAP + 1),
            _visual("137", 1080, 1000, filesize_approx=1),
        ]
    return [
        _visual("137", 1080, _FINAL_CAP),
        _drm(),
        _audio("140", 128, _FINAL_CAP),
        _combined("22", 720, _FINAL_CAP + 1),
    ]


def _full_info(formats: list[dict[str, object]]) -> dict[str, object]:
    return {
        "id": VIDEO_ID,
        "extractor_key": "Youtube",
        "_type": "video",
        "is_live": False,
        "live_status": "not_live",
        "availability": "public",
        "age_limit": 0,
        "duration": 120,
        "title": "Synthetic title",
        "channel": "Synthetic channel",
        "channel_id": "channel-id",
        "upload_date": "20260701",
        "_filename": "Remote title.mp4",
        "filename": "fallback-name.mp4",
        "description": "synthetic description",
        "thumbnails": [{"url": "https://example.invalid/thumb.jpg"}],
        "automatic_captions": {
            "en": [{"url": "https://example.invalid/cap.vtt"}]
        },
        "subtitles": {"en": [{"url": "https://example.invalid/sub.vtt"}]},
        "heatmap": [{"start_time": 0, "end_time": 1, "value": 1}],
        "formats": formats,
    }


def _selection_outcome(
    downloader: YtDlpYouTubeDownloader,
    info: dict[str, object],
) -> YouTubeInspection | str:
    try:
        return downloader._inspection_from_payload(
            canonicalize_youtube_url(f"https://youtu.be/{VIDEO_ID}"),
            info,
            "2026.7.4",
        )
    except YouTubeInspectionError as exc:
        return exc.code


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (
            "split",
            YouTubeDownloadPlan(
                video_format_id="137",
                audio_format_id="141",
                expected_size_bytes=1040,
                has_source_audio=True,
                split_streams=True,
            ),
        ),
        (
            "combined",
            YouTubeDownloadPlan(
                video_format_id="22",
                audio_format_id=None,
                expected_size_bytes=800,
                has_source_audio=True,
                split_streams=False,
            ),
        ),
        (
            "silent",
            YouTubeDownloadPlan(
                video_format_id="137",
                audio_format_id=None,
                expected_size_bytes=1000,
                has_source_audio=False,
                split_streams=False,
            ),
        ),
        ("over-cap", "COMPATIBLE_FORMAT_UNAVAILABLE"),
    ],
)
def test_projected_formats_match_full_payload_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected: YouTubeDownloadPlan | str,
) -> None:
    info = _full_info(_equivalence_formats(mode))
    staging = _private_staging(tmp_path)
    direct = YtDlpYouTubeDownloader(staging)
    direct_outcome = _selection_outcome(direct, info)
    stdout = _inspection_stdout(info)
    text = stdout.decode("utf-8")
    metadata_line, formats_line = text.splitlines()
    assert "formats" not in json.loads(metadata_line)
    assert isinstance(json.loads(formats_line), list)
    for banned in (
        "description",
        "thumbnails",
        "automatic_captions",
        "subtitles",
        "heatmap",
        "fragments",
        '"url"',
    ):
        assert banned not in text
    signals: list[signal.Signals] = []
    _record_killpg(monkeypatch, signals)
    factory = _RecordingFactory(
        [
            _ImmediateProcess(stdout=b"2026.07.04\n"),
            _ImmediateProcess(stdout=stdout),
        ]
    )
    projected = YtDlpYouTubeDownloader(staging, process_factory=factory)
    try:
        inspected_outcome: YouTubeInspection | str = asyncio.run(
            projected.inspect(
                canonicalize_youtube_url(f"https://youtu.be/{VIDEO_ID}")
            )
        )
    except YouTubeInspectionError as exc:
        inspected_outcome = exc.code
    assert signals == []
    if isinstance(expected, str):
        assert direct_outcome == expected
        assert inspected_outcome == expected
        return
    assert isinstance(direct_outcome, YouTubeInspection)
    assert isinstance(inspected_outcome, YouTubeInspection)
    assert direct_outcome.plan == expected
    assert inspected_outcome.plan == expected
    assert inspected_outcome.title == direct_outcome.title == "Synthetic title"
    assert inspected_outcome.channel == direct_outcome.channel == "Synthetic channel"
    assert inspected_outcome.channel_id == direct_outcome.channel_id == "channel-id"
    assert inspected_outcome.source_date == direct_outcome.source_date == "2026-07-01"
    assert (
        inspected_outcome.remote_filename
        == direct_outcome.remote_filename
        == "Remote title.mp4"
    )
