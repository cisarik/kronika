"""Standard-library CLI for FrameNest catalog backup operations."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from kronika.infrastructure.persistence.catalog_backup import (
    BackupError,
    BackupResult,
    create_catalog_backup,
    restore_catalog_backup,
    verify_catalog_backup,
)
from kronika.infrastructure.persistence.catalog_backup_ops import (
    CatalogBackupIdentityEnvironmentConflictError,
    CatalogBackupOpsConfig,
    CatalogBackupOpsError,
    build_retention_plan,
    expire_automatic_backups,
    export_latest_scheduled_recovery_point,
    list_bundle_summaries,
    load_catalog_backup_ops_config,
    operation_lock,
    read_operator_status,
    run_offdevice_catalog_copy,
    run_scheduled_catalog_backup,
    verify_restore_bundle,
)
from kronika.infrastructure.persistence.catalog_backup_offdevice import (
    OffdeviceError,
    OffdeviceIdentityEnvironmentConflictError,
)
from kronika.infrastructure.persistence.catalog_backup_transfer import TransferError

INVALID_INPUT_CODE = "FRAMENEST_BACKUP_INVALID_INPUT"
COMMAND_FAILED_CODE = "FRAMENEST_BACKUP_COMMAND_FAILED"
BUSY_CODE = "BACKUP_OPS_BUSY"
PROTECTED_OPERATIONS = frozenset(
    {
        "create",
        "verify",
        "restore",
        "run-scheduled",
        "run-offdevice",
        "verify-restore",
        "expire",
        "export-latest",
    }
)


class _UsageError(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError("Invalid backup command.")


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch catalog backup operations and return a process exit code."""
    operation = "unknown"
    try:
        parser = _build_parser()
        args = parser.parse_args(argv)
        operation = args.operation
        if operation in PROTECTED_OPERATIONS:
            config = load_catalog_backup_ops_config()
            with operation_lock(config) as held:
                if not held:
                    raise CatalogBackupOpsError(
                        "Another catalog backup operation is in progress.",
                        error_code=BUSY_CODE,
                    )
                return _dispatch_protected(operation, args, config)
        return _dispatch_readonly(operation, args)
    except _UsageError:
        _write_error(
            operation=operation,
            error_code=INVALID_INPUT_CODE,
            message="Invalid backup command.",
        )
        return 2
    except CatalogBackupOpsError as exc:
        if isinstance(exc, CatalogBackupIdentityEnvironmentConflictError):
            _write_error(
                operation=operation,
                error_code=COMMAND_FAILED_CODE,
                message=str(exc),
            )
            return exc.exit_status
        if exc.error_code == BUSY_CODE:
            _write_error(
                operation=operation,
                error_code=BUSY_CODE,
                message="Another catalog backup operation is in progress.",
            )
            return 1
        if exc.error_code == "PENDING_CLEANUP":
            _write_error(
                operation=operation,
                error_code="PENDING_CLEANUP",
                message="Disposable restore cleanup is pending.",
            )
            return 1
        if exc.error_code == "INVALID_KEEP_AUTO":
            _write_error(
                operation=operation,
                error_code="INVALID_KEEP_AUTO",
                message="Catalog backup retention configuration is invalid.",
            )
            return 1
        _write_error(
            operation=operation,
            error_code=COMMAND_FAILED_CODE,
            message="Backup command failed.",
        )
        return 1
    except OffdeviceError as exc:
        if isinstance(exc, OffdeviceIdentityEnvironmentConflictError):
            _write_error(
                operation=operation,
                error_code=exc.error_code,
                message=str(exc),
            )
            return exc.exit_status
        message = "Off-device catalog copy failed."
        if exc.error_code == "OFFDEVICE_DISABLED":
            message = "Off-device catalog copy is disabled."
        elif exc.error_code == "OFFDEVICE_COPY_CONFLICT":
            message = "Off-device destination bundle conflicts."
        elif exc.error_code == "OFFDEVICE_ATOMIC_PUBLISH_UNSUPPORTED":
            message = "Atomic off-device publication is unsupported."
        _write_error(
            operation=operation,
            error_code=exc.error_code,
            message=message,
        )
        return 1
    except TransferError as exc:
        _write_error(
            operation=operation,
            error_code=exc.error_code,
            message="Catalog export transfer failed.",
        )
        return 1
    except (BackupError, Exception):
        _write_error(
            operation=operation,
            error_code=COMMAND_FAILED_CODE,
            message="Backup command failed.",
        )
        return 1


def _dispatch_protected(operation: str, args: argparse.Namespace, config: CatalogBackupOpsConfig) -> int:
    """Execute a mutating backup CLI command under the shared ops lock."""
    if operation == "create":
        result = create_catalog_backup(Path(args.source), Path(args.output))
        _write_success(operation, result)
        return 0
    if operation == "verify":
        result = verify_catalog_backup(Path(args.bundle))
        _write_success(operation, result)
        return 0
    if operation == "restore":
        result = restore_catalog_backup(Path(args.bundle), Path(args.destination))
        _write_success(operation, result)
        return 0
    if operation == "run-scheduled":
        scheduled = run_scheduled_catalog_backup(config)
        _write_payload(
            {
                "operation": operation,
                "state": "succeeded",
                "bundle_id": scheduled.bundle_id,
                "catalog": {
                    "size_bytes": scheduled.catalog_size_bytes,
                    "sha256": scheduled.catalog_sha256,
                    "alembic_revision": scheduled.alembic_revision,
                },
                "retention": {
                    "keep_auto": scheduled.retention.keep_auto,
                    "retain": list(scheduled.retention.retain),
                    "expire": list(scheduled.retention.expire),
                    "expired": list(scheduled.expired),
                },
                "pending_cleanup": scheduled.pending_cleanup,
            }
        )
        return 0
    if operation == "run-offdevice":
        result = run_offdevice_catalog_copy(config)
        _write_payload(
            {
                "operation": operation,
                "state": "succeeded",
                "bundle_id": result.bundle_id,
                "catalog": {
                    "size_bytes": result.catalog_size_bytes,
                    "sha256": result.catalog_sha256,
                    "alembic_revision": result.alembic_revision,
                },
                "reused_existing": result.reused_existing,
                "pending_cleanup": result.pending_cleanup,
                "semantic": dict(result.semantic),
            }
        )
        return 0
    if operation == "verify-restore":
        evidence = verify_restore_bundle(config, Path(args.bundle))
        _write_payload({"state": "verified_restored", **evidence})
        return 0
    if operation == "expire":
        if args.apply and args.dry_run:
            raise _UsageError("Invalid backup command.")
        apply = bool(args.apply)
        payload = expire_automatic_backups(config, apply=apply)
        _write_payload(payload)
        return 0
    if operation == "export-latest":
        # Protocol bytes occupy stdout exclusively; do not emit JSON success.
        export_latest_scheduled_recovery_point(config, sys.stdout.buffer)
        return 0
    raise _UsageError("Invalid backup command.")


def _dispatch_readonly(operation: str, args: argparse.Namespace) -> int:
    """Execute non-mutating summary commands without holding the ops lock."""
    config = load_catalog_backup_ops_config()
    if operation == "status":
        _write_payload(read_operator_status(config))
        return 0
    if operation == "list":
        summaries = list_bundle_summaries(config, verify=True)
        _write_payload(
            {
                "operation": "list",
                "bundles": [
                    {
                        "bundle_id": item.bundle_id,
                        "classification": item.classification,
                        "complete": item.complete,
                        "verification_eligible": item.verification_eligible,
                        "retention_eligible": item.retention_eligible,
                        "created_at_utc": item.created_at_utc,
                        "alembic_revision": item.alembic_revision,
                    }
                    for item in summaries
                ],
            }
        )
        return 0
    if operation == "retain-plan":
        plan = build_retention_plan(config, verify=True)
        _write_payload(
            {
                "operation": "retain-plan",
                "keep_auto": plan.keep_auto,
                "retain": list(plan.retain),
                "expire": list(plan.expire),
                "automatic_count": plan.automatic_count,
                "pinned_count": plan.pinned_count,
            }
        )
        return 0
    raise _UsageError("Invalid backup command.")


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="framenest-backup", add_help=True)
    subcommands = parser.add_subparsers(dest="operation", required=True)

    create = subcommands.add_parser("create", help="Create a catalog backup bundle.")
    create.add_argument("--source", required=True, help="Existing catalog SQLite database.")
    create.add_argument("--output", required=True, help="New backup bundle directory.")

    verify = subcommands.add_parser("verify", help="Verify a catalog backup bundle.")
    verify.add_argument("--bundle", required=True, help="Backup bundle directory.")

    restore = subcommands.add_parser("restore", help="Restore a bundle to a new catalog path.")
    restore.add_argument("--bundle", required=True, help="Backup bundle directory.")
    restore.add_argument("--destination", required=True, help="Absent destination database path.")

    subcommands.add_parser(
        "run-scheduled",
        help="Run the daily create, verify, restore, and retention pipeline.",
    )
    subcommands.add_parser(
        "run-offdevice",
        help=(
            "Copy the latest verified scheduled catalog recovery point to the "
            "configured off-device mount and restore-verify it."
        ),
    )
    subcommands.add_parser(
        "export-latest",
        help=(
            "Stream the latest verified scheduled catalog recovery point as "
            "protocol-v1 bytes on stdout (no arguments)."
        ),
    )

    verify_restore = subcommands.add_parser(
        "verify-restore",
        help="Restore-verify one selected bundle into a disposable destination.",
    )
    verify_restore.add_argument("--bundle", required=True, help="Backup bundle directory.")

    subcommands.add_parser("status", help="Show catalog backup operator status.")
    subcommands.add_parser("list", help="List catalog backup bundles.")
    subcommands.add_parser("retain-plan", help="Show deterministic retention plan.")

    expire = subcommands.add_parser("expire", help="Expire eligible automatic catalog backups.")
    expire.add_argument(
        "--dry-run",
        action="store_true",
        help="Print eligible deletions without deleting (default when --apply is omitted).",
    )
    expire.add_argument(
        "--apply",
        action="store_true",
        help="Apply the safely revalidated expiration plan.",
    )
    return parser


def _write_success(operation: str, result: BackupResult) -> None:
    payload = {
        "operation": operation,
        "state": result.state,
        "catalog": {
            "size_bytes": result.catalog_size_bytes,
            "sha256": result.catalog_sha256,
            "alembic_revision": result.alembic_revision,
        },
    }
    _write_payload(payload)


def _write_payload(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), file=sys.stdout)


def _write_error(*, operation: str, error_code: str, message: str) -> None:
    payload = {
        "operation": operation,
        "state": "error",
        "error_code": error_code,
        "message": message,
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
