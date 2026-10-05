"""Static contract tests for Cursor Worker execution-boundary convergence.

These tests lock document order and classification owners. They do not execute
Python through the ambient interpreter.
"""

from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
AGENTS_PATH = REPOSITORY_ROOT / "AGENTS.md"
CONTRACT_PATH = REPOSITORY_ROOT / "docs" / "WORKER_EXECUTION_CONTRACT.md"
LEDGER_PATH = REPOSITORY_ROOT / "docs" / "AP_UPGRADE_OBSERVATIONS.md"
GATE_SCRIPT = (
    REPOSITORY_ROOT / "scripts" / "operator" / "network" / "framenest_nuc_worker_gate.fish"
)
CANONICAL_GATE_SCRIPT = (
    REPOSITORY_ROOT / "scripts" / "operator" / "network" / "kronika_nuc_worker_gate.fish"
)

AUTHORIZED_BASELINE = "5abb2adfcd1d5f3391df9c3044b4b81ac1aac923"
LEDGER_ENTRY = "consumer-declared-execution-and-capability-route-binding"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_agents_declares_untrusted_cursor_routes() -> None:
    text = _text(AGENTS_PATH)
    boundary = text.split("## Cursor Worker Execution Boundary", 1)[1]
    boundary = boundary.split("\n## ", 1)[0]
    collapsed = " ".join(boundary.split())
    assert "untrusted" in collapsed
    assert "./.ap/ap exec" in collapsed
    assert "kronika_nuc_worker_gate.fish" in collapsed
    assert "framenest_nuc_worker_gate.fish" in collapsed
    assert "KRONIKA_NUC_SSH_*" in collapsed
    assert "sudo -K" in collapsed
    assert "sudo -v" in collapsed
    assert "expected lifecycle state" in collapsed
    assert ".venv/bin/python" in collapsed
    assert "poetry run" in collapsed
    assert "Do not duplicate universal AP protocol here." in collapsed


def test_ap_exec_precedes_raw_human_shell_python_routes() -> None:
    text = _text(CONTRACT_PATH)
    ap_route = text.index("## Canonical Cursor Worker Python Route")
    human_shell = text.index("## Clean Human Development Shell Only")
    poetry = text.index("poetry run pytest")
    raw_venv = text.index("<physical-repository-root>/.venv/bin/python -m pytest")
    assert ap_route < human_shell
    assert human_shell < poetry
    assert human_shell < raw_venv
    assert "./.ap/ap exec" in text[ap_route:human_shell]
    assert "never be rendered into Cursor Worker prompts" in text[human_shell:]


def test_contract_owns_encodings_ssh_and_sudo_classification() -> None:
    text = _text(CONTRACT_PATH)
    assert "Failed to import encodings" in text
    assert "No module named 'encodings'" in text
    assert "ambient-route violation" in text
    assert "Do not inventory Pythons" in text
    assert "kronika_nuc_worker_gate.fish --probe" in text
    assert "framenest_nuc_worker_gate.fish" in text
    assert "KRONIKA_NUC_SSH_TARGET" in text
    assert "ssh-agent: ready" in text
    assert "expected lifecycle state" in text
    assert "timestamp_timeout=1440" in text
    assert "Workers must not run `sudo -v`" in text
    assert GATE_SCRIPT.is_file()
    assert CANONICAL_GATE_SCRIPT.is_file()


def test_ledger_records_accepted_route_binding_observation() -> None:
    text = _text(LEDGER_PATH)
    assert text.startswith(
        "Ledger storage version: 1\n"
        "Upgrade ledger: upgrade https://github.com/cisarik/ap.git\n"
        "Activation snapshot: zero candidate observations at "
        "17b7e085139e9bcbb0e4953d26aef9b6687d541c\n"
    )
    assert f"Entry: {LEDGER_ENTRY}\n" in text
    assert "Entry state: accepted\n" in text
    assert "Entry authority: non-authorizing\n" in text
    assert "Evidence class: worker-observed\n" in text
    assert f"Observed against: {AUTHORIZED_BASELINE}\n" in text
    assert "Last revalidated against: 7ef45da756ed3cc14808e89bf25d0a9f9aba5d26\n" in text
    assert "Implementation task grant: none\n" in text
    assert "Implementation status: not-started\n" in text
    assert "Disposition evidence: 7ef45da756ed3cc14808e89bf25d0a9f9aba5d26 (" in text
    assert "Promotion target: none\n" in text
    assert "Closure action: retain-active\n" in text
    assert "Historical evidence: none\n" in text
    assert "Provenance destroyed: no\n" in text
    assert text.count("Entry:") == 1
    assert "darwin-bsd-awk-project-argv-counting" not in text
