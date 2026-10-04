"""Standard-library CLI for FrameNest catalog operations."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from typing import Any, NoReturn

from kronika.adapters.cli.library_root import LibraryRootNotUsableError, prepare_library_root
from kronika.application.ports.device_repository import (
    DeviceAlreadyExistsError,
    DeviceRepository,
    FrameNestDeviceRepositoryError,
)
from kronika.application.library_scan import (
    FrameNestLibraryScanError,
    LibraryScanFailedError,
    LibraryScanLimits,
    LibraryScanNotFoundError,
    LibraryScanPreviewResult,
    LibraryScanUnavailableError,
    PreviewLibraryScan,
    default_scan_limits,
)
from kronika.application.media_analysis import (
    FrameNestMediaAnalysisError,
    MediaAnalysisFailedError,
    MediaAnalysisNotFoundError,
    MediaAnalysisUnavailableError,
    MediaRelativePath,
    PreparedAnalysisResult,
    PrepareLocalMediaAnalysis,
)
from kronika.application.media_suggestion import (
    MediaSuggestionNotFoundError,
    MediaSuggestionPreparationFailedError,
    MediaSuggestionPreparationUnavailableError,
    MediaSuggestionPreviewResult,
    MediaSuggestionProviderAuthError,
    MediaSuggestionProviderFailedError,
    MediaSuggestionProviderInvalidResponseError,
    MediaSuggestionProviderRateLimitedError,
    MediaSuggestionProviderUnavailableError,
    PreviewMediaSuggestion,
)
from kronika.application.ports.library_repository import (
    FrameNestLibraryRepositoryError,
    LibraryAlreadyExistsError,
    LibraryDeviceNotFoundError,
    LibraryRepository,
    LibraryRootAlreadyRegisteredError,
)
from kronika.configuration import (
    KronikaSettings,
    IdentityEnvironmentConfigurationError,
    load_settings,
)
from kronika.domain import (
    Device,
    DeviceId,
    FrameNestDeviceError,
    FrameNestIdentityError,
    FrameNestLibraryError,
    Library,
    LibraryId,
    LibraryRoot,
)
from kronika.infrastructure.ai import NvidiaNimMediaSuggestionProvider
from kronika.infrastructure.ai.constants import DEFAULT_MODEL_ID, DEFAULT_PROVIDER_ID, MAX_MODEL_ID_LENGTH
from kronika.infrastructure.ai.credentials import NvidiaApiCredential
from kronika.infrastructure.filesystem.library_scanner import LocalLibraryScanner
from kronika.infrastructure.media_analysis import LocalMediaAnalysisAdapter
from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.errors import FrameNestPersistenceError
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.migrations import inspect_database_migration_status

INVALID_INPUT_CODE = "FRAMENEST_CATALOG_INVALID_INPUT"
INVALID_INPUT_MESSAGE = "Invalid catalog command."
DEVICE_NOT_FOUND_CODE = "FRAMENEST_DEVICE_NOT_FOUND"
DEVICE_NOT_FOUND_MESSAGE = "Device not found."
LIBRARY_NOT_FOUND_CODE = "FRAMENEST_LIBRARY_NOT_FOUND"
LIBRARY_NOT_FOUND_MESSAGE = "Library not found."
LIBRARY_DEVICE_NOT_FOUND_CODE = "FRAMENEST_LIBRARY_DEVICE_NOT_FOUND"
LIBRARY_DEVICE_NOT_FOUND_MESSAGE = "Owning device not found."
ROOT_NOT_USABLE_CODE = "FRAMENEST_LIBRARY_ROOT_NOT_USABLE"
ROOT_NOT_USABLE_MESSAGE = "Library root is not usable."
ROOT_ALREADY_REGISTERED_CODE = "FRAMENEST_LIBRARY_ROOT_ALREADY_REGISTERED"
ROOT_ALREADY_REGISTERED_MESSAGE = "Library root is already registered."
NOT_READY_CODE = "FRAMENEST_CATALOG_NOT_READY"
NOT_READY_MESSAGE = "Catalog database is not ready. Run framenest-db migrate."
COMMAND_FAILED_CODE = "FRAMENEST_CATALOG_COMMAND_FAILED"
COMMAND_FAILED_MESSAGE = "Catalog command failed."
SCAN_UNAVAILABLE_CODE = "FRAMENEST_LIBRARY_SCAN_UNAVAILABLE"
SCAN_UNAVAILABLE_MESSAGE = "Library scan preview is not available."
ANALYSIS_UNAVAILABLE_CODE = "FRAMENEST_LIBRARY_ANALYSIS_UNAVAILABLE"
ANALYSIS_UNAVAILABLE_MESSAGE = "Local media analysis preparation is not available."
CLOUD_CONFIRMATION_REQUIRED_CODE = "FRAMENEST_CATALOG_CLOUD_CONFIRMATION_REQUIRED"
CLOUD_CONFIRMATION_REQUIRED_MESSAGE = "Cloud upload confirmation is required."
NVIDIA_CREDENTIAL_MISSING_CODE = "FRAMENEST_CATALOG_NVIDIA_CREDENTIAL_MISSING"
NVIDIA_CREDENTIAL_MISSING_MESSAGE = "NVIDIA API credential is not available."
UNSUPPORTED_PROVIDER_CODE = "FRAMENEST_CATALOG_UNSUPPORTED_PROVIDER"
UNSUPPORTED_PROVIDER_MESSAGE = "Media suggestion provider is not supported."
INVALID_MODEL_CODE = "FRAMENEST_CATALOG_INVALID_MODEL"
INVALID_MODEL_MESSAGE = "Media suggestion model is not valid."
SUGGESTION_PREPARATION_UNAVAILABLE_CODE = "FRAMENEST_LIBRARY_SUGGESTION_PREPARATION_UNAVAILABLE"
SUGGESTION_PREPARATION_UNAVAILABLE_MESSAGE = "Local media preparation is not available."
SUGGESTION_PROVIDER_UNAVAILABLE_CODE = "FRAMENEST_MEDIA_SUGGESTION_PROVIDER_UNAVAILABLE"
SUGGESTION_PROVIDER_UNAVAILABLE_MESSAGE = "Media suggestion provider is not available."
SUGGESTION_PROVIDER_RATE_LIMITED_CODE = "FRAMENEST_MEDIA_SUGGESTION_PROVIDER_RATE_LIMITED"
SUGGESTION_PROVIDER_RATE_LIMITED_MESSAGE = "Media suggestion provider rate limit was reached."
SUGGESTION_PROVIDER_AUTH_CODE = "FRAMENEST_MEDIA_SUGGESTION_PROVIDER_AUTH_REJECTED"
SUGGESTION_PROVIDER_AUTH_MESSAGE = "Media suggestion provider authentication was rejected."
SUGGESTION_PROVIDER_INVALID_RESPONSE_CODE = "FRAMENEST_MEDIA_SUGGESTION_PROVIDER_INVALID_RESPONSE"
SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE = "Media suggestion provider response was invalid."


class _UsageError(Exception):
    pass


class _InvalidInputError(Exception):
    pass


class _DeviceNotFoundError(Exception):
    pass


class _LibraryNotFoundError(Exception):
    pass


class _LibraryDeviceNotFoundError(Exception):
    pass


class _RootNotUsableError(Exception):
    pass


class _RootAlreadyRegisteredError(Exception):
    pass


class _NotReadyError(Exception):
    pass


class _ScanUnavailableError(Exception):
    pass


class _AnalysisUnavailableError(Exception):
    pass


class _CloudConfirmationRequiredError(Exception):
    pass


class _NvidiaCredentialMissingError(Exception):
    pass


class _UnsupportedProviderError(Exception):
    pass


class _InvalidModelError(Exception):
    pass


class _SuggestionPreparationUnavailableError(Exception):
    pass


class _SuggestionProviderUnavailableError(Exception):
    pass


class _SuggestionProviderRateLimitedError(Exception):
    pass


class _SuggestionProviderAuthError(Exception):
    pass


class _SuggestionProviderInvalidResponseError(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError(INVALID_INPUT_MESSAGE)


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch catalog operations and return a process exit code."""
    operation = "unknown"
    try:
        parser = _build_parser()
        args = parser.parse_args(argv)
        settings = load_settings()
        operation = _operation_name(args)
        result = _dispatch(args, settings)
        _write_success(operation, result)
        return 0
    except _UsageError:
        _write_error(
            operation=operation,
            error_code=INVALID_INPUT_CODE,
            message=INVALID_INPUT_MESSAGE,
        )
        return 2
    except _InvalidInputError:
        _write_error(
            operation=operation,
            error_code=INVALID_INPUT_CODE,
            message=INVALID_INPUT_MESSAGE,
        )
        return 2
    except _RootNotUsableError:
        _write_error(
            operation=operation,
            error_code=ROOT_NOT_USABLE_CODE,
            message=ROOT_NOT_USABLE_MESSAGE,
        )
        return 2
    except _DeviceNotFoundError:
        _write_error(
            operation=operation,
            error_code=DEVICE_NOT_FOUND_CODE,
            message=DEVICE_NOT_FOUND_MESSAGE,
        )
        return 3
    except _LibraryNotFoundError:
        _write_error(
            operation=operation,
            error_code=LIBRARY_NOT_FOUND_CODE,
            message=LIBRARY_NOT_FOUND_MESSAGE,
        )
        return 3
    except _LibraryDeviceNotFoundError:
        _write_error(
            operation=operation,
            error_code=LIBRARY_DEVICE_NOT_FOUND_CODE,
            message=LIBRARY_DEVICE_NOT_FOUND_MESSAGE,
        )
        return 3
    except _RootAlreadyRegisteredError:
        _write_error(
            operation=operation,
            error_code=ROOT_ALREADY_REGISTERED_CODE,
            message=ROOT_ALREADY_REGISTERED_MESSAGE,
        )
        return 5
    except _NotReadyError:
        _write_error(
            operation=operation,
            error_code=NOT_READY_CODE,
            message=NOT_READY_MESSAGE,
        )
        return 4
    except _ScanUnavailableError:
        _write_error(
            operation=operation,
            error_code=SCAN_UNAVAILABLE_CODE,
            message=SCAN_UNAVAILABLE_MESSAGE,
        )
        return 6
    except _AnalysisUnavailableError:
        _write_error(
            operation=operation,
            error_code=ANALYSIS_UNAVAILABLE_CODE,
            message=ANALYSIS_UNAVAILABLE_MESSAGE,
        )
        return 6
    except _CloudConfirmationRequiredError:
        _write_error(
            operation=operation,
            error_code=CLOUD_CONFIRMATION_REQUIRED_CODE,
            message=CLOUD_CONFIRMATION_REQUIRED_MESSAGE,
        )
        return 2
    except _NvidiaCredentialMissingError:
        _write_error(
            operation=operation,
            error_code=NVIDIA_CREDENTIAL_MISSING_CODE,
            message=NVIDIA_CREDENTIAL_MISSING_MESSAGE,
        )
        return 2
    except _UnsupportedProviderError:
        _write_error(
            operation=operation,
            error_code=UNSUPPORTED_PROVIDER_CODE,
            message=UNSUPPORTED_PROVIDER_MESSAGE,
        )
        return 2
    except _InvalidModelError:
        _write_error(
            operation=operation,
            error_code=INVALID_MODEL_CODE,
            message=INVALID_MODEL_MESSAGE,
        )
        return 2
    except _SuggestionPreparationUnavailableError:
        _write_error(
            operation=operation,
            error_code=SUGGESTION_PREPARATION_UNAVAILABLE_CODE,
            message=SUGGESTION_PREPARATION_UNAVAILABLE_MESSAGE,
        )
        return 6
    except _SuggestionProviderUnavailableError:
        _write_error(
            operation=operation,
            error_code=SUGGESTION_PROVIDER_UNAVAILABLE_CODE,
            message=SUGGESTION_PROVIDER_UNAVAILABLE_MESSAGE,
        )
        return 7
    except _SuggestionProviderRateLimitedError:
        _write_error(
            operation=operation,
            error_code=SUGGESTION_PROVIDER_RATE_LIMITED_CODE,
            message=SUGGESTION_PROVIDER_RATE_LIMITED_MESSAGE,
        )
        return 7
    except _SuggestionProviderAuthError:
        _write_error(
            operation=operation,
            error_code=SUGGESTION_PROVIDER_AUTH_CODE,
            message=SUGGESTION_PROVIDER_AUTH_MESSAGE,
        )
        return 7
    except _SuggestionProviderInvalidResponseError:
        _write_error(
            operation=operation,
            error_code=SUGGESTION_PROVIDER_INVALID_RESPONSE_CODE,
            message=SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE,
        )
        return 7
    except IdentityEnvironmentConfigurationError as exc:
        _write_error(
            operation=operation,
            error_code=COMMAND_FAILED_CODE,
            message=str(exc),
        )
        return exc.exit_status
    except (
        FrameNestPersistenceError,
        FrameNestDeviceRepositoryError,
        FrameNestLibraryRepositoryError,
        DeviceAlreadyExistsError,
        LibraryAlreadyExistsError,
        Exception,
    ):
        _write_error(
            operation=operation,
            error_code=COMMAND_FAILED_CODE,
            message=COMMAND_FAILED_MESSAGE,
        )
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="framenest-catalog", add_help=True)
    resources = parser.add_subparsers(dest="resource", required=True)

    device = resources.add_parser("device")
    device_commands = device.add_subparsers(dest="operation", required=True)
    device_register = device_commands.add_parser("register")
    device_register.add_argument("--display-name", required=True, dest="display_name")
    device_get = device_commands.add_parser("get")
    device_get.add_argument("--id", required=True, dest="device_id")
    device_commands.add_parser("list")

    library = resources.add_parser("library")
    library_commands = library.add_subparsers(dest="operation", required=True)
    library_register = library_commands.add_parser("register")
    library_register.add_argument("--device-id", required=True, dest="device_id")
    library_register.add_argument("--display-name", required=True, dest="display_name")
    library_register.add_argument("--root", required=True, dest="root_path")
    library_get = library_commands.add_parser("get")
    library_get.add_argument("--id", required=True, dest="library_id")
    library_commands.add_parser("list")
    library_scan_preview = library_commands.add_parser("scan-preview")
    library_scan_preview.add_argument("--id", required=True, dest="library_id")
    library_scan_preview.add_argument(
        "--max-entries",
        type=int,
        default=default_scan_limits().max_entries,
        dest="max_entries",
    )
    library_scan_preview.add_argument(
        "--max-candidates",
        type=int,
        default=default_scan_limits().max_candidates,
        dest="max_candidates",
    )
    library_analyze_preview = library_commands.add_parser("analyze-preview")
    library_analyze_preview.add_argument("--id", required=True, dest="library_id")
    library_analyze_preview.add_argument("--path", required=True, dest="relative_path")
    library_suggest_preview = library_commands.add_parser("suggest-preview")
    library_suggest_preview.add_argument("--id", required=True, dest="library_id")
    library_suggest_preview.add_argument("--path", required=True, dest="relative_path")
    library_suggest_preview.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER_ID,
        dest="provider_id",
    )
    library_suggest_preview.add_argument(
        "--model",
        default=DEFAULT_MODEL_ID,
        dest="model_id",
    )
    library_suggest_preview.add_argument(
        "--confirm-cloud-upload",
        action="store_true",
        dest="confirm_cloud_upload",
    )

    return parser


def _operation_name(args: argparse.Namespace) -> str:
    resource = getattr(args, "resource", None)
    operation = getattr(args, "operation", None)
    if resource not in {"device", "library"} or operation is None:
        return "unknown"
    normalized_operation = str(operation).replace("-", "_")
    return f"{resource}.{normalized_operation}"


def _dispatch(args: argparse.Namespace, settings: KronikaSettings) -> dict[str, Any]:
    if args.resource == "device":
        return _dispatch_device(args, settings)
    if args.resource == "library":
        return _dispatch_library(args, settings)
    raise _UsageError(INVALID_INPUT_MESSAGE)


def _dispatch_device(args: argparse.Namespace, settings: KronikaSettings) -> dict[str, Any]:
    if args.operation == "register":
        _validate_device_register_input(args.display_name)
        return _with_device_repository(
            settings,
            lambda repository: _register_device(repository, args.display_name),
        )
    if args.operation == "get":
        device_id = _parse_device_id(args.device_id)
        return _with_device_repository(
            settings,
            lambda repository: _get_device(repository, device_id),
        )
    if args.operation == "list":
        return _with_device_repository(settings, _list_devices)
    raise _UsageError(INVALID_INPUT_MESSAGE)


def _dispatch_library(args: argparse.Namespace, settings: KronikaSettings) -> dict[str, Any]:
    if args.operation == "register":
        device_id = _parse_device_id(args.device_id)
        _validate_library_register_input(args.display_name)
        root = _prepare_library_root(args.root_path)
        return _with_library_repository(
            settings,
            lambda repository: _register_library(
                repository,
                device_id=device_id,
                display_name=args.display_name,
                root=root,
            ),
        )
    if args.operation == "get":
        library_id = _parse_library_id(args.library_id)
        return _with_library_repository(
            settings,
            lambda repository: _get_library(repository, library_id),
        )
    if args.operation == "list":
        return _with_library_repository(settings, _list_libraries)
    if args.operation == "scan-preview":
        library_id = _parse_library_id(args.library_id)
        limits = _parse_scan_limits(args.max_entries, args.max_candidates)
        return _with_scan_preview(settings, library_id, limits)
    if args.operation == "analyze-preview":
        library_id = _parse_library_id(args.library_id)
        relative_path = _parse_media_relative_path(args.relative_path)
        return _with_analyze_preview(settings, library_id, relative_path)
    if args.operation == "suggest-preview":
        library_id = _parse_library_id(args.library_id)
        relative_path = _parse_media_relative_path(args.relative_path)
        _validate_suggest_preview_prerequisites(confirm_cloud_upload=args.confirm_cloud_upload)
        provider_id = _parse_suggestion_provider_id(args.provider_id)
        model_id = _parse_suggestion_model_id(args.model_id)
        return _with_suggest_preview(
            settings,
            library_id,
            relative_path,
            provider_id=provider_id,
            model_id=model_id,
        )
    raise _UsageError(INVALID_INPUT_MESSAGE)


def _parse_device_id(value: str) -> DeviceId:
    try:
        return DeviceId.from_string(value)
    except FrameNestIdentityError:
        raise _InvalidInputError() from None


def _parse_library_id(value: str) -> LibraryId:
    try:
        return LibraryId.from_string(value)
    except FrameNestIdentityError:
        raise _InvalidInputError() from None


def _validate_device_register_input(display_name: str) -> None:
    try:
        Device(id=DeviceId.new(), display_name=display_name)
    except FrameNestDeviceError:
        raise _InvalidInputError() from None


def _validate_library_register_input(display_name: str) -> None:
    try:
        Library(
            id=LibraryId.new(),
            device_id=DeviceId.new(),
            display_name=display_name,
            root=_valid_placeholder_root(),
        )
    except FrameNestLibraryError:
        raise _InvalidInputError() from None


def _valid_placeholder_root() -> LibraryRoot:
    from kronika.adapters.cli.library_root import native_library_path_flavor
    from kronika.domain import LibraryPathFlavor

    flavor = native_library_path_flavor()
    path = "C:\\" if flavor == LibraryPathFlavor.WINDOWS else "/"
    return LibraryRoot(flavor=flavor, path=path)


def _prepare_library_root(raw_path: str) -> LibraryRoot:
    try:
        return prepare_library_root(raw_path)
    except LibraryRootNotUsableError:
        raise _RootNotUsableError() from None


def _register_device(repository: DeviceRepository, display_name: str) -> dict[str, Any]:
    try:
        device = Device(id=DeviceId.new(), display_name=display_name)
    except FrameNestDeviceError:
        raise _InvalidInputError() from None
    repository.add(device)
    return {"device": _device_payload(device)}


def _get_device(repository: DeviceRepository, device_id: DeviceId) -> dict[str, Any]:
    device = repository.get(device_id)
    if device is None:
        raise _DeviceNotFoundError()
    return {"device": _device_payload(device)}


def _list_devices(repository: DeviceRepository) -> dict[str, Any]:
    devices = repository.list_all()
    return {"devices": [_device_payload(device) for device in devices]}


def _register_library(
    repository: LibraryRepository,
    *,
    device_id: DeviceId,
    display_name: str,
    root: LibraryRoot,
) -> dict[str, Any]:
    try:
        library = Library(
            id=LibraryId.new(),
            device_id=device_id,
            display_name=display_name,
            root=root,
        )
    except FrameNestLibraryError:
        raise _InvalidInputError() from None
    try:
        repository.add(library)
    except LibraryDeviceNotFoundError:
        raise _LibraryDeviceNotFoundError() from None
    except LibraryRootAlreadyRegisteredError:
        raise _RootAlreadyRegisteredError() from None
    return {"library": _library_payload(library)}


def _get_library(repository: LibraryRepository, library_id: LibraryId) -> dict[str, Any]:
    library = repository.get(library_id)
    if library is None:
        raise _LibraryNotFoundError()
    return {"library": _library_payload(library)}


def _list_libraries(repository: LibraryRepository) -> dict[str, Any]:
    libraries = repository.list_all()
    return {"libraries": [_library_payload(library) for library in libraries]}


def _parse_media_relative_path(value: str) -> MediaRelativePath:
    try:
        return MediaRelativePath(value)
    except FrameNestMediaAnalysisError:
        raise _InvalidInputError() from None


def _validate_suggest_preview_prerequisites(*, confirm_cloud_upload: bool) -> None:
    if not confirm_cloud_upload:
        raise _CloudConfirmationRequiredError()
    if not os.environ.get("NVIDIA_API_KEY", "").strip():
        raise _NvidiaCredentialMissingError()


def _parse_suggestion_provider_id(value: str) -> str:
    if value != DEFAULT_PROVIDER_ID:
        raise _UnsupportedProviderError()
    return value


def _parse_suggestion_model_id(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_MODEL_ID_LENGTH:
        raise _InvalidModelError()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise _InvalidModelError()
    return value


def _parse_scan_limits(max_entries: object, max_candidates: object) -> LibraryScanLimits:
    try:
        return LibraryScanLimits(max_entries=max_entries, max_candidates=max_candidates)
    except FrameNestLibraryScanError:
        raise _InvalidInputError() from None


def _scan_preview(
    repository: LibraryRepository,
    library_id: LibraryId,
    limits: LibraryScanLimits,
) -> dict[str, Any]:
    service = PreviewLibraryScan(repository, LocalLibraryScanner())
    try:
        preview = service.execute(library_id, limits)
    except LibraryScanNotFoundError:
        raise _LibraryNotFoundError() from None
    except LibraryScanUnavailableError:
        raise _ScanUnavailableError() from None
    except LibraryScanFailedError:
        raise
    return _scan_preview_payload(preview)


def _scan_preview_payload(preview: LibraryScanPreviewResult) -> dict[str, Any]:
    return {
        "library_id": preview.library_id.to_string(),
        "limits": {
            "max_entries": preview.limits.max_entries,
            "max_candidates": preview.limits.max_candidates,
        },
        "summary": {
            "entries_seen": preview.summary.entries_seen,
            "directories_seen": preview.summary.directories_seen,
            "regular_files_seen": preview.summary.regular_files_seen,
            "candidate_files_seen": preview.summary.candidate_files_seen,
            "candidate_bytes_seen": preview.summary.candidate_bytes_seen,
            "skipped_hidden_entries": preview.summary.skipped_hidden_entries,
            "skipped_symlink_entries": preview.summary.skipped_symlink_entries,
            "skipped_other_entries": preview.summary.skipped_other_entries,
            "inaccessible_entries": preview.summary.inaccessible_entries,
            "truncated": preview.summary.truncated,
            "candidates_truncated": preview.summary.candidates_truncated,
        },
        "candidates": [
            {
                "relative_path": candidate.relative_path,
                "kind": candidate.kind.value,
                "extension": candidate.extension,
                "size_bytes": candidate.size_bytes,
            }
            for candidate in preview.candidates
        ],
    }


def _with_scan_preview(
    settings: KronikaSettings,
    library_id: LibraryId,
    limits: LibraryScanLimits,
) -> dict[str, Any]:
    status = inspect_database_migration_status(settings)
    if status.state != "at_head":
        raise _NotReadyError()
    engine = create_sqlite_engine(settings.database_path)
    try:
        repository = SqliteLibraryRepository(engine)
        return _scan_preview(repository, library_id, limits)
    finally:
        dispose_engine(engine)


def _analyze_preview(
    repository: LibraryRepository,
    library_id: LibraryId,
    relative_path: MediaRelativePath,
) -> dict[str, Any]:
    service = PrepareLocalMediaAnalysis(repository, LocalMediaAnalysisAdapter())
    try:
        prepared = service.execute(library_id, relative_path)
    except MediaAnalysisNotFoundError:
        raise _LibraryNotFoundError() from None
    except MediaAnalysisUnavailableError:
        raise _AnalysisUnavailableError() from None
    except MediaAnalysisFailedError:
        raise
    return _analyze_preview_payload(library_id, prepared)


def _analyze_preview_payload(
    library_id: LibraryId,
    prepared: PreparedAnalysisResult,
) -> dict[str, Any]:
    metadata = prepared.technical_metadata
    return {
        "library_id": library_id.to_string(),
        "relative_path": prepared.relative_path.value,
        "candidate_kind": prepared.candidate_kind.value,
        "technical_metadata": {
            "duration_ms": metadata.duration_ms,
            "width": metadata.width,
            "height": metadata.height,
            "video_codec": metadata.video_codec,
            "container_formats": list(metadata.container_formats),
            "has_audio": metadata.has_audio,
        },
        "requested_frame_count": prepared.requested_frame_count,
        "produced_frame_count": len(prepared.representative_frames),
        "representative_frames": [
            {
                "ordinal": index,
                "timestamp_ms": frame.timestamp_ms,
                "mime_type": frame.mime_type,
                "byte_size": frame.byte_size,
                "sha256": frame.sha256,
            }
            for index, frame in enumerate(prepared.representative_frames, start=1)
        ],
        "warnings": list(prepared.warnings),
        "tools": {
            "ffprobe": prepared.ffprobe_version,
            "ffmpeg": prepared.ffmpeg_version,
        },
    }


def _with_analyze_preview(
    settings: KronikaSettings,
    library_id: LibraryId,
    relative_path: MediaRelativePath,
) -> dict[str, Any]:
    status = inspect_database_migration_status(settings)
    if status.state != "at_head":
        raise _NotReadyError()
    engine = create_sqlite_engine(settings.database_path)
    try:
        repository = SqliteLibraryRepository(engine)
        return _analyze_preview(repository, library_id, relative_path)
    finally:
        dispose_engine(engine)


def _suggest_preview(
    repository: LibraryRepository,
    library_id: LibraryId,
    relative_path: MediaRelativePath,
    *,
    provider_id: str,
    model_id: str,
) -> dict[str, Any]:
    credential = NvidiaApiCredential(os.environ["NVIDIA_API_KEY"])
    provider = NvidiaNimMediaSuggestionProvider(
        credential,
        model_id=model_id,
        provider_id=provider_id,
    )
    service = PreviewMediaSuggestion(
        repository,
        LocalMediaAnalysisAdapter(),
        provider,
    )
    try:
        preview = service.execute(library_id, relative_path)
    except MediaSuggestionNotFoundError:
        raise _LibraryNotFoundError() from None
    except MediaSuggestionPreparationUnavailableError:
        raise _SuggestionPreparationUnavailableError() from None
    except (
        MediaSuggestionPreparationFailedError,
        MediaAnalysisFailedError,
    ):
        raise
    except MediaSuggestionProviderUnavailableError:
        raise _SuggestionProviderUnavailableError() from None
    except MediaSuggestionProviderRateLimitedError:
        raise _SuggestionProviderRateLimitedError() from None
    except MediaSuggestionProviderAuthError:
        raise _SuggestionProviderAuthError() from None
    except MediaSuggestionProviderInvalidResponseError:
        raise _SuggestionProviderInvalidResponseError() from None
    except MediaSuggestionProviderFailedError:
        raise
    return _suggest_preview_payload(preview)


def _suggest_preview_payload(preview: MediaSuggestionPreviewResult) -> dict[str, Any]:
    metadata = preview.prepared.technical_metadata
    suggestion = preview.suggestion
    return {
        "library_id": preview.library_id.to_string(),
        "relative_path": preview.relative_path.value,
        "provider_id": suggestion.provider_id,
        "model_id": suggestion.model_id,
        "prompt_version": suggestion.prompt_version,
        "sent_frame_count": preview.sent_frame_count,
        "technical_metadata": {
            "duration_ms": metadata.duration_ms,
            "width": metadata.width,
            "height": metadata.height,
            "video_codec": metadata.video_codec,
            "container_formats": list(metadata.container_formats),
            "has_audio": metadata.has_audio,
        },
        "suggestion": {
            "title": suggestion.title,
            "description": suggestion.description,
            "collection": suggestion.collection,
            "tags": list(suggestion.tags),
            "suggested_filename": suggestion.suggested_filename,
            "confidence": suggestion.confidence,
            "evidence": list(suggestion.evidence),
            "uncertainties": list(suggestion.uncertainties),
        },
    }


def _with_suggest_preview(
    settings: KronikaSettings,
    library_id: LibraryId,
    relative_path: MediaRelativePath,
    *,
    provider_id: str,
    model_id: str,
) -> dict[str, Any]:
    status = inspect_database_migration_status(settings)
    if status.state != "at_head":
        raise _NotReadyError()
    engine = create_sqlite_engine(settings.database_path)
    try:
        repository = SqliteLibraryRepository(engine)
        return _suggest_preview(
            repository,
            library_id,
            relative_path,
            provider_id=provider_id,
            model_id=model_id,
        )
    finally:
        dispose_engine(engine)


def _device_payload(device: Device) -> dict[str, str]:
    return {"id": device.id.to_string(), "display_name": device.display_name}


def _library_payload(library: Library) -> dict[str, Any]:
    return {
        "id": library.id.to_string(),
        "device_id": library.device_id.to_string(),
        "display_name": library.display_name,
        "root": {
            "flavor": library.root.flavor.value,
            "path": library.root.path,
        },
    }


def _with_device_repository(
    settings: KronikaSettings,
    callback: Callable[[DeviceRepository], dict[str, Any]],
) -> dict[str, Any]:
    status = inspect_database_migration_status(settings)
    if status.state != "at_head":
        raise _NotReadyError()
    engine = create_sqlite_engine(settings.database_path)
    try:
        repository = SqliteDeviceRepository(engine)
        return callback(repository)
    finally:
        dispose_engine(engine)


def _with_library_repository(
    settings: KronikaSettings,
    callback: Callable[[LibraryRepository], dict[str, Any]],
) -> dict[str, Any]:
    status = inspect_database_migration_status(settings)
    if status.state != "at_head":
        raise _NotReadyError()
    engine = create_sqlite_engine(settings.database_path)
    try:
        repository = SqliteLibraryRepository(engine)
        return callback(repository)
    finally:
        dispose_engine(engine)


def _write_success(operation: str, result: dict[str, Any]) -> None:
    payload = {
        "operation": operation,
        "state": "ok",
        **result,
    }
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), file=sys.stdout)


def _write_error(*, operation: str, error_code: str, message: str) -> None:
    payload = {
        "operation": operation,
        "state": "error",
        "error_code": error_code,
        "message": message,
    }
    print(json.dumps(payload, separators=(",", ":")), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
