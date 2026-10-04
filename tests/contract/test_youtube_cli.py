"""Contract evidence for the thin JSONL YouTube operator CLI."""

from __future__ import annotations

from collections.abc import Mapping
import importlib
import http.client
import json

import pytest

from tests.support.kronika_identity import expected

from kronika.adapters.cli import youtube
from kronika.configuration import KronikaSettings

CLAIM_ID = "11111111-1111-4111-8111-111111111111"
MEDIA_ID = "22222222-2222-4222-8222-222222222222"
LOCATION_ID = "33333333-3333-4333-8333-333333333333"
VIDEO_ID = "AbCdEf123_-"


def _snapshot(
    state: str,
    *,
    upload_state: str | None = None,
) -> dict[str, object]:
    terminal = state in {"cataloged", "duplicate_resolved", "failed"}
    return {
        "id": CLAIM_ID,
        "state": state,
        "acquisition_source": "youtube_manual_claim",
        "youtube_video_id": VIDEO_ID,
        "upload_id": CLAIM_ID if upload_state is not None else None,
        "upload_state": upload_state,
        "media_id": MEDIA_ID if state in {"cataloged", "duplicate_resolved"} else None,
        "media_location_id": (
            LOCATION_ID if state in {"cataloged", "duplicate_resolved"} else None
        ),
        "result": (
            "new"
            if state == "cataloged"
            else "reused"
            if state == "duplicate_resolved"
            else None
        ),
        "downloaded_size_bytes": 123 if upload_state is not None else None,
        "failure_stage": "download" if state == "failed" else None,
        "failure_code": "DOWNLOAD_FAILED" if state == "failed" else None,
        "cleanup_state": "complete" if terminal else "pending",
        "retry_of_claim_id": None,
        "resolved_claim_id": None,
        "created_at_ms": 10,
        "updated_at_ms": 11,
        "completed_at_ms": 12 if terminal else None,
        "version": 1,
    }


class _Client:
    def __init__(
        self,
        *,
        initial: dict[str, object] | None = None,
        statuses: list[dict[str, object]] | None = None,
    ) -> None:
        self.initial = initial or _snapshot("claimed")
        self.statuses = list(statuses or [])
        self.posts: list[tuple[str, Mapping[str, object]]] = []
        self.gets: list[str] = []

    def post(self, path: str, payload: Mapping[str, object]) -> dict[str, object]:
        self.posts.append((path, payload))
        return self.initial

    def get(self, path: str) -> dict[str, object]:
        self.gets.append(path)
        if self.statuses:
            return self.statuses.pop(0)
        return self.initial


def _settings(host: str = "127.0.0.1") -> KronikaSettings:
    return KronikaSettings(host=host, port=8000, _env_file=None)


def _run(
    argv: list[str],
    client: _Client,
    *,
    input_text: str = "yes\n",
    clocks: list[float] | None = None,
    sleep=lambda _seconds: None,
) -> int:
    clock_values = iter(clocks or [0.0] * 100)
    return youtube._run(
        argv,
        settings_loader=_settings,
        client_factory=lambda _settings: client,
        input_reader=lambda: input_text,
        sleep=sleep,
        clock=lambda: next(clock_values),
    )


def test_ingest_confirms_posts_original_url_and_emits_sanitized_jsonl(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _Client(
        statuses=[
            _snapshot("downloading"),
            _snapshot("cataloged", upload_state="cataloged"),
        ]
    )

    result = _run(
        [
            "ingest",
            f"https://youtu.be/{VIDEO_ID}",
            "--yes",
            "--wait-timeout",
            "10",
        ],
        client,
    )

    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines()]
    assert result == 0
    assert client.posts == [
        (
            "/api/operator/youtube/claims",
            {
                "url": f"https://youtu.be/{VIDEO_ID}",
                "confirmation_method": "yes_flag",
            },
        )
    ]
    assert [line["event"] for line in lines] == [
        "confirmation",
        "status",
        "status",
        "status",
        "success",
    ]
    assert lines[1]["claim_id"] == CLAIM_ID
    assert lines[-1]["media_id"] == MEDIA_ID
    assert lines[-1]["media_location_id"] == LOCATION_ID
    assert captured.err == ""
    assert "Synthetic title" not in captured.out


def test_interactive_decline_creates_no_claim(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _Client()

    with pytest.raises(youtube._DeclinedError):
        _run(
            ["ingest", f"https://youtu.be/{VIDEO_ID}"],
            client,
            input_text="\n",
        )

    captured = capsys.readouterr()
    assert client.posts == []
    assert "Proceed? [y/N]" in captured.err


def test_wait_timeout_and_interrupt_leave_server_work_running(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeout_client = _Client(initial=_snapshot("claimed"))
    monkeypatch.setattr(youtube, "load_settings", _settings)
    monkeypatch.setattr(
        youtube,
        "_LoopbackHttpClient",
        lambda _settings: timeout_client,
    )
    times = iter([0.0, 2.0])
    monkeypatch.setattr(youtube.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(youtube.time, "sleep", lambda _seconds: None)
    timeout_exit = youtube.main(
        [
            "ingest",
            f"https://youtu.be/{VIDEO_ID}",
            "--yes",
            "--wait-timeout",
            "1",
        ]
    )
    timeout_output = capsys.readouterr()

    interrupt_client = _Client(initial=_snapshot("claimed"))
    monkeypatch.setattr(
        youtube,
        "_LoopbackHttpClient",
        lambda _settings: interrupt_client,
    )
    monkeypatch.setattr(youtube.time, "monotonic", lambda: 0.0)

    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr(youtube.time, "sleep", interrupt)
    interrupt_exit = youtube.main(
        ["ingest", f"https://youtu.be/{VIDEO_ID}", "--yes"]
    )
    interrupt_output = capsys.readouterr()

    assert timeout_exit == 124
    timeout_error = json.loads(timeout_output.err)
    assert timeout_error["error_code"] == "YOUTUBE_WAIT_TIMEOUT"
    assert timeout_error["claim_id"] == CLAIM_ID
    assert interrupt_exit == 130
    interrupt_error = json.loads(interrupt_output.err)
    assert interrupt_error["error_code"] == "YOUTUBE_CLIENT_INTERRUPTED"
    assert all("/cancel" not in path for path in timeout_client.gets)
    assert all("/cancel" not in path for path in interrupt_client.gets)


def test_exit_code_matrix_for_invalid_not_configured_failure_and_status(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = youtube.main(["ingest", "https://evil.example/video", "--yes"])
    capsys.readouterr()

    # Settings validation now rejects non-loopback tcp hosts fail-closed;
    # simulate unvalidated legacy state to exercise the CLI boundary check.
    unvalidated = _settings().model_copy(update={"host": "192.0.2.1"})
    monkeypatch.setattr(youtube, "load_settings", lambda: unvalidated)
    not_configured = youtube.main(
        ["ingest", f"https://youtu.be/{VIDEO_ID}", "--yes"]
    )
    capsys.readouterr()

    failed_client = _Client(initial=_snapshot("failed"))
    monkeypatch.setattr(youtube, "load_settings", _settings)
    monkeypatch.setattr(
        youtube,
        "_LoopbackHttpClient",
        lambda _settings: failed_client,
    )
    terminal_failure = youtube.main(
        ["ingest", f"https://youtu.be/{VIDEO_ID}", "--yes"]
    )
    failure_output = capsys.readouterr()

    status_client = _Client(initial=_snapshot("failed"))
    monkeypatch.setattr(
        youtube,
        "_LoopbackHttpClient",
        lambda _settings: status_client,
    )
    status = youtube.main(["status", CLAIM_ID])
    capsys.readouterr()

    assert invalid == 2
    assert not_configured == 3
    assert terminal_failure == 4
    assert len(failure_output.err.splitlines()) == 1
    assert "DOWNLOAD_FAILED" not in failure_output.err
    assert status == 0


def test_retry_uses_confirmation_and_no_arbitrary_base_url_option(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _Client(initial=_snapshot("duplicate_resolved"))

    result = _run(["retry", CLAIM_ID, "--yes"], client)

    assert result == 0
    assert client.posts == [
        (
            f"/api/operator/youtube/claims/{CLAIM_ID}/retry",
            {"confirmation_method": "yes_flag"},
        )
    ]
    with pytest.raises(youtube._UsageError):
        _run(
            ["status", CLAIM_ID, "--base-url", "http://192.0.2.1"],
            _Client(),
        )
    capsys.readouterr()


def test_import_has_no_network_or_operator_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    def forbidden_connection(*args: object, **kwargs: object) -> None:
        calls.append((*args, kwargs))
        raise AssertionError("CLI import must not construct an HTTP client")

    monkeypatch.setattr(http.client, "HTTPConnection", forbidden_connection)

    importlib.reload(youtube)

    assert calls == []


def test_protocol_failure_reports_the_derived_brand_on_the_loopback_refusal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The `_ProtocolError` branch of the loopback refusal.

    Two branches write the same sentence and only one is a declared protocol
    failure; they are asserted separately so a change to either is caught on its
    own.
    """
    monkeypatch.setattr(youtube, "load_settings", _settings)

    class _ProtocolFailingClient:
        def __init__(self, _settings: KronikaSettings) -> None:
            raise youtube._ProtocolError()

    monkeypatch.setattr(youtube, "_LoopbackHttpClient", _ProtocolFailingClient)

    result = youtube.main(["status", CLAIM_ID])
    payload = json.loads(capsys.readouterr().err)

    assert result == 5
    assert payload["error_code"] == "YOUTUBE_LOOPBACK_UNAVAILABLE"
    assert payload["message"] == expected(
        "The loopback {brand} operator API is unavailable."
    )


def test_unexpected_failure_reports_the_derived_brand_on_the_loopback_refusal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The bare `Exception` branch of the same refusal."""
    monkeypatch.setattr(youtube, "load_settings", _settings)

    class _BrokenClient:
        def __init__(self, _settings: KronikaSettings) -> None:
            raise RuntimeError("an unexpected failure the CLI must still sanitize")

    monkeypatch.setattr(youtube, "_LoopbackHttpClient", _BrokenClient)

    result = youtube.main(["status", CLAIM_ID])
    output = capsys.readouterr()
    payload = json.loads(output.err)

    assert result == 5
    assert payload["error_code"] == "YOUTUBE_LOOPBACK_UNAVAILABLE"
    assert payload["message"] == expected(
        "The loopback {brand} operator API is unavailable."
    )
    assert "an unexpected failure" not in output.err
    assert "Traceback" not in output.err
