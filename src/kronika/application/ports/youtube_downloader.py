"""Application contracts for isolated YouTube inspection and download."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kronika.domain.youtube_acquisition import YouTubeSourceIdentity


class YouTubeDownloaderError(RuntimeError):
    """Sanitized failure with a fixed operator-safe code."""

    def __init__(self, code: str) -> None:
        super().__init__("YouTube downloader operation failed.")
        self.code = code


class YouTubeDownloaderConfigurationError(YouTubeDownloaderError):
    """Required downloader or merger capability is unavailable."""


class YouTubeInspectionError(YouTubeDownloaderError):
    """Inspection failed or returned media outside policy."""


class YouTubeDownloadError(YouTubeDownloaderError):
    """Download failed, timed out, or exceeded a bounded limit."""


@dataclass(frozen=True, slots=True)
class YouTubeDownloadPlan:
    """Exact non-transcoding format selection derived from inspection."""

    video_format_id: str
    audio_format_id: str | None
    expected_size_bytes: int | None
    has_source_audio: bool
    split_streams: bool


@dataclass(frozen=True, slots=True)
class YouTubeInspection:
    """Bounded upstream snapshot and selected exact download formats."""

    video_id: str
    extractor_key: str
    title: str | None
    channel: str | None
    channel_id: str | None
    source_date: str | None
    remote_filename: str | None
    duration_seconds: float
    downloader_version: str
    extractor_version: str
    plan: YouTubeDownloadPlan


@dataclass(frozen=True, slots=True)
class YouTubeDownloadResult:
    """Stable downloaded artifact evidence, excluding raw provider output."""

    size_bytes: int


class YouTubeDownloader(Protocol):
    """Server-owned cookie-free source adapter."""

    async def attest_version(self) -> str:
        """Return the installed, subprocess-attested exact downloader version."""

    async def inspect(
        self,
        identity: YouTubeSourceIdentity,
    ) -> YouTubeInspection:
        """Inspect and select one supported public non-live video."""

    async def download(
        self,
        identity: YouTubeSourceIdentity,
        inspection: YouTubeInspection,
        *,
        staging_key: str,
    ) -> YouTubeDownloadResult:
        """Download or resume one selected artifact into claim-owned staging."""
