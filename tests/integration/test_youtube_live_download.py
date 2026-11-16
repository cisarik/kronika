"""Opt-in live cookie-free YouTube acquisition smoke test.

This test contacts YouTube for one exact public video and is excluded from the
default suite by the environment gate. It uses only the downloader adapter and
a private temporary staging root: no database, catalog, acquisition claim,
server API, cookie jar, browser profile or durable application state.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from kronika.domain.youtube_acquisition import canonicalize_youtube_url
from kronika.infrastructure.youtube.downloader import YtDlpYouTubeDownloader
from kronika.infrastructure.youtube.staging import FilesystemYouTubeStaging

pytestmark = pytest.mark.skipif(
    os.environ.get("KRONIKA_RUN_YOUTUBE_LIVE_DOWNLOAD") != "1",
    reason=(
        "Set KRONIKA_RUN_YOUTUBE_LIVE_DOWNLOAD=1 to run the live "
        "cookie-free YouTube download smoke test."
    ),
)

LIVE_URL = "https://www.youtube.com/watch?v=0fC1uf1vYuA"
STAGING_KEY = "7" * 32


def test_live_cookie_free_download_produces_a_policy_compliant_artifact(
    tmp_path: Path,
) -> None:
    root = tmp_path / "youtube"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    staging = FilesystemYouTubeStaging(root)
    downloader = YtDlpYouTubeDownloader(staging)
    identity = canonicalize_youtube_url(LIVE_URL)

    async def scenario() -> None:
        inspection = await downloader.inspect(identity)

        assert inspection.video_id == identity.video_id
        assert inspection.extractor_key == identity.extractor_key
        assert inspection.plan.video_format_id
        if inspection.plan.split_streams:
            assert inspection.plan.audio_format_id

        result = await downloader.download(
            identity,
            inspection,
            staging_key=STAGING_KEY,
        )

        assert result.size_bytes > 0
        reader = staging.open_artifact(
            STAGING_KEY,
            expected_size_bytes=result.size_bytes,
        )
        try:
            assert reader.size_bytes == result.size_bytes
            reader.verify_still_consistent()
        finally:
            reader.close()

    try:
        asyncio.run(scenario())
    finally:
        staging.cleanup(STAGING_KEY)
