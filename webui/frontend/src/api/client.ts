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

export const api = new APIClient()
