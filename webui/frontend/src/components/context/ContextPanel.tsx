import { useMemo } from 'react'
import { FileSearch, FileCode, Wrench, BarChart3, LineChart } from 'lucide-react'
import { useSessionStore } from '../../stores/session'
import { useChatStore } from '../../stores/chat'
import {
  extractFileChanges,
  extractToolActivity,
  extractBacktestResults,
  extractStrategyFiles,
} from '../../utils/contextExtractors'
import { StrategyFileSection } from './StrategyFileSection'

/**
 * Middle panel that surfaces the working context of the active
 * session: files touched by the agent, recent tool calls, session
 * metrics, and any backtest results found in the message stream.
 *
 * All data is extracted client-side from `messages` via
 * `utils/contextExtractors.ts`. The extractor rules are intentionally
 * parameterised so we can tune them without touching this component.
 */
export function ContextPanel() {
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const messages = useChatStore((s) => s.messages)

  // TODO: once the session-id keying on the messages Map is
  //       guaranteed, drop the manual session_id filter. For now we
  //       filter defensively to avoid showing another session's data
  //       in the panel after a quick switch.
  const messageList = useMemo(() => {
    const list = Array.from(messages.values())
    if (currentSessionId) {
      return list.filter((m) => m.session_id === currentSessionId)
        .sort((a, b) => a.created_at - b.created_at)
    }
    return list.sort((a, b) => a.created_at - b.created_at)
  }, [messages, currentSessionId])

  const fileChanges = useMemo(() => extractFileChanges(messageList), [messageList])
  const tools = useMemo(() => extractToolActivity(messageList), [messageList])
  const backtests = useMemo(() => extractBacktestResults(messageList), [messageList])
  const strategyFiles = useMemo(() => extractStrategyFiles(messageList), [messageList])

  const isEmpty = fileChanges.length === 0
    && tools.length === 0
    && backtests.length === 0
    && strategyFiles.length === 0

  return (
    <div className="flex h-full flex-col bg-slate-900/40">
      <div className="flex items-center justify-between border-b border-slate-800 px-3 py-2">
        <span className="text-xs font-medium text-slate-300">上下文</span>
        <span className="text-[10px] text-slate-500">
          {messageList.length} 条消息
        </span>
      </div>

      {isEmpty ? (
        <div className="flex flex-1 items-center justify-center">
          <div className="px-6 text-center text-slate-500">
            <FileSearch className="mx-auto mb-2 h-8 w-8 opacity-50" />
            <p className="text-xs">暂无上下文</p>
            <p className="mt-1 text-[10px] opacity-60">
              Agent 操作的文件、工具和回测结果会显示在这里
            </p>
          </div>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto p-2 space-y-3">
          {/* TODO: replace these inline previews with the dedicated
              Section components (FileSection / ToolActivitySection /
              MetricsSection / BacktestSection). Kept inline for the
              first slice so we can validate the layout + extractors
              before splitting. */}
          {fileChanges.length > 0 && (
            <PreviewBlock icon={FileCode} label="文件" count={fileChanges.length}>
              {fileChanges.slice(0, 6).map((f) => (
                <div
                  key={f.path}
                  className="flex items-center gap-2 rounded px-2 py-1 text-xs hover:bg-slate-800/60"
                >
                  <FileCode className="h-3.5 w-3.5 flex-shrink-0 text-slate-500" />
                  <span className="flex-1 truncate text-slate-300">{f.path}</span>
                  <span className="text-[10px] text-amber-400">{f.status}</span>
                </div>
              ))}
            </PreviewBlock>
          )}

          {tools.length > 0 && (
            <PreviewBlock icon={Wrench} label="工具活动" count={tools.length}>
              {tools.map((t) => {
                const dot = t.status === 'running'
                  ? 'bg-primary-500 animate-pulse'
                  : t.status === 'done'
                    ? 'bg-emerald-500'
                    : t.status === 'error'
                      ? 'bg-red-500'
                      : 'bg-slate-500'
                return (
                  <div
                    key={t.id}
                    className="flex items-start gap-2 rounded px-2 py-1 text-xs hover:bg-slate-800/60"
                  >
                    <span className={`mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full ${dot}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono text-[11px] text-slate-300">{t.name}</span>
                        <span className="text-[9px] text-slate-600">{t.status}</span>
                      </div>
                      {t.preview && (
                        <p className="truncate text-[10px] text-slate-500" title={t.preview}>
                          {t.preview}
                        </p>
                      )}
                    </div>
                  </div>
                )
              })}
            </PreviewBlock>
          )}

          <MetricsPreview sessionId={currentSessionId} />

          <StrategyFileSection files={strategyFiles} />

          {backtests.length > 0 && (
            <PreviewBlock icon={LineChart} label="回测结果" count={backtests.length}>
              {backtests.map((r, i) => (
                <div
                  key={i}
                  className="rounded border border-slate-800/50 bg-slate-900/30 p-2"
                >
                  <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-slate-300">
                    <BarChart3 className="h-3 w-3" />
                    {r.title}
                  </div>
                  <div className="space-y-0.5">
                    {r.metrics.map((m, j) => (
                      <div key={j} className="flex justify-between text-[10px]">
                        <span className="text-slate-500">{m.label}</span>
                        <span className="font-mono text-slate-300">{m.value}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </PreviewBlock>
          )}
        </div>
      )}
    </div>
  )
}

function PreviewBlock({
  icon: Icon,
  label,
  count,
  children,
}: {
  icon: typeof FileCode
  label: string
  count: number
  children: React.ReactNode
}) {
  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5 px-1 text-[10px] font-medium uppercase tracking-wider text-slate-500">
        <Icon className="h-3 w-3" />
        <span>{label}</span>
        <span className="text-slate-600">({count})</span>
      </div>
      <div className="space-y-0.5">{children}</div>
    </div>
  )
}

function MetricsPreview({ sessionId }: { sessionId: string | null }) {
  const tokensUsed = useChatStore((s) =>
    sessionId ? s.tokensUsed.get(sessionId) ?? 0 : 0
  )
  const messages = useChatStore((s) => s.messages)
  const modelInfo = useSystemStoreSafe()

  const limit = modelInfo?.context_tokens ?? 0
  const pct = limit > 0 ? Math.min(100, (tokensUsed / limit) * 100) : 0
  const messageCount = Array.from(messages.values())
    .filter((m) => !sessionId || m.session_id === sessionId).length

  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5 px-1 text-[10px] font-medium uppercase tracking-wider text-slate-500">
        <BarChart3 className="h-3 w-3" />
        <span>指标</span>
      </div>
      <div className="space-y-2 rounded border border-slate-800/50 bg-slate-900/30 p-2">
        {limit > 0 && (
          <div>
            <div className="mb-1 flex justify-between text-[10px] text-slate-400">
              <span>上下文</span>
              <span className="font-mono">{pct.toFixed(1)}%</span>
            </div>
            <div className="h-1 w-full overflow-hidden rounded-full bg-slate-800">
              <div
                className={`h-full transition-all ${
                  pct >= 80 ? 'bg-red-500'
                    : pct >= 50 ? 'bg-amber-500'
                      : 'bg-emerald-500'
                }`}
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        )}
        <div className="flex justify-between text-[10px] text-slate-400">
          <span>消息</span>
          <span className="font-mono">{messageCount}</span>
        </div>
      </div>
    </div>
  )
}

// Lazy import to avoid pulling systemStore into the test bundle if
// this file is ever imported by a unit test that doesn't need it.
import { useSystemStore } from '../../stores/system'
function useSystemStoreSafe() {
  return useSystemStore((s) => s.modelInfo)
}