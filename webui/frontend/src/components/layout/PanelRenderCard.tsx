import { BarChart3, FileCode2 } from 'lucide-react'
import { ChartRenderer } from '../chat/ChartBlock'
import { EquityCurveCard } from '../performance/EquityCurveCard'
import type { BacktestMetrics, PanelRenderable } from '../../utils/equityCurve'

interface Props {
  /**
   * Latest agent-driven renderable (show_chart / show_report).
   * The agent decides what this card shows.
   */
  item: PanelRenderable | null
  /** Metrics fallback when no renderable exists yet. */
  metrics?: BacktestMetrics | null
}

/**
 * 「表现曲线」right-panel card — driven by the chat agent.
 *
 * show_chart / show_report push renderables into the chat stream; the
 * panel shows the MOST RECENT one (chart via recharts, html report via
 * a sandboxed iframe). Before any renderable exists, falls back to the
 * latest run_backtest metrics (Tier B P7), so the card is never empty
 * after a real backtest. docs/right-panel-agent-driven.md
 */
export function PanelRenderCard({ item, metrics }: Props) {
  const title =
    item?.kind === 'chart'
      ? item.chart?.title || '图表'
      : item?.kind === 'html'
        ? item.html?.title || 'HTML 报告'
        : ''

  return (
    <div className="rounded-lg border border-slate-800/50 bg-slate-900/30 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
        {item?.kind === 'chart' ? (
          <BarChart3 className="h-3 w-3" />
        ) : item?.kind === 'html' ? (
          <FileCode2 className="h-3 w-3" />
        ) : (
          <BarChart3 className="h-3 w-3" />
        )}
        <span>表现曲线</span>
      </div>

      {item?.kind === 'chart' ? (
        <div>
          {title && <div className="mb-2 text-xs text-slate-400">{title}</div>}
          <ChartRenderer
            chart={{
              type: 'chart',
              chart_type: item.chart!.chart_type as 'bar' | 'line' | 'pie' | 'scatter',
              data: item.chart!.data,
              title,
            }}
          />
        </div>
      ) : item?.kind === 'html' ? (
        <div>
          {title && <div className="mb-2 text-xs text-slate-400">{title}</div>}
          <iframe
            title={title}
            sandbox=""
            srcDoc={item.html!.content}
            className="h-[260px] w-full rounded border border-slate-800 bg-white"
          />
        </div>
      ) : (
        <EquityCurveCard curve={null} metrics={metrics} />
      )}
    </div>
  )
}
