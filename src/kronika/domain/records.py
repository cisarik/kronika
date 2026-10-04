"""Pure Kronika record and completed-document values.

No SQLAlchemy, FastAPI, pydantic, or capture imports. Error text is fixed
and never includes document text, citations, or private-state content.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import re
import unicodedata
from typing import Self

from kronika.domain.identities import FrameNestIdentityError, MediaId
from kronika.domain.identity_access import (
    FrameNestIdentityAccessError,
    normalize_login,
)
from kronika.domain.research import (
    MAX_ANSWER_UTF8_BYTES,
    MAX_CITATION_COUNT,
    MAX_CITATION_TITLE_BYTES,
    MAX_CITATION_URL_BYTES,
    MAX_PROMPT_UTF8_BYTES,
    CompletionEvidence,
    ResearchCitation,
    ResearchValueError,
    completion_error,
)

MAX_OPERATION_TOKEN_LENGTH = 128
_UUID_TEXT = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_RECORD_MESSAGE = "Record value is invalid."
_STORAGE_MESSAGE = "Stored record is invalid."


class RecordValueError(ValueError):
    """Sanitized rejection of an invalid record or document value."""

    def __init__(self) -> None:
        super().__init__(_RECORD_MESSAGE)


class RecordStorageIntegrityError(RuntimeError):
    """Sanitized refusal of persisted record data that cannot be revalidated."""

    def __init__(self) -> None:
        super().__init__(_STORAGE_MESSAGE)


class RecordNotFoundError(LookupError):
    """Sanitized absence. Callers must use the same response for denial."""

    def __init__(self) -> None:
        super().__init__("Record was not found.")


class RecordConflictError(RuntimeError):
    """Optimistic version, digest, or binding conflict."""

    def __init__(self) -> None:
        super().__init__("Record changed.")


class RecordKind(str, Enum):
    MEDIA = "media"
    SEARCH = "search"
    RESEARCH = "research"


class RecordVisibility(str, Enum):
    PRIVATE = "private"
    FAMILY = "family"


@dataclass(frozen=True, slots=True, repr=False)
class _RecordUuid:
    _value: str

    def __post_init__(self) -> None:
        try:
            parsed = MediaId.from_string(self._value)
        except FrameNestIdentityError as exc:
            raise RecordValueError() from exc
        object.__setattr__(self, "_value", parsed.to_string())

    @classmethod
    def new(cls) -> Self:
        return cls(MediaId.new().to_string())

    @classmethod
    def from_string(cls, value: object) -> Self:
        if not isinstance(value, str):
            raise RecordValueError()
        return cls(value)

    def to_string(self) -> str:
        return self._value

    def __str__(self) -> str:
        return self._value


class RecordId(_RecordUuid):
    """UUIDv4 identity of one common record."""

    __slots__ = ()


class DocumentId(_RecordUuid):
    """UUIDv4 identity of one completed question/answer document."""

    __slots__ = ()


def parse_operation_token(value: object) -> str:
    """Return a bounded operation token that is not UUID-formatted."""
    if not isinstance(value, str):
        raise RecordValueError()
    if not value or len(value) > MAX_OPERATION_TOKEN_LENGTH:
        raise RecordValueError()
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise RecordValueError()
    if _UUID_TEXT.fullmatch(value.casefold()):
        raise RecordValueError()
    encoded = value.encode("utf-8")
    if not 1 <= len(encoded) <= MAX_OPERATION_TOKEN_LENGTH:
        raise RecordValueError()
    return value


def parse_owner_login_key(value: object) -> str:
    """Normalize a server-derived owner through the identity function."""
    try:
        return normalize_login(value)
    except FrameNestIdentityAccessError as exc:
        raise RecordValueError() from exc


def _utf8_length(value: str) -> int:
    return len(value.encode("utf-8"))


def _nonblank_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecordValueError()
    size = _utf8_length(value)
    if size < 1 or size > maximum:
        raise RecordValueError()
    return value


@dataclass(frozen=True, slots=True)
class NormalizedCitation:
    """One citation stored with a completed document."""

    url: str
    title: str

    def __post_init__(self) -> None:
        try:
            citation = ResearchCitation(url=self.url, title=self.title)
        except ResearchValueError as exc:
            raise RecordValueError() from exc
        object.__setattr__(self, "url", citation.url)
        object.__setattr__(self, "title", citation.title)

    def as_json_object(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url}


@dataclass(frozen=True, slots=True)
class CompletedDocument:
    """Immutable completed Search or Research question and answer."""

    document_id: DocumentId
    operation_id: str
    kind: RecordKind
    question_text: str
    answer_text: str
    citations: tuple[NormalizedCitation, ...]
    evidence: CompletionEvidence
    created_at_ms: int
    completed_at_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, DocumentId):
            raise RecordValueError()
        object.__setattr__(self, "operation_id", parse_operation_token(self.operation_id))
        if self.kind not in {RecordKind.SEARCH, RecordKind.RESEARCH}:
            raise RecordValueError()
        object.__setattr__(
            self,
            "question_text",
            _nonblank_text(self.question_text, maximum=MAX_PROMPT_UTF8_BYTES),
        )
        object.__setattr__(
            self,
            "answer_text",
            _nonblank_text(self.answer_text, maximum=MAX_ANSWER_UTF8_BYTES),
        )
        if type(self.citations) is not tuple or len(self.citations) > MAX_CITATION_COUNT:
            raise RecordValueError()
        if any(not isinstance(item, NormalizedCitation) for item in self.citations):
            raise RecordValueError()
        if not isinstance(self.evidence, CompletionEvidence):
            raise RecordValueError()
        if completion_error(self.evidence) is not None:
            raise RecordValueError()
        _require_timestamp(self.created_at_ms)
        _require_timestamp(self.completed_at_ms)
        if self.completed_at_ms < self.created_at_ms:
            raise RecordValueError()

    def citations_json(self) -> str:
        return json.dumps(
            [item.as_json_object() for item in self.citations],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def evidence_json(self) -> str:
        payload = {
            "answer_complete": self.evidence.answer_complete,
            "incomplete_marker": self.evidence.incomplete_marker,
            "provider_terminal": self.evidence.provider_terminal,
            "refusal_marker": self.evidence.refusal_marker,
            "web_search_executed": self.evidence.web_search_executed,
        }
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def document_from_storage(
    *,
    document_id: object,
    operation_id: object,
    kind: object,
    question_text: object,
    answer_text: object,
    citations_json: object,
    completion_evidence_json: object,
    created_at_ms: object,
    completed_at_ms: object,
) -> CompletedDocument:
    """Revalidate a persisted document or fail closed without rewriting it."""
    try:
        parsed_kind = RecordKind(kind)
        citations = _citations_from_json(citations_json)
        evidence = _evidence_from_json(completion_evidence_json)
        return CompletedDocument(
            document_id=DocumentId.from_string(document_id),
            operation_id=str(operation_id) if isinstance(operation_id, str) else "",
            kind=parsed_kind,
            question_text=question_text if isinstance(question_text, str) else "",
            answer_text=answer_text if isinstance(answer_text, str) else "",
            citations=citations,
            evidence=evidence,
            created_at_ms=created_at_ms if isinstance(created_at_ms, int) else -1,
            completed_at_ms=completed_at_ms if isinstance(completed_at_ms, int) else -1,
        )
    except (RecordValueError, ResearchValueError, ValueError) as exc:
        raise RecordStorageIntegrityError() from exc


def _citations_from_json(value: object) -> tuple[NormalizedCitation, ...]:
    if not isinstance(value, str):
        raise RecordValueError()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RecordValueError() from exc
    if type(parsed) is not list or len(parsed) > MAX_CITATION_COUNT:
        raise RecordValueError()
    citations: list[NormalizedCitation] = []
    for item in parsed:
        if type(item) is not dict or set(item) != {"url", "title"}:
            raise RecordValueError()
        url = item["url"]
        title = item["title"]
        if not isinstance(url, str) or not isinstance(title, str):
            raise RecordValueError()
        if _utf8_length(url) > MAX_CITATION_URL_BYTES:
            raise RecordValueError()
        if _utf8_length(title) > MAX_CITATION_TITLE_BYTES:
            raise RecordValueError()
        citations.append(NormalizedCitation(url=url, title=title))
    canonical = json.dumps(
        [item.as_json_object() for item in citations],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if canonical != value:
        raise RecordValueError()
    return tuple(citations)


def _evidence_from_json(value: object) -> CompletionEvidence:
    if not isinstance(value, str):
        raise RecordValueError()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RecordValueError() from exc
    expected = {
        "answer_complete",
        "incomplete_marker",
        "provider_terminal",
        "refusal_marker",
        "web_search_executed",
    }
    if type(parsed) is not dict or set(parsed) != expected:
        raise RecordValueError()
    if any(type(item) is not bool for item in parsed.values()):
        raise RecordValueError()
    canonical = json.dumps(parsed, separators=(",", ":"), sort_keys=True)
    if canonical != value:
        raise RecordValueError()
    return CompletionEvidence(
        provider_terminal=parsed["provider_terminal"],
        answer_complete=parsed["answer_complete"],
        web_search_executed=parsed["web_search_executed"],
        refusal_marker=parsed["refusal_marker"],
        incomplete_marker=parsed["incomplete_marker"],
    )


def _require_timestamp(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RecordValueError()
    return value


@dataclass(frozen=True, slots=True)
class ApprovedTagSnapshot:
    key: str
    display_name: str
    position: int


@dataclass(frozen=True, slots=True)
class ApprovedLocationSnapshot:
    location_id: str
    library_id: str
    relative_path: str
    availability: str
    observed_size_bytes: int | None
    observed_mtime_ns: int | None


@dataclass(frozen=True, slots=True)
class ApprovalProjection:
    """Normalized snapshot frozen at approval. Relational rows are derived from it."""

    schema_version: int
    record_kind: RecordKind
    record_version: int
    analysis_run_id: str | None
    media_id: str | None
    display_title: str | None
    description: str | None
    content_category: str | None
    acquisition_source: str | None
    creator_attribution_kind: str | None
    creator_stable_id: str | None
    creator_handle: str | None
    creator_display_name: str | None
    cover_artifact_digest: str | None
    tags: tuple[ApprovedTagSnapshot, ...]
    locations: tuple[ApprovedLocationSnapshot, ...]
    analysis_result: dict[str, object] | None
    document: CompletedDocument | None

    def canonical_json(self) -> str:
        payload = {
            "acquisition_source": self.acquisition_source,
            "analysis_result": self.analysis_result,
            "analysis_run_id": self.analysis_run_id,
            "content_category": self.content_category,
            "cover_artifact_digest": self.cover_artifact_digest,
            "creator_attribution_kind": self.creator_attribution_kind,
            "creator_display_name": self.creator_display_name,
            "creator_handle": self.creator_handle,
            "creator_stable_id": self.creator_stable_id,
            "description": self.description,
            "display_title": self.display_title,
            "document": None
            if self.document is None
            else {
                "answer_text": self.document.answer_text,
                "citations": [item.as_json_object() for item in self.document.citations],
                "completed_at_ms": self.document.completed_at_ms,
                "created_at_ms": self.document.created_at_ms,
                "document_id": self.document.document_id.to_string(),
                "evidence": json.loads(self.document.evidence_json()),
                "kind": self.document.kind.value,
                "operation_id": self.document.operation_id,
                "question_text": self.document.question_text,
            },
            "locations": [
                {
                    "availability": item.availability,
                    "library_id": item.library_id,
                    "location_id": item.location_id,
                    "observed_mtime_ns": item.observed_mtime_ns,
                    "observed_size_bytes": item.observed_size_bytes,
                    "relative_path": item.relative_path,
                }
                for item in self.locations
            ],
            "media_id": self.media_id,
            "record_kind": self.record_kind.value,
            "record_version": self.record_version,
            "schema_version": self.schema_version,
            "tags": [
                {
                    "display_name": item.display_name,
                    "key": item.key,
                    "position": item.position,
                }
                for item in self.tags
            ],
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def digest(self) -> str:
        import hashlib

        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def projection_from_storage(value: object) -> ApprovalProjection:
    """Revalidate a stored projection or fail closed."""
    if not isinstance(value, str) or not value:
        raise RecordStorageIntegrityError()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RecordStorageIntegrityError() from exc
    if type(parsed) is not dict:
        raise RecordStorageIntegrityError()
    try:
        document_payload = parsed.get("document")
        document = None
        if document_payload is not None:
            if type(document_payload) is not dict:
                raise RecordValueError()
            evidence = document_payload["evidence"]
            document = document_from_storage(
                document_id=document_payload["document_id"],
                operation_id=document_payload["operation_id"],
                kind=document_payload["kind"],
                question_text=document_payload["question_text"],
                answer_text=document_payload["answer_text"],
                citations_json=json.dumps(
                    document_payload["citations"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                completion_evidence_json=json.dumps(
                    evidence, separators=(",", ":"), sort_keys=True
                ),
                created_at_ms=document_payload["created_at_ms"],
                completed_at_ms=document_payload["completed_at_ms"],
            )
        tags = tuple(
            ApprovedTagSnapshot(
                key=str(item["key"]),
                display_name=str(item["display_name"]),
                position=int(item["position"]),
            )
            for item in parsed.get("tags") or []
        )
        locations = tuple(
            ApprovedLocationSnapshot(
                location_id=str(item["location_id"]),
                library_id=str(item["library_id"]),
                relative_path=str(item["relative_path"]),
                availability=str(item["availability"]),
                observed_size_bytes=item["observed_size_bytes"],
                observed_mtime_ns=item["observed_mtime_ns"],
            )
            for item in parsed.get("locations") or []
        )
        projection = ApprovalProjection(
            schema_version=int(parsed["schema_version"]),
            record_kind=RecordKind(parsed["record_kind"]),
            record_version=int(parsed["record_version"]),
            analysis_run_id=parsed.get("analysis_run_id"),
            media_id=parsed.get("media_id"),
            display_title=parsed.get("display_title"),
            description=parsed.get("description"),
            content_category=parsed.get("content_category"),
            acquisition_source=parsed.get("acquisition_source"),
            creator_attribution_kind=parsed.get("creator_attribution_kind"),
            creator_stable_id=parsed.get("creator_stable_id"),
            creator_handle=parsed.get("creator_handle"),
            creator_display_name=parsed.get("creator_display_name"),
            cover_artifact_digest=parsed.get("cover_artifact_digest"),
            tags=tags,
            locations=locations,
            analysis_result=parsed.get("analysis_result"),
            document=document,
        )
    except (KeyError, TypeError, ValueError, RecordValueError) as exc:
        raise RecordStorageIntegrityError() from exc
    if projection.canonical_json() != value:
        raise RecordStorageIntegrityError()
    return projection
