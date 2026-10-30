"""Exercise the real Bash helper in private synthetic roots, without privilege.

The missing evidence is safe recovery of both bytes and absence, including
refusal before writes when state, paths or metadata are untrustworthy.
Pytest owns tmp_path retention and cleanup; no fixture reads or writes /etc.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
import stat
import subprocess

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPOSITORY_ROOT / "scripts/operator/security/kronika_nuc_boundary_rollback.sh"
SURFACES = {
    "ufw": ("etc/ufw/user.rules", "etc/ufw/user6.rules"),
    "sshd": ("etc/ssh/sshd_config.d/00-kronika-hardening.conf",
             "etc/ssh/sshd_config.d/50-cloud-init.conf"),
}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    # Exact disposable identity: tmp_path/root, synthetic configuration only.
    path = tmp_path / "root"
    path.mkdir(mode=0o700)
    for relative in ("var/lib", "etc/ufw", "etc/ssh/sshd_config.d"):
        directory = path / relative
        directory.mkdir(parents=True)
        for parent in (directory, *directory.parents):
            if parent == path:
                break
            parent.chmod(0o755)
    return path


def run(root: Path, operation: str = "snapshot", surface: str = "ufw",
        *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(HELPER), operation, surface, *extra],
        env={"PATH": "/usr/bin:/bin", "KRONIKA_NSH_TEST_ROOT": str(root)},
        capture_output=True, text=True, check=False, timeout=10,
    )


def good(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert len(result.stdout.splitlines()) == 1


def refused(result: subprocess.CompletedProcess[str], cause: str) -> None:
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == f"nsh-boundary-rollback: {cause}\n"


def populate(root: Path, surface: str = "ufw") -> dict[str, tuple[bytes, int, int, int]]:
    expected = {}
    for index, relative in enumerate(SURFACES[surface]):
        path = root / relative
        data = b"synthetic\x00rules\r\n" + bytes([index, 255])
        path.write_bytes(data)
        path.chmod((0o640, 0o600)[index])
        expected[relative] = (data, stat.S_IMODE(path.stat().st_mode),
                              path.stat().st_uid, path.stat().st_gid)
    return expected


def state(root: Path) -> Path:
    return root / "var/lib/kronika-host-hardening"


def live_image(root: Path, surface: str = "ufw") -> dict[str, bytes | None]:
    return {rel: (root / rel).read_bytes() if (root / rel).exists() else None
            for rel in SURFACES[surface]}


@pytest.mark.parametrize("surface", SURFACES)
def test_exact_roundtrip_and_private_snapshot(root: Path, surface: str) -> None:
    expected = populate(root, surface)
    good(run(root, surface=surface))
    snapshot = state(root) / surface
    for path in (state(root), *state(root).rglob("*")):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    manifest = (snapshot / "manifest").read_text()
    for rel, (data, mode, uid, gid) in expected.items():
        assert (snapshot / "files" / rel).read_bytes() == data
        assert f"{rel}|present|{mode:o}|{uid}|{gid}|{hashlib.sha256(data).hexdigest()}\n" in manifest
        (root / rel).write_bytes(b"changed")
        (root / rel).chmod(0o644)
    before = live_image(root, surface)
    good(run(root, "verify", surface))
    assert live_image(root, surface) == before  # verify never compares/writes live files
    good(run(root, "restore", surface))
    for rel, expected_values in expected.items():
        path = root / rel
        assert (path.read_bytes(), stat.S_IMODE(path.stat().st_mode),
                path.stat().st_uid, path.stat().st_gid) == expected_values
    good(run(root, "restore", surface))  # retry is safe after a complete restore


@pytest.mark.parametrize("present_count", [0, 1])
def test_absent_ssh_dropins_are_recorded_and_restored(root: Path, present_count: int) -> None:
    if present_count:
        populate(root, "sshd")
        (root / SURFACES["sshd"][0]).unlink()
    before = live_image(root, "sshd")
    good(run(root, surface="sshd"))
    for rel in SURFACES["sshd"]:
        (root / rel).write_bytes(b"new cut")
        (root / rel).chmod(0o600)
    good(run(root, "restore", "sshd"))
    assert live_image(root, "sshd") == before


def test_restore_recreates_a_missing_recorded_file(root: Path) -> None:
    populate(root)
    before = live_image(root)
    good(run(root))
    (root / SURFACES["ufw"][0]).unlink()
    good(run(root, "restore"))
    assert live_image(root) == before


@pytest.mark.parametrize("missing", SURFACES["ufw"])
def test_missing_required_ufw_input_refuses_snapshot(root: Path, missing: str) -> None:
    populate(root)
    (root / missing).unlink()
    before = live_image(root)
    refused(run(root), "missing surface file")
    assert not (state(root) / "ufw").exists()
    assert live_image(root) == before


@pytest.mark.parametrize("operation,surface,extra", [
    ("invalid", "ufw", ()), ("snapshot", "../sshd", ()),
    ("restore", "/etc/ufw/user.rules", ()), ("snapshot", "ufw", ("/etc/passwd",)),
])
def test_interface_never_accepts_arbitrary_paths(root: Path, operation: str,
                                               surface: str, extra: tuple[str, ...]) -> None:
    result = run(root, operation, surface, *extra)
    assert result.returncode != 0
    assert len(result.stderr.splitlines()) == 1
    assert not state(root).exists()


@pytest.mark.parametrize("unsafe", ["missing", "relative", "dotdot", "symlink", "mode", "empty"])
def test_unsafe_test_root_is_rejected(root: Path, unsafe: str) -> None:
    candidate = root
    if unsafe == "missing":
        candidate = root / "missing"
    elif unsafe == "relative":
        candidate = Path("relative")
    elif unsafe == "dotdot":
        candidate = root / ".." / "root"
    elif unsafe == "symlink":
        candidate = root.parent / "link"
        candidate.symlink_to(root, target_is_directory=True)
    elif unsafe == "mode":
        root.chmod(0o755)
    result = run(candidate) if unsafe != "empty" else run(Path(""))
    refused(result, "unsafe test root")
    assert not state(root).exists()


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo", "hardlink", "mode", "parent"])
def test_unsafe_live_surface_fails_before_snapshot(root: Path, kind: str) -> None:
    populate(root)
    path = root / SURFACES["ufw"][1]
    if kind in {"symlink", "directory", "fifo"}:
        path.unlink()
        if kind == "symlink":
            path.symlink_to(root / SURFACES["ufw"][0])
        elif kind == "directory":
            path.mkdir()
        else:
            os.mkfifo(path)
    elif kind == "hardlink":
        os.link(path, root / "unrelated-link")
    elif kind == "mode":
        path.chmod(0o666)
    else:
        path.parent.chmod(0o777)
    result = run(root)
    assert result.returncode != 0
    assert len(result.stderr.splitlines()) == 1
    assert not (state(root) / "ufw").exists()


def test_symlinked_surface_directory_is_rejected(root: Path) -> None:
    directory = root / "etc/ufw"
    directory.rmdir()
    directory.symlink_to(root / "etc/ssh", target_is_directory=True)
    refused(run(root), "unsafe or missing directory")


def test_unexpected_owner_record_fails_without_root(root: Path) -> None:
    populate(root)
    good(run(root))
    manifest = state(root) / "ufw/manifest"
    text = manifest.read_text()
    manifest.write_text(text.replace(f"|{os.getuid()}|{os.getgid()}|",
                                     f"|{os.getuid() + 1}|{os.getgid()}|"))
    before = live_image(root)
    refused(run(root, "restore"), "invalid snapshot metadata")
    assert live_image(root) == before


def test_unexpected_live_owner_with_synthetic_stat_metadata(root: Path) -> None:
    # This branch uses a stat fixture, not real foreign ownership or a host
    # namespace. Other tests execute real stat and real filesystem refusals.
    populate(root)
    command = '''stat() {
        if [[ $1 == -c && $2 == '%u %g %a %h' ]]; then
            printf '4294967294 4294967294 600 1\\n'
        else command stat "$@"; fi
    }
    export -f stat
    exec /bin/bash "$1" snapshot ufw'''
    result = subprocess.run(
        ["/bin/bash", "-c", command, "test", str(HELPER)],
        env={"PATH": "/usr/bin:/bin", "KRONIKA_NSH_TEST_ROOT": str(root)},
        capture_output=True, text=True, timeout=10, check=False,
    )
    refused(result, "unexpected surface owner")


@pytest.mark.parametrize("kind", ["checksum", "metadata", "extra", "path", "truncated",
                                 "nul", "missing-newline", "symlink", "parent-symlink"])
def test_corrupt_snapshot_refuses_restore_without_live_mutation(root: Path, kind: str) -> None:
    populate(root)
    good(run(root))
    snapshot = state(root) / "ufw"
    copied = snapshot / "files" / SURFACES["ufw"][0]
    manifest = snapshot / "manifest"
    if kind == "checksum":
        copied.write_bytes(b"corrupt")
    elif kind == "metadata":
        copied.chmod(0o644)
    elif kind == "extra":
        (snapshot / "unexpected").write_bytes(b"extra")
    elif kind == "path":
        manifest.write_text(manifest.read_text().replace(SURFACES["ufw"][0], "etc/../passwd"))
    elif kind == "truncated":
        manifest.write_text("NSH1|ufw\n")
    elif kind == "nul":
        manifest.write_bytes(manifest.read_bytes().replace(b"present", b"pre\x00sent", 1))
    elif kind == "missing-newline":
        manifest.write_bytes(manifest.read_bytes().removesuffix(b"\n"))
    elif kind == "symlink":
        copied.unlink()
        copied.symlink_to(root / SURFACES["ufw"][0])
    else:
        # A dangling directory link must fail before digest tries to follow it.
        directory = snapshot / "files/etc/ufw"
        for rel in SURFACES["ufw"]:
            (snapshot / "files" / rel).unlink()
        directory.rmdir()
        directory.symlink_to(root / "missing", target_is_directory=True)
    before = live_image(root)
    result = run(root, "restore")
    assert result.returncode != 0
    assert len(result.stderr.splitlines()) == 1
    assert live_image(root) == before


def test_verify_needs_no_live_surface(root: Path) -> None:
    populate(root)
    good(run(root))
    for rel in SURFACES["ufw"]:
        (root / rel).unlink()
    good(run(root, "verify"))
    assert live_image(root) == dict.fromkeys(SURFACES["ufw"])


@pytest.mark.parametrize("operation", ["verify", "restore"])
def test_missing_state_is_refused_without_creation(root: Path, operation: str) -> None:
    refused(run(root, operation), "missing recovery state")
    assert not state(root).exists()


def test_existing_snapshot_is_never_replaced(root: Path) -> None:
    populate(root)
    good(run(root))
    manifest = state(root) / "ufw/manifest"
    before = manifest.read_bytes()
    refused(run(root), "snapshot already exists; stale or conflicting state")
    assert manifest.read_bytes() == before


@pytest.mark.parametrize("entry", [".pending-ufw", "unrelated"])
def test_abandoned_or_unknown_state_is_a_conflict(root: Path, entry: str) -> None:
    populate(root)
    good(run(root))
    (state(root) / entry).mkdir(mode=0o700)
    refused(run(root, "verify"), "incomplete snapshot conflict" if entry.startswith(".")
            else "unexpected recovery state entry")


@pytest.mark.parametrize("kind", ["state-mode", "state-symlink", "lock-symlink", "lock-mode"])
def test_unsafe_state_and_lock_are_refused(root: Path, kind: str) -> None:
    populate(root)
    good(run(root))
    if kind == "state-mode":
        state(root).chmod(0o755)
    elif kind == "state-symlink":
        original = state(root).with_name("saved-state")
        state(root).rename(original)
        state(root).symlink_to(original, target_is_directory=True)
    elif kind == "lock-symlink":
        (state(root) / "lock").unlink()
        (state(root) / "lock").symlink_to(root / SURFACES["ufw"][0])
    else:
        (state(root) / "lock").chmod(0o644)
    result = run(root, "verify")
    assert result.returncode != 0
    assert len(result.stderr.splitlines()) == 1


@pytest.mark.parametrize("operation", ["snapshot", "verify", "restore"])
def test_held_lock_is_an_immediate_conflict(root: Path, operation: str) -> None:
    populate(root)
    good(run(root))
    before = live_image(root)
    with (state(root) / "lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        refused(run(root, operation), "operation lock conflict")
    assert live_image(root) == before


@pytest.mark.parametrize("kind", ["symlink", "mode", "staging"])
def test_restore_preflights_both_targets_before_any_write(root: Path, kind: str) -> None:
    populate(root)
    good(run(root))
    first, second = (root / rel for rel in SURFACES["ufw"])
    first.write_bytes(b"first must remain changed on refusal")
    if kind == "symlink":
        second.unlink()
        second.symlink_to(first)
    elif kind == "mode":
        second.chmod(0o666)
    else:
        second.with_name(f".nsh-boundary-rollback-{second.name}.next").write_bytes(b"stale")
    before = first.read_bytes()
    assert run(root, "restore").returncode != 0
    assert first.read_bytes() == before


def test_first_failure_is_not_overwritten_by_a_later_failure(root: Path) -> None:
    populate(root)
    good(run(root))
    # A broken snapshot and a broken destination coexist: verification must win,
    # with one causal message and no cleanup that can replace that outcome.
    (state(root) / "ufw/files" / SURFACES["ufw"][0]).write_bytes(b"corrupt")
    (root / SURFACES["ufw"][1]).chmod(0o666)
    refused(run(root, "restore"), "snapshot checksum mismatch")


@pytest.mark.parametrize("operation", ["snapshot", "restore"])
def test_command_failure_preserves_one_causal_message(root: Path, operation: str) -> None:
    populate(root)
    if operation == "restore":
        good(run(root))
    before = live_image(root)
    # Test-only fault injection at the copy command, without full disks or
    # changing real host files. The production implementation is still executed.
    result = subprocess.run(
        ["/bin/bash", "-c", 'cat() { return 73; }; export -f cat; exec /bin/bash "$1" "$2" ufw',
         "test", str(HELPER), operation],
        env={"PATH": "/usr/bin:/bin", "KRONIKA_NSH_TEST_ROOT": str(root)},
        capture_output=True, text=True, timeout=10, check=False,
    )
    refused(result, "snapshot creation failed" if operation == "snapshot" else "restore staging failed")
    assert live_image(root) == before
