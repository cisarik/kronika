"""Domain types for durable media analysis runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from kronika.domain.analysis_identities import accepted_durable_identity
from kronika.domain.identities import MediaId, MediaLocationId


class MediaAnalysisRunState(str, Enum):
    """Persisted lifecycle states for one analysis run."""

    PENDING = "pending"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    FAILED = "failed"


AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION = "automatic_post_catalog"
RESULT_SCHEMA_VERSION = "framenest-media-suggestion-result-v1"
#: The spelling the writer cut will emit. No writer uses it yet; readers accept
#: it so rows written after that cut stay visible to readers written before it.
CANONICAL_RESULT_SCHEMA_VERSION = "kronika-media-suggestion-result-v1"
ACCEPTED_RESULT_SCHEMA_VERSIONS = accepted_durable_identity(
    RESULT_SCHEMA_VERSION, CANONICAL_RESULT_SCHEMA_VERSION
)
DEFAULT_MAX_ANALYSIS_ATTEMPTS = 3
MAX_CONFIGURED_ANALYSIS_ATTEMPTS = 10

ACTIVE_ANALYSIS_RUN_STATES = frozenset(
    {
        MediaAnalysisRunState.PENDING,
        MediaAnalysisRunState.ANALYZING,
    }
)
TERMINAL_ANALYSIS_RUN_STATES = frozenset(
    {
        MediaAnalysisRunState.ANALYZED,
        MediaAnalysisRunState.FAILED,
    }
)
ACTIVE_ANALYSIS_RUN_STATE_VALUES = frozenset(
    state.value for state in ACTIVE_ANALYSIS_RUN_STATES
)


@dataclass(frozen=True, slots=True)
class MediaAnalysisRunId:
    """Opaque durable identity for one analysis run."""

    value: str

    def to_string(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class MediaAnalysisRun:
    """One durable analysis lifecycle record.

    ``analysis_definition`` identifies the analysis profile/workflow key
    (for example ``automatic_post_catalog`` or ``movie_identification``).
    It is not request-trigger provenance. Automatic versus manual intent is
    expressed by the create path, not by rewriting the definition string.
    """

    id: MediaAnalysisRunId
    media_id: MediaId
    media_location_id: MediaLocationId
    analysis_definition: str
    state: MediaAnalysisRunState
    attempt_count: int
    provider_id: str | None
    model_id: str | None
    prompt_version: str | None
    result_schema_version: str | None
    result_json: str | None
    error_code: str | None
    error_message: str | None
    created_at_ms: int
    started_at_ms: int | None
    completed_at_ms: int | None
    version: int
    analysis_profile: str | None = None
    reasoning_enabled: bool | None = None
    derivative_strategy: str | None = None
    derivative_count: int | None = None
    provider_submission_occurred: bool | None = None
    supersedes_run_id: MediaAnalysisRunId | None = None
