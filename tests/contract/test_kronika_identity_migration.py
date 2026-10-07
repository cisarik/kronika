"""C4-B contracts: canonical artifacts, layout selection, typed environment
transformation, and the simulated-host identity migration machinery.

These tests are hermetic. They never contact a host, never execute
``migrate-identity apply`` through the real CLI, and never read a credential
value. The production host adapter is exercised only through command builders
and through a fake command runner; the phase machinery is driven by an
in-memory simulated host with failure injection.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import socket
import sys
import time

import pytest

from kronika import configuration as configuration_module

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ENGINE_PATH = REPOSITORY_ROOT / "deploy" / "ubuntu" / "kronika_release.py"
SYSTEMD = REPOSITORY_ROOT / "deploy" / "systemd"

_SPEC = importlib.util.spec_from_file_location("kronika_release_migration", ENGINE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
engine = importlib.util.module_from_spec(_SPEC)
sys.modules["kronika_release_migration"] = engine
_SPEC.loader.exec_module(engine)

from tests.contract.test_kronika_identity_retention import (  # noqa: E402
    EXPECTED_FRAMENEST_BASENAME_PATHS,
)

RELEASE = "a" * 40
OLD_RELEASE = f"/opt/framenest/releases/{RELEASE}"
NEW_RELEASE = f"/opt/kronika/releases/{RELEASE}"

WEB_ARTIFACT_FAMILIES = ("deploy/systemd/", "deploy/ubuntu/")

SAMPLE_ENVIRONMENT = """\
# Kronika sample environment
FRAMENEST_HOST=127.0.0.1
FRAMENEST_DATABASE_PATH=/var/lib/framenest/catalog.sqlite3
FRAMENEST_GALLERY_PREVIEW_CACHE_PATH=/var/cache/framenest/gallery-previews
FRAMENEST_COVER_STORAGE_ROOT=/var/lib/framenest/covers
FRAMENEST_COVER_THUMBNAIL_CACHE_PATH=/var/cache/framenest/cover-thumbnails
FRAMENEST_AI_CONFIG_PATH=/var/lib/framenest/ai/config.json
FRAMENEST_CATALOG_BACKUP_ROOT=/var/lib/framenest/catalog-backups
FRAMENEST_CATALOG_RESTORE_VERIFY_ROOT=/var/lib/framenest/catalog-restore-verify
FRAMENEST_CATALOG_BACKUP_OPS_ROOT=/var/lib/framenest/catalog-backup-ops
FRAMENEST_YOUTUBE_ACQUISITION_ROOT=/var/lib/framenest/youtube-acquisition
FRAMENEST_UDS_PATH=/run/framenest/framenest.sock
FRAMENEST_CATALOG_BACKUP_KEEP_AUTO=30
FRAMENEST_UPLOAD_QUARANTINE_ROOT=/srv/media/framenest-quarantine
FRAMENEST_CATALOG_OFFDEVICE_DESTINATION_ID=0123456789abcdef0123456789abcdef
FRAMENEST_EXTERNAL_ORIGIN=https://framenest.example.ts.net
# comment naming /mnt/framenest-catalog-offdevice stays as written
"""


def _tracked_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Derivation A: canonical artifacts from the retention ledger's Part B set
# ---------------------------------------------------------------------------


def _canonical_counterpart(part_b_path: str) -> str:
    parent, _, name = part_b_path.rpartition("/")
    canonical = name.replace("framenest_", "kronika_").replace("framenest", "kronika")
    return f"{parent}/{canonical}" if parent else canonical


def test_web_family_part_b_paths_have_canonical_counterparts() -> None:
    """Rule: replace the retired basename component, add the canonical file.

    Derived from the ledger's live Part B pin, never from a hand-built list.
    Every deploy/systemd and deploy/ubuntu retired-basename path must have its
    canonical counterpart present. The canonical counterpart of an artifact
    the previous cut already moved (``kronika-release``, ``kronika_release.py``)
    exists for the same reason.
    """
    measured = {
        path
        for path in EXPECTED_FRAMENEST_BASENAME_PATHS
        if path.startswith(WEB_ARTIFACT_FAMILIES)
    }
    missing = {
        _canonical_counterpart(path): path
        for path in measured
        if not (REPOSITORY_ROOT / _canonical_counterpart(path)).is_file()
    }
    assert not missing, f"canonical counterparts are missing: {missing}"


def test_every_part_b_artifact_remains_in_place() -> None:
    missing = {
        path
        for path in EXPECTED_FRAMENEST_BASENAME_PATHS
        if not (REPOSITORY_ROOT / path).is_file()
    }
    assert not missing, f"Part B moved: {missing}"


def test_canonical_web_artifact_set_matches_the_issued_sample() -> None:
    expected = {
        "deploy/systemd/kronika.service",
        "deploy/systemd/kronika-catalog-backup.service",
        "deploy/systemd/kronika-catalog-backup.timer",
        "deploy/systemd/kronika-catalog-offdevice.service",
        "deploy/systemd/kronika-catalog-offdevice.timer",
        "deploy/systemd/kronika.env.example",
        "deploy/systemd/kronika-ai-credential-nvidia-nim.conf",
        "deploy/systemd/kronika-ai-credential-opencode-go.conf",
        "deploy/systemd/kronika-ai-credential-vercel-ai-gateway.conf",
        "deploy/systemd/kronika-research-credential.conf",
        "deploy/ubuntu/kronika-catalog-export-v1",
    }
    created = {
        path for path in expected if (REPOSITORY_ROOT / path).is_file()
    }
    assert created == expected


def test_operator_script_families_are_outside_this_cuts_web_artifact_scope() -> None:
    """The infosec/mullvad diagnostics are not web host layout artifacts.

    Their canonical counterparts belong to the cut that owns the workstation
    operator surface; this cut derives only the web-side artifact set. Their
    retired spellings stay in place and Part B stays whole.
    """
    operator = {
        path
        for path in EXPECTED_FRAMENEST_BASENAME_PATHS
        if path.startswith("scripts/operator/")
    }
    assert operator
    assert all(
        (REPOSITORY_ROOT / _canonical_counterpart(path)).is_file()
        for path in operator
        if _canonical_counterpart(path) in {"scripts/operator/network/kronika_nuc_worker_gate.fish"}
    )


# ---------------------------------------------------------------------------
# Canonical artifact contents
# ---------------------------------------------------------------------------


def test_canonical_units_use_the_canonical_target_layout() -> None:
    canonical_units = (
        "kronika.service",
        "kronika-catalog-backup.service",
        "kronika-catalog-offdevice.service",
    )
    for name in canonical_units:
        text = _tracked_text(SYSTEMD / name)
        assert "User=kronika" in text
        assert "Group=kronika" in text
        assert "WorkingDirectory=/opt/kronika/current" in text
        assert "EnvironmentFile=/etc/kronika/kronika.env" in text
        assert "User=framenest" not in text
        assert "Group=framenest" not in text
        assert "/opt/framenest" not in text
        assert "/etc/framenest" not in text
        assert "StateDirectory=kronika" in text


def test_canonical_service_uses_canonical_console_scripts() -> None:
    text = _tracked_text(SYSTEMD / "kronika.service")
    assert (
        "ExecStartPre=/opt/kronika/current/.venv/bin/kronika-production "
        "check-database-ready" in text
    )
    assert (
        "ExecStart=/opt/kronika/current/.venv/bin/kronika-production serve" in text
    )
    backup = _tracked_text(SYSTEMD / "kronika-catalog-backup.service")
    assert "ExecStart=/opt/kronika/current/.venv/bin/kronika-backup run-scheduled" in backup
    offdevice = _tracked_text(SYSTEMD / "kronika-catalog-offdevice.service")
    assert "ExecStart=/opt/kronika/current/.venv/bin/kronika-backup run-offdevice" in offdevice


def test_canonical_offdevice_artifacts_keep_the_frozen_mount() -> None:
    service = _tracked_text(SYSTEMD / "kronika-catalog-offdevice.service")
    assert "/mnt/framenest-catalog-offdevice" in service
    assert "/mnt/kronika-catalog-offdevice" not in service
    environment = _tracked_text(SYSTEMD / "kronika.env.example")
    assert "/mnt/framenest-catalog-offdevice" in environment
    assert "/mnt/kronika-catalog-offdevice" not in environment


def test_canonical_environment_template_is_canonical_only() -> None:
    text = _tracked_text(SYSTEMD / "kronika.env.example")
    assert re.search(r"^\s*FRAMENEST_", text, re.MULTILINE) is None
    assert "FRAMENEST_" not in text
    assert "KRONIKA_DATABASE_PATH=/var/lib/kronika/catalog.sqlite3" in text
    assert "KRONIKA_UDS_PATH=/run/kronika/kronika.sock" in text
    assert "/var/lib/framenest" not in text
    assert "/var/cache/framenest" not in text
    assert "/run/framenest" not in text
    assert "/opt/framenest" not in text


def test_canonical_credential_dropins_name_canonical_sources() -> None:
    for name, identity in (
        ("kronika-ai-credential-nvidia-nim.conf", "NVIDIA_API_KEY"),
        ("kronika-ai-credential-opencode-go.conf", "OPENCODE_API_KEY"),
        ("kronika-ai-credential-vercel-ai-gateway.conf", "AI_GATEWAY_API_KEY"),
    ):
        text = _tracked_text(SYSTEMD / name)
        assert f"LoadCredential={identity}:/etc/kronika/credentials/{identity}" in text
    research = _tracked_text(SYSTEMD / "kronika-research-credential.conf")
    assert (
        "LoadCredential=KRONIKA_RESEARCH_OPENAI_API_KEY:"
        "/etc/kronika/credentials/research-openai" in research
    )


def test_canonical_export_launcher_uses_canonical_paths_only() -> None:
    text = _tracked_text(REPOSITORY_ROOT / "deploy" / "ubuntu" / "kronika-catalog-export-v1")
    assert "framenest" not in text.lower()
    assert "/usr/local/libexec/kronika-catalog-export-v1" in text
    assert "/opt/kronika/current/.venv/bin/kronika-backup" in text
    assert "/var/lib/kronika/catalog.sqlite3" in text
    assert "kronika-catalog-export-v1 rejects all arguments." in text
    mode = (REPOSITORY_ROOT / "deploy" / "ubuntu" / "kronika-catalog-export-v1").stat().st_mode
    assert mode & 0o111


# ---------------------------------------------------------------------------
# Derivation B: typed environment key classification
# ---------------------------------------------------------------------------


def _settings_path_field_suffixes() -> set[str]:
    suffixes: set[str] = set()
    for name, field in configuration_module.KronikaSettings.model_fields.items():
        annotation = str(field.annotation)
        if "Path" in annotation:
            suffixes.add(name.upper())
    return suffixes


def test_every_settings_path_field_is_classified_as_a_path() -> None:
    derived = _settings_path_field_suffixes()
    expected = {
        "DATABASE_PATH",
        "GALLERY_PREVIEW_CACHE_PATH",
        "COVER_STORAGE_ROOT",
        "COVER_THUMBNAIL_CACHE_PATH",
        "UPLOAD_QUARANTINE_ROOT",
        "YOUTUBE_ACQUISITION_ROOT",
        "X_ACQUISITION_ROOT",
        "UDS_PATH",
        "RUNTIME_SETTINGS_PATH",
    }
    assert derived == expected
    assert derived <= engine.IDENTITY_ENVIRONMENT_PATH_SUFFIXES


def test_every_settings_field_suffix_is_classified() -> None:
    unclassified = {
        name.upper()
        for name in configuration_module.KronikaSettings.model_fields
        if name.upper() not in engine.IDENTITY_ENVIRONMENT_PATH_SUFFIXES
        and name.upper() not in engine.IDENTITY_ENVIRONMENT_OPAQUE_SUFFIXES
    }
    assert not unclassified, f"unclassified settings suffixes: {unclassified}"


# ---------------------------------------------------------------------------
# Typed environment transformation
# ---------------------------------------------------------------------------


def test_typed_transformation_moves_every_path_class() -> None:
    transformation = engine.transform_environment_text(SAMPLE_ENVIRONMENT)
    rendered = transformation.text
    assert "FRAMENEST_" not in rendered
    assert "KRONIKA_HOST=127.0.0.1" in rendered
    assert "KRONIKA_DATABASE_PATH=/var/lib/kronika/catalog.sqlite3" in rendered
    assert "KRONIKA_GALLERY_PREVIEW_CACHE_PATH=/var/cache/kronika/gallery-previews" in rendered
    assert "KRONIKA_COVER_STORAGE_ROOT=/var/lib/kronika/covers" in rendered
    assert "KRONIKA_COVER_THUMBNAIL_CACHE_PATH=/var/cache/kronika/cover-thumbnails" in rendered
    assert "KRONIKA_AI_CONFIG_PATH=/var/lib/kronika/ai/config.json" in rendered
    assert "KRONIKA_CATALOG_BACKUP_ROOT=/var/lib/kronika/catalog-backups" in rendered
    assert "KRONIKA_CATALOG_RESTORE_VERIFY_ROOT=/var/lib/kronika/catalog-restore-verify" in rendered
    assert "KRONIKA_CATALOG_BACKUP_OPS_ROOT=/var/lib/kronika/catalog-backup-ops" in rendered
    assert "KRONIKA_YOUTUBE_ACQUISITION_ROOT=/var/lib/kronika/youtube-acquisition" in rendered
    assert "KRONIKA_UDS_PATH=/run/kronika/kronika.sock" in rendered
    assert "KRONIKA_CATALOG_BACKUP_KEEP_AUTO=30" in rendered
    assert "KRONIKA_CATALOG_OFFDEVICE_DESTINATION_ID=0123456789abcdef0123456789abcdef" in rendered
    assert "https://framenest.example.ts.net" in rendered
    assert "# comment naming /mnt/framenest-catalog-offdevice stays as written" in rendered


def test_typed_transformation_preserves_custom_and_frozen_paths() -> None:
    assert (
        engine.transform_path_value("/srv/media/framenest-quarantine")
        == "/srv/media/framenest-quarantine"
    )
    assert (
        engine.transform_path_value("/mnt/framenest-catalog-offdevice/bundles")
        == "/mnt/framenest-catalog-offdevice/bundles"
    )
    assert (
        engine.transform_path_value(
            "/opt/framenest/tooling/poetry/2.4.1/.venv/bin/poetry"
        )
        == "/opt/framenest/tooling/poetry/2.4.1/.venv/bin/poetry"
    )
    assert (
        engine.transform_path_value("/var/lib/kronika/catalog.sqlite3")
        == "/var/lib/kronika/catalog.sqlite3"
    )


def test_typed_transformation_is_idempotent() -> None:
    once = engine.transform_environment_text(SAMPLE_ENVIRONMENT)
    twice = engine.transform_environment_text(once.text)
    assert twice.text == once.text
    assert engine.environment_transformation_is_idempotent(once)


def test_unclassified_identity_key_fails_closed() -> None:
    with pytest.raises(engine.ReleaseError) as exc:
        engine.transform_environment_text("FRAMENEST_FUTURE_UNKNOWN_KEY=value\n")
    assert exc.value.exit_code == engine.EXIT_MIGRATION
    assert "FUTURE_UNKNOWN_KEY" in str(exc.value)


def test_opaque_value_containing_the_token_is_untouched() -> None:
    transformation = engine.transform_environment_text(
        "FRAMENEST_EXTERNAL_ORIGIN=https://framenest.example\n"
        "FRAMENEST_CATALOG_OFFDEVICE_DESTINATION_ID=abc-framenest-def\n"
    )
    assert (
        "KRONIKA_EXTERNAL_ORIGIN=https://framenest.example" in transformation.text
    )
    assert (
        "KRONIKA_CATALOG_OFFDEVICE_DESTINATION_ID=abc-framenest-def"
        in transformation.text
    )


# ---------------------------------------------------------------------------
# Fail-closed layout selection and the three transition states
# ---------------------------------------------------------------------------


def _reading(
    layout: engine.WebLayout,
    *,
    load_state: str = "loaded",
    active_state: str = "active",
    unit_file_state: str = "enabled",
    user: str | None = None,
    group: str | None = None,
    working_directory: str | None = None,
    executable: str | None = None,
) -> engine.WebLayoutReading:
    return engine.parse_web_layout_probe(
        _probe_answer(
            layout,
            load_state=load_state,
            active_state=active_state,
            unit_file_state=unit_file_state,
            user=user,
            group=group,
            working_directory=working_directory,
            executable=executable,
        ),
        layout,
    )


def _probe_answer(
    layout: engine.WebLayout,
    *,
    load_state: str = "loaded",
    active_state: str = "active",
    unit_file_state: str = "enabled",
    user: str | None = None,
    group: str | None = None,
    working_directory: str | None = None,
    executable: str | None = None,
) -> str:
    return (
        f"LoadState={load_state}\n"
        f"ActiveState={active_state}\n"
        f"UnitFileState={unit_file_state}\n"
        f"User={user if user is not None else layout.user}\n"
        f"Group={group if group is not None else layout.group}\n"
        f"WorkingDirectory={working_directory if working_directory is not None else layout.current}\n"
        "ExecStart={ path="
        + (executable or f"{layout.current}/.venv/bin/framenest-production")
        + " ; argv[]=serve }\n"
    )


def test_parse_probe_accepts_its_own_layout() -> None:
    reading = engine.parse_web_layout_probe(
        _probe_answer(engine.OLD_WEB_LAYOUT), engine.OLD_WEB_LAYOUT
    )
    assert reading.recognises_layout
    assert reading.installed and reading.active and reading.enabled


def test_parse_probe_refuses_a_foreign_effective_identity() -> None:
    reading = engine.parse_web_layout_probe(
        _probe_answer(
            engine.OLD_WEB_LAYOUT,
            user="kronika",
            working_directory="/opt/kronika/current",
            executable="/opt/kronika/current/.venv/bin/kronika-production",
        ),
        engine.OLD_WEB_LAYOUT,
    )
    assert not reading.recognises_layout
    assert reading.unrecognised


def test_select_web_layout_resolves_the_three_transition_states() -> None:
    old_serving = [
        _reading(engine.OLD_WEB_LAYOUT),
        _reading(engine.NEW_WEB_LAYOUT, load_state="not-found", active_state="inactive", unit_file_state="not-found"),
    ]
    assert engine.select_web_layout(old_serving) is engine.OLD_WEB_LAYOUT

    new_serving_old_stopped = [
        _reading(engine.OLD_WEB_LAYOUT, active_state="inactive", unit_file_state="disabled"),
        _reading(engine.NEW_WEB_LAYOUT),
    ]
    assert engine.select_web_layout(new_serving_old_stopped) is engine.NEW_WEB_LAYOUT

    new_only = [
        _reading(engine.OLD_WEB_LAYOUT, load_state="not-found", active_state="inactive", unit_file_state="not-found"),
        _reading(engine.NEW_WEB_LAYOUT),
    ]
    assert engine.select_web_layout(new_only) is engine.NEW_WEB_LAYOUT


def test_select_web_layout_fails_closed_on_ambiguity_and_absence() -> None:
    both_active = [_reading(engine.OLD_WEB_LAYOUT), _reading(engine.NEW_WEB_LAYOUT)]
    with pytest.raises(engine.ReleaseError) as exc:
        engine.select_web_layout(both_active)
    assert exc.value.exit_code == engine.EXIT_LAYOUT

    neither = [
        _reading(engine.OLD_WEB_LAYOUT, load_state="not-found", active_state="inactive", unit_file_state="not-found"),
        _reading(engine.NEW_WEB_LAYOUT, load_state="not-found", active_state="inactive", unit_file_state="not-found"),
    ]
    with pytest.raises(engine.ReleaseError) as exc:
        engine.select_web_layout(neither)
    assert exc.value.exit_code == engine.EXIT_LAYOUT

    both_enabled_inactive = [
        _reading(engine.OLD_WEB_LAYOUT, active_state="inactive"),
        _reading(engine.NEW_WEB_LAYOUT, active_state="inactive"),
    ]
    with pytest.raises(engine.ReleaseError) as exc:
        engine.select_web_layout(both_enabled_inactive)
    assert exc.value.exit_code == engine.EXIT_LAYOUT

    unrecognised = [
        _reading(
            engine.OLD_WEB_LAYOUT,
            working_directory="/opt/kronika/current",
        ),
        _reading(engine.NEW_WEB_LAYOUT, load_state="not-found", active_state="inactive", unit_file_state="not-found"),
    ]
    with pytest.raises(engine.ReleaseError) as exc:
        engine.select_web_layout(unrecognised)
    assert exc.value.exit_code == engine.EXIT_LAYOUT


def _layout_runner(
    old: str, new: str, *, old_capture: str = "absent", new_capture: str = "absent"
):
    calls: list[str] = []

    def runner(argv, input_bytes):
        combined = " ".join(argv)
        calls.append(combined)
        if "systemctl show --property=LoadState" in combined:
            return new if "kronika.service" in combined else old
        if "test -L /opt/framenest/capture-current" in combined:
            return old_capture
        if "test -L /opt/kronika/capture-current" in combined:
            return new_capture
        raise AssertionError(combined)

    return runner, calls


def test_resolve_host_layout_selects_each_state_and_fails_closed() -> None:
    old_absent = engine.parse_web_layout_probe(
        _probe_answer(
            engine.OLD_WEB_LAYOUT,
            load_state="not-found",
            active_state="inactive",
            unit_file_state="not-found",
        ),
        engine.OLD_WEB_LAYOUT,
    )
    new_absent = engine.parse_web_layout_probe(
        _probe_answer(
            engine.NEW_WEB_LAYOUT,
            load_state="not-found",
            active_state="inactive",
            unit_file_state="not-found",
        ),
        engine.NEW_WEB_LAYOUT,
    )
    del old_absent, new_absent

    transport = {"target": "nuc", "user": "op", "identity": "identity"}

    # State 1: old web, no capture pointer.
    runner, _ = _layout_runner(
        _probe_answer(engine.OLD_WEB_LAYOUT),
        _probe_answer(
            engine.NEW_WEB_LAYOUT, load_state="not-found", active_state="inactive", unit_file_state="not-found"
        ),
    )
    state = engine.resolve_host_layout(runner, transport)
    assert state.web is engine.OLD_WEB_LAYOUT
    assert state.capture_pointer == "none"

    # State 2: new web, old capture pointer.
    runner, _ = _layout_runner(
        _probe_answer(engine.OLD_WEB_LAYOUT, active_state="inactive", unit_file_state="disabled"),
        _probe_answer(engine.NEW_WEB_LAYOUT),
        old_capture=f"/opt/framenest/releases/{RELEASE}",
    )
    state = engine.resolve_host_layout(runner, transport)
    assert state.web is engine.NEW_WEB_LAYOUT
    assert state.capture_pointer == "old"
    assert state.capture_release_root == engine.OLD_WEB_LAYOUT.release_root

    # State 3: new web, new capture pointer.
    runner, _ = _layout_runner(
        _probe_answer(engine.OLD_WEB_LAYOUT, load_state="not-found", active_state="inactive", unit_file_state="not-found"),
        _probe_answer(engine.NEW_WEB_LAYOUT),
        new_capture=f"/opt/kronika/releases/{RELEASE}",
    )
    state = engine.resolve_host_layout(runner, transport)
    assert state.capture_pointer == "new"
    assert state.capture_release_root == engine.NEW_WEB_LAYOUT.release_root

    # Both capture pointers live: ambiguous.
    runner, _ = _layout_runner(
        _probe_answer(engine.OLD_WEB_LAYOUT),
        _probe_answer(engine.NEW_WEB_LAYOUT, load_state="not-found", active_state="inactive", unit_file_state="not-found"),
        old_capture=f"/opt/framenest/releases/{RELEASE}",
        new_capture=f"/opt/kronika/releases/{RELEASE}",
    )
    with pytest.raises(engine.ReleaseError) as exc:
        engine.resolve_host_layout(runner, transport)
    assert exc.value.exit_code == engine.EXIT_LAYOUT


# ---------------------------------------------------------------------------
# Samples and the simulated host
# ---------------------------------------------------------------------------


def _observation(capture_pointer: str = "old") -> engine.MigrationObservation:
    return engine.MigrationObservation(
        release_sha=RELEASE,
        old_layout=engine.OLD_WEB_LAYOUT,
        new_layout=engine.NEW_WEB_LAYOUT,
        environment_text=SAMPLE_ENVIRONMENT,
        copy_sources=(
            ("/var/lib/framenest", "/var/lib/kronika"),
            ("/var/cache/framenest", "/var/cache/kronika"),
        ),
        installed_units=(
            "framenest.service",
            "framenest-catalog-backup.service",
            "framenest-catalog-backup.timer",
        ),
        installed_dropins=("20-ai-credential.conf",),
        credential_sources=("/etc/framenest/credentials/NVIDIA_API_KEY",),
        export_installed="/usr/local/libexec/framenest-catalog-export-v1",
        sudo_rule_installed=None,
        account_uid=1001,
        account_gid=1001,
        account_home="/var/lib/framenest",
        capture_pointer=capture_pointer,
        capture_release_sha=None,
        tailscale_handler_count=1,
        tailscale_old_handlers=1,
    )


def _plan(capture_pointer: str = "old") -> engine.MigrationPlan:
    return engine.build_migration_plan(_observation(capture_pointer))


class SimulatedMigrationHost:
    """In-memory host adapter driven by the real orchestration."""

    def __init__(self, plan: engine.MigrationPlan, *, fail_phase: str | None = None) -> None:
        self.plan = plan
        self.fail_phase = fail_phase
        self.journals: list[dict[str, object]] = []
        self.recovery_manifest: dict[str, object] | None = None
        self.journal: dict[str, object] | None = None
        self.existing_journal: dict[str, object] | None = None
        self.fail_journal_once_after: str | None = None
        self._journal_failure_spent = False
        self.call_log: list[str] = []
        self.locked = False
        self.account = {"name": "framenest", "uid": plan.account_uid, "gid": plan.account_gid}
        self.useradd_calls = 0
        self.account_count = 1
        self.capture_restarts = 0
        self.service_state = "framenest"
        self.writes_admitted = False
        self.recovered: str | None = None
        self.forward_recovery_required = False
        self.stale_state_restored = False
        self.old_layout_restored = False
        self.phases_run: list[str] = []
        self.tailscale_events: list[str] = []
        self.replace_calls = 0
        self.reset_calls = 0
        self.tailscale = {
            "handler_count": 1,
            "old_handlers": [0],
            "mounts": ["node.tailnet.ts.net:443/"],
            "targets": ["unix+http:///run/framenest/framenest.sock"],
        }
        self.new_release_path: str | None = None
        self.old_release_modified = False
        self.env_transformed = False
        self.copied = False
        self.resumed = False

    # -- non-phase methods ------------------------------------------------

    def write_journal(self, payload: dict[str, object]) -> None:
        self.call_log.append("write_journal")
        if (
            self.fail_journal_once_after is not None
            and not self._journal_failure_spent
            and payload.get("outcome") == "running"
            and list(payload.get("completed_phases", []))[-1:]
            == [self.fail_journal_once_after]
        ):
            self._journal_failure_spent = True
            raise engine.ReleaseError(
                "simulated journal write failure", engine.EXIT_MIGRATION
            )
        self.journal = copy.deepcopy(payload)
        self.journals.append(copy.deepcopy(payload))

    def read_journal(self) -> dict[str, object] | None:
        self.call_log.append("read_journal")
        return self.existing_journal

    def write_recovery_manifest(self, payload: dict[str, object]) -> None:
        self.call_log.append("write_recovery_manifest")
        self.recovery_manifest = copy.deepcopy(payload)

    def acquire_lock(self) -> str:
        self.call_log.append("acquire_lock")
        self.locked = True
        return "simulated-owner"

    def release_lock(self) -> None:
        self.call_log.append("release_lock")
        self.locked = False

    def recover_pre_write(self, journal: dict[str, object]) -> None:
        self.call_log.append("recover_pre_write")
        assert not engine.migration_writes_possible(journal)
        self.recovered = "pre-write"
        self.account["name"] = "framenest"
        self.account_count = 1
        self.service_state = "framenest"
        self.old_layout_restored = True
        self.stale_state_restored = False

    def recover_post_write(self, journal: dict[str, object]) -> None:
        self.call_log.append("recover_post_write")
        assert engine.migration_writes_possible(journal)
        self.recovered = "post-write"
        self.forward_recovery_required = True
        self.stale_state_restored = False

    # -- phase methods ----------------------------------------------------

    def __getattr__(self, name: str):
        phase_methods = set(engine._MIGRATION_PHASE_METHODS.values())
        if name not in phase_methods:
            raise AttributeError(name)

        def phase_method():
            self.call_log.append(name)
            if self.fail_phase == name:
                raise engine.ReleaseError(
                    "simulated phase failure", engine.EXIT_MIGRATION
                )
            self._run_phase(name)
            self.phases_run.append(name)
            return None

        return phase_method

    def _run_phase(self, name: str) -> None:
        if name == "quiesce":
            self.service_state = "stopped"
        elif name == "verify_capture_untouched":
            assert self.capture_restarts == 0
        elif name == "create_checkpoint":
            self.checkpoint = "verified"
        elif name == "copy_state":
            self.copied = True
        elif name == "transform_environment":
            self.env_transformed = True
        elif name == "preserve_ancillary":
            self.ancillary = True
        elif name == "prepare_release_environment":
            self.new_release_path = self.plan.new_layout.release_dir(
                self.plan.release_sha
            )
            assert not self.old_release_modified
        elif name == "rename_account":
            self.account = {
                "name": "kronika",
                "uid": self.account["uid"],
                "gid": self.account["gid"],
            }
            self.account_count = 1
        elif name == "install_units":
            self.installed_units = True
        elif name == "verify_effective_units":
            self.effective_ok = True
        elif name == "start_service":
            self.service_state = "kronika"
            self.writes_admitted = True
        elif name == "replace_tailscale_handler":
            self.tailscale_events.append("record")
            self.tailscale_events.append("replace")
            self.replace_calls += 1
        elif name == "verify_ingress":
            self.ingress_ok = True
        elif name == "resume_writers":
            self.resumed = True


# ---------------------------------------------------------------------------
# Migration machinery: success, failure injection, recovery branches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("capture_pointer", ["old", "new", "none"])
def test_simulated_apply_succeeds_in_each_capture_pointer_state(
    capture_pointer: str,
) -> None:
    plan = _plan(capture_pointer)
    host = SimulatedMigrationHost(plan)
    journal = engine.run_migration_apply(host, plan)
    assert journal["outcome"] == "completed"
    assert journal["completed_phases"] == list(engine.MIGRATION_PHASES)
    assert journal["writes_admitted"] is True
    assert host.recovered is None
    assert host.locked is False
    assert host.account == {"name": "kronika", "uid": 1001, "gid": 1001}
    assert host.capture_restarts == 0
    assert host.useradd_calls == 0
    assert host.tailscale_events == ["record", "replace"]
    assert host.new_release_path == NEW_RELEASE


@pytest.mark.parametrize("capture_pointer", ["old", "new", "none"])
@pytest.mark.parametrize("fail_index", range(len(engine.MIGRATION_PHASES)))
def test_failure_injection_after_every_phase(
    capture_pointer: str, fail_index: int
) -> None:
    plan = _plan(capture_pointer)
    failing_phase = engine.MIGRATION_PHASES[fail_index]
    fail_method = engine._MIGRATION_PHASE_METHODS[failing_phase]
    host = SimulatedMigrationHost(plan, fail_phase=fail_method)

    with pytest.raises(engine.ReleaseError):
        engine.run_migration_apply(host, plan)

    completed = list(engine.MIGRATION_PHASES[:fail_index])
    assert host.journals[-1]["completed_phases"] == completed
    assert host.journals[-1]["outcome"] == "failed"
    post_write = engine.migration_writes_possible(host.journals[-1])
    assert host.recovered == ("post-write" if post_write else "pre-write")

    # UID/GID survive every branch, exactly one account exists, and no phase
    # or recovery ever restarts capture.
    assert host.account["uid"] == 1001
    assert host.account["gid"] == 1001
    assert host.account_count == 1
    assert host.useradd_calls == 0
    assert host.capture_restarts == 0
    assert host.locked is False

    if post_write:
        assert host.forward_recovery_required is True
        assert host.stale_state_restored is False
        assert host.old_layout_restored is False
        assert host.account["name"] == "kronika"
    else:
        assert host.account["name"] == "framenest"
        assert host.old_layout_restored is True
        assert host.stale_state_restored is False


def test_pre_write_recovery_restores_the_former_layout() -> None:
    plan = _plan()
    host = SimulatedMigrationHost(plan, fail_phase="rename_account")
    with pytest.raises(engine.ReleaseError):
        engine.run_migration_apply(host, plan)
    assert host.recovered == "pre-write"
    assert host.account["name"] == "framenest"
    assert host.service_state == "framenest"
    assert host.old_layout_restored is True
    assert host.stale_state_restored is False


def test_post_write_recovery_never_restores_stale_copied_state() -> None:
    plan = _plan()
    host = SimulatedMigrationHost(plan, fail_phase="verify_ingress")
    with pytest.raises(engine.ReleaseError):
        engine.run_migration_apply(host, plan)
    assert host.recovered == "post-write"
    assert host.forward_recovery_required is True
    assert host.stale_state_restored is False
    assert host.old_layout_restored is False
    assert host.account["name"] == "kronika"
    assert host.capture_restarts == 0


def test_journal_is_written_before_the_first_mutation_and_after_every_phase() -> None:
    plan = _plan()
    host = SimulatedMigrationHost(plan)
    engine.run_migration_apply(host, plan)
    assert host.journals[0]["completed_phases"] == []
    assert host.recovery_manifest is not None
    assert host.recovery_manifest["plan_digest"] == plan.plan_digest
    assert host.journals[-1]["completed_phases"] == list(engine.MIGRATION_PHASES)


def test_plan_digest_binds_apply_to_a_validated_preflight() -> None:
    plan = _plan()
    assert engine.migration_plan_digest(plan.payload()) == plan.plan_digest
    changed = _observation()
    changed = engine.MigrationObservation(
        **{**changed.__dict__, "release_sha": "b" * 40}
    )
    other = engine.build_migration_plan(changed)
    assert other.plan_digest != plan.plan_digest


# ---------------------------------------------------------------------------
# Tailscale handler replacement
# ---------------------------------------------------------------------------


TAILSCALE_TWO_HANDLERS = json.dumps(
    {
        "Web": {
            "node.tailnet.ts.net:443": {
                "Handlers": {
                    "/": {"Proxy": "unix+http:///run/framenest/framenest.sock"},
                    "/other": {"Proxy": "http://127.0.0.1:9999"},
                }
            }
        }
    }
)


def test_tailscale_status_classifies_the_single_old_handler() -> None:
    status = engine.parse_tailscale_serve_status(TAILSCALE_TWO_HANDLERS)
    assert status["handler_count"] == 2
    assert status["old_handlers"] == [0]
    assert status["mounts"][0] == "node.tailnet.ts.net:443/"


def test_tailscale_replacement_command_never_resets() -> None:
    command = engine.cmd_remote_tailscale_replace(
        "node.tailnet.ts.net:443/",
        "unix+http:///run/kronika/kronika.sock",
    )
    assert "tailscale serve --bg" in command
    assert "reset" not in command


def test_production_replacement_records_first_replaces_one_and_never_resets() -> None:
    plan = _plan()
    calls: list[str] = []

    def runner(argv, input_bytes):
        combined = " ".join(argv)
        calls.append(combined)
        if "tailscale serve status --json" in combined:
            return TAILSCALE_TWO_HANDLERS
        if "tailscale serve --bg" in combined:
            return ""
        raise AssertionError(combined)

    host = engine.RemoteMigrationHost(
        runner,
        {"target": "nuc", "user": "op", "identity": "identity"},
        plan,
        engine_path=ENGINE_PATH,
        repository_root=REPOSITORY_ROOT,
    )
    result = host.replace_tailscale_handler()
    assert result == {"replaced": 1, "handler_count": 2}
    assert sum("tailscale serve --bg" in call for call in calls) == 1
    assert all("reset" not in call for call in calls)
    assert sum("tailscale serve status --json" in call for call in calls) == 1


def test_replacement_fails_closed_without_exactly_one_old_handler() -> None:
    plan = _plan()
    two_old = json.dumps(
        {
            "Web": {
                "node.tailnet.ts.net:443": {
                    "Handlers": {
                        "/": {"Proxy": "unix+http:///run/framenest/framenest.sock"},
                        "/second": {"Proxy": "unix+http:///run/framenest/framenest.sock"},
                    }
                }
            }
        }
    )

    def runner(argv, input_bytes):
        combined = " ".join(argv)
        if "tailscale serve status --json" in combined:
            return two_old
        if "tailscale serve --bg" in combined:
            raise AssertionError("replacement must not run")
        raise AssertionError(combined)

    host = engine.RemoteMigrationHost(
        runner,
        {"target": "nuc", "user": "op", "identity": "identity"},
        plan,
        engine_path=ENGINE_PATH,
        repository_root=REPOSITORY_ROOT,
    )
    with pytest.raises(engine.ReleaseError) as exc:
        host.replace_tailscale_handler()
    assert exc.value.exit_code == engine.EXIT_MIGRATION


# ---------------------------------------------------------------------------
# New-path environment preparation
# ---------------------------------------------------------------------------


def test_relocated_environment_resolves_and_leaves_the_old_release_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "RELEASE_ROOT", str(tmp_path / "releases"))
    releases = tmp_path / "releases"
    old_release = releases / RELEASE
    old_release.mkdir(parents=True)
    old_marker = old_release / "marker.txt"
    old_marker.write_text("old release bytes\n", encoding="utf-8")
    old_digest = hashlib.sha256(old_marker.read_bytes()).hexdigest()

    staging = releases / f"{RELEASE}.staging"
    final = releases / RELEASE
    staging.mkdir()
    bindir = staging / ".venv" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "framenest-db").write_text(
        f"#!{staging}/.venv/bin/python\nprint('db')\n", encoding="utf-8"
    )
    (bindir / "framenest-backup").write_text(
        f"#!{staging}/.venv/bin/python\nprint('backup')\n", encoding="utf-8"
    )
    site = staging / ".venv" / "lib" / "site-packages"
    site.mkdir(parents=True)
    (site / "editable.pth").write_text(f"{staging}/src\n", encoding="utf-8")
    (site / "direct_url.json").write_text(
        json.dumps({"url": f"file://{staging}"}), encoding="utf-8"
    )

    engine.relocate_venv_shebangs(str(staging), str(final))

    for name in ("framenest-db", "framenest-backup"):
        text = (bindir / name).read_text(encoding="utf-8")
        assert text.splitlines()[0] == f"#!{final}/.venv/bin/python"
    assert f"{final}/src" in (site / "editable.pth").read_text(encoding="utf-8")
    assert "staging" not in (site / "direct_url.json").read_text(encoding="utf-8")
    # The retained release tree is a different directory and is byte-unchanged.
    assert hashlib.sha256(old_marker.read_bytes()).hexdigest() == old_digest


def test_new_release_is_prepared_at_its_final_path() -> None:
    assert engine.NEW_WEB_LAYOUT.release_dir(RELEASE) == NEW_RELEASE
    assert engine.NEW_WEB_LAYOUT.staging_dir(RELEASE) == f"{NEW_RELEASE}.staging"
    command = engine.cmd_remote_prepare_migration_release(
        OLD_RELEASE, f"{NEW_RELEASE}.staging"
    )
    assert OLD_RELEASE in command
    assert f"{NEW_RELEASE}.staging" in command
    assert "rm -rf" in command
    relocations = engine.cmd_remote_relocate_venv_shebangs(
        f"{NEW_RELEASE}.staging", NEW_RELEASE, "/tmp/engine.py"
    )
    assert NEW_RELEASE in relocations
    final_verify = engine.cmd_remote_verify_migration_release(
        f"{NEW_RELEASE}.staging", NEW_RELEASE
    )
    assert f"{NEW_RELEASE}/.venv" in final_verify
    assert f"{NEW_RELEASE}.staging" in final_verify


# ---------------------------------------------------------------------------
# Preflight: non-mutating and filename/existence only
# ---------------------------------------------------------------------------


MUTATION_TOKENS = (
    "cat >",
    "mkdir",
    "rm ",
    "rm -",
    "mv ",
    "install -d",
    "cp -a",
    "systemctl stop",
    "systemctl start",
    "systemctl restart",
    "systemctl enable",
    "systemctl disable",
    "groupmod",
    "usermod",
    "useradd",
    "groupadd",
    "daemon-reload",
    "tailscale serve --bg",
    "visudo",
    "chown",
    "chmod",
)

READONLY_ENVIRONMENT = """\
FRAMENEST_HOST=127.0.0.1
FRAMENEST_DATABASE_PATH=/var/lib/framenest/catalog.sqlite3
FRAMENEST_UDS_PATH=/run/framenest/framenest.sock
FRAMENEST_EXTERNAL_ORIGIN=https://node.tailnet.ts.net
"""


class ReadOnlyPreflightRunner:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.credential_listing = "NVIDIA_API_KEY\nOPENCODE_API_KEY\n"

    def __call__(self, argv, input_bytes):
        combined = " ".join(argv)
        self.calls.append(combined)
        for token in MUTATION_TOKENS:
            assert token not in combined, f"preflight issued a mutation: {combined}"
        if "systemctl show --property=LoadState" in combined:
            if "kronika.service" in combined:
                return _probe_answer(
                    engine.NEW_WEB_LAYOUT,
                    load_state="not-found",
                    active_state="inactive",
                    unit_file_state="not-found",
                )
            return _probe_answer(engine.OLD_WEB_LAYOUT)
        if "test -L /opt/framenest/capture-current" in combined:
            return "absent"
        if "test -L /opt/kronika/capture-current" in combined:
            return "absent"
        if "cat" in combined and engine.OLD_WEB_LAYOUT.env_file in combined:
            return READONLY_ENVIRONMENT
        if "ls -1" in combined and "credentials" in combined:
            return self.credential_listing
        if "ls -1" in combined:
            return "absent"
        if "test -e" in combined and "/usr/local/libexec" in combined:
            return "present"
        if "test -e" in combined:
            return "present"
        if "getent passwd framenest" in combined:
            return "1001 1001 /var/lib/framenest"
        if "getent passwd kronika" in combined:
            return "absent"
        if "tailscale serve status --json" in combined:
            return json.dumps(
                {
                    "Web": {
                        "node.tailnet.ts.net:443": {
                            "Handlers": {
                                "/": {
                                    "Proxy": (
                                        "unix+http:///run/framenest/framenest.sock"
                                    )
                                }
                            }
                        }
                    }
                }
            )
        raise AssertionError(f"unexpected preflight command: {combined}")


def test_preflight_is_read_only_and_reports_existence_only(capsys) -> None:
    runner = ReadOnlyPreflightRunner()
    transport = {"target": "nuc", "user": "op", "identity": "identity"}
    observation = engine.read_migration_observation(
        runner, transport, release_sha=RELEASE
    )
    plan = engine.build_migration_plan(observation)
    assert plan.plan_digest
    # Credential sources are reported as filenames only.
    assert observation.credential_sources == (
        "/etc/framenest/credentials/NVIDIA_API_KEY",
        "/etc/framenest/credentials/OPENCODE_API_KEY",
    )
    assert runner.calls

    import argparse

    args = argparse.Namespace(
        release=RELEASE, target="nuc", user="op", identity="identity",
        migration_command="preflight",
    )
    assert engine._cmd_migrate_preflight(args, runner) == engine.EXIT_OK
    output = capsys.readouterr().out
    assert f"plan_digest: {plan.plan_digest}" in output
    # Key names are listed; no assignment value and no host identifier leaks.
    assert "FRAMENEST_DATABASE_PATH=/" not in output
    assert "/var/lib/framenest/catalog.sqlite3" not in output
    assert "https://node.tailnet.ts.net" not in output
    assert "node.tailnet.ts.net" not in output


def test_preflight_never_reads_a_credential_value() -> None:
    runner = ReadOnlyPreflightRunner()
    transport = {"target": "nuc", "user": "op", "identity": "identity"}
    engine.read_migration_observation(runner, transport, release_sha=RELEASE)
    credential_reads = [
        call
        for call in runner.calls
        if "credentials" in call and "cat" in call
    ]
    assert credential_reads == []


# ---------------------------------------------------------------------------
# Production host phases exercised through a fake command runner
# ---------------------------------------------------------------------------


def _production_host(runner) -> engine.RemoteMigrationHost:
    return engine.RemoteMigrationHost(
        runner,
        {"target": "nuc", "user": "op", "identity": "identity"},
        _plan(),
        engine_path=ENGINE_PATH,
        repository_root=REPOSITORY_ROOT,
    )


def test_production_copy_state_verifies_before_it_continues() -> None:
    calls: list[str] = []

    def runner(argv, input_bytes):
        combined = " ".join(argv)
        calls.append(combined)
        if "not-a-directory" in combined:
            return "absent"
        if "find ." in combined:
            if "/var/cache/framenest" in combined:
                return "manifest-cache-source"
            if "/var/cache/kronika" in combined:
                return "manifest-cache-destination-drifted"
            return "manifest-source"
        return ""

    host = _production_host(runner)
    with pytest.raises(engine.ReleaseError) as exc:
        host.copy_state()
    assert exc.value.exit_code == engine.EXIT_MIGRATION
    assert any("cp -a" in call for call in calls)


def test_production_copy_state_accepts_a_matching_manifest() -> None:
    def runner(argv, input_bytes):
        combined = " ".join(argv)
        if "not-a-directory" in combined:
            return "absent"
        if "find ." in combined:
            if "/var/cache/kronika" in combined:
                return "cache-manifest"
            if "/var/cache/framenest" in combined:
                return "cache-manifest"
            return "var-manifest"
        return ""

    host = _production_host(runner)
    assert host.copy_state() == ["/var/lib/kronika", "/var/cache/kronika"]


def test_production_transform_environment_refuses_retired_assignments() -> None:
    def runner(argv, input_bytes):
        combined = " ".join(argv)
        if "grep -Eq" in combined:
            return "retired-assignment-present"
        return ""

    host = _production_host(runner)
    with pytest.raises(engine.ReleaseError) as exc:
        host.transform_environment()
    assert exc.value.exit_code == engine.EXIT_MIGRATION


def test_production_rename_account_keeps_uid_and_gid() -> None:
    calls: list[str] = []

    def runner(argv, input_bytes):
        calls.append(" ".join(argv))
        return ""

    host = _production_host(runner)
    assert host.rename_account() == {
        "uid": 1001,
        "gid": 1001,
        "home": "/var/lib/kronika",
    }
    groupmod = next(i for i, call in enumerate(calls) if "groupmod -n kronika framenest" in call)
    usermod = next(i for i, call in enumerate(calls) if "usermod -l kronika" in call)
    verify = next(i for i, call in enumerate(calls) if "id -u kronika" in call)
    assert groupmod < usermod < verify
    assert "usermod -l kronika -d /var/lib/kronika framenest" in calls[usermod]
    assert "1001" in calls[verify]
    assert all("useradd" not in call and "groupadd" not in call for call in calls)


def test_production_install_units_installs_only_discovered_artifacts() -> None:
    written: list[str] = []

    def runner(argv, input_bytes):
        combined = " ".join(argv)
        match = re.search(r"cat > '?(/etc/systemd/system/[^'\s]+)'?", combined)
        if match:
            written.append(match.group(1))
        if "ls -1" in combined:
            return "absent"
        return ""

    host = _production_host(runner)
    installed = host.install_units()
    assert installed == [
        "kronika.service",
        "kronika-catalog-backup.service",
        "kronika-catalog-backup.timer",
    ]
    assert "/etc/systemd/system/kronika.service" in written
    assert "/etc/systemd/system/kronika-catalog-backup.timer" in written
    assert "/etc/systemd/system/kronika.service.d/20-ai-credential.conf" in written
    assert all("offdevice" not in path for path in written)
    assert all("/etc/kronika/credentials/" not in path for path in written)


def test_production_prepare_release_environment_builds_at_the_new_path() -> None:
    calls: list[str] = []
    manifest_payload = json.dumps(
        engine.make_manifest(
            release_sha=RELEASE,
            ap_pin="b" * 40,
            superproject_sha256="e" * 64,
            ap_archive_sha256="f" * 64,
            capture_code_tree="d" * 40,
            capture_runtime_contract_sha256="1" * 64,
            capture_unit_contract_sha256="2" * 64,
            capture_bridge_protocol="1",
        )
    )

    def runner(argv, input_bytes):
        combined = " ".join(argv)
        calls.append(combined)
        if "then echo manifest" in combined or "then echo sha" in combined:
            return (
                f"manifest {engine.RELEASE_MANIFEST_MARKER}\n"
                f"sha {engine.RELEASE_SHA_MARKER}"
            )
        if f"/{engine.RELEASE_MANIFEST_MARKER}" in combined and "cat " in combined:
            return manifest_payload
        if f"/{engine.RELEASE_SHA_MARKER}" in combined and "cat " in combined:
            return RELEASE
        return ""

    host = _production_host(runner)
    host.prepare_release_environment()
    transcript = "\n".join(calls)
    assert f"cp -a {OLD_RELEASE}/. {NEW_RELEASE}.staging/" in transcript
    assert f"rm -rf {NEW_RELEASE}.staging/.venv" in transcript
    assert f"--staging {NEW_RELEASE}.staging" in transcript
    assert f"--final {NEW_RELEASE}" in transcript
    assert f"mv {NEW_RELEASE}.staging {NEW_RELEASE}" in transcript
    assert f"test -d {NEW_RELEASE}/.venv" in transcript
    relocate_index = transcript.index("_remote-relocate-venv-shebangs")
    rename_index = transcript.index(f"mv {NEW_RELEASE}.staging {NEW_RELEASE}")
    assert relocate_index < rename_index


def test_production_pre_write_recovery_restores_the_old_layout_and_writers() -> None:
    calls: list[str] = []

    def runner(argv, input_bytes):
        calls.append(" ".join(argv))
        return ""

    host = _production_host(runner)
    host.recover_pre_write(
        {"completed_phases": list(engine.MIGRATION_PHASES[:8])}
    )
    transcript = "\n".join(calls)
    assert "groupmod -n framenest kronika" in transcript
    assert "usermod -l framenest -d /var/lib/framenest kronika" in transcript
    assert "start framenest.service" in transcript
    assert "start framenest-catalog-backup.timer" in transcript
    assert "disable kronika.service" in transcript
    assert "useradd" not in transcript
    assert "groupadd" not in transcript
    assert "cp -a" not in transcript


def test_production_post_write_recovery_never_restores_stale_state() -> None:
    calls: list[str] = []

    def runner(argv, input_bytes):
        calls.append(" ".join(argv))
        return ""

    host = _production_host(runner)
    completed = list(engine.MIGRATION_PHASES[:11])
    assert engine.migration_is_post_write(completed)
    host.recover_post_write({"completed_phases": completed})
    transcript = "\n".join(calls)
    assert "cp -a" not in transcript
    assert "groupmod" not in transcript
    assert "usermod" not in transcript
    assert "capture" not in transcript

    with pytest.raises(engine.ReleaseError) as exc:
        host.recover_post_write(
            {"completed_phases": list(engine.MIGRATION_PHASES[:4])}
        )
    assert exc.value.exit_code == engine.EXIT_MIGRATION


# ---------------------------------------------------------------------------
# apply confirmation and the never-invoked guarantee
# ---------------------------------------------------------------------------


def test_apply_without_confirmation_never_touches_the_runner() -> None:
    import argparse

    def exploding_runner(argv, input_bytes):
        raise AssertionError("apply must refuse before any command")

    args = argparse.Namespace(
        release=RELEASE,
        yes=False,
        preflight_digest="0" * 64,
        target="nuc",
        user="op",
        identity="identity",
        migration_command="apply",
    )
    with pytest.raises(engine.ReleaseError) as exc:
        engine._cmd_migrate_apply(args, exploding_runner)
    assert exc.value.exit_code == engine.EXIT_USAGE


def test_apply_parser_requires_confirmation_and_a_preflight_digest() -> None:
    parser = engine._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["migrate-identity", "apply", "--release", RELEASE, "--yes"]
        )
    with pytest.raises(SystemExit):
        parser.parse_args(["migrate-identity", "apply", "--release", RELEASE])
    parsed = parser.parse_args(
        [
            "migrate-identity",
            "apply",
            "--release",
            RELEASE,
            "--yes",
            "--preflight-digest",
            "a" * 64,
        ]
    )
    assert parsed.migration_command == "apply"
    assert parsed.yes is True


def test_digest_mismatch_refuses_before_any_mutation() -> None:
    import argparse

    runner = ReadOnlyPreflightRunner()
    args = argparse.Namespace(
        release=RELEASE,
        yes=True,
        preflight_digest="0" * 64,
        target="nuc",
        user="op",
        identity="identity",
        migration_command="apply",
    )
    with pytest.raises(engine.ReleaseError) as exc:
        engine._cmd_migrate_apply(args, runner)
    assert exc.value.exit_code == engine.EXIT_MIGRATION
    assert all("tailscale serve --bg" not in call for call in runner.calls)


def test_routine_commands_never_reach_identity_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.contract import test_nuc_release_remote_contract as remote

    def forbidden(*args, **kwargs):
        raise AssertionError("routine command reached identity migration")

    monkeypatch.setattr(engine, "_cmd_migrate_identity", forbidden)
    monkeypatch.setattr(engine, "read_migration_observation", forbidden)
    monkeypatch.setattr(engine, "run_migration_apply", forbidden)
    monkeypatch.setattr(engine, "RemoteMigrationHost", forbidden)

    for command in ("deploy", "rollback"):
        runner = remote.FakeRunner()
        assert engine.main(remote._args(command), runner=runner) == engine.EXIT_OK
        assert "migrate" not in remote._ssh_combined(runner)

    for command in ("activate-capture", "rollback-capture"):
        from tests.contract import test_kronika_capture_services as capture

        runner = capture.CaptureRunner()
        assert (
            engine.main(capture._activate(command=command), runner=runner) == engine.EXIT_OK
        )
        assert "migrate" not in capture._ssh(runner)


def test_engine_and_routine_builders_never_emit_migrate_identity() -> None:
    routine_builders = (
        engine.cmd_remote_atomic_switch(NEW_RELEASE, engine.NEW_WEB_LAYOUT),
        engine.cmd_remote_restart_service(engine.NEW_WEB_LAYOUT.service),
        engine.cmd_remote_run_scheduled_backup(NEW_RELEASE, engine.NEW_WEB_LAYOUT),
        engine.cmd_remote_check_database_ready(NEW_RELEASE, engine.NEW_WEB_LAYOUT),
    )
    for command in routine_builders:
        assert "migrate-identity" not in command


def test_migration_phases_are_ordered_and_complete() -> None:
    assert engine.MIGRATION_PHASES[0] == engine.MIGRATION_PHASE_QUIESCE
    assert engine.MIGRATION_PHASES[-1] == engine.MIGRATION_PHASE_RESUME_WRITERS
    assert len(engine.MIGRATION_PHASES) == 14
    assert set(engine._MIGRATION_PHASE_METHODS) == set(engine.MIGRATION_PHASES)
    assert engine.MIGRATION_CUTOVER_PHASE == engine.MIGRATION_PHASE_START_SERVICE


def test_canonical_artifacts_are_never_installed_by_routine_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.contract import test_nuc_release_remote_contract as remote

    for command in ("deploy", "rollback"):
        runner = remote.FakeRunner()
        assert engine.main(remote._args(command), runner=runner) == engine.EXIT_OK
        transcript = remote._ssh_combined(runner)
        assert "cat > /etc/systemd/system/kronika" not in transcript
        assert "/etc/kronika/kronika.env'" not in transcript
        assert "groupmod" not in transcript
        assert "usermod" not in transcript
        assert "tailscale serve --bg" not in transcript


# ---------------------------------------------------------------------------
# C6-P1: stateful production-adapter boundary
# ---------------------------------------------------------------------------


class RemoteBoundary:
    """A stateful in-memory model of the remote filesystem and unit manager.

    It executes the exact production command builders and mutates its own state,
    so a production ``RemoteMigrationHost`` method sees the effects of the
    commands it issued. Unknown commands raise instead of succeeding, so a test
    cannot pass vacuously.
    """

    def __init__(self) -> None:
        self.paths: dict[str, dict[str, object]] = {}
        self.calls: list[str] = []
        self.privilege_ok = True
        self.ps_query_ok = True
        self.process_counts: dict[str, int] = {}
        self.users: dict[str, dict[str, object]] = {
            "framenest": {"uid": 1001, "gid": 1001, "home": "/var/lib/framenest"}
        }
        self.groups: dict[str, dict[str, object]] = {"framenest": {"gid": 1001}}
        self.units: dict[str, dict[str, object]] = {}
        self.fail_atomic_write = False
        self.fail_atomic_rename = False
        self.fail_groupmod = False

    # -- state helpers ----------------------------------------------------

    def _ensure_parents(self, path: str) -> None:
        parent = str(Path(path).parent)
        while parent not in ("", "/") and parent not in self.paths:
            grand = str(Path(parent).parent)
            self.paths[parent] = {
                "kind": "dir",
                "mode": 0o755,
                "owner": "root",
                "group": "root",
            }
            parent = grand

    def add_dir(
        self, path: str, mode: int = 0o755, owner: str = "root", group: str = "root"
    ) -> None:
        self._ensure_parents(path)
        self.paths[path] = {"kind": "dir", "mode": mode, "owner": owner, "group": group}

    def add_file(
        self,
        path: str,
        data: bytes,
        mode: int = 0o644,
        owner: str = "root",
        group: str = "root",
    ) -> None:
        self._ensure_parents(path)
        self.paths[path] = {
            "kind": "file",
            "data": data,
            "mode": mode,
            "owner": owner,
            "group": group,
        }

    def add_link(
        self, path: str, target: str, owner: str = "root", group: str = "root"
    ) -> None:
        self._ensure_parents(path)
        self.paths[path] = {
            "kind": "link",
            "target": target,
            "mode": 0o777,
            "owner": owner,
            "group": group,
        }

    def exists(self, path: str) -> bool:
        return path in self.paths

    def children(self, path: str) -> list[str]:
        prefix = path.rstrip("/") + "/"
        return sorted(
            entry[len(prefix):]
            for entry in self.paths
            if entry.startswith(prefix) and "/" not in entry[len(prefix):]
        )

    def _require(self, path: str) -> dict[str, object]:
        if path not in self.paths:
            raise engine.ReleaseError(f"no such path: {path}", engine.EXIT_TRANSPORT)
        return self.paths[path]

    def remove(self, path: str) -> None:
        if path in self.paths:
            del self.paths[path]
            parent = str(Path(path).parent)
            while (
                parent not in ("", "/")
                and parent in self.paths
                and not self.children(parent)
            ):
                del self.paths[parent]
                parent = str(Path(parent).parent)

    def remove_tree(self, path: str) -> None:
        prefix = path.rstrip("/") + "/"
        for entry in [
            entry
            for entry in self.paths
            if entry == path or entry.startswith(prefix)
        ]:
            del self.paths[entry]

    def move(self, source: str, destination: str) -> None:
        self._require(source)
        prefix = source.rstrip("/") + "/"
        entries = {
            entry: self.paths[entry]
            for entry in list(self.paths)
            if entry == source or entry.startswith(prefix)
        }
        for entry in entries:
            del self.paths[entry]
        self._ensure_parents(destination)
        if destination in self.paths:
            del self.paths[destination]
        for entry, value in entries.items():
            self.paths[destination + entry[len(source):]] = value

    def copy_tree(self, source: str, destination: str) -> None:
        self._require(source)
        self.add_dir(destination)
        prefix = source.rstrip("/") + "/"
        for entry in sorted(e for e in self.paths if e.startswith(prefix)):
            target = f"{destination}/{entry[len(prefix):]}"
            self._ensure_parents(target)
            self.paths[target] = dict(self.paths[entry])

    def manifest(self, path: str) -> str:
        self._require(path)
        prefix = path.rstrip("/") + "/"
        lines: list[str] = []
        for entry in sorted(e for e in self.paths if e.startswith(prefix)):
            relative = "./" + entry[len(prefix):]
            value = self.paths[entry]
            meta = f"{value['owner']}:{value['group']}:{int(value['mode']):04o}"
            if value["kind"] == "link":
                lines.append(f"{relative}|symlink|{value['target']}|{meta}")
            elif value["kind"] == "dir":
                lines.append(f"{relative}|dir|-|{meta}")
            elif value["kind"] == "file":
                digest = hashlib.sha256(value["data"]).hexdigest()  # type: ignore[arg-type]
                lines.append(f"{relative}|file|{digest}|{meta}")
            else:
                lines.append(f"{relative}|other|-|{meta}")
        return "\n".join(lines)

    # -- command execution ------------------------------------------------

    def __call__(self, argv, input_bytes):
        combined = " ".join(argv)
        self.calls.append(combined)
        return self._dispatch(combined, input_bytes)

    @staticmethod
    def _unquote(token: str) -> str:
        return token.strip().strip("'")

    def _dispatch(self, combined: str, input_bytes) -> str:
        if "sudo -n mkdir -m 0700 " in combined:
            path = combined.split("sudo -n mkdir -m 0700 ", 1)[1].strip()
            if self.exists(path):
                raise engine.ReleaseError("exists", engine.EXIT_EXISTS)
            self.add_dir(path, mode=0o700)
            return ""
        if "rm -rf " in combined:
            self.remove_tree(self._unquote(combined.split("rm -rf ", 1)[1]))
            return ""
        if "sudo -n rmdir " in combined:
            path = combined.split("sudo -n rmdir ", 1)[1].strip()
            if not self.exists(path) or self.paths[path]["kind"] != "dir":
                raise engine.ReleaseError("no such directory", engine.EXIT_TRANSPORT)
            if self.children(path):
                raise engine.ReleaseError("directory not empty", engine.EXIT_TRANSPORT)
            self.remove(path)
            return ""
        if "sudo -n rm -f " in combined:
            self.remove(self._unquote(combined.split("rm -f ", 1)[1]))
            return ""
        if "install -d -o root -g root -m " in combined:
            mode_text, path = combined.split(
                "install -d -o root -g root -m ", 1
            )[1].split(" ", 1)
            self.add_dir(self._unquote(path), mode=int(mode_text, 8))
            return ""
        if "cat > " in combined:
            path = self._unquote(combined.split("cat > ", 1)[1].split("\n", 1)[0])
            if path.endswith(".next") and self.fail_atomic_write:
                raise engine.ReleaseError("interrupted write", engine.EXIT_TRANSPORT)
            payload = input_bytes or b""
            self.add_file(path, payload, mode=0o600)
            expected = re.findall(r"[0-9a-f]{64}", combined)
            if expected and hashlib.sha256(payload).hexdigest() != expected[-1]:
                raise engine.ReleaseError("digest mismatch", engine.EXIT_TRANSPORT)
            if "mv -T " in combined:
                source, destination = combined.split("mv -T ", 1)[1].split(" ", 1)
                source = self._unquote(source)
                destination = self._unquote(destination)
                if self.fail_atomic_rename and source.endswith(".next"):
                    raise engine.ReleaseError(
                        "interrupted rename", engine.EXIT_TRANSPORT
                    )
                self.move(source, destination)
            return ""
        if "mv -T " in combined:
            source, destination = combined.split("mv -T ", 1)[1].split(" ", 1)
            source = self._unquote(source)
            destination = self._unquote(destination)
            if self.fail_atomic_rename and source.endswith(".next"):
                raise engine.ReleaseError("interrupted rename", engine.EXIT_TRANSPORT)
            self.move(source, destination)
            return ""
        if "test ! -e " in combined:
            path = self._unquote(combined.split("test ! -e ", 1)[1])
            if self.exists(path):
                raise engine.ReleaseError("path exists", engine.EXIT_TRANSPORT)
            return ""
        if "not-a-directory" in combined:
            if not self.privilege_ok:
                return "query-failed"
            match = re.search(r"\[ ! -e (\S+) \]", combined)
            assert match is not None, combined
            path = match.group(1)
            if not self.exists(path):
                return "absent"
            if self.paths[path]["kind"] != "dir":
                return "not-a-directory"
            return "empty" if not self.children(path) else "populated"
        if "sudo -n cat " in combined:
            if not self.privilege_ok:
                return "query-failed"
            path = self._unquote(combined.split("sudo -n cat ", 1)[1].split(";", 1)[0])
            if not self.exists(path):
                return "absent"
            return self._require(path)["data"].decode("utf-8")  # type: ignore[union-attr]
        if "test -e " in combined:
            if not self.privilege_ok:
                return "query-failed"
            path = self._unquote(combined.split("test -e ", 1)[1].split(";", 1)[0])
            return "present" if self.exists(path) else "absent"
        if "ls -1 " in combined:
            if not self.privilege_ok:
                return "query-failed"
            path = self._unquote(combined.split("ls -1 ", 1)[1].split(";", 1)[0])
            if not self.exists(path) or self.paths[path]["kind"] != "dir":
                return "absent"
            return "\n".join(self.children(path))
        if "test -L " in combined:
            if not self.privilege_ok:
                return "query-failed"
            path = self._unquote(combined.split("test -L ", 1)[1].split(";", 1)[0])
            if not self.exists(path) or self.paths[path]["kind"] != "link":
                return "absent"
            return str(self.paths[path]["target"])
        if "load=%s" in combined:
            if not self.privilege_ok:
                return "query-failed"
            match = re.search(
                r"systemctl show -p LoadState --value (\S+)", combined
            )
            assert match is not None, combined
            unit = self._unquote(match.group(1))
            if unit not in self.units:
                return "query-failed"
            state = self.units[unit]
            return (
                f"load={state['load']} enabled={state['enabled']} "
                f"active={state['active']}"
            )
        if "find ." in combined:
            match = re.search(r"cd (\S+)", combined)
            assert match is not None, combined
            return self.manifest(self._unquote(match.group(1)))
        if "cp -a " in combined:
            source, destination = combined.split("cp -a ", 1)[1].split(" ", 1)
            source = self._unquote(source).rstrip("/")
            if source.endswith("/."):
                source = source[:-2]
            self.copy_tree(source, self._unquote(destination).rstrip("/"))
            return ""
        if "groupmod -n " in combined:
            _command, rest = combined.split("groupmod -n ", 1)
            new_name, old_name = rest.split()[:2]
            if self.fail_groupmod:
                raise engine.ReleaseError("groupmod failed", engine.EXIT_MIGRATION)
            if old_name not in self.groups:
                raise engine.ReleaseError("no such group", engine.EXIT_TRANSPORT)
            self.groups[new_name] = self.groups.pop(old_name)
            return ""
        if "usermod -l " in combined:
            _command, rest = combined.split("usermod -l ", 1)
            tokens = rest.split()
            new_name = tokens[0]
            old_name = tokens[-1]
            home = tokens[tokens.index("-d") + 1] if "-d" in tokens else None
            if old_name not in self.users:
                raise engine.ReleaseError("no such user", engine.EXIT_TRANSPORT)
            record = self.users.pop(old_name)
            if home is not None:
                record["home"] = home
            self.users[new_name] = record
            return ""
        if "systemctl " in combined:
            return self._dispatch_systemctl(combined)
        if "ps -u " in combined:
            user = self._unquote(combined.split("ps -u ", 1)[1].split()[0])
            if "printf %s query-failed" in combined:
                if not self.ps_query_ok or user not in self.users:
                    return "query-failed"
            return str(self.process_counts.get(user, 0))
        raise AssertionError(f"unhandled remote command: {combined}")

    def _dispatch_systemctl(self, combined: str) -> str:
        if "daemon-reload" in combined:
            return ""
        if "is-active" in combined:
            unit = combined.split()[-1]
            if unit not in self.units:
                raise engine.ReleaseError("unit not found", engine.EXIT_TRANSPORT)
            return str(self.units[unit]["active"])
        if "show -p ActiveState --value" in combined:
            unit = combined.split()[-1]
            if unit not in self.units:
                raise engine.ReleaseError("unit not found", engine.EXIT_TRANSPORT)
            return str(self.units[unit]["active"])
        if "show -p Result --value" in combined:
            return "success"
        for action, field, value in (
            ("stop", "active", "inactive"),
            ("start", "active", "active"),
            ("enable", "enabled", "enabled"),
            ("disable", "enabled", "disabled"),
        ):
            if f"systemctl {action} " in combined:
                unit = combined.split()[-1]
                if unit not in self.units:
                    raise engine.ReleaseError("unit not found", engine.EXIT_TRANSPORT)
                self.units[unit][field] = value
                return ""
        raise AssertionError(f"unhandled systemctl command: {combined}")


def _boundary_host(boundary: RemoteBoundary) -> engine.RemoteMigrationHost:
    return engine.RemoteMigrationHost(
        boundary,
        {"target": "nuc", "user": "op", "identity": "identity"},
        _plan(),
        engine_path=ENGINE_PATH,
        repository_root=REPOSITORY_ROOT,
    )


def _populate_state_sources(boundary: RemoteBoundary) -> None:
    boundary.add_dir("/var/lib/framenest")
    boundary.add_file(
        "/var/lib/framenest/catalog.sqlite3",
        b"catalog-bytes",
        mode=0o640,
        owner="framenest",
        group="framenest",
    )
    boundary.add_link("/var/lib/framenest/current", "/var/lib/framenest/catalog.sqlite3")
    boundary.add_dir("/var/cache/framenest")
    boundary.add_file("/var/cache/framenest/preview.bin", b"preview-bytes")


def test_production_control_state_does_not_block_the_state_copy() -> None:
    """The journal must not make a state-copy destination exist.

    At the baseline this fails inside ``copy_state`` because writing the journal
    created ``/var/lib/kronika``, which the copy requires absent.
    """
    plan = _plan()
    boundary = RemoteBoundary()
    _populate_state_sources(boundary)
    host = engine.RemoteMigrationHost(
        boundary,
        {"target": "nuc", "user": "op", "identity": "identity"},
        plan,
        engine_path=ENGINE_PATH,
        repository_root=REPOSITORY_ROOT,
    )
    host.write_recovery_manifest(plan.recovery_manifest())
    host.write_journal(engine.migration_journal_payload(plan))
    host.acquire_lock()
    try:
        copied = host.copy_state()
    finally:
        host.release_lock()
    assert copied == ["/var/lib/kronika", "/var/cache/kronika"]
    control = engine.MIGRATION_DIRECTORY.rstrip("/")
    for _source, destination in plan.copy_moves:
        normalized = destination.rstrip("/")
        assert control != normalized
        assert not control.startswith(normalized + "/")
        assert not normalized.startswith(control + "/")


def test_orchestration_acquires_the_exclusion_before_writing_control_state() -> None:
    """Control state is written only while the routine exclusion is held."""
    plan = _plan()
    host = SimulatedMigrationHost(plan)
    engine.run_migration_apply(host, plan)
    assert host.call_log.index("acquire_lock") < host.call_log.index(
        "write_recovery_manifest"
    )
    assert host.call_log.index("acquire_lock") < host.call_log.index("write_journal")


def test_migration_control_directory_is_outside_every_plan_destination() -> None:
    plan = _plan()
    assert engine.migration_control_conflicts(
        engine.MIGRATION_DIRECTORY, plan.copy_moves
    ) == ()
    control = engine.MIGRATION_DIRECTORY.rstrip("/")
    for _source, destination in plan.copy_moves:
        normalized = destination.rstrip("/")
        assert control != normalized
        assert not control.startswith(normalized + "/")
        assert not normalized.startswith(control + "/")


def test_control_location_guard_refuses_a_destination_that_contains_it() -> None:
    observation = _observation()
    conflicting = engine.MigrationObservation(
        **{
            **observation.__dict__,
            "copy_sources": (
                ("/var/lib/framenest", engine.MIGRATION_DIRECTORY),
            ),
        }
    )
    plan = engine.build_migration_plan(conflicting)
    boundary = RemoteBoundary()
    host = engine.RemoteMigrationHost(
        boundary,
        {"target": "nuc", "user": "op", "identity": "identity"},
        plan,
        engine_path=ENGINE_PATH,
        repository_root=REPOSITORY_ROOT,
    )
    assert engine.migration_control_conflicts(
        engine.MIGRATION_DIRECTORY, plan.copy_moves
    ) == (engine.MIGRATION_DIRECTORY,)
    with pytest.raises(engine.ReleaseError) as exc:
        host.write_journal(engine.migration_journal_payload(plan))
    assert exc.value.exit_code == engine.EXIT_MIGRATION
    assert boundary.calls == []


def test_production_exclusion_is_shared_with_routine_deploy_and_rollback() -> None:
    plan = _plan()
    boundary = RemoteBoundary()
    host = engine.RemoteMigrationHost(
        boundary,
        {"target": "nuc", "user": "op", "identity": "identity"},
        plan,
        engine_path=ENGINE_PATH,
        repository_root=REPOSITORY_ROOT,
    )
    host.acquire_lock()
    try:
        second = engine.RemoteMigrationHost(
            boundary,
            {"target": "nuc", "user": "op", "identity": "identity"},
            plan,
            engine_path=ENGINE_PATH,
            repository_root=REPOSITORY_ROOT,
        )
        with pytest.raises(engine.ReleaseError) as exc:
            second.acquire_lock()
        assert exc.value.exit_code == engine.EXIT_EXISTS
        with pytest.raises(engine.ReleaseError) as routine_exc:
            engine.acquire_deploy_lock(
                boundary,
                {"target": "nuc", "user": "op", "identity": "identity"},
                engine.deploy_lock_owner_record(),
            )
        assert routine_exc.value.exit_code == engine.EXIT_EXISTS
        assert not boundary.exists(engine.MIGRATION_JOURNAL_PATH)
    finally:
        host.release_lock()
    # The exclusion is free again once the first run released it.
    decision = engine.acquire_deploy_lock(
        boundary,
        {"target": "nuc", "user": "op", "identity": "identity"},
        engine.deploy_lock_owner_record(),
    )
    engine.release_deploy_lock(
        boundary,
        {"target": "nuc", "user": "op", "identity": "identity"},
        decision,
    )


def test_orchestration_refuses_and_preserves_an_existing_journal() -> None:
    plan = _plan()
    host = SimulatedMigrationHost(plan)
    host.existing_journal = {"outcome": "failed", "completed_phases": ["quiesce"]}
    with pytest.raises(engine.ReleaseError) as exc:
        engine.run_migration_apply(host, plan)
    assert exc.value.exit_code == engine.EXIT_MIGRATION
    assert host.journals == []
    assert host.locked is False
    assert "write_journal" not in host.call_log
    assert "write_recovery_manifest" not in host.call_log


def test_production_journal_write_interruption_preserves_the_previous_journal() -> None:
    plan = _plan()
    boundary = RemoteBoundary()
    host = _boundary_host(boundary)
    first = engine.migration_journal_payload(plan)
    host.write_journal(first)
    interrupted = dict(first)
    interrupted["current_phase"] = engine.MIGRATION_PHASE_QUIESCE

    boundary.fail_atomic_write = True
    with pytest.raises(engine.ReleaseError):
        host.write_journal(interrupted)
    boundary.fail_atomic_write = False
    assert host.read_journal() == first

    boundary.fail_atomic_rename = True
    with pytest.raises(engine.ReleaseError):
        host.write_journal(interrupted)
    boundary.fail_atomic_rename = False
    assert host.read_journal() == first


def test_production_journal_read_refuses_a_failed_query() -> None:
    boundary = RemoteBoundary()
    boundary.privilege_ok = False
    host = _boundary_host(boundary)
    with pytest.raises(engine.ReleaseError) as exc:
        host.read_journal()
    assert exc.value.exit_code == engine.EXIT_MIGRATION


def test_production_copy_state_refuses_a_populated_destination() -> None:
    boundary = RemoteBoundary()
    _populate_state_sources(boundary)
    boundary.add_file("/var/lib/kronika/unrelated.txt", b"keep-me")
    host = _boundary_host(boundary)
    with pytest.raises(engine.ReleaseError) as exc:
        host.copy_state()
    assert exc.value.exit_code == engine.EXIT_MIGRATION
    assert "not absent" in str(exc.value)
    assert boundary.paths["/var/lib/kronika/unrelated.txt"]["data"] == b"keep-me"
    assert all("cp -a" not in call for call in boundary.calls)
    assert all("rm " not in call for call in boundary.calls)


def test_production_copy_state_refuses_a_destination_with_a_failed_query() -> None:
    boundary = RemoteBoundary()
    _populate_state_sources(boundary)
    boundary.privilege_ok = False
    host = _boundary_host(boundary)
    with pytest.raises(engine.ReleaseError) as exc:
        host.copy_state()
    assert exc.value.exit_code == engine.EXIT_MIGRATION
    assert "unverifiable" in str(exc.value)
    assert all("cp -a" not in call for call in boundary.calls)


def _writer_units() -> dict[str, dict[str, object]]:
    return {
        "framenest.service": {
            "installed": True,
            "load": "loaded",
            "enabled": "enabled",
            "active": "active",
        },
        "framenest-catalog-backup.service": {
            "installed": True,
            "load": "loaded",
            "enabled": "static",
            "active": "inactive",
        },
        "framenest-catalog-backup.timer": {
            "installed": True,
            "load": "loaded",
            "enabled": "disabled",
            "active": "inactive",
        },
    }


def test_production_observe_writer_state_records_installation_and_schedule() -> None:
    boundary = RemoteBoundary()
    boundary.units = _writer_units()
    host = _boundary_host(boundary)
    journal = engine.migration_journal_payload(_plan())
    host.bind_journal(journal)
    observed = host.observe_writer_state()
    assert observed["framenest-catalog-backup.timer"] == {
        "installed": True,
        "load": "loaded",
        "enabled": "disabled",
        "active": "inactive",
    }
    assert journal["substeps"]["observed_units"] == observed


def test_production_observe_writer_state_refuses_a_failed_query() -> None:
    boundary = RemoteBoundary()
    boundary.units = _writer_units()
    boundary.privilege_ok = False
    host = _boundary_host(boundary)
    with pytest.raises(engine.ReleaseError) as exc:
        host.observe_writer_state()
    assert exc.value.exit_code == engine.EXIT_MIGRATION
    assert "query failed" in str(exc.value)


def test_production_observe_writer_state_refuses_an_absent_unit() -> None:
    boundary = RemoteBoundary()
    boundary.units = _writer_units()
    boundary.units["framenest-catalog-backup.timer"] = {
        "load": "not-found",
        "enabled": "",
        "active": "",
    }
    host = _boundary_host(boundary)
    with pytest.raises(engine.ReleaseError) as exc:
        host.observe_writer_state()
    assert exc.value.exit_code == engine.EXIT_MIGRATION
    assert "absent" in str(exc.value)


def test_production_writer_process_query_is_not_read_as_absence() -> None:
    boundary = RemoteBoundary()
    host = _boundary_host(boundary)

    boundary.ps_query_ok = False
    with pytest.raises(engine.ReleaseError) as exc:
        host.assert_no_legacy_writers()
    assert exc.value.exit_code == engine.EXIT_MIGRATION

    boundary.ps_query_ok = True
    boundary.process_counts["framenest"] = 2
    with pytest.raises(engine.ReleaseError):
        host.assert_no_legacy_writers()

    boundary.process_counts["framenest"] = 0
    host.assert_no_legacy_writers()


def test_production_pre_write_recovery_reverses_a_partial_rename() -> None:
    boundary = RemoteBoundary()
    boundary.groups = {"kronika": {"gid": 1001}}
    boundary.users = {
        "framenest": {"uid": 1001, "gid": 1001, "home": "/var/lib/framenest"}
    }
    boundary.units = _writer_units()
    host = _boundary_host(boundary)
    journal = {
        "completed_phases": list(engine.MIGRATION_PHASES[:8]),
        "substeps": {
            "group_renamed": True,
            "writers_stopped": ["framenest.service"],
            "observed_units": {
                unit: dict(state) for unit, state in _writer_units().items()
            },
        },
    }
    host.recover_pre_write(journal)
    transcript = "\n".join(boundary.calls)
    assert "groupmod -n framenest kronika" in transcript
    assert "usermod -l" not in transcript
    assert "systemctl start framenest.service" in transcript
    assert "systemctl enable framenest-catalog-backup.timer" not in transcript
    assert "systemctl start framenest-catalog-backup.timer" not in transcript
    assert set(boundary.groups) == {"framenest"}
    assert set(boundary.users) == {"framenest"}


def test_production_pre_write_recovery_restores_the_observed_home() -> None:
    boundary = RemoteBoundary()
    boundary.groups = {"kronika": {"gid": 1001}}
    boundary.users = {
        "kronika": {"uid": 1001, "gid": 1001, "home": "/var/lib/kronika"}
    }
    boundary.units = _writer_units()
    host = _boundary_host(boundary)
    journal = {
        "completed_phases": list(engine.MIGRATION_PHASES[:8]),
        "substeps": {
            "group_renamed": True,
            "user_renamed": True,
            "account_home": "/var/lib/kronika",
            "observed_units": {
                unit: dict(state) for unit, state in _writer_units().items()
            },
        },
    }
    host.recover_pre_write(journal)
    transcript = "\n".join(boundary.calls)
    assert "groupmod -n framenest kronika" in transcript
    assert "usermod -l framenest -d /var/lib/framenest kronika" in transcript
    assert boundary.users["framenest"]["home"] == "/var/lib/framenest"


def test_orchestration_startup_ack_failure_selects_post_write_recovery() -> None:
    plan = _plan()
    host = SimulatedMigrationHost(plan)
    host.fail_journal_once_after = engine.MIGRATION_PHASE_START_SERVICE
    with pytest.raises(engine.ReleaseError):
        engine.run_migration_apply(host, plan)
    assert host.recovered == "post-write"
    assert host.stale_state_restored is False
    assert host.forward_recovery_required is True
    assert host.account["name"] == "kronika"
    failed = host.journals[-1]
    assert failed["writes_possible"] is True
    assert failed["writes_admitted"] is True
    assert failed["outcome"] == "failed"


def test_orchestration_pre_cutover_ack_failure_selects_pre_write_recovery() -> None:
    plan = _plan()
    host = SimulatedMigrationHost(plan)
    host.fail_journal_once_after = engine.MIGRATION_PHASE_VERIFY_UNITS
    with pytest.raises(engine.ReleaseError):
        engine.run_migration_apply(host, plan)
    assert host.recovered == "pre-write"
    assert host.old_layout_restored is True
    assert host.journals[-1]["writes_possible"] is False


def test_start_service_entry_selects_post_write_without_a_completed_phase() -> None:
    """The durable boundary selects recovery even when no phase completed.

    Entering the start-service phase persists ``writes_possible`` first, so a
    crash inside that phase selects post-write recovery from the durable record,
    not from inference over a completed-phase list.
    """
    plan = _plan()
    host = SimulatedMigrationHost(plan, fail_phase="start_service")
    with pytest.raises(engine.ReleaseError):
        engine.run_migration_apply(host, plan)
    assert host.recovered == "post-write"
    failed = host.journals[-1]
    assert failed["writes_possible"] is True
    assert engine.MIGRATION_PHASE_START_SERVICE not in failed["completed_phases"]
    assert host.stale_state_restored is False


def test_production_partial_rename_is_recorded_and_reversible() -> None:
    """A rename interrupted between group and user is reversed from substeps."""
    boundary = RemoteBoundary()
    boundary.units = _writer_units()
    host = _boundary_host(boundary)
    journal = engine.migration_journal_payload(_plan())
    host.bind_journal(journal)
    boundary.fail_groupmod = True
    with pytest.raises(engine.ReleaseError):
        host.rename_account()
    assert journal["substeps"]["group_renamed"] is True
    assert "user_renamed" not in journal["substeps"]

    boundary.fail_groupmod = False
    host.recover_pre_write(journal)
    transcript = "\n".join(boundary.calls)
    assert "groupmod -n framenest kronika" in transcript
    assert "usermod -l" not in transcript


def test_writes_possible_reads_the_durable_boundary_first() -> None:
    assert engine.migration_writes_possible({"writes_possible": True}) is True
    assert engine.migration_writes_possible({"writes_admitted": True}) is True
    assert (
        engine.migration_writes_possible(
            {"completed_phases": [engine.MIGRATION_CUTOVER_PHASE]}
        )
        is True
    )
    assert engine.migration_writes_possible({"completed_phases": []}) is False


def test_production_post_write_recovery_preserves_current_state() -> None:
    boundary = RemoteBoundary()
    _populate_state_sources(boundary)
    host = _boundary_host(boundary)
    before = {path: dict(value) for path, value in boundary.paths.items()}
    host.recover_post_write(
        {"writes_possible": True, "completed_phases": [], "substeps": {}}
    )
    assert boundary.paths == before
    assert boundary.calls == []

    with pytest.raises(engine.ReleaseError) as exc:
        host.recover_post_write({"completed_phases": [], "substeps": {}})
    assert exc.value.exit_code == engine.EXIT_MIGRATION
    assert boundary.calls == []


def test_production_pre_write_recovery_falls_back_for_a_legacy_journal() -> None:
    boundary = RemoteBoundary()
    boundary.groups = {"kronika": {"gid": 1001}}
    boundary.users = {
        "kronika": {"uid": 1001, "gid": 1001, "home": "/var/lib/kronika"}
    }
    boundary.units = _writer_units()
    host = _boundary_host(boundary)
    host.recover_pre_write(
        {"completed_phases": list(engine.MIGRATION_PHASES[:8])}
    )
    transcript = "\n".join(boundary.calls)
    assert "groupmod -n framenest kronika" in transcript
    assert "usermod -l framenest -d /var/lib/framenest kronika" in transcript
    assert "systemctl start framenest.service" in transcript
    assert "systemctl enable framenest-catalog-backup.timer" in transcript
    assert "systemctl start framenest-catalog-backup.timer" in transcript
