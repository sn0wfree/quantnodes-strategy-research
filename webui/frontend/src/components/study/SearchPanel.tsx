import { useCallback, useEffect, useState } from 'react'
import { Search, ExternalLink, Loader2, AlertTriangle } from 'lucide-react'
import { api, type MiniMaxSearchResponse, type MiniMaxSearchResult } from '../../api/client'

interface SearchPanelProps {
  /** Optional placeholder override (default: 'A-share momentum strategy'). */
  placeholder?: string
  /** Optional default query (e.g. study objective). */
  defaultQuery?: string
  /** Default result count to request (MiniMax clamps to 1..10). */
  defaultCount?: number
}

/**
 * Lightweight MiniMax search panel — usable from any page (study,
 * chat, etc.). Backend: ``GET /api/search/minimax?q=...&count=...``.
 * If the backend is not configured the panel renders disabled.
 */
export function SearchPanel({
  placeholder = 'MiniMax 搜索 — 例：A 股动量因子研究',
  defaultQuery = '',
  defaultCount = 5,
}: SearchPanelProps) {
  const [query, setQuery] = useState(defaultQuery)
  const [count, setCount] = useState(defaultCount)
  const [data, setData] = useState<MiniMaxSearchResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [enabled, setEnabled] = useState<boolean | null>(null)

  // Probe health once on mount to decide whether to render enabled.
  useEffect(() => {
    let cancelled = false
    api.search
      .minimaxHealth()
      .then((r) => {
        if (!cancelled) setEnabled(r.configured)
      })
      .catch(() => {
        if (!cancelled) setEnabled(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const onSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      const q = query.trim()
      if (!q || loading) return
      setLoading(true)
      setError('')
      try {
        const r = await api.search.minimax(q, count)
        setData(r)
      } catch (err) {
        // api.request throws with .status; surface a useful message.
        const status = (err as { status?: number })?.status
        if (status === 503) {
          setError('后端未配置 MiniMax 搜索（需要 MINIMAX_CODE_PLAN_KEY）')
        } else {
          setError((err as Error).message || '搜索失败')
        }
        setData(null)
      } finally {
        setLoading(false)
      }
    },
    [query, count, loading],
  )

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-slate-800 bg-slate-900/60 p-3 shadow-soft">
      <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
        <Search className="h-3 w-3" />
        MiniMax 搜索
        {enabled === false && (
          <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-amber-900/40 px-1.5 py-0.5 text-[9px] text-amber-300">
            <AlertTriangle className="h-2.5 w-2.5" />
            后端未配置
          </span>
        )}
      </div>

      <form onSubmit={onSubmit} className="flex items-center gap-1.5">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={placeholder}
          disabled={enabled === false}
          className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 outline-none transition-shadow placeholder:text-slate-600 focus:border-primary-500 focus:ring-2 focus:ring-primary-500/40 disabled:opacity-50"
        />
        <select
          value={count}
          onChange={(e) => setCount(Number(e.target.value))}
          disabled={enabled === false}
          className="rounded-lg border border-slate-700 bg-slate-950 px-1.5 py-1.5 text-xs text-slate-300 outline-none focus:border-primary-500 disabled:opacity-50"
          title="结果数量"
        >
          {[3, 5, 8, 10].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <button
          type="submit"
          disabled={loading || enabled === false || !query.trim()}
          className="inline-flex items-center gap-1 rounded-lg bg-primary-600 px-2.5 py-1.5 text-xs text-white transition-all hover:bg-primary-500 active:scale-95 disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
          搜索
        </button>
      </form>

      {error && (
        <p className="text-[10px] text-rose-400">{error}</p>
      )}

      <div className="min-h-0 space-y-1.5 overflow-y-auto" style={{ maxHeight: 240 }}>
        {data?.results?.length ? (
          data.results.map((r, i) => <ResultRow key={`${r.href || i}`} r={r} index={i + 1} />)
        ) : data?.results?.length === 0 ? (
          <p className="px-1 py-2 text-center text-[10px] text-slate-600">
            暂无结果
          </p>
        ) : null}

        {data?.related_queries?.length ? (
          <div className="border-t border-slate-800/60 pt-1.5">
            <p className="mb-1 text-[9px] uppercase tracking-wider text-slate-600">相关搜索</p>
            <div className="flex flex-wrap gap-1">
              {data.related_queries.slice(0, 5).map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => setQuery(q)}
                  className="rounded-full border border-slate-700 bg-slate-800/40 px-2 py-0.5 text-[10px] text-slate-300 transition-colors hover:border-slate-500 hover:text-slate-100"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}

function ResultRow({ r, index }: { r: MiniMaxSearchResult; index: number }) {
  return (
    <a
      href={r.href}
      target="_blank"
      rel="noreferrer noopener"
      className="group block rounded-lg border border-slate-800/60 bg-slate-950/40 px-2.5 py-1.5 transition-colors hover:border-slate-700 hover:bg-slate-900/60"
    >
      <div className="flex items-start gap-1.5">
        <span className="mt-0.5 inline-flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full bg-slate-800 font-mono text-[9px] text-slate-400">
          {index}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1 truncate text-[11px] font-medium text-slate-200 group-hover:text-primary-300">
            <span className="truncate">{r.title || r.href || '(无标题)'}</span>
            {r.href && <ExternalLink className="h-2.5 w-2.5 flex-shrink-0 text-slate-600" />}
          </div>
          {r.body && (
            <p className="mt-0.5 line-clamp-2 text-[10px] leading-relaxed text-slate-500">
              {r.body}
            </p>
          )}
          {r.href && (
            <p className="mt-0.5 truncate font-mono text-[9px] text-slate-600">
              {r.href}
            </p>
          )}
        </div>
      </div>
    </a>
  )
}