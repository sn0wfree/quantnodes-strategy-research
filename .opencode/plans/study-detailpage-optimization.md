# StudyDetailPage 标签页和布局优化实现计划

> 日期: 2026-08-18
> 状态: 待执行

## 目标

1. 合并"Agent 活动"和"任务"标签页为"研究流程"
2. 使用简单的 CSS 流水线布局替代 ReactFlow
3. Agent 节点卡片显示状态/耗时/摘要
4. 点击节点弹出浮窗显示聊天记录

## 当前结构

```
标签页: [概览] [Agent 活动] [日志] [任务]

Agent 活动 标签页:
├─ AgentActivityPanel (左 2/3)
└─ DAGVisualization (右 1/3)

任务 标签页:
├─ ObjectiveProgress + Journal (左 2/3)
└─ Directive 输入 + 记录 (右 1/3)
```

## 改进后结构

```
标签页: [概览] [研究流程] [日志]

研究流程 标签页:
┌─────────────────────────────────────────────────────────────┐
│ 顶部: 研究目标进度 + 指令输入                                │
├─────────────────────────────────────────────────────────────┤
│ 中部: 流水线画布 (Agent 节点 + 连线)                         │
│ - 水平流水线布局                                            │
│ - 每个 Agent 是一个节点卡片                                  │
│ - 节点显示状态、耗时、输出摘要                               │
│ - 点击节点展开浮窗详情                                      │
├─────────────────────────────────────────────────────────────┤
│ 底部: 研究日志 + 指令记录                                    │
└─────────────────────────────────────────────────────────────┘
```

## 实现步骤

### 步骤 1: 修改 StudyDetailPage.tsx

**修改内容**:
1. 将标签页从 4 个减少到 3 个
2. 将 `agents` 和 `todos` 标签合并为 `flow`
3. 导入新的组件

**修改位置**: `webui/frontend/src/components/study/StudyDetailPage.tsx`

```tsx
// 修改前
type TabKey = 'overview' | 'agents' | 'logs' | 'todos'

const TABS = [
  { key: 'overview', label: '概览', icon: <BarChart3 /> },
  { key: 'agents', label: 'Agent 活动', icon: <Layers /> },
  { key: 'logs', label: '日志', icon: <MessageSquare /> },
  { key: 'todos', label: '任务', icon: <CheckSquare /> },
]

// 修改后
type TabKey = 'overview' | 'flow' | 'logs'

const TABS = [
  { key: 'overview', label: '概览', icon: <BarChart3 /> },
  { key: 'flow', label: '研究流程', icon: <GitBranch /> },
  { key: 'logs', label: '日志', icon: <MessageSquare /> },
]
```

**修改标签页内容**:
- 删除 `agents` 和 `todos` 标签页内容
- 添加 `flow` 标签页内容（使用新的 StudyFlowTab 组件）

### 步骤 2: 创建 StudyFlowTab.tsx

**新建文件**: `webui/frontend/src/components/study/StudyFlowTab.tsx`

**功能**:
- 顶部：研究目标进度 + 指令输入
- 中部：流水线画布
- 底部：研究日志 + 指令记录

**组件结构**:
```tsx
interface StudyFlowTabProps {
  studyId: string
  summary: StudySummaryResponse
  directives: StudyDirectivesResponse | null
  journal: StudyJournalResponse | null
  directiveText: string
  submittingDirective: boolean
  canDirective: boolean
  onDirective: () => void
  onDirectiveTextChange: (text: string) => void
}

export function StudyFlowTab({
  studyId,
  summary,
  directives,
  journal,
  directiveText,
  submittingDirective,
  canDirective,
  onDirective,
  onDirectiveTextChange,
}: StudyFlowTabProps) {
  return (
    <div className="flex flex-col gap-4">
      {/* 顶部：目标进度 + 指令 */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <ObjectiveProgress
            objective={summary.objective}
            progressPercent={summary.goal_snapshot?.progress_percent ?? 0}
            evidenceCount={summary.goal_snapshot?.evidence_count ?? 0}
            criteria={summary.goal_snapshot?.criteria ?? []}
          />
        </div>
        <div>
          {/* 指令输入 */}
          {canDirective && (
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 shadow-soft space-y-2">
              <label className="block text-[10px] font-medium uppercase tracking-wider text-slate-500">
                注入研究方向
              </label>
              <textarea
                rows={2}
                value={directiveText}
                onChange={(e) => onDirectiveTextChange(e.target.value)}
                placeholder="例：改成动量因子 + 减小 top_n"
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 outline-none transition-shadow focus:border-primary-500 focus:ring-2 focus:ring-primary-500/40"
              />
              <button
                type="button"
                onClick={onDirective}
                disabled={submittingDirective || !directiveText.trim()}
                className="inline-flex cursor-pointer items-center gap-1 rounded-lg bg-indigo-600 px-2.5 py-1.5 text-xs text-white transition-all hover:bg-indigo-500 active:scale-95 disabled:opacity-50"
              >
                <Send className="h-3.5 w-3.5" /> 提交指令
              </button>
            </div>
          )}
        </div>
      </div>

      {/* 中部：流水线画布 */}
      <AgentFlowCanvas
        studyId={studyId}
        currentRound={summary.current_round ?? 1}
        totalRounds={summary.max_rounds}
      />

      {/* 底部：日志 + 指令记录 */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <AgentChatLog
          studyId={studyId}
          currentRound={summary.current_round ?? 1}
        />
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 shadow-soft">
          <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
            <Clock className="h-3 w-3" /> 指令记录
          </div>
          {(directives?.directives?.length ?? 0) === 0 ? (
            <p className="text-xs text-slate-500">暂无指令</p>
          ) : (
            <ul className="space-y-1.5 max-h-64 overflow-y-auto">
              {directives!.directives.map((d) => (
                <li key={d.directive_id} className="rounded-lg border border-slate-800/60 bg-slate-950/60 p-2 text-[11px]">
                  <p className="text-slate-300">{d.content}</p>
                  <div className="mt-1 flex items-center gap-2 text-[10px] text-slate-500">
                    <span>{formatDateTime(d.created_at)}</span>
                    {d.issued_by && <span>· {d.issued_by}</span>}
                    <span
                      className={
                        d.consumed_at
                          ? 'rounded-full border border-emerald-500/30 bg-emerald-500/10 px-1.5 text-emerald-400'
                          : 'rounded-full border border-amber-500/30 bg-amber-500/10 px-1.5 text-amber-400'
                      }
                    >
                      {d.consumed_at ? '已消费' : '待消费'}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
```

### 步骤 3: 创建 AgentFlowCanvas.tsx

**新建文件**: `webui/frontend/src/components/study/AgentFlowCanvas.tsx`

**功能**:
- 使用 CSS Flexbox 实现水平流水线
- 显示 9 个 Agent 节点卡片
- 节点之间用连线连接
- 进度条显示完成百分比

**组件结构**:
```tsx
interface AgentFlowCanvasProps {
  studyId: string
  currentRound: number
  totalRounds?: number
}

const AGENT_SEQUENCE = [
  { id: 'researcher', label: 'Researcher', abbr: 'R' },
  { id: 'data_quality', label: 'Data Quality', abbr: 'DQ' },
  { id: 'factor_analyst', label: 'Factor Analyst', abbr: 'FA' },
  { id: 'strategist', label: 'Strategist', abbr: 'ST' },
  { id: 'portfolio_construction', label: 'Portfolio', abbr: 'PC' },
  { id: 'risk_controller', label: 'Risk Control', abbr: 'RC' },
  { id: 'attribution_analyst', label: 'Attribution', abbr: 'AA' },
  { id: 'anti_overfit_analyst', label: 'Anti-Overfit', abbr: 'AO' },
]

export function AgentFlowCanvas({ studyId, currentRound, totalRounds }: AgentFlowCanvasProps) {
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null)
  const [agentStatuses, setAgentStatuses] = useState<Record<string, AgentStatus>>({})

  // 从 manifest 获取 agent 状态
  useEffect(() => {
    loadAgentStatuses()
  }, [studyId, currentRound])

  const doneCount = Object.values(agentStatuses).filter(s => s.status === 'done').length
  const progress = Math.round((doneCount / AGENT_SEQUENCE.length) * 100)

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 shadow-soft">
      {/* 标题 */}
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
          <GitBranch className="h-3 w-3 text-primary-400" />
          Agent 流水线 · Round {currentRound}
        </div>
        {totalRounds && (
          <span className="font-mono text-[9px] text-slate-600">共 {totalRounds} 轮</span>
        )}
      </div>

      {/* 流水线 */}
      <div className="flex items-center gap-1 overflow-x-auto pb-2" style={{ scrollbarWidth: 'none' }}>
        {AGENT_SEQUENCE.map((agent, i) => (
          <React.Fragment key={agent.id}>
            <AgentNodeCard
              agent={agent}
              status={agentStatuses[agent.id]}
              onClick={() => setSelectedAgent(agent.id)}
            />
            {i < AGENT_SEQUENCE.length - 1 && (
              <Connector status={agentStatuses[AGENT_SEQUENCE[i + 1].id]?.status ?? 'pending'} />
            )}
          </React.Fragment>
        ))}
      </div>

      {/* 进度条 */}
      <div className="mt-4 flex items-center gap-2 border-t border-slate-800/60 pt-3 text-[10px] text-slate-500">
        <span className="font-mono text-slate-300">{doneCount}/{AGENT_SEQUENCE.length} 步骤</span>
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-700/80">
          <div
            className="h-full bg-gradient-to-r from-sky-500 via-primary-500 to-accent-400 transition-all duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
        <span className="font-mono tabular-nums">{progress}%</span>
      </div>

      {/* 图例 */}
      <div className="mt-3 flex items-center gap-4 text-[9px] text-slate-500">
        <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-emerald-500" /> 完成</span>
        <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-sky-500 animate-pulse" /> 运行中</span>
        <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-slate-700" /> 待执行</span>
        <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-rose-500" /> 失败</span>
      </div>

      {/* 浮窗 */}
      {selectedAgent && (
        <AgentNodeDetail
          agentId={selectedAgent}
          studyId={studyId}
          currentRound={currentRound}
          onClose={() => setSelectedAgent(null)}
        />
      )}
    </div>
  )
}
```

### 步骤 4: 创建 AgentNodeCard.tsx

**新建文件**: `webui/frontend/src/components/study/AgentNodeCard.tsx`

**功能**:
- 显示 Agent 状态、耗时、输出摘要
- 点击触发浮窗

**样式**:
```tsx
interface AgentNodeCardProps {
  agent: { id: string; label: string; abbr: string }
  status?: AgentStatus
  onClick: () => void
}

const STATUS_CONFIG = {
  pending: { border: 'border-slate-700', bg: 'bg-slate-800/40', text: 'text-slate-500', icon: '·' },
  running: { border: 'border-sky-500', bg: 'bg-sky-500/10', text: 'text-sky-400', icon: '◉' },
  done: { border: 'border-emerald-500', bg: 'bg-emerald-500/10', text: 'text-emerald-400', icon: '✓' },
  error: { border: 'border-rose-500', bg: 'bg-rose-500/10', text: 'text-rose-400', icon: '✗' },
}

export function AgentNodeCard({ agent, status, onClick }: AgentNodeCardProps) {
  const config = STATUS_CONFIG[status?.status ?? 'pending']

  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-32 flex-shrink-0 flex-col rounded-xl border p-3 transition-all hover:scale-[1.02] hover:shadow-lg ${config.border} ${config.bg}`}
    >
      {/* 状态 + 耗时 */}
      <div className="flex items-center justify-between">
        <span className={`text-lg font-bold ${config.text}`}>{config.icon}</span>
        {status?.duration_s != null && (
          <span className="font-mono text-[10px] text-slate-500">{status.duration_s.toFixed(0)}s</span>
        )}
      </div>

      {/* Agent 名称 */}
      <div className="mt-1 text-xs font-medium text-slate-200">{agent.abbr}</div>

      {/* 输出摘要（截断） */}
      {status?.output_summary && (
        <div className="mt-1 text-[10px] text-slate-400 line-clamp-2">
          {status.output_summary}
        </div>
      )}
    </button>
  )
}
```

### 步骤 5: 创建 AgentNodeDetail.tsx

**新建文件**: `webui/frontend/src/components/study/AgentNodeDetail.tsx`

**功能**:
- 浮窗显示 Agent 详细信息
- 包含状态、耗时、假设、变更、聊天记录

**样式**:
```tsx
interface AgentNodeDetailProps {
  agentId: string
  studyId: string
  currentRound: number
  onClose: () => void
}

export function AgentNodeDetail({ agentId, studyId, currentRound, onClose }: AgentNodeDetailProps) {
  const [agentData, setAgentData] = useState<AgentDetail | null>(null)
  const [chatLogs, setChatLogs] = useState<ChatEntry[]>([])

  useEffect(() => {
    loadAgentData()
  }, [agentId, studyId, currentRound])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="w-full max-w-lg rounded-xl border border-slate-800 bg-slate-900 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <h3 className="text-sm font-semibold text-slate-200">{agentData?.label}</h3>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* 内容 */}
        <div className="max-h-[60vh] overflow-y-auto p-4 space-y-4">
          {/* 状态信息 */}
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <span className="text-slate-500">状态</span>
              <span className="ml-2 text-slate-200">{agentData?.status}</span>
            </div>
            <div>
              <span className="text-slate-500">耗时</span>
              <span className="ml-2 text-slate-200">{agentData?.duration_s}s</span>
            </div>
          </div>

          {/* 假设 */}
          {agentData?.hypothesis && (
            <div>
              <div className="text-[10px] font-medium uppercase tracking-wider text-slate-500">假设</div>
              <p className="mt-1 text-xs text-slate-300">{agentData.hypothesis}</p>
            </div>
          )}

          {/* 变更 */}
          {agentData?.changes && Object.keys(agentData.changes).length > 0 && (
            <div>
              <div className="text-[10px] font-medium uppercase tracking-wider text-slate-500">变更</div>
              <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px] text-slate-400">
                {JSON.stringify(agentData.changes, null, 2)}
              </pre>
            </div>
          )}

          {/* 聊天记录 */}
          <div>
            <div className="text-[10px] font-medium uppercase tracking-wider text-slate-500">聊天记录</div>
            <div className="mt-2 space-y-2">
              {chatLogs.map((log, i) => (
                <div key={i} className="rounded-lg border border-slate-800 bg-slate-950/60 p-2">
                  <div className="text-[10px] text-slate-500">{log.role}</div>
                  <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px] text-slate-400">
                    {log.content}
                  </pre>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
```

## 测试计划

1. 标签页切换正常
2. 流水线画布正确显示 9 个 Agent 节点
3. 点击节点弹出浮窗
4. 浮窗显示正确的 Agent 信息和聊天记录
5. 响应式布局在不同屏幕尺寸下正常

## 预计工作量

| 步骤 | 文件数 | 预计时间 |
|------|--------|----------|
| 步骤 1 | 1 | 10 分钟 |
| 步骤 2 | 1 | 15 分钟 |
| 步骤 3 | 1 | 20 分钟 |
| 步骤 4 | 1 | 10 分钟 |
| 步骤 5 | 1 | 15 分钟 |
| 测试 | - | 10 分钟 |
| **总计** | **5** | **~80 分钟** |
