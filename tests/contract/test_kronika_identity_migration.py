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
        self.journal = copy.deepcopy(payload)
        self.journals.append(copy.deepcopy(payload))

    def write_recovery_manifest(self, payload: dict[str, object]) -> None:
        self.recovery_manifest = copy.deepcopy(payload)

    def acquire_lock(self) -> str:
        self.locked = True
        return "simulated-owner"

    def release_lock(self) -> None:
        self.locked = False

    def recover_pre_write(self, journal: dict[str, object]) -> None:
        assert not engine.migration_is_post_write(list(journal["completed_phases"]))
        self.recovered = "pre-write"
        self.account["name"] = "framenest"
        self.account_count = 1
        self.service_state = "framenest"
        self.old_layout_restored = True
        self.stale_state_restored = False

    def recover_post_write(self, journal: dict[str, object]) -> None:
        assert engine.migration_is_post_write(list(journal["completed_phases"]))
        self.recovered = "post-write"
        self.forward_recovery_required = True
        self.stale_state_restored = False

    # -- phase methods ----------------------------------------------------

    def __getattr__(self, name: str):
        phase_methods = set(engine._MIGRATION_PHASE_METHODS.values())
        if name not in phase_methods:
            raise AttributeError(name)

        def phase_method():
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
    post_write = engine.migration_is_post_write(completed)
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
    assert "usermod -l framenest kronika" in transcript
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
