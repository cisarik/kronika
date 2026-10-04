"""Standard-library CLI for FrameNest workstation catalog recovery."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from kronika.infrastructure.persistence.catalog_backup_workstation import (
    WorkstationError,
    init_workstation_store,
    list_workstation_snapshots,
    pull_workstation_snapshot,
    verify_workstation_snapshot,
)

INVALID_INPUT_CODE = "FRAMENEST_RECOVERY_INVALID_INPUT"
COMMAND_FAILED_CODE = "FRAMENEST_RECOVERY_COMMAND_FAILED"


class _UsageError(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError("Invalid recovery command.")


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch workstation recovery operations and return a process exit code."""
    operation = "unknown"
    try:
        parser = _build_parser()
        args = parser.parse_args(argv)
        operation = args.operation
        return _dispatch(operation, args)
    except _UsageError:
        _write_error(
            operation=operation,
            error_code=INVALID_INPUT_CODE,
            message="Invalid recovery command.",
        )
        return 2
    except WorkstationError as exc:
        _write_error(
            operation=operation,
            error_code=exc.error_code,
            message=str(exc) or "Recovery command failed.",
        )
        return 1
    except Exception:
        _write_error(
            operation=operation,
            error_code=COMMAND_FAILED_CODE,
            message="Recovery command failed.",
        )
        return 1


def _dispatch(operation: str, args: argparse.Namespace) -> int:
    store_root = Path(args.store_root)
    mount_root = Path(args.mount_root)
    if operation == "init-store":
        result = init_workstation_store(store_root=store_root, mount_root=mount_root)
        _write_payload(
            {
                "operation": operation,
                "state": "succeeded",
                "store_id": result.store_id,
                "created": result.created,
            }
        )
        return 0
    expected_store_id = args.expected_store_id
    if operation == "pull":
        result = pull_workstation_snapshot(
            store_root=store_root,
            mount_root=mount_root,
            expected_store_id=expected_store_id,
            ssh_target=args.ssh_target,
            ssh_port=args.ssh_port,
            connect_timeout_seconds=args.connect_timeout_seconds,
            transfer_timeout_seconds=args.transfer_timeout_seconds,
        )
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
                "semantic": dict(result.semantic),
            }
        )
        return 0
    if operation == "list":
        snapshots = list_workstation_snapshots(
            store_root=store_root,
            mount_root=mount_root,
            expected_store_id=expected_store_id,
        )
        _write_payload(
            {
                "operation": operation,
                "state": "succeeded",
                "snapshots": snapshots,
            }
        )
        return 0
    if operation == "verify":
        evidence = verify_workstation_snapshot(
            store_root=store_root,
            mount_root=mount_root,
            expected_store_id=expected_store_id,
            bundle_id=args.bundle_id,
        )
        _write_payload(
            {
                "operation": operation,
                "state": "verified",
                **evidence,
            }
        )
        return 0
    raise _UsageError("Invalid recovery command.")


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="framenest-recovery", add_help=True)
    subcommands = parser.add_subparsers(dest="operation", required=True)

    init_store = subcommands.add_parser(
        "init-store",
        help="Initialize or verify a trusted workstation snapshot store.",
    )
    _add_store_root_args(init_store, require_expected_id=False)

    pull = subcommands.add_parser(
        "pull",
        help="Pull the latest scheduled catalog recovery point into the snapshot store.",
    )
    _add_store_root_args(pull, require_expected_id=True)
    pull.add_argument("--ssh-target", required=True, help="OpenSSH destination (user@host or alias).")
    pull.add_argument("--ssh-port", type=int, default=None, help="Optional SSH port.")
    pull.add_argument(
        "--connect-timeout-seconds",
        type=int,
        default=30,
        help="OpenSSH connection timeout in seconds.",
    )
    pull.add_argument(
        "--transfer-timeout-seconds",
        type=int,
        default=1800,
        help="Overall pull/transfer timeout in seconds.",
    )

    list_cmd = subcommands.add_parser(
        "list",
        help="List recognized final workstation snapshots (offline).",
    )
    _add_store_root_args(list_cmd, require_expected_id=True)

    verify = subcommands.add_parser(
        "verify",
        help="Strictly verify one local workstation snapshot (offline).",
    )
    _add_store_root_args(verify, require_expected_id=True)
    verify.add_argument("--bundle-id", required=True, help="Exact snapshot/bundle identity.")
    return parser


def _add_store_root_args(parser: argparse.ArgumentParser, *, require_expected_id: bool) -> None:
    parser.add_argument(
        "--store-root",
        required=True,
        help="Absolute workstation snapshot store root beneath the mount.",
    )
    parser.add_argument(
        "--mount-root",
        required=True,
        help="Absolute expected mount root for the snapshot store filesystem.",
    )
    parser.add_argument(
        "--expected-store-id",
        required=require_expected_id,
        default=None,
        help="Non-secret 32-hex store identity pin.",
    )


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
