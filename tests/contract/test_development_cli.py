from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from typing import Any

import pytest
from tests.support.kronika_identity import expected
from tests.support.tooling import resolve_tool

from framenest.adapters.cli import development as cli
from framenest.infrastructure.runtime.development import RuntimeStatus

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ROOT_WRAPPER = REPOSITORY_ROOT / "framenest"
CANONICAL_VENV_BIN = REPOSITORY_ROOT / ".venv" / "bin"
SYNTHETIC_NVIDIA_KEY = "synthetic-nvidia-key"
SYNTHETIC_GATEWAY_KEY = "synthetic-gateway-key"

FAKE_UV_SCRIPT = (
    "#!/bin/sh\n"
    "echo uv:$* >> \"$LOG\"\n"
    "if [ \"$1 $2 $3\" = \"python find 3.13.14\" ]; then\n"
    "  if [ -f \"$FOUND\" ]; then echo /tmp/python-3.13.14; exit 0; fi\n"
    "  touch \"$FOUND\"; exit 1\n"
    "fi\n"
    "exit 0\n"
)
FAKE_POETRY_SCRIPT = (
    "#!/bin/sh\n"
    "echo poetry:$*:venv=$POETRY_VIRTUALENVS_IN_PROJECT >> \"$LOG\"\n"
    "mkdir -p .venv/bin\n"
    "printf '#!/bin/sh\\nexit 0\\n' > .venv/bin/framenest-dev\n"
    "chmod +x .venv/bin/framenest-dev\n"
    "exit 0\n"
)
FAKE_POETRY_FAILING_SCRIPT = (
    "#!/bin/sh\n"
    "echo poetry:$*:venv=$POETRY_VIRTUALENVS_IN_PROJECT >> \"$LOG\"\n"
    "exit 1\n"
)
FAKE_CONTROLLER_STUB = "#!/bin/sh\nexit 0\n"
SCENARIO_TMP_MARKER = "test_setup_uses_uv_managed_pyt"
_RUNNING_URL = "http://127.0.0.1:8000/"


class _SetupScenario:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.launcher = root / "framenest"
        self.bin_dir = root / "bin"
        self.log = root / "commands.log"
        self.found = root / "found"
        self.fish_home = root / "fish-home"

    def prepare(self, *, failing_poetry: bool = False, without_uv: bool = False) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT_WRAPPER, self.launcher)
        self.bin_dir.mkdir(exist_ok=True)
        if not without_uv:
            uv = self.bin_dir / "uv"
            uv.write_text(FAKE_UV_SCRIPT, encoding="utf-8")
            uv.chmod(0o755)
        poetry = self.bin_dir / "poetry"
        poetry.write_text(
            FAKE_POETRY_FAILING_SCRIPT if failing_poetry else FAKE_POETRY_SCRIPT,
            encoding="utf-8",
        )
        poetry.chmod(0o755)

    def env(self, *, tools_only_path: bool = False) -> dict[str, str]:
        env = os.environ.copy()
        if tools_only_path:
            # Hermetic tool visibility: only the fake bin directory and the
            # system defaults are visible, so missing-tool behavior cannot
            # silently fall back to a real host toolchain.
            env["PATH"] = os.pathsep.join([str(self.bin_dir), "/usr/bin", "/bin"])
        else:
            env["PATH"] = f"{self.bin_dir}{os.pathsep}{env['PATH']}"
        env["LOG"] = str(self.log)
        env["FOUND"] = str(self.found)
        env["HOME"] = str(self.fish_home / "home")
        env["XDG_CONFIG_HOME"] = str(self.fish_home / "xdg-config")
        env["XDG_DATA_HOME"] = str(self.fish_home / "xdg-data")
        return env

    def run(
        self,
        *,
        failing_poetry: bool = False,
        without_uv: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        self.prepare(failing_poetry=failing_poetry, without_uv=without_uv)
        return subprocess.run(
            [resolve_tool("fish"), str(self.launcher), "setup"],
            cwd=self.bin_dir,
            env=self.env(tools_only_path=without_uv),
            check=False,
            text=True,
            capture_output=True,
        )

    def fresh_fish(self, script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [resolve_tool("fish"), "-c", script],
            env=self.env(),
            check=False,
            text=True,
            capture_output=True,
        )


def _real_fish_user_paths() -> list[str]:
    result = subprocess.run(
        [resolve_tool("fish"), "-c", "for p in $fish_user_paths; echo $p; end"],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("fish is unavailable for real persistent-state observation")
    return result.stdout.splitlines()


def _fish_user_paths_fingerprint() -> str:
    joined = "\n".join(_real_fish_user_paths())
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _assert_no_scenario_paths(entries: list[str], owned_prefix: str) -> None:
    leaked = [entry for entry in entries if owned_prefix in entry]
    assert not leaked, f"scenario-owned paths leaked into persistent fish state: {len(leaked)} entries"


def _remove_owned_tree(target: Path, owned_root: Path) -> None:
    resolved_target = target.resolve()
    resolved_owned = owned_root.resolve()
    if resolved_target != resolved_owned and resolved_owned not in resolved_target.parents:
        raise ValueError("refusing to remove a path outside the owned root")
    if target.is_symlink():
        raise ValueError("refusing to remove a symlink")
    shutil.rmtree(target, ignore_errors=True)


class _Runtime:
    last: "_Runtime | None" = None

    def __init__(self) -> None:
        self.open_after_start: list[bool] = []
        self.opened = False
        self.log_lines = ["one\n", "two\n"]
        _Runtime.last = self

    def start(self, *, open_after_start: bool) -> Any:
        self.open_after_start.append(open_after_start)
        return _result(True, "running", "started")

    def stop(self) -> Any:
        return _result(True, "stopped", "stopped")

    def restart(self, *, open_after_start: bool) -> Any:
        self.open_after_start.append(open_after_start)
        return _result(True, "running", "restarted")

    def status(self) -> RuntimeStatus:
        # This fake mirrors the real runtime result shape, which interpolates the
        # served URL into the message. It previously carried a bare sentence that
        # no longer matched any product message and that no assertion read, so it
        # proved nothing about the launcher and drifted further at every identity
        # cut. `_RUNNING_URL` and the derived message are asserted in
        # `test_cli_status_open_and_logs`.
        return RuntimeStatus(
            kind="running",
            url=_RUNNING_URL,
            pid=123,
            database_state="at_head",
            log_available=True,
            message=expected("{brand} is running at {url}", url=_RUNNING_URL),
        )

    def open(self) -> Any:
        self.opened = True
        return _result(True, "running", "opened")

    def read_log_tail(self, *, lines: int) -> list[str]:
        return self.log_lines[-lines:]

    def follow_log(self) -> Any:
        return iter(())


def _result(ok: bool, kind: str, message: str) -> Any:
    status = RuntimeStatus(
        kind=kind,  # type: ignore[arg-type]
        url=_RUNNING_URL if kind == "running" else None,
        pid=123 if kind == "running" else None,
        database_state="at_head",
        log_available=True,
        message=message,
    )
    return type("Result", (), {"ok": ok, "status": status, "message": message})()


def test_root_wrapper_is_executable_fish_script() -> None:
    mode = ROOT_WRAPPER.stat().st_mode

    assert mode & stat.S_IXUSR
    assert ROOT_WRAPPER.read_text(encoding="utf-8").splitlines()[0] == "#!/usr/bin/env fish"
    subprocess.run([resolve_tool("fish"), "--no-execute", str(ROOT_WRAPPER)], check=True)


def test_root_wrapper_avoids_eval_and_bash_only_constructs() -> None:
    source = ROOT_WRAPPER.read_text(encoding="utf-8")

    assert "eval" not in source
    assert "[[" not in source
    assert "$?" not in source
    assert "function " in source
    assert "POETRY_VIRTUALENVS_IN_PROJECT" in source
    assert "uv python find 3.13.14" in source
    assert "uv python install 3.13.14" in source
    assert "poetry install --no-interaction" in source


def test_root_wrapper_resolves_script_root_from_another_cwd_and_preserves_exit(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "framenest"
    shutil.copy2(ROOT_WRAPPER, launcher)
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    controller = venv_bin / "framenest-dev"
    controller.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$PWD\" \"$1\" \"$2\"\nexit 37\n",
        encoding="utf-8",
    )
    controller.chmod(0o755)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()

    result = subprocess.run(
        [resolve_tool("fish"), str(launcher), "status", "value with spaces"],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 37
    lines = result.stdout.splitlines()
    assert lines == [str(cwd), "status", "value with spaces"]


def test_root_wrapper_routes_ai_commands_to_ai_controller(tmp_path: Path) -> None:
    launcher = tmp_path / "framenest"
    shutil.copy2(ROOT_WRAPPER, launcher)
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    controller = venv_bin / "framenest-ai"
    controller.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$1\" \"$2\"\nexit 23\n",
        encoding="utf-8",
    )
    controller.chmod(0o755)

    result = subprocess.run(
        [resolve_tool("fish"), str(launcher), "ai", "status", "--config-path"],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 23
    assert result.stdout.splitlines() == ["status", "--config-path"]


def test_root_wrapper_loads_local_ai_env_for_ai_controller(tmp_path: Path) -> None:
    launcher = tmp_path / "framenest"
    shutil.copy2(ROOT_WRAPPER, launcher)
    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()
    secrets_file = secrets_dir / "ai.env.fish"
    secrets_file.write_text(
        "\n".join(
            [
                f"set -gx NVIDIA_API_KEY '{SYNTHETIC_NVIDIA_KEY}'",
                f"set -gx AI_GATEWAY_API_KEY '{SYNTHETIC_GATEWAY_KEY}'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    secrets_file.chmod(0o600)
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    controller = venv_bin / "framenest-ai"
    controller.write_text(
        "#!/bin/sh\n"
        f'test "$NVIDIA_API_KEY" = "{SYNTHETIC_NVIDIA_KEY}" || exit 41\n'
        f'test "$AI_GATEWAY_API_KEY" = "{SYNTHETIC_GATEWAY_KEY}" || exit 42\n'
        "printf 'received\\n'\n",
        encoding="utf-8",
    )
    controller.chmod(0o755)

    result = subprocess.run(
        [resolve_tool("fish"), str(launcher), "ai", "status"],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "received"
    assert SYNTHETIC_NVIDIA_KEY not in result.stdout + result.stderr
    assert SYNTHETIC_GATEWAY_KEY not in result.stdout + result.stderr


def test_root_wrapper_loads_local_ai_env_for_managed_start(tmp_path: Path) -> None:
    launcher = tmp_path / "framenest"
    shutil.copy2(ROOT_WRAPPER, launcher)
    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()
    secrets_file = secrets_dir / "ai.env.fish"
    secrets_file.write_text(
        "\n".join(
            [
                f"set -gx NVIDIA_API_KEY '{SYNTHETIC_NVIDIA_KEY}'",
                f"set -gx AI_GATEWAY_API_KEY '{SYNTHETIC_GATEWAY_KEY}'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    secrets_file.chmod(0o600)
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    controller = venv_bin / "framenest-dev"
    controller.write_text(
        "#!/bin/sh\n"
        f'test "$NVIDIA_API_KEY" = "{SYNTHETIC_NVIDIA_KEY}" || exit 43\n'
        f'test "$AI_GATEWAY_API_KEY" = "{SYNTHETIC_GATEWAY_KEY}" || exit 44\n'
        "printf 'started\\n'\n",
        encoding="utf-8",
    )
    controller.chmod(0o755)

    result = subprocess.run(
        [resolve_tool("fish"), str(launcher), "start", "--no-open"],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "started"
    assert SYNTHETIC_NVIDIA_KEY not in result.stdout + result.stderr
    assert SYNTHETIC_GATEWAY_KEY not in result.stdout + result.stderr


def test_root_wrapper_rejects_symlinked_local_ai_env(tmp_path: Path) -> None:
    launcher = tmp_path / "framenest"
    shutil.copy2(ROOT_WRAPPER, launcher)
    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()
    target = tmp_path / "target.env.fish"
    target.write_text(f"set -gx NVIDIA_API_KEY '{SYNTHETIC_NVIDIA_KEY}'\n", encoding="utf-8")
    target.chmod(0o600)
    (secrets_dir / "ai.env.fish").symlink_to(target)
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    controller = venv_bin / "framenest-ai"
    controller.write_text("#!/bin/sh\nprintf 'controller-ran\\n'\n", encoding="utf-8")
    controller.chmod(0o755)

    result = subprocess.run(
        [resolve_tool("fish"), str(launcher), "ai", "status"],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "unsafe" in result.stderr
    assert "controller-ran" not in result.stdout
    assert SYNTHETIC_NVIDIA_KEY not in result.stdout + result.stderr


def test_root_wrapper_rejects_insecure_local_ai_env_permissions(tmp_path: Path) -> None:
    launcher = tmp_path / "framenest"
    shutil.copy2(ROOT_WRAPPER, launcher)
    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()
    secrets_file = secrets_dir / "ai.env.fish"
    secrets_file.write_text(f"set -gx NVIDIA_API_KEY '{SYNTHETIC_NVIDIA_KEY}'\n", encoding="utf-8")
    secrets_file.chmod(0o644)
    if stat.S_IMODE(secrets_file.stat().st_mode) != 0o644:
        pytest.skip("platform did not preserve test file permissions")
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    controller = venv_bin / "framenest-ai"
    controller.write_text("#!/bin/sh\nprintf 'controller-ran\\n'\n", encoding="utf-8")
    controller.chmod(0o755)

    result = subprocess.run(
        [resolve_tool("fish"), str(launcher), "ai", "status"],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "unsafe" in result.stderr
    assert "controller-ran" not in result.stdout
    assert SYNTHETIC_NVIDIA_KEY not in result.stdout + result.stderr


def test_root_wrapper_rejects_invalid_local_ai_env_before_execution(tmp_path: Path) -> None:
    launcher = tmp_path / "framenest"
    shutil.copy2(ROOT_WRAPPER, launcher)
    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()
    secrets_file = secrets_dir / "ai.env.fish"
    secrets_file.write_text(
        f"echo '{SYNTHETIC_NVIDIA_KEY}'\nset -gx NVIDIA_API_KEY (\n",
        encoding="utf-8",
    )
    secrets_file.chmod(0o600)
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    controller = venv_bin / "framenest-ai"
    controller.write_text("#!/bin/sh\nprintf 'controller-ran\\n'\n", encoding="utf-8")
    controller.chmod(0o755)

    result = subprocess.run(
        [resolve_tool("fish"), str(launcher), "ai", "status"],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "invalid" in result.stderr
    assert "controller-ran" not in result.stdout
    assert SYNTHETIC_NVIDIA_KEY not in result.stdout + result.stderr


def test_setup_uses_uv_managed_python_and_poetry_install_without_real_download(
    tmp_path: Path,
) -> None:
    # The fake uv/poetry toolchain is visible only through the explicit
    # process-local PATH of the child. The child Fish process runs with an
    # isolated HOME/XDG surface so no Fish universal variable, user shell
    # configuration, or persistent PATH state can be read or written.
    scenario = _SetupScenario(tmp_path)
    parent_path_before = os.environ["PATH"]

    result = scenario.run()

    assert result.returncode == 0
    assert os.environ["PATH"] == parent_path_before
    recorded = scenario.log.read_text(encoding="utf-8")
    assert "uv:python find 3.13.14" in recorded
    assert "uv:python install 3.13.14" in recorded
    assert "poetry:env use /tmp/python-3.13.14:venv=true" in recorded
    assert "poetry:install --no-interaction:venv=true" in recorded

    # Positive control: the fake Poetry really wrote its console script, and
    # it wrote it only under the test-owned root.
    fake_controller = tmp_path / ".venv" / "bin" / "framenest-dev"
    assert fake_controller.read_text(encoding="utf-8") == FAKE_CONTROLLER_STUB

    # The isolated Fish surface observed no persistent path mutation.
    fresh = scenario.fresh_fish("echo (count $fish_user_paths)")
    assert fresh.returncode == 0
    assert fresh.stdout.strip() == "0"


def _canonical_controller_hash() -> str | None:
    controller = CANONICAL_VENV_BIN / "framenest-dev"
    if not controller.is_file():
        return None
    return hashlib.sha256(controller.read_bytes()).hexdigest()


def test_setup_scenario_does_not_touch_real_persistent_fish_state(tmp_path: Path) -> None:
    fingerprint_before = _fish_user_paths_fingerprint()

    scenario = _SetupScenario(tmp_path)
    result = scenario.run()

    assert result.returncode == 0
    assert _fish_user_paths_fingerprint() == fingerprint_before
    _assert_no_scenario_paths(_real_fish_user_paths(), str(tmp_path))


def test_setup_scenario_twice_keeps_identical_clean_fish_state(tmp_path: Path) -> None:
    scenario = _SetupScenario(tmp_path)

    first = scenario.run()
    assert first.returncode == 0
    store = scenario.fish_home / "xdg-config" / "fish" / "fish_variables"
    first_snapshot = store.read_bytes() if store.exists() else None

    second = scenario.run()
    assert second.returncode == 0
    second_snapshot = store.read_bytes() if store.exists() else None

    assert first_snapshot == second_snapshot
    fresh = scenario.fresh_fish("echo (count $fish_user_paths)")
    assert fresh.stdout.strip() == "0"
    fingerprint = _real_fish_user_paths()
    _assert_no_scenario_paths(fingerprint, str(tmp_path))


def test_setup_scenario_failure_leaves_fish_and_path_state_clean(tmp_path: Path) -> None:
    parent_path_before = os.environ["PATH"]
    scenario = _SetupScenario(tmp_path)

    result = scenario.run(failing_poetry=True)

    assert result.returncode != 0
    assert os.environ["PATH"] == parent_path_before
    fresh = scenario.fresh_fish("echo (count $fish_user_paths)")
    assert fresh.stdout.strip() == "0"
    _assert_no_scenario_paths(_real_fish_user_paths(), str(tmp_path))
    assert not (tmp_path / ".venv").exists()


def test_setup_scenario_missing_uv_stops_before_any_install_mutation(tmp_path: Path) -> None:
    canonical_before = _canonical_controller_hash()
    scenario = _SetupScenario(tmp_path)

    result = scenario.run(without_uv=True)

    assert result.returncode == 127
    assert not (tmp_path / ".venv").exists()
    assert not scenario.log.exists()
    assert _canonical_controller_hash() == canonical_before


def test_setup_scenario_preserves_seeded_unrelated_fish_paths(tmp_path: Path) -> None:
    scenario = _SetupScenario(tmp_path)
    sentinel = tmp_path / "sentinel-keep"
    sentinel.mkdir()
    seeded = scenario.fresh_fish(f"fish_add_path -p {sentinel}")
    assert seeded.returncode == 0

    result = scenario.run()
    assert result.returncode == 0

    fresh = scenario.fresh_fish("for p in $fish_user_paths; echo $p; end")
    assert fresh.stdout.splitlines() == [str(sentinel)]


def test_setup_scenario_does_not_modify_fish_config_files(tmp_path: Path) -> None:
    real_config = Path.home() / ".config" / "fish" / "config.fish"
    config_hash_before = (
        hashlib.sha256(real_config.read_bytes()).hexdigest() if real_config.is_file() else None
    )

    scenario = _SetupScenario(tmp_path)
    result = scenario.run()
    assert result.returncode == 0

    config_hash_after = (
        hashlib.sha256(real_config.read_bytes()).hexdigest() if real_config.is_file() else None
    )
    assert config_hash_after == config_hash_before
    # Fish may generate its own default config inside the isolated sandbox;
    # it must never reference the scenario-owned toolchain paths.
    for generated in scenario.fish_home.rglob("config.fish"):
        content = generated.read_text(encoding="utf-8")
        assert str(scenario.bin_dir) not in content
        assert str(tmp_path) not in content


def test_fake_toolchain_not_resolvable_outside_scenario_process(tmp_path: Path) -> None:
    scenario = _SetupScenario(tmp_path)
    result = scenario.run()
    assert result.returncode == 0

    # Positive control inside the exact child boundary.
    inside = scenario.fresh_fish("command -v uv; command -v poetry")
    assert inside.stdout.splitlines() == [
        str(scenario.bin_dir / "uv"),
        str(scenario.bin_dir / "poetry"),
    ]

    # A sibling process with the parent environment cannot resolve the fakes.
    for tool in ("uv", "poetry"):
        resolved = shutil.which(tool)
        if resolved is not None:
            assert not resolved.startswith(str(tmp_path))


def test_parent_worker_path_has_no_scenario_components(tmp_path: Path) -> None:
    parent_path_before = os.environ["PATH"]

    scenario = _SetupScenario(tmp_path)
    result = scenario.run()
    assert result.returncode == 0

    assert os.environ["PATH"] == parent_path_before
    components = os.environ["PATH"].split(os.pathsep)
    assert not [entry for entry in components if str(tmp_path) in entry]


def test_setup_scenario_cannot_replace_canonical_venv_console_script(tmp_path: Path) -> None:
    canonical_before = _canonical_controller_hash()
    if canonical_before is None:
        pytest.skip("canonical .venv is not present on this host")

    scenario = _SetupScenario(tmp_path)
    result = scenario.run()
    assert result.returncode == 0

    assert _canonical_controller_hash() == canonical_before
    fake_controller = tmp_path / ".venv" / "bin" / "framenest-dev"
    assert fake_controller.read_text(encoding="utf-8") == FAKE_CONTROLLER_STUB
    assert fake_controller.resolve().is_relative_to(tmp_path)


def test_owned_tree_cleanup_is_idempotent_and_contained(tmp_path: Path) -> None:
    owned_root = tmp_path / "owned"
    scenario = _SetupScenario(owned_root)
    result = scenario.run()
    assert result.returncode == 0
    assert (owned_root / ".venv" / "bin" / "framenest-dev").exists()

    _remove_owned_tree(owned_root, owned_root)
    assert not owned_root.exists()
    # Idempotent: a second cleanup of the same owned root is a no-op.
    _remove_owned_tree(owned_root, owned_root)
    assert not owned_root.exists()


def test_owned_tree_cleanup_refuses_escape_outside_root(tmp_path: Path) -> None:
    owned_root = tmp_path / "owned"
    owned_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep\n", encoding="utf-8")

    with pytest.raises(ValueError):
        _remove_owned_tree(outside, owned_root)
    with pytest.raises(ValueError):
        _remove_owned_tree(tmp_path, owned_root)
    assert marker.read_text(encoding="utf-8") == "keep\n"


def test_owned_tree_cleanup_rejects_symlink_escape(tmp_path: Path) -> None:
    owned_root = tmp_path / "owned"
    owned_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep\n", encoding="utf-8")
    link = owned_root / "link"
    link.symlink_to(outside)

    with pytest.raises(ValueError):
        _remove_owned_tree(link, owned_root)
    assert marker.read_text(encoding="utf-8") == "keep\n"
    # Removing the owned root itself removes only the link, never the target.
    _remove_owned_tree(owned_root, owned_root)
    assert marker.read_text(encoding="utf-8") == "keep\n"


def test_cleanup_after_partial_fixture_creation(tmp_path: Path) -> None:
    owned_root = tmp_path / "owned"
    scenario = _SetupScenario(owned_root)
    # Simulate a failure before full fixture creation: only the launcher copy
    # and an empty bin directory exist.
    owned_root.mkdir()
    shutil.copy2(ROOT_WRAPPER, scenario.launcher)
    scenario.bin_dir.mkdir()

    _remove_owned_tree(owned_root, owned_root)

    assert not owned_root.exists()
    _assert_no_scenario_paths(_real_fish_user_paths(), str(tmp_path))


def test_real_poetry_remains_resolvable_after_scenario(tmp_path: Path) -> None:
    scenario = _SetupScenario(tmp_path)
    result = scenario.run()
    assert result.returncode == 0

    resolved = shutil.which("poetry")
    if resolved is None:
        pytest.skip("real poetry is not installed on this host")
    assert SCENARIO_TMP_MARKER not in resolved
    version = subprocess.run(
        [resolved, "--version"],
        check=False,
        text=True,
        capture_output=True,
    )
    assert version.returncode == 0


def test_project_console_entries_match_packaged_metadata() -> None:
    if not CANONICAL_VENV_BIN.is_dir():
        pytest.skip("canonical .venv is not present on this host")
    import tomllib

    metadata = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    script_names = sorted(metadata["project"]["scripts"])

    assert script_names, "packaged console scripts must exist"
    # Installed console entry points must invoke the canonical project venv
    # Python runtime. The venv exposes that interpreter under both "python" and
    # "python3" (python3 is a symlink to python); either name is equivalent.
    python_link = CANONICAL_VENV_BIN / "python"
    python3_link = CANONICAL_VENV_BIN / "python3"
    if python_link.is_symlink() and python3_link.is_symlink() and python_link.resolve() == python3_link.resolve():
        allowed_shebangs = {
            f"#!{CANONICAL_VENV_BIN}/python",
            f"#!{CANONICAL_VENV_BIN}/python3",
        }
    else:
        allowed_shebangs = {f"#!{CANONICAL_VENV_BIN}/python"}
    for name in script_names:
        script = CANONICAL_VENV_BIN / name
        assert script.is_file(), f"missing console script: {name}"
        content = script.read_text(encoding="utf-8")
        assert content.splitlines()[0] in allowed_shebangs
        assert content != FAKE_CONTROLLER_STUB


def test_cli_help_and_command_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as root_help:
        cli.main(["--help"])
    with pytest.raises(SystemExit) as command_help:
        cli.main(["start", "--help"])

    assert root_help.value.code == 0
    assert command_help.value.code == 0
    assert "start" in capsys.readouterr().out


def test_cli_invalid_command_exits_with_usage_code() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["server_start"])

    assert exc_info.value.code == cli.EXIT_USAGE


def test_cli_start_no_open_and_restart_no_open_delegate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "DevelopmentRuntime", _Runtime)

    assert cli.main(["start", "--no-open"]) == cli.EXIT_OK
    assert _Runtime.last is not None
    assert _Runtime.last.open_after_start == [False]
    assert cli.main(["restart", "--no-open"]) == cli.EXIT_OK
    assert _Runtime.last is not None
    assert _Runtime.last.open_after_start == [False]


def test_cli_status_open_and_logs(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "DevelopmentRuntime", _Runtime)

    assert cli.main(["status"]) == cli.EXIT_OK
    assert cli.main(["open"]) == cli.EXIT_OK
    assert cli.main(["logs", "--lines", "1"]) == cli.EXIT_OK

    output = capsys.readouterr().out
    assert "Status: running" in output
    assert f"URL: {_RUNNING_URL}" in output
    # The status message is now part of what this test proves, so the fake at
    # `_Runtime.status` is read rather than merely printed.
    assert expected("{brand} is running at {url}", url=_RUNNING_URL) in output
    assert "opened" in output
    assert "two" in output


def test_cli_missing_log_is_clean(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    class MissingLogRuntime(_Runtime):
        def read_log_tail(self, *, lines: int) -> list[str]:
            return []

    monkeypatch.setattr(cli, "DevelopmentRuntime", MissingLogRuntime)

    assert cli.main(["logs"]) == cli.EXIT_OK
    assert capsys.readouterr().out == expected(
        "{brand} development log is not yet available."
    ) + "\n"


def test_cli_maps_stopped_unhealthy_and_conflict_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StoppedRuntime(_Runtime):
        def status(self) -> RuntimeStatus:
            return RuntimeStatus("stopped", None, None, "uninitialized", False, "stopped")

    class UnhealthyRuntime(_Runtime):
        def start(self, *, open_after_start: bool) -> Any:
            return _result(False, "unhealthy", "not healthy")

    class ConflictRuntime(_Runtime):
        def start(self, *, open_after_start: bool) -> Any:
            return _result(False, "conflict", "conflict")

    monkeypatch.setattr(cli, "DevelopmentRuntime", StoppedRuntime)
    assert cli.main(["status"]) == cli.EXIT_STOPPED
    monkeypatch.setattr(cli, "DevelopmentRuntime", UnhealthyRuntime)
    assert cli.main(["start"]) == cli.EXIT_UNHEALTHY
    monkeypatch.setattr(cli, "DevelopmentRuntime", ConflictRuntime)
    assert cli.main(["start"]) == cli.EXIT_CONFLICT


def test_cli_sanitizes_runtime_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class BrokenRuntime:
        def __init__(self) -> None:
            raise cli.DevelopmentRuntimeError("sanitized failure")

    monkeypatch.setattr(cli, "DevelopmentRuntime", BrokenRuntime)

    assert cli.main(["status"]) == cli.EXIT_ERROR
    assert capsys.readouterr().err == expected("{brand} launcher error: ") + "sanitized failure\n"


def test_cli_import_has_no_runtime_side_effects() -> None:
    command = [
        sys.executable,
        "-c",
        "import framenest.adapters.cli.development; print('imported')",
    ]

    result = subprocess.run(command, check=True, text=True, capture_output=True)

    assert result.stdout == "imported\n"
