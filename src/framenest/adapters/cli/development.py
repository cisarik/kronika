"""Human-facing CLI for the local FrameNest development launcher."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from framenest.infrastructure.runtime.development import (
    DEFAULT_LOG_LINES,
    DevelopmentRuntime,
    DevelopmentRuntimeError,
    IdentityEnvironmentDevelopmentError,
    RuntimeResult,
    RuntimeStatus,
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
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
