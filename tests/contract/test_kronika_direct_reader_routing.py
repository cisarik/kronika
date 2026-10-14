"""Contract tests that direct readers accept canonical inputs and ignore ``FRAMENEST_`` inputs.

These modules each already accepted an explicit environment mapping, so the
routing is proved without mutating the process environment.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_catalog_backup_ops_config_reads_only_the_canonical_prefix(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        load_catalog_backup_ops_config,
    )

    old_only = load_catalog_backup_ops_config(
        {"FRAMENEST_CATALOG_BACKUP_ROOT": str(tmp_path / "old")}
    )
    new_only = load_catalog_backup_ops_config(
        {"KRONIKA_CATALOG_BACKUP_ROOT": str(tmp_path / "new")}
    )

    assert old_only.backup_root == load_catalog_backup_ops_config({}).backup_root
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


def test_catalog_backup_ops_config_uses_canonical_input_in_a_mixed_pair(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import load_catalog_backup_ops_config

    config = load_catalog_backup_ops_config({
        "KRONIKA_CATALOG_BACKUP_ROOT": str(tmp_path / "a"),
        "FRAMENEST_CATALOG_BACKUP_ROOT": str(tmp_path / "b"),
    })
    assert config.backup_root == tmp_path / "a"


def test_offdevice_destination_id_reads_only_the_canonical_prefix() -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        parse_configured_destination_id,
    )

    destination_id = "0123456789abcdef0123456789abcdef"

    assert (
        parse_configured_destination_id(
            {"FRAMENEST_CATALOG_OFFDEVICE_DESTINATION_ID": destination_id}
        )
        is None
    )
    assert (
        parse_configured_destination_id(
            {"KRONIKA_CATALOG_OFFDEVICE_DESTINATION_ID": destination_id}
        )
        == destination_id
    )
    assert parse_configured_destination_id({}) is None
    assert parse_configured_destination_id({"KRONIKA_CATALOG_OFFDEVICE_DESTINATION_ID": ""}) is None


def test_offdevice_destination_id_uses_canonical_input_in_a_mixed_pair() -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import parse_configured_destination_id

    assert parse_configured_destination_id({
        "KRONIKA_CATALOG_OFFDEVICE_DESTINATION_ID": "0" * 32,
        "FRAMENEST_CATALOG_OFFDEVICE_DESTINATION_ID": "1" * 32,
    }) == "0" * 32


def test_ai_config_path_reads_only_the_canonical_prefix(tmp_path: Path) -> None:
    from kronika.infrastructure.ai.configuration import (
        AiConfigurationError,
        default_ai_config_path,
    )

    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"

    assert default_ai_config_path({"FRAMENEST_AI_CONFIG_PATH": str(old_path)}) == default_ai_config_path({})
    assert default_ai_config_path({"KRONIKA_AI_CONFIG_PATH": str(new_path)}) == new_path


def test_ai_config_path_uses_canonical_input_in_a_mixed_pair(tmp_path: Path) -> None:
    from kronika.infrastructure.ai.configuration import default_ai_config_path

    first, second = tmp_path / "first.json", tmp_path / "second.json"
    assert default_ai_config_path({
        "KRONIKA_AI_CONFIG_PATH": str(first), "FRAMENEST_AI_CONFIG_PATH": str(second),
    }) == first


def test_development_runtime_paths_read_only_the_canonical_prefix(tmp_path: Path) -> None:
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

    assert old.database_path == tmp_path / ".local" / "share" / "Kronika" / "development" / "catalog.sqlite3"
    assert new.database_path == tmp_path / "new.sqlite3"


def test_development_runtime_dirs_read_only_the_canonical_prefix(tmp_path: Path) -> None:
    from kronika.infrastructure.runtime.development import resolve_development_paths

    resolved = resolve_development_paths(
        environ={
            "FRAMENEST_DEVELOPMENT_RUNTIME_DIR": str(tmp_path / "old-runtime"),
            "KRONIKA_DEVELOPMENT_LOG_DIR": str(tmp_path / "new-logs"),
        },
        platform_name="linux",
        home=tmp_path,
    )

    assert resolved.runtime_dir == tmp_path / ".local" / "state" / "Kronika" / "development" / "runtime"
    assert resolved.log_path == tmp_path / "new-logs" / "server.log"


def test_development_port_reads_only_the_canonical_prefix() -> None:
    from kronika.infrastructure.runtime.development import (
        DEFAULT_PORT,
        DevelopmentRuntimeError,
        selected_development_port,
    )

    assert selected_development_port({"FRAMENEST_PORT": "9101"}) == DEFAULT_PORT
    assert selected_development_port({"KRONIKA_PORT": "9102"}) == 9102
    assert selected_development_port({}) == DEFAULT_PORT
    assert selected_development_port({"KRONIKA_PORT": ""}) == DEFAULT_PORT

    assert selected_development_port(
        {"KRONIKA_PORT": "9103", "FRAMENEST_PORT": "9104"}
    ) == 9103


def test_development_spawned_child_environment_uses_the_canonical_names(tmp_path: Path) -> None:
    from kronika.infrastructure.runtime import development

    assert development.DATABASE_ENV == "KRONIKA_DATABASE_PATH"
    assert development.PORT_ENV == "KRONIKA_PORT"
    assert development.RUNTIME_DIR_ENV == "KRONIKA_DEVELOPMENT_RUNTIME_DIR"
    assert development.LOG_DIR_ENV == "KRONIKA_DEVELOPMENT_LOG_DIR"


def test_ai_config_environment_name_constant_is_canonical() -> None:
    from kronika.infrastructure.ai.configuration import AI_CONFIG_PATH_ENVIRONMENT_NAME

    assert AI_CONFIG_PATH_ENVIRONMENT_NAME == "KRONIKA_AI_CONFIG_PATH"


def test_env_file_environment_variable_constant_is_canonical() -> None:
    from kronika.configuration import ENV_FILE_ENVIRONMENT_VARIABLE

    assert ENV_FILE_ENVIRONMENT_VARIABLE == "KRONIKA_ENV_FILE"
