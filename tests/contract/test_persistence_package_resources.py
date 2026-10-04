"""Installed-package verification for FrameNest migration resources."""

from __future__ import annotations
from tests.support.tooling import resolve_tool

import json
import subprocess
import sys
import venv
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_migration_resources_are_discoverable_from_installed_wheel(
    tmp_path: Path,
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    virtualenv_path = tmp_path / "resource-check-venv"
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
                "import kronika.infrastructure.persistence.alembic_environment as env\n"
                "root = resources.files(env)\n"
                "print(json.dumps({\n"
                "    'env': root.joinpath('env.py').is_file(),\n"
                "    'template': root.joinpath('script.py.mako').is_file(),\n"
                "    'revision_0001': root.joinpath('versions', '0001_initial_foundation.py').is_file(),\n"
                "    'revision_0002': root.joinpath('versions', '0002_device_registry.py').is_file(),\n"
                "    'revision_0003': root.joinpath('versions', '0003_library_registry.py').is_file(),\n"
                "    'revision_0004': root.joinpath('versions', '0004_media_catalog_foundation.py').is_file(),\n"
                "    'revision_0005': root.joinpath('versions', '0005_media_metadata_and_canonical_tags.py').is_file(),\n"
                "    'revision_0006': root.joinpath('versions', '0006_persistent_media_description.py').is_file(),\n"
                "    'revision_0007': root.joinpath('versions', '0007_automatic_processed_collection.py').is_file(),\n"
                "    'revision_0008': root.joinpath('versions', '0008_upload_sessions.py').is_file(),\n"
                "    'revision_0009': root.joinpath('versions', '0009_upload_session_completeness.py').is_file(),\n"
                "    'revision_0010': root.joinpath('versions', '0010_upload_validation_evidence.py').is_file(),\n"
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
        "env": True,
        "revision_0001": True,
        "revision_0002": True,
        "revision_0003": True,
        "revision_0004": True,
        "revision_0005": True,
        "revision_0006": True,
        "revision_0007": True,
        "revision_0008": True,
        "revision_0009": True,
        "revision_0010": True,
        "template": True,
    }
    assert not (REPOSITORY_ROOT / "dist").exists()
