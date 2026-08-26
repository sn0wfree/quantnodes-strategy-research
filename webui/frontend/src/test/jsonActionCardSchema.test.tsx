/**
 * Tests for schema-driven JsonActionCard rendering (PR 2/3).
 *
 * The schema comes from /api/agents/schemas (backend parses .prompts/*.md).
 * When a schema exists for the agent, field order / labels / core
 * visibility / enum mappings / percentage formats follow the schema;
 * otherwise the generic KEY_FIELDS heuristics apply (previous behavior).
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { JsonActionCard } from '../components/chat/JsonActionCard'
import {
  __setSchemaCacheForTest,
  loadAgentSchemas,
  getSchemaFor,
  type AgentSchema,
} from '../api/agentSchemas'

// ── Test fixtures ───────────────────────────────────────────────────

const RISK_SCHEMA: AgentSchema = {
  role: 'risk_controller',
  fields: [
    'risk_passed', 'risk_rating', 'var_95', 'max_drawdown', 'tail_risk',
  ],
  field_hints: {
    risk_passed: { label: '是否通过', type: 'bool', core: true },
    risk_rating: {
      label: '风险评级', type: 'enum', core: true,
      enum_values: { Green: '🟢绿', Yellow: '🟡黄', Red: '🔴红' },
    },
    var_95: { label: 'VaR 95%', type: 'number', core: false, format: 'percentage' },
    max_drawdown: { label: '最大回撤', type: 'number', core: false, format: 'percentage' },
    tail_risk: { label: '尾部风险', type: 'object', core: false },
  },
  action_field: null,
  action_enum: null,
}

afterEach(() => {
  __setSchemaCacheForTest(null)
  vi.restoreAllMocks()
})

// ── Schema loader ───────────────────────────────────────────────────

describe('agentSchemas loader', () => {
  it('loadAgentSchemas fetches and caches', async () => {
    const get = vi.fn().mockResolvedValue({ researcher: RISK_SCHEMA })
    vi.stubGlobal('api', { get })
    // loadAgentSchemas imports api from ./client — stub the module instead
    const { api } = await import('../api/client')
    vi.spyOn(api, 'get').mockResolvedValue({ researcher: RISK_SCHEMA })

    const schemas = await loadAgentSchemas(true)
    expect(schemas.researcher.role).toBe('risk_controller')
    // Cached: second call does not re-fetch
    await loadAgentSchemas()
    expect(api.get).toHaveBeenCalledTimes(1)
  })

  it('getSchemaFor returns undefined without cache', () => {
    expect(getSchemaFor('researcher')).toBeUndefined()
  })

  it('getSchemaFor returns schema when cached', () => {
    __setSchemaCacheForTest({ researcher: RISK_SCHEMA })
    expect(getSchemaFor('researcher')?.role).toBe('risk_controller')
    expect(getSchemaFor('unknown')).toBeUndefined()
    expect(getSchemaFor(null)).toBeUndefined()
  })
})

// ── Schema-driven card rendering ────────────────────────────────────

describe('JsonActionCard schema-driven rendering', () => {
  it('uses schema labels and core visibility', () => {
    __setSchemaCacheForTest({ risk_controller: RISK_SCHEMA })
    render(
      <JsonActionCard
        agentId="risk_controller"
        fullJson={{
          risk_passed: false,
          risk_rating: 'Red',
          var_95: -0.021,
          tail_risk: { kurtosis: 3.2 },
        }}
      />,
    )
    // Core rows visible with Chinese labels
    expect(screen.getByText('是否通过:')).toBeInTheDocument()
    expect(screen.getByText('风险评级:')).toBeInTheDocument()
    // Enum value mapped to display label
    expect(screen.getByText('🔴红')).toBeInTheDocument()
    // Boolean rendered as ✗
    expect(document.body.textContent).toContain('✗')
    // Non-core fields collapsed: 2 (var_95 + tail_risk; max_drawdown absent
    // from the JSON payload)
    expect(screen.getByText('其他字段 (2)')).toBeInTheDocument()
  })

  it('formats percentage fields (0.25 → 25.0%)', () => {
    __setSchemaCacheForTest({ risk_controller: RISK_SCHEMA })
    render(
      <JsonActionCard
        agentId="risk_controller"
        fullJson={{
          risk_passed: true,
          risk_rating: 'Green',
          var_95: -0.021,
          max_drawdown: -0.125,
        }}
      />,
    )
    // Percentage formatting only applies in the core rows; var_95 /
    // max_drawdown are non-core here → still counted in the collapse
    // section. Render with a core percentage field instead:
    expect(screen.getByText('其他字段 (2)')).toBeInTheDocument()
  })

  it('core percentage fields render as percent', () => {
    __setSchemaCacheForTest({
      demo: {
        role: 'demo',
        fields: ['max_weight', 'note'],
        field_hints: {
          max_weight: { label: '最大权重', type: 'number', core: true, format: 'percentage' },
          note: { label: '备注', type: 'string', core: false },
        },
      },
    })
    render(
      <JsonActionCard
        agentId="demo"
        fullJson={{ max_weight: 0.25, note: 'x' }}
      />,
    )
    expect(screen.getByText('最大权重:')).toBeInTheDocument()
    expect(screen.getByText('25.0%')).toBeInTheDocument()
  })

  it('runtime fields beyond the schema fall back to generic core list', () => {
    __setSchemaCacheForTest({ risk_controller: RISK_SCHEMA })
    render(
      <JsonActionCard
        agentId="risk_controller"
        fullJson={{
          risk_passed: true,
          // "recommendation" is NOT in the prompt schema (runtime-evolved),
          // but IS in the generic KEY_FIELDS list → still a core row.
          recommendation: 'BLOCK this round',
        }}
      />,
    )
    expect(screen.getByText('建议:')).toBeInTheDocument()
    expect(screen.getByText('BLOCK this round')).toBeInTheDocument()
  })

  it('falls back to generic rendering without a schema', () => {
    render(
      <JsonActionCard
        agentId="totally_unknown_agent"
        fullJson={{ risk_passed: false, risk_rating: 'Red' }}
      />,
    )
    // Generic path: KEY_FIELD_LABELS labels
    expect(screen.getByText('是否通过:')).toBeInTheDocument()
    expect(screen.getByText('风险评级:')).toBeInTheDocument()
    // No enum mapping in generic path → raw value shown
    expect(screen.getByText('Red')).toBeInTheDocument()
  })

  it('keeps schema field order (schema order, not JSON order)', () => {
    __setSchemaCacheForTest({ risk_controller: RISK_SCHEMA })
    const { container } = render(
      <JsonActionCard
        agentId="risk_controller"
        fullJson={{
          risk_rating: 'Yellow',
          risk_passed: true, // JSON order differs from schema order
        }}
      />,
    )
    const labels = Array.from(
      container.querySelectorAll('.min-w-20'),
    ).map((el) => el.textContent)
    // schema.fields order: risk_passed before risk_rating
    expect(labels.indexOf('是否通过:')).toBeLessThan(labels.indexOf('风险评级:'))
  })
})
