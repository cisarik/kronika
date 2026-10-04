"""Behavioral tests for the centralized FrameNest settings boundary."""

from __future__ import annotations

import json
import logging
import logging.config
import subprocess
import tempfile
from io import StringIO
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from tests.support.kronika_identity import expected

from framenest.configuration import FrameNestSettings, load_settings

FRAMENEST_ENV_VARS = (
    "FRAMENEST_HOST",
    "FRAMENEST_PORT",
    "FRAMENEST_API_KEY",
    "FRAMENEST_DATABASE_PATH",
    "FRAMENEST_UPLOAD_QUARANTINE_ROOT",
    "FRAMENEST_UPLOAD_MAX_TOTAL_BYTES",
    "FRAMENEST_UPLOAD_MAX_PATCH_BYTES",
    "FRAMENEST_UPLOAD_SESSION_TTL_SECONDS",
    "FRAMENEST_UPLOAD_MIN_FREE_SPACE_RESERVE_BYTES",
    "FRAMENEST_YOUTUBE_ACQUISITION_ROOT",
    "FRAMENEST_YOUTUBE_ACQUISITION_MAX_STAGING_BYTES",
    "FRAMENEST_AI_PROVIDER_ID",
    "FRAMENEST_AI_MODEL_ID",
    "FRAMENEST_AUTOMATIC_MEDIA_ANALYSIS_ENABLED",
    "FRAMENEST_AUTOMATIC_MEDIA_ANALYSIS_MAX_ATTEMPTS",
)


def test_overlapping_private_storage_roots_report_the_derived_brand(
    tmp_path: Path,
) -> None:
    """The overlap refusal, reached with a cache root that contains a cover root."""
    cache_root = tmp_path / "private"

    with pytest.raises(ValidationError) as excinfo:
        FrameNestSettings(
            _env_file=None,
            gallery_preview_cache_path=cache_root,
            cover_storage_root=cache_root / "covers",
        )

    assert expected("{brand} private storage paths must not overlap") in str(
        excinfo.value
    )


@pytest.fixture(autouse=True)
def isolate_framenest_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove FrameNest configuration variables before and after each test."""
    for variable in FRAMENEST_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)


def test_settings_load_without_real_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = load_settings(env_file=None)
    assert settings.host == "127.0.0.1"


def test_default_host_is_loopback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = load_settings(env_file=None)
    assert settings.host == "127.0.0.1"


def test_default_port_is_8000(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = load_settings(env_file=None)
    assert settings.port == 8000


def test_database_path_is_typed_path_and_safe_temporary_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    configured_temp_root = tmp_path / "system-temp"
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(configured_temp_root))

    settings = load_settings(env_file=None)

    assert isinstance(settings.database_path, Path)
    assert settings.database_path == (
        configured_temp_root / "framenest-development" / "catalog.sqlite3"
    )
    assert settings.database_path.is_absolute()
    assert Path(__file__).resolve().parents[2] not in settings.database_path.parents


def test_default_database_path_loading_creates_no_directory_or_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    configured_temp_root = tmp_path / "system-temp"
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(configured_temp_root))

    settings = load_settings(env_file=None)

    assert settings.database_path.parent == configured_temp_root / "framenest-development"
    assert not settings.database_path.parent.exists()
    assert not settings.database_path.exists()


def test_temporary_env_file_sets_database_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    configured_path = tmp_path / "configured" / "catalog.sqlite3"
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"FRAMENEST_DATABASE_PATH={configured_path}\n",
        encoding="utf-8",
    )

    settings = load_settings(env_file=env_file)

    assert settings.database_path == configured_path


def test_process_environment_overrides_temporary_env_file_database_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    env_file_path = tmp_path / "env-file" / "catalog.sqlite3"
    process_path = tmp_path / "process-env" / "catalog.sqlite3"
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"FRAMENEST_DATABASE_PATH={env_file_path}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(process_path))

    settings = load_settings(env_file=env_file)

    assert settings.database_path == process_path


def test_upload_quarantine_settings_are_optional_bounded_and_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "quarantine"
    monkeypatch.setenv("FRAMENEST_UPLOAD_QUARANTINE_ROOT", str(root))
    monkeypatch.setenv("FRAMENEST_UPLOAD_MAX_TOTAL_BYTES", "123")
    monkeypatch.setenv("FRAMENEST_UPLOAD_MAX_PATCH_BYTES", "12")
    monkeypatch.setenv("FRAMENEST_UPLOAD_SESSION_TTL_SECONDS", "30")
    monkeypatch.setenv("FRAMENEST_UPLOAD_MIN_FREE_SPACE_RESERVE_BYTES", "7")

    settings = load_settings(env_file=None)

    assert settings.upload_quarantine_root == root
    assert settings.upload_max_total_bytes == 123
    assert settings.upload_max_patch_bytes == 12
    assert settings.upload_session_ttl_seconds == 30
    assert settings.upload_min_free_space_reserve_bytes == 7
    assert str(root) not in f"{settings!r}{settings!s}"


def test_relative_upload_quarantine_root_is_rejected_without_path_echo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    supplied = "relative/private/quarantine"
    monkeypatch.setenv("FRAMENEST_UPLOAD_QUARANTINE_ROOT", supplied)

    with pytest.raises(ValidationError) as exc_info:
        load_settings(env_file=None)

    assert supplied not in str(exc_info.value)


def test_youtube_staging_configuration_is_absolute_private_and_non_overlapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state"
    youtube_root = state_root / "youtube"
    database_path = state_root / "catalog.sqlite3"
    quarantine_root = tmp_path / "quarantine"
    monkeypatch.setenv("FRAMENEST_YOUTUBE_ACQUISITION_ROOT", str(youtube_root))
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(database_path))
    monkeypatch.setenv("FRAMENEST_UPLOAD_QUARANTINE_ROOT", str(quarantine_root))
    monkeypatch.setenv(
        "FRAMENEST_YOUTUBE_ACQUISITION_MAX_STAGING_BYTES",
        "12345",
    )

    settings = load_settings(env_file=None)

    assert settings.youtube_acquisition_root == youtube_root
    assert settings.database_path == database_path
    assert settings.youtube_acquisition_max_staging_bytes == 12345
    assert str(youtube_root) not in f"{settings!r}{settings!s}"

    monkeypatch.setenv(
        "FRAMENEST_UPLOAD_QUARANTINE_ROOT",
        str(youtube_root / "quarantine"),
    )
    with pytest.raises(ValidationError) as exc_info:
        load_settings(env_file=None)
    assert str(youtube_root) not in str(exc_info.value)

    monkeypatch.setenv("FRAMENEST_UPLOAD_QUARANTINE_ROOT", str(quarantine_root))
    monkeypatch.setenv("FRAMENEST_YOUTUBE_ACQUISITION_ROOT", str(state_root))
    with pytest.raises(ValidationError) as exc_info:
        load_settings(env_file=None)
    assert str(state_root) not in str(exc_info.value)


def test_relative_youtube_staging_root_is_rejected_without_path_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supplied = "relative/private/youtube"
    monkeypatch.setenv("FRAMENEST_YOUTUBE_ACQUISITION_ROOT", supplied)

    with pytest.raises(ValidationError) as exc_info:
        load_settings(env_file=None)

    assert supplied not in str(exc_info.value)


def test_database_path_expands_user_and_normalizes_to_absolute_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", "~/catalogs/../catalog.sqlite3")

    settings = load_settings(env_file=None)

    assert settings.database_path == home / "catalog.sqlite3"
    assert settings.database_path.is_absolute()
    assert not settings.database_path.exists()


def test_relative_database_path_is_rejected_with_sanitized_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    supplied_path = "relative/private/catalog.sqlite3"
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", supplied_path)

    with pytest.raises(ValidationError) as exc_info:
        load_settings(env_file=None)

    error_text = str(exc_info.value)
    assert supplied_path not in error_text
    assert "relative/private" not in error_text


def test_database_path_absent_from_settings_repr_logs_api_and_openapi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from framenest.adapters.api.application import create_app
    from framenest.structured_logging import build_uvicorn_log_config, get_logger

    monkeypatch.chdir(tmp_path)
    private_path = tmp_path / "private" / "catalog.sqlite3"
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(private_path))
    settings = load_settings(env_file=None)

    rendered_settings = f"{settings!r}{settings!s}"
    assert str(private_path) not in rendered_settings

    logging.config.dictConfig(build_uvicorn_log_config())
    stream = StringIO()
    logger = logging.getLogger("framenest")
    handler = logger.handlers[0]
    handler.stream = stream  # type: ignore[attr-defined]
    get_logger("configuration").emit(
        level="INFO",
        event="settings_loaded",
        operation="test",
        context={"path": str(private_path), "settings": settings},
    )
    log_output = stream.getvalue()
    assert str(private_path) not in log_output
    assert json.loads(log_output)

    app = create_app(settings=settings)
    api_output = TestClient(app).get("/health").text
    openapi_output = json.dumps(app.openapi())
    assert str(private_path) not in api_output
    assert str(private_path) not in openapi_output


def test_temporary_env_file_sets_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("FRAMENEST_PORT=9001\n", encoding="utf-8")
    settings = load_settings(env_file=env_file)
    assert settings.port == 9001


def test_process_environment_overrides_temporary_env_file_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("FRAMENEST_PORT=9001\n", encoding="utf-8")
    monkeypatch.setenv("FRAMENEST_PORT", "9002")
    settings = load_settings(env_file=env_file)
    assert settings.port == 9002


def test_invalid_port_produces_sanitized_validation_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    invalid_port = "70000"
    secret_value = "must-not-appear-in-error-output"
    monkeypatch.setenv("FRAMENEST_PORT", invalid_port)
    monkeypatch.setenv("FRAMENEST_API_KEY", secret_value)
    with pytest.raises(ValidationError) as exc_info:
        load_settings(env_file=None)
    error_text = str(exc_info.value)
    assert invalid_port not in error_text
    assert secret_value not in error_text


def test_temporary_env_file_overrides_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("FRAMENEST_HOST=127.0.0.2\n", encoding="utf-8")
    settings = load_settings(env_file=env_file)
    assert settings.host == "127.0.0.2"


def test_process_environment_overrides_temporary_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("FRAMENEST_HOST=127.0.0.2\n", encoding="utf-8")
    monkeypatch.setenv("FRAMENEST_HOST", "127.0.0.3")
    settings = load_settings(env_file=env_file)
    assert settings.host == "127.0.0.3"


def test_secret_field_uses_secret_str(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRAMENEST_API_KEY", "representative-secret-value")
    settings = load_settings(env_file=None)
    assert isinstance(settings.api_key, SecretStr)


def test_secret_plaintext_not_in_repr_or_str(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    secret_value = "representative-secret-value"
    monkeypatch.setenv("FRAMENEST_API_KEY", secret_value)
    settings = load_settings(env_file=None)
    rendered = f"{settings!r}{settings!s}"
    assert secret_value not in rendered


def test_invalid_configuration_produces_sanitized_validation_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    invalid_host = "not-a-valid-ip-address"
    secret_value = "must-not-appear-in-error-output"
    monkeypatch.setenv("FRAMENEST_HOST", invalid_host)
    monkeypatch.setenv("FRAMENEST_API_KEY", secret_value)
    with pytest.raises(ValidationError) as exc_info:
        load_settings(env_file=None)
    error_text = str(exc_info.value)
    assert invalid_host not in error_text
    assert secret_value not in error_text


@pytest.mark.parametrize(
    "provider_id",
    ["nvidia-nim", "vercel-ai-gateway", "opencode-go"],
)
def test_ai_provider_id_accepts_builtin_and_declared_ids(provider_id: str) -> None:
    settings = FrameNestSettings(_env_file=None, ai_provider_id=provider_id)

    assert settings.ai_provider_id == provider_id


def test_environment_override_accepts_declared_ai_provider_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRAMENEST_AI_PROVIDER_ID", "opencode-go")
    settings = load_settings(env_file=None)

    assert settings.ai_provider_id == "opencode-go"


def test_invalid_ai_provider_id_produces_sanitized_validation_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    invalid_provider_id = "OpenCode!"
    secret_value = "must-not-appear-in-error-output"
    monkeypatch.setenv("FRAMENEST_AI_PROVIDER_ID", invalid_provider_id)
    monkeypatch.setenv("FRAMENEST_API_KEY", secret_value)
    with pytest.raises(ValidationError) as exc_info:
        load_settings(env_file=None)
    error_text = str(exc_info.value)
    assert invalid_provider_id not in error_text
    assert secret_value not in error_text


def test_dotenv_is_gitignored() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "-v", ".env"],
        cwd=Path(__file__).resolve().parents[2],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert ".env" in result.stdout


def test_automatic_media_analysis_max_attempts_defaults_compatibly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from framenest.domain.media_analysis_runs import DEFAULT_MAX_ANALYSIS_ATTEMPTS

    monkeypatch.chdir(tmp_path)
    settings = load_settings(env_file=None)
    assert settings.automatic_media_analysis_max_attempts == DEFAULT_MAX_ANALYSIS_ATTEMPTS
    assert settings.automatic_media_analysis_max_attempts == 3


def test_automatic_media_analysis_max_attempts_override_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRAMENEST_AUTOMATIC_MEDIA_ANALYSIS_MAX_ATTEMPTS", "1")
    settings = load_settings(env_file=None)
    assert settings.automatic_media_analysis_max_attempts == 1


@pytest.mark.parametrize("invalid", ["0", "-1", "11"])
def test_automatic_media_analysis_max_attempts_rejects_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRAMENEST_AUTOMATIC_MEDIA_ANALYSIS_MAX_ATTEMPTS", invalid)
    with pytest.raises(ValidationError):
        load_settings(env_file=None)
