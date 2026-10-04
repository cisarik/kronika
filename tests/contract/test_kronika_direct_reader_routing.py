"""Contract tests that every direct ``FRAMENEST_`` reader goes through the resolver.

These modules each already accepted an explicit environment mapping, so the
routing is proved without mutating the process environment.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_catalog_backup_ops_config_reads_either_prefix(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        load_catalog_backup_ops_config,
    )

    old_only = load_catalog_backup_ops_config(
        {"FRAMENEST_CATALOG_BACKUP_ROOT": str(tmp_path / "old")}
    )
    new_only = load_catalog_backup_ops_config(
        {"KRONIKA_CATALOG_BACKUP_ROOT": str(tmp_path / "new")}
    )

    assert old_only.backup_root == tmp_path / "old"
    assert new_only.backup_root == tmp_path / "new"


def test_catalog_backup_ops_config_keeps_the_caller_default() -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        DEFAULT_BACKUP_ROOT,
        load_catalog_backup_ops_config,
    )

    assert load_catalog_backup_ops_config({}).backup_root == DEFAULT_BACKUP_ROOT


def test_catalog_backup_ops_config_treats_an_empty_value_as_unset(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        DEFAULT_BACKUP_ROOT,
        load_catalog_backup_ops_config,
    )

    config = load_catalog_backup_ops_config(
        {"KRONIKA_CATALOG_BACKUP_ROOT": "", "FRAMENEST_CATALOG_BACKUP_ROOT": ""}
    )

    assert config.backup_root == DEFAULT_BACKUP_ROOT


def test_catalog_backup_ops_config_fails_closed_on_a_conflict(tmp_path: Path) -> None:
    from kronika.identity_env import IdentityEnvironmentConflictError
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        CatalogBackupIdentityEnvironmentConflictError,
        load_catalog_backup_ops_config,
    )

    with pytest.raises(CatalogBackupIdentityEnvironmentConflictError) as excinfo:
        load_catalog_backup_ops_config(
            {
                "KRONIKA_CATALOG_BACKUP_ROOT": str(tmp_path / "a"),
                "FRAMENEST_CATALOG_BACKUP_ROOT": str(tmp_path / "b"),
            }
        )

    cause = excinfo.value.__cause__
    assert isinstance(cause, IdentityEnvironmentConflictError)
    assert cause.suffix == "CATALOG_BACKUP_ROOT"
    assert excinfo.value.exit_status == 2
    assert "CATALOG_BACKUP_ROOT" in str(excinfo.value)
    assert str(tmp_path / "a") not in str(excinfo.value)
    assert str(tmp_path / "b") not in str(excinfo.value)


def test_offdevice_destination_id_reads_either_prefix() -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        parse_configured_destination_id,
    )

    destination_id = "0123456789abcdef0123456789abcdef"

    assert (
        parse_configured_destination_id(
            {"FRAMENEST_CATALOG_OFFDEVICE_DESTINATION_ID": destination_id}
        )
        == destination_id
    )
    assert (
        parse_configured_destination_id(
            {"KRONIKA_CATALOG_OFFDEVICE_DESTINATION_ID": destination_id}
        )
        == destination_id
    )
    assert parse_configured_destination_id({}) is None
    assert parse_configured_destination_id({"KRONIKA_CATALOG_OFFDEVICE_DESTINATION_ID": ""}) is None


def test_offdevice_destination_id_fails_closed_on_a_conflict() -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        parse_configured_destination_id,
    )

    with pytest.raises(OffdeviceError) as excinfo:
        parse_configured_destination_id(
            {
                "KRONIKA_CATALOG_OFFDEVICE_DESTINATION_ID": "0" * 32,
                "FRAMENEST_CATALOG_OFFDEVICE_DESTINATION_ID": "1" * 32,
            }
        )

    assert excinfo.value.error_code == "OFFDEVICE_DESTINATION_ID_INVALID"
    assert excinfo.value.exit_status == 2
    rendered = str(excinfo.value)
    assert "CATALOG_OFFDEVICE_DESTINATION_ID" in rendered
    assert "0" * 32 not in rendered
    assert "1" * 32 not in rendered


def test_ai_config_path_reads_either_prefix(tmp_path: Path) -> None:
    from kronika.infrastructure.ai.configuration import (
        AiConfigurationError,
        default_ai_config_path,
    )

    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"

    assert default_ai_config_path({"FRAMENEST_AI_CONFIG_PATH": str(old_path)}) == old_path
    assert default_ai_config_path({"KRONIKA_AI_CONFIG_PATH": str(new_path)}) == new_path


def test_ai_config_path_fails_closed_on_a_conflict(tmp_path: Path) -> None:
    from kronika.infrastructure.ai.configuration import (
        AiConfigurationError,
        default_ai_config_path,
    )

    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    with pytest.raises(AiConfigurationError) as excinfo:
        default_ai_config_path(
            {"KRONIKA_AI_CONFIG_PATH": str(first), "FRAMENEST_AI_CONFIG_PATH": str(second)}
        )

    rendered = str(excinfo.value)
    assert "AI_CONFIG_PATH" in rendered
    assert str(first) not in rendered
    assert str(second) not in rendered


def test_development_runtime_paths_read_either_prefix(tmp_path: Path) -> None:
    from kronika.infrastructure.runtime.development import resolve_development_paths

    old = resolve_development_paths(
        environ={"FRAMENEST_DATABASE_PATH": str(tmp_path / "old.sqlite3")},
        platform_name="linux",
        home=tmp_path,
    )
    new = resolve_development_paths(
        environ={"KRONIKA_DATABASE_PATH": str(tmp_path / "new.sqlite3")},
        platform_name="linux",
        home=tmp_path,
    )

    assert old.database_path == tmp_path / "old.sqlite3"
    assert new.database_path == tmp_path / "new.sqlite3"


def test_development_runtime_dirs_read_either_prefix(tmp_path: Path) -> None:
    from kronika.infrastructure.runtime.development import resolve_development_paths

    resolved = resolve_development_paths(
        environ={
            "FRAMENEST_DEVELOPMENT_RUNTIME_DIR": str(tmp_path / "old-runtime"),
            "KRONIKA_DEVELOPMENT_LOG_DIR": str(tmp_path / "new-logs"),
        },
        platform_name="linux",
        home=tmp_path,
    )

    assert resolved.runtime_dir == tmp_path / "old-runtime"
    assert resolved.log_path == tmp_path / "new-logs" / "server.log"


def test_development_port_reads_either_prefix() -> None:
    from kronika.infrastructure.runtime.development import (
        DEFAULT_PORT,
        DevelopmentRuntimeError,
        selected_development_port,
    )

    assert selected_development_port({"FRAMENEST_PORT": "9101"}) == 9101
    assert selected_development_port({"KRONIKA_PORT": "9102"}) == 9102
    assert selected_development_port({}) == DEFAULT_PORT
    assert selected_development_port({"KRONIKA_PORT": ""}) == DEFAULT_PORT

    with pytest.raises(DevelopmentRuntimeError) as excinfo:
        selected_development_port(
            {"KRONIKA_PORT": "9103", "FRAMENEST_PORT": "9104"}
        )

    assert excinfo.value.exit_status == 2
    rendered = str(excinfo.value)
    assert "PORT" in rendered
    assert "9103" not in rendered
    assert "9104" not in rendered


def test_development_spawned_child_environment_keeps_the_old_names(tmp_path: Path) -> None:
    from kronika.infrastructure.runtime import development

    assert development.DATABASE_ENV == "FRAMENEST_DATABASE_PATH"
    assert development.PORT_ENV == "FRAMENEST_PORT"
    assert development.RUNTIME_DIR_ENV == "FRAMENEST_DEVELOPMENT_RUNTIME_DIR"
    assert development.LOG_DIR_ENV == "FRAMENEST_DEVELOPMENT_LOG_DIR"


def test_ai_config_environment_name_constant_is_unchanged() -> None:
    from kronika.infrastructure.ai.configuration import AI_CONFIG_PATH_ENVIRONMENT_NAME

    assert AI_CONFIG_PATH_ENVIRONMENT_NAME == "FRAMENEST_AI_CONFIG_PATH"


def test_env_file_environment_variable_constant_is_unchanged() -> None:
    from kronika.configuration import ENV_FILE_ENVIRONMENT_VARIABLE

    assert ENV_FILE_ENVIRONMENT_VARIABLE == "FRAMENEST_ENV_FILE"
