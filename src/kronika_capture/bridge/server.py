"""Loopback HTTP server for the stripped ask bridge."""

from __future__ import annotations

import json
import logging
import re
import signal
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from kronika_capture import config
from kronika_capture.bridge import auth
from kronika_capture.bridge.jobs import CLIENT_KINDS, DEFAULT_CLIENT_KIND, BridgeError, JobManager, utc_now_iso
from kronika_capture.bridge.journal import JournalResults
from kronika_capture.bridge.store import Store
from kronika_capture.errors import EXIT_BRIDGE

LOGGER = logging.getLogger("kronika.bridge")

JOB_PATH = re.compile(r"^/v1/jobs/([A-Za-z0-9_-]+)$")
JOB_ACTION_PATH = re.compile(r"^/v1/jobs/([A-Za-z0-9_-]+)/(cancel|events|result)$")
FILE_PATH = re.compile(r"^/v1/files/([A-Za-z0-9_.-]+)$")
REMOVED_EXACT_PATHS = frozenset({"/v1/capture-auth", "/v1/ingest", "/v1/author"})


@dataclass
class BridgeState:
    store: Store
    jobs: JobManager
    token: str
    results: JournalResults
    port: int = 0


class BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, state: BridgeState) -> None:
        super().__init__(address, handler)
        self.state = state
        state.port = self.server_address[1]


class BridgeRequestHandler(BaseHTTPRequestHandler):
    server_version = f"{config.APP_NAME}/{config.BRIDGE_VERSION}"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 120

    def setup(self) -> None:
        super().setup()
        try:
            self.connection.settimeout(self.timeout)
        except OSError:
            pass

    def log_message(self, format: str, *args) -> None:
        LOGGER.debug("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_OPTIONS(self) -> None:
        self._handle("OPTIONS")

    def do_PUT(self) -> None:
        self._reject_method("PUT")

    def do_DELETE(self) -> None:
        self._reject_method("DELETE")

    def do_PATCH(self) -> None:
        self._reject_method("PATCH")

    def do_HEAD(self) -> None:
        self._reject_method("HEAD")

    def _reject_method(self, method: str) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/s" or parsed.path.startswith("/s/") or parsed.path in REMOVED_EXACT_PATHS:
            self._send_json(
                404,
                {
                    "ok": False,
                    "error": {"code": "E_INTERNAL", "step": "route", "message": "unknown path"},
                },
            )
            return
        self.send_error(501, f"Unsupported method ({method!r})")

    def _handle(self, method: str) -> None:
        state: BridgeState = self.server.state
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        origin = self.headers.get("Origin")
        job_id = None
        error_code = None
        status = 500
        allow_origin = None
        try:
            if path == "/s" or path.startswith("/s/") or path in REMOVED_EXACT_PATHS:
                status, error_code, job_id = self._unknown(allow_origin)
                return
            result = auth.check_request(
                self.headers,
                self.headers.get("Host"),
                state.token,
                port=state.port,
                origin=origin,
                exempt_token=method == "GET" and path == "/v1/health",
            )
            if not result.ok:
                status, error_code = self._send_auth_failure(result)
                return
            allow_origin = result.allow_origin
            if method == "OPTIONS":
                status = self._send_empty(204, allow_origin=allow_origin)
                return
            result_route = JOB_ACTION_PATH.match(path)
            is_result = bool(result_route and result_route.group(2) == "result")
            body, body_error = self._read_json_body(method, is_result=is_result)
            if body_error is not None:
                status, error_code = body_error
                self._send_error(status, error_code, "request", "invalid request body", allow_origin)
                return
            status, error_code, job_id = self._route(method, path, query, body, state, allow_origin)
        except BridgeError as exc:
            status, error_code = exc.status, exc.code
            self._send_error(exc.status, exc.code, exc.step, exc.message, allow_origin)
        except Exception:
            LOGGER.error("bridge: internal request failure")
            status, error_code = 500, "E_INTERNAL"
            self._safe_send_error(500, "E_INTERNAL", "internal", "internal error", allow_origin)
        finally:
            self._record_log(method, path, status, job_id, error_code)

    def _route(self, method, path, query, body, state: BridgeState, allow_origin):
        if method == "GET" and path == "/v1/health":
            return (
                self._send_json(
                    200,
                    {"ok": True, "api": config.APP_NAME, "api_version": config.API_VERSION},
                    allow_origin,
                ),
                None,
                None,
            )
        if method == "GET" and path == "/v1/status":
            return self._send_json(200, state.jobs.status(), allow_origin), None, None
        if method == "POST" and path == "/v1/hello":
            payload = state.jobs.hello(body)
            return self._send_json(200, payload, allow_origin), None, None
        if method == "POST" and path == "/v1/resume":
            return self._send_json(200, state.jobs.resume(body), allow_origin), None, None
        if method == "GET" and path == "/v1/next":
            wait = self._parse_wait(query)
            kind = self._parse_client(query)
            job = state.jobs.next_offer(
                wait, kind, runner_id=(query.get("runner_id") or [None])[0],
                epoch=(query.get("epoch") or [None])[0],
            )
            if job is None:
                return self._send_empty(204, allow_origin), None, None
            return (
                self._send_json(200, {"ok": True, "job": job.offer()}, allow_origin),
                None,
                job.job_id,
            )
        if method == "POST" and path == "/v1/jobs":
            job = state.jobs.create_job(body)
            return (
                self._send_json(200, {"ok": True, "job_id": job.job_id,
                                     "request_id": job.request_id, "status": job.status}, allow_origin),
                None,
                job.job_id,
            )
        match = JOB_ACTION_PATH.match(path)
        if match is not None:
            job_id, action = match.group(1), match.group(2)
            return self._job_action(method, action, job_id, body, state, allow_origin)
        match = JOB_PATH.match(path)
        if match is not None and method == "GET":
            job_id = match.group(1)
            payload = state.jobs.get(job_id, self._parse_since(query))
            return self._send_json(200, payload, allow_origin), None, job_id
        if FILE_PATH.match(path) is not None:
            return (
                self._send_json(
                    501,
                    {
                        "ok": False,
                        "error": {
                            "code": "E_INTERNAL",
                            "step": "availability",
                            "message": config.UPLOAD_UNAVAILABLE,
                        },
                    },
                    allow_origin,
                ),
                "E_INTERNAL",
                None,
            )
        return self._unknown(allow_origin)

    def _unknown(self, allow_origin):
        return (
            self._send_json(
                404,
                {
                    "ok": False,
                    "error": {"code": "E_INTERNAL", "step": "route", "message": "unknown path"},
                },
                allow_origin,
            ),
            "E_INTERNAL",
            None,
        )

    def _job_action(self, method, action, job_id, body, state: BridgeState, allow_origin):
        if method != "POST":
            return self._unknown(allow_origin)
        jobs = state.jobs
        if action == "cancel":
            job_status = jobs.cancel(job_id)
            return (
                self._send_json(
                    200, {"ok": True, "job_id": job_id, "status": job_status}, allow_origin
                ),
                None,
                job_id,
            )
        if action == "events":
            events = body.get("events")
            if not isinstance(events, list):
                raise BridgeError("E_INTERNAL", "events", "events must be a list", 400)
            payload = jobs.append_events(job_id, events, body)
            return self._send_json(200, payload, allow_origin), None, job_id
        if action == "result":
            jobs.resolve(job_id, body)
            return self._send_json(200, {"ok": True}, allow_origin), None, job_id
        return self._unknown(allow_origin)

    def _parse_wait(self, query: dict) -> float:
        raw = (query.get("wait") or [str(config.NEXT_WAIT_DEFAULT_S)])[0]
        try:
            wait = float(raw)
        except (TypeError, ValueError):
            raise BridgeError("E_INTERNAL", "next", "wait must be numeric", 400)
        return max(0.0, min(wait, float(config.NEXT_WAIT_MAX_S)))

    @staticmethod
    def _parse_client(query: dict) -> str:
        kind = (query.get("client") or [DEFAULT_CLIENT_KIND])[0]
        if kind not in CLIENT_KINDS:
            raise BridgeError(
                "E_INTERNAL",
                "next",
                f"client must be one of {', '.join(CLIENT_KINDS)}",
                400,
            )
        return kind

    @staticmethod
    def _parse_since(query: dict) -> int:
        raw = (query.get("since") or ["0"])[0]
        try:
            since = int(raw)
        except (TypeError, ValueError):
            raise BridgeError("E_INTERNAL", "jobs", "since must be an integer", 400)
        return max(since, 0)

    def _read_json_body(self, method: str, *, is_result=False):
        if method != "POST":
            return {}, None
        length_header = self.headers.get("Content-Length")
        try:
            length = int(length_header) if length_header else 0
        except ValueError:
            return {}, (400, "E_INTERNAL")
        limit = config.RESULT_MAX_BYTES if is_result else config.BODY_MAX_BYTES
        if length < 0 or length > limit:
            self.close_connection = True
            code = "E_RESULT_TOO_LARGE" if is_result and length > limit else "E_INTERNAL"
            return {}, (413 if length > limit else 400, code)
        if length == 0:
            return {}, None
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}, (400, "E_INTERNAL")
        if not isinstance(payload, dict):
            return {}, (400, "E_INTERNAL")
        return payload, None

    def _send_auth_failure(self, result: auth.AuthResult) -> tuple[int, str]:
        code = result.code or "E_INTERNAL"
        self._safe_send_error(result.status, code, "auth", result.message)
        return result.status, code

    def _send_json(self, status: int, payload: dict, allow_origin=None) -> int:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if allow_origin:
                self.send_header("Access-Control-Allow-Origin", allow_origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            if self.command not in ("HEAD",) and data:
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        return status

    def _send_empty(self, status: int, allow_origin=None) -> int:
        try:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            if allow_origin:
                self.send_header("Access-Control-Allow-Origin", allow_origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        return status

    def _send_error(self, status, code, step, message, allow_origin=None) -> int:
        return self._send_json(
            status,
            {"ok": False, "error": {"code": code, "step": step, "message": message}},
            allow_origin,
        )

    def _safe_send_error(self, status, code, step, message, allow_origin=None) -> int:
        try:
            return self._send_error(status, code, step, message, allow_origin)
        except Exception:
            return status

    def _record_log(self, method, path, status, job_id, error_code) -> None:
        entry = {
            "ts": utc_now_iso(),
            "method": method,
            "path": path,
            "status": status,
            "job_id": job_id,
            "error_code": error_code,
        }
        try:
            self.server.state.store.append_log(entry)
        except Exception:
            LOGGER.debug("bridge: log append failed", exc_info=True)


def build_state(store: Store | None = None) -> BridgeState:
    store = store or Store()
    token = auth.load_or_create_token(store.token_path)
    manager = JobManager(store)
    results = JournalResults(manager)
    return BridgeState(store=store, jobs=manager, token=token, results=results)


def create_server(host: str, port: int, state: BridgeState) -> BridgeHTTPServer:
    if host != config.DEFAULT_BRIDGE_HOST:
        raise ValueError("the bridge binds 127.0.0.1 only")
    return BridgeHTTPServer((host, port), BridgeRequestHandler, state)


def _watchdog_loop(manager: JobManager, stop: threading.Event) -> None:
    while not stop.wait(1.0):
        try:
            manager.watchdog()
        except Exception:
            LOGGER.error("bridge: watchdog tick failed")


def serve(
    host: str = config.DEFAULT_BRIDGE_HOST,
    port: int = config.DEFAULT_BRIDGE_PORT,
    log_level: str = "info",
    state_dir: Path | str | None = None,
) -> int:
    level = getattr(logging, str(log_level).upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    state = build_state(Store(state_dir))
    try:
        server = create_server(host, port, state)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_BRIDGE
    config_data = state.store.load_config()
    config_data["port"] = state.port
    state.store.save_config(config_data)
    stop = threading.Event()
    watchdog = threading.Thread(
        target=_watchdog_loop, args=(state.jobs, stop), name="bridge-watchdog", daemon=True
    )
    watchdog.start()

    def request_shutdown(signum, frame) -> None:  # noqa: ARG001
        threading.Thread(target=server.shutdown, daemon=True).start()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, request_shutdown)
        except (ValueError, OSError):
            pass
    LOGGER.info("bridge: listening on http://%s:%s", host, state.port)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        stop.set()
        server.server_close()
    return 0
