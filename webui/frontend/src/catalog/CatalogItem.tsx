/**
 * CatalogItem — single story renderer.
 * Route: /catalog/:name
 *
 * Loads the story by URL slug and renders it isolated in a neutral wrapper.
 */

import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, ExternalLink } from 'lucide-react'
import { findStory, stories } from './stories'

export function CatalogItem() {
  const { name } = useParams<{ name: string }>()
  const story = name ? findStory(name) : undefined

  if (!story) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 px-6 text-slate-200">
        <div className="max-w-md text-center">
          <h1 className="mb-2 text-xl font-semibold text-slate-100">
            Story not found
          </h1>
          <p className="mb-6 text-sm text-slate-400">
            No story named <code className="font-mono text-slate-300">"{name}"</code>.
          </p>
          <Link
            to="/catalog"
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-700"
          >
            <ArrowLeft className="h-4 w-4" /> All stories
          </Link>
        </div>
      </div>
    )
  }

  const idx = stories.findIndex((s) => s.name === story.name)
  const prev = idx > 0 ? stories[idx - 1] : null
  const next = idx < stories.length - 1 ? stories[idx + 1] : null

  return (
    <div className="min-h-screen bg-slate-950 px-6 py-6 text-slate-200">
      {/* Top bar */}
      <div className="mx-auto mb-6 flex max-w-6xl items-center justify-between">
        <Link
          to="/catalog"
          className="inline-flex items-center gap-1.5 text-sm text-slate-400 hover:text-slate-200"
        >
          <ArrowLeft className="h-4 w-4" /> All stories
        </Link>
        <div className="flex items-center gap-2">
          {prev && (
            <Link
              to={`/catalog/${prev.name}`}
              className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-300 hover:bg-slate-700"
            >
              ← {prev.title}
            </Link>
          )}
          {next && (
            <Link
              to={`/catalog/${next.name}`}
              className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-300 hover:bg-slate-700"
            >
              {next.title} →
            </Link>
          )}
        </div>
      </div>

      {/* Header */}
      <div className="mx-auto mb-6 max-w-6xl">
        <div className="flex items-baseline gap-3">
          <h1 className="text-xl font-semibold text-slate-100">{story.title}</h1>
          <code className="font-mono text-xs text-slate-500">/catalog/{story.name}</code>
        </div>
        <p className="mt-1 text-sm text-slate-400">{story.description}</p>
      </div>

      {/* Isolated render surface — no app chrome */}
      <div
        data-testid="catalog-stage"
        className="mx-auto max-w-6xl rounded-lg border border-slate-800 bg-slate-900 p-6"
      >
        {story.render()}
      </div>

      <footer className="mx-auto mt-6 max-w-6xl text-center text-xs text-slate-600">
        <ExternalLink className="mr-1 inline h-3 w-3" />
        Edit{' '}
        <code className="font-mono text-slate-500">src/catalog/stories.tsx</code>{' '}
        to add new stories.
      </footer>
    </div>
  )
}