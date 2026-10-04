"""Exclusive derived-artifact cleanup for catalog-removal receipts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from kronika.application.catalog_removal import (
    CatalogRemovalInfrastructureError,
    CleanupState,
    DerivedArtifactCleanup,
)
from kronika.application.gallery_preview import GALLERY_PREVIEW_ALGORITHM_VERSION
from kronika.domain.identities import MediaId
from kronika.infrastructure.filesystem.cover_storage import (
    FilesystemCoverThumbnailCache,
    FilesystemDurableCoverStorage,
)
from kronika.infrastructure.media_analysis.gallery_preview import (
    FilesystemGalleryPreviewCache,
)


class FilesystemDerivedArtifactCleanup:
    """Best-effort exclusive cleanup for covers and gallery previews."""

    def __init__(
        self,
        *,
        cover_storage: FilesystemDurableCoverStorage | None,
        thumbnail_cache: FilesystemCoverThumbnailCache | None,
        preview_cache: FilesystemGalleryPreviewCache | None,
    ) -> None:
        self._cover_storage = cover_storage
        self._thumbnail_cache = thumbnail_cache
        self._preview_cache = preview_cache

    def cleanup_cover(
        self, *, media_id: str, artifact_digest: str | None
    ) -> CleanupState:
        if artifact_digest is None:
            return "none"
        if self._cover_storage is None or self._thumbnail_cache is None:
            raise CatalogRemovalInfrastructureError(
                "Cover cleanup infrastructure is unavailable."
            )
        try:
            parsed = MediaId.from_string(media_id)
            media_dir = self._cover_storage.root / parsed.to_string()
            if media_dir.exists():
                if media_dir.is_symlink() or not media_dir.is_dir():
                    return "failed"
                resolved = media_dir.resolve(strict=False)
                if not _is_within(resolved, self._cover_storage.root.resolve()):
                    return "failed"
                shutil.rmtree(resolved)
            thumb_key = self._thumbnail_cache.key_for(
                media_id=parsed, artifact_digest=artifact_digest
            )
            thumb_path = self._thumbnail_cache.root.joinpath(*Path(thumb_key).parts)
            if thumb_path.exists() or thumb_path.is_symlink():
                if thumb_path.is_symlink() or not thumb_path.is_file():
                    return "failed"
                resolved_thumb = thumb_path.resolve(strict=False)
                if not _is_within(
                    resolved_thumb, self._thumbnail_cache.root.resolve()
                ):
                    return "failed"
                resolved_thumb.unlink()
            return "complete"
        except (OSError, ValueError):
            return "failed"

    def cleanup_previews(self, *, location_ids_json: str | None) -> CleanupState:
        if location_ids_json is None:
            return "none"
        if self._preview_cache is None:
            raise CatalogRemovalInfrastructureError(
                "Gallery preview cleanup infrastructure is unavailable."
            )
        try:
            location_ids = json.loads(location_ids_json)
            if not isinstance(location_ids, list):
                return "failed"
            for location_id in location_ids:
                if not isinstance(location_id, str) or len(location_id) != 36:
                    return "failed"
                location_dir = (
                    self._preview_cache.root
                    / GALLERY_PREVIEW_ALGORITHM_VERSION
                    / location_id
                )
                if not location_dir.exists() and not location_dir.is_symlink():
                    continue
                if location_dir.is_symlink() or not location_dir.is_dir():
                    return "failed"
                resolved = location_dir.resolve(strict=False)
                if not _is_within(resolved, self._preview_cache.root.resolve()):
                    return "failed"
                shutil.rmtree(resolved)
            return "complete"
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return "failed"


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
