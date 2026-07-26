"""Request models and the response envelope.

Finding rows deliberately stay list[dict]: they originate in dbx_platform's
fetch/classify functions, and re-modeling each row shape here would create a
second schema that drifts from the package. Only payloads the app itself
composes get typed models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, model_validator


def envelope(data: Any, as_of: datetime, was_cached: bool) -> dict:
    return {
        "data": data,
        "count": len(data) if isinstance(data, list) else None,
        "as_of": as_of.isoformat(),
        "cached": was_cached,
    }


class ApplyRequest(BaseModel):
    plan_id: str
    confirm: str


class ActionPlanRequest(BaseModel):
    action_type: str = Field(
        min_length=1,
        max_length=100,
        validation_alias=AliasChoices("action_type", "action"),
    )
    parameters: dict[str, Any] = Field(default_factory=dict)


class ActionApprovalRequest(BaseModel):
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: str | None = Field(
        default=None,
        validation_alias=AliasChoices("confirmation", "confirm"),
    )


class ActionRejectRequest(BaseModel):
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(default=None, max_length=1000)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class ChatPageContext(BaseModel):
    """Bounded display context; never valid as an executor/tool payload."""

    route: str = Field(default="/", min_length=1, max_length=200, pattern=r"^/")
    query: str = Field(default="", max_length=1000)
    focus_action_id: str | None = Field(default=None, min_length=1, max_length=100)
    filters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    selected_resources: list[dict[str, str]] = Field(
        default_factory=list,
        max_length=20,
    )

    @model_validator(mode="after")
    def bounded_context(self):
        if len(self.filters) > 30:
            raise ValueError("Assistant context accepts at most 30 filters.")
        if any(len(str(key)) > 100 for key in self.filters):
            raise ValueError("Assistant context filter names are too long.")
        for resource in self.selected_resources:
            if len(resource) > 10 or any(
                len(str(key)) > 100 or len(str(value)) > 500
                for key, value in resource.items()
            ):
                raise ValueError("Assistant selected-resource context is too large.")
        return self


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=50)
    context: ChatPageContext = Field(default_factory=ChatPageContext)


class AgentExecutionStage(BaseModel):
    """One server-observed segment of a read-only assistant execution."""

    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    category: Literal[
        "foundry_agent",
        "databricks_retrieval",
        "llm_synthesis",
    ]
    start_ms: float = Field(ge=0)
    duration_ms: float | None = Field(default=None, ge=0)
    detail: str | None = Field(default=None, max_length=500)


class AgentExecutionTrace(BaseModel):
    """Bounded timing metadata; unavailable streaming timings remain null."""

    total_ms: float | None = Field(default=None, ge=0)
    ttft_ms: float | None = Field(default=None, ge=0)
    tpot_ms: float | None = Field(default=None, ge=0)
    timing_source: Literal["server", "unavailable"] = "unavailable"
    stages: list[AgentExecutionStage] = Field(default_factory=list, max_length=50)


class ComplianceMetric(BaseModel):
    """A ratio backed by explicit, currently attested resource evidence."""

    id: Literal[
        "zdr",
        "content_safety",
        "access_control",
        "audit_logging",
        "rate_limit_headroom",
    ]
    label: str = Field(min_length=1, max_length=100)
    value_pct: float | None = Field(default=None, ge=0, le=100)
    compliant_resources: int = Field(ge=0)
    evaluated_resources: int = Field(ge=0)
    total_resources: int = Field(ge=0)
    evidence_note: str = Field(min_length=1, max_length=500)


class ZdrAlert(BaseModel):
    """An explicit zero-data-retention control failure, never an inference."""

    resource_id: str = Field(min_length=1, max_length=1000)
    resource_name: str = Field(min_length=1, max_length=300)
    scope: str = Field(min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=100)
    status: Literal["disabled"] = "disabled"
    remediation: str = Field(min_length=1, max_length=1000)


class AiCompliancePosture(BaseModel):
    """Cross-provider AI control posture returned to both governance views."""

    metrics: list[ComplianceMetric] = Field(min_length=5, max_length=5)
    zdr_alerts: list[ZdrAlert] = Field(default_factory=list)
    unverified_zdr_resources: int = Field(ge=0)
    evaluated_resources: int = Field(ge=0)


AttributionMethod = Literal[
    "DIRECT_METADATA",
    "DIRECT_TAG",
    "DIRECT_RESOURCE",
    "CONFLICT",
    "SHARED_UNALLOCATED",
    "UNATTRIBUTED",
]


class CostLedger(BaseModel):
    """One additive partition; different bases/currencies remain separate."""

    id: str
    source: str
    title: str
    amount: float
    currency: str
    pricing_basis: str
    attributed_cost: float
    unallocated_cost: float
    coverage_start: str | None = None
    coverage_end: str | None = None
    freshness: str | None = None
    scope: list[str] = Field(default_factory=list)
    status: str = "unavailable"
    trusted: bool = False
    trend_pct: float | None = None


class ApplicationTagHealth(BaseModel):
    status: Literal["matched", "missing", "conflict"]
    matched: int = Field(ge=0)
    missing: int = Field(ge=0)
    conflicts: int = Field(ge=0)


class ApplicationSummary(BaseModel):
    application_key: str
    display_name: str
    environments: list[str]
    sources: list[str]
    ledgers: list[CostLedger]
    trend_pct: float | None = None
    tag_health: ApplicationTagHealth
    coverage_pct: float | None = Field(default=None, ge=0, le=100)
    last_evidence_at: str | None = None


class ApplicationFacets(BaseModel):
    environments: list[str]
    sources: list[str]


class ApplicationListData(BaseModel):
    applications: list[ApplicationSummary]
    facets: ApplicationFacets
    next_cursor: str | None = None


class ApplicationIdentity(BaseModel):
    application_key: str
    display_name: str
    environments: list[str]
    sources: list[str]
    last_evidence_at: str | None = None


class ApplicationPeriod(BaseModel):
    window: Literal["7d", "30d", "90d"]
    days: Literal[7, 30, 90]
    start: str
    end: str


class ApplicationSeriesPoint(BaseModel):
    usage_date: str
    ledger_id: str
    source: str
    currency: str
    pricing_basis: str
    cost: float


class ApplicationDriverPathNode(BaseModel):
    dimension: Literal["source", "service", "resource", "workload"]
    key: str
    label: str


class ApplicationDriver(BaseModel):
    source: str
    ledger_id: str
    dimension: Literal["resource"] = "resource"
    name: str
    resource_type: str
    resource_id: str
    service: str
    workload: str
    path: list[ApplicationDriverPathNode]
    cost: float
    currency: str
    pricing_basis: str
    attribution_method: AttributionMethod


class TagAlignment(BaseModel):
    source: str
    resource_id: str
    resource_name: str
    tag_key: str | None = None
    raw_value: str | None = None
    normalized_value: str | None = None
    status: Literal["matched", "missing", "conflict"]
    observed_cost: float | None = None
    evidence_id: str | None = None
    scope: str
    freshness: str | None = None


class ApplicationCoverage(BaseModel):
    source: str
    status: Literal["healthy", "conflict", "no_data", "partial", "unavailable"]
    attributed_rows: int = Field(ge=0)
    total_rows: int = Field(ge=0)
    attributed_cost: float
    total_cost: float
    currency: str
    pricing_basis: str
    coverage_pct: float | None = Field(default=None, ge=0, le=100)


class UnallocatedCostPool(BaseModel):
    source: str
    reason: str
    cost: float
    currency: str
    pricing_basis: str
    row_count: int = Field(ge=0)


class SourceHealth(BaseModel):
    source: str
    status: str
    last_success_at: str | None = None
    coverage_start: str | None = None
    coverage_end: str | None = None
    notes: str
    subscription_id: str | None = None
    scope_filter: str | None = None
    scope: str
    observed_at: str | None = None
    freshness: str | None = None


class ApplicationTagObservation(BaseModel):
    evidence_id: str
    tag_key: str
    tag_value: str
    normalized_value: str | None = None
    observed_cost: float


class ApplicationProfileData(BaseModel):
    application: ApplicationIdentity
    period: ApplicationPeriod
    ledgers: list[CostLedger]
    series: list[ApplicationSeriesPoint]
    drivers: list[ApplicationDriver]
    tag_alignment: list[TagAlignment]
    coverage: list[ApplicationCoverage]
    unallocated: list[UnallocatedCostPool]
    source_health: list[SourceHealth]


class ApplicationEvidence(BaseModel):
    evidence_id: str
    usage_date: str
    source: str
    environment: str
    resource_type: str
    resource_id: str
    resource_name: str
    resource_group: str = ""
    resource_aliases: dict[str, Any] = Field(default_factory=dict)
    service: str
    workload: str
    application_key: str | None = None
    raw_application: str | None = None
    attribution_method: AttributionMethod
    tag_key: str | None = None
    tags: dict[str, Any] = Field(default_factory=dict)
    identity_tags: dict[str, Any] = Field(default_factory=dict)
    tag_observations: list[ApplicationTagObservation] = Field(default_factory=list)
    cost: float
    cost_known: bool = True
    inventory_only: bool = False
    currency: str
    pricing_basis: str
    evidence_at: str | None = None
    scope: str
    snapshot_id: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    job_id: str = ""
    run_id: str = ""
    trigger_type: str = ""
    unpriced_usage_quantity: float = 0
    conflict_values: list[str] = Field(default_factory=list)


class ApplicationEvidenceData(BaseModel):
    items: list[ApplicationEvidence]
    next_cursor: str | None = None


class ApplicationListEnvelope(BaseModel):
    data: ApplicationListData
    count: int
    as_of: str
    cached: bool


class ApplicationProfileEnvelope(BaseModel):
    data: ApplicationProfileData
    count: None = None
    as_of: str
    cached: bool


class ApplicationEvidenceEnvelope(BaseModel):
    data: ApplicationEvidenceData
    count: int
    as_of: str
    cached: bool
