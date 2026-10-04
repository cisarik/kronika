"""Frontend contract for YouTube category filter and creator attribution chips."""

from __future__ import annotations

from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[2] / "src/kronika/adapters/api/web"


def test_youtube_gallery_control_uses_content_category() -> None:
    index = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    assert 'data-content-category="youtube"' in index
    assert 'data-acquisition-source="youtube_manual_claim"' not in index
    assert 'option value="youtube"' in index
    assert 'setCatalogClassificationFilter({ contentCategory: "youtube" })' in app
    assert 'catalogState.contentCategory === "youtube"' in app


def test_acquisition_source_is_read_only_in_metadata_ui() -> None:
    index = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    assert 'id="metadata-acquisition-source" disabled' in index
    assert "sourceSelect.disabled = true" in app
    assert "acquisition_source: normalized.acquisitionSource" not in app


def test_creator_chip_helpers_precede_semantic_tags() -> None:
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    styles = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")
    assert "function appendCatalogCreatorChip" in app
    assert "function appendDetailsCreatorChip" in app
    assert "catalog-card__tag--creator" in app
    assert "metadata-tag-chip--creator" in app
    assert "setCatalogCreatorFilter" in app
    assert "catalog-card__tag--creator" in styles
    assert "media-details-dialog__tag--creator" in styles


def test_ai_quick_action_does_not_read_or_preserve_taxonomy_on_the_card() -> None:
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    start = app.index("async function handleAnalyzeCatalogCard")
    end = app.index("\nfunction setCatalogPagination", start + 1)
    body = app[start:end]
    assert "acquisition_source:" not in body
    assert "creator_attribution_kind:" not in body
    assert "last-write-wins" not in body
    assert "will replace the current canonical values" not in body
