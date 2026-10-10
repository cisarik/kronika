"""Human-facing CLI for the local FrameNest development launcher."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from kronika.identity_env import IdentityEnvironmentConflictFailure
from kronika.infrastructure.runtime.development import (
    DEFAULT_LOG_LINES,
    DevelopmentRuntime,
    DevelopmentRuntimeError,
    IdentityEnvironmentDevelopmentError,
    RuntimeResult,
    RuntimeStatus,
)
from kronika.infrastructure.runtime.local_state_migration import (
    LocalStateMigration,
    LocalStateMigrationError,
    ManagedDevelopmentServerActiveError,
    MigrationReport,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_STOPPED = 3
EXIT_UNHEALTHY = 4
EXIT_CONFLICT = 5


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="framenest-dev",
        description="Control the local Kronika browser-development server.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    start = subcommands.add_parser("start", help="Start the managed development server.")
    start.add_argument("--no-open", action="store_true", help="Do not open a browser.")

    subcommands.add_parser("stop", help="Stop the verified managed server.")

    restart = subcommands.add_parser("restart", help="Restart the managed development server.")
    restart.add_argument("--no-open", action="store_true", help="Do not open a browser.")

    subcommands.add_parser("status", help="Show managed development server status.")
    subcommands.add_parser("open", help="Open the managed server in the default browser.")

    logs = subcommands.add_parser("logs", help="Show the development server log.")
    logs.add_argument("--follow", action="store_true", help="Follow new log lines.")
    logs.add_argument(
        "--lines",
        type=int,
        default=DEFAULT_LOG_LINES,
        help=f"Number of recent lines to show before following. Default: {DEFAULT_LOG_LINES}.",
    )

    migration = subcommands.add_parser(
        "migrate-identity-paths",
        help="Check or migrate owned local development and AI state to canonical paths.",
    )
    migration.add_argument(
        "operation",
        choices=("check", "apply"),
        help="Check the owned mapping without writing state, or copy to absent destinations.",
    )
    migration.add_argument(
        "--receipt-dir",
        default=None,
        help="Private directory for the migration receipt outside the repository.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "migrate-identity-paths":
            return _run_identity_path_migration(args)
        runtime = DevelopmentRuntime()
        if args.command == "start":
            return _print_result(runtime.start(open_after_start=not args.no_open))
        if args.command == "stop":
            return _print_result(runtime.stop())
        if args.command == "restart":
            return _print_result(runtime.restart(open_after_start=not args.no_open))
        if args.command == "status":
            return _print_status(runtime.status())
        if args.command == "open":
            return _print_result(runtime.open())
        if args.command == "logs":
            if args.lines < 0:
                print("Log line count must be zero or greater.", file=sys.stderr)
                return EXIT_USAGE
            return _print_logs(runtime, follow=args.follow, lines=args.lines)
    except DevelopmentRuntimeError as exc:
        print(f"Kronika launcher error: {exc}", file=sys.stderr)
        if isinstance(exc, IdentityEnvironmentDevelopmentError):
            return exc.exit_status
        return EXIT_ERROR
    return EXIT_USAGE


def _run_identity_path_migration(args: argparse.Namespace) -> int:
    receipt_directory = (
        Path(args.receipt_dir).expanduser() if args.receipt_dir else None
    )
    try:
        migration = LocalStateMigration(receipt_dir=receipt_directory)
        if args.operation == "check":
            report = migration.check()
        else:
            report = migration.apply()
    except LocalStateMigrationError as exc:
        print(f"Kronika identity-path migration error: {exc}", file=sys.stderr)
        if isinstance(exc, IdentityEnvironmentConflictFailure):
            return exc.exit_status
        if isinstance(exc, ManagedDevelopmentServerActiveError):
            return EXIT_CONFLICT
        return EXIT_ERROR
    _print_migration_report(report)
    if report.operation == "apply" and report.refused_keys:
        return EXIT_CONFLICT
    return EXIT_OK


def _print_migration_report(report: MigrationReport) -> None:
    counts = report.counts
    print(f"Identity-path migration {report.operation}")
    print(f"Classes: {counts['classes']} total, {counts['overridden']} explicit overrides")
    if report.operation == "check":
        print(
            f"Sources: present {counts['sources_present']}, "
            f"absent {counts['sources_absent']}"
        )
        print(
            f"Destinations: absent {counts['destinations_absent']}, "
            f"occupied {counts['destinations_occupied']}"
        )
    else:
        print(f"Copied verified: {counts['copied_verified']}")
        print(f"Already verified: {counts['already_verified']}")
        print(f"Sources absent: {counts['sources_absent']}")
        print(f"Refused: {counts['refused']}")
        if report.refused_keys:
            print("Refused classes: " + ", ".join(report.refused_keys))
    print("Receipt: recorded privately.")


def _print_result(result: RuntimeResult) -> int:
    stream = sys.stdout if result.ok else sys.stderr
    print(result.message, file=stream)
    _print_status_lines(result.status, stream=stream)
    return _exit_for_status(result.status, ok=result.ok)


def _print_status(status: RuntimeStatus) -> int:
    _print_status_lines(status, stream=sys.stdout)
    return _exit_for_status(status, ok=status.kind == "running")


def _print_status_lines(status: RuntimeStatus, *, stream: object) -> None:
    print(f"Status: {status.kind}", file=stream)
    if status.url is not None:
        print(f"URL: {status.url}", file=stream)
    if status.pid is not None:
        print(f"PID: {status.pid}", file=stream)
    print(f"Database: {status.database_state}", file=stream)
    print(f"Log: {'available' if status.log_available else 'not yet available'}", file=stream)
    print(status.message, file=stream)


def _print_logs(runtime: DevelopmentRuntime, *, follow: bool, lines: int) -> int:
    tail = runtime.read_log_tail(lines=lines)
    if not tail:
        print("Kronika development log is not yet available.")
    else:
        for line in tail:
            print(line, end="" if line.endswith("\n") else "\n")
    if follow:
        try:
            for line in runtime.follow_log():
                print(line, end="" if line.endswith("\n") else "\n", flush=True)
        except KeyboardInterrupt:
            return EXIT_OK
    return EXIT_OK


def _exit_for_status(status: RuntimeStatus, *, ok: bool) -> int:
    if ok:
        return EXIT_OK
    if status.kind in {"stopped", "stale"}:
        return EXIT_STOPPED
    if status.kind == "unhealthy":
        return EXIT_UNHEALTHY
    if status.kind == "conflict":
        return EXIT_CONFLICT
    return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
