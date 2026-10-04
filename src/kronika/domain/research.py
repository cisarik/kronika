"""Pure provider-neutral research values.

No framework, persistence, transport, or provider SDK imports. Error values
carry stable codes only. They never carry raw provider text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

MICRO_USD_SCALE = 1_000_000

MAX_PROMPT_UTF8_BYTES = 16_384
MAX_PROVIDER_RESPONSE_BYTES = 8_388_608
MAX_ANSWER_UTF8_BYTES = 2_097_152
MAX_CITATION_COUNT = 200
MAX_CITATION_URL_BYTES = 2_048
MAX_CITATION_TITLE_BYTES = 300

MAX_SEARCH_TOOL_CALLS = 3
MAX_RESEARCH_TOOL_CALLS = 20
MAX_SEARCH_OUTPUT_TOKENS = 4_096
MAX_RESEARCH_OUTPUT_TOKENS = 32_768
MAX_SEARCH_DEADLINE_SECONDS = 180
MAX_RESEARCH_DEADLINE_SECONDS = 1_800
MAX_SEARCH_RESERVATION_MICRO_USD = 500_000
MAX_RESEARCH_RESERVATION_MICRO_USD = 5_000_000
MAX_DAILY_BUDGET_MICRO_USD = 10_000_000
MAX_MONTHLY_BUDGET_MICRO_USD = 30_000_000

WEB_SEARCH_TOOL = "web_search"
ALLOWED_RESEARCH_TOOLS = frozenset({WEB_SEARCH_TOOL})
ALLOWED_REASONING_EFFORTS = frozenset({"low", "high"})

OPENAI_RESPONSES_PROVIDER_ID = "openai-responses"
CHATGPT_PAGE_PROVIDER_ID = "chatgpt-page"
SELF_HOSTED_PROVIDER_EXTENSION = "self-hosted"
FIXED_OPENAI_RESPONSES_MODEL_ID = "gpt-5.5-2026-04-23"
RESEARCH_CONFIGURATION_VERSION = "3"
UNSHIPPED_ADAPTER_VERSION = "0"


class ResearchValueError(ValueError):
    """Invalid pure research value. The message is fixed and sanitized."""

    def __init__(self) -> None:
        super().__init__("research value is invalid.")


class ResearchOperationKind(str, Enum):
    """Search and research are separate operation kinds."""

    SEARCH = "search"
    RESEARCH = "research"


class ResearchLifecycleState(str, Enum):
    """Application lifecycle for one research operation.

    Remote cleanup and accounting are separate state machines.
    """

    ADMITTED = "admitted"
    SUBMITTING = "submitting"
    RUNNING = "running"
    VALIDATING = "validating"
    SAVED = "saved"
    REFUSED = "refused"
    FAILED = "failed"
    INCOMPLETE = "incomplete"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    SUBMISSION_UNKNOWN = "submission_unknown"


NONTERMINAL_RESEARCH_LIFECYCLE_STATES = frozenset(
    {
        ResearchLifecycleState.ADMITTED,
        ResearchLifecycleState.SUBMITTING,
        ResearchLifecycleState.RUNNING,
        ResearchLifecycleState.VALIDATING,
        ResearchLifecycleState.CANCEL_REQUESTED,
    }
)
TERMINAL_RESEARCH_LIFECYCLE_STATES = frozenset(
    {
        ResearchLifecycleState.SAVED,
        ResearchLifecycleState.REFUSED,
        ResearchLifecycleState.FAILED,
        ResearchLifecycleState.INCOMPLETE,
        ResearchLifecycleState.CANCELLED,
        ResearchLifecycleState.TIMEOUT,
        ResearchLifecycleState.SUBMISSION_UNKNOWN,
    }
)


class ResearchRemoteCleanupState(str, Enum):
    """Remote-response deletion tracked apart from the generation lifecycle."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    DELETED = "deleted"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ResearchAccountingState(str, Enum):
    """Budget accounting tracked apart from the generation lifecycle."""

    RESERVED = "reserved"
    RECONCILED = "reconciled"
    UNKNOWN = "unknown"


class ProviderAvailability(str, Enum):
    """Catalog availability. Configuration is not proof of live readiness."""

    DISABLED = "disabled"
    PARKED = "parked"
    UNCONFIGURED = "unconfigured"
    CONFIGURED = "configured"


class ExecutionLocation(str, Enum):
    """Where the model and web-search loop runs."""

    PROVIDER_NATIVE = "provider_native"
    PARKED = "parked"


class RetentionPosture(str, Enum):
    """Accepted retention. Remote deletion is not zero-data retention."""

    STANDARD_REMOTE_DELETE = "standard_remote_delete"
    NOT_APPLICABLE = "not_applicable"


class SubmissionIdempotency(str, Enum):
    """Provider submission idempotency. Not-guaranteed is an explicit value."""

    NOT_GUARANTEED = "not_guaranteed"
    GUARANTEED = "guaranteed"


class ProviderObservationKind(str, Enum):
    """Outcome classes returned by a research provider port."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    REFUSED = "refused"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


class ResearchErrorCode(str, Enum):
    """Stable sanitized research errors. No raw provider text belongs here."""

    DISABLED = "E_DISABLED"
    NOT_CONFIGURED = "E_NOT_CONFIGURED"
    CAPABILITY_UNAVAILABLE = "E_CAPABILITY_UNAVAILABLE"
    INVALID_REQUEST = "E_INVALID_REQUEST"
    BUSY = "E_BUSY"
    IDEMPOTENCY_CONFLICT = "E_IDEMPOTENCY_CONFLICT"
    AUTH = "E_AUTH"
    RATE_LIMIT = "E_RATE_LIMIT"
    PROVIDER_QUOTA = "E_PROVIDER_QUOTA"
    PROVIDER_UNAVAILABLE = "E_PROVIDER_UNAVAILABLE"
    REFUSED = "E_REFUSED"
    INVALID_RESULT = "E_INVALID_RESULT"
    INCOMPLETE_RESULT = "E_INCOMPLETE_RESULT"
    RESULT_TOO_LARGE = "E_RESULT_TOO_LARGE"
    NO_WEB_EVIDENCE = "E_NO_WEB_EVIDENCE"
    TIMEOUT = "E_TIMEOUT"
    CANCELLED = "E_CANCELLED"
    SUBMISSION_UNKNOWN = "E_SUBMISSION_UNKNOWN"
    RESULT_EXPIRED = "E_RESULT_EXPIRED"
    ACCOUNTING_UNKNOWN = "E_ACCOUNTING_UNKNOWN"
    BUDGET_EXCEEDED = "E_BUDGET_EXCEEDED"
    STORAGE = "E_STORAGE"


RESEARCH_ERROR_CODES = frozenset(code.value for code in ResearchErrorCode)


def is_terminal_lifecycle(state: ResearchLifecycleState) -> bool:
    """Return whether generation will not continue from this state."""
    return state in TERMINAL_RESEARCH_LIFECYCLE_STATES


def _require_bool(value: object) -> bool:
    if type(value) is not bool:
        raise ResearchValueError
    return value


def _require_non_negative_int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ResearchValueError
    return value


def _require_positive_int(value: object, *, maximum: int) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        raise ResearchValueError
    return value


def _utf8_length(value: str) -> int:
    return len(value.encode("utf-8"))


def _require_token(value: object, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ResearchValueError
    if not value or len(value) > maximum or any(character.isspace() for character in value):
        raise ResearchValueError
    return value


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """What one provider descriptor claims it can do."""

    search: bool
    research: bool
    native_research: bool
    cancellation: bool
    remote_retrieval: bool
    remote_deletion: bool

    def __post_init__(self) -> None:
        _require_bool(self.search)
        _require_bool(self.research)
        _require_bool(self.native_research)
        _require_bool(self.cancellation)
        _require_bool(self.remote_retrieval)
        _require_bool(self.remote_deletion)

    def supports(self, kind: ResearchOperationKind) -> bool:
        if kind is ResearchOperationKind.SEARCH:
            return self.search
        if kind is ResearchOperationKind.RESEARCH:
            return self.research
        raise ResearchValueError


@dataclass(frozen=True, slots=True)
class AccountingCapabilities:
    """Usage the provider may report. Missing usage is not success."""

    reports_token_usage: bool
    reports_cached_input_separately: bool
    reports_web_tool_calls: bool
    missing_usage_is_visible_failure: bool

    def __post_init__(self) -> None:
        _require_bool(self.reports_token_usage)
        _require_bool(self.reports_cached_input_separately)
        _require_bool(self.reports_web_tool_calls)
        _require_bool(self.missing_usage_is_visible_failure)


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Network-free description of one research provider."""

    provider_id: str
    adapter_version: str
    configuration_version: str
    capabilities: ProviderCapabilities
    execution_location: ExecutionLocation
    retention_posture: RetentionPosture
    accounting: AccountingCapabilities
    submission_idempotency: SubmissionIdempotency
    availability: ProviderAvailability

    def __post_init__(self) -> None:
        _require_token(self.provider_id, maximum=64)
        _require_token(self.adapter_version, maximum=32)
        _require_token(self.configuration_version, maximum=32)
        if not isinstance(self.capabilities, ProviderCapabilities):
            raise ResearchValueError
        if not isinstance(self.execution_location, ExecutionLocation):
            raise ResearchValueError
        if not isinstance(self.retention_posture, RetentionPosture):
            raise ResearchValueError
        if not isinstance(self.accounting, AccountingCapabilities):
            raise ResearchValueError
        if not isinstance(self.submission_idempotency, SubmissionIdempotency):
            raise ResearchValueError
        if not isinstance(self.availability, ProviderAvailability):
            raise ResearchValueError


@dataclass(frozen=True, slots=True)
class ServerSelectedProfile:
    """Provider profile chosen by the server at admission.

    Clients do not supply endpoint, model, or tool fields. This profile is a
    copy of the server configuration snapshot.
    """

    provider_id: str
    model_id: str
    configuration_version: str
    reasoning_effort: str
    tool_allowlist: tuple[str, ...]
    background: bool
    max_tool_calls: int
    max_output_tokens: int
    deadline_seconds: int
    budget_reservation_usd_micros: int

    def __post_init__(self) -> None:
        _require_token(self.provider_id, maximum=64)
        _require_token(self.model_id, maximum=120)
        _require_token(self.configuration_version, maximum=32)
        if self.reasoning_effort not in ALLOWED_REASONING_EFFORTS:
            raise ResearchValueError
        if type(self.tool_allowlist) is not tuple or not self.tool_allowlist:
            raise ResearchValueError
        if any(tool not in ALLOWED_RESEARCH_TOOLS for tool in self.tool_allowlist):
            raise ResearchValueError
        _require_bool(self.background)
        _require_positive_int(self.max_tool_calls, maximum=MAX_RESEARCH_TOOL_CALLS)
        _require_positive_int(self.max_output_tokens, maximum=MAX_RESEARCH_OUTPUT_TOKENS)
        _require_positive_int(self.deadline_seconds, maximum=MAX_RESEARCH_DEADLINE_SECONDS)
        _require_positive_int(
            self.budget_reservation_usd_micros,
            maximum=MAX_RESEARCH_RESERVATION_MICRO_USD,
        )


@dataclass(frozen=True, slots=True)
class ApprovedResourceLimits:
    """Server-approved bounds copied onto one request."""

    max_tool_calls: int
    max_output_tokens: int
    budget_reservation_usd_micros: int
    prompt_max_utf8_bytes: int
    answer_max_utf8_bytes: int
    citation_count_max: int

    def __post_init__(self) -> None:
        _require_positive_int(self.max_tool_calls, maximum=MAX_RESEARCH_TOOL_CALLS)
        _require_positive_int(self.max_output_tokens, maximum=MAX_RESEARCH_OUTPUT_TOKENS)
        _require_positive_int(
            self.budget_reservation_usd_micros,
            maximum=MAX_RESEARCH_RESERVATION_MICRO_USD,
        )
        _require_positive_int(self.prompt_max_utf8_bytes, maximum=MAX_PROMPT_UTF8_BYTES)
        _require_positive_int(self.answer_max_utf8_bytes, maximum=MAX_ANSWER_UTF8_BYTES)
        _require_positive_int(self.citation_count_max, maximum=MAX_CITATION_COUNT)


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """One bounded provider submission.

    Owner identity stays in the application. This value has no endpoint, no
    client model, and no client tool fields.
    """

    operation_id: str
    kind: ResearchOperationKind
    prompt: str
    profile: ServerSelectedProfile
    deadline_seconds: int
    resource_limits: ApprovedResourceLimits

    def __post_init__(self) -> None:
        _require_token(self.operation_id, maximum=128)
        if not isinstance(self.kind, ResearchOperationKind):
            raise ResearchValueError
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ResearchValueError
        if not isinstance(self.profile, ServerSelectedProfile):
            raise ResearchValueError
        if not isinstance(self.resource_limits, ApprovedResourceLimits):
            raise ResearchValueError
        _require_positive_int(self.deadline_seconds, maximum=MAX_RESEARCH_DEADLINE_SECONDS)
        if _utf8_length(self.prompt) > self.resource_limits.prompt_max_utf8_bytes:
            raise ResearchValueError


@dataclass(frozen=True, slots=True)
class ProviderHandle:
    """Opaque remote handle. It is not a credential or a URL to fetch."""

    value: str

    def __post_init__(self) -> None:
        _require_token(self.value, maximum=256)


@dataclass(frozen=True, slots=True)
class ResearchUsage:
    """Token and tool counts. Reasoning tokens are a subset of output tokens.

    ``cache_write_input_tokens`` is optional because a provider may not report
    the separate cache-write field. Absence is retained as absence; it is never
    fabricated as zero.
    """

    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    web_tool_calls: int
    cache_write_input_tokens: int | None = None

    def __post_init__(self) -> None:
        _require_non_negative_int(self.input_tokens)
        _require_non_negative_int(self.cached_input_tokens)
        _require_non_negative_int(self.output_tokens)
        _require_non_negative_int(self.reasoning_tokens)
        _require_non_negative_int(self.web_tool_calls)
        if self.cache_write_input_tokens is not None:
            _require_non_negative_int(self.cache_write_input_tokens)
        if self.cached_input_tokens > self.input_tokens:
            raise ResearchValueError
        if self.reasoning_tokens > self.output_tokens:
            raise ResearchValueError
        if (
            self.cache_write_input_tokens is not None
            and self.cached_input_tokens + self.cache_write_input_tokens
            > self.input_tokens
        ):
            raise ResearchValueError


@dataclass(frozen=True, slots=True)
class UsageTokenPrices:
    """One tier of integer micro-USD prices per million tokens."""

    input_micro_usd_per_million: int
    cached_input_micro_usd_per_million: int
    cache_write_input_micro_usd_per_million: int
    output_micro_usd_per_million: int

    def __post_init__(self) -> None:
        _require_non_negative_int(self.input_micro_usd_per_million)
        _require_non_negative_int(self.cached_input_micro_usd_per_million)
        _require_non_negative_int(self.cache_write_input_micro_usd_per_million)
        _require_non_negative_int(self.output_micro_usd_per_million)


@dataclass(frozen=True, slots=True)
class UsagePriceSchedule:
    """Integer micro-USD prices per million tokens, plus web search per thousand.

    The four original fields describe the short-context tier. New catalog
    schedules add an explicit cache-write rate, a long-context threshold and a
    long-context tier. The three added fields may be absent only for explicitly
    supported legacy schedules. Web-search pricing stays on the parent schedule.
    """

    input_micro_usd_per_million: int
    cached_input_micro_usd_per_million: int
    output_micro_usd_per_million: int
    web_search_micro_usd_per_thousand: int
    cache_write_input_micro_usd_per_million: int | None = None
    long_context_threshold_tokens: int | None = None
    long_context: UsageTokenPrices | None = None

    def __post_init__(self) -> None:
        _require_non_negative_int(self.input_micro_usd_per_million)
        _require_non_negative_int(self.cached_input_micro_usd_per_million)
        _require_non_negative_int(self.output_micro_usd_per_million)
        _require_non_negative_int(self.web_search_micro_usd_per_thousand)
        if self.cache_write_input_micro_usd_per_million is not None:
            _require_non_negative_int(self.cache_write_input_micro_usd_per_million)
        if self.long_context_threshold_tokens is not None:
            _require_non_negative_int(self.long_context_threshold_tokens)
        if self.long_context is not None and not isinstance(
            self.long_context, UsageTokenPrices
        ):
            raise ResearchValueError


@dataclass(frozen=True, slots=True)
class ResearchCitation:
    """One citation the user may open. The application does not fetch it."""

    url: str
    title: str

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or not isinstance(self.title, str):
            raise ResearchValueError
        if not self.url or any(character.isspace() for character in self.url):
            raise ResearchValueError
        if _utf8_length(self.url) > MAX_CITATION_URL_BYTES:
            raise ResearchValueError
        if _utf8_length(self.title) > MAX_CITATION_TITLE_BYTES:
            raise ResearchValueError


@dataclass(frozen=True, slots=True)
class CompletionEvidence:
    """Facts required before a result can be treated as complete."""

    provider_terminal: bool
    answer_complete: bool
    web_search_executed: bool
    refusal_marker: bool
    incomplete_marker: bool

    def __post_init__(self) -> None:
        _require_bool(self.provider_terminal)
        _require_bool(self.answer_complete)
        _require_bool(self.web_search_executed)
        _require_bool(self.refusal_marker)
        _require_bool(self.incomplete_marker)


def completion_error(evidence: CompletionEvidence) -> ResearchErrorCode | None:
    """Return the typed failure for evidence that is not a successful result."""
    if not isinstance(evidence, CompletionEvidence):
        raise ResearchValueError
    if evidence.refusal_marker:
        return ResearchErrorCode.REFUSED
    if (
        evidence.incomplete_marker
        or not evidence.answer_complete
        or not evidence.provider_terminal
    ):
        return ResearchErrorCode.INCOMPLETE_RESULT
    if not evidence.web_search_executed:
        return ResearchErrorCode.NO_WEB_EVIDENCE
    return None


@dataclass(frozen=True, slots=True)
class ResearchAnswer:
    """Full answer text or Markdown, citations, evidence, and usage.

    There is no trusted HTML field.
    """

    text: str
    citations: tuple[ResearchCitation, ...]
    evidence: CompletionEvidence
    usage: ResearchUsage | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text:
            raise ResearchValueError
        if _utf8_length(self.text) > MAX_ANSWER_UTF8_BYTES:
            raise ResearchValueError
        if type(self.citations) is not tuple:
            raise ResearchValueError
        if len(self.citations) > MAX_CITATION_COUNT:
            raise ResearchValueError
        if any(not isinstance(citation, ResearchCitation) for citation in self.citations):
            raise ResearchValueError
        if not isinstance(self.evidence, CompletionEvidence):
            raise ResearchValueError
        if self.usage is not None and not isinstance(self.usage, ResearchUsage):
            raise ResearchValueError


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    """One provider outcome. Error values are codes, never provider bodies."""

    kind: ProviderObservationKind
    remote_handle: ProviderHandle | None = None
    answer: ResearchAnswer | None = None
    error_code: ResearchErrorCode | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProviderObservationKind):
            raise ResearchValueError
        if self.remote_handle is not None and not isinstance(self.remote_handle, ProviderHandle):
            raise ResearchValueError
        if self.answer is not None and not isinstance(self.answer, ResearchAnswer):
            raise ResearchValueError
        if self.error_code is not None and not isinstance(self.error_code, ResearchErrorCode):
            raise ResearchValueError
        if self.kind is ProviderObservationKind.COMPLETE:
            if self.answer is None or self.remote_handle is None or self.error_code is not None:
                raise ResearchValueError
            if completion_error(self.answer.evidence) is not None:
                raise ResearchValueError
            return
        if self.answer is not None:
            raise ResearchValueError
        if self.kind in {ProviderObservationKind.PENDING, ProviderObservationKind.RUNNING}:
            if self.error_code is not None:
                raise ResearchValueError
            return
        if self.error_code is None:
            raise ResearchValueError


@dataclass(frozen=True, slots=True)
class CleanupOutcome:
    """Result of releasing one remote response."""

    state: ResearchRemoteCleanupState
    error_code: ResearchErrorCode | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, ResearchRemoteCleanupState):
            raise ResearchValueError
        if self.error_code is not None and not isinstance(self.error_code, ResearchErrorCode):
            raise ResearchValueError
        if self.state is ResearchRemoteCleanupState.FAILED and self.error_code is None:
            raise ResearchValueError
        if self.state is ResearchRemoteCleanupState.DELETED and self.error_code is not None:
            raise ResearchValueError


@dataclass(frozen=True, slots=True)
class ResearchRequestRecord:
    """Application request record. It does not carry a client endpoint or model."""

    operation_id: str
    kind: ResearchOperationKind
    prompt: str
    profile: ServerSelectedProfile
    deadline_seconds: int
    resource_limits: ApprovedResourceLimits
    state: ResearchLifecycleState
    cleanup_state: ResearchRemoteCleanupState
    accounting_state: ResearchAccountingState
    remote_handle: ProviderHandle | None
    error_code: ResearchErrorCode | None

    def __post_init__(self) -> None:
        _require_token(self.operation_id, maximum=128)
        if not isinstance(self.kind, ResearchOperationKind):
            raise ResearchValueError
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ResearchValueError
        if not isinstance(self.profile, ServerSelectedProfile):
            raise ResearchValueError
        _require_positive_int(self.deadline_seconds, maximum=MAX_RESEARCH_DEADLINE_SECONDS)
        if not isinstance(self.resource_limits, ApprovedResourceLimits):
            raise ResearchValueError
        if not isinstance(self.state, ResearchLifecycleState):
            raise ResearchValueError
        if not isinstance(self.cleanup_state, ResearchRemoteCleanupState):
            raise ResearchValueError
        if not isinstance(self.accounting_state, ResearchAccountingState):
            raise ResearchValueError
        if self.remote_handle is not None and not isinstance(self.remote_handle, ProviderHandle):
            raise ResearchValueError
        if self.error_code is not None and not isinstance(self.error_code, ResearchErrorCode):
            raise ResearchValueError


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    """One atomic reservation against daily and monthly thresholds."""

    operation_id: str
    kind: ResearchOperationKind
    reserved_usd_micros: int
    daily_limit_usd_micros: int
    monthly_limit_usd_micros: int

    def __post_init__(self) -> None:
        _require_token(self.operation_id, maximum=128)
        if not isinstance(self.kind, ResearchOperationKind):
            raise ResearchValueError
        _require_positive_int(
            self.reserved_usd_micros,
            maximum=MAX_RESEARCH_RESERVATION_MICRO_USD,
        )
        _require_positive_int(self.daily_limit_usd_micros, maximum=MAX_DAILY_BUDGET_MICRO_USD)
        _require_positive_int(
            self.monthly_limit_usd_micros,
            maximum=MAX_MONTHLY_BUDGET_MICRO_USD,
        )
        if self.daily_limit_usd_micros > self.monthly_limit_usd_micros:
            raise ResearchValueError


@dataclass(frozen=True, slots=True)
class BudgetHold:
    """Reservation result. Unknown accounting must not pretend the cost is zero."""

    operation_id: str
    reserved_usd_micros: int
    state: ResearchAccountingState

    def __post_init__(self) -> None:
        _require_token(self.operation_id, maximum=128)
        _require_positive_int(
            self.reserved_usd_micros,
            maximum=MAX_RESEARCH_RESERVATION_MICRO_USD,
        )
        if not isinstance(self.state, ResearchAccountingState):
            raise ResearchValueError


@dataclass(frozen=True, slots=True)
class BudgetReconciliation:
    """Usage reconciliation. A missing usage record is not stored as zero."""

    operation_id: str
    usage: ResearchUsage | None
    calculated_cost_usd_micros: int | None
    state: ResearchAccountingState

    def __post_init__(self) -> None:
        _require_token(self.operation_id, maximum=128)
        if self.usage is not None and not isinstance(self.usage, ResearchUsage):
            raise ResearchValueError
        if self.calculated_cost_usd_micros is not None:
            _require_non_negative_int(self.calculated_cost_usd_micros)
        if not isinstance(self.state, ResearchAccountingState):
            raise ResearchValueError
        if self.state is ResearchAccountingState.RECONCILED:
            if self.usage is None or self.calculated_cost_usd_micros is None:
                raise ResearchValueError
        if self.state is ResearchAccountingState.UNKNOWN and self.calculated_cost_usd_micros is not None:
            raise ResearchValueError


@dataclass(frozen=True, slots=True)
class ResultCompletion:
    """Local completion payload saved in one atomic transaction."""

    operation_id: str
    answer: ResearchAnswer
    remote_handle: ProviderHandle

    def __post_init__(self) -> None:
        _require_token(self.operation_id, maximum=128)
        if not isinstance(self.answer, ResearchAnswer):
            raise ResearchValueError
        if not isinstance(self.remote_handle, ProviderHandle):
            raise ResearchValueError
        if completion_error(self.answer.evidence) is not None:
            raise ResearchValueError


@dataclass(frozen=True, slots=True)
class ResultCompletionReceipt:
    """Proof that the local completion transaction reached saved."""

    operation_id: str
    state: ResearchLifecycleState

    def __post_init__(self) -> None:
        _require_token(self.operation_id, maximum=128)
        if self.state is not ResearchLifecycleState.SAVED:
            raise ResearchValueError


def ceiling_div(numerator: int, denominator: int) -> int:
    """Divide non-negative integers, rounding up."""
    if type(numerator) is not int or type(denominator) is not int:
        raise ResearchValueError
    if numerator < 0 or denominator <= 0:
        raise ResearchValueError
    if numerator == 0:
        return 0
    return (numerator + denominator - 1) // denominator


def token_cost_micro_usd(token_count: int, micro_usd_per_million: int) -> int:
    """Convert a token count to integer micro-USD, rounding up."""
    _require_non_negative_int(token_count)
    _require_non_negative_int(micro_usd_per_million)
    return ceiling_div(token_count * micro_usd_per_million, MICRO_USD_SCALE)


def add_micro_usd(*amounts: int) -> int:
    """Add integer micro-USD amounts."""
    total = 0
    for amount in amounts:
        total += _require_non_negative_int(amount)
    return total


def select_token_prices(
    schedule: UsagePriceSchedule,
    input_tokens: int,
) -> UsageTokenPrices | None:
    """Return the tier prices selected by total input tokens.

    ``None`` means the schedule does not cover that tier, so accounting is
    unknown rather than silently clamped. A legacy schedule may set a long
    threshold without a long tier.
    """
    if not isinstance(schedule, UsagePriceSchedule):
        raise ResearchValueError
    _require_non_negative_int(input_tokens)
    threshold = schedule.long_context_threshold_tokens
    if threshold is not None and input_tokens > threshold:
        return schedule.long_context
    return UsageTokenPrices(
        input_micro_usd_per_million=schedule.input_micro_usd_per_million,
        cached_input_micro_usd_per_million=schedule.cached_input_micro_usd_per_million,
        cache_write_input_micro_usd_per_million=(
            schedule.input_micro_usd_per_million
            if schedule.cache_write_input_micro_usd_per_million is None
            else schedule.cache_write_input_micro_usd_per_million
        ),
        output_micro_usd_per_million=schedule.output_micro_usd_per_million,
    )


def usage_cost_micro_usd(usage: ResearchUsage, schedule: UsagePriceSchedule) -> int:
    """Calculate integer micro-USD from reported usage.

    Cached input is billed only at the cached rate; cache-write input is billed
    only at the write rate; reasoning tokens are a subset of output tokens and
    are never added again. When the write rate differs from the ordinary input
    rate and the write count is absent, or when the schedule does not cover the
    selected tier, accounting is unknown and this raises instead of fabricating
    a value. Callers must not store a zero for missing usage.
    """
    if not isinstance(usage, ResearchUsage) or not isinstance(schedule, UsagePriceSchedule):
        raise ResearchValueError
    prices = select_token_prices(schedule, usage.input_tokens)
    if prices is None:
        raise ResearchValueError
    write_rate = prices.cache_write_input_micro_usd_per_million
    if usage.cache_write_input_tokens is None:
        if write_rate != prices.input_micro_usd_per_million:
            raise ResearchValueError
        write_tokens = 0
    else:
        write_tokens = usage.cache_write_input_tokens
    if usage.cached_input_tokens + write_tokens > usage.input_tokens:
        raise ResearchValueError
    uncached_input = usage.input_tokens - usage.cached_input_tokens - write_tokens
    tool_cost = ceiling_div(
        usage.web_tool_calls * schedule.web_search_micro_usd_per_thousand,
        1000,
    )
    return add_micro_usd(
        token_cost_micro_usd(uncached_input, prices.input_micro_usd_per_million),
        token_cost_micro_usd(
            usage.cached_input_tokens, prices.cached_input_micro_usd_per_million
        ),
        token_cost_micro_usd(write_tokens, write_rate),
        token_cost_micro_usd(usage.output_tokens, prices.output_micro_usd_per_million),
        tool_cost,
    )


def usage_cost_micro_usd_or_none(
    usage: ResearchUsage | None,
    schedule: UsagePriceSchedule | None,
) -> int | None:
    """Return the cost, or ``None`` when accounting is unknown.

    Missing usage, a missing schedule and an uncovered tier are all explicit
    unknown accounting rather than a fabricated zero.
    """
    if usage is None or schedule is None:
        return None
    try:
        return usage_cost_micro_usd(usage, schedule)
    except ResearchValueError:
        return None
