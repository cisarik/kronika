"""Deterministic fake X extractor for application, integration and browser tests.

Never contacts X. Emits synthetic normalized metadata and writes
repository-owned fixture bytes into claim-owned staging.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from kronika.application.ports.x_extractor import (
    XAssetAcquisition,
    XExtractionError,
)
from kronika.domain.x_acquisition import (
    XMediaType,
    XNormalizedAssetDescriptor,
    XNormalizedInspection,
)


class FakeXExtractor:
    """Deterministic scripted X extractor used only in tests."""

    def __init__(self, fixture_directory: Path) -> None:
        self._fixture_directory = fixture_directory
        self.inspect_script: list[tuple[object, object]] = []
        self.download_bytes: dict[int, bytes] = {}
        self.posted_at_ms: int | None = 1_700_000_000_000
        self.author_stable_id = "user_123"
        self.author_handle = "author"
        self.author_display_name = "Author Name"
        self.post_text = "A funny clip"
        self.assets: list[XNormalizedAssetDescriptor] = []

    def attest_version(self) -> str | None:
        return "fake-1.0"

    def inspect(self, *, post_id: str, submitted_url: str):
        if self.inspect_script:
            behavior, value = self.inspect_script.pop(0)
            if behavior == "error":
                raise XExtractionError(value, value)
            if behavior == "inspection":
                return value
        assets = self.assets or [
            XNormalizedAssetDescriptor(
                ordinal=0,
                media_type=XMediaType.VIDEO,
                expected_mime="video/mp4",
                source_media_key="asset-0",
                width=640,
                height=360,
                duration_seconds=12,
                selected_variant="x-video-default-mp4-v1",
            )
        ]
        return XNormalizedInspection(
            post_id=post_id,
            canonical_url=None,
            post_text=self.post_text,
            posted_at_ms=self.posted_at_ms,
            author_stable_id=self.author_stable_id,
            author_handle=self.author_handle,
            author_display_name=self.author_display_name,
            assets=tuple(assets),
            extractor_version="fake-1.0",
        )

    def download(
        self,
        *,
        post_id: str,
        ordinal: int,
        media_type: str,
        expected_mime: str,
        source_media_key: str | None,
        selected_variant: str | None = None,
        stage_key: str,
        submitted_url: str,
        staging: object,
    ) -> XAssetAcquisition:
        payload = self.download_bytes.get(ordinal)
        if payload is None:
            payload = self._default_fixture(media_type)
        directory = staging.prepare(stage_key)
        # X uses one fixed FrameNest artifact name regardless of media type;
        # MIME sniffing determines the actual type at handoff.
        artifact = Path(directory) / "artifact.bin"
        artifact.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        return XAssetAcquisition(size_bytes=len(payload), sha256=digest)

    def _default_fixture(self, media_type: str) -> bytes:
        if media_type == "image":
            farne = self._fixture_directory / "lal.jpg"
            if farne.exists():
                return farne.read_bytes()
            return b"\xff\xd8\xff\xe0fake-jpeg-bytes"
        for name in ("lal.mp4", "small.mp4", "artifact.mp4"):
            candidate = self._fixture_directory / name
            if candidate.exists():
                return candidate.read_bytes()
        return b"\x00\x00\x00\x18ftypmp42fake-video-bytes"


class ScriptedXExtractor(FakeXExtractor):
    """X extractor whose inspect responses and failures are fully scripted."""

    def __init__(self, fixture_directory: Path) -> None:
        super().__init__(fixture_directory)
        self.invalid_extra_assets: tuple[XNormalizedAssetDescriptor, ...] = ()

    def set_inspection(self, inspection: XNormalizedInspection) -> None:
        self.inspect_script.append(("inspection", inspection))

    def set_error(self, code: str) -> None:
        self.inspect_script.append(("error", code))
