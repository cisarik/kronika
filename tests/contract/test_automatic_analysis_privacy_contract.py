"""Contract guards for automatic-analysis consent and privacy wording."""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PRODUCT = REPOSITORY_ROOT / "PRODUCT.md"
CONFIGURATION = REPOSITORY_ROOT / "src" / "kronika" / "configuration.py"
ADR_0044 = (
    REPOSITORY_ROOT
    / "docs"
    / "adr"
    / "0044-durable-automatic-post-catalog-analysis.md"
)


def _product() -> str:
    return PRODUCT.read_text(encoding="utf-8")


def _adr() -> str:
    return ADR_0044.read_text(encoding="utf-8")


def test_product_rejects_absolute_automatic_upload_ban() -> None:
    product = _product()
    # Absolute product rule that contradicts explicit server-owner opt-in.
    assert "upload frames without user intent and confirmation" not in product
    # Forbid restoring a blanket automatic-upload ban without the enablement carve-out.
    blanket = re.search(
        r"must not automatically[^.]*upload[^.]*frames[^.]*\.",
        product,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if blanket is not None:
        clause = blanket.group(0).lower()
        assert "framenest_automatic_media_analysis_enabled" in clause or (
            "enabled" in clause and ("server" in clause or "administrator" in clause)
        )


def test_product_states_default_disabled_and_server_owner_standing_consent() -> None:
    product = _product()
    lower = product.lower()
    assert "FRAMENEST_AUTOMATIC_MEDIA_ANALYSIS_ENABLED" in product
    assert (
        re.search(r"default[^.]*\b(disabled|off|false)\b", lower) is not None
        or re.search(r"\b(disabled|off|false)\b[^.]*default", lower) is not None
    )
    assert (
        "standing" in lower
        or "opt-in" in lower
        or "enablement" in lower
        or "administrator" in lower
    )
    assert "confirm_cloud_upload" in lower or "per-request" in lower


def test_product_and_adr_0044_share_server_enablement_consent_boundary() -> None:
    product = _product()
    adr = _adr()
    assert "FRAMENEST_AUTOMATIC_MEDIA_ANALYSIS_ENABLED" in product
    assert "FRAMENEST_AUTOMATIC_MEDIA_ANALYSIS_ENABLED" in adr
    assert "consent" in adr.lower()
    assert "confirm_cloud_upload" in adr.lower()
    assert "upload frames without user intent and confirmation" not in product
    assert "server-owned enablement" in adr.lower() or "server-owned" in adr.lower()
    assert "ANALYSIS_OUTCOME_UNKNOWN" in adr or "ambiguous" in adr.lower()


def test_git_tracked_default_remains_false() -> None:
    configuration = CONFIGURATION.read_text(encoding="utf-8")
    assert (
        "automatic_media_analysis_enabled: bool = Field(default=False)" in configuration
    )
