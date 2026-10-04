"""Shell-free, cookie-free yt-dlp subprocess boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
import json
import math
import os
from pathlib import Path
import shutil
import signal
import sys
import time
from typing import Any

from kronika.application.in_process_lifecycle import (
    ShutdownDeadline,
    split_termination_budget,
)
from kronika.application.ports.youtube_downloader import (
    YouTubeDownloadError,
    YouTubeDownloaderConfigurationError,
    YouTubeDownloadPlan,
    YouTubeDownloadResult,
    YouTubeInspection,
    YouTubeInspectionError,
)
from kronika.application.ports.youtube_staging import (
    YouTubeStagingError,
    YouTubeStagingStorage,
)
from kronika.domain.youtube_acquisition import (
    MAX_REMOTE_FILENAME_CODE_POINTS,
    MAX_UPSTREAM_CHANNEL_CODE_POINTS,
    MAX_UPSTREAM_CHANNEL_ID_CODE_POINTS,
    MAX_UPSTREAM_TITLE_CODE_POINTS,
    YOUTUBE_DOWNLOADER_NAME,
    YOUTUBE_EXTRACTOR_KEY,
    FrameNestYouTubeAcquisitionError,
    YouTubeSourceIdentity,
    normalize_advisory_text,
)
from kronika.infrastructure.youtube.staging import ARTIFACT_FILENAME

DEFAULT_INSPECTION_TIMEOUT_SECONDS = 60.0
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 7_200.0
DEFAULT_SOCKET_TIMEOUT_SECONDS = 30
DEFAULT_RETRIES = 3
DEFAULT_FINAL_SIZE_LIMIT_BYTES = 1_073_741_824
DEFAULT_STAGING_SIZE_LIMIT_BYTES = 2_214_592_512
DEFAULT_FREE_SPACE_RESERVE_BYTES = 67_108_864
MAX_STDOUT_BYTES = 1_048_576
MAX_STDERR_BYTES = 65_536
TERMINATE_GRACE_SECONDS = 10.0
KILL_GRACE_SECONDS = 5.0
_MONITOR_INTERVAL_SECONDS = 0.25

_SUPPORTED_VIDEO_CODEC_PREFIXES = (
    "avc1",
    "h264",
    "hev1",
    "hvc1",
    "hevc",
    "mp4v",
    "av01",
    "vp09",
    "vp9",
)
_SUPPORTED_AUDIO_CODEC_PREFIXES = ("mp4a", "aac")
_REJECTED_LIVE_STATUSES = frozenset(
    {"is_live", "is_upcoming", "post_live", "was_live"}
)
_REJECTED_AVAILABILITY = frozenset(
    {
        "needs_auth",
        "premium_only",
        "subscriber_only",
        "private",
        "needs_subscription",
    }
)

ProcessFactory = Callable[..., Awaitable[Any]]


class YtDlpYouTubeDownloader:
    """Controlled subprocess adapter for one public YouTube video at a time."""

    def __init__(
        self,
        staging: YouTubeStagingStorage,
        *,
        max_final_size_bytes: int = DEFAULT_FINAL_SIZE_LIMIT_BYTES,
        max_staging_size_bytes: int = DEFAULT_STAGING_SIZE_LIMIT_BYTES,
        free_space_reserve_bytes: int = DEFAULT_FREE_SPACE_RESERVE_BYTES,
        inspection_timeout_seconds: float = DEFAULT_INSPECTION_TIMEOUT_SECONDS,
        download_timeout_seconds: float = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
        process_factory: ProcessFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._staging = staging
        self._max_final_size_bytes = _positive_int(max_final_size_bytes)
        self._max_staging_size_bytes = _positive_int(max_staging_size_bytes)
        self._free_space_reserve_bytes = _non_negative_int(
            free_space_reserve_bytes
        )
        self._inspection_timeout_seconds = _positive_float(
            inspection_timeout_seconds
        )
        self._download_timeout_seconds = _positive_float(
            download_timeout_seconds
        )
        self._process_factory = process_factory or asyncio.create_subprocess_exec
        self._clock = clock
        self._attested_version: str | None = None
        self._shutdown_deadline: ShutdownDeadline | None = None

    def bind_shutdown_deadline(self, deadline: ShutdownDeadline | None) -> None:
        """Use the shared application remaining budget for TERM/KILL."""
        self._shutdown_deadline = deadline

    async def attest_version(self) -> str:
        """Compare package metadata with the isolated module entry point."""
        try:
            metadata_version = package_version("yt-dlp")
        except PackageNotFoundError as exc:
            raise YouTubeDownloaderConfigurationError(
                "DOWNLOADER_UNAVAILABLE"
            ) from exc
        try:
            completed = await self._run_bounded(
                self._base_argv() + ("--version",),
                timeout_seconds=self._inspection_timeout_seconds,
                stdout_limit=128,
                stderr_limit=4_096,
                cwd=None,
            )
        except YouTubeDownloadError as exc:
            raise YouTubeDownloaderConfigurationError(
                "DOWNLOADER_ATTESTATION_TIMEOUT"
            ) from exc
        if completed.returncode != 0 or completed.stdout_overflow:
            raise YouTubeDownloaderConfigurationError(
                "DOWNLOADER_ATTESTATION_FAILED"
            )
        try:
            reported = completed.stdout.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise YouTubeDownloaderConfigurationError(
                "DOWNLOADER_ATTESTATION_FAILED"
            ) from exc
        if _numeric_version(metadata_version) != _numeric_version(reported):
            raise YouTubeDownloaderConfigurationError(
                "DOWNLOADER_VERSION_MISMATCH"
            )
        self._attested_version = metadata_version
        return metadata_version

    async def inspect(
        self,
        identity: YouTubeSourceIdentity,
    ) -> YouTubeInspection:
        if not isinstance(identity, YouTubeSourceIdentity):
            raise YouTubeInspectionError("INVALID_SOURCE_IDENTITY")
        version = await self._require_attested_version()
        argv = self._base_argv() + self._common_network_argv() + (
            "--dump-single-json",
            "--skip-download",
            "--no-write-info-json",
            "--",
            identity.canonical_url,
        )
        try:
            completed = await self._run_bounded(
                argv,
                timeout_seconds=self._inspection_timeout_seconds,
                stdout_limit=MAX_STDOUT_BYTES,
                stderr_limit=MAX_STDERR_BYTES,
                cwd=None,
            )
        except YouTubeDownloadError as exc:
            raise YouTubeInspectionError("INSPECTION_TIMEOUT") from exc
        if completed.returncode != 0:
            raise YouTubeInspectionError("INSPECTION_FAILED")
        if completed.stdout_overflow or completed.stderr_overflow:
            raise YouTubeInspectionError("INSPECTION_OUTPUT_LIMIT")
        try:
            raw = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise YouTubeInspectionError("INSPECTION_INVALID_OUTPUT") from exc
        if not isinstance(raw, dict):
            raise YouTubeInspectionError("INSPECTION_INVALID_OUTPUT")
        return self._inspection_from_payload(identity, raw, version)

    async def download(
        self,
        identity: YouTubeSourceIdentity,
        inspection: YouTubeInspection,
        *,
        staging_key: str,
    ) -> YouTubeDownloadResult:
        if (
            not isinstance(identity, YouTubeSourceIdentity)
            or not isinstance(inspection, YouTubeInspection)
            or inspection.video_id != identity.video_id
            or inspection.extractor_key != identity.extractor_key
        ):
            raise YouTubeDownloadError("INVALID_DOWNLOAD_PLAN")
        if inspection.plan.split_streams and shutil.which(
            "ffmpeg", path=os.defpath
        ) is None:
            raise YouTubeDownloaderConfigurationError("MERGER_UNAVAILABLE")
        try:
            claim_directory = self._staging.prepare(staging_key)
            self._enforce_staging_limits(staging_key)
        except YouTubeStagingError as exc:
            raise YouTubeDownloadError("STAGING_UNAVAILABLE") from exc

        format_selector = inspection.plan.video_format_id
        if inspection.plan.audio_format_id is not None:
            format_selector = (
                f"{format_selector}+{inspection.plan.audio_format_id}"
            )
        argv = self._base_argv() + self._common_network_argv() + (
            "--format",
            format_selector,
            "--output",
            ARTIFACT_FILENAME,
            "--merge-output-format",
            "mp4",
            "--continue",
            "--no-overwrites",
            "--no-mtime",
            "--restrict-filenames",
            "--no-write-subs",
            "--no-write-auto-subs",
            "--no-write-thumbnail",
            "--no-write-description",
            "--no-write-info-json",
            "--no-write-playlist-metafiles",
            "--no-write-comments",
            "--no-embed-metadata",
            "--no-embed-thumbnail",
            "--no-embed-subs",
            "--no-embed-chapters",
            "--",
            identity.canonical_url,
        )
        completed = await self._run_download(
            argv,
            staging_key=staging_key,
            claim_directory=claim_directory,
        )
        if completed.returncode != 0:
            raise YouTubeDownloadError("DOWNLOAD_FAILED")
        if completed.stdout_overflow or completed.stderr_overflow:
            raise YouTubeDownloadError("DOWNLOAD_OUTPUT_LIMIT")
        try:
            reader = self._staging.open_artifact(staging_key)
        except (FileNotFoundError, YouTubeStagingError) as exc:
            raise YouTubeDownloadError("DOWNLOAD_ARTIFACT_INVALID") from exc
        try:
            if reader.size_bytes > self._max_final_size_bytes:
                raise YouTubeDownloadError("FINAL_SIZE_LIMIT")
            reader.verify_still_consistent()
            return YouTubeDownloadResult(size_bytes=reader.size_bytes)
        finally:
            reader.close()

    def _inspection_from_payload(
        self,
        identity: YouTubeSourceIdentity,
        raw: Mapping[str, object],
        downloader_version: str,
    ) -> YouTubeInspection:
        if (
            raw.get("extractor_key") != YOUTUBE_EXTRACTOR_KEY
            or raw.get("id") != identity.video_id
            or raw.get("_type") not in {None, "video"}
            or raw.get("is_live") is True
            or raw.get("live_status") in _REJECTED_LIVE_STATUSES
            or raw.get("availability") in _REJECTED_AVAILABILITY
        ):
            raise YouTubeInspectionError("UNSUPPORTED_MEDIA")
        age_limit = raw.get("age_limit")
        if isinstance(age_limit, bool) or (
            isinstance(age_limit, int | float) and age_limit > 0
        ):
            raise YouTubeInspectionError("AUTHENTICATED_MEDIA_REQUIRED")
        duration = _finite_number(raw.get("duration"))
        if duration is None or duration <= 0 or duration > 21_600:
            raise YouTubeInspectionError("DURATION_LIMIT")
        formats = raw.get("formats")
        if not isinstance(formats, list):
            raise YouTubeInspectionError("NO_VISUAL_STREAM")
        plan = self._select_plan(formats)
        if (
            plan.expected_size_bytes is not None
            and plan.expected_size_bytes > self._max_final_size_bytes
        ):
            raise YouTubeInspectionError("FINAL_SIZE_LIMIT")
        source_date = _source_date(raw.get("upload_date"))
        return YouTubeInspection(
            video_id=identity.video_id,
            extractor_key=YOUTUBE_EXTRACTOR_KEY,
            title=_safe_advisory(
                raw.get("title"), maximum=MAX_UPSTREAM_TITLE_CODE_POINTS
            ),
            channel=_safe_advisory(
                raw.get("channel"), maximum=MAX_UPSTREAM_CHANNEL_CODE_POINTS
            ),
            channel_id=_safe_advisory(
                raw.get("channel_id"),
                maximum=MAX_UPSTREAM_CHANNEL_ID_CODE_POINTS,
            ),
            source_date=source_date,
            remote_filename=_safe_advisory(
                raw.get("_filename") or raw.get("filename"),
                maximum=MAX_REMOTE_FILENAME_CODE_POINTS,
            ),
            duration_seconds=duration,
            downloader_version=downloader_version,
            extractor_version=downloader_version,
            plan=plan,
        )

    def _select_plan(
        self,
        formats: list[object],
    ) -> YouTubeDownloadPlan:
        visual_only: list[Mapping[str, object]] = []
        audio_only: list[Mapping[str, object]] = []
        combined: list[Mapping[str, object]] = []
        source_has_audio = False
        for candidate in formats:
            if not isinstance(candidate, dict) or candidate.get("has_drm") is True:
                continue
            format_id = candidate.get("format_id")
            if not _safe_format_id(format_id):
                continue
            vcodec = candidate.get("vcodec")
            acodec = candidate.get("acodec")
            has_video = isinstance(vcodec, str) and vcodec != "none"
            has_audio = isinstance(acodec, str) and acodec != "none"
            source_has_audio = source_has_audio or has_audio
            video_ext = candidate.get("video_ext") or candidate.get("ext")
            audio_ext = candidate.get("audio_ext") or candidate.get("ext")
            video_supported = (
                has_video
                and video_ext == "mp4"
                and vcodec.lower().startswith(_SUPPORTED_VIDEO_CODEC_PREFIXES)
            )
            audio_supported = (
                has_audio
                and audio_ext in {"m4a", "mp4"}
                and acodec.lower().startswith(_SUPPORTED_AUDIO_CODEC_PREFIXES)
            )
            if video_supported and not has_audio:
                visual_only.append(candidate)
            elif audio_supported and not has_video:
                audio_only.append(candidate)
            elif video_supported and audio_supported:
                combined.append(candidate)

        visual_only.sort(key=_format_quality_key, reverse=True)
        audio_only.sort(key=_audio_quality_key, reverse=True)
        combined.sort(key=_format_quality_key, reverse=True)

        if not visual_only and not combined:
            raise YouTubeInspectionError("NO_VISUAL_STREAM")
        if source_has_audio and visual_only and audio_only:
            for video in visual_only:
                for audio in audio_only:
                    expected = _sum_estimates(video, audio)
                    if (
                        expected is None
                        or expected <= self._max_final_size_bytes
                    ):
                        return YouTubeDownloadPlan(
                            video_format_id=str(video["format_id"]),
                            audio_format_id=str(audio["format_id"]),
                            expected_size_bytes=expected,
                            has_source_audio=True,
                            split_streams=True,
                        )
        if source_has_audio:
            for selected in combined:
                expected = _format_size(selected)
                if (
                    expected is None
                    or expected <= self._max_final_size_bytes
                ):
                    return YouTubeDownloadPlan(
                        video_format_id=str(selected["format_id"]),
                        audio_format_id=None,
                        expected_size_bytes=expected,
                        has_source_audio=True,
                        split_streams=False,
                    )
            raise YouTubeInspectionError("COMPATIBLE_FORMAT_UNAVAILABLE")
        for selected in visual_only:
            expected = _format_size(selected)
            if expected is None or expected <= self._max_final_size_bytes:
                return YouTubeDownloadPlan(
                    video_format_id=str(selected["format_id"]),
                    audio_format_id=None,
                    expected_size_bytes=expected,
                    has_source_audio=False,
                    split_streams=False,
                )
        raise YouTubeInspectionError("NO_VISUAL_STREAM")

    async def _require_attested_version(self) -> str:
        if self._attested_version is None:
            return await self.attest_version()
        return self._attested_version

    async def _run_download(
        self,
        argv: tuple[str, ...],
        *,
        staging_key: str,
        claim_directory: Path,
    ) -> "_CompletedProcess":
        process = await self._spawn(argv, cwd=claim_directory)
        stdout_task = asyncio.create_task(
            _capture_stream(process.stdout, MAX_STDOUT_BYTES)
        )
        stderr_task = asyncio.create_task(
            _capture_stream(process.stderr, MAX_STDERR_BYTES)
        )
        started = self._clock()
        failure_code: str | None = None
        try:
            while True:
                elapsed = self._clock() - started
                if elapsed >= self._download_timeout_seconds:
                    failure_code = "DOWNLOAD_TIMEOUT"
                    break
                try:
                    returncode = await asyncio.wait_for(
                        process.wait(),
                        timeout=min(
                            _MONITOR_INTERVAL_SECONDS,
                            self._download_timeout_seconds - elapsed,
                        ),
                    )
                    break
                except TimeoutError:
                    try:
                        self._enforce_staging_limits(staging_key)
                    except YouTubeDownloadError as exc:
                        failure_code = exc.code
                        break
        except BaseException:
            try:
                await self._terminate_process_group(process)
            except Exception:
                pass
            await _finish_capture_tasks(stdout_task, stderr_task)
            raise
        if failure_code is not None:
            await self._terminate_process_group(process)
            await _finish_capture_tasks(stdout_task, stderr_task)
            raise YouTubeDownloadError(failure_code)
        stdout, stdout_overflow = await stdout_task
        stderr, stderr_overflow = await stderr_task
        return _CompletedProcess(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            stdout_overflow=stdout_overflow,
            stderr_overflow=stderr_overflow,
        )

    async def _run_bounded(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        stdout_limit: int,
        stderr_limit: int,
        cwd: Path | None,
    ) -> "_CompletedProcess":
        process = await self._spawn(argv, cwd=cwd)
        stdout_task = asyncio.create_task(
            _capture_stream(process.stdout, stdout_limit)
        )
        stderr_task = asyncio.create_task(
            _capture_stream(process.stderr, stderr_limit)
        )
        try:
            returncode = await asyncio.wait_for(
                process.wait(),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            try:
                await self._terminate_process_group(process)
            except Exception:
                pass
            await _finish_capture_tasks(stdout_task, stderr_task)
            raise YouTubeDownloadError("SUBPROCESS_TIMEOUT") from exc
        except BaseException:
            try:
                await self._terminate_process_group(process)
            except Exception:
                pass
            await _finish_capture_tasks(stdout_task, stderr_task)
            raise
        stdout, stdout_overflow = await stdout_task
        stderr, stderr_overflow = await stderr_task
        return _CompletedProcess(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            stdout_overflow=stdout_overflow,
            stderr_overflow=stderr_overflow,
        )

    async def _spawn(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None,
    ) -> Any:
        try:
            return await self._process_factory(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=None if cwd is None else str(cwd),
                env=_subprocess_environment(),
                start_new_session=True,
            )
        except OSError as exc:
            raise YouTubeDownloaderConfigurationError(
                "DOWNLOADER_UNAVAILABLE"
            ) from exc

    async def _terminate_process_group(self, process: Any) -> None:
        term_timeout = TERMINATE_GRACE_SECONDS
        kill_timeout = KILL_GRACE_SECONDS
        deadline = self._shutdown_deadline
        if deadline is not None:
            term_timeout, kill_timeout = split_termination_budget(
                deadline.remaining_seconds()
            )
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            try:
                await asyncio.wait_for(process.wait(), timeout=max(kill_timeout, 0.0))
            except TimeoutError:
                return
            return
        except OSError as exc:
            raise YouTubeDownloadError("SUBPROCESS_TERMINATION_FAILED") from exc
        if term_timeout > 0:
            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=term_timeout,
                )
                return
            except TimeoutError:
                pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            try:
                await asyncio.wait_for(process.wait(), timeout=max(kill_timeout, 0.0))
            except TimeoutError:
                return
            return
        except OSError as exc:
            raise YouTubeDownloadError("SUBPROCESS_TERMINATION_FAILED") from exc
        try:
            await asyncio.wait_for(process.wait(), timeout=max(kill_timeout, 0.0))
        except TimeoutError as exc:
            raise YouTubeDownloadError("SUBPROCESS_TERMINATION_FAILED") from exc

    def _enforce_staging_limits(self, staging_key: str) -> None:
        try:
            if self._staging.usage_bytes(staging_key) > self._max_staging_size_bytes:
                raise YouTubeDownloadError("STAGING_SIZE_LIMIT")
            if (
                self._staging.available_bytes()
                < self._free_space_reserve_bytes
            ):
                raise YouTubeDownloadError("STAGING_SPACE_RESERVE")
        except YouTubeDownloadError:
            raise
        except YouTubeStagingError as exc:
            raise YouTubeDownloadError("STAGING_UNAVAILABLE") from exc

    @staticmethod
    def _base_argv() -> tuple[str, ...]:
        return (sys.executable, "-I", "-m", "yt_dlp")

    @staticmethod
    def _common_network_argv() -> tuple[str, ...]:
        return (
            "--ignore-config",
            "--no-playlist",
            "--no-warnings",
            "--quiet",
            "--no-cache-dir",
            "--socket-timeout",
            str(DEFAULT_SOCKET_TIMEOUT_SECONDS),
            "--retries",
            str(DEFAULT_RETRIES),
            "--fragment-retries",
            str(DEFAULT_RETRIES),
        )


@dataclass(frozen=True, slots=True)
class _CompletedProcess:
    returncode: int
    stdout: bytes
    stderr: bytes
    stdout_overflow: bool
    stderr_overflow: bool


async def _capture_stream(
    stream: Any,
    limit: int,
) -> tuple[bytes, bool]:
    captured = bytearray()
    overflow = False
    while True:
        chunk = await stream.read(65_536)
        if not chunk:
            break
        remaining = limit - len(captured)
        if remaining > 0:
            captured.extend(chunk[:remaining])
        if len(chunk) > remaining:
            overflow = True
    return bytes(captured), overflow


async def _finish_capture_tasks(*tasks: asyncio.Task[object]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _subprocess_environment() -> dict[str, str]:
    return {
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }


def _numeric_version(value: object) -> tuple[int, ...]:
    if not isinstance(value, str):
        raise YouTubeDownloaderConfigurationError(
            "DOWNLOADER_ATTESTATION_FAILED"
        )
    try:
        parts = tuple(int(part) for part in value.split("."))
    except ValueError as exc:
        raise YouTubeDownloaderConfigurationError(
            "DOWNLOADER_ATTESTATION_FAILED"
        ) from exc
    if len(parts) != 3 or any(part < 0 for part in parts):
        raise YouTubeDownloaderConfigurationError(
            "DOWNLOADER_ATTESTATION_FAILED"
        )
    return parts


def _safe_advisory(value: object, *, maximum: int) -> str | None:
    try:
        return normalize_advisory_text(value, maximum=maximum)
    except FrameNestYouTubeAcquisitionError:
        return None


def _source_date(value: object) -> str | None:
    if not isinstance(value, str) or len(value) != 8 or not value.isascii():
        return None
    if not value.isdigit():
        return None
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def _safe_format_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 120
        and value[0].isalnum()
        and value.isascii()
        and all(character.isalnum() or character in "._+-" for character in value)
    )


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _format_size(candidate: Mapping[str, object]) -> int | None:
    for key in ("filesize", "filesize_approx"):
        value = candidate.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float) and math.isfinite(float(value)):
            integer = int(value)
            if integer > 0:
                return integer
    return None


def _sum_estimates(
    first: Mapping[str, object],
    second: Mapping[str, object],
) -> int | None:
    first_size = _format_size(first)
    second_size = _format_size(second)
    if first_size is None or second_size is None:
        return None
    return first_size + second_size


def _format_quality_key(
    candidate: Mapping[str, object],
) -> tuple[float, float, float, float]:
    return (
        _finite_number(candidate.get("height")) or 0.0,
        _finite_number(candidate.get("width")) or 0.0,
        _finite_number(candidate.get("fps")) or 0.0,
        _finite_number(candidate.get("tbr")) or 0.0,
    )


def _audio_quality_key(
    candidate: Mapping[str, object],
) -> tuple[float, float]:
    return (
        _finite_number(candidate.get("abr")) or 0.0,
        _finite_number(candidate.get("asr")) or 0.0,
    )


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("positive integer required")
    return value


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("non-negative integer required")
    return value


def _positive_float(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or value <= 0
    ):
        raise ValueError("positive finite number required")
    return float(value)
