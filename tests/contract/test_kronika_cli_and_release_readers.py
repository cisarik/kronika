"""Contract tests for ADR-0085 CLI fail-closed and release-marker readers.

The release helper and the AI credential helper are standard-library-only engines
that run without the application package on ``sys.path``. These tests prove their
local mirror of the resolver fails closed with exit status 2 and the two variable
names only, and that release markers and manifest keys are read under both
accepted spellings while their writers stay unchanged.

The second part covers every in-package command line entry point. One
identity-environment conflict must exit 2 everywhere it can surface, name the two
variable suffixes only, and change no other exit status. The enumeration is
derived from the declared console scripts, so a newly declared script fails the
classification test instead of escaping the ledger.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RELEASE_HELPER = REPOSITORY_ROOT / "deploy" / "ubuntu" / "kronika_release.py"
LEGACY_RELEASE_HELPER = REPOSITORY_ROOT / "deploy" / "ubuntu" / "framenest_release.py"
AI_DEPLOY_HELPER = REPOSITORY_ROOT / "deploy" / "ubuntu" / "production_ai_deploy.py"

SUFFIX = "NUC_SSH_TARGET"
PRIMARY = "KRONIKA_"
COMPATIBLE = "FRAMENEST_"


def _load_helper(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def release_helper() -> Any:
    return _load_helper("_c1_release_helper", RELEASE_HELPER)


@pytest.fixture(scope="module")
def ai_deploy_helper() -> Any:
    return _load_helper("_c1_ai_deploy_helper", AI_DEPLOY_HELPER)


# ---------------------------------------------------------------------------
# Standard-library-only resolver mirrors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("helper_name", ["release_helper", "ai_deploy_helper"])
def test_both_helpers_mirror_the_same_prefix_pair(
    helper_name: str,
    request: pytest.FixtureRequest,
) -> None:
    helper = request.getfixturevalue(helper_name)

    assert helper.IDENTITY_ENVIRONMENT_PREFIX == PRIMARY
    assert helper.COMPATIBLE_ENVIRONMENT_PREFIX == COMPATIBLE


def test_release_helper_resolves_either_prefix(release_helper: Any) -> None:
    assert release_helper.lookup_env(SUFFIX, environ={f"{PRIMARY}{SUFFIX}": "a"}) == "a"
    assert release_helper.lookup_env(SUFFIX, environ={f"{COMPATIBLE}{SUFFIX}": "b"}) == "b"
    assert release_helper.lookup_env(SUFFIX, environ={}) is None
    assert (
        release_helper.lookup_env(
            SUFFIX, environ={f"{PRIMARY}{SUFFIX}": "c", f"{COMPATIBLE}{SUFFIX}": "c"}
        )
        == "c"
    )
    assert release_helper.lookup_env(SUFFIX, environ={f"{PRIMARY}{SUFFIX}": ""}) is None


def test_release_helper_conflict_fails_closed_with_status_two(release_helper: Any) -> None:
    with pytest.raises(release_helper.ReleaseError) as excinfo:
        release_helper.lookup_env(
            SUFFIX, environ={f"{PRIMARY}{SUFFIX}": "alpha", f"{COMPATIBLE}{SUFFIX}": "beta"}
        )

    error = excinfo.value
    assert error.exit_code == 2
    rendered = str(error)
    assert f"{PRIMARY}{SUFFIX}" in rendered
    assert f"{COMPATIBLE}{SUFFIX}" in rendered
    assert "alpha" not in rendered
    assert "beta" not in rendered


def test_release_helper_transport_conflict_is_reported_not_raised_as_traceback(
    release_helper: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argparse

    monkeypatch.setenv(f"{PRIMARY}{SUFFIX}", "alpha-host")
    monkeypatch.setenv(f"{COMPATIBLE}{SUFFIX}", "beta-host")
    args = argparse.Namespace(target=None, user=None, identity="/nonexistent")

    with pytest.raises(release_helper.ReleaseError) as excinfo:
        release_helper._resolve_transport(args)

    assert excinfo.value.exit_code == 2
    assert "alpha-host" not in str(excinfo.value)
    assert "beta-host" not in str(excinfo.value)


def test_ai_deploy_helper_conflict_exits_two(
    ai_deploy_helper: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(f"{PRIMARY}{ai_deploy_helper.SSH_TARGET_ENVIRONMENT_SUFFIX}", "alpha-host")
    monkeypatch.setenv(
        f"{COMPATIBLE}{ai_deploy_helper.SSH_TARGET_ENVIRONMENT_SUFFIX}", "beta-host"
    )
    argv = [
        "--expected-hostname",
        "nuc",
        "--provider",
        "nvidia-nim",
        "--model",
        "example-model",
        "--credential-file",
        "/nonexistent",
    ]

    exit_code = ai_deploy_helper.main(argv)
    captured = capsys.readouterr()

    assert exit_code == 2
    suffix = ai_deploy_helper.SSH_TARGET_ENVIRONMENT_SUFFIX
    assert f"{PRIMARY}{suffix}" in captured.err
    assert f"{COMPATIBLE}{suffix}" in captured.err
    assert "alpha-host" not in captured.err
    assert "beta-host" not in captured.err
    assert "alpha-host" not in captured.out
    assert "beta-host" not in captured.out


def test_ai_deploy_helper_accepts_either_prefix_alone(
    ai_deploy_helper: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = ai_deploy_helper.SSH_TARGET_ENVIRONMENT_SUFFIX
    monkeypatch.setenv(f"{PRIMARY}{suffix}", "alpha-host")

    assert ai_deploy_helper.lookup_env(suffix) == "alpha-host"

    monkeypatch.delenv(f"{PRIMARY}{suffix}")
    monkeypatch.setenv(f"{COMPATIBLE}{suffix}", "beta-host")

    assert ai_deploy_helper.lookup_env(suffix) == "beta-host"


# ---------------------------------------------------------------------------
# Release markers and manifest keys
# ---------------------------------------------------------------------------


def test_release_markers_and_manifest_keys_keep_their_writer_spelling(
    release_helper: Any,
) -> None:
    assert release_helper.RELEASE_SHA_MARKER == ".framenest-release-sha"
    assert release_helper.RELEASE_MANIFEST_MARKER == ".framenest-release-manifest.json"
    assert release_helper.RELEASE_SHA_MANIFEST_KEY == "framenest_release_sha"
    assert release_helper.make_manifest(
        release_sha="0" * 40,
        ap_pin="1" * 40,
        superproject_sha256="a" * 64,
        ap_archive_sha256="b" * 64,
        capture_code_tree="2" * 40,
        capture_runtime_contract_sha256="c" * 64,
        capture_unit_contract_sha256="d" * 64,
        capture_bridge_protocol=release_helper.CAPTURE_BRIDGE_PROTOCOL,
    )["framenest_release_sha"] == "0" * 40


def test_every_reader_resolves_through_the_accepted_marker_tables(
    release_helper: Any,
) -> None:
    """The accepted tables are frozen data, and the writers are members of them.

    A reader therefore keeps resolving a historical tree even after the writer
    cut removes the former writer constants, and the writer spelling can never
    fall out of the table a reader resolves through.
    """
    for writer, accepted in (
        (release_helper.RELEASE_SHA_MARKER, release_helper.ACCEPTED_RELEASE_SHA_MARKERS),
        (
            release_helper.RELEASE_MANIFEST_MARKER,
            release_helper.ACCEPTED_RELEASE_MANIFEST_MARKERS,
        ),
    ):
        assert writer in accepted
        assert len(accepted) == 2
    assert (
        release_helper.RELEASE_SHA_MANIFEST_KEY
        in release_helper.ACCEPTED_RELEASE_SHA_MANIFEST_KEYS
    )
    assert (
        "framenest_release_sha" in release_helper.ACCEPTED_RELEASE_SHA_MANIFEST_KEYS
        and "kronika_release_sha" in release_helper.ACCEPTED_RELEASE_SHA_MANIFEST_KEYS
    )


def test_release_marker_presence_probes_every_accepted_spelling(
    release_helper: Any,
) -> None:
    command = release_helper.cmd_remote_release_marker_presence("/opt/x")

    for marker in (
        *release_helper.ACCEPTED_RELEASE_MANIFEST_MARKERS,
        *release_helper.ACCEPTED_RELEASE_SHA_MARKERS,
    ):
        assert marker in command
    assert command.count("test -e") == 4
    assert "then echo manifest" in command
    assert "then echo sha" in command
    # Manifest before SHA, and each group in the accepted order.
    assert command.index(release_helper.ACCEPTED_RELEASE_MANIFEST_MARKERS[0]) < command.index(
        release_helper.ACCEPTED_RELEASE_MANIFEST_MARKERS[1]
    )
    assert command.index(release_helper.ACCEPTED_RELEASE_SHA_MARKERS[0]) < command.index(
        release_helper.ACCEPTED_RELEASE_SHA_MARKERS[1]
    )
    assert command.index(release_helper.ACCEPTED_RELEASE_MANIFEST_MARKERS[1]) < command.index(
        release_helper.ACCEPTED_RELEASE_SHA_MARKERS[0]
    )


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("", ([], [])),
        ("sha .framenest-release-sha", ([], [".framenest-release-sha"])),
        ("sha .kronika-release-sha", ([], [".kronika-release-sha"])),
        (
            "manifest .kronika-release-manifest.json\nsha .framenest-release-sha",
            ([".kronika-release-manifest.json"], [".framenest-release-sha"]),
        ),
        ("none\n", None),
        ("sha .unknown-release-sha", None),
        ("marker .framenest-release-sha", None),
    ],
)
def test_release_marker_presence_parser_is_closed(
    release_helper: Any, output: str, expected: object
) -> None:
    if expected is None:
        with pytest.raises(release_helper.ReleaseError):
            release_helper.parse_release_marker_presence(output)
        return
    assert release_helper.parse_release_marker_presence(output) == expected


def test_release_read_commands_accept_both_spellings(release_helper: Any) -> None:
    assert ".framenest-release-sha" in release_helper.cmd_remote_read_release_sha("/opt/x")
    assert (
        ".kronika-release-sha"
        in release_helper.cmd_remote_read_release_sha("/opt/x", ".kronika-release-sha")
    )
    assert (
        ".framenest-release-manifest.json"
        in release_helper.cmd_remote_read_manifest("/opt/x")
    )
    assert (
        ".kronika-release-manifest.json"
        in release_helper.cmd_remote_read_manifest(
            "/opt/x", ".kronika-release-manifest.json"
        )
    )


@pytest.mark.parametrize(
    "manifest",
    [
        {"framenest_release_sha": "a" * 40},
        {"kronika_release_sha": "a" * 40},
    ],
)
def test_manifest_release_sha_is_read_under_both_keys(
    release_helper: Any,
    manifest: dict,
) -> None:
    assert release_helper.manifest_release_sha(manifest) == "a" * 40


def test_manifest_release_sha_absent_key_returns_none(release_helper: Any) -> None:
    assert release_helper.manifest_release_sha({"other": 1}) is None
    assert release_helper.manifest_release_sha("not-a-mapping") is None


def test_release_helper_still_injects_the_old_env_file_name(release_helper: Any) -> None:
    prefix = release_helper.service_account_prefix("/opt/framenest/releases/abc")

    assert f"env {COMPATIBLE}ENV_FILE={release_helper.ENV_FILE}" in prefix
    assert f"{PRIMARY}ENV_FILE" not in prefix


def test_release_helper_env_file_path_constant_is_unchanged(release_helper: Any) -> None:
    assert release_helper.ENV_FILE == "/etc/framenest/framenest.env"
    assert release_helper.RELEASE_ROOT == "/opt/framenest/releases"
    assert release_helper.SERVICE == "framenest.service"


def test_protocol_magic_and_version_reads_are_unchanged() -> None:
    from importlib.metadata import version

    from kronika.infrastructure.persistence.catalog_backup_transfer import PROTOCOL_MAGIC

    assert PROTOCOL_MAGIC == b"FNCBE01\0"
    assert isinstance(version("kronika"), str)


def test_development_database_directory_is_unchanged() -> None:
    from kronika.configuration import DEVELOPMENT_DATABASE_DIRECTORY

    assert DEVELOPMENT_DATABASE_DIRECTORY == "framenest-development"


def test_emitted_cli_error_codes_stay_on_the_former_prefix() -> None:
    from kronika.adapters.cli.backup import COMMAND_FAILED_CODE, INVALID_INPUT_CODE
    from kronika.infrastructure.persistence.cli import (
        COMMAND_ERROR_CODE,
        CONFIGURATION_ERROR_CODE,
    )
    from kronika.infrastructure.runtime.production import (
        DATABASE_NOT_READY_CODE,
        HEALTH_CHECK_FAILED_CODE,
    )

    codes = (
        COMMAND_FAILED_CODE,
        INVALID_INPUT_CODE,
        COMMAND_ERROR_CODE,
        CONFIGURATION_ERROR_CODE,
        DATABASE_NOT_READY_CODE,
        HEALTH_CHECK_FAILED_CODE,
    )
    assert all(code.startswith("FRAMENEST_") for code in codes)
    assert not any("KRONIKA_" in code for code in codes)


def test_catalog_cli_error_codes_stay_on_the_former_prefix() -> None:
    from kronika.adapters.cli import catalog

    codes = [
        value
        for name, value in vars(catalog).items()
        if name.endswith("_CODE") and isinstance(value, str)
    ]
    assert codes
    assert all(code.startswith("FRAMENEST_") for code in codes)


def test_json_manifest_key_reader_is_used_for_the_current_release_fallback(
    release_helper: Any,
) -> None:
    payload = json.loads(json.dumps({"kronika_release_sha": "b" * 40}))

    assert release_helper.manifest_release_sha(payload) == "b" * 40


# ---------------------------------------------------------------------------
# In-package entry points: one uniform fail-closed exit status
# ---------------------------------------------------------------------------

#: One setting-name suffix every entry point below resolves, so a single
#: conflicting pair is enough to exercise each command's own settings or
#: operator-configuration reader.
CONFLICT_SUFFIX = "DATABASE_PATH"
CONFLICT_PRIMARY_VALUE = "alpha-conflict-value"
CONFLICT_COMPATIBLE_VALUE = "beta-conflict-value"

CONFLICT_SENTENCE_MARKER = "Conflicting environment variables "
CONFLICT_SENTENCE_END = "are set to different values."

#: Symbols that would let a module reach the dual-prefix reader. A module that
#: carries none of them cannot surface the conflict, whatever its arguments are.
#: ``Settings`` stands for the settings class, whose full name repeats the
#: product spelling this repository still counts occurrence by occurrence.
SETTINGS_SURFACE_SYMBOLS = (
    "load_settings",
    "Settings",
    "IdentityEnvironmentConflictError",
    "IdentityEnvironmentConfigurationError",
    "lookup_env",
    "identity_env",
    "load_catalog_backup_ops_config",
    "default_ai_config_path",
)

DECLARED_CONSOLE_SCRIPTS: dict[str, str] = tomllib.loads(
    (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
)["project"]["scripts"]

#: Entry-point module paths, under the distribution package, that construct
#: settings or operator configuration and therefore can surface the conflict.
#: Each case states the exact arguments used and the ordinary exit status of the
#: same arguments without a conflict, so a status change fails here too.
ENTRY_POINT_CASES: dict[str, dict[str, Any]] = {
    "server": {
        "arguments": (),
        # The ordinary path of this entry point is a long-running server, so the
        # non-conflicting run uses an unreadable environment file instead, which
        # is its ordinary configuration failure.
        "normal_environment": (("{COMPATIBLE}ENV_FILE", "{TMP}/absent.env"),),
        "normal_status": 1,
    },
    "infrastructure.persistence.cli": {
        "arguments": ("status",),
        "normal_status": 0,
    },
    "infrastructure.runtime.production": {
        "arguments": ("check-database-ready",),
        "normal_status": 4,
    },
    "adapters.cli.ai": {
        "arguments": ("--config-path", "{TMP}/ai.json", "status", "--no-write"),
        "normal_status": 0,
    },
    "adapters.cli.backup": {
        "arguments": ("status",),
        "environment": (
            ("{COMPATIBLE}CATALOG_BACKUP_ROOT", "{TMP}/backups"),
            ("{COMPATIBLE}CATALOG_BACKUP_OPS_ROOT", "{TMP}/ops"),
            ("{COMPATIBLE}CATALOG_RESTORE_VERIFY_ROOT", "{TMP}/restore"),
        ),
        "normal_status": 0,
    },
    "adapters.cli.catalog": {
        "arguments": ("device", "list"),
        "normal_status": 4,
    },
    "adapters.cli.covers": {
        "arguments": ("status",),
        "normal_status": 4,
    },
    "adapters.cli.development": {
        "arguments": ("status",),
        "environment": (("{COMPATIBLE}DEVELOPMENT_RUNTIME_DIR", "{TMP}/dev"),),
        "normal_status": 3,
    },
    "adapters.cli.library": {
        "arguments": ("status",),
        "normal_status": 4,
    },
    "adapters.cli.previews": {
        "arguments": ("status",),
        "normal_status": 4,
    },
    "adapters.cli.sidecar": {
        "arguments": (
            "export",
            "--media-id",
            "12345678-1234-4234-9234-123456789abc",
            "--location-id",
            "abcdefab-cdef-4abc-8def-abcdefabcdef",
        ),
        "normal_status": 1,
    },
    "adapters.cli.youtube": {
        "arguments": ("status", "12345678-1234-4234-9234-123456789abc"),
        "normal_status": 5,
    },
}

#: The one declared entry point that resolves no setting name, so a conflicting
#: pair cannot change its status.
SETTINGS_FREE_ENTRY_POINT = "adapters.cli.recovery"

#: Both parked capture scripts target ``kronika_capture.cli``, whose module path
#: under the distribution package is therefore just ``cli``.
CAPTURE_MODULE_KEY = "cli"
CAPTURE_SOURCE_ROOT = REPOSITORY_ROOT / "src" / "kronika_capture"

INVALID_ARGUMENTS = ("not-a-declared-command",)

#: Cases whose entry point takes arguments, and therefore has an existing
#: invalid-command status that the conflict handler must not claim. The
#: server entry point takes none and is absent by construction.
INVALID_COMMAND_CASES = tuple(
    sorted(key for key, case in ENTRY_POINT_CASES.items() if case["arguments"])
)


def _module_key(target: str) -> str:
    """Return the entry-point module path under its distribution package."""
    return target.partition(":")[0].split(".", 1)[-1]


def _script_name(module_key: str) -> str:
    """Return the declared console script that owns one entry-point module."""
    for name, target in DECLARED_CONSOLE_SCRIPTS.items():
        if _module_key(target) == module_key:
            return name
    raise AssertionError(f"no declared console script for {module_key}")


def _expand(values: tuple[str, ...], tmp_path: Path) -> tuple[str, ...]:
    return tuple(
        value.format(TMP=tmp_path, PRIMARY=PRIMARY, COMPATIBLE=COMPATIBLE)
        for value in values
    )


def _environment(entries: tuple[tuple[str, str], ...], tmp_path: Path) -> dict[str, str]:
    return {
        name.format(PRIMARY=PRIMARY, COMPATIBLE=COMPATIBLE): value.format(
            TMP=tmp_path, PRIMARY=PRIMARY, COMPATIBLE=COMPATIBLE
        )
        for name, value in entries
    }


def _isolated_environment(
    tmp_path: Path,
    *,
    extra: dict[str, str],
    conflicting: bool,
    database_path: Path,
) -> dict[str, str]:
    """Return a sanitized environment for one console-script run.

    Every accepted-prefix variable is removed first, so an inherited value from
    the surrounding run cannot decide the result.
    """
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith((PRIMARY, COMPATIBLE))
    }
    if conflicting:
        environment[f"{PRIMARY}{CONFLICT_SUFFIX}"] = CONFLICT_PRIMARY_VALUE
        environment[f"{COMPATIBLE}{CONFLICT_SUFFIX}"] = CONFLICT_COMPATIBLE_VALUE
    else:
        environment[f"{COMPATIBLE}{CONFLICT_SUFFIX}"] = str(database_path)
    environment.update(extra)
    return environment


def _run_entry_point(
    script_name: str,
    arguments: tuple[str, ...],
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    script = Path(sys.executable).parent / script_name
    assert script.is_file(), f"expected installed console script at {script}"
    return subprocess.run(
        [str(script), *arguments],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _reported_conflict_sentence(combined_output: str) -> str:
    start = combined_output.index(CONFLICT_SENTENCE_MARKER)
    end = combined_output.index(CONFLICT_SENTENCE_END, start)
    return combined_output[start : end + len(CONFLICT_SENTENCE_END)]


def test_every_declared_console_script_is_classified() -> None:
    """A newly declared entry point must be classified, never silently skipped."""
    classified = (
        set(ENTRY_POINT_CASES)
        | {SETTINGS_FREE_ENTRY_POINT}
        | {CAPTURE_MODULE_KEY}
    )

    assert classified == {_module_key(target) for target in DECLARED_CONSOLE_SCRIPTS.values()}


def test_the_parked_capture_package_cannot_surface_the_conflict() -> None:
    """The capture package resolves no setting name, so it cannot reach exit 2."""
    offenders = sorted(
        path.relative_to(CAPTURE_SOURCE_ROOT).as_posix()
        for path in CAPTURE_SOURCE_ROOT.rglob("*.py")
        if any(
            symbol in path.read_text(encoding="utf-8", errors="replace")
            for symbol in SETTINGS_SURFACE_SYMBOLS
        )
    )

    assert offenders == []


@pytest.mark.parametrize("module_key", sorted(ENTRY_POINT_CASES))
def test_identity_conflict_exits_two_and_discloses_no_value(
    module_key: str,
    tmp_path: Path,
) -> None:
    """One conflicting pair exits 2 at every entry point that can surface it."""
    case = ENTRY_POINT_CASES[module_key]
    script_name = _script_name(module_key)
    arguments = _expand(case["arguments"], tmp_path)

    result = _run_entry_point(
        script_name,
        arguments,
        _isolated_environment(
            tmp_path,
            extra=_environment(case.get("environment", ()), tmp_path),
            conflicting=True,
            database_path=tmp_path / "catalog.sqlite3",
        ),
    )
    combined = result.stdout + result.stderr

    assert result.returncode == 2, f"{script_name}: {combined}"
    sentence = _reported_conflict_sentence(combined)
    assert f"{PRIMARY}{CONFLICT_SUFFIX}" in sentence
    assert f"{COMPATIBLE}{CONFLICT_SUFFIX}" in sentence
    assert "Traceback" not in combined
    for value in (CONFLICT_PRIMARY_VALUE, CONFLICT_COMPATIBLE_VALUE):
        assert value not in combined
        assert value not in sentence
        assert str(len(value)) not in sentence
        assert hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] not in sentence
        assert repr(value) not in sentence


@pytest.mark.parametrize("module_key", sorted(ENTRY_POINT_CASES))
def test_ordinary_status_of_the_same_arguments_is_unchanged(
    module_key: str,
    tmp_path: Path,
) -> None:
    """The same arguments without a conflict keep their documented status."""
    case = ENTRY_POINT_CASES[module_key]
    script_name = _script_name(module_key)
    arguments = _expand(case["arguments"], tmp_path)
    extra = dict(case.get("environment", ())) | dict(case.get("normal_environment", ()))

    result = _run_entry_point(
        script_name,
        arguments,
        _isolated_environment(
            tmp_path,
            extra=_environment(tuple(extra.items()), tmp_path),
            conflicting=False,
            database_path=tmp_path / "catalog.sqlite3",
        ),
    )
    combined = result.stdout + result.stderr

    assert result.returncode == case["normal_status"], f"{script_name}: {combined}"
    assert CONFLICT_SENTENCE_MARKER not in combined
    assert "Traceback" not in combined


@pytest.mark.parametrize("module_key", INVALID_COMMAND_CASES)
def test_invalid_command_status_is_identical_with_and_without_the_conflict(
    module_key: str,
    tmp_path: Path,
) -> None:
    """An existing failure status is not claimed by the conflict handler."""
    script_name = _script_name(module_key)

    ordinary = _run_entry_point(
        script_name,
        INVALID_ARGUMENTS,
        _isolated_environment(
            tmp_path,
            extra={},
            conflicting=False,
            database_path=tmp_path / "catalog.sqlite3",
        ),
    )
    conflicting = _run_entry_point(
        script_name,
        INVALID_ARGUMENTS,
        _isolated_environment(
            tmp_path,
            extra={},
            conflicting=True,
            database_path=tmp_path / "catalog.sqlite3",
        ),
    )

    assert ordinary.returncode == conflicting.returncode
    assert CONFLICT_SENTENCE_MARKER not in conflicting.stdout + conflicting.stderr


def test_the_settings_free_entry_point_keeps_its_status_under_a_conflict(
    tmp_path: Path,
) -> None:
    """An entry point that resolves no setting name is inert here."""
    script_name = _script_name(SETTINGS_FREE_ENTRY_POINT)
    arguments = (
        "list",
        "--store-root",
        str(tmp_path / "store"),
        "--mount-root",
        str(tmp_path),
        "--expected-store-id",
        "0" * 32,
    )

    ordinary = _run_entry_point(
        script_name,
        arguments,
        _isolated_environment(
            tmp_path,
            extra={},
            conflicting=False,
            database_path=tmp_path / "catalog.sqlite3",
        ),
    )
    conflicting = _run_entry_point(
        script_name,
        arguments,
        _isolated_environment(
            tmp_path,
            extra={},
            conflicting=True,
            database_path=tmp_path / "catalog.sqlite3",
        ),
    )

    assert ordinary.returncode == conflicting.returncode == 1
    assert CONFLICT_SENTENCE_MARKER not in conflicting.stdout + conflicting.stderr


def test_an_empty_primary_value_still_behaves_as_unset_at_an_entry_point(
    tmp_path: Path,
) -> None:
    """The correction cannot turn the empty-string rule into a conflict."""
    script_name = _script_name("infrastructure.persistence.cli")

    result = _run_entry_point(
        script_name,
        ("status",),
        _isolated_environment(
            tmp_path,
            extra={f"{PRIMARY}{CONFLICT_SUFFIX}": ""},
            conflicting=False,
            database_path=tmp_path / "catalog.sqlite3",
        ),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert CONFLICT_SENTENCE_MARKER not in result.stdout + result.stderr


def test_identical_values_in_both_prefixes_still_succeed_at_an_entry_point(
    tmp_path: Path,
) -> None:
    """The correction cannot turn the identical-value rule into a failure."""
    script_name = _script_name("infrastructure.persistence.cli")
    database_path = str(tmp_path / "catalog.sqlite3")

    result = _run_entry_point(
        script_name,
        ("status",),
        _isolated_environment(
            tmp_path,
            extra={f"{PRIMARY}{CONFLICT_SUFFIX}": database_path},
            conflicting=False,
            database_path=tmp_path / "catalog.sqlite3",
        ),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert CONFLICT_SENTENCE_MARKER not in result.stdout + result.stderr
