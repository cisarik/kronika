"""Capture resources, provenance and both entry points in the built wheel."""

from __future__ import annotations
from tests.support.tooling import resolve_tool

import json
import subprocess
import sys
import zipfile
from importlib import resources
from pathlib import Path

from kronika_capture.paths import packaged_extension_path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CAPTURE_ROOT = REPOSITORY_ROOT / "src" / "kronika_capture"
ASSET_PATHS = (
    "_assets/extension/src/adapters/adapter.js",
    "_assets/extension/src/adapters/pack_v5.json",
    "_assets/extension/src/engine/dom_engine.js",
    "_assets/extension/src/engine/index.js",
    "_assets/extension/src/engine/interventions.js",
    "_assets/extension/src/headless/bridge_client.mjs",
    "_assets/extension/src/headless/cdp_client.mjs",
    "_assets/extension/src/headless/driver.mjs",
    "_assets/extension/src/headless/job_engine.mjs",
    "_assets/extension/src/headless/login_app/app.css",
    "_assets/extension/src/headless/login_app/app.js",
    "_assets/extension/src/headless/login_app/index.html",
    "_assets/extension/src/headless/login_server.mjs",
    "_assets/extension/src/headless/probe.mjs",
    "_assets/extension/src/headless/resource_policy.mjs",
    "_assets/extension/src/headless/runner.mjs",
    "_assets/extension/src/protocol.js",
    "_assets/extension/src/url_guard.js",
)


def test_importlib_resources_resolve_packaged_assets() -> None:
    root = resources.files("kronika_capture")
    for relative in ASSET_PATHS:
        asset = root.joinpath(relative)
        assert asset.is_file(), relative
        assert packaged_extension_path(*Path(relative).parts[3:]).read_bytes() == asset.read_bytes()


def test_provenance_covers_every_relocated_file() -> None:
    manifest = json.loads(
        (REPOSITORY_ROOT / "docs/provenance/kronika-capture.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == 1
    assert manifest["upstreams"] == [{
        "repository": "https://github.com/cisarik/cli_chatgpt.git",
        "captured_as": "https://github.com/cisarik/kronika.git",
        "commit": "66c40d43c577276b0ad304a494fbbb1ffb6fc933",
        "tree": "848f247434deea4c217170c012612b39e41557f3",
    }]
    assert manifest["framenest_baseline"]["commit"] == (
        "93e7742d56d46d4725d4561bd8751b15e55e5eb5"
    )
    files = manifest["files"]
    destinations = {entry["destination_path"] for entry in files}
    actual = {
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in CAPTURE_ROOT.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert len(files) == len(destinations) == 32
    assert destinations | {"src/kronika_capture/bridge/journal.py"} == actual
    for entry in files:
        assert entry["source_path"]
        assert entry["disposition"] == "relocated"
        assert entry["feature"] and entry["adaptation_summary"]
        for blob in (entry["source_blob"], entry["framenest_origin"]["blob"]):
            assert len(blob) == 40 and set(blob) <= set("0123456789abcdef")
    excluded = manifest["excluded_features"]
    assert len(excluded) == 6
    assert all(entry["source_location"] and entry["exclusion_reason"] for entry in excluded)


def test_wheel_contains_kernel_assets_and_entry_point(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    build = subprocess.run(
        [resolve_tool("poetry"), "build", "--format", "wheel", "--output", str(wheelhouse)],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120.0,
    )
    assert build.returncode == 0, build.stderr
    wheels = sorted(wheelhouse.glob("kronika-*.whl"))
    assert len(wheels) == 1
    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        assert len(names) == len(set(names))
        # ADR-0085 moved the application package to `kronika`. The canonical
        # package must be present in the wheel and the retired one must be
        # absent: this assertion is inverted, not deleted, so a regression that
        # reinstalls an importable `framenest` package fails loudly here.
        assert any(name.startswith("kronika/") for name in names)
        assert not any(name.startswith(("framenest/", "vendor/")) for name in names)
        expected = {
            path.relative_to(REPOSITORY_ROOT / "src").as_posix(): path
            for path in CAPTURE_ROOT.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        assert len(expected) == 33
        assert {name for name in names if name.startswith("kronika_capture/")} == set(expected)
        for name, source in expected.items():
            assert wheel.read(name) == source.read_bytes(), name
        entry_points = wheel.read("kronika-0.1.0.dist-info/entry_points.txt").decode("utf-8")
        wheel.extractall(unpacked)
    for relative in ASSET_PATHS:
        assert f"kronika_capture/{relative}" in names
    assert "kronika_capture/__init__.py" in names
    for command in ("kronika-capture", "framenest-chatgpt-page"):
        assert f"{command}=kronika_capture.cli:main" in entry_points.replace(" ", "")

    # Import the built artifact without checkout paths or installed packages.
    # No command, browser or provider is started.
    probe = subprocess.run(
        [
            sys.executable, "-I", "-S", "-B", "-c",
            """import sys
from importlib import metadata, resources
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
import kronika_capture
from kronika_capture.cli import main
from kronika_capture.paths import packaged_extension_path
assert Path(kronika_capture.__file__).parent == root / "kronika_capture"
entries = {
    ep.name: ep for ep in metadata.distribution("kronika").entry_points
    if ep.group == "console_scripts"
}
assert entries["kronika-capture"].load() is main
assert entries["framenest-chatgpt-page"].load() is main
for relative in sys.argv[2:]:
    data = resources.files("kronika_capture").joinpath(relative).read_bytes()
    assert packaged_extension_path(*Path(relative).parts[3:]).read_bytes() == data
assert not any(name == "framenest" or name.startswith("framenest.") for name in sys.modules)
""",
            str(unpacked), *ASSET_PATHS,
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    assert probe.returncode == 0, probe.stderr
    assert not (REPOSITORY_ROOT / "dist").exists()
