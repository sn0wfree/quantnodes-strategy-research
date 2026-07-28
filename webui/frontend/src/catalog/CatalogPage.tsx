/**
 * CatalogPage — index of all stories.
 * Route: /catalog
 *
 * Renders a grid of all stories grouped by category. Click a card to
 * navigate to /catalog/:name.
 */

import { Link } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import {
  categoryIcons,
  categoryLabels,
  storiesByCategory,
  type StoryCategory,
} from './stories'

const ORDERED_CATEGORIES: StoryCategory[] = ['common', 'chat', 'agent', 'workflow']

export function CatalogPage() {
  const grouped = storiesByCategory()

  return (
    <div className="min-h-screen bg-slate-950 px-6 py-8 text-slate-200">
      {/* Header */}
      <div className="mx-auto max-w-6xl">
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-slate-100">Component Catalog</h1>
            <p className="mt-1 text-sm text-slate-400">
              Storybook-style index of all UI components. Click any card to view
              an isolated render at <code className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-xs">/catalog/&lt;name&gt;</code>.
            </p>
          </div>
          <Link
            to="/"
            className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-700"
          >
            ← Back
          </Link>
        </div>

        {/* Category sections */}
        {ORDERED_CATEGORIES.map((category) => {
          const items = grouped[category]
          if (items.length === 0) return null
          const Icon = categoryIcons[category]
          return (
            <section key={category} className="mb-10">
              <div className="mb-3 flex items-center gap-2">
                <Icon className="h-5 w-5 text-slate-400" />
                <h2 className="text-lg font-medium text-slate-200">
                  {categoryLabels[category]}
                </h2>
                <span className="text-xs text-slate-500">
                  ({items.length} {items.length === 1 ? 'story' : 'stories'})
                </span>
              </div>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
                {items.map((story) => (
                  <Link
                    key={story.name}
                    to={`/catalog/${story.name}`}
                    data-testid={`catalog-card-${story.name}`}
                    className="group flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/50 p-4 transition-colors hover:border-primary-600 hover:bg-slate-800/70"
                  >
                    <div className="min-w-0 flex-1">
                      <h3 className="truncate text-sm font-medium text-slate-100">
                        {story.title}
                      </h3>
                      <p className="mt-0.5 truncate text-xs text-slate-500">
                        {story.description}
                      </p>
                      <p className="mt-1 font-mono text-[10px] text-slate-600">
                        /catalog/{story.name}
                      </p>
                    </div>
                    <ChevronRight className="h-4 w-4 flex-shrink-0 text-slate-600 transition-colors group-hover:text-primary-400" />
                  </Link>
                ))}
              </div>
            </section>
          )
        })}

        <footer className="mt-12 border-t border-slate-800 pt-6 text-center text-xs text-slate-600">
          Catalog is dev-only by default. Set{' '}
          <code className="rounded bg-slate-800 px-1.5 py-0.5 font-mono">
            VITE_ENABLE_CATALOG=1
          </code>{' '}
          to enable in production builds.
        </footer>
      </div>
    </div>
  )
}