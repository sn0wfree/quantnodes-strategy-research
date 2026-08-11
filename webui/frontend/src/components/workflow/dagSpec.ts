import { NODE_PALETTE, CONFIG_FIELDS } from './nodeTypes'

/** Pure helpers for the orchestration chat: validating / diffing /
 *  sanitizing DAG specs produced by the LLM. All functions are
 *  side-effect free so they can be unit tested in isolation. */

export interface DagSpecNode {
  id: string
  type: string
  label?: string
  config?: Record<string, unknown>
}

export interface DagSpecEdge {
  source: string
  target: string
}

export interface DagSpec {
  nodes: DagSpecNode[]
  edges: DagSpecEdge[]
}

const VALID_TYPES = NODE_PALETTE.map((p) => p.type)
const SINGLETON_TYPES = ['planner', 'evaluator', 'approval']
const ID_RE = /^[a-zA-Z_][\w-]*$/

/** Validate a DAG spec; returns [] when valid. Mirrors the backend's
 *  WorkflowDefinition.validate() so LLM output is rejected here exactly
 *  like it would be at save time. */
export function validateDag(nodes: DagSpecNode[], edges: DagSpecEdge[]): string[] {
  const errors: string[] = []
  if (nodes.length === 0) {
    errors.push('DAG 不能为空（至少 1 个节点）')
  }
  const seen = new Set<string>()
  for (const n of nodes) {
    if (!n.id || !ID_RE.test(n.id)) {
      errors.push(`节点 id '${n.id}' 必须匹配 ^[a-zA-Z_][\\w-]*$`)
      continue
    }
    if (seen.has(n.id)) {
      errors.push(`重复节点 id '${n.id}'`)
      continue
    }
    seen.add(n.id)
    if (!VALID_TYPES.includes(n.type as (typeof VALID_TYPES)[number])) {
      errors.push(`节点 '${n.id}': 未知类型 '${n.type}'（可选 ${VALID_TYPES.join('/')}）`)
    }
  }
  for (const stype of SINGLETON_TYPES) {
    const count = nodes.filter((n) => n.type === stype).length
    if (count > 1) {
      errors.push(`类型 '${stype}' 最多出现 1 次（当前 ${count}）`)
    }
  }
  for (const e of edges) {
    if (!seen.has(e.source)) errors.push(`连线起点 '${e.source}' 不存在`)
    if (!seen.has(e.target)) errors.push(`连线终点 '${e.target}' 不存在`)
    if (e.source === e.target) errors.push(`节点 '${e.source}' 自环`)
  }
  // Cycle detection (Kahn's algorithm)
  const indeg = new Map<string, number>()
  const adj = new Map<string, string[]>()
  for (const n of nodes) {
    indeg.set(n.id, 0)
    adj.set(n.id, [])
  }
  for (const e of edges) {
    if (seen.has(e.source) && seen.has(e.target) && e.source !== e.target) {
      indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1)
      adj.get(e.source)?.push(e.target)
    }
  }
  const queue = nodes.filter((n) => indeg.get(n.id) === 0).map((n) => n.id)
  let visited = 0
  while (queue.length > 0) {
    const id = queue.shift()!
    visited++
    for (const t of adj.get(id) ?? []) {
      const next = (indeg.get(t) ?? 0) - 1
      indeg.set(t, next)
      if (next === 0) queue.push(t)
    }
  }
  if (visited < nodes.length) {
    errors.push('DAG 存在环（依赖不能成环）')
  }
  return errors
}

export interface DagDiff {
  addedNodes: DagSpecNode[]
  removedNodes: DagSpecNode[]
  updatedNodes: Array<{ before: DagSpecNode; after: DagSpecNode }>
  addedEdges: DagSpecEdge[]
  removedEdges: DagSpecEdge[]
}

const edgeKey = (e: DagSpecEdge) => `${e.source}->${e.target}`

/** Diff two DAG specs (previous vs next). Used to summarize what the
 *  LLM changed in this incremental step. */
export function diffDag(prev: DagSpec, next: DagSpec): DagDiff {
  const prevNodes = new Map(prev.nodes.map((n) => [n.id, n]))
  const nextNodes = new Map(next.nodes.map((n) => [n.id, n]))
  const addedNodes: DagSpecNode[] = []
  const removedNodes: DagSpecNode[] = []
  const updatedNodes: Array<{ before: DagSpecNode; after: DagSpecNode }> = []
  for (const n of next.nodes) {
    if (!prevNodes.has(n.id)) addedNodes.push(n)
  }
  for (const n of prev.nodes) {
    if (!nextNodes.has(n.id)) removedNodes.push(n)
  }
  for (const n of next.nodes) {
    const p = prevNodes.get(n.id)
    if (p && JSON.stringify(p) !== JSON.stringify(n)) {
      updatedNodes.push({ before: p, after: n })
    }
  }
  const prevKeys = new Set(prev.edges.map(edgeKey))
  const nextKeys = new Set(next.edges.map(edgeKey))
  const addedEdges = next.edges.filter((e) => !prevKeys.has(edgeKey(e)))
  const removedEdges = prev.edges.filter((e) => !nextKeys.has(edgeKey(e)))
  return { addedNodes, removedNodes, updatedNodes, addedEdges, removedEdges }
}

/** Human-readable summary of a diff (for the chat UI). */
export function diffSummary(diff: DagDiff): string {
  const parts: string[] = []
  if (diff.addedNodes.length > 0) {
    parts.push(`新增节点 ${diff.addedNodes.map((n) => n.label || n.id).join('、')}`)
  }
  if (diff.removedNodes.length > 0) {
    parts.push(`移除节点 ${diff.removedNodes.map((n) => n.label || n.id).join('、')}`)
  }
  if (diff.updatedNodes.length > 0) {
    parts.push(`修改节点 ${diff.updatedNodes.map((u) => u.after.label || u.after.id).join('、')}`)
  }
  if (diff.addedEdges.length > 0) {
    parts.push(`新增连线 ${diff.addedEdges.map(edgeKey).join('、')}`)
  }
  if (diff.removedEdges.length > 0) {
    parts.push(`移除连线 ${diff.removedEdges.map(edgeKey).join('、')}`)
  }
  if (parts.length === 0) return '无变化'
  return parts.join('；')
}

function sanitizeId(raw: string, taken: Set<string>): string {
  let id = String(raw ?? '')
  id = id.replace(/[^\w-]/g, '_')
  if (!ID_RE.test(id)) id = `n_${id}`
  let base = id
  let i = 2
  while (taken.has(id)) {
    id = `${base}_${i++}`
  }
  taken.add(id)
  return id
}

/** Clean an LLM-produced spec: sanitize/dedupe ids, fill default labels,
 *  inject agent colors, filter config keys per type, drop unknown types.
 *  Tolerates the LLM sending non-array nodes/edges (e.g. ""). */
export function sanitizeSpec(spec: DagSpec): DagSpec {
  const taken = new Set<string>()
  const nodes: DagSpecNode[] = []
  const rawNodes = Array.isArray(spec?.nodes) ? spec.nodes : []
  for (const n of rawNodes) {
    const meta = NODE_PALETTE.find((p) => p.type === n.type)
    if (!meta) continue
    const id = sanitizeId(n.id || `${n.type}_node`, taken)
    const label = n.label?.trim() ? n.label.trim() : meta.label
    const allowedKeys = new Set((CONFIG_FIELDS[n.type] ?? []).map((f) => f.key))
    const config: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(n.config ?? {})) {
      if (allowedKeys.has(k) && v !== undefined && v !== null && v !== '') {
        config[k] = v
      }
    }
    nodes.push({ id, type: n.type, label, config })
  }
  const edges: DagSpecEdge[] = []
  const rawEdges = Array.isArray(spec?.edges) ? spec.edges : []
  for (const e of rawEdges) {
    if (taken.has(e.source) && taken.has(e.target) && e.source !== e.target) {
      edges.push(e)
    }
  }
  return { nodes, edges }
}

/** Merge an LLM step into the current canvas: the LLM usually submits
 *  only the part it touched this round (one edit per round), so nodes
 *  already on the canvas are kept unless the spec overrides them, and
 *  edges are deduped. Returns the merged spec plus the per-part diff. */
export function mergeDagStep(current: DagSpec, step: DagSpec): DagSpec {
  const nodes: DagSpecNode[] = []
  const curMap = new Map(current.nodes.map((n) => [n.id, n]))
  for (const n of current.nodes) {
    const override = step.nodes.find((s) => s.id === n.id)
    nodes.push(override ? { ...override } : { ...n })
  }
  for (const s of step.nodes) {
    if (!curMap.has(s.id)) nodes.push({ ...s })
  }
  const key = new Set(current.edges.map(edgeKey))
  const edges = [...current.edges]
  for (const e of step.edges) {
    if (!key.has(edgeKey(e))) {
      edges.push(e)
      key.add(edgeKey(e))
    }
  }
  return { nodes, edges }
}
