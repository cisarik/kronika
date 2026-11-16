"""Deterministic contracts for the single shared yt-dlp dependency pin.

These tests never contact the network. They bind the declared project pin,
the resolved lock entry, the installed distribution metadata, the shared X
extractor pin constant, and the X extractor seam that the pin advance must
keep working.
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
import tomllib

from kronika.infrastructure.x.status_bridge import PINNED_YTDLP_VERSION

EXPECTED_YTDLP_PIN = "2026.10.26"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_installed_distribution_matches_the_declared_pin() -> None:
    assert metadata.version("yt-dlp") == EXPECTED_YTDLP_PIN


def test_shared_x_pin_constant_matches_the_declared_pin() -> None:
    assert PINNED_YTDLP_VERSION == EXPECTED_YTDLP_PIN


def test_project_declares_one_exact_yt_dlp_dependency_without_ejs() -> None:
    document = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = document["project"]["dependencies"]
    yt_dlp_requirements = [
        requirement
        for requirement in dependencies
        if requirement.startswith("yt-dlp")
    ]

    assert yt_dlp_requirements == [f"yt-dlp (=={EXPECTED_YTDLP_PIN})"]
    assert not any("yt-dlp-ejs" in requirement for requirement in dependencies)


def test_lock_pins_one_yt_dlp_release_and_no_ejs_distribution() -> None:
    lock = tomllib.loads(
        (REPOSITORY_ROOT / "poetry.lock").read_text(encoding="utf-8")
    )
    packages = {package["name"]: package for package in lock["package"]}

    assert packages["yt-dlp"]["version"] == EXPECTED_YTDLP_PIN
    assert "yt-dlp-ejs" not in packages


def test_installed_yt_dlp_requires_ejs_only_through_an_optional_extra() -> None:
    requirements = metadata.requires("yt-dlp") or []
    ejs_requirements = [
        requirement
        for requirement in requirements
        if "yt-dlp-ejs" in requirement
    ]

    assert ejs_requirements
    for requirement in ejs_requirements:
        assert "extra ==" in requirement


def test_pinned_twitter_extractor_seam_is_available() -> None:
    from yt_dlp.extractor.twitter import TwitterIE

    assert callable(getattr(TwitterIE, "_extract_status", None))
