"""Deterministic consequence-fingerprint evidence for catalog removal."""

from __future__ import annotations

from kronika.application.catalog_removal import (
    CatalogRemovalAnalysisSnapshot,
    CatalogRemovalCoverSnapshot,
    CatalogRemovalLocationSnapshot,
    CatalogRemovalSnapshot,
    CatalogRemovalUploadSnapshot,
    CatalogRemovalYouTubeSnapshot,
    ORIGINAL_BYTES_POLICY,
    build_preview,
    compute_consequence_fingerprint,
)

MEDIA_ID = "11111111-1111-4111-8111-111111111111"
LOCATION_ID = "22222222-2222-4222-8222-222222222222"
LIBRARY_ID = "33333333-3333-4333-8333-333333333333"


def _snapshot(**overrides: object) -> CatalogRemovalSnapshot:
    values: dict[str, object] = {
        "media_id": MEDIA_ID,
        "media_kind": "video",
        "media_updated_at_ms": 100,
        "display_title": "Synthetic title",
        "acquisition_source": "library_scan",
        "content_category": "general",
        "publication_state": "unpublished",
        "published_at_ms": None,
        "publication_origin": None,
        "locations": (
            CatalogRemovalLocationSnapshot(
                location_id=LOCATION_ID,
                library_id=LIBRARY_ID,
                relative_path="safe/item.mp4",
                availability="available",
                observed_size_bytes=10,
                observed_mtime_ns=20,
            ),
        ),
        "upload_publications": (),
        "youtube_claims": (),
        "cover": None,
        "analysis_runs": (),
        "storage_class": "operator_managed",
    }
    values.update(overrides)
    return CatalogRemovalSnapshot(**values)  # type: ignore[arg-type]


def test_fingerprint_is_stable_across_equal_snapshots() -> None:
    first = compute_consequence_fingerprint(_snapshot())
    second = compute_consequence_fingerprint(_snapshot())
    assert first == second
    assert len(first) == 64
    assert first == first.lower()


def test_fingerprint_changes_when_material_state_changes() -> None:
    base = compute_consequence_fingerprint(_snapshot())
    updated = compute_consequence_fingerprint(_snapshot(media_updated_at_ms=101))
    published = compute_consequence_fingerprint(
        _snapshot(
            publication_state="published",
            published_at_ms=50,
            publication_origin="admin_explicit",
        )
    )
    covered = compute_consequence_fingerprint(
        _snapshot(
            cover=CatalogRemovalCoverSnapshot(
                revision=1,
                artifact_digest="a" * 64,
            )
        )
    )
    analyzed = compute_consequence_fingerprint(
        _snapshot(
            analysis_runs=(
                CatalogRemovalAnalysisSnapshot(
                    run_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    version=1,
                    provider_submission_occurred=True,
                ),
            )
        )
    )
    assert len({base, updated, published, covered, analyzed}) == 5


def test_preview_always_retains_original_bytes() -> None:
    preview = build_preview(
        _snapshot(
            upload_publications=(
                CatalogRemovalUploadSnapshot(
                    upload_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                    destination_id=LIBRARY_ID,
                    relative_target="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.mp4",
                    byte_identity_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                    state="cataloged",
                ),
            ),
            youtube_claims=(
                CatalogRemovalYouTubeSnapshot(
                    claim_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                    state="cataloged",
                    media_id=MEDIA_ID,
                    media_location_id=LOCATION_ID,
                ),
            ),
            storage_class="server_managed_upload",
        )
    )
    assert preview.original_bytes_policy == ORIGINAL_BYTES_POLICY
    assert preview.original_bytes_outcome == "retained_server_managed"
    assert "youtube_claims_become_catalog_removed" in preview.provenance_effects
    assert "upload_publication_catalog_links_detached" in preview.provenance_effects
    assert "gallery_preview_cache" in preview.derived_artifact_cleanup_intent
