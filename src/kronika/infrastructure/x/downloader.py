"""X-specific yt-dlp adapter with a bounded, normalized extractor contract.

The adapter never exposes raw yt-dlp structures. Inspection and public-photo
download go through the isolated status-bridge module
(``sys.executable -I -m kronika.infrastructure.x.status_bridge``), which
calls pinned ``TwitterIE._extract_status`` (``yt-dlp==2026.7.4``, runtime
``2026.07.04``). Video and animated-GIF-as-MP4 download still uses a
cookie-free, ``--ignore-config`` yt-dlp subprocess into the claim-owned
``artifact.bin`` staging name after reinspecting and matching
``source_media_key``.

The configured commands never use cookies, ``.netrc``, browser-cookie
extraction, arbitrary plugin/config discovery, or requester-supplied shell
interpolation. Photo CDN URLs never appear on argv or in persisted inspect
JSON. Tests inject a synthetic ``extract_status`` seam and never contact X.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Callable

from kronika.application.in_process_lifecycle import (
    ShutdownDeadline,
    split_termination_budget,
)
from kronika.application.ports.x_extractor import (
    XAssetAcquisition,
    XExtractionError,
    XExtractionInterrupted,
    XRequiresAuthenticationError,
    XStagingStorage,
)
from kronika.domain.x_acquisition import (
    MAX_ASSETS_PER_POST,
    XMediaType,
    XNormalizedAssetDescriptor,
    XNormalizedInspection,
    accept_x_post_url,
)
from kronika.infrastructure.x import status_bridge
from kronika.infrastructure.x.staging import ARTIFACT_FILENAME
from kronika.infrastructure.x.status_bridge import (
    StatusBridgeError,
)

DEFAULT_INSPECT_TIMEOUT_SECONDS = 60
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 600
DEFAULT_SOCKET_TIMEOUT_SECONDS = 30
MAX_STDOUT_BYTES = 4_194_304
TERMINATE_GRACE_SECONDS = 5
KILL_GRACE_SECONDS = 2


class YtDlpXExtractor:
    """Run yt-dlp under an argument-only, config-free, bounded subprocess."""

    def __init__(
        self,
        staging: XStagingStorage | None = None,
        *,
        executable: str = "yt-dlp",
        inspect_timeout_seconds: float = DEFAULT_INSPECT_TIMEOUT_SECONDS,
        download_timeout_seconds: float = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
        socket_timeout_seconds: float = DEFAULT_SOCKET_TIMEOUT_SECONDS,
        working_directory: Path | None = None,
        max_assets: int = MAX_ASSETS_PER_POST,
        extract_status: Callable[[str], object] | None = None,
        photo_transport: status_bridge.PhotoTransport | None = None,
        photo_resolver: status_bridge.PhotoResolver | None = None,
        bridge_executable: str | None = None,
    ) -> None:
        self._staging = staging
        self._executable = executable
        self._inspect_timeout_seconds = inspect_timeout_seconds
        self._download_timeout_seconds = download_timeout_seconds
        self._socket_timeout_seconds = socket_timeout_seconds
        self._working_directory = working_directory
        self._max_assets = max_assets
        self._extract_status = extract_status
        self._photo_transport = photo_transport
        self._photo_resolver = photo_resolver
        self._bridge_executable = bridge_executable or sys.executable
        self._guard = threading.Lock()
        self._interrupt = threading.Event()
        self._active_process: subprocess.Popen[bytes] | None = None
        self._shutdown_deadline: ShutdownDeadline | None = None

    def bind_shutdown_deadline(self, deadline: ShutdownDeadline | None) -> None:
        self._shutdown_deadline = deadline

    def request_interrupt(self) -> None:
        """Idempotently interrupt the currently owned process group."""
        self._interrupt.set()
        with self._guard:
            process = self._active_process
        if process is None:
            return
        _terminate_process_group(
            process,
            remaining_seconds=_remaining_or_default(self._shutdown_deadline),
            reap=False,
        )

    def attest_version(self) -> str | None:
        try:
            completed = subprocess.run(
                [self._executable, "--version"],
                capture_output=True,
                timeout=self._inspect_timeout_seconds,
                env=_subprocess_environment(),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        first = completed.stdout.splitlines()[0] if completed.stdout else b""
        return first.decode("utf-8", "replace").strip()[:64] or None

    def inspect(
        self,
        *,
        post_id: str,
        submitted_url: str,
    ) -> XNormalizedInspection:
        identity = accept_x_post_url(submitted_url)
        if identity.post_id != post_id:
            raise XExtractionError("X_URL_INVALID_POST_ID", "Invalid X post identity.")
        try:
            if self._extract_status is not None:
                inspection = status_bridge.inspect_post(
                    post_id,
                    submitted_url,
                    extract_status=self._extract_status,
                )
            else:
                inspection = self._inspect_via_bridge_subprocess(post_id, submitted_url)
        except StatusBridgeError as exc:
            raise _x_error_from_bridge(exc) from exc
        if len(inspection.assets) > self._max_assets:
            raise XExtractionError("X_TOO_MANY_ASSETS", "X post exceeds asset limit.")
        if not inspection.assets:
            raise XExtractionError("X_NO_SUPPORTED_MEDIA", "X post has no media.")
        return inspection

    def download(
        self,
        *,
        post_id: str,
        ordinal: int,
        media_type: str,
        expected_mime: str,
        source_media_key: str | None,
        selected_variant: str | None,
        stage_key: str,
        submitted_url: str,
        staging: XStagingStorage,
    ) -> XAssetAcquisition:
        if self._staging is not None:
            staging = self._staging
        identity = accept_x_post_url(submitted_url)
        if identity.post_id != post_id:
            raise XExtractionError("X_URL_INVALID_POST_ID", "Invalid X post identity.")
        if not selected_variant or not source_media_key:
            raise XExtractionError(
                "X_SOURCE_MEDIA_CHANGED", "X source media is no longer available."
            )
        matched = self._reinspect_match(
            post_id=post_id,
            submitted_url=submitted_url,
            source_media_key=source_media_key,
            selected_variant=selected_variant,
            media_type=media_type,
        )
        directory = staging.prepare(stage_key)
        destination = Path(directory) / ARTIFACT_FILENAME
        try:
            if matched.media_type is XMediaType.IMAGE:
                self._download_photo(
                    post_id=post_id,
                    submitted_url=submitted_url,
                    source_media_key=source_media_key,
                    selected_variant=selected_variant,
                    destination=destination,
                )
            else:
                self._download_video(
                    identity=identity,
                    matched=matched,
                    source_media_key=source_media_key,
                    directory=Path(directory),
                    destination=destination,
                )
        except StatusBridgeError as exc:
            _delete_path(destination)
            raise _x_error_from_bridge(exc) from exc
        except XExtractionError:
            _delete_path(destination)
            raise
        artifact = destination if destination.is_file() else _find_artifact(directory)
        if artifact is None:
            raise XExtractionError("X_STAGING_FAILED", "X produced no artifact.")
        size_bytes = os.path.getsize(artifact)
        if size_bytes <= 0:
            _delete_path(artifact)
            raise XExtractionError("X_MEDIA_TYPE_UNSUPPORTED", "X artifact is empty.")
        digest = hashlib.sha256()
        with open(artifact, "rb") as handle:
            while True:
                block = handle.read(1_048_576)
                if not block:
                    break
                digest.update(block)
        return XAssetAcquisition(size_bytes=size_bytes, sha256=digest.hexdigest())

    def inspect_argv(self, post_id: str) -> list[str]:
        return [
            self._bridge_executable,
            "-I",
            "-m",
            "kronika.infrastructure.x.status_bridge",
            "inspect",
            post_id,
        ]

    def _inspect_via_bridge_subprocess(
        self, post_id: str, submitted_url: str
    ) -> XNormalizedInspection:
        completed = self._run_bounded(
            self.inspect_argv(post_id),
            timeout=self._inspect_timeout_seconds,
        )
        if completed.timed_out:
            raise XExtractionError("X_DOWNLOAD_TIMEOUT", "X extraction timed out.")
        try:
            payload = json.loads(completed.stdout or b"{}")
        except (ValueError, TypeError) as exc:
            raise XExtractionError(
                "X_EXTRACTOR_MALFORMED", "X extractor returned malformed JSON."
            ) from exc
        if completed.returncode != 0:
            if isinstance(payload, dict) and isinstance(payload.get("error_code"), str):
                raise XExtractionError(payload["error_code"], "X extraction failed.")
            raise _extraction_from_exit(completed.returncode)
        try:
            return status_bridge.inspection_from_payload(
                payload, post_id=post_id, submitted_url=submitted_url
            )
        except StatusBridgeError as exc:
            raise _x_error_from_bridge(exc) from exc

    def _reinspect_match(
        self,
        *,
        post_id: str,
        submitted_url: str,
        source_media_key: str,
        selected_variant: str,
        media_type: str,
    ) -> XNormalizedAssetDescriptor:
        inspection = self.inspect(post_id=post_id, submitted_url=submitted_url)
        matched = None
        for asset in inspection.assets:
            if asset.source_media_key == source_media_key:
                matched = asset
                break
        if (
            matched is None
            or matched.media_type.value != media_type
            or matched.selected_variant != selected_variant
        ):
            raise XExtractionError(
                "X_SOURCE_MEDIA_CHANGED", "X source media is no longer available."
            )
        return matched

    def _download_photo(
        self,
        *,
        post_id: str,
        submitted_url: str,
        source_media_key: str,
        selected_variant: str,
        destination: Path,
    ) -> None:
        if self._extract_status is not None:
            status_bridge.download_photo(
                post_id,
                submitted_url,
                source_media_key=source_media_key,
                selected_variant=selected_variant,
                destination=destination,
                extract_status=self._extract_status,
                resolver=self._photo_resolver,
                transport=self._photo_transport,
            )
            return
        argv = [
            self._bridge_executable,
            "-I",
            "-m",
            "kronika.infrastructure.x.status_bridge",
            "download-photo",
            post_id,
            source_media_key,
            selected_variant,
            str(destination),
        ]
        completed = self._run_bounded(argv, timeout=self._download_timeout_seconds)
        if completed.timed_out:
            raise XExtractionError("X_DOWNLOAD_TIMEOUT", "X download timed out.")
        if completed.returncode != 0:
            try:
                payload = json.loads(completed.stdout or b"{}")
            except (ValueError, TypeError):
                payload = {}
            if isinstance(payload, dict) and isinstance(payload.get("error_code"), str):
                raise XExtractionError(payload["error_code"], "X extraction failed.")
            raise _extraction_from_exit(completed.returncode)

    def _download_video(
        self,
        *,
        identity: object,
        matched: XNormalizedAssetDescriptor,
        source_media_key: str,
        directory: Path,
        destination: Path,
    ) -> None:
        if matched.provider_download_index is None:
            raise XExtractionError(
                "X_SOURCE_MEDIA_CHANGED", "X source media is no longer available."
            )
        argv = [
            self._executable,
            "--ignore-config",
            "--no-warnings",
            "--no-progress",
            "--no-overwrites",
            "--print",
            "after_move:%(id)s",
            "--output",
            ARTIFACT_FILENAME,
            "--playlist-items",
            str(int(matched.provider_download_index) + 1),
            "--socket-timeout",
            str(int(self._socket_timeout_seconds)),
            "--",
            identity.canonical_url,
        ]
        completed = self._run_bounded(
            argv,
            timeout=self._download_timeout_seconds,
            cwd=directory,
        )
        if completed.timed_out:
            raise XExtractionError("X_DOWNLOAD_TIMEOUT", "X download timed out.")
        if completed.returncode != 0:
            raise _extraction_from_exit(completed.returncode)
        printed = (completed.stdout or b"").decode("utf-8", "replace").strip().splitlines()
        reported_id = printed[-1].strip() if printed else ""
        if reported_id != source_media_key:
            _delete_path(destination)
            raise XExtractionError(
                "X_SOURCE_MEDIA_CHANGED", "X source media is no longer available."
            )

    def _run_bounded(
        self,
        argv: list[str],
        *,
        timeout: float,
        cwd: Path | None = None,
    ) -> _BoundedCompleted:
        if self._interrupt.is_set():
            raise XExtractionInterrupted()
        try:
            process = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(cwd) if cwd is not None else None,
                env=_subprocess_environment(),
                start_new_session=True,
            )
        except OSError as exc:
            raise XExtractionError("X_EXTRACTOR_UNAVAILABLE", "X extractor is unavailable.") from exc
        with self._guard:
            self._active_process = process
        timed_out = False
        stdout = b""
        stderr = b""
        try:
            if self._interrupt.is_set():
                self._reap_interrupted(process)
                raise XExtractionInterrupted()
            remaining = timeout
            deadline = time.monotonic() + timeout
            while True:
                slice_timeout = min(0.05, max(0.0, remaining))
                try:
                    stdout, stderr = process.communicate(timeout=slice_timeout)
                    break
                except subprocess.TimeoutExpired:
                    if self._interrupt.is_set():
                        self._reap_interrupted(process)
                        raise XExtractionInterrupted() from None
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        _terminate_process_group(
                            process,
                            remaining_seconds=TERMINATE_GRACE_SECONDS + KILL_GRACE_SECONDS,
                            reap=False,
                        )
                        try:
                            stdout, stderr = process.communicate(
                                timeout=TERMINATE_GRACE_SECONDS
                            )
                        except subprocess.TimeoutExpired:
                            process.kill()
                            stdout, stderr = process.communicate()
                        break
            if self._interrupt.is_set():
                raise XExtractionInterrupted()
        finally:
            with self._guard:
                if self._active_process is process:
                    self._active_process = None
            if process.poll() is None:
                try:
                    process.wait(timeout=0.05)
                except subprocess.TimeoutExpired:
                    pass
        if _out_overflow(stdout):
            raise XExtractionError("X_EXTRACTOR_FAILED", "X extractor output overflowed.")
        return _BoundedCompleted(
            stdout=stdout or b"",
            stderr=stderr or b"",
            returncode=process.returncode,
            timed_out=timed_out,
        )

    def _reap_interrupted(self, process: subprocess.Popen[bytes]) -> None:
        _terminate_process_group(
            process,
            remaining_seconds=_remaining_or_default(self._shutdown_deadline),
            reap=False,
        )
        try:
            process.communicate(
                timeout=max(_remaining_or_default(self._shutdown_deadline), 0.05)
            )
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


def _x_error_from_bridge(exc: StatusBridgeError) -> XExtractionError:
    if exc.code == "X_AUTHENTICATION_REQUIRED":
        return XRequiresAuthenticationError(exc.code, "X post is not publicly available.")
    return XExtractionError(exc.code, "X extraction failed.")


def _delete_path(path: Path) -> None:
    try:
        if path.exists() or path.is_symlink():
            path.unlink()
    except OSError:
        return


def _extraction_from_exit(returncode: int) -> XExtractionError:
    if returncode == -9:
        return XExtractionError("X_DOWNLOAD_TIMEOUT", "X download killed.")
    if returncode in {1, 2}:
        return XExtractionError("X_EXTRACTOR_FAILED", "X extraction failed.")
    return XExtractionError("X_EXTRACTOR_FAILED", "X extraction failed.")


def _subprocess_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "NO_COLOR": "1",
    }


class _BoundedCompleted:
    __slots__ = ("stdout", "stderr", "returncode", "timed_out")

    def __init__(self, stdout: bytes, stderr: bytes, returncode: int, timed_out: bool):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.timed_out = timed_out


def _remaining_or_default(deadline: ShutdownDeadline | None) -> float:
    if deadline is None:
        return TERMINATE_GRACE_SECONDS + KILL_GRACE_SECONDS
    return deadline.remaining_seconds()


def _out_overflow(stdout: bytes | None) -> bool:
    return stdout is not None and len(stdout) > MAX_STDOUT_BYTES


def _terminate_process_group(
    process: subprocess.Popen[bytes],
    *,
    remaining_seconds: float,
    reap: bool,
) -> None:
    term_seconds, kill_seconds = split_termination_budget(remaining_seconds)
    if remaining_seconds <= 0:
        term_seconds, kill_seconds = 0.0, 0.0
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        try:
            process.terminate()
        except OSError:
            pass
    if reap and term_seconds > 0:
        try:
            process.wait(timeout=term_seconds)
            return
        except subprocess.TimeoutExpired:
            pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        try:
            process.kill()
        except OSError:
            pass
    if reap:
        try:
            process.wait(timeout=max(kill_seconds, 0.05))
        except subprocess.TimeoutExpired:
            pass


def _find_artifact(directory: Path) -> Path | None:
    candidate = Path(directory) / ARTIFACT_FILENAME
    if candidate.is_file():
        return candidate
    return None