import { useAuthStore } from '../stores/auth'

const API_BASE = '/api'

export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export interface GoalStatusResponse {
  status: string
  goal_id?: string
  goal_status?: string
  objective?: string
  session_id?: string
  progress_percent?: number
  criteria?: Criterion[]
  evidence_count?: number
}

export interface GoalStartResponse {
  status: string
  goal_id?: string
}

export interface GoalEvidenceResponse {
  status: string
  goal_id?: string
  evidence_id?: string
}

export interface GoalCompleteResponse {
  status: string
  goal_id?: string
  new_status?: string
}

export interface Criterion {
  criterion_id: string
  text: string
  status: string
  required: boolean
  evidence_count?: number
}

export interface GoalListItem {
  goal_id: string
  session_id: string
  goal_status: string
  objective: string
  workflow_id?: string | null
  created_at: string
}

export interface GoalListResponse {
  status: string
  goals: GoalListItem[]
}

class APIClient {
  private getToken(): string | null {
    return useAuthStore.getState().token
  }

  private async request<T>(
    path: string,
    options: RequestInit = {}
  ): Promise<T> {
    const token = this.getToken()
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...(options.headers as Record<string, string>),
    }
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }

    const res = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
    })

    if (res.status === 401) {
      useAuthStore.getState().logout()
      window.location.href = '/login'
      throw new Error('Unauthorized')
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      const detail = (err as any)?.detail
      const message =
        typeof detail === 'string' && detail ? detail : 'Request failed'
      throw new ApiError(res.status, message, detail)
    }

    // Parse the body defensively: 2xx with an empty body (e.g. future
    // 204 endpoints) must not throw on res.json().
    if (typeof res.text === 'function') {
      const text = await res.text()
      if (text) return JSON.parse(text) as T
      return undefined as T
    }
    return res.json()
  }

  get<T>(path: string): Promise<T> {
    return this.request<T>(path)
  }

  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: 'POST',
      body: body ? JSON.stringify(body) : undefined,
    })
  }

  put<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: 'PUT',
      body: body ? JSON.stringify(body) : undefined,
    })
  }

  patch<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: 'PATCH',
      body: body ? JSON.stringify(body) : undefined,
    })
  }

  delete<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: 'DELETE' })
  }

  // TODO(reuse): useSSE.ts currently hand-rolls its EventSource (token
  // parsing, params) instead of calling this helper. Unify once the
  // SSE hook refactor lands (see hooks/useSSE.ts connect()).
  sse(path: string, lastEventId?: string): EventSource {
    const token = this.getToken()
    const params = new URLSearchParams()
    if (token) params.set('token', token)
    if (lastEventId) params.set('Last-Event-ID', lastEventId)
    const qs = params.toString() ? `?${params}` : ''
    return new EventSource(`${API_BASE}${path}${qs}`)
  }

  // ── Goal API ──────────────────────────────────────────────────────

  goal = {
    getStatus: (sessionId: string) =>
      this.get<GoalStatusResponse>(`/goal/status?session_id=${sessionId}`),

    list: (params: { session_id?: string; status?: string; limit?: number } = {}) =>
      this.get<GoalListResponse>('/goal/list' + qs(params)),

    start: (sessionId: string, objective: string, criteria?: string[]) =>
      this.post<GoalStartResponse>('/goal/start', {
        session_id: sessionId,
        objective,
        criteria,
      }),

    evidence: (sessionId: string, text: string, criterionId?: string) =>
      this.post<GoalEvidenceResponse>('/goal/evidence', {
        session_id: sessionId,
        evidence: text,
        criterion_id: criterionId,
      }),

    complete: (sessionId: string, recap?: string) =>
      this.post<GoalCompleteResponse>('/goal/complete', {
        session_id: sessionId,
        outcome: 'complete',
        summary: recap,
      }),

    cancel: (sessionId: string, recap?: string) =>
      this.post<GoalCompleteResponse>('/goal/complete', {
        session_id: sessionId,
        outcome: 'cancelled',
        summary: recap,
      }),
  }

  // ── Study API (Phase 5) ──────────────────────────────────────────────

  study = {
    start: (body: StudyStartRequest) =>
      this.post<StudyStartResponse>('/study/start', body),

    status: (sessionId: string, studyId?: string) =>
      this.get<StudyStatusResponse>(
        `/study/status?session_id=${sessionId}` +
          (studyId ? `&study_id=${studyId}` : '')
      ),

    list: (params: { session_id?: string; status?: string; limit?: number } = {}) =>
      this.get<StudyListResponse>('/study/list' + qs(params)),

    pause: (studyId: string) => this.post<StudyControlResponse>(`/study/${studyId}/pause`),
    resume: (studyId: string) => this.post<StudyControlResponse>(`/study/${studyId}/resume`),
    cancel: (studyId: string) => this.post<StudyControlResponse>(`/study/${studyId}/cancel`),

    directive: (studyId: string, content: string, issuedBy?: string) =>
      this.post<StudyDirectiveResponse>(`/study/${studyId}/directive`, {
        content,
        issued_by: issuedBy,
      }),

    summary: (studyId: string) =>
      this.get<StudySummaryResponse>(`/study/${studyId}/summary`),

    directives: (studyId: string) =>
      this.get<StudyDirectivesResponse>(`/study/${studyId}/directives`),

    journal: (studyId: string) =>
      this.get<StudyJournalResponse>(`/study/${studyId}/journal`),

    roundArtifacts: (studyId: string, roundNum: number) =>
      this.get<StudyRoundArtifactsResponse>(`/study/${studyId}/rounds/${roundNum}/artifacts`),

    roundManifest: (studyId: string, roundNum: number) =>
      this.get<StudyRoundManifestResponse>(`/study/${studyId}/rounds/${roundNum}/manifest`),

    roundDiff: (studyId: string, roundNum: number, against: number) =>
      this.get<StudyRoundDiffResponse>(
        `/study/${studyId}/rounds/${roundNum}/diff?against=${against}`
      ),

    roundSummaryMd: (studyId: string, roundNum: number) =>
      this.get<StudyRoundSummaryMdResponse>(`/study/${studyId}/rounds/${roundNum}/summary_md`),

    adoptRound: (studyId: string, roundNum: number) =>
      this.post<StudyAdoptResponse>(`/study/${studyId}/rounds/${roundNum}/adopt`),

    hangingEvents: (studyId: string, hours = 24, limit = 20) =>
      this.get<StudyHangingEventsResponse>(
        `/study/${studyId}/hanging_events?hours=${hours}&limit=${limit}`
      ),

    availableActions: (studyId: string) =>
      this.get<StudyAvailableActionsResponse>(`/study/${studyId}/available_actions`),

    dispatchAction: (studyId: string, name: string, reason?: string) =>
      this.post<StudyActionResponse>(`/study/${studyId}/actions/${name}`, {
        reason,
      }),

    redoRound: (studyId: string, roundNum: number) =>
      this.post<StudyActionResponse>(`/study/${studyId}/rounds/${roundNum}/redo`),
  }

  run = {
    status: (
      workspacePath: string,
      strategyName: string,
      runName: string,
    ) =>
      this.get<RunStatusResponse>(
        `/run/status?workspace_path=${encodeURIComponent(workspacePath)}` +
          `&strategy_name=${encodeURIComponent(strategyName)}` +
          `&run_name=${encodeURIComponent(runName)}`,
      ),

    equity: (
      workspacePath: string,
      strategyName: string,
      runName: string,
      maxPoints = 2000,
    ) =>
      this.get<RunEquityResponse>(
        `/run/equity?workspace_path=${encodeURIComponent(workspacePath)}` +
          `&strategy_name=${encodeURIComponent(strategyName)}` +
          `&run_name=${encodeURIComponent(runName)}` +
          `&max_points=${maxPoints}`,
      ),

    list: (
      workspacePath: string,
      strategyName = '',
      limit = 20,
    ) =>
      this.get<RunListResponse>(
        `/run/list?workspace_path=${encodeURIComponent(workspacePath)}` +
          `&strategy_name=${encodeURIComponent(strategyName)}` +
          `&limit=${limit}`,
      ),
  }

  strategies = {
    list: (workspacePath: string) =>
      this.get<StrategiesListResponse>(
        `/strategies/list?workspace_path=${encodeURIComponent(workspacePath)}`,
      ),
  }

  // Tier 1 A1: permission handshake reply. Returns the gateway's
  // verdict acknowledgement ("ok" / "expired" / "invalid_action").
  permission = {
    respond: (body: {
      tool_call_id: string
      action: 'allow' | 'deny'
      permanent?: boolean
      reason?: string
    }) =>
      this.post<{ status: string }>(
        '/chat/permission/respond',
        {
          tool_call_id: body.tool_call_id,
          action: body.action,
          permanent: body.permanent ?? false,
          reason: body.reason ?? '',
        },
      ),
  }

  personas = {
    list: () => this.get<PersonasResponse>('/chat/personas'),
  }

  workflow = {
    list: () => this.get<WorkflowListResponse>('/goal/workflow/list'),

    graph: (name: string) =>
      this.get<WorkflowGraphResponse>(`/goal/workflow/${name}/graph`),

    start: (sessionId: string, workflowName: string, objective: string) =>
      this.post<WorkflowStartResponse>('/goal/workflow/start', {
        session_id: sessionId,
        workflow_name: workflowName,
        objective,
      }),

    status: (goalId: string) =>
      this.get<WorkflowStatusResponse>(
        `/goal/workflow/status?goal_id=${encodeURIComponent(goalId)}`,
      ),

    pause: (goalId: string) =>
      this.post<{ status: string; paused: boolean }>(
        `/goal/workflow/pause?goal_id=${encodeURIComponent(goalId)}`,
      ),

    resume: (goalId: string) =>
      this.post<{ status: string; resumed: boolean }>(
        `/goal/workflow/resume?goal_id=${encodeURIComponent(goalId)}`,
      ),
  }

  // ── Modular DAG workflows (docs/workflow-module-design.md) ──
  definitions = {
    list: () => this.get<DefinitionListResponse>('/goal/workflow/definitions'),

    get: (name: string) =>
      this.get<DefinitionGetResponse>(`/goal/workflow/definitions/${encodeURIComponent(name)}`),

    save: (payload: DefinitionPayload) =>
      this.post<{ status: string; name: string; path: string; nodes: number; edges: number }>(
        '/goal/workflow/definitions',
        payload,
      ),

    remove: (name: string) =>
      this.delete<{ status: string; deleted: string }>(
        `/goal/workflow/definitions/${encodeURIComponent(name)}`,
      ),

    copy: (name: string) =>
      this.post<{ status: string; name: string; path: string }>(
        `/goal/workflow/definitions/${encodeURIComponent(name)}/copy`,
      ),

    graph: (name: string) =>
      this.get<DefinitionGraphResponse>(
        `/goal/workflow/definitions/${encodeURIComponent(name)}/graph`,
      ),
  }

  // ── Orchestration chat (DAG-bound session + auto-save drafts) ──
  orchestrate = {
    session: (dagId: string) =>
      this.post<{ status: string; session_id: string }>(
        '/goal/workflow/orchestrate/session',
        { dag_id: dagId },
      ),

    saveDraft: (dagId: string, nodes: unknown[], edges: unknown[]) =>
      this.put<{ status: string }>('/goal/workflow/orchestrate/draft', {
        dag_id: dagId,
        nodes,
        edges,
      }),

    getDraft: (dagId: string) =>
      this.get<{ dag: { nodes: unknown[]; edges: unknown[] } | null }>(
        `/goal/workflow/orchestrate/draft/${encodeURIComponent(dagId)}`,
      ),

    clearDraft: (dagId: string) =>
      this.delete<{ status: string; cleared: string }>(
        `/goal/workflow/orchestrate/draft/${encodeURIComponent(dagId)}`,
      ),
  }

  sendOrchestrate = (sessionId: string, content: string) =>
    this.post<{ message_id: string; event_id: string; queue_length?: number }>(
      '/chat/send_async',
      {
        session_id: sessionId,
        content,
        agent_id: 'workflow_orchestrator',
      },
    )

  definitionRuns = {
    start: (sessionId: string, definitionName: string, objective: string, params?: Record<string, unknown>) =>
      this.post<DefinitionRunStartResponse>('/goal/workflow/start-definition', {
        session_id: sessionId,
        definition_name: definitionName,
        objective,
        params: params ?? {},
      }),

    approve: (runId: string, approved: boolean, edits?: Record<string, unknown>) =>
      this.post<DefinitionRunResponse>('/goal/workflow/approve', {
        run_id: runId,
        approved,
        edits,
      }),

    status: (runId: string) =>
      this.get<DefinitionRunResponse>(`/goal/workflow/run/${encodeURIComponent(runId)}/status`),

    detail: (runId: string) =>
      this.get<DefinitionRunDetailResponse>(`/goal/workflow/run/${encodeURIComponent(runId)}`),

    remove: (runId: string) =>
      this.delete<{ status: string; deleted: string }>(
        `/goal/workflow/run/${encodeURIComponent(runId)}`,
      ),
  }
}

// ── Study API types ──────────────────────────────────────────────────

export interface MetricTarget {
  name: string
  op: '>=' | '<=' | '>' | '<' | '=='
  value: number
}

export interface StudyStartRequest {
  session_id: string
  objective: string
  workspace_path: string
  strategy_name: string
  metric_targets?: MetricTarget[]
  budget_token?: number
  budget_turn?: number
  budget_time_seconds?: number
  cooldown_base?: number
  cooldown_jitter?: number
  min_cooldown?: number
  max_rounds?: number
  behavior?: string
  monitor_interval_seconds?: number
}

export interface StudyStartResponse {
  status: string
  study_id: string
  goal_id?: string
  execution_status: string
}

export interface StudyStatusResponse {
  status: string
  study_id?: string
  goal_id?: string
  execution_status?: string
  current_round?: number
  objective?: string
  workspace_path?: string
  strategy_name?: string
  metric_targets?: MetricTarget[]
  last_metrics?: Record<string, number> | null
  last_verdict?: string | null
  last_error?: string | null
  trace_id?: string
  heartbeat?: string
  created_at?: string
  updated_at?: string
  completed_at?: string | null
  goal_snapshot?: {
    goal_id?: string
    goal_status?: string
    objective?: string
    progress_percent?: number
    evidence_count?: number
    criteria?: Array<{
      criterion_id: string
      text: string
      status: string
      required: boolean
    }>
  } | null
}

export interface StudyListResponse {
  status: string
  studies: StudySummary[]
}

export interface StudySummary {
  study_id: string
  session_id: string
  goal_id?: string
  objective: string
  strategy_name: string
  workspace_path: string
  execution_status: string
  current_round: number
  last_verdict?: string | null
  last_metrics?: Record<string, number> | null
  last_error?: string | null
  created_at?: string
  updated_at?: string
  completed_at?: string | null
}

export interface StudyControlResponse {
  status: string
  study_id: string
  action: string
}

export interface StudyDirectiveResponse {
  status: string
  study_id: string
  directive_id: string
  created_at: string
}

export interface StudyDirectiveItem {
  directive_id: string
  content: string
  issued_by?: string
  created_at: string
  consumed_at?: string | null
}

export interface StudyDirectivesResponse {
  status: string
  study_id: string
  directives: StudyDirectiveItem[]
}

// ── Study Summary types ─────────────────────────────────────────────

export interface StudyRoundSummary {
  round_num: number
  run_name: string
  metrics: Record<string, number> | null
  verdict: string | null
  factor_failures?: FactorFailure[]
  created_at: string
}

export interface LeverScoreSummary {
  lever: string
  precision_mean: number
  attempts: number
  accepted: number
  reverted: number
}

export interface FactorFailure {
  factor_name: string
  factor_code: string
  error: string
  available_columns?: string[]
  suggested_fix?: string
}

export interface StudySummaryResponse {
  status: string
  study_id: string
  execution_status: string
  current_round: number
  max_rounds?: number
  objective: string
  strategy_name?: string
  workspace_path?: string
  last_metrics?: Record<string, number> | null
  last_verdict?: string | null
  created_at?: string
  updated_at?: string
  completed_at?: string | null
  recent_rounds: StudyRoundSummary[]
  scoreboard: LeverScoreSummary[]
  goal_snapshot?: {
    goal_id?: string
    goal_status?: string
    objective?: string
    progress_percent?: number
    evidence_count?: number
    criteria?: Array<{
      criterion_id: string
      text: string
      status: string
      required: boolean
    }>
  } | null
  monitor_state?: {
    drift_count: number
    last_check_at?: string | null
    interval_seconds?: number | null
  } | null
}

// ── Phase 3: round detail / artifacts / diff / adopt types ──────────

export interface StudyJournalResponse {
  status: string
  study_id: string
  journal: string
}

export interface ArtifactItem {
  path: string
  size: number
  mtime?: string
}

export interface StudyRoundArtifactsResponse {
  status: string
  study_id: string
  round: number
  round_dir: string
  artifacts: ArtifactItem[]
}

export interface StudyRoundManifestResponse {
  status: string
  study_id: string
  round: number
  manifest: Record<string, unknown>
}

export interface DiffLine {
  line: string
  kind: 'context' | 'add' | 'del'
}

export interface StudyRoundDiffResponse {
  status: string
  study_id: string
  round_a: number
  round_b: number
  diff: DiffLine[]
  stats: { adds: number; dels: number; context: number }
}

export interface StudyRoundSummaryMdResponse {
  status: string
  study_id: string
  round: number
  summary_md: string
}

export interface StudyAdoptResponse {
  status: string
  study_id: string
  round: number
  adopted_run_dir: string
  note: string
}

// ── Phase 4: per-study hanging events ───────────────────────────────

export interface HangingEventItem {
  event_type: string
  study_id?: string
  session_id?: string
  detail?: string
  created_at: number
  created_at_iso: string
}

export interface StudyHangingEventsResponse {
  status: string
  study_id: string
  window_hours: number
  by_type: Record<string, number>
  recent: HangingEventItem[]
}

export const HANGING_EVENT_LABELS: Record<string, string> = {
  wallclock_timeout: 'LLM 墙钟超时',
  log_stall: '日志停滞',
  no_progress: '无进展',
  circuit_breaker_open: '熔断器打开',
  watchdog_interrupt: '看门狗中断',
}

// ── Phase 5: action matrix ──────────────────────────────────────────

export interface StudyActionItem {
  name: string
  label: string
  destructive: boolean
}

export interface StudyAvailableActionsResponse {
  status: string
  study_id: string
  execution_status: string
  actions: StudyActionItem[]
}

// ── Flow types ─────────────────────────────────────────────────────

export type NodeStatus = 'pending' | 'running' | 'done'

export interface FlowNodeData {
  id: string
  label: string
  status: NodeStatus
  started_at?: string
  duration_ms?: number
}

// ── Run API types ──────────────────────────────────────────────────

export interface RunStatusResponse {
  status: string
  run: string
  metrics: Record<string, number | string>
}

export interface RunEquityPoint {
  timestamp: string | number
  capital?: number
  unrealized?: number
  equity: number
  positions?: number
}

export interface RunEquityResponse {
  status: string
  run: string
  equity: RunEquityPoint[]
}

export interface RunListItem {
  name: string
  metrics: Record<string, number | string>
}

export interface RunListResponse {
  status: string
  runs: RunListItem[]
}

// ── Strategies API types ───────────────────────────────────────────

export interface StrategyListItem {
  name: string
  has_strategy_py: boolean
  has_config_yaml: boolean
}

export interface StrategiesListResponse {
  strategies: StrategyListItem[]
}

// ── Workflow API types ─────────────────────────────────────────────

export interface WorkflowListItem {
  name: string
  description?: string
  path?: string
}

export interface WorkflowListResponse {
  status: string
  workflows: WorkflowListItem[]
}

// ── Chat personas API types ────────────────────────────────────────

export interface ChatPersona {
  id: string
  name: string
  description: string
}

export interface PersonasResponse {
  personas: ChatPersona[]
}

export interface WorkflowGraphResponse {
  status: string
  name: string
  description?: string
  nodes: Array<{ id: string; label: string }>
  edges: Array<{ source: string; target: string }>
}

export interface WorkflowStartResponse {
  status: string
  goal_id: string
  workflow_name: string
}

export interface WorkflowStatusResponse {
  status: string
  goal_id: string
  workflow_name: string
  progress?: {
    status?: string
    current_layer?: number
    total_layers?: number
    agents_completed?: number
    agents_total?: number
    evidence_count?: number
    paused?: boolean
    agent_statuses?: Record<string, string>
  }
}

function qs(params: Record<string, string | number | undefined>): string {
  const entries: string[] = []
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue
    entries.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
  }
  return entries.length ? `?${entries.join('&')}` : ''
}

// ── Modular DAG workflow types (docs/workflow-module-design.md) ──

export interface DefinitionNodeConfig {
  [key: string]: unknown
}

export interface DefinitionNode {
  id: string
  type: 'llm_agent' | 'planner' | 'evaluator' | 'approval' | 'python' | 'tool'
  label: string
  config: DefinitionNodeConfig
}

export interface DefinitionEdge {
  source: string
  target: string
}

export interface DefinitionPayload {
  name: string
  description?: string
  version?: string
  budget?: Record<string, unknown>
  llm?: Record<string, unknown>
  params?: Record<string, unknown>
  nodes: DefinitionNode[]
  edges: DefinitionEdge[]
}

export interface DefinitionListItem {
  name: string
  source: 'builtin' | 'user'
  description: string
  node_count: number
}

export interface DefinitionListResponse {
  status: string
  definitions: DefinitionListItem[]
}

export interface DefinitionGetResponse {
  status: string
  definition: DefinitionPayload & { source: string }
}

export interface DefinitionGraphResponse {
  status: string
  name: string
  description?: string
  nodes: Array<{ id: string; label: string; type: string }>
  edges: Array<{ source: string; target: string }>
}

export interface DefinitionRunSnapshot {
  run_id: string
  definition: string
  status: 'pending' | 'running' | 'awaiting' | 'completed' | 'failed' | 'cancelled'
  segment_idx: number
  segments_total: number
  replan_count: number
  replan_max: number
  completed_nodes: string[]
  findings: string[]
  failures: string[]
  elapsed_s: number
}

export interface DefinitionRunStartResponse {
  status: string
  run_id: string
  run: DefinitionRunSnapshot
}

export interface DefinitionRunResponse {
  status: string
  run_id: string
  run: DefinitionRunSnapshot | Record<string, unknown>
}

export interface DefinitionNodeOutput {
  run_id: string
  segment_idx: number
  node_id: string
  status: string
  summary: string
  artifacts: string
  metrics: string
  error: string | null
  elapsed_s: number
}

export interface DefinitionRunDetailResponse {
  status: string
  run: Record<string, unknown>
  segments: Array<Record<string, unknown>>
  node_outputs: DefinitionNodeOutput[]
  approvals: Array<Record<string, unknown>>
}

export const api = new APIClient()
