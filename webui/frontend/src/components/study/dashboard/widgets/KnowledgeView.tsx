/**
 * KnowledgeView — fetches and renders knowledge.md content from API.
 */
import { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../../../../api/client'
import type { WidgetProps } from '../types'

export function KnowledgeView({ studyId }: WidgetProps) {
  const [content, setContent] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.study
      .knowledge(studyId)
      .then((r) => {
        if (!cancelled) {
          setContent(r.knowledge || null)
          setLoading(false)
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError((e as Error).message)
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [studyId])

  if (loading) {
    return (
      <div className="flex h-16 items-center justify-center text-xs text-slate-500">
        <div className="h-3 w-3 animate-spin rounded-full border border-slate-600 border-t-slate-400" />
      </div>
    )
  }

  if (error) {
    return <div className="text-xs text-rose-400">加载失败: {error}</div>
  }

  if (!content) {
    return <div className="text-xs text-slate-500 italic">暂无知识库内容</div>
  }

  return (
    <div className="prose prose-invert prose-xs max-w-none text-slate-300">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {content}
      </ReactMarkdown>
    </div>
  )
}
