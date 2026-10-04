"""Installed-package verification for the committed vision-probe fixture."""

from __future__ import annotations
from tests.support.tooling import resolve_tool

import subprocess
import zipfile
from importlib import resources
from pathlib import Path

import kronika.infrastructure.ai.fixtures as fixture_resources

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_NAME = "vision-probe-red-8x8.png"
FIXTURE_BYTES = 74
WHEEL_FIXTURE_PATH = f"kronika/infrastructure/ai/fixtures/{FIXTURE_NAME}"


def test_vision_probe_fixture_is_available_from_package_resource_boundary() -> None:
    resource_root = resources.files(fixture_resources)
    fixture = resource_root.joinpath(FIXTURE_NAME)

    assert fixture.is_file()
    assert len(fixture.read_bytes()) == FIXTURE_BYTES


def test_vision_probe_fixture_is_included_in_built_wheel(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()

    build = subprocess.run(
        [resolve_tool("poetry"), "build", "--format", "wheel", "--output", str(wheelhouse)],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60.0,
    )
    assert build.returncode == 0, build.stderr

    wheels = sorted(wheelhouse.glob("kronika-*.whl"))
    assert len(wheels) == 1

    with zipfile.ZipFile(wheels[0]) as wheel:
        wheel_names = set(wheel.namelist())

    assert WHEEL_FIXTURE_PATH in wheel_names
    assert not (REPOSITORY_ROOT / "dist").exists()
