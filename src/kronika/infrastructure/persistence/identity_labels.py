"""Bounded catalog display-label maintenance for one stopped-writers window.

This module owns the only repository interface that modifies existing catalog
label rows. It resolves exact candidates, records private inverse values in a
selection receipt, and applies or reverts display names by compare-and-set
inside one immediate transaction. It never deletes or re-registers a row, never
changes an ID or relationship, and never touches a non-label column.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError

from kronika.domain import DeviceId, FrameNestIdentityError, LibraryId
from kronika.infrastructure.persistence.catalog_schema import devices, libraries
from kronika.infrastructure.persistence.engine import (
    DEFAULT_BUSY_TIMEOUT_SECONDS,
    create_sqlite_engine,
    create_sqlite_readonly_engine,
    dispose_engine,
    run_in_immediate_transaction,
)

RETIRED_DEVICE_LABEL = "FrameNest NUC"
CANONICAL_DEVICE_LABEL = "Kronika NUC"
RETIRED_BRAND = "FrameNest"
CANONICAL_BRAND = "Kronika"
RECEIPT_VERSION = 1
RECEIPT_SUFFIX = ".identity-labels-receipt.json"
_RECEIPT_MODE = 0o600

_IdentityFactory = Callable[[str], Any]


class IdentityLabelsError(RuntimeError):
    """Sanitized identity-label failure whose message carries no value."""


class IdentityLabelsReceiptExistsError(IdentityLabelsError):
    """The selection receipt already exists and is never overwritten."""


class IdentityLabelsReceiptNotFoundError(IdentityLabelsError):
    """The approved selection receipt does not exist."""


class IdentityLabelsReceiptInvalidError(IdentityLabelsError):
    """The receipt is malformed, tampered or not privately stored."""


class IdentityLabelsStaleReceiptError(IdentityLabelsError):
    """The recorded expected state no longer matches the catalog."""


class IdentityLabelsAmbiguousCandidateError(IdentityLabelsError):
    """More than one candidate row matches the expected old value."""


class IdentityLabelsSelectionNotApprovedError(IdentityLabelsError):
    """The receipt and the explicit invocation select nothing to change."""


class IdentityLabelsVerificationError(IdentityLabelsError):
    """The post-commit read-back did not observe the intended state."""


class IdentityLabelsCatalogBusyError(IdentityLabelsError):
    """Another writer holds the catalog; the window must retry."""


@dataclass(frozen=True)
class DeviceLabelSelection:
    device_id: str
    old_label: str
    new_label: str


@dataclass(frozen=True)
class LibraryLabelSelection:
    library_id: str
    old_label: str
    new_label: str


@dataclass(frozen=True)
class IdentityLabelsReceipt:
    version: int
    created_at_ms: int
    device_candidate_count: int
    device_selection: DeviceLabelSelection | None
    library_candidate_count: int
    library_selections: tuple[LibraryLabelSelection, ...]


@dataclass(frozen=True)
class IdentityLabelsCheckResult:
    device_candidate_count: int
    library_candidate_count: int
    receipt_digest: str


@dataclass(frozen=True)
class IdentityLabelsMaintenanceResult:
    device_changed: bool
    libraries_changed: int
    device_verified: bool
    libraries_verified: bool
    foreign_keys_valid: bool
    schema_revision_at_head: bool
    selected_labels_remaining: int
    receipt_digest: str


@dataclass(frozen=True)
class _CandidateRows:
    device_rows: tuple[tuple[str, str], ...]
    library_rows: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _Verification:
    device_verified: bool
    libraries_verified: bool
    foreign_keys_valid: bool
    schema_revision_at_head: bool
    selected_labels_remaining: int


def receipt_path_for(database_path: Path | str) -> Path:
    """Return the fixed private receipt path beside the catalog."""
    path = Path(database_path)
    return path.with_name(path.name + RECEIPT_SUFFIX)


def check_identity_labels(
    database_path: Path | str,
    *,
    busy_timeout_seconds: float = DEFAULT_BUSY_TIMEOUT_SECONDS,
) -> IdentityLabelsCheckResult:
    """Resolve candidates read-only and write one private selection receipt."""
    engine = create_sqlite_readonly_engine(
        database_path,
        busy_timeout_seconds=busy_timeout_seconds,
    )
    try:
        with engine.connect() as connection:
            candidates = _resolve_candidates(connection)
    finally:
        dispose_engine(engine)
    receipt = _build_receipt(candidates)
    digest = _write_receipt(receipt_path_for(database_path), receipt)
    return IdentityLabelsCheckResult(
        device_candidate_count=len(candidates.device_rows),
        library_candidate_count=len(candidates.library_rows),
        receipt_digest=digest,
    )


def apply_identity_labels(
    database_path: Path | str,
    *,
    include_libraries: bool,
    expected_revision: str,
    busy_timeout_seconds: float = DEFAULT_BUSY_TIMEOUT_SECONDS,
) -> IdentityLabelsMaintenanceResult:
    """Apply the receipt selections by compare-and-set in one transaction."""
    receipt, digest = read_receipt(receipt_path_for(database_path))
    engine = create_sqlite_engine(
        database_path,
        busy_timeout_seconds=busy_timeout_seconds,
    )
    try:
        _run_immediate_operation(
            engine,
            receipt,
            include_libraries=include_libraries,
            applied=True,
        )
    finally:
        dispose_engine(engine)
    verification = _verify_maintenance(
        database_path,
        receipt,
        include_libraries=include_libraries,
        applied=True,
        expected_revision=expected_revision,
        busy_timeout_seconds=busy_timeout_seconds,
    )
    _require_verified(verification)
    return _result(
        receipt,
        verification,
        include_libraries=include_libraries,
        digest=digest,
    )


def rollback_identity_labels(
    database_path: Path | str,
    *,
    include_libraries: bool,
    expected_revision: str,
    busy_timeout_seconds: float = DEFAULT_BUSY_TIMEOUT_SECONDS,
) -> IdentityLabelsMaintenanceResult:
    """Restore the recorded inverse values by compare-and-set."""
    receipt, digest = read_receipt(receipt_path_for(database_path))
    engine = create_sqlite_engine(
        database_path,
        busy_timeout_seconds=busy_timeout_seconds,
    )
    try:
        _run_immediate_operation(
            engine,
            receipt,
            include_libraries=include_libraries,
            applied=False,
        )
    finally:
        dispose_engine(engine)
    verification = _verify_maintenance(
        database_path,
        receipt,
        include_libraries=include_libraries,
        applied=False,
        expected_revision=expected_revision,
        busy_timeout_seconds=busy_timeout_seconds,
    )
    _require_verified(verification)
    return _result(
        receipt,
        verification,
        include_libraries=include_libraries,
        digest=digest,
    )


def read_receipt(path: Path | str) -> tuple[IdentityLabelsReceipt, str]:
    """Read and strictly validate one privately stored selection receipt."""
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise IdentityLabelsReceiptNotFoundError() from None
    except OSError as exc:
        raise IdentityLabelsReceiptInvalidError() from exc
    try:
        info = os.fstat(descriptor)
        _require_private_regular_file(info)
        raw = _read_all(descriptor)
    except IdentityLabelsReceiptInvalidError:
        raise
    except OSError as exc:
        raise IdentityLabelsReceiptInvalidError() from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return _parse_receipt(raw), hashlib.sha256(raw).hexdigest()


def _run_immediate_operation(
    engine: Engine,
    receipt: IdentityLabelsReceipt,
    *,
    include_libraries: bool,
    applied: bool,
) -> None:
    try:
        run_in_immediate_transaction(
            engine,
            lambda connection: _maintenance_operation(
                connection,
                receipt,
                include_libraries=include_libraries,
                applied=applied,
            ),
        )
    except OperationalError as exc:
        if _is_catalog_busy(exc):
            raise IdentityLabelsCatalogBusyError() from None
        raise


def _maintenance_operation(
    connection: Connection,
    receipt: IdentityLabelsReceipt,
    *,
    include_libraries: bool,
    applied: bool,
) -> None:
    if include_libraries and not receipt.library_selections:
        raise IdentityLabelsSelectionNotApprovedError()
    if applied:
        candidates = _resolve_candidates(connection)
        _require_receipt_matches_candidates(receipt, candidates)
        if len(candidates.device_rows) > 1:
            raise IdentityLabelsAmbiguousCandidateError()
    if receipt.device_selection is None and not (
        include_libraries and receipt.library_selections
    ):
        raise IdentityLabelsSelectionNotApprovedError()
    if receipt.device_selection is not None:
        if applied:
            _compare_and_set(
                connection,
                devices,
                receipt.device_selection.device_id,
                receipt.device_selection.old_label,
                receipt.device_selection.new_label,
            )
        else:
            _require_label(
                connection,
                devices,
                receipt.device_selection.device_id,
                receipt.device_selection.new_label,
            )
            _require_no_other_label(
                connection,
                devices,
                receipt.device_selection.device_id,
                receipt.device_selection.old_label,
            )
            _compare_and_set(
                connection,
                devices,
                receipt.device_selection.device_id,
                receipt.device_selection.new_label,
                receipt.device_selection.old_label,
            )
    if include_libraries:
        for selection in receipt.library_selections:
            if applied:
                _compare_and_set(
                    connection,
                    libraries,
                    selection.library_id,
                    selection.old_label,
                    selection.new_label,
                )
            else:
                _require_label(
                    connection,
                    libraries,
                    selection.library_id,
                    selection.new_label,
                )
                _compare_and_set(
                    connection,
                    libraries,
                    selection.library_id,
                    selection.new_label,
                    selection.old_label,
                )


def _resolve_candidates(connection: Connection) -> _CandidateRows:
    device_rows = tuple(
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            select(devices.c.id, devices.c.display_name)
            .where(devices.c.display_name == RETIRED_DEVICE_LABEL)
            .order_by(devices.c.id)
        )
    )
    library_rows = tuple(
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            select(libraries.c.id, libraries.c.display_name)
            .where(func.instr(libraries.c.display_name, RETIRED_BRAND) > 0)
            .order_by(libraries.c.id)
        )
    )
    return _CandidateRows(device_rows=device_rows, library_rows=library_rows)


def _build_receipt(candidates: _CandidateRows) -> IdentityLabelsReceipt:
    device_selection: DeviceLabelSelection | None = None
    if len(candidates.device_rows) == 1:
        device_id, old_label = candidates.device_rows[0]
        device_selection = DeviceLabelSelection(
            device_id=device_id,
            old_label=old_label,
            new_label=CANONICAL_DEVICE_LABEL,
        )
    library_selections = tuple(
        LibraryLabelSelection(
            library_id=library_id,
            old_label=old_label,
            new_label=_canonicalize_library_label(old_label),
        )
        for library_id, old_label in candidates.library_rows
    )
    return IdentityLabelsReceipt(
        version=RECEIPT_VERSION,
        created_at_ms=int(time.time() * 1000),
        device_candidate_count=len(candidates.device_rows),
        device_selection=device_selection,
        library_candidate_count=len(candidates.library_rows),
        library_selections=library_selections,
    )


def _require_receipt_matches_candidates(
    receipt: IdentityLabelsReceipt,
    candidates: _CandidateRows,
) -> None:
    if receipt.device_candidate_count != len(candidates.device_rows):
        raise IdentityLabelsStaleReceiptError()
    if receipt.device_candidate_count == 1:
        selection = receipt.device_selection
        device_id, display_name = candidates.device_rows[0]
        if (
            selection is None
            or selection.device_id != device_id
            or selection.old_label != display_name
        ):
            raise IdentityLabelsStaleReceiptError()
    elif receipt.device_selection is not None:
        raise IdentityLabelsStaleReceiptError()
    if receipt.library_candidate_count != len(candidates.library_rows):
        raise IdentityLabelsStaleReceiptError()
    expected = {
        (selection.library_id, selection.old_label)
        for selection in receipt.library_selections
    }
    if expected != set(candidates.library_rows):
        raise IdentityLabelsStaleReceiptError()


def _compare_and_set(
    connection: Connection,
    table: Any,
    row_id: str,
    expected: str,
    target: str,
) -> None:
    result = connection.execute(
        update(table)
        .where(table.c.id == row_id, table.c.display_name == expected)
        .values(display_name=target)
    )
    if result.rowcount != 1:
        raise IdentityLabelsStaleReceiptError()


def _require_label(
    connection: Connection,
    table: Any,
    row_id: str,
    expected: str,
) -> None:
    current = connection.execute(
        select(table.c.display_name).where(table.c.id == row_id)
    ).scalar_one_or_none()
    if current != expected:
        raise IdentityLabelsStaleReceiptError()


def _require_no_other_label(
    connection: Connection,
    table: Any,
    row_id: str,
    label: str,
) -> None:
    count = connection.execute(
        select(func.count())
        .select_from(table)
        .where(table.c.display_name == label, table.c.id != row_id)
    ).scalar_one()
    if count:
        raise IdentityLabelsAmbiguousCandidateError()


def _verify_maintenance(
    database_path: Path | str,
    receipt: IdentityLabelsReceipt,
    *,
    include_libraries: bool,
    applied: bool,
    expected_revision: str,
    busy_timeout_seconds: float,
) -> _Verification:
    engine = create_sqlite_readonly_engine(
        database_path,
        busy_timeout_seconds=busy_timeout_seconds,
    )
    try:
        with engine.connect() as connection:
            device_verified = True
            if receipt.device_selection is not None:
                expected = (
                    receipt.device_selection.new_label
                    if applied
                    else receipt.device_selection.old_label
                )
                device_verified = (
                    _fetch_label(
                        connection, devices, receipt.device_selection.device_id
                    )
                    == expected
                )
            libraries_verified = True
            if include_libraries:
                for selection in receipt.library_selections:
                    expected = (
                        selection.new_label if applied else selection.old_label
                    )
                    if (
                        _fetch_label(connection, libraries, selection.library_id)
                        != expected
                    ):
                        libraries_verified = False
            foreign_keys_valid = not connection.exec_driver_sql(
                "PRAGMA foreign_key_check"
            ).fetchall()
            schema_revision = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar()
            schema_revision_at_head = schema_revision == expected_revision
            selected_labels_remaining = _count_selected_at_source_label(
                connection,
                receipt,
                include_libraries=include_libraries,
                applied=applied,
            )
    finally:
        dispose_engine(engine)
    return _Verification(
        device_verified=device_verified,
        libraries_verified=libraries_verified,
        foreign_keys_valid=foreign_keys_valid,
        schema_revision_at_head=schema_revision_at_head,
        selected_labels_remaining=selected_labels_remaining,
    )


def _count_selected_at_source_label(
    connection: Connection,
    receipt: IdentityLabelsReceipt,
    *,
    include_libraries: bool,
    applied: bool,
) -> int:
    remaining = 0
    if receipt.device_selection is not None:
        source = (
            receipt.device_selection.old_label
            if applied
            else receipt.device_selection.new_label
        )
        if (
            _fetch_label(connection, devices, receipt.device_selection.device_id)
            == source
        ):
            remaining += 1
    if include_libraries:
        for selection in receipt.library_selections:
            source = selection.old_label if applied else selection.new_label
            if (
                _fetch_label(connection, libraries, selection.library_id) == source
            ):
                remaining += 1
    return remaining


def _fetch_label(connection: Connection, table: Any, row_id: str) -> str | None:
    return connection.execute(
        select(table.c.display_name).where(table.c.id == row_id)
    ).scalar_one_or_none()


def _require_verified(verification: _Verification) -> None:
    if not (
        verification.device_verified
        and verification.libraries_verified
        and verification.foreign_keys_valid
        and verification.schema_revision_at_head
        and verification.selected_labels_remaining == 0
    ):
        raise IdentityLabelsVerificationError()


def _result(
    receipt: IdentityLabelsReceipt,
    verification: _Verification,
    *,
    include_libraries: bool,
    digest: str,
) -> IdentityLabelsMaintenanceResult:
    return IdentityLabelsMaintenanceResult(
        device_changed=receipt.device_selection is not None,
        libraries_changed=(
            len(receipt.library_selections) if include_libraries else 0
        ),
        device_verified=verification.device_verified,
        libraries_verified=verification.libraries_verified,
        foreign_keys_valid=verification.foreign_keys_valid,
        schema_revision_at_head=verification.schema_revision_at_head,
        selected_labels_remaining=verification.selected_labels_remaining,
        receipt_digest=digest,
    )


def _canonicalize_library_label(label: str) -> str:
    return label.replace(RETIRED_BRAND, CANONICAL_BRAND)


def _is_catalog_busy(exc: OperationalError) -> bool:
    text = str(getattr(exc, "orig", exc)).lower()
    return "locked" in text or "busy" in text


def _write_receipt(path: Path, receipt: IdentityLabelsReceipt) -> str:
    payload = _serialize_receipt(receipt)
    try:
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            _RECEIPT_MODE,
        )
    except FileExistsError:
        raise IdentityLabelsReceiptExistsError() from None
    except OSError as exc:
        raise IdentityLabelsError("receipt write failed") from exc
    try:
        os.fchmod(descriptor, _RECEIPT_MODE)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    except BaseException:
        try:
            os.close(descriptor)
        finally:
            _remove_owned_receipt(path)
        raise
    os.close(descriptor)
    return hashlib.sha256(payload).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        written += os.write(descriptor, payload[written:])


def _read_all(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _remove_owned_receipt(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _require_private_regular_file(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise IdentityLabelsReceiptInvalidError()
    if info.st_uid != os.getuid():
        raise IdentityLabelsReceiptInvalidError()
    if stat.S_IMODE(info.st_mode) != _RECEIPT_MODE:
        raise IdentityLabelsReceiptInvalidError()
    if info.st_nlink != 1:
        raise IdentityLabelsReceiptInvalidError()


def _serialize_receipt(receipt: IdentityLabelsReceipt) -> bytes:
    document: dict[str, Any] = {
        "version": receipt.version,
        "created_at_ms": receipt.created_at_ms,
        "device_candidate_count": receipt.device_candidate_count,
        "device_selection": (
            None
            if receipt.device_selection is None
            else {
                "id": receipt.device_selection.device_id,
                "old_label": receipt.device_selection.old_label,
                "new_label": receipt.device_selection.new_label,
            }
        ),
        "library_candidate_count": receipt.library_candidate_count,
        "library_selections": [
            {
                "id": selection.library_id,
                "old_label": selection.old_label,
                "new_label": selection.new_label,
            }
            for selection in receipt.library_selections
        ],
    }
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return (encoded + "\n").encode("utf-8")


def _parse_receipt(raw: bytes) -> IdentityLabelsReceipt:
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise IdentityLabelsReceiptInvalidError() from None
    if not isinstance(document, dict):
        raise IdentityLabelsReceiptInvalidError()
    if set(document) != {
        "version",
        "created_at_ms",
        "device_candidate_count",
        "device_selection",
        "library_candidate_count",
        "library_selections",
    }:
        raise IdentityLabelsReceiptInvalidError()
    if document["version"] != RECEIPT_VERSION:
        raise IdentityLabelsReceiptInvalidError()
    created_at_ms = _require_non_negative_int(document["created_at_ms"])
    device_candidate_count = _require_non_negative_int(
        document["device_candidate_count"]
    )
    library_candidate_count = _require_non_negative_int(
        document["library_candidate_count"]
    )
    device_selection = _parse_device_selection(document["device_selection"])
    if device_selection is None:
        if device_candidate_count == 1:
            raise IdentityLabelsReceiptInvalidError()
    elif device_candidate_count != 1:
        raise IdentityLabelsReceiptInvalidError()
    library_selections = _parse_library_selections(document["library_selections"])
    if len(library_selections) != library_candidate_count:
        raise IdentityLabelsReceiptInvalidError()
    return IdentityLabelsReceipt(
        version=RECEIPT_VERSION,
        created_at_ms=created_at_ms,
        device_candidate_count=device_candidate_count,
        device_selection=device_selection,
        library_candidate_count=library_candidate_count,
        library_selections=library_selections,
    )


def _require_non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IdentityLabelsReceiptInvalidError()
    return value


def _parse_device_selection(value: object) -> DeviceLabelSelection | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"id", "old_label", "new_label"}:
        raise IdentityLabelsReceiptInvalidError()
    device_id = _require_identity(DeviceId, value["id"])
    old_label = value["old_label"]
    new_label = value["new_label"]
    if not isinstance(old_label, str) or not isinstance(new_label, str):
        raise IdentityLabelsReceiptInvalidError()
    if old_label != RETIRED_DEVICE_LABEL or new_label != CANONICAL_DEVICE_LABEL:
        raise IdentityLabelsReceiptInvalidError()
    return DeviceLabelSelection(
        device_id=device_id,
        old_label=old_label,
        new_label=new_label,
    )


def _parse_library_selections(value: object) -> tuple[LibraryLabelSelection, ...]:
    if not isinstance(value, list):
        raise IdentityLabelsReceiptInvalidError()
    selections: list[LibraryLabelSelection] = []
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {
            "id",
            "old_label",
            "new_label",
        }:
            raise IdentityLabelsReceiptInvalidError()
        library_id = _require_identity(LibraryId, entry["id"])
        old_label = entry["old_label"]
        new_label = entry["new_label"]
        if not isinstance(old_label, str) or not isinstance(new_label, str):
            raise IdentityLabelsReceiptInvalidError()
        if RETIRED_BRAND not in old_label:
            raise IdentityLabelsReceiptInvalidError()
        if new_label != _canonicalize_library_label(old_label):
            raise IdentityLabelsReceiptInvalidError()
        if library_id in seen:
            raise IdentityLabelsReceiptInvalidError()
        seen.add(library_id)
        selections.append(
            LibraryLabelSelection(
                library_id=library_id,
                old_label=old_label,
                new_label=new_label,
            )
        )
    return tuple(selections)


def _require_identity(factory: _IdentityFactory, value: object) -> str:
    if not isinstance(value, str):
        raise IdentityLabelsReceiptInvalidError()
    try:
        return factory.from_string(value).to_string()
    except FrameNestIdentityError:
        raise IdentityLabelsReceiptInvalidError() from None
