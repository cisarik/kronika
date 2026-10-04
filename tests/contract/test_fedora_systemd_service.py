"""Contract tests for repository-native systemd deployment artifacts."""

from __future__ import annotations

import configparser
import re
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SERVICE_PATH = REPOSITORY_ROOT / "deploy" / "systemd" / "framenest.service"
ENV_TEMPLATE_PATH = REPOSITORY_ROOT / "deploy" / "systemd" / "framenest.env.example"
NVIDIA_CREDENTIAL_DROPIN_PATH = (
    REPOSITORY_ROOT / "deploy" / "systemd" / "framenest-ai-credential-nvidia-nim.conf"
)
VERCEL_CREDENTIAL_DROPIN_PATH = (
    REPOSITORY_ROOT / "deploy" / "systemd" / "framenest-ai-credential-vercel-ai-gateway.conf"
)
FEDORA_DOC_PATH = REPOSITORY_ROOT / "docs" / "FEDORA_SERVICE.md"
UBUNTU_DOC_PATH = REPOSITORY_ROOT / "docs" / "UBUNTU_NUC_DEPLOYMENT.md"
FEDORA_ADR_PATH = REPOSITORY_ROOT / "docs" / "adr" / "0031-fedora-systemd-service-foundation.md"
UBUNTU_ADR_PATH = REPOSITORY_ROOT / "docs" / "adr" / "0032-ubuntu-nuc-deployment-foundation.md"
ADR_INDEX_PATH = REPOSITORY_ROOT / "docs" / "adr" / "README.md"
UBUNTU_SUPPORT_PATH = REPOSITORY_ROOT / "deploy" / "ubuntu" / "README.md"

INSTALL_ROOT = "/opt/framenest/current"
PRODUCTION_BIN = f"{INSTALL_ROOT}/.venv/bin/framenest-production"
SUPERSEDED_INSTALL_BIN = "/opt/" + "framenest/.venv"
SUPERSEDED_AI_CONFIG = "/etc/" + "framenest/ai/config.json"
REMOVED_SECRET_ENVIRONMENT = "framenest-" + "secrets.env"
OPTIONAL_ENVIRONMENT_FILE_PREFIX = "EnvironmentFile" + "=-"


def _read_service() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    assert parser.read(SERVICE_PATH, encoding="utf-8") == [str(SERVICE_PATH)]
    return parser


def _service_text() -> str:
    return SERVICE_PATH.read_text(encoding="utf-8")


def _env_template_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in ENV_TEMPLATE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def test_systemd_service_uses_current_release_working_directory() -> None:
    service = _read_service()["Service"]

    assert service["User"] == "framenest"
    assert service["Group"] == "framenest"
    assert service["WorkingDirectory"] == INSTALL_ROOT


def test_systemd_service_uses_production_executable_for_readiness_and_serve() -> None:
    service = _read_service()["Service"]

    assert service["ExecStartPre"] == f"{PRODUCTION_BIN} check-database-ready"
    assert service["ExecStart"] == f"{PRODUCTION_BIN} serve"
    assert service["ExecStartPre"].startswith(f"{INSTALL_ROOT}/.venv/bin/")
    assert service["ExecStart"].startswith(f"{INSTALL_ROOT}/.venv/bin/")


def test_systemd_service_avoids_development_wrappers_and_shells() -> None:
    text = _service_text()

    forbidden = [
        "./framenest",
        "framenest-server",
        "poetry",
        "uv ",
        "fish",
        "open ",
        "xdg-open",
        "/bin/sh",
        "/usr/bin/env",
    ]
    for marker in forbidden:
        assert marker not in text


def test_systemd_environment_file_contract_contains_no_secret_file() -> None:
    text = _service_text()

    assert "EnvironmentFile=/etc/framenest/framenest.env" in text
    assert "LoadCredential" not in text
    assert OPTIONAL_ENVIRONMENT_FILE_PREFIX not in text
    assert REMOVED_SECRET_ENVIRONMENT not in text
    assert "NVIDIA_API_KEY" not in text
    assert "AI_GATEWAY_API_KEY" not in text


def test_systemd_service_directory_boundaries_are_mutable_state_cache_runtime_only() -> None:
    service = _read_service()["Service"]
    text = _service_text()

    assert service["StateDirectory"] == "framenest"
    assert service["StateDirectoryMode"] == "0700"
    assert service["CacheDirectory"] == "framenest"
    assert service["RuntimeDirectory"] == "framenest"
    assert "ConfigurationDirectory" not in service
    assert "LogsDirectory" not in service
    assert "ReadWritePaths" not in text
    assert "/media" not in text
    assert "/mnt" not in text
    assert "/home" not in text


def test_systemd_service_lifecycle_uses_bounded_sigterm_and_failure_restart() -> None:
    service = _read_service()["Service"]

    assert service["KillSignal"] == "SIGTERM"
    assert service["TimeoutStopSec"] == "30s"
    assert service["Restart"] == "on-failure"
    assert service["RestartSec"] == "5s"


def test_systemd_service_logs_to_journald_explicitly() -> None:
    service = _read_service()["Service"]

    assert service["StandardOutput"] == "journal"
    assert service["StandardError"] == "journal"


def test_systemd_service_removes_capabilities_and_retains_compatible_hardening() -> None:
    service = _read_service()["Service"]

    assert service["CapabilityBoundingSet"] == ""
    assert service["AmbientCapabilities"] == ""
    assert service["NoNewPrivileges"] == "true"
    assert service["PrivateTmp"] == "true"
    assert service["ProtectSystem"] == "strict"
    assert service["ProtectHome"] == "read-only"
    assert service["ProtectControlGroups"] == "true"
    assert service["ProtectKernelModules"] == "true"
    assert service["ProtectKernelTunables"] == "true"
    assert service["RestrictSUIDSGID"] == "true"
    assert service["LockPersonality"] == "true"
    assert service["UMask"] == "0077"


def test_non_secret_environment_template_is_loopback_first_and_path_explicit() -> None:
    values = _env_template_values()

    assert values == {
        "FRAMENEST_HOST": "127.0.0.1",
        "FRAMENEST_PORT": "8000",
        "FRAMENEST_DATABASE_PATH": "/var/lib/framenest/catalog.sqlite3",
        "FRAMENEST_GALLERY_PREVIEW_CACHE_PATH": "/var/cache/framenest/gallery-previews",
        "FRAMENEST_COVER_STORAGE_ROOT": "/var/lib/framenest/covers",
        "FRAMENEST_COVER_THUMBNAIL_CACHE_PATH": "/var/cache/framenest/cover-thumbnails",
        "FRAMENEST_AI_CONFIG_PATH": "/var/lib/framenest/ai/config.json",
        "FRAMENEST_CATALOG_BACKUP_ROOT": "/var/lib/framenest/catalog-backups",
        "FRAMENEST_CATALOG_RESTORE_VERIFY_ROOT": "/var/lib/framenest/catalog-restore-verify",
        "FRAMENEST_CATALOG_BACKUP_OPS_ROOT": "/var/lib/framenest/catalog-backup-ops",
        "FRAMENEST_CATALOG_BACKUP_KEEP_AUTO": "30",
    }


def test_youtube_staging_uses_existing_state_directory_without_unit_broadening() -> None:
    service = _read_service()["Service"]
    text = _service_text()
    environment_text = ENV_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert service["StateDirectory"] == "framenest"
    assert (
        "# FRAMENEST_YOUTUBE_ACQUISITION_ROOT="
        "/var/lib/framenest/youtube-acquisition"
        in environment_text
    )
    assert "ReadWritePaths" not in text
    assert "/srv/media" not in text


def test_service_artifacts_do_not_encode_public_network_or_proxy_shortcuts() -> None:
    combined = _service_text() + "\n" + ENV_TEMPLATE_PATH.read_text(encoding="utf-8")

    forbidden = [
        "0.0.0.0",
        "funnel",
        "firewall",
        "firewalld",
        "reverse-proxy",
        "proxy_pass",
        "ReadWritePaths",
    ]
    for marker in forbidden:
        assert marker.lower() not in combined.lower()


def test_environment_template_documents_tailscale_ingress_as_commented_options() -> None:
    text = ENV_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "# FRAMENEST_INGRESS_MODE=tailscale_uds" in text
    assert "# FRAMENEST_UDS_PATH=/run/framenest/framenest.sock" in text
    assert "# FRAMENEST_EXTERNAL_ORIGIN=https://<node>.<tailnet>.ts.net" in text
    assert "# FRAMENEST_IDENTITY_MAP=" in text
    assert "# FRAMENEST_COMPANION_EXTENSION_ORIGINS=" in text
    assert "funnel" not in text.lower()
    for line in text.splitlines():
        assert not line.startswith("FRAMENEST_INGRESS_MODE")
        assert not line.startswith("FRAMENEST_UDS_PATH")
        assert not line.startswith("FRAMENEST_EXTERNAL_ORIGIN")
        assert not line.startswith("FRAMENEST_IDENTITY_MAP")
        assert not line.startswith("FRAMENEST_COMPANION_EXTENSION_ORIGINS")


def test_non_secret_environment_template_contains_no_secret_variables() -> None:
    text = ENV_TEMPLATE_PATH.read_text(encoding="utf-8")

    forbidden = {
        "NVIDIA_API_KEY",
        "AI_GATEWAY_API_KEY",
        "PASSWORD",
        "TOKEN",
        "AUTHORIZATION",
        "COOKIE",
        "PRIVATE_KEY",
        REMOVED_SECRET_ENVIRONMENT,
    }
    for marker in forbidden:
        assert marker not in text
    assert re.search(r"YOUR|REPLACE|<redacted>|secret-value", text, re.IGNORECASE) is None


def test_optional_systemd_ai_credential_dropins_are_provider_specific_and_secret_free() -> None:
    dropins = {
        NVIDIA_CREDENTIAL_DROPIN_PATH: (
            "NVIDIA_API_KEY",
            "/etc/framenest/credentials/NVIDIA_API_KEY",
        ),
        VERCEL_CREDENTIAL_DROPIN_PATH: (
            "AI_GATEWAY_API_KEY",
            "/etc/framenest/credentials/AI_GATEWAY_API_KEY",
        ),
    }

    for path, (identity, credential_path) in dropins.items():
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        assert parser.read(path, encoding="utf-8") == [str(path)]
        service = parser["Service"]
        text = path.read_text(encoding="utf-8")

        assert service["LoadCredential"] == f"{identity}:{credential_path}"
        assert "[Service]" in text
        assert "Environment=" not in text
        assert "EnvironmentFile" not in text
        assert "YOUR" not in text
        assert "secret-value" not in text
        assert "0.0.0.0" not in text
        assert "tailscale" not in text.lower()
        assert "ufw" not in text.lower()


def test_production_console_script_is_declared() -> None:
    pyproject = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert (
        pyproject["project"]["scripts"]["framenest-production"]
        == "kronika.infrastructure.runtime.production:main"
    )


def test_fedora_operator_documentation_is_clearly_superseded() -> None:
    text = FEDORA_DOC_PATH.read_text(encoding="utf-8")

    assert "superseded" in text.lower()
    assert "not the current deployment workflow" in text
    assert "docs/UBUNTU_NUC_DEPLOYMENT.md" in text
    assert "ADR-0032" in text
    assert "not an installer" in text
    assert "install, enable, start, stop,\nreload, or inspect a real service" in text
    assert INSTALL_ROOT in text
    assert f"{PRODUCTION_BIN} serve" in text
    assert "/var/lib/framenest/catalog.sqlite3" in text
    assert "/var/cache/framenest/gallery-previews" in text
    assert "/var/lib/framenest/ai/config.json" in text
    assert "/etc/framenest/framenest.env" in text
    assert "/run/framenest" in text
    assert "./framenest` launcher remains macOS/local browser-development" in text
    assert REMOVED_SECRET_ENVIRONMENT not in text
    assert SUPERSEDED_AI_CONFIG not in text
    assert SUPERSEDED_INSTALL_BIN not in text


def test_fedora_adr_remains_historical_and_ubuntu_adr_supersedes_it() -> None:
    fedora_text = FEDORA_ADR_PATH.read_text(encoding="utf-8")
    ubuntu_text = UBUNTU_ADR_PATH.read_text(encoding="utf-8")
    index_text = ADR_INDEX_PATH.read_text(encoding="utf-8")

    assert "Status\n\n`Accepted`" in fedora_text
    assert "Fedora systemd Service Foundation | Superseded by [ADR-0032]" in index_text
    assert "0032 | Ubuntu NUC Deployment Foundation | Accepted | 2026-07-08" in index_text
    assert "This ADR supersedes [ADR-0031]" in ubuntu_text
    assert "Ubuntu Server 24.04" in ubuntu_text
    assert "Intel NUC6i5SYH" in ubuntu_text
    assert "personal production server" in ubuntu_text
    assert INSTALL_ROOT in ubuntu_text
    assert f"{PRODUCTION_BIN} check-database-ready" in ubuntu_text
    assert f"{PRODUCTION_BIN} serve" in ubuntu_text
    assert "read-only readiness gate" in ubuntu_text
    assert "env_file=None" in fedora_text
    assert "/var/lib/framenest/ai/config.json" in ubuntu_text
    assert REMOVED_SECRET_ENVIRONMENT not in ubuntu_text
    assert SUPERSEDED_AI_CONFIG not in ubuntu_text
    assert SUPERSEDED_INSTALL_BIN not in ubuntu_text
    assert "systemd credentials" in ubuntu_text
    assert "Tailscale" in ubuntu_text
    assert "never run migrations implicitly" in ubuntu_text


def test_ubuntu_adr_records_secure_python_strategy_without_unsafe_installers() -> None:
    text = UBUNTU_ADR_PATH.read_text(encoding="utf-8")

    assert "CPython `3.13.14`" in text
    assert "`uv` release version" in text
    assert "`0.11.28`" in text
    assert "sha256.sum" in text
    assert "gh attestation verify" in text
    assert "do not replace `/usr/bin/python3`" in text
    assert "Poetry" in text
    assert "/opt/framenest/current/.venv" in text
    assert "future Ubuntu VPS" in text
    assert "curl ... | sh" in text
    assert "wget ... | sh" in text
    assert "unreviewed PPA" in text


def test_ubuntu_runbook_has_auditable_phase_and_safety_boundaries() -> None:
    text = UBUNTU_DOC_PATH.read_text(encoding="utf-8")

    for heading in [
        "## 0. Preconditions And Authority",
        "## 1. Check",
        "## 2. Plan",
        "## 3. Prepare Release",
        "## 4. Apply One Bounded Change",
        "## 5. Migrate",
        "## 6. Readiness Verification",
        "## 7. Controlled Activation",
        "## 8. Health And Log Verification",
        "## 9. Rollback",
        "## 10. Evidence Capture",
    ]:
        assert heading in text

    for marker in [
        "Read-only checks",
        "Planned mutations",
        "Planned reversible mutations",
        "Service-affecting mutation",
        "Stop conditions",
        "Evidence",
        "Threat:",
        "Benefit:",
        "Limitation:",
        "Rollback:",
        "Verification:",
    ]:
        assert marker in text

    assert "/srv/media" in text
    assert "service-writable by default" in text
    assert "Tailscale" in text
    assert "Do not capture or share" in text


def test_ubuntu_support_map_makes_required_phases_discoverable() -> None:
    text = UBUNTU_SUPPORT_PATH.read_text(encoding="utf-8")

    assert "docs/UBUNTU_NUC_DEPLOYMENT.md" in text
    assert "ADR-0032" in text
    assert "fn-production-env-deploy" in text
    for phase in ["check", "plan", "apply", "verify", "rollback"]:
        assert f"| {phase} |" in text
    assert "must not be used as a general environment-file copier" in text
