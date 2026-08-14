"""Pydantic v2 response/request schemas for the study HTTP API.

Single source of truth for wire shapes. The routers return plain dicts;
FastAPI's ``response_model`` coerces + documents them. Frontend types are
generated from ``/openapi.json`` (openapi-typescript), so renaming a field
here must be mirrored by regenerating the client types.

Field names follow the API wire contract (not necessarily the core model):
- ``round_num`` (never ``round`` — ``round`` shadows the builtin)
- ``factor_failures`` / ``verdict_reason`` / ``error`` present on every
  round shape so the UI can render failure detail without optional checks.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# ── small shared shapes ─────────────────────────────────────────────


class MetricTargetModel(BaseModel):
    name: str
    op: str = ">="
    value: float


class DirectiveModel(BaseModel):
    directive_id: str
    content: str
    issued_by: Optional[str] = None
    created_at: Optional[str] = None
    consumed_at: Optional[str] = None


# ── round shape (shared by summary + rounds list) ───────────────────


class StudyRoundModel(BaseModel):
    round_num: int
    run_name: str
    verdict: str
    metrics: dict = Field(default_factory=dict)
    review: Optional[dict] = None
    error: Optional[str] = None
    factor_failures: list = Field(default_factory=list)
    verdict_reason: Optional[str] = None
    created_at: Optional[str] = None


# ── study list item ────────────────────────────────────────────────


class StudyListItem(BaseModel):
    study_id: str
    session_id: str
    goal_id: Optional[str] = None
    objective: str
    strategy_name: str
    workspace_path: str
    execution_status: str
    current_round: int
    last_verdict: Optional[str] = None
    last_metrics: Optional[dict] = None
    last_error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None


# ── goal snapshot (flattened, not the raw GoalStore shape) ─────────


class GoalCriterionModel(BaseModel):
    criterion_id: Optional[str] = None
    text: Optional[str] = None
    status: Optional[str] = None
    required: bool = True


class GoalSnapshotModel(BaseModel):
    goal_id: Optional[str] = None
    goal_status: Optional[str] = None
    objective: Optional[str] = None
    progress_percent: float = 0.0
    evidence_count: int = 0
    criteria: list[GoalCriterionModel] = Field(default_factory=list)


# ── endpoint response models ───────────────────────────────────────


class StudyListResponse(BaseModel):
    status: str
    studies: list[StudyListItem]
    next_cursor: Optional[str] = None


class StudyStatusResponse(BaseModel):
    status: str
    session_id: Optional[str] = None
    study_id: Optional[str] = None
    goal_id: Optional[str] = None
    execution_status: Optional[str] = None
    current_round: Optional[int] = None
    objective: Optional[str] = None
    workspace_path: Optional[str] = None
    strategy_name: Optional[str] = None
    metric_targets: Optional[list[MetricTargetModel]] = None
    last_metrics: Optional[dict] = None
    last_verdict: Optional[str] = None
    last_error: Optional[str] = None
    last_traceback: Optional[str] = None
    heartbeat: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    goal_snapshot: Optional[GoalSnapshotModel] = None


class StudySummaryResponse(BaseModel):
    status: str
    study_id: str
    execution_status: str
    current_round: int
    max_rounds: Optional[int] = None
    objective: str
    strategy_name: str
    workspace_path: str
    metric_targets: Optional[list[MetricTargetModel]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    last_metrics: Optional[dict] = None
    last_verdict: Optional[str] = None
    last_error: Optional[str] = None
    last_traceback: Optional[str] = None
    recent_rounds: list[StudyRoundModel] = Field(default_factory=list)
    scoreboard: list = Field(default_factory=list)
    goal_snapshot: Optional[GoalSnapshotModel] = None
    monitor_state: Optional[dict] = None


class StudyRoundsResponse(BaseModel):
    status: str
    study_id: str
    total: int
    offset: int
    limit: int
    rounds: list[StudyRoundModel]


class StudyDirectivesResponse(BaseModel):
    status: str
    study_id: str
    directives: list[DirectiveModel]


class StudyActionResponse(BaseModel):
    status: str
    study_id: str
    action: str


class StudyStartResponse(BaseModel):
    status: str
    study_id: str
    goal_id: Optional[str] = None
    session_id: str
    execution_status: str
    executor_type: str


class StudyDirectiveCreatedResponse(BaseModel):
    status: str
    study_id: str
    directive_id: str
    created_at: Optional[str] = None


class StudyJournalResponse(BaseModel):
    status: str
    study_id: str
    journal: str


class StudyGuidanceResponse(BaseModel):
    status: str
    study_id: str
    source: Optional[str] = None
    task_scope: Optional[bool] = None
    gates: list = Field(default_factory=list)
    body: Optional[str] = None
    text: str


class StudyRoundSummaryMdResponse(BaseModel):
    status: str
    study_id: str
    round: int
    summary_md: str


# ── Phase 3: round detail / artifacts / diff / adopt ────────────────


class ArtifactItem(BaseModel):
    path: str
    size: int
    mtime: Optional[str] = None


class StudyRoundArtifactsResponse(BaseModel):
    status: str
    study_id: str
    round: int
    round_dir: str
    artifacts: list[ArtifactItem]


class StudyRoundManifestResponse(BaseModel):
    status: str
    study_id: str
    round: int
    manifest: dict


class DiffLine(BaseModel):
    line: str
    kind: str  # "context" | "add" | "del"


class StudyRoundDiffResponse(BaseModel):
    status: str
    study_id: str
    round_a: int
    round_b: int
    diff: list[DiffLine]
    stats: dict


class StudyAdoptResponse(BaseModel):
    status: str
    study_id: str
    round: int
    adopted_run_dir: str
    note: str


# ── Phase 4: per-study hanging events (observability in the UI) ─────


class HangingEventItem(BaseModel):
    event_type: str
    study_id: Optional[str] = None
    session_id: Optional[str] = None
    detail: Optional[str] = None
    created_at: float
    created_at_iso: str


class StudyHangingEventsResponse(BaseModel):
    status: str
    study_id: str
    window_hours: float
    by_type: dict = Field(default_factory=dict)
    recent: list[HangingEventItem] = Field(default_factory=list)


# ── Phase 5: action matrix (state-machine v2) ───────────────────────


class StudyActionItem(BaseModel):
    name: str
    label: str
    destructive: bool = False


class StudyAvailableActionsResponse(BaseModel):
    status: str
    study_id: str
    execution_status: str
    actions: list[StudyActionItem]


class StudyActionRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=512)


class StudyRedoRequest(BaseModel):
    round_num: int = Field(..., ge=1)
