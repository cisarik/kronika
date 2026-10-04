"""Administrator catalog-removal preview fingerprint and service contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from collections.abc import Callable
from typing import Literal, Protocol

from kronika.domain.identities import MediaId

ORIGINAL_BYTES_POLICY = "retain_all"
CATALOG_OUTCOME_REMOVED = "removed"

StorageClass = Literal["operator_managed", "server_managed_upload", "unknown"]
CleanupState = Literal["none", "pending", "complete", "failed"]
OriginalBytesOutcome = Literal[
    "retained_operator_managed",
    "retained_server_managed",
    "retained_already_missing",
    "retained_unknown",
]


class CatalogRemovalError(RuntimeError):
    """Sanitized catalog-removal failure."""


class CatalogRemovalNotFoundError(CatalogRemovalError):
    """Raised when the medium or receipt is absent."""


class CatalogRemovalStateConflictError(CatalogRemovalError):
    """Raised when the consequence fingerprint no longer matches."""


class CatalogRemovalBoundRecordError(CatalogRemovalStateConflictError):
    """Raised when removal is refused because a common record is bound."""

    def __init__(self) -> None:
        super().__init__("Catalog removal is not available for this media item.")


class CatalogRemovalValidationError(CatalogRemovalError, ValueError):
    """Raised for malformed removal requests."""


class CatalogRemovalInfrastructureError(CatalogRemovalError):
    """Raised when required cleanup infrastructure is unavailable."""


@dataclass(frozen=True, slots=True)
class CatalogRemovalLocationSnapshot:
    location_id: str
    library_id: str
    relative_path: str
    availability: str
    observed_size_bytes: int | None
    observed_mtime_ns: int | None


@dataclass(frozen=True, slots=True)
class CatalogRemovalUploadSnapshot:
    upload_id: str
    destination_id: str
    relative_target: str
    byte_identity_id: str
    state: str


@dataclass(frozen=True, slots=True)
class CatalogRemovalYouTubeSnapshot:
    claim_id: str
    state: str
    media_id: str | None
    media_location_id: str | None


@dataclass(frozen=True, slots=True)
class CatalogRemovalCoverSnapshot:
    revision: int
    artifact_digest: str


@dataclass(frozen=True, slots=True)
class CatalogRemovalAnalysisSnapshot:
    run_id: str
    version: int
    provider_submission_occurred: bool


@dataclass(frozen=True, slots=True)
class CatalogRemovalSnapshot:
    """Authoritative removal-relevant state for one logical medium."""

    media_id: str
    media_kind: str
    media_updated_at_ms: int
    display_title: str | None
    acquisition_source: str
    content_category: str
    publication_state: str
    published_at_ms: int | None
    publication_origin: str | None
    locations: tuple[CatalogRemovalLocationSnapshot, ...]
    upload_publications: tuple[CatalogRemovalUploadSnapshot, ...]
    youtube_claims: tuple[CatalogRemovalYouTubeSnapshot, ...]
    cover: CatalogRemovalCoverSnapshot | None
    analysis_runs: tuple[CatalogRemovalAnalysisSnapshot, ...]
    storage_class: StorageClass

    @property
    def analysis_run_count(self) -> int:
        return len(self.analysis_runs)

    @property
    def provider_submission_count(self) -> int:
        return sum(
            1 for run in self.analysis_runs if run.provider_submission_occurred
        )

    @property
    def original_bytes_outcome(self) -> OriginalBytesOutcome:
        if self.storage_class == "operator_managed":
            return "retained_operator_managed"
        if self.storage_class == "server_managed_upload":
            return "retained_server_managed"
        return "retained_unknown"


@dataclass(frozen=True, slots=True)
class CatalogRemovalPreview:
    media_id: str
    display_title: str | None
    publication_state: str
    acquisition_source: str
    storage_class: StorageClass
    original_bytes_policy: str
    original_bytes_outcome: OriginalBytesOutcome
    recovery_limitations: tuple[str, ...]
    provenance_effects: tuple[str, ...]
    analysis_run_count: int
    provider_submission_count: int
    derived_artifact_cleanup_intent: tuple[str, ...]
    consequence_fingerprint: str


@dataclass(frozen=True, slots=True)
class CatalogRemovalReceipt:
    id: str
    occurred_at_ms: int
    request_id: str
    actor_key: str
    media_id: str
    display_title_snapshot: str | None
    acquisition_source: str
    storage_class: StorageClass
    was_published: bool
    published_at_ms: int | None
    consequence_fingerprint: str
    catalog_outcome: str
    original_bytes_policy: str
    original_bytes_outcome: OriginalBytesOutcome
    youtube_claims_transitioned: int
    upload_publications_detached: int
    analysis_run_count: int
    provider_submission_count: int
    cover_artifact_digest: str | None
    preview_location_ids_json: str | None
    cover_cleanup_state: CleanupState
    preview_cleanup_state: CleanupState
    cleanup_updated_at_ms: int | None


@dataclass(frozen=True, slots=True)
class CatalogRemovalResult:
    catalog_state: Literal["removed"]
    receipt: CatalogRemovalReceipt
    derived_artifacts_outcome: Literal["none", "pending", "partial", "complete"]
    cleanup_retry_available: bool


def compute_consequence_fingerprint(snapshot: CatalogRemovalSnapshot) -> str:
    """Return a deterministic SHA-256 fingerprint of removal-relevant state."""
    payload = {
        "media_id": snapshot.media_id,
        "media_kind": snapshot.media_kind,
        "media_updated_at_ms": snapshot.media_updated_at_ms,
        "acquisition_source": snapshot.acquisition_source,
        "content_category": snapshot.content_category,
        "publication": {
            "state": snapshot.publication_state,
            "published_at_ms": snapshot.published_at_ms,
            "origin": snapshot.publication_origin,
        },
        "locations": [
            {
                "location_id": location.location_id,
                "library_id": location.library_id,
                "relative_path": location.relative_path,
                "availability": location.availability,
                "observed_size_bytes": location.observed_size_bytes,
                "observed_mtime_ns": location.observed_mtime_ns,
            }
            for location in sorted(snapshot.locations, key=lambda item: item.location_id)
        ],
        "upload_publications": [
            {
                "upload_id": item.upload_id,
                "destination_id": item.destination_id,
                "relative_target": item.relative_target,
                "byte_identity_id": item.byte_identity_id,
                "state": item.state,
            }
            for item in sorted(
                snapshot.upload_publications, key=lambda item: item.upload_id
            )
        ],
        "youtube_claims": [
            {
                "claim_id": item.claim_id,
                "state": item.state,
                "media_id": item.media_id,
                "media_location_id": item.media_location_id,
            }
            for item in sorted(snapshot.youtube_claims, key=lambda item: item.claim_id)
        ],
        "cover": None
        if snapshot.cover is None
        else {
            "revision": snapshot.cover.revision,
            "artifact_digest": snapshot.cover.artifact_digest,
        },
        "analysis": {
            "runs": [
                {
                    "run_id": run.run_id,
                    "version": run.version,
                    "provider_submission_occurred": run.provider_submission_occurred,
                }
                for run in sorted(snapshot.analysis_runs, key=lambda item: item.run_id)
            ],
            "provider_submission_count": snapshot.provider_submission_count,
        },
        "original_bytes_policy": ORIGINAL_BYTES_POLICY,
        "storage_class": snapshot.storage_class,
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_preview(snapshot: CatalogRemovalSnapshot) -> CatalogRemovalPreview:
    fingerprint = compute_consequence_fingerprint(snapshot)
    cleanup_intent: list[str] = []
    if snapshot.cover is not None:
        cleanup_intent.append("cover_artifact")
        cleanup_intent.append("cover_thumbnail")
    if snapshot.locations:
        cleanup_intent.append("gallery_preview_cache")
    provenance: list[str] = []
    if snapshot.youtube_claims:
        provenance.append("youtube_claims_become_catalog_removed")
    if snapshot.upload_publications:
        provenance.append("upload_publication_catalog_links_detached")
    if snapshot.analysis_run_count:
        provenance.append("analysis_runs_removed")
    return CatalogRemovalPreview(
        media_id=snapshot.media_id,
        display_title=snapshot.display_title,
        publication_state=snapshot.publication_state,
        acquisition_source=snapshot.acquisition_source,
        storage_class=snapshot.storage_class,
        original_bytes_policy=ORIGINAL_BYTES_POLICY,
        original_bytes_outcome=snapshot.original_bytes_outcome,
        recovery_limitations=(
            "Catalog backup can restore catalog rows only.",
            "Original media files remain on disk and are not purged by this action.",
            "Removed analysis history is summarized on the removal receipt only.",
        ),
        provenance_effects=tuple(provenance),
        analysis_run_count=snapshot.analysis_run_count,
        provider_submission_count=snapshot.provider_submission_count,
        derived_artifact_cleanup_intent=tuple(cleanup_intent),
        consequence_fingerprint=fingerprint,
    )


def derived_artifacts_outcome(
    cover_state: CleanupState, preview_state: CleanupState
) -> Literal["none", "pending", "partial", "complete"]:
    states = {cover_state, preview_state}
    actionable = {state for state in states if state != "none"}
    if not actionable:
        return "none"
    if actionable <= {"complete"}:
        return "complete"
    if "pending" in actionable or "failed" in actionable:
        if "complete" in actionable:
            return "partial"
        return "pending"
    return "partial"


class CatalogRemovalRepository(Protocol):
    def load_snapshot(self, media_id: MediaId) -> CatalogRemovalSnapshot | None:
        """Load removal-relevant state or None when absent."""

    def remove_catalog_media(
        self,
        *,
        media_id: MediaId,
        expected_fingerprint: str,
        request_id: str,
        actor_key: str,
        now_ms: int,
    ) -> CatalogRemovalReceipt:
        """Atomically remove one catalog aggregate and insert a durable receipt."""

    def get_receipt(self, receipt_id: str) -> CatalogRemovalReceipt | None:
        """Return one receipt by identity."""

    def update_cleanup_states(
        self,
        receipt_id: str,
        *,
        cover_cleanup_state: CleanupState,
        preview_cleanup_state: CleanupState,
        now_ms: int,
    ) -> CatalogRemovalReceipt:
        """Persist derived-artifact cleanup outcomes for one receipt."""


class DerivedArtifactCleanup(Protocol):
    def cleanup_cover(
        self, *, media_id: str, artifact_digest: str | None
    ) -> CleanupState:
        """Remove exclusive cover artifacts; missing is complete."""

    def cleanup_previews(self, *, location_ids_json: str | None) -> CleanupState:
        """Remove exclusive gallery-preview caches for location IDs."""


@dataclass(frozen=True, slots=True)
class CatalogMediaRemovalService:
    """Preview, execute, and retry administrator catalog removal."""

    repository: CatalogRemovalRepository
    cleanup: DerivedArtifactCleanup
    now_ms: Callable[[], int]

    def preview(self, media_id: str) -> CatalogRemovalPreview:
        snapshot = self._require_snapshot(media_id)
        return build_preview(snapshot)

    def execute(
        self,
        *,
        media_id: str,
        acknowledge_consequences: object,
        consequence_fingerprint: object,
        request_id: str,
        actor_key: str,
    ) -> CatalogRemovalResult:
        if acknowledge_consequences is not True:
            raise CatalogRemovalValidationError(
                "Catalog removal acknowledgment is required."
            )
        if (
            not isinstance(consequence_fingerprint, str)
            or len(consequence_fingerprint) != 64
            or consequence_fingerprint != consequence_fingerprint.lower()
            or any(character not in "0123456789abcdef" for character in consequence_fingerprint)
        ):
            raise CatalogRemovalValidationError(
                "Catalog removal consequence fingerprint is invalid."
            )
        parsed = MediaId.from_string(media_id)
        receipt = self.repository.remove_catalog_media(
            media_id=parsed,
            expected_fingerprint=consequence_fingerprint,
            request_id=request_id,
            actor_key=actor_key,
            now_ms=self.now_ms(),
        )
        receipt = self._run_cleanup(receipt)
        outcome = derived_artifacts_outcome(
            receipt.cover_cleanup_state, receipt.preview_cleanup_state
        )
        return CatalogRemovalResult(
            catalog_state="removed",
            receipt=receipt,
            derived_artifacts_outcome=outcome,
            cleanup_retry_available=outcome in {"pending", "partial"},
        )

    def retry_cleanup(self, receipt_id: str) -> CatalogRemovalResult:
        receipt = self.repository.get_receipt(receipt_id)
        if receipt is None:
            raise CatalogRemovalNotFoundError("Catalog removal receipt not found.")
        receipt = self._run_cleanup(receipt)
        outcome = derived_artifacts_outcome(
            receipt.cover_cleanup_state, receipt.preview_cleanup_state
        )
        return CatalogRemovalResult(
            catalog_state="removed",
            receipt=receipt,
            derived_artifacts_outcome=outcome,
            cleanup_retry_available=outcome in {"pending", "partial"},
        )

    def _require_snapshot(self, media_id: str) -> CatalogRemovalSnapshot:
        parsed = MediaId.from_string(media_id)
        snapshot = self.repository.load_snapshot(parsed)
        if snapshot is None:
            raise CatalogRemovalNotFoundError("Media not found.")
        return snapshot

    def _run_cleanup(self, receipt: CatalogRemovalReceipt) -> CatalogRemovalReceipt:
        cover_state = receipt.cover_cleanup_state
        preview_state = receipt.preview_cleanup_state
        if cover_state in {"pending", "failed"}:
            cover_state = self.cleanup.cleanup_cover(
                media_id=receipt.media_id,
                artifact_digest=receipt.cover_artifact_digest,
            )
        if preview_state in {"pending", "failed"}:
            preview_state = self.cleanup.cleanup_previews(
                location_ids_json=receipt.preview_location_ids_json
            )
        if (
            cover_state == receipt.cover_cleanup_state
            and preview_state == receipt.preview_cleanup_state
        ):
            return receipt
        return self.repository.update_cleanup_states(
            receipt.id,
            cover_cleanup_state=cover_state,
            preview_cleanup_state=preview_state,
            now_ms=self.now_ms(),
        )
