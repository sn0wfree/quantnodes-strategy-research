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
}

export const api = new APIClient()
