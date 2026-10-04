"""Unit tests for temporary NVIDIA credential loading."""

from __future__ import annotations

from pathlib import Path
import stat

import pytest

from kronika.infrastructure.ai.credentials import (
    AI_CREDENTIAL_MAX_BYTES,
    CREDENTIAL_MISSING_MESSAGE,
    NvidiaApiCredential,
    VercelAiGatewayCredential,
    load_nvidia_api_credential,
    load_vercel_ai_gateway_credential,
)


def test_missing_key_returns_none() -> None:
    assert load_nvidia_api_credential({}) is None


def test_blank_key_returns_none() -> None:
    assert load_nvidia_api_credential({"NVIDIA_API_KEY": "   "}) is None


def test_non_empty_key_returns_credential_without_exposing_value() -> None:
    credential = load_nvidia_api_credential({"NVIDIA_API_KEY": "test-secret-value"})

    assert isinstance(credential, NvidiaApiCredential)
    assert "test-secret-value" not in repr(credential)
    assert repr(credential) == "NvidiaApiCredential(<redacted>)"


def test_injected_mapping_is_used_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "process-secret")

    assert load_nvidia_api_credential({}) is None
    credential = load_nvidia_api_credential({"NVIDIA_API_KEY": "mapped-secret"})

    assert isinstance(credential, NvidiaApiCredential)
    assert credential.authorization_header() == "Bearer mapped-secret"


def test_key_is_absent_from_errors_and_repr() -> None:
    with pytest.raises(ValueError) as exc_info:
        NvidiaApiCredential(" ")

    assert str(exc_info.value) == CREDENTIAL_MISSING_MESSAGE
    assert "real-secret-value" not in str(exc_info.value)
    assert "secret" not in repr(NvidiaApiCredential("real-secret-value"))


def test_unrelated_environment_values_are_ignored() -> None:
    assert load_nvidia_api_credential({"OTHER_API_KEY": "unrelated-secret"}) is None


def test_vercel_gateway_key_uses_unprefixed_environment_name() -> None:
    credential = load_vercel_ai_gateway_credential({"AI_GATEWAY_API_KEY": "gateway-secret"})

    assert isinstance(credential, VercelAiGatewayCredential)
    assert credential.authorization_header() == "Bearer gateway-secret"
    assert "gateway-secret" not in repr(credential)


def test_vercel_gateway_missing_key_returns_none() -> None:
    assert load_vercel_ai_gateway_credential({}) is None
    assert load_vercel_ai_gateway_credential({"FRAMENEST_AI_GATEWAY_API_KEY": "wrong"}) is None


def _credential_file(tmp_path: Path, name: str, payload: bytes) -> Path:
    directory = tmp_path / "credentials"
    directory.mkdir()
    path = directory / name
    path.write_bytes(payload)
    return path


def test_systemd_credential_directory_fallback(tmp_path: Path) -> None:
    path = _credential_file(tmp_path, "NVIDIA_API_KEY", b"systemd-sentinel")

    credential = load_nvidia_api_credential({"CREDENTIALS_DIRECTORY": str(path.parent)})

    assert isinstance(credential, NvidiaApiCredential)
    assert credential.authorization_header() == "Bearer systemd-sentinel"


def test_environment_credential_precedes_systemd_credential(tmp_path: Path) -> None:
    path = _credential_file(tmp_path, "NVIDIA_API_KEY", b"systemd-sentinel")

    credential = load_nvidia_api_credential(
        {
            "NVIDIA_API_KEY": "env-sentinel",
            "CREDENTIALS_DIRECTORY": str(path.parent),
        }
    )

    assert isinstance(credential, NvidiaApiCredential)
    assert credential.authorization_header() == "Bearer env-sentinel"


def test_systemd_credential_missing_or_empty_returns_none(tmp_path: Path) -> None:
    directory = tmp_path / "credentials"
    directory.mkdir()
    (directory / "NVIDIA_API_KEY").write_bytes(b"\n")

    assert load_nvidia_api_credential({"CREDENTIALS_DIRECTORY": str(tmp_path / "missing")}) is None
    assert load_nvidia_api_credential({"CREDENTIALS_DIRECTORY": str(directory)}) is None


def test_systemd_credential_allows_one_trailing_newline(tmp_path: Path) -> None:
    path = _credential_file(tmp_path, "AI_GATEWAY_API_KEY", b"gateway-sentinel\n")

    credential = load_vercel_ai_gateway_credential({"CREDENTIALS_DIRECTORY": str(path.parent)})

    assert isinstance(credential, VercelAiGatewayCredential)
    assert credential.authorization_header() == "Bearer gateway-sentinel"


@pytest.mark.parametrize(
    "payload",
    [
        b"first\nsecond",
        b"first\r\nsecond",
        b"contains\x00nul",
        b"\xff",
        b"x" * (AI_CREDENTIAL_MAX_BYTES + 1),
    ],
)
def test_malformed_or_oversized_systemd_credential_is_sanitized(
    tmp_path: Path,
    payload: bytes,
) -> None:
    path = _credential_file(tmp_path, "NVIDIA_API_KEY", payload)

    credential = load_nvidia_api_credential({"CREDENTIALS_DIRECTORY": str(path.parent)})

    assert credential is None
    assert "NVIDIA_API_KEY" not in repr(credential)


def test_non_regular_systemd_credential_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "credentials"
    directory.mkdir()
    path = directory / "NVIDIA_API_KEY"
    path.mkdir()

    assert load_nvidia_api_credential({"CREDENTIALS_DIRECTORY": str(directory)}) is None

    path.rmdir()
    path.symlink_to(tmp_path / "elsewhere")
    assert path.lstat().st_mode & stat.S_IFLNK
    assert load_nvidia_api_credential({"CREDENTIALS_DIRECTORY": str(directory)}) is None
