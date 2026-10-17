"""Source and public-gate contract tests for the NUC release-update engine.

These tests exercise the local-only layers: release identity validation, archive
member validation and extraction, manifest/marker construction, hashing, and the
git source/public gates. They use a fake command runner, synthetic archives, and
temporary directories only. They never contact a real host or inspect secrets.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import tarfile

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ENGINE_PATH = REPOSITORY_ROOT / "deploy" / "ubuntu" / "kronika_release.py"

_SPEC = importlib.util.spec_from_file_location("kronika_release", ENGINE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
engine = importlib.util.module_from_spec(_SPEC)
import sys
sys.modules["kronika_release"] = engine
_SPEC.loader.exec_module(engine)

RELEASE = "a" * 40
AP_PIN = "b" * 40


class FakeGitRunner:
    def __init__(self, **overrides: str) -> None:
        self.calls: list[list[str]] = []
        self.responses = {
            "rev-parse --show-toplevel": "/repo",
            "rev-parse HEAD": RELEASE,
            "status --porcelain": "",
            "ls-remote origin refs/heads/main": f"{RELEASE}\trefs/heads/main",
            f"ls-tree {RELEASE} .ap": f"160000 commit {AP_PIN}\t.ap",
            "rev-parse HEAD": RELEASE,
        }
        self.responses.update(overrides)

    def __call__(self, argv: list[str], input_bytes: bytes | None) -> str:
        self.calls.append(list(argv))
        command = " ".join(argv)
        for key, value in self.responses.items():
            if key in command:
                return value
        raise AssertionError(f"unexpected git command: {command}")


def _safe_tar(path: Path, *, member: str = "pyproject.toml", content: bytes = b"x") -> None:
    with tarfile.open(path, "w") as archive:
        info = tarfile.TarInfo(name=member)
        info.size = len(content)
        import io
        archive.addfile(info, io.BytesIO(content))


def test_release_sha_validation() -> None:
    engine.validate_release_sha("a" * 40)

    for bad in ("A" * 40, "abc123", "g" * 40, "", "a" * 39):
        with pytest.raises(engine.ReleaseError) as exc:
            engine.validate_release_sha(bad)
        assert exc.value.exit_code == engine.EXIT_USAGE


def test_archive_member_validation_rejects_unsafe_members() -> None:
    unsafe = [
        ("/etc/passwd", None, False),
        ("../escape", None, False),
        ("a/../../b", None, False),
        ("dev", None, True),
        ("link", "/etc/passwd", False),
        ("link", "../outside", False),
    ]
    for name, linkname, isdev in unsafe:
        with pytest.raises(engine.ReleaseError) as exc:
            engine.validate_archive_member(name, linkname=linkname, isdev=isdev)
        assert exc.value.exit_code == engine.EXIT_UNSAFE_ARCHIVE


def test_archive_member_validation_accepts_safe_members() -> None:
    engine.validate_archive_member("pyproject.toml", linkname=None, isdev=False)
    engine.validate_archive_member("a/b/c.py", linkname=None, isdev=False)
    engine.validate_archive_member("link", linkname="target.py", isdev=False)


def test_extract_validated_archive_materializes_safe_members(tmp_path: Path) -> None:
    archive = tmp_path / "safe.tar"
    _safe_tar(archive, member="pyproject.toml", content=b"hello")
    destination = tmp_path / "release"
    destination.mkdir()

    engine.extract_validated_archive(archive, str(destination))

    assert (destination / "pyproject.toml").read_bytes() == b"hello"


def test_extract_validated_archive_rejects_absolute_member(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as handle:
        info = tarfile.TarInfo(name="/etc/passwd")
        info.size = 3
        import io
        handle.addfile(info, io.BytesIO(b"bad"))

    with pytest.raises(engine.ReleaseError) as exc:
        engine.extract_validated_archive(archive, str(tmp_path / "dest"))
    assert exc.value.exit_code == engine.EXIT_UNSAFE_ARCHIVE


def test_make_manifest_contains_only_public_provenance() -> None:
    manifest = engine.make_manifest(
        release_sha=RELEASE,
        ap_pin=AP_PIN,
        superproject_sha256="c" * 64,
        ap_archive_sha256="d" * 64,
        capture_code_tree="e" * 40,
        capture_runtime_contract_sha256="1" * 64,
        capture_unit_contract_sha256="2" * 64,
        capture_bridge_protocol="1",
    )

    assert set(manifest) == {
        "kronika_release_sha",
        "ap_gitlink",
        "superproject_archive_sha256",
        "ap_archive_sha256",
        "capture_code_tree",
        "capture_runtime_contract_sha256",
        "capture_unit_contract_sha256",
        "capture_bridge_protocol",
    }
    assert manifest["kronika_release_sha"] == RELEASE
    assert manifest["ap_gitlink"] == AP_PIN


def test_sha256_of_file_matches_known_content(tmp_path: Path) -> None:
    path = tmp_path / "data"
    path.write_bytes(b"abc")

    assert (
        engine.sha256_of_file(path)
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_verify_local_head_rejects_mismatch() -> None:
    runner = FakeGitRunner(**{"rev-parse HEAD": "f" * 40})
    with pytest.raises(engine.ReleaseError) as exc:
        engine.verify_local_head(runner, RELEASE)
    assert exc.value.exit_code == engine.EXIT_SOURCE_GATE


def test_verify_clean_worktrees_rejects_dirty_superproject() -> None:
    runner = FakeGitRunner(**{"status --porcelain": " M AGENTS.md"})
    with pytest.raises(engine.ReleaseError) as exc:
        engine.verify_clean_worktrees(runner)
    assert exc.value.exit_code == engine.EXIT_SOURCE_GATE


def test_verify_clean_worktrees_ignores_untracked_owner_paths() -> None:
    class _OwnerUntracked:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def __call__(self, argv: list[str], input_bytes: bytes | None) -> str:
            self.calls.append(list(argv))
            if argv[0] != "git":
                raise AssertionError(argv)
            if "status" in argv and "--porcelain" in argv:
                if "--untracked-files=no" in argv:
                    return ""
                return "?? .playwright-mcp/\n?? uv.lock\n"
            raise AssertionError(argv)

    runner = _OwnerUntracked()
    engine.verify_clean_worktrees(runner)
    assert runner.calls
    for call in runner.calls:
        assert "--untracked-files=no" in call


def test_verify_public_main_rejects_unpublished_release() -> None:
    runner = FakeGitRunner(
        **{"ls-remote origin refs/heads/main": f"{'f' * 40}\trefs/heads/main"}
    )
    with pytest.raises(engine.ReleaseError) as exc:
        engine.verify_public_main(runner, RELEASE)
    assert exc.value.exit_code == engine.EXIT_PUBLIC_MISMATCH


def test_verify_ap_pin_rejects_moving_ap() -> None:
    runner = FakeGitRunner(**{"-C .ap rev-parse HEAD": "e" * 40})
    with pytest.raises(engine.ReleaseError) as exc:
        engine.verify_ap_pin(runner, RELEASE)
    assert exc.value.exit_code == engine.EXIT_AP_MISMATCH


def test_ap_gitlink_resolution_uses_pinned_gitlink_not_main() -> None:
    runner = FakeGitRunner()
    assert engine.ap_gitlink_of(runner, RELEASE) == AP_PIN
    joined = " ".join(" ".join(call) for call in runner.calls)
    assert "ls-tree" in joined
    assert RELEASE in joined


def test_verify_local_head_accepts_exact_match() -> None:
    runner = FakeGitRunner()
    engine.verify_local_head(runner, RELEASE)


def test_validate_remote_path_bounds_paths() -> None:
    engine.validate_remote_path("/opt/framenest/releases/abc", engine.RELEASE_ROOT)
    with pytest.raises(engine.ReleaseError):
        engine.validate_remote_path("/etc/passwd", engine.RELEASE_ROOT)
    with pytest.raises(engine.ReleaseError):
        engine.validate_remote_path("/opt/framenest/../etc", engine.RELEASE_ROOT)


def test_relocate_venv_shebangs_rewrites_staging_prefix_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "RELEASE_ROOT", str(tmp_path))
    staging = tmp_path / f"{RELEASE}.staging"
    final = tmp_path / RELEASE
    bindir = staging / ".venv" / "bin"
    bindir.mkdir(parents=True)
    db = bindir / "kronika-db"
    backup = bindir / "kronika-backup"
    untouched = bindir / "other-tool"
    original_untouched = "#!/usr/bin/env python3\nprint('leave me')\n"
    db.write_text(f"#!{staging}/.venv/bin/python\nprint('db')\n", encoding="utf-8")
    backup.write_text(f"#!{staging}/.venv/bin/python\nprint('backup')\n", encoding="utf-8")
    untouched.write_text(original_untouched, encoding="utf-8")

    engine.relocate_venv_shebangs(str(staging), str(final))

    db_text = db.read_text(encoding="utf-8")
    backup_text = backup.read_text(encoding="utf-8")
    assert db_text.splitlines()[0] == f"#!{final}/.venv/bin/python"
    assert backup_text.splitlines()[0] == f"#!{final}/.venv/bin/python"
    assert ".staging" not in db_text
    assert ".staging" not in backup_text
    assert untouched.read_text(encoding="utf-8") == original_untouched


def test_relocate_venv_shebangs_fails_closed_when_none_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "RELEASE_ROOT", str(tmp_path))
    staging = tmp_path / f"{RELEASE}.staging"
    final = tmp_path / RELEASE
    bindir = staging / ".venv" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "kronika-db").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (bindir / "kronika-backup").write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    with pytest.raises(engine.ReleaseError) as exc:
        engine.relocate_venv_shebangs(str(staging), str(final))
    assert exc.value.exit_code == engine.EXIT_POETRY


def _editable_metadata_tree(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path, Path]:
    staging = tmp_path / f"{RELEASE}.staging"
    final = tmp_path / RELEASE
    bindir = staging / ".venv" / "bin"
    site = staging / ".venv" / "lib" / "python3.13" / "site-packages"
    dist = site / "framenest-0.1.0.dist-info"
    bindir.mkdir(parents=True)
    dist.mkdir(parents=True)
    db = bindir / "kronika-db"
    backup = bindir / "kronika-backup"
    db.write_text(f"#!{staging}/.venv/bin/python\nprint('db')\n", encoding="utf-8")
    backup.write_text(f"#!{staging}/.venv/bin/python\nprint('backup')\n", encoding="utf-8")
    pth = site / "framenest.pth"
    direct_url = dist / "direct_url.json"
    sibling = site / "unrelated.txt"
    return staging, final, pth, direct_url, sibling, db


def test_relocate_venv_rewrites_editable_pth_and_direct_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "RELEASE_ROOT", str(tmp_path))
    staging, final, pth, direct_url, sibling, db = _editable_metadata_tree(tmp_path)
    pth.write_text(f"{staging}/src\n", encoding="utf-8")
    direct_url.write_text(
        f'{{"url": "file://{staging}", "dir_info": {{"editable": true}}}}\n',
        encoding="utf-8",
    )
    original_sibling = "no staging prefix here\n"
    sibling.write_text(original_sibling, encoding="utf-8")

    engine.relocate_venv_shebangs(str(staging), str(final))

    db_text = db.read_text(encoding="utf-8")
    pth_text = pth.read_text(encoding="utf-8")
    direct_text = direct_url.read_text(encoding="utf-8")
    assert db_text.splitlines()[0] == f"#!{final}/.venv/bin/python"
    assert pth_text == f"{final}/src\n"
    assert direct_text == (
        f'{{"url": "file://{final}", "dir_info": {{"editable": true}}}}\n'
    )
    assert ".staging" not in db_text
    assert ".staging" not in pth_text
    assert ".staging" not in direct_text
    assert engine.CPYTHON_BIN not in db_text
    assert sibling.read_text(encoding="utf-8") == original_sibling


def test_relocate_venv_fails_closed_when_pth_retains_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "RELEASE_ROOT", str(tmp_path))
    staging, final, pth, direct_url, sibling, _db = _editable_metadata_tree(tmp_path)
    leftover = tmp_path / f"{'b' * 40}.staging"
    pth.write_text(f"{leftover}/src\n", encoding="utf-8")
    direct_url.write_text(
        f'{{"url": "file://{final}", "dir_info": {{"editable": true}}}}\n',
        encoding="utf-8",
    )
    sibling.write_text("no staging prefix here\n", encoding="utf-8")

    with pytest.raises(engine.ReleaseError) as exc:
        engine.relocate_venv_shebangs(str(staging), str(final))
    assert exc.value.exit_code == engine.EXIT_POETRY
    assert ".staging" in pth.read_text(encoding="utf-8")


def test_relocate_venv_fails_closed_when_direct_url_retains_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "RELEASE_ROOT", str(tmp_path))
    staging, final, pth, direct_url, sibling, _db = _editable_metadata_tree(tmp_path)
    leftover = tmp_path / f"{'b' * 40}.staging"
    pth.write_text(f"{final}/src\n", encoding="utf-8")
    direct_url.write_text(
        f'{{"url": "file://{leftover}", "dir_info": {{"editable": true}}}}\n',
        encoding="utf-8",
    )
    sibling.write_text("no staging prefix here\n", encoding="utf-8")

    with pytest.raises(engine.ReleaseError) as exc:
        engine.relocate_venv_shebangs(str(staging), str(final))
    assert exc.value.exit_code == engine.EXIT_POETRY
    assert ".staging" in direct_url.read_text(encoding="utf-8")


def _script_bindir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    monkeypatch.setattr(engine, "RELEASE_ROOT", str(tmp_path))
    staging = tmp_path / f"{RELEASE}.staging"
    final = tmp_path / RELEASE
    bindir = staging / ".venv" / "bin"
    bindir.mkdir(parents=True)
    return staging, final, bindir


def _write_staging_script(bindir: Path, staging: Path, name: str) -> Path:
    script = bindir / name
    script.write_text(f"#!{staging}/.venv/bin/python\nprint({name!r})\n", encoding="utf-8")
    return script


def test_relocate_venv_shebangs_requires_the_default_web_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The omitted script list is the web pair, so a capture-only venv is refused."""
    staging, final, bindir = _script_bindir(tmp_path, monkeypatch)
    backup = _write_staging_script(bindir, staging, "kronika-backup")
    with pytest.raises(engine.ReleaseError) as exc:
        engine.relocate_venv_shebangs(str(staging), str(final))
    assert str(exc.value) == "required console script is missing"
    assert exc.value.exit_code == engine.EXIT_POETRY
    assert backup.read_text(encoding="utf-8").splitlines()[0] == (
        f"#!{final}/.venv/bin/python"
    )


def test_relocate_venv_shebangs_accepts_a_requested_capture_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final, bindir = _script_bindir(tmp_path, monkeypatch)
    name = f"{final}/.venv/bin/kronika-capture".rsplit("/", 1)[-1]
    script = _write_staging_script(bindir, staging, name)
    assert not (bindir / "kronika-db").exists()
    assert not (bindir / "kronika-backup").exists()

    engine.relocate_venv_shebangs(str(staging), str(final), required_scripts=(name,))

    assert script.read_text(encoding="utf-8").splitlines()[0] == (
        f"#!{final}/.venv/bin/python"
    )


def test_relocate_venv_shebangs_refuses_a_venv_missing_the_requested_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final, bindir = _script_bindir(tmp_path, monkeypatch)
    other = _write_staging_script(bindir, staging, "other-tool")
    with pytest.raises(engine.ReleaseError) as exc:
        engine.relocate_venv_shebangs(
            str(staging), str(final), required_scripts=("kronika-capture",)
        )
    assert str(exc.value) == "required console script is missing"
    assert exc.value.exit_code == engine.EXIT_POETRY
    assert other.read_text(encoding="utf-8").splitlines()[0] == (
        f"#!{final}/.venv/bin/python"
    )


def test_relocate_venv_shebangs_rejects_an_empty_script_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final, bindir = _script_bindir(tmp_path, monkeypatch)
    for name in ("kronika-db", "kronika-backup"):
        _write_staging_script(bindir, staging, name)
    engine.relocate_venv_shebangs(str(staging), str(final))
    with pytest.raises(engine.ReleaseError) as exc:
        engine.relocate_venv_shebangs(str(staging), str(final), required_scripts=())
    assert str(exc.value) == "required console script list is empty"
    assert exc.value.exit_code == engine.EXIT_POETRY


@pytest.mark.parametrize(
    "name",
    ["", "../kronika-capture", "kronika/capture", "kronika capture", "-capture", ".", ".."],
)
def test_relocate_venv_shebangs_rejects_an_unsafe_script_name(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final, bindir = _script_bindir(tmp_path, monkeypatch)
    script = _write_staging_script(bindir, staging, "kronika-capture")
    original = script.read_text(encoding="utf-8")
    with pytest.raises(engine.ReleaseError) as exc:
        engine.relocate_venv_shebangs(
            str(staging), str(final), required_scripts=(name,)
        )
    assert str(exc.value) == "required console script name is invalid"
    assert exc.value.exit_code == engine.EXIT_POETRY
    assert "required console script is missing" not in str(exc.value)
    assert script.read_text(encoding="utf-8") == original


def test_relocate_venv_shebangs_rejects_a_bare_script_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final, bindir = _script_bindir(tmp_path, monkeypatch)
    _write_staging_script(bindir, staging, "kronika-capture")
    with pytest.raises(engine.ReleaseError) as exc:
        engine.relocate_venv_shebangs(
            str(staging), str(final), required_scripts="kronika-capture"
        )
    assert str(exc.value) == "required console script name is invalid"


def test_capture_script_relocation_still_rejects_staging_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "RELEASE_ROOT", str(tmp_path))
    staging, final, pth, _direct_url, _sibling, _db = _editable_metadata_tree(tmp_path)
    bindir = staging / ".venv" / "bin"
    _write_staging_script(bindir, staging, "kronika-capture")
    (bindir / "kronika-db").unlink()
    (bindir / "kronika-backup").unlink()
    leftover = tmp_path / f"{'b' * 40}.staging"
    pth.write_text(f"{leftover}/src\n", encoding="utf-8")
    with pytest.raises(engine.ReleaseError) as exc:
        engine.relocate_venv_shebangs(
            str(staging), str(final), required_scripts=("kronika-capture",)
        )
    assert str(exc.value) == "editable install metadata still names staging path"
    assert exc.value.exit_code == engine.EXIT_POETRY


def _subcommand_choices(parser: argparse.ArgumentParser, dest: str) -> set[str]:
    matches = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction) and action.dest == dest
    ]
    assert len(matches) == 1
    return set(matches[0].choices)


def test_parser_exposes_only_the_routine_release_commands() -> None:
    """Routine release commands remain; the one-time migration commands do not."""
    parser = engine._build_parser()
    assert _subcommand_choices(parser, "command") == {
        "status",
        "check",
        "deploy",
        "rollback",
        "activate-capture",
        "rollback-capture",
        "_remote",
    }
    remote = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction) and action.dest == "command"
    ).choices["_remote"]
    assert _subcommand_choices(remote, "remote_command") == {
        "_remote-extract",
        "_remote-relocate-venv-shebangs",
    }
