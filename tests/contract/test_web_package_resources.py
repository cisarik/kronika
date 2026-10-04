"""Installed-package verification for FrameNest web application resources."""

from __future__ import annotations
from tests.support.tooling import resolve_tool

import json
import subprocess
import sys
import venv
import zipfile
from importlib import resources
from pathlib import Path

import kronika.adapters.api.web as web_resources

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WEB_RESOURCE_NAMES = ("index.html", "styles.css", "app.js")


def test_web_resources_are_available_from_package_resource_boundary() -> None:
    resource_root = resources.files(web_resources)
    discovered = {
        resource_name: resource_root.joinpath(resource_name).is_file()
        for resource_name in WEB_RESOURCE_NAMES
    }
    assert discovered == {
        "app.js": True,
        "index.html": True,
        "styles.css": True,
    }


def test_web_resources_are_included_in_built_wheel(
    tmp_path: Path,
) -> None:
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

    for resource_name in WEB_RESOURCE_NAMES:
        assert f"kronika/adapters/api/web/{resource_name}" in wheel_names

    assert not (REPOSITORY_ROOT / "dist").exists()


def test_web_resources_are_discoverable_from_installed_wheel(
    tmp_path: Path,
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    virtualenv_path = tmp_path / "web-resource-check-venv"
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

    venv.EnvBuilder(with_pip=True).create(virtualenv_path)
    python = virtualenv_path / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    install = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            str(wheels[0]),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60.0,
    )
    assert install.returncode == 0, install.stderr

    probe = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import json\n"
                "from importlib import resources\n"
                "import kronika.adapters.api.web as web\n"
                "root = resources.files(web)\n"
                "print(json.dumps({\n"
                "    'index': root.joinpath('index.html').is_file(),\n"
                "    'styles': root.joinpath('styles.css').is_file(),\n"
                "    'app': root.joinpath('app.js').is_file(),\n"
                "}, sort_keys=True))\n"
            ),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=30.0,
    )

    assert probe.returncode == 0, probe.stderr
    assert json.loads(probe.stdout) == {
        "app": True,
        "index": True,
        "styles": True,
    }
    assert not (REPOSITORY_ROOT / "dist").exists()
