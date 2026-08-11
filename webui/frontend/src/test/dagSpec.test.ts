import { describe, it, expect } from 'vitest'
import { validateDag, diffDag, diffSummary, sanitizeSpec, mergeDagStep, type DagSpec } from '../components/workflow/dagSpec'

const llm = (id: string) => ({ id, type: 'llm_agent', label: id, config: { role: 'researcher' } })

describe('validateDag', () => {
  it('accepts a valid DAG', () => {
    expect(validateDag([llm('a'), llm('b')], [{ source: 'a', target: 'b' }])).toEqual([])
  })

  it('rejects unknown types', () => {
    const errors = validateDag([{ id: 'a', type: 'chatbot', label: 'A' }], [])
    expect(errors.some((e) => e.includes('未知类型'))).toBe(true)
  })

  it('rejects duplicate ids', () => {
    const errors = validateDag([llm('a'), llm('a')], [])
    expect(errors.some((e) => e.includes('重复节点'))).toBe(true)
  })

  it('rejects bad ids', () => {
    const errors = validateDag([{ id: 'bad id!', type: 'llm_agent' }], [])
    expect(errors.some((e) => e.includes('必须匹配'))).toBe(true)
  })

  it('rejects more than one singleton', () => {
    const errors = validateDag(
      [
        { id: 'a', type: 'approval' },
        { id: 'b', type: 'approval' },
        llm('c'),
      ],
      [{ source: 'a', target: 'c' }, { source: 'b', target: 'c' }],
    )
    expect(errors.some((e) => e.includes('approval') && e.includes('最多'))).toBe(true)
  })

  it('rejects dangling edge refs', () => {
    const errors = validateDag([llm('a')], [{ source: 'a', target: 'ghost' }])
    expect(errors.some((e) => e.includes('ghost'))).toBe(true)
  })

  it('rejects cycles', () => {
    const errors = validateDag(
      [llm('a'), llm('b')],
      [{ source: 'a', target: 'b' }, { source: 'b', target: 'a' }],
    )
    expect(errors.some((e) => e.includes('环'))).toBe(true)
  })

  it('rejects empty DAGs', () => {
    expect(validateDag([], []).some((e) => e.includes('不能为空'))).toBe(true)
  })
})

describe('diffDag', () => {
  const prev: DagSpec = {
    nodes: [llm('a'), llm('b')],
    edges: [{ source: 'a', target: 'b' }],
  }

  it('detects added/removed/updated nodes and edges', () => {
    const next: DagSpec = {
      nodes: [
        { ...llm('a'), label: '改过的A' },
        llm('c'),
      ],
      edges: [{ source: 'a', target: 'c' }],
    }
    const d = diffDag(prev, next)
    expect(d.addedNodes.map((n) => n.id)).toEqual(['c'])
    expect(d.removedNodes.map((n) => n.id)).toEqual(['b'])
    expect(d.updatedNodes.map((u) => u.after.id)).toEqual(['a'])
    expect(d.addedEdges).toEqual([{ source: 'a', target: 'c' }])
    expect(d.removedEdges).toEqual([{ source: 'a', target: 'b' }])
  })

  it('reports no changes for identical specs', () => {
    const d = diffDag(prev, JSON.parse(JSON.stringify(prev)))
    expect(diffSummary(d)).toBe('无变化')
  })

  it('summarizes in Chinese', () => {
    const d = diffDag(prev, { nodes: [...prev.nodes, llm('c')], edges: [...prev.edges, { source: 'b', target: 'c' }] })
    const s = diffSummary(d)
    expect(s).toContain('新增节点')
    expect(s).toContain('新增连线')
  })
})

describe('sanitizeSpec', () => {
  it('sanitizes and dedupes ids', () => {
    const s = sanitizeSpec({
      nodes: [
        { id: '中文 id', type: 'llm_agent', label: 'A', config: {} },
        { id: '中文 id', type: 'llm_agent', label: 'B', config: {} },
        { id: '2start', type: 'llm_agent', label: 'C', config: {} },
      ],
      edges: [],
    })
    expect(s.nodes[0].id).toMatch(/^[a-zA-Z_][\w-]*$/)
    expect(s.nodes[1].id).not.toBe(s.nodes[0].id)
    expect(s.nodes[2].id).toMatch(/^n_/)
  })

  it('drops unknown types', () => {
    const s = sanitizeSpec({ nodes: [{ id: 'x', type: 'chatbot', label: 'X' }], edges: [] })
    expect(s.nodes).toEqual([])
  })

  it('fills default labels from palette', () => {
    const s = sanitizeSpec({ nodes: [{ id: 'p', type: 'planner', label: '' }], edges: [] })
    expect(s.nodes[0].label).toBe('生成计划')
  })

  it('filters config keys per type', () => {
    const s = sanitizeSpec({
      nodes: [{ id: 'a', type: 'llm_agent', label: 'A', config: { role: 'researcher', hacker: 'x', max_iterations: 3 } }],
      edges: [],
    })
    expect(s.nodes[0].config).toEqual({ role: 'researcher', max_iterations: 3 })
  })

  it('drops edges referencing dropped nodes', () => {
    const s = sanitizeSpec({
      nodes: [{ id: 'a', type: 'llm_agent', label: 'A', config: { role: 'r' } }],
      edges: [{ source: 'a', target: 'ghost' }, { source: 'a', target: 'a' }],
    })
    expect(s.edges).toEqual([])
  })

  it('tolerates non-array nodes/edges (LLM sends "")', () => {
    const s = sanitizeSpec({ nodes: [], edges: '' as never })
    expect(s.nodes).toEqual([])
    expect(s.edges).toEqual([])
  })
})

describe('mergeDagStep', () => {
  const canvas: DagSpec = {
    nodes: [llm('a'), llm('b')],
    edges: [{ source: 'a', target: 'b' }],
  }

  it('keeps canvas nodes and appends new ones', () => {
    const step: DagSpec = {
      nodes: [{ id: 'c', type: 'tool', label: 'C', config: { tool: 'x' } }],
      edges: [{ source: 'b', target: 'c' }],
    }
    const merged = mergeDagStep(canvas, step)
    expect(merged.nodes.map((n) => n.id).sort()).toEqual(['a', 'b', 'c'])
    expect(merged.edges).toEqual([
      { source: 'a', target: 'b' },
      { source: 'b', target: 'c' },
    ])
  })

  it('overrides nodes submitted by the step', () => {
    const step: DagSpec = {
      nodes: [{ id: 'a', type: 'planner', label: '改过的A' }],
      edges: [],
    }
    const merged = mergeDagStep(canvas, step)
    const a = merged.nodes.find((n) => n.id === 'a')
    expect(a?.type).toBe('planner')
    expect(a?.label).toBe('改过的A')
    expect(merged.nodes).toHaveLength(2)
  })

  it('dedupes edges', () => {
    const step: DagSpec = {
      nodes: [],
      edges: [{ source: 'a', target: 'b' }],
    }
    const merged = mergeDagStep(canvas, step)
    expect(merged.edges).toHaveLength(1)
  })
})
