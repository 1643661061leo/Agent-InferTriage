"""Canonical v1 evidence. Missing measurements are never silently replaced by zero."""

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Stage = Literal["orchestration", "prompt", "cache", "scheduling", "kernel"]
STAGES: tuple[Stage, ...] = ("orchestration", "prompt", "cache", "scheduling", "kernel")
Status = Literal["reported", "derived", "estimated", "unknown", "not_applicable"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Metric(Model):
    value: Any = None
    unit: str
    status: Status = "unknown"
    method: str = ""
    scope: str = ""
    source_refs: list[str] = Field(default_factory=list)
    reason: str = ""
    known_subtotal: int | str | None = None
    unknown_count: int = 0

    @model_validator(mode="after")
    def check_missing(self) -> "Metric":
        if self.status in ("unknown", "not_applicable") and self.value is not None:
            raise ValueError("Unknown/not_applicable metric must have a null value")
        return self


class Clock(Model):
    clock_id: str
    aligned_to: str | None = None
    offset_ns: int = 0
    uncertainty_ns: int = Field(default=0, ge=0)


class Source(Model):
    source_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    path: str
    format: str = "text"
    version: str = ""
    captured_at_ns: int | None = Field(default=None, ge=0)
    endpoint_id: str | None = None


class Workload(Model):
    workload_id: str
    name: str = ""
    start_ns: int | None = Field(default=None, ge=0)
    end_ns: int | None = Field(default=None, ge=0)
    clock_domain: str = "local"
    start_boundary: str = "unspecified"
    end_boundary: str = "unspecified"
    completion_status: Literal["completed", "failed", "cancelled", "unknown"] = "unknown"
    graph_complete: bool = False
    graph_provenance: Literal["observed", "declared", "inferred"] = "declared"
    cross_layer_links_verified: bool = False
    clock_alignment_verified: bool = False
    source_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def ordered(self) -> "Workload":
        if self.start_ns is not None and self.end_ns is not None and self.end_ns < self.start_ns:
            raise ValueError("Workload ends before it starts")
        return self


class Record(Model):
    record_id: str
    workload_id: str
    source_refs: list[str] = Field(default_factory=list)


class Span(Record):
    record_type: Literal["span"] = "span"
    span_id: str
    parent_id: str | None = None
    name: str = ""
    operation: str = "custom"
    observation_level: Literal["L0", "L2", "L3"] | None = None
    stages: list[Stage] = Field(default_factory=list)
    start_ns: int | None = Field(default=None, ge=0)
    end_ns: int | None = Field(default=None, ge=0)
    clock_domain: str = "local"
    agent_id: str | None = None
    round_id: str | None = None
    stage_occurrence_id: str | None = None
    request_id: str | None = None
    request_refs: list[str] = Field(default_factory=list)
    attempt_id: str | None = None
    engine_iteration_id: str | None = None
    batch_id: str | None = None
    kernel_invocation_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ordered(self) -> "Span":
        if self.start_ns is not None and self.end_ns is not None and self.end_ns < self.start_ns:
            raise ValueError("Span ends before it starts")
        return self


class Activity(Record):
    record_type: Literal["activity"] = "activity"
    activity_id: str
    scope_span_id: str | None = None
    duration_ns: int = Field(ge=0)
    release_ns: int = Field(default=0, ge=0)
    stages: list[Stage] = Field(default_factory=list)
    clock_domain: str = "local"
    execution_key: str | None = None
    provenance: Literal["observed", "declared", "inferred"] = "declared"


class Dependency(Record):
    record_type: Literal["dependency"] = "dependency"
    from_id: str
    to_id: str
    min_lag_ns: int = Field(default=0, ge=0)
    provenance: Literal["observed", "declared", "inferred"] = "declared"


class Usage(Record):
    record_type: Literal["usage"] = "usage"
    request_id: str
    attempt_id: str = "0"
    span_id: str | None = None
    agent_id: str | None = None
    round_id: str | None = None
    provider: str = "unknown"
    model: str = "unknown"
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    usage_status: Literal["reported", "estimated", "unknown"] = "reported"
    raw_usage: dict[str, Any] = Field(default_factory=dict)
    normalized_semantics: Literal["inclusive_v1", "unknown"] = "inclusive_v1"

    @model_validator(mode="after")
    def subsets(self) -> "Usage":
        if self.input_tokens is not None:
            for value in (self.cache_read_tokens, self.cache_write_tokens):
                if value is not None and value > self.input_tokens:
                    raise ValueError("Cache subset exceeds inclusive input tokens")
        if (
            self.reasoning_tokens is not None
            and self.output_tokens is not None
            and self.reasoning_tokens > self.output_tokens
        ):
            raise ValueError("Reasoning subset exceeds inclusive output tokens")
        return self


class CacheSample(Record):
    record_type: Literal["cache"] = "cache"
    cache_type: str
    scope: str
    unit: str
    hits: int | None = Field(default=None, ge=0)
    queries: int | None = Field(default=None, ge=0)
    occupancy: float | None = Field(default=None, ge=0)
    capacity: float | None = Field(default=None, ge=0)
    sample_kind: Literal["delta", "gauge", "cumulative"] = "delta"
    interval_start_ns: int | None = None
    interval_end_ns: int | None = None
    round_id: str | None = None
    agent_id: str | None = None
    request_id: str | None = None
    attempt_id: str | None = None

    @model_validator(mode="after")
    def valid_counts(self) -> "CacheSample":
        if self.hits is not None and self.queries is not None and self.hits > self.queries:
            raise ValueError("Cache hits exceed queries")
        return self


class CostItem(Record):
    record_type: Literal["cost"] = "cost"
    owner_event: str
    amount: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: str
    method: Literal["billed", "estimated"] = "estimated"
    basis: Literal["primary", "alternative", "breakdown"] = "primary"
    pricing_ref: str


class ContentBlock(Record):
    record_type: Literal["content"] = "content"
    request_id: str
    attempt_id: str = "0"
    role: str
    text: str | None = None
    token_ids: list[int] | None = None
    token_count: int | None = Field(default=None, ge=0)
    tokenizer_ref: str | None = None
    template_ref: str | None = None
    content_key: str | None = None


RecordType = Span | Activity | Dependency | Usage | CacheSample | CostItem | ContentBlock
RECORD_TYPES: dict[str, type[Record]] = {
    "span": Span,
    "activity": Activity,
    "dependency": Dependency,
    "usage": Usage,
    "cache": CacheSample,
    "cost": CostItem,
    "content": ContentBlock,
}


class Manifest(Model):
    schema_version: Literal["1.0"] = "1.0"
    workloads: list[Workload]
    sources: list[Source] = Field(default_factory=list)
    clock_domains: list[Clock] = Field(default_factory=lambda: [Clock(clock_id="local")])
    events_file: str = "events.jsonl"
    content_policy: Literal["metadata", "local", "model_allowed"] = "metadata"


class EvidenceSet(Model):
    manifest: Manifest
    spans: list[Span] = Field(default_factory=list)
    activities: list[Activity] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    usages: list[Usage] = Field(default_factory=list)
    caches: list[CacheSample] = Field(default_factory=list)
    costs: list[CostItem] = Field(default_factory=list)
    contents: list[ContentBlock] = Field(default_factory=list)
    sources: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    import_report: dict[str, Any] = Field(default_factory=dict)
    service_series: list[dict[str, Any]] = Field(default_factory=list)


class AnalysisConfig(Model):
    context_min_calls: int = Field(default=3, ge=2)
    context_growth_ratio: float = Field(default=1.5, ge=1)
    context_growth_tokens: int = Field(default=2048, ge=0)
    retry_min_calls: int = Field(default=3, ge=2)
    price_file: str | None = None


class Finding(Model):
    finding_id: str
    rule_id: str
    title: str
    scope_refs: list[str]
    evidence_refs: list[str]
    metric_refs: list[str] = Field(default_factory=list)
    owner_layers: list[str]
    evidence_level: str = "observed"
    observation_levels: list[str] = Field(default_factory=list)
    confidence_reason: str
    counter_evidence: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    benefit: dict[str, Any] = Field(default_factory=lambda: {"status": "unknown"})
    quality_risk: str = "Task correctness must be checked before changing behavior."
    next_check: str


class AnalysisResult(Model):
    schema_version: str = "1.0"
    workloads: list[dict[str, Any]]
    metrics: dict[str, Metric]
    findings: list[Finding]
    evidence_index: dict[str, Any]
    config_digest: str
    warnings: list[str]
    service_metrics: list[dict[str, Any]] = Field(default_factory=list)
