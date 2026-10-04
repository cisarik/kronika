"""Unit tests for pure-domain upload sessions."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from tests.support.kronika_identity import expected

from kronika.domain import MediaByteIdentityId
from kronika.domain.uploads import (
    ALLOWED_UPLOAD_SESSION_TRANSITIONS,
    COMPLETE_UPLOAD_SESSION_STATES,
    FrameNestIncompleteUploadSessionError,
    FrameNestUploadChecksumError,
    FrameNestUploadOffsetError,
    FrameNestUploadSessionError,
    FrameNestUploadSessionTransitionError,
    FrameNestUploadValidationEvidenceError,
    TERMINAL_UPLOAD_SESSION_STATES,
    UploadDuplicateDisposition,
    UploadDisplayFilename,
    UploadSession,
    UploadSessionId,
    UploadSessionState,
    UploadStorageKey,
    UploadValidatedFormat,
    UploadValidatedMediaKind,
    VALIDATED_UPLOAD_SESSION_STATES,
    ensure_upload_session_can_transition,
    ensure_upload_session_transition_allowed,
    is_terminal_upload_session_state,
    validate_sha256_checksum_hex,
    validate_upload_offset_advance,
    validate_upload_validation_evidence,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DOMAIN_UPLOADS_MODULE = REPOSITORY_ROOT / "src" / "kronika" / "domain" / "uploads.py"


def _session(**overrides: object) -> UploadSession:
    values: dict[str, object] = {
        "id": UploadSessionId.new(),
        "state": UploadSessionState.CREATED,
        "storage_key": UploadStorageKey("upload-session-0001"),
        "display_filename": UploadDisplayFilename("example.mp4"),
        "declared_size_bytes": 100,
        "received_size_bytes": 0,
        "checksum_algorithm": None,
        "checksum_hex": None,
        "created_at_ms": 10,
        "updated_at_ms": 10,
        "expires_at_ms": 20,
        "failure_code": None,
        "version": 0,
        "byte_identity_id": None,
        "duplicate_disposition": None,
    }
    values.update(overrides)
    if values["state"] in VALIDATED_UPLOAD_SESSION_STATES:
        values.setdefault("checksum_algorithm", "sha256")
        values.setdefault("checksum_hex", "a" * 64)
        values.setdefault("validated_media_kind", UploadValidatedMediaKind.VIDEO)
        values.setdefault("validated_format", UploadValidatedFormat.MP4)
        values.setdefault("byte_identity_id", MediaByteIdentityId.new())
        if values["checksum_algorithm"] is None:
            values["checksum_algorithm"] = "sha256"
        if values["checksum_hex"] is None:
            values["checksum_hex"] = "a" * 64
        if values.get("validated_media_kind") is None:
            values["validated_media_kind"] = UploadValidatedMediaKind.VIDEO
        if values.get("validated_format") is None:
            values["validated_format"] = UploadValidatedFormat.MP4
        if values.get("byte_identity_id") is None:
            values["byte_identity_id"] = MediaByteIdentityId.new()
    return UploadSession(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (source, target)
        for source, targets in ALLOWED_UPLOAD_SESSION_TRANSITIONS.items()
        for target in targets
    ],
)
def test_every_allowed_transition_is_accepted(
    source: UploadSessionState,
    target: UploadSessionState,
) -> None:
    ensure_upload_session_transition_allowed(source, target)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (UploadSessionState.CREATED, UploadSessionState.RECEIVED),
        (UploadSessionState.RECEIVING, UploadSessionState.PUBLISHED),
        (UploadSessionState.VALIDATING, UploadSessionState.CATALOGED),
        (UploadSessionState.PUBLISHED, UploadSessionState.FAILED),
    ],
)
def test_representative_forbidden_transitions_are_rejected(
    source: UploadSessionState,
    target: UploadSessionState,
) -> None:
    with pytest.raises(FrameNestUploadSessionTransitionError):
        ensure_upload_session_transition_allowed(source, target)


@pytest.mark.parametrize("terminal", sorted(TERMINAL_UPLOAD_SESSION_STATES, key=str))
def test_terminal_states_have_no_outgoing_transitions(terminal: UploadSessionState) -> None:
    assert is_terminal_upload_session_state(terminal)
    assert ALLOWED_UPLOAD_SESSION_TRANSITIONS[terminal] == frozenset()
    for target in UploadSessionState:
        with pytest.raises(FrameNestUploadSessionTransitionError):
            ensure_upload_session_transition_allowed(terminal, target)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (UploadSessionState.PUBLISH_PENDING, UploadSessionState.PUBLISHED),
        (UploadSessionState.PUBLISHED, UploadSessionState.CATALOGED),
    ],
)
def test_external_effect_owned_transitions_are_not_generic(
    source: UploadSessionState,
    target: UploadSessionState,
) -> None:
    with pytest.raises(FrameNestUploadSessionTransitionError):
        ensure_upload_session_transition_allowed(
            source,
            target,
        )


def test_duplicate_pending_has_only_the_approved_disposition_and_failure_transitions() -> None:
    approved = frozenset(
        {
            UploadSessionState.PUBLISH_PENDING,
            UploadSessionState.CANCELLED,
            UploadSessionState.EXPIRED,
            UploadSessionState.FAILED,
        }
    )

    assert ALLOWED_UPLOAD_SESSION_TRANSITIONS[UploadSessionState.DUPLICATE_PENDING] == (
        approved
    )
    for target in approved:
        ensure_upload_session_transition_allowed(
            UploadSessionState.DUPLICATE_PENDING,
            target,
        )
    for target in set(UploadSessionState) - approved:
        with pytest.raises(FrameNestUploadSessionTransitionError):
            ensure_upload_session_transition_allowed(
                UploadSessionState.DUPLICATE_PENDING,
                target,
            )


@pytest.mark.parametrize(
    ("declared", "received"),
    [(0, 0), (-1, 0), (100, -1), (100, 101), (True, 0), (100, True)],
)
def test_size_invariants_are_enforced(declared: Any, received: Any) -> None:
    with pytest.raises(FrameNestUploadSessionError):
        _session(declared_size_bytes=declared, received_size_bytes=received)


def test_created_state_requires_zero_received_bytes() -> None:
    assert _session(state=UploadSessionState.CREATED, received_size_bytes=0).state == (
        UploadSessionState.CREATED
    )
    with pytest.raises(FrameNestUploadSessionError):
        _session(state=UploadSessionState.CREATED, received_size_bytes=1)


@pytest.mark.parametrize("state", sorted(COMPLETE_UPLOAD_SESSION_STATES, key=str))
def test_complete_states_require_exact_declared_bytes(state: UploadSessionState) -> None:
    assert (
        _session(state=state, declared_size_bytes=100, received_size_bytes=100).state
        == state
    )
    with pytest.raises(FrameNestIncompleteUploadSessionError):
        _session(state=state, declared_size_bytes=100, received_size_bytes=99)


@pytest.mark.parametrize(
    "state",
    [
        UploadSessionState.RECEIVING,
        UploadSessionState.CANCELLED,
        UploadSessionState.EXPIRED,
        UploadSessionState.FAILED,
    ],
)
def test_partial_or_complete_progress_states_accept_truthful_offsets(
    state: UploadSessionState,
) -> None:
    assert _session(state=state, received_size_bytes=50).received_size_bytes == 50
    assert _session(state=state, received_size_bytes=100).received_size_bytes == 100


def test_incomplete_receiving_cannot_transition_to_received() -> None:
    with pytest.raises(FrameNestIncompleteUploadSessionError):
        ensure_upload_session_can_transition(
            _session(state=UploadSessionState.RECEIVING, received_size_bytes=99),
            UploadSessionState.RECEIVED,
        )


def test_complete_receiving_can_transition_to_received_and_later_complete_states() -> None:
    received = _session(state=UploadSessionState.RECEIVING, received_size_bytes=100)
    ensure_upload_session_can_transition(received, UploadSessionState.RECEIVED)

    for source, target in (
        (UploadSessionState.RECEIVED, UploadSessionState.VALIDATING),
        (UploadSessionState.VALIDATING, UploadSessionState.DUPLICATE_PENDING),
        (UploadSessionState.VALIDATING, UploadSessionState.PUBLISH_PENDING),
        (UploadSessionState.VALIDATING, UploadSessionState.REJECTED),
        (UploadSessionState.DUPLICATE_PENDING, UploadSessionState.PUBLISH_PENDING),
    ):
        ensure_upload_session_can_transition(
            _session(state=source, received_size_bytes=100),
            target,
        )


@pytest.mark.parametrize(
    ("current", "accepted", "declared", "expected"),
    [(0, 1, 10, 1), (5, 5, 10, 10)],
)
def test_valid_offset_advance_returns_next_offset(
    current: int,
    accepted: int,
    declared: int,
    expected: int,
) -> None:
    assert (
        validate_upload_offset_advance(
            current_offset=current,
            accepted_bytes=accepted,
            declared_size_bytes=declared,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("current", "accepted", "declared"),
    [(-1, 1, 10), (0, 0, 10), (0, -1, 10), (9, 2, 10)],
)
def test_invalid_offset_advance_is_rejected(
    current: int,
    accepted: int,
    declared: int,
) -> None:
    with pytest.raises((FrameNestUploadOffsetError, FrameNestUploadSessionError)):
        validate_upload_offset_advance(
            current_offset=current,
            accepted_bytes=accepted,
            declared_size_bytes=declared,
        )


def test_checksum_validation_accepts_only_lowercase_sha256_hex() -> None:
    digest = "a" * 64

    assert validate_sha256_checksum_hex(digest) == digest
    _session(checksum_algorithm="sha256", checksum_hex=digest)

    for invalid in ("A" * 64, "g" * 64, "a" * 63, None, 1):
        with pytest.raises(FrameNestUploadChecksumError):
            validate_sha256_checksum_hex(invalid)
        with pytest.raises(FrameNestUploadChecksumError):
            _session(checksum_algorithm="sha256", checksum_hex=invalid)


def test_checksum_pair_is_absent_before_known() -> None:
    session = _session()

    assert session.checksum_algorithm is None
    assert session.checksum_hex is None

    with pytest.raises(FrameNestUploadChecksumError):
        _session(checksum_algorithm="sha256", checksum_hex=None)
    with pytest.raises(FrameNestUploadChecksumError):
        _session(checksum_algorithm=None, checksum_hex="a" * 64)


def test_validation_evidence_accepts_only_approved_kind_format_pairs() -> None:
    kind, media_format = validate_upload_validation_evidence(
        media_kind="animated_image",
        media_format="gif",
    )

    assert kind is UploadValidatedMediaKind.ANIMATED_IMAGE
    assert media_format is UploadValidatedFormat.GIF
    _session(
        state=UploadSessionState.PUBLISH_PENDING,
        received_size_bytes=100,
        checksum_algorithm="sha256",
        checksum_hex="a" * 64,
        validated_media_kind=UploadValidatedMediaKind.VIDEO,
        validated_format=UploadValidatedFormat.MP4,
    )
    with pytest.raises(FrameNestUploadValidationEvidenceError):
        validate_upload_validation_evidence(media_kind="video", media_format="gif")
    with pytest.raises(FrameNestUploadValidationEvidenceError):
        _session(
            state=UploadSessionState.REJECTED,
            received_size_bytes=100,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.GIF,
        )


def test_advanced_states_require_checksum_and_validation_evidence() -> None:
    values: dict[str, object] = {
        "id": UploadSessionId.new(),
        "state": UploadSessionState.PUBLISH_PENDING,
        "storage_key": UploadStorageKey("upload-session-0002"),
        "display_filename": UploadDisplayFilename("example.mp4"),
        "declared_size_bytes": 100,
        "received_size_bytes": 100,
        "checksum_algorithm": None,
        "checksum_hex": None,
        "created_at_ms": 10,
        "updated_at_ms": 10,
        "expires_at_ms": 20,
        "failure_code": None,
        "version": 0,
    }

    with pytest.raises(FrameNestUploadValidationEvidenceError):
        UploadSession(**values)  # type: ignore[arg-type]


def test_advanced_states_require_byte_identity_link() -> None:
    with pytest.raises(FrameNestUploadValidationEvidenceError):
        UploadSession(
            id=UploadSessionId.new(),
            state=UploadSessionState.PUBLISH_PENDING,
            storage_key=UploadStorageKey("upload-session-0003"),
            display_filename=UploadDisplayFilename("example.mp4"),
            declared_size_bytes=100,
            received_size_bytes=100,
            checksum_algorithm="sha256",
            checksum_hex="a" * 64,
            created_at_ms=10,
            updated_at_ms=10,
            expires_at_ms=20,
            failure_code=None,
            version=0,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            byte_identity_id=None,
        )


@pytest.mark.parametrize(
    ("state", "disposition"),
    [
        (UploadSessionState.PUBLISH_PENDING, UploadDuplicateDisposition.KEEP_SEPARATE),
        (UploadSessionState.PUBLISHED, UploadDuplicateDisposition.KEEP_SEPARATE),
        (UploadSessionState.CATALOGED, UploadDuplicateDisposition.KEEP_SEPARATE),
        (UploadSessionState.FAILED, UploadDuplicateDisposition.KEEP_SEPARATE),
        (UploadSessionState.CANCELLED, UploadDuplicateDisposition.DISCARD),
    ],
)
def test_duplicate_disposition_requires_compatible_state_and_validation_evidence(
    state: UploadSessionState,
    disposition: UploadDuplicateDisposition,
) -> None:
    evidence = {
        "checksum_algorithm": "sha256",
        "checksum_hex": "a" * 64,
        "validated_media_kind": UploadValidatedMediaKind.VIDEO,
        "validated_format": UploadValidatedFormat.MP4,
        "byte_identity_id": MediaByteIdentityId.new(),
    }

    session = _session(
        state=state,
        received_size_bytes=100,
        duplicate_disposition=disposition,
        **evidence,
    )

    assert session.duplicate_disposition is disposition


@pytest.mark.parametrize(
    ("state", "disposition"),
    [
        (UploadSessionState.DUPLICATE_PENDING, UploadDuplicateDisposition.KEEP_SEPARATE),
        (UploadSessionState.PUBLISH_PENDING, UploadDuplicateDisposition.DISCARD),
        (UploadSessionState.CANCELLED, UploadDuplicateDisposition.KEEP_SEPARATE),
    ],
)
def test_duplicate_disposition_rejects_incompatible_state(
    state: UploadSessionState,
    disposition: UploadDuplicateDisposition,
) -> None:
    with pytest.raises(FrameNestUploadSessionError):
        _session(
            state=state,
            received_size_bytes=100,
            duplicate_disposition=disposition,
            checksum_algorithm="sha256",
            checksum_hex="a" * 64,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            byte_identity_id=MediaByteIdentityId.new(),
        )


def test_duplicate_disposition_rejects_missing_validation_evidence() -> None:
    with pytest.raises(FrameNestUploadValidationEvidenceError):
        _session(
            state=UploadSessionState.CANCELLED,
            received_size_bytes=100,
            duplicate_disposition=UploadDuplicateDisposition.DISCARD,
        )


def test_storage_key_is_opaque_and_display_filename_is_not_a_storage_path() -> None:
    session = _session(
        storage_key=UploadStorageKey("serverownedkey001"),
        display_filename=UploadDisplayFilename("Family Clip.mp4"),
    )

    assert session.storage_key.value == "serverownedkey001"
    assert session.display_filename.value == "Family Clip.mp4"

    for rejected_key in ("/absolute/file.mp4", "clips/file.mp4", "C:\\media\\file.mp4"):
        with pytest.raises(FrameNestUploadSessionError):
            UploadStorageKey(rejected_key)
    for rejected_name in ("/absolute/file.mp4", "clips/file.mp4", "C:\\media\\file.mp4"):
        with pytest.raises(FrameNestUploadSessionError):
            UploadDisplayFilename(rejected_name)


def test_expiration_must_be_later_than_creation() -> None:
    with pytest.raises(FrameNestUploadSessionError):
        _session(created_at_ms=10, expires_at_ms=10)


def test_upload_domain_module_imports_no_infrastructure_or_framework() -> None:
    tree = ast.parse(DOMAIN_UPLOADS_MODULE.read_text(encoding="utf-8"))
    forbidden_roots = {
        "alembic",
        "fastapi",
        "pydantic",
        "pydantic_settings",
        "sqlalchemy",
        "starlette",
        "uvicorn",
        "kronika.infrastructure",
        "kronika.application",
        "kronika.adapters",
        "kronika.configuration",
    }
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module = node.names[0].name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
        else:
            continue
        root = module.split(".")[0]
        if root in forbidden_roots or any(module.startswith(prefix) for prefix in forbidden_roots):
            violations.append(module)
    assert violations == []


def test_rejected_upload_session_identity_reports_the_derived_brand() -> None:
    with pytest.raises(FrameNestUploadSessionError) as excinfo:
        UploadSessionId(1)

    assert str(excinfo.value) == expected("Invalid {brand} upload session.")
