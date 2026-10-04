"""Centralized FrameNest settings loading boundary."""

from __future__ import annotations

from ipaddress import ip_address
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping
import uuid

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    SettingsError,
)

from framenest.domain.identity_access import (
    ROLE_ADMIN,
    FrameNestIdentityAccessError,
    build_identity_mapping,
)
from framenest.domain.media_analysis_runs import (
    DEFAULT_MAX_ANALYSIS_ATTEMPTS,
    MAX_CONFIGURED_ANALYSIS_ATTEMPTS,
)
from framenest.identity_env import (
    COMPATIBLE_ENVIRONMENT_PREFIX,
    IdentityEnvironmentConflictError,
    IdentityEnvironmentConflictFailure,
    canonical_identity_environment,
    folded_identity_environment,
    lookup_env,
    lookup_field_value,
)

ENV_FILE_ENVIRONMENT_VARIABLE = f"{COMPATIBLE_ENVIRONMENT_PREFIX}ENV_FILE"
ENV_FILE_ENVIRONMENT_SUFFIX = "ENV_FILE"
EXPLICIT_ENV_FILE_MESSAGE = (
    "The explicitly configured environment file is missing or unreadable."
)
DEVELOPMENT_DATABASE_DIRECTORY = "framenest-development"
DEVELOPMENT_DATABASE_FILENAME = "catalog.sqlite3"
RUNTIME_SETTINGS_FILENAME = "runtime-settings.json"
DEVELOPMENT_GALLERY_PREVIEW_DIRECTORY = "gallery-previews"
DEVELOPMENT_COVER_STORAGE_DIRECTORY = "covers"
DEVELOPMENT_COVER_THUMBNAILS_DIRECTORY = "cover-thumbnails"
DEFAULT_UPLOAD_MAX_TOTAL_BYTES = 1_073_741_824
DEFAULT_UPLOAD_MAX_PATCH_BYTES = 8_388_608
DEFAULT_UPLOAD_SESSION_TTL_SECONDS = 86_400
DEFAULT_UPLOAD_MIN_FREE_SPACE_RESERVE_BYTES = 67_108_864
DEFAULT_YOUTUBE_ACQUISITION_MAX_STAGING_BYTES = 2_214_592_512
DEFAULT_YOUTUBE_REQUEST_MAX_ACTIVE_PER_USER = 1
DEFAULT_YOUTUBE_REQUEST_MAX_GLOBAL_ACTIVE = 8
DEFAULT_YOUTUBE_REQUEST_MAX_SUBMITS_PER_HOUR = 6
DEFAULT_YOUTUBE_REQUEST_MAX_FAILED_PER_24H = 10
DEFAULT_YOUTUBE_REQUEST_MAX_PRIVATE_ITEMS = 20
DEFAULT_YOUTUBE_REQUEST_MAX_PRIVATE_BYTES = 10_737_418_240
DEFAULT_YOUTUBE_FINAL_MEDIA_BYTES = 1_073_741_824
DEFAULT_X_ACQUISITION_MAX_STAGING_BYTES = 2_147_483_648
DEFAULT_X_REQUEST_MAX_ACTIVE_PER_USER = 1
DEFAULT_X_REQUEST_MAX_GLOBAL_ACTIVE = 8
DEFAULT_X_REQUEST_MAX_SUBMITS_PER_HOUR = 6
DEFAULT_X_REQUEST_MAX_FAILED_PER_24H = 10
_AI_PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
INGRESS_MODE_TCP = "tcp"
INGRESS_MODE_TAILSCALE_UDS = "tailscale_uds"
INGRESS_MODE_PUBLIC_PUBLISHED_UDS = "public_published_uds"
SUPPORTED_INGRESS_MODES = frozenset(
    {
        INGRESS_MODE_TCP,
        INGRESS_MODE_TAILSCALE_UDS,
        INGRESS_MODE_PUBLIC_PUBLISHED_UDS,
    }
)
WORKSPACE_UDS_PATH = Path("/run/framenest/framenest.sock")
DEFAULT_PUBLIC_PUBLISHED_UDS_PATH = Path("/run/framenest/framenest-public.sock")
MAX_EXTERNAL_ORIGIN_LENGTH = 255
MAX_COMPANION_EXTENSION_ORIGINS = 4
_CHROME_EXTENSION_ORIGIN_PATTERN = re.compile(r"^chrome-extension://[a-p]{32}$")
_INGRESS_CONFIGURATION_MESSAGE = (
    "Tailscale UDS ingress requires an explicit UDS path, an exact https "
    "external origin, and at least one configured admin identity."
)
_PUBLIC_INGRESS_CONFIGURATION_MESSAGE = (
    "Public published UDS ingress requires a distinct Unix socket path."
)
_TCP_INGRESS_CONFIGURATION_MESSAGE = (
    "tcp ingress requires the host to be a loopback address; binding the "
    "full workspace application to a non-loopback address is rejected"
)
_COMPANION_EXTENSION_ORIGIN_MESSAGE = (
    "companion extension origins must be at most four unique exact "
    "chrome-extension:// origins"
)


def _default_database_path() -> Path:
    return _normalize_database_path(
        Path(tempfile.gettempdir())
        / DEVELOPMENT_DATABASE_DIRECTORY
        / DEVELOPMENT_DATABASE_FILENAME
    )


def _default_gallery_preview_cache_path() -> Path:
    return _normalize_absolute_path(
        Path(tempfile.gettempdir())
        / DEVELOPMENT_DATABASE_DIRECTORY
        / DEVELOPMENT_GALLERY_PREVIEW_DIRECTORY
    )


def _default_cover_storage_root() -> Path:
    return _normalize_absolute_path(
        Path(tempfile.gettempdir())
        / DEVELOPMENT_DATABASE_DIRECTORY
        / DEVELOPMENT_COVER_STORAGE_DIRECTORY
    )


def _default_cover_thumbnail_cache_path() -> Path:
    return _normalize_absolute_path(
        Path(tempfile.gettempdir())
        / DEVELOPMENT_DATABASE_DIRECTORY
        / DEVELOPMENT_COVER_THUMBNAILS_DIRECTORY
    )


def _normalize_absolute_path(value: Any) -> Path:
    try:
        path = Path(value).expanduser()
    except (RuntimeError, TypeError, ValueError) as exc:
        raise ValueError("path must be an absolute path") from exc
    if not path.is_absolute():
        raise ValueError("path must be an absolute path")
    return path.resolve(strict=False)


def _normalize_database_path(value: Any) -> Path:
    try:
        path = Path(value).expanduser()
    except (RuntimeError, TypeError, ValueError) as exc:
        raise ValueError("database path must be an absolute path") from exc
    if not path.is_absolute():
        raise ValueError("database path must be an absolute path")
    return path.resolve(strict=False)


class _IdentityResolverFieldMixin:
    """Shared identity-resolution field walk for the settings sources."""

    settings_cls: type[BaseSettings]

    def _identity_suffix(self, env_name: str) -> str | None:
        """Return the setting-name suffix for one internal environment key."""
        prefix = self._apply_case_sensitive(self.env_prefix)  # type: ignore[attr-defined]
        if not env_name.startswith(prefix):
            # An aliased field whose environment name is outside the internal
            # prefix spelling has no unambiguous identity suffix. Refuse to
            # resolve it rather than guess which variable was meant.
            return None
        return env_name[len(prefix) :].upper()

    def _resolved_field_values(self, resolver_values: Mapping[str, str]) -> dict[str, str]:
        case_folded = folded_identity_environment(resolver_values)
        resolved: dict[str, str] = {}
        for field_name, field in self.settings_cls.model_fields.items():
            for _field_key, env_name, _value_is_complex in self._extract_field_info(  # type: ignore[attr-defined]
                field, field_name
            ):
                suffix = self._identity_suffix(env_name)
                if suffix is None:
                    continue
                value = lookup_field_value(
                    suffix,
                    environ=resolver_values,
                    case_folded=case_folded,
                )
                if value is not None:
                    resolved[env_name] = value
        return resolved


class _DualPrefixEnvSettingsSource(_IdentityResolverFieldMixin, EnvSettingsSource):
    """Environment source whose field values come from the identity resolver.

    ``env_prefix`` stays as the internal key spelling only; it is no longer the
    reader. Every field value is resolved by
    :func:`framenest.identity_env.lookup_field_value`, so each field accepts
    ``KRONIKA_<SUFFIX>`` and ``FRAMENEST_<SUFFIX>`` under any case, and a
    conflicting pair of the two canonical names fails closed inside source
    construction, before any field value is assembled.

    Everything the inherited implementation provides is unchanged: complex field
    decoding, strict value coercion, case-insensitive field mapping, the
    ``env_ignore_empty`` and ``env_parse_none_str`` handling, and the source
    ordering that makes process environment override environment-file values.
    """

    def _load_env_vars(self) -> Mapping[str, str]:
        return self._resolved_field_values(os.environ)


class _DualPrefixDotEnvSettingsSource(_IdentityResolverFieldMixin, DotEnvSettingsSource):
    """Environment-file source whose field values come from the resolver too.

    The file keys the library already read are preserved exactly, and the
    resolver adds the field values the file supplies under either accepted
    prefix. ``env_file_encoding``, the ``env_prefix``-based extras handling, and
    the source ordering are unchanged.
    """

    def _load_env_vars(self) -> Mapping[str, str]:
        file_values = self._read_env_files()
        resolved = self._resolved_field_values(canonical_identity_environment(file_values))
        return {**file_values, **resolved}


class FrameNestSettings(BaseSettings):
    """Typed application settings loaded outside the domain layer."""

    model_config = SettingsConfigDict(
        env_prefix="FRAMENEST_",
        env_file_encoding="utf-8",
        hide_input_in_errors=True,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Route process-environment and environment-file reads through the resolver.

        The returned order is the library default order: initialisation
        arguments, then the process environment, then the environment file,
        then file secrets. The process environment therefore keeps overriding
        environment-file values.
        """
        assert isinstance(dotenv_settings, DotEnvSettingsSource)
        return (
            init_settings,
            _DualPrefixEnvSettingsSource(settings_cls),
            _DualPrefixDotEnvSettingsSource(settings_cls, env_file=dotenv_settings.env_file),
            file_secret_settings,
        )

    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000, ge=0, le=65535)
    api_key: SecretStr | None = Field(default=None)
    database_path: Path = Field(default_factory=_default_database_path, repr=False)
    gallery_preview_cache_path: Path = Field(
        default_factory=_default_gallery_preview_cache_path,
        repr=False,
    )
    cover_storage_root: Path = Field(
        default_factory=_default_cover_storage_root,
        repr=False,
    )
    cover_thumbnail_cache_path: Path = Field(
        default_factory=_default_cover_thumbnail_cache_path,
        repr=False,
    )
    upload_quarantine_root: Path | None = Field(default=None, repr=False)
    upload_publication_library_id: str | None = Field(default=None, repr=False)
    upload_max_total_bytes: int = Field(default=DEFAULT_UPLOAD_MAX_TOTAL_BYTES, gt=0)
    upload_max_patch_bytes: int = Field(default=DEFAULT_UPLOAD_MAX_PATCH_BYTES, gt=0)
    upload_session_ttl_seconds: int = Field(
        default=DEFAULT_UPLOAD_SESSION_TTL_SECONDS,
        gt=0,
    )
    upload_min_free_space_reserve_bytes: int = Field(
        default=DEFAULT_UPLOAD_MIN_FREE_SPACE_RESERVE_BYTES,
        ge=0,
    )
    youtube_acquisition_root: Path | None = Field(default=None, repr=False)
    youtube_acquisition_max_staging_bytes: int = Field(
        default=DEFAULT_YOUTUBE_ACQUISITION_MAX_STAGING_BYTES,
        gt=0,
    )
    youtube_request_max_active_per_user: int = Field(
        default=DEFAULT_YOUTUBE_REQUEST_MAX_ACTIVE_PER_USER,
        gt=0,
    )
    youtube_request_max_global_active: int = Field(
        default=DEFAULT_YOUTUBE_REQUEST_MAX_GLOBAL_ACTIVE,
        gt=0,
    )
    youtube_request_max_submits_per_hour: int = Field(
        default=DEFAULT_YOUTUBE_REQUEST_MAX_SUBMITS_PER_HOUR,
        gt=0,
    )
    youtube_request_max_failed_per_24h: int = Field(
        default=DEFAULT_YOUTUBE_REQUEST_MAX_FAILED_PER_24H,
        gt=0,
    )
    youtube_request_max_private_items: int = Field(
        default=DEFAULT_YOUTUBE_REQUEST_MAX_PRIVATE_ITEMS,
        gt=0,
    )
    youtube_request_max_private_bytes: int = Field(
        default=DEFAULT_YOUTUBE_REQUEST_MAX_PRIVATE_BYTES,
        gt=0,
    )
    x_acquisition_root: Path | None = Field(default=None, repr=False)
    x_acquisition_max_staging_bytes: int = Field(
        default=DEFAULT_X_ACQUISITION_MAX_STAGING_BYTES,
        gt=0,
    )
    x_request_max_active_per_user: int = Field(
        default=DEFAULT_X_REQUEST_MAX_ACTIVE_PER_USER,
        gt=0,
    )
    x_request_max_global_active: int = Field(
        default=DEFAULT_X_REQUEST_MAX_GLOBAL_ACTIVE,
        gt=0,
    )
    x_request_max_submits_per_hour: int = Field(
        default=DEFAULT_X_REQUEST_MAX_SUBMITS_PER_HOUR,
        gt=0,
    )
    x_request_max_failed_per_24h: int = Field(
        default=DEFAULT_X_REQUEST_MAX_FAILED_PER_24H,
        gt=0,
    )
    ai_provider_id: str | None = Field(default=None)
    ai_model_id: str | None = Field(default=None)
    ingress_mode: str = Field(default=INGRESS_MODE_TCP)
    uds_path: Path | None = Field(default=None, repr=False)
    external_origin: str | None = Field(default=None)
    companion_extension_origins: list[str] = Field(default_factory=list)
    identity_map: dict[str, str] = Field(default_factory=dict, repr=False)
    local_owner_login: str | None = Field(default=None, repr=False)
    automatic_media_analysis_enabled: bool = Field(default=False)
    automatic_media_analysis_max_attempts: int = Field(
        default=DEFAULT_MAX_ANALYSIS_ATTEMPTS,
        ge=1,
        le=MAX_CONFIGURED_ANALYSIS_ATTEMPTS,
    )
    runtime_settings_path: Path | None = Field(default=None, repr=False)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        try:
            ip_address(value)
        except ValueError as exc:
            raise ValueError("host must be a valid IP address") from exc
        return value

    @field_validator("database_path", mode="before")
    @classmethod
    def validate_database_path(cls, value: Any) -> Path:
        return _normalize_database_path(value)

    @field_validator("gallery_preview_cache_path", mode="before")
    @classmethod
    def validate_gallery_preview_cache_path(cls, value: Any) -> Path:
        try:
            return _normalize_absolute_path(value)
        except ValueError as exc:
            raise ValueError("gallery preview cache path must be an absolute path") from exc

    @field_validator("cover_storage_root", mode="before")
    @classmethod
    def validate_cover_storage_root(cls, value: Any) -> Path:
        try:
            return _normalize_absolute_path(value)
        except ValueError as exc:
            raise ValueError("cover storage root must be an absolute path") from exc

    @field_validator("cover_thumbnail_cache_path", mode="before")
    @classmethod
    def validate_cover_thumbnail_cache_path(cls, value: Any) -> Path:
        try:
            return _normalize_absolute_path(value)
        except ValueError as exc:
            raise ValueError(
                "cover thumbnail cache path must be an absolute path"
            ) from exc

    @field_validator("runtime_settings_path", mode="before")
    @classmethod
    def validate_runtime_settings_path(cls, value: Any) -> Path | None:
        if value is None or value == "":
            return None
        try:
            return _normalize_absolute_path(value)
        except ValueError as exc:
            raise ValueError("runtime settings path must be an absolute path") from exc

    @field_validator("upload_quarantine_root", mode="before")
    @classmethod
    def validate_upload_quarantine_root(cls, value: Any) -> Path | None:
        if value is None or value == "":
            return None
        try:
            return _normalize_absolute_path(value)
        except ValueError as exc:
            raise ValueError("upload quarantine root must be an absolute path") from exc

    @field_validator("youtube_acquisition_root", mode="before")
    @classmethod
    def validate_youtube_acquisition_root(cls, value: Any) -> Path | None:
        if value is None or value == "":
            return None
        try:
            path = Path(value).expanduser()
        except (RuntimeError, TypeError, ValueError) as exc:
            raise ValueError(
                "YouTube acquisition root must be an absolute path"
            ) from exc
        if not path.is_absolute():
            raise ValueError(
                "YouTube acquisition root must be an absolute path"
            )
        return Path(os.path.abspath(path))

    @field_validator("x_acquisition_root", mode="before")
    @classmethod
    def validate_x_acquisition_root(cls, value: Any) -> Path | None:
        if value is None or value == "":
            return None
        try:
            path = Path(value).expanduser()
        except (RuntimeError, TypeError, ValueError) as exc:
            raise ValueError(
                "X acquisition root must be an absolute path"
            ) from exc
        if not path.is_absolute():
            raise ValueError(
                "X acquisition root must be an absolute path"
            )
        return Path(os.path.abspath(path))

    @model_validator(mode="after")
    def validate_private_storage_roots(self) -> "FrameNestSettings":
        storage_paths = (
            self.database_path,
            self.gallery_preview_cache_path,
            self.cover_storage_root,
            self.cover_thumbnail_cache_path,
            self.upload_quarantine_root,
            self.youtube_acquisition_root,
            self.x_acquisition_root,
        )
        for first, second in _disjoint_pairs(storage_paths):
            if _paths_overlap(first, second):
                raise ValueError(
                    "Kronika private storage paths must not overlap"
                )
        return self

    @field_validator("upload_publication_library_id", mode="before")
    @classmethod
    def validate_upload_publication_library_id(cls, value: Any) -> str | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise ValueError("upload publication library id must be a UUIDv4")
        try:
            parsed = uuid.UUID(value)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("upload publication library id must be a UUIDv4") from exc
        if (
            str(parsed) != value
            or parsed.variant != uuid.RFC_4122
            or parsed.version != 4
        ):
            raise ValueError("upload publication library id must be a UUIDv4")
        return value

    @field_validator("ai_provider_id")
    @classmethod
    def validate_ai_provider_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not _AI_PROVIDER_ID_PATTERN.fullmatch(normalized):
            raise ValueError("ai provider id is not supported")
        return normalized

    @field_validator("ai_model_id")
    @classmethod
    def validate_ai_model_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError("ai model id must be non-empty when provided")
        return normalized

    @field_validator("ingress_mode")
    @classmethod
    def validate_ingress_mode(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in SUPPORTED_INGRESS_MODES:
            raise ValueError("ingress mode is not supported")
        return normalized

    @field_validator("uds_path", mode="before")
    @classmethod
    def validate_uds_path(cls, value: Any) -> Path | None:
        if value is None or value == "":
            return None
        try:
            return _normalize_absolute_path(value)
        except ValueError as exc:
            raise ValueError("uds path must be an absolute path") from exc

    @field_validator("external_origin")
    @classmethod
    def validate_external_origin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if len(normalized) > MAX_EXTERNAL_ORIGIN_LENGTH or not _is_exact_https_origin(
            normalized
        ):
            raise ValueError("external origin must be an exact https origin")
        return normalized

    @field_validator("companion_extension_origins")
    @classmethod
    def validate_companion_extension_origins(cls, value: list[str]) -> list[str]:
        if not isinstance(value, list):
            raise ValueError(_COMPANION_EXTENSION_ORIGIN_MESSAGE)
        if len(value) > MAX_COMPANION_EXTENSION_ORIGINS:
            raise ValueError(_COMPANION_EXTENSION_ORIGIN_MESSAGE)
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str) or _CHROME_EXTENSION_ORIGIN_PATTERN.fullmatch(
                item
            ) is None:
                raise ValueError(_COMPANION_EXTENSION_ORIGIN_MESSAGE)
            if item in seen:
                raise ValueError(_COMPANION_EXTENSION_ORIGIN_MESSAGE)
            seen.add(item)
            normalized.append(item)
        return normalized

    @field_validator("local_owner_login")
    @classmethod
    def validate_local_owner_login(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            return None
        from framenest.domain.identity_access import normalize_login

        try:
            return normalize_login(value)
        except FrameNestIdentityAccessError as exc:
            raise ValueError("local owner login is invalid") from exc

    @field_validator("identity_map")
    @classmethod
    def validate_identity_map(cls, value: dict[str, str]) -> dict[str, str]:
        try:
            build_identity_mapping(value)
        except FrameNestIdentityAccessError as exc:
            raise ValueError("identity map is invalid") from exc
        return value

    @model_validator(mode="after")
    def validate_ingress_configuration(self) -> "FrameNestSettings":
        if self.local_owner_login is not None:
            try:
                mapping = build_identity_mapping(self.identity_map)
            except FrameNestIdentityAccessError as exc:
                raise ValueError("local owner login is not mapped") from exc
            if self.local_owner_login not in mapping:
                raise ValueError("local owner login is not mapped")
        if self.ingress_mode == INGRESS_MODE_PUBLIC_PUBLISHED_UDS:
            resolved_uds = self.uds_path or DEFAULT_PUBLIC_PUBLISHED_UDS_PATH
            if resolved_uds == WORKSPACE_UDS_PATH:
                raise ValueError(_PUBLIC_INGRESS_CONFIGURATION_MESSAGE)
            if self.uds_path is None:
                self.uds_path = resolved_uds
            return self
        if self.ingress_mode == INGRESS_MODE_TCP:
            if not ip_address(self.host).is_loopback:
                raise ValueError(_TCP_INGRESS_CONFIGURATION_MESSAGE)
            return self
        if self.ingress_mode != INGRESS_MODE_TAILSCALE_UDS:
            return self
        if self.uds_path is None or self.external_origin is None:
            raise ValueError(_INGRESS_CONFIGURATION_MESSAGE)
        try:
            mapping = build_identity_mapping(self.identity_map)
        except FrameNestIdentityAccessError as exc:
            raise ValueError(_INGRESS_CONFIGURATION_MESSAGE) from exc
        if not any(entry.role == ROLE_ADMIN for entry in mapping.values()):
            raise ValueError(_INGRESS_CONFIGURATION_MESSAGE)
        return self


class FrameNestConfigurationError(Exception):
    """Sanitized configuration failure safe for operator-facing output."""


class IdentityEnvironmentConfigurationError(
    FrameNestConfigurationError,
    IdentityEnvironmentConflictFailure,
):
    """Configuration failure caused by one setting name set under both prefixes.

    ``FrameNestSettings`` is never built when the two accepted spellings of one
    setting-name suffix carry different values, so this type is raised instead of
    returning a validated settings object. Every command line entry point catches
    it ahead of its generic configuration failure, reports it in that command's
    own output shape, and returns the single fail-closed ``exit_status`` this
    class carries. The message names the two variable suffixes only.
    """


class _EnvFileNotSpecified:
    """Sentinel marking an omitted ``env_file`` argument."""


_ENV_FILE_NOT_SPECIFIED = _EnvFileNotSpecified()


def resolved_runtime_settings_path(settings: FrameNestSettings) -> Path:
    """Return the JSON sidecar path for administrator runtime settings.

    Defaults to ``{database_path.parent}/runtime-settings.json``. An explicit
    ``runtime_settings_path`` (env ``FRAMENEST_RUNTIME_SETTINGS_PATH``) wins.
    """
    if settings.runtime_settings_path is not None:
        return settings.runtime_settings_path
    return settings.database_path.parent / RUNTIME_SETTINGS_FILENAME


def load_settings(
    *,
    env_file: Path | str | None | _EnvFileNotSpecified = _ENV_FILE_NOT_SPECIFIED,
) -> FrameNestSettings:
    """Load settings with deterministic explicit-only environment-file authority.

    Environment-file selection:

    - an explicit ``env_file`` path is authoritative and must name a readable
      regular file;
    - ``env_file=None`` disables environment-file loading entirely;
    - an omitted argument consults the ``ENV_FILE`` process environment
      variable through the identity resolver, so both ``KRONIKA_ENV_FILE`` and
      ``FRAMENEST_ENV_FILE`` select the file, and, when set to different values,
      fails closed before the file is opened; when unset or empty, no
      environment file is loaded.

    The caller's current working directory is never probed for an implicit
    ``.env`` file, so administrative and production commands behave
    identically from any working directory. An explicitly requested file that
    is missing, unreadable, or unloadable fails closed with
    ``FrameNestConfigurationError``. Process environment variables always
    override environment-file values.

    A conflict between the two accepted spellings of one setting-name suffix
    raises :class:`IdentityEnvironmentConfigurationError`, a
    :class:`FrameNestConfigurationError` subtype carrying the same sanitized
    message, so every command line entry point can map exactly this failure to
    its own output shape and to the one fail-closed exit status.
    """
    if isinstance(env_file, _EnvFileNotSpecified):
        try:
            selected = lookup_env(ENV_FILE_ENVIRONMENT_SUFFIX)
        except IdentityEnvironmentConflictError as exc:
            raise IdentityEnvironmentConfigurationError(str(exc)) from exc
        requested = (selected or "").strip()
        if not requested:
            return _build_settings(None)
        env_file = requested
    if env_file is None:
        return _build_settings(None)
    explicit_path = _require_readable_env_file(env_file)
    try:
        return FrameNestSettings(_env_file=explicit_path)
    except IdentityEnvironmentConflictError as exc:
        raise IdentityEnvironmentConfigurationError(str(exc)) from exc
    except (OSError, SettingsError) as exc:
        raise FrameNestConfigurationError(EXPLICIT_ENV_FILE_MESSAGE) from exc


def _build_settings(env_file: Path | None) -> FrameNestSettings:
    """Build settings from the process environment only, failing closed."""
    try:
        return FrameNestSettings(_env_file=env_file)
    except IdentityEnvironmentConflictError as exc:
        raise IdentityEnvironmentConfigurationError(str(exc)) from exc


def _require_readable_env_file(env_file: Path | str) -> Path:
    try:
        candidate = Path(env_file).expanduser()
        if not candidate.is_file():
            raise FrameNestConfigurationError(EXPLICIT_ENV_FILE_MESSAGE)
        with candidate.open("rb"):
            pass
    except FrameNestConfigurationError:
        raise
    except OSError as exc:
        raise FrameNestConfigurationError(EXPLICIT_ENV_FILE_MESSAGE) from exc
    return candidate


def _paths_overlap(first: Path, second: Path | None) -> bool:
    if second is None:
        return False
    return first == second or first in second.parents or second in first.parents


def _disjoint_pairs(paths: tuple[Path | None, ...]) -> list[tuple[Path, Path]]:
    concrete = [path for path in paths if path is not None]
    pairs: list[tuple[Path, Path]] = []
    for index, first in enumerate(concrete):
        for second in concrete[index + 1 :]:
            pairs.append((first, second))
    return pairs


def _is_exact_https_origin(value: str) -> bool:
    if not value.startswith("https://"):
        return False
    host = value[len("https://"):]
    if not host or host != host.lower():
        return False
    if any(character in host for character in ("/", "?", "#", "@", " ")):
        return False
    if ":" in host:
        return False
    labels = host
    if not labels or "." not in labels:
        return False
    return all(
        label
        and len(label) <= 63
        and not label.startswith("-")
        and not label.endswith("-")
        and all(character.isalnum() or character == "-" for character in label)
        for label in labels.split(".")
    )
