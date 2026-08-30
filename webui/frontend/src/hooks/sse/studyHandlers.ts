import type { SSEHandler } from './types'
import { useStudyStore } from '../../stores/study'
import type { StudyStatusResponse } from '../../api/client'

/**
 * Study event handlers — keep the Study panel's progress view in sync
 * with the backend's ``study_*`` SSE events (emitted by StudyScheduler /
 * AutoresearchExecutor via EventStore → SSE). The StudyProgress poll
 * remains the authoritative fallback (3s) — these handlers just make
 * updates instant when the tab is open.
 *
 * Backend payloads (docs/study-longhorizon-plan.md §6):
 *   study_queued       {study_id, session_id, objective}
 *   study_started      {study_id, round}
 *   study_round        {study_id, round, run, metrics, verdict, agent_statuses}
 *   study_directives_consumed {study_id, round, consumed_ids, count}
 *   study_progress     {study_id, covered, total, percent}
 *   study_goal_snapshot {study_id, goal_snapshot}  — real-time goal state
 *   study_scoreboard   {study_id, round, scoreboard}
 *   study_budget       {study_id, budget}
 *   study_completed    {study_id, goal_id, metrics, round, recap}
 *   study_failed       {study_id, error, reason}
 *   study_budget_limited {study_id, used}
 *   study_paused       {study_id, round}
 *   study_resumed      {study_id, round}
 *   study_cancelled    {study_id}
 *   study_monitoring_started {study_id, interval_seconds}
 *   study_monitor_check {study_id, metrics, meets_targets, drift, drift_count}
 *   study_drift_detected {study_id, metrics, reason}
 */

const patch = (data: Record<string, unknown>) => {
  const cur = useStudyStore.getState().current
  // Start from the incoming payload, then layer the current snapshot
  // under it so status/objective/etc. survive partial events.
  const merged: Record<string, unknown> = { ...(cur ?? {}), ...data }
  const studyId = (data.study_id as string) ?? merged.study_id
  if (studyId) merged.study_id = studyId
  if (data.round !== undefined) {
    // Monotonic guard: SSE replays and stale out-of-order events carry
    // older rounds — never let current_round regress (F4).
    const prevRound = typeof cur?.current_round === 'number' ? cur.current_round : 0
    merged.current_round = Math.max(prevRound, data.round as number)
  }
  if (data.metrics !== undefined) merged.last_metrics = data.metrics as Record<string, number>
  if (data.verdict !== undefined) merged.last_verdict = data.verdict as string
  if (data.error !== undefined) merged.last_error = data.error as string
  if (data.trace_id !== undefined) merged.trace_id = data.trace_id as string
  useStudyStore.getState().setCurrent(merged as StudyStatusResponse)
}

export const studyRound: SSEHandler = (data) => {
  patch(data)
  const store = useStudyStore.getState()
  store.addLiveEvent({
    type: 'phase',
    message: `Round ${data.round ?? '?'} 完成 · verdict=${data.verdict ?? '—'}`,
    round: data.round as number | undefined,
  })
}

export const studyCompleted: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'complete' })
  const store = useStudyStore.getState()
  store.clearHitlInterrupt(data.study_id as string | undefined)
  store.addLiveEvent({
    type: 'review',
    message: `🎉 研究完成 (Round ${data.round ?? '?'})`,
    round: data.round as number | undefined,
  })
}

export const studyFailed: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'error' })
  const store = useStudyStore.getState()
  store.clearHitlInterrupt(data.study_id as string | undefined)
  store.addLiveEvent({
    type: 'other',
    message: `❌ 研究失败: ${data.error ?? data.reason ?? '未知原因'}`,
    round: data.round as number | undefined,
  })
}

export const studyBudgetLimited: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'budget_limited' })
}

/**
 * study_paused — runner pauses on the HITL novelty gate. When the
 * payload carries the REAL DB interrupt_id, open the webui approval
 * card slot (the card POSTs /study/{id}/interrupts/{iid}/respond —
 * synthetic ids used to 404 here, F2).
 */
export const studyPaused: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'paused' })
  const studyId = data.study_id as string | undefined
  const interruptId = data.interrupt_id as string | undefined
  useStudyStore.getState().addLiveEvent({
    type: 'other',
    message: interruptId
      ? `⏸ 等待人工审批 (Round ${data.round ?? '?'})`
      : `⏸ 研究已暂停 (Round ${data.round ?? '?'})`,
    round: data.round as number | undefined,
  })
  if (studyId && interruptId) {
    useStudyStore.getState().setHitlInterrupt({
      study_id: studyId,
      interrupt_id: interruptId,
      round: data.round as number | undefined,
      hypothesis: (data.hypothesis as string) ?? '',
      message:
        (data.hypothesis as string) ||
        `Round ${data.round ?? '?'}: 请审批 researcher 假设`,
    })
  }
}

export const studyResumed: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'running' })
  useStudyStore.getState().clearHitlInterrupt(data.study_id as string | undefined)
  useStudyStore.getState().addLiveEvent({
    type: 'other',
    message: `▶ 研究已恢复 (Round ${data.round ?? '?'})`,
    round: data.round as number | undefined,
  })
}

export const studyRoundRejected: SSEHandler = (data) => {
  // Emitted after a HITL timeout/rejection (reason: hitl_timeout /
  // hitl_rejected) — close the approval card and show the timeline note.
  const store = useStudyStore.getState()
  store.clearHitlInterrupt(data.study_id as string | undefined)
  store.addLiveEvent({
    type: 'other',
    message: `轮次被拒绝/超时: ${data.reason ?? '未知原因'}`,
    round: data.round as number | undefined,
  })
}

/**
 * study_progress — goal criteria coverage after a keep round
 * (runner._record_keep_evidence). Payload: { study_id, covered, total, percent }
 */
export const studyProgress: SSEHandler = (data) => {
  useStudyStore.getState().addLiveEvent({
    type: 'evidence',
    message: `目标进度: ${data.covered ?? '?'}/${data.total ?? '?'} (${data.percent ?? 0}%)`,
  })
  patch(data)
}

/**
 * study_retry_queued — continue/retry re-enqueued the study
 * (scheduler). Payload: { study_id, status }
 */
export const studyRetryQueued: SSEHandler = (data) => {
  useStudyStore.getState().addLiveEvent({
    type: 'other',
    message: '已重新排队，等待调度执行',
  })
  patch({ ...data, execution_status: 'queued' })
}

/**
 * study_monitor_check_failed — a periodic monitor check threw
 * (monitor.py). Payload: { study_id, error }
 */
export const studyMonitorCheckFailed: SSEHandler = (data) => {
  useStudyStore.getState().addLiveEvent({
    type: 'other',
    message: `监控检查失败: ${data.error ?? '未知错误'}`,
  })
}

export const studyInterrupted: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'interrupted' })
}

export const studyCancelled: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'cancelled' })
  useStudyStore.getState().clearHitlInterrupt(data.study_id as string | undefined)
}

export const studyStarted: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'running' })
}

export const studyQueued: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'queued' })
}

export const studyMonitoringStarted: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'monitoring' })
}

export const studyDriftDetected: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'needs_refresh' })
}

export const studyMonitorCheck: SSEHandler = (data) => {
  patch(data)
}

/**
 * Agent-loop approval gate (Step C of round/retry/loop fix).
 * Emitted by ``AgentLoop._check_no_progress`` when the LLM calls the
 * same tool N times in a row. The store keeps a queue so the dialog
 * component can show each pending approval in order.
 */
export const agentApprovalRequested: SSEHandler = (data) => {
  const studyId = data.study_id as string | undefined
  if (!studyId) return
  useStudyStore.getState().enqueueAgentApproval({
    study_id: studyId,
    role: (data.role as string) ?? null,
    tool_hash: (data.tool_hash as string) ?? '',
    window: (data.window as number) ?? 3,
    iteration: (data.iteration as number) ?? 0,
    timeout_s: (data.timeout_s as number) ?? 1800,
    on_timeout: (data.on_timeout as string) ?? 'continue',
    message: (data.message as string) ?? '',
    requested_at: Date.now(),
  })
}

export const agentApprovalResponded: SSEHandler = (data) => {
  const studyId = data.study_id as string | undefined
  if (!studyId) return
  const role = data.role as string | undefined
  const iter = data.iteration as number | undefined
  useStudyStore.getState().resolveAgentApproval(studyId, role ?? null, iter)
}

/**
 * Real-time scoreboard update (lever precision per round).
 * Emitted after each round to keep the summary's scoreboard fresh
 * without requiring a /summary poll.
 */
export const studyScoreboard: SSEHandler = (data) => {
  const cur = useStudyStore.getState().current
  if (!cur) return
  useStudyStore.getState().setCurrent({
    ...cur,
    scoreboard: data.scoreboard,
  } as StudyStatusResponse)
}

/**
 * Real-time goal snapshot update (progress, evidence count, criteria).
 * Emitted after each keep-round evidence append.
 */
export const studyGoalSnapshot: SSEHandler = (data) => {
  const cur = useStudyStore.getState().current
  if (!cur) return
  useStudyStore.getState().setCurrent({
    ...cur,
    goal_snapshot: data.goal_snapshot,
  } as StudyStatusResponse)
}

/**
 * Real-time budget usage update (turns/time used).
 * Emitted after each round's accounting.
 */
export const studyBudget: SSEHandler = (data) => {
  const cur = useStudyStore.getState().current
  if (!cur) return
  useStudyStore.getState().setCurrent({
    ...cur,
    budget: data.budget,
  } as StudyStatusResponse)
}

/**
 * parse_failed auto-retry notification (Step B). The StudyPage shows
 * a small toast so the user knows the round is being retried with
 * exponential backoff instead of silently failing.
 */
export const studyParseRetry: SSEHandler = (data) => {
  const failed = data.failed_agents as string[] | undefined
  const attempt = data.attempt as number | undefined
  const delay = data.delay_s as number | undefined
  // eslint-disable-next-line no-console
  console.info(
    '[study] parse_failed retry:', failed, 'attempt', attempt, 'delay', delay
  )
  // Also add to live event timeline
  const store = useStudyStore.getState()
  if (failed && failed.length > 0) {
    store.addLiveEvent({
      type: 'retry',
      message: `解析重试: ${failed.join(', ')} (第${attempt ?? '?'}次, ${delay ?? '?'}s后)`,
      round: data.round as number | undefined,
    })
  }
}

// ── Phase D: live activity handlers ──────────────────────────────

/**
 * study_phase — emitted at start/end of each phase within a round.
 * Payload: { study_id, round, phase: "researcher"|"execution"|"evaluation", status: "started"|"done" }
 */
export const studyPhase: SSEHandler = (data) => {
  const store = useStudyStore.getState()
  const phase = data.phase as string | undefined
  const status = data.status as string | undefined
  const round = data.round as number | undefined

  if (phase && status === 'started') {
    store.setPhase(phase)
    const phaseLabel = PHASE_LABELS[phase] ?? phase
    store.addLiveEvent({ type: 'phase', message: `${phaseLabel} 开始`, round })
  } else if (status === 'done') {
    store.setPhase(null)
    const phaseLabel = PHASE_LABELS[phase ?? ''] ?? phase
    store.addLiveEvent({ type: 'phase', message: `${phaseLabel} 完成`, round })
  } else if (status === 'awaiting_approval') {
    store.setPhase(null)
    store.addLiveEvent({
      type: 'other',
      message: `等待审批: ${phase ?? 'novelty_gate'}`,
      round,
    })
  }
  patch(data)
}

/**
 * study_agent_complete — emitted when a single agent finishes within DAG execution.
 * Payload: { study_id, round, agent, status, elapsed_s }
 */
export const studyAgentComplete: SSEHandler = (data) => {
  const store = useStudyStore.getState()
  const agent = data.agent as string | undefined
  const status = data.status as string | undefined
  const elapsed = data.elapsed_s as number | undefined
  const round = data.round as number | undefined

  if (agent) {
    store.updateNodeStatus(agent, status ?? 'completed')
    const label = AGENT_LABELS[agent] ?? agent
    const elapsedStr = elapsed != null ? ` (${elapsed.toFixed(1)}s)` : ''
    store.addLiveEvent({
      type: 'agent',
      message: `${label} ${status === 'completed' ? '完成' : status === 'failed' ? '失败' : status}${elapsedStr}`,
      round,
    })
    // Clear current agent when agent completes
    store.setAgent(null)
  }
}

/**
 * study_graph_node — per-node status update from DAG execution.
 * Payload: { study_id, round, layer, node_id, node_type, node_label, enabled, status }
 */
export const studyGraphNode: SSEHandler = (data) => {
  const store = useStudyStore.getState()
  const nodeId = data.node_id as string | undefined
  const status = data.status as string | undefined

  if (nodeId && status) {
    store.updateNodeStatus(nodeId, status)
    if (status === 'running') {
      store.setAgent(nodeId)
    }
  }
}

/**
 * study_knowledge_check — pre-round knowledge gap check.
 * Payload: { study_id, round, gap_topics, collected }
 */
export const studyKnowledgeCheck: SSEHandler = (data) => {
  const store = useStudyStore.getState()
  const topics = data.gap_topics as string[] | undefined
  const collected = data.collected as boolean | undefined
  const round = data.round as number | undefined

  if (topics && topics.length > 0) {
    store.addLiveEvent({
      type: 'knowledge',
      message: `知识检查: 缺口 [${topics.join(', ')}]${collected ? ' (已收集)' : ''}`,
      round,
    })
  }
}

/**
 * study_knowledge_update — knowledge collector appended entries.
 * Payload: { study_id, entries_added }
 */
export const studyKnowledgeUpdate: SSEHandler = (data) => {
  const store = useStudyStore.getState()
  const added = data.entries_added as number | undefined
  if (added && added > 0) {
    store.addLiveEvent({
      type: 'knowledge',
      message: `知识更新: +${added} 条`,
    })
  }
}

/**
 * study_review — review cycle completed.
 * Payload: { study_id, round, deviation, info_gap }
 */
export const studyReview: SSEHandler = (data) => {
  const store = useStudyStore.getState()
  const deviation = data.deviation as string | undefined
  const infoGap = data.info_gap as boolean | undefined
  const round = data.round as number | undefined

  store.addLiveEvent({
    type: 'review',
    message: `审核: 偏差=${deviation ?? '?'}, 信息缺口=${infoGap ? '是' : '否'}`,
    round,
  })
  patch(data)
}

/**
 * study_evidence — keep round recorded evidence.
 * Payload: { study_id, evidence_id, run }
 */
export const studyEvidence: SSEHandler = (data) => {
  const store = useStudyStore.getState()
  const evidenceId = data.evidence_id as string | undefined
  store.addLiveEvent({
    type: 'evidence',
    message: `证据记录: ${evidenceId ?? '新证据'}`,
  })
}

/**
 * study_directive_added — new user directive injected.
 * Payload: { study_id, directive_id, content, issued_by, created_at }
 */
export const studyDirectiveAdded: SSEHandler = (data) => {
  const store = useStudyStore.getState()
  const content = data.content as string | undefined
  store.addLiveEvent({
    type: 'directive',
    message: `新指令: "${(content ?? '').slice(0, 50)}${(content ?? '').length > 50 ? '...' : ''}"`,
  })
}

/**
 * study_objective_applied — pending objective replacement applied.
 * Payload: { study_id, round, count }
 */
export const studyObjectiveApplied: SSEHandler = (data) => {
  const store = useStudyStore.getState()
  const count = data.count as number | undefined
  const round = data.round as number | undefined
  store.addLiveEvent({
    type: 'directive',
    message: `目标替换已应用 (${count ?? '?'} 项)`,
    round,
  })
}

/**
 * study_early_stopped — early stopping triggered.
 * Payload: { study_id, round, reason, idle_rounds?, best_score? }
 */
export const studyEarlyStopped: SSEHandler = (data) => {
  const store = useStudyStore.getState()
  const reason = data.reason as string | undefined
  const round = data.round as number | undefined
  store.addLiveEvent({
    type: 'other',
    message: `提前停止: ${reason ?? '未知原因'}`,
    round,
  })
  patch({ ...data, execution_status: 'early_stopped' })
}

/**
 * study_todos_updated — todos list updated from reviewer.
 * Payload: { study_id, updates: [...] }
 */
export const studyTodosUpdated: SSEHandler = (data) => {
  const store = useStudyStore.getState()
  const updates = data.updates as Array<{ text?: string; status?: string }> | undefined
  const count = updates?.length ?? 0
  store.addLiveEvent({
    type: 'other',
    message: `待办更新: ${count} 项`,
  })
}

// ── Label maps ───────────────────────────────────────────────────

const PHASE_LABELS: Record<string, string> = {
  researcher: '研究',
  execution: '回测执行',
  evaluation: '评估',
  review: '审核',
  knowledge: '知识收集',
  novelty_gate: '假设审批',
  langgraph_init: '引擎初始化',
  langgraph_exec: '图执行',
  langgraph_resume: '恢复执行',
}

const AGENT_LABELS: Record<string, string> = {
  researcher: 'Researcher',
  data_quality: 'DataQuality',
  factor_analyst: 'FactorAnalyst',
  strategist: 'Strategist',
  portfolio_construction: 'Portfolio',
  risk_controller: 'RiskCtrl',
  attribution_analyst: 'Attribution',
  anti_overfit_analyst: 'AntiOverfit',
  backtest_diagnostics: 'BacktestDiag',
}

// ── P4: HITL interrupt handlers ──────────────────────────────────

/**
 * study_phase with status="awaiting_approval" — HITL approval needed.
 * Emitted by LangGraph engine when interrupt() fires.
 */
export const studyAwaitingApproval: SSEHandler = (data) => {
  const store = useStudyStore.getState()
  const round = data.round as number | undefined
  const hypothesis = data.hypothesis as string | undefined
  store.addLiveEvent({
    type: 'other',
    message: `等待审批: Round ${round ?? '?'} — ${hypothesis?.slice(0, 60) ?? '假设审批'}`,
    round,
  })
  patch({ ...data, execution_status: 'awaiting_approval' })
}

/**
 * study_interrupt_responded — user responded to HITL interrupt.
 */
export const studyInterruptResponded: SSEHandler = (data) => {
  const store = useStudyStore.getState()
  const decision = data.decision as string | undefined
  const interruptId = data.interrupt_id as string | undefined
  store.addLiveEvent({
    type: 'other',
    message: `审批${decision === 'approve' ? '通过' : '拒绝'}: ${interruptId ?? ''}`,
  })
}