// DefinitionWorkflowPage tests — list, edit mode, save, run, approval flow.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { DefinitionWorkflowPage } from '../components/workflow/DefinitionWorkflowPage'
import { ApprovalDialog } from '../components/workflow/ApprovalDialog'
import type { DefinitionRunSnapshot } from '../api/client'

vi.mock('../api/client', async () => {
  return {
    api: {
      definitions: {
        list: vi.fn(async () => ({
          status: 'ok',
          definitions: [
            { name: 'plan_execute_auto', source: 'builtin', description: '自动', node_count: 2 },
            { name: 'my_flow', source: 'user', description: '自定义', node_count: 1 },
          ],
        })),
        get: vi.fn(async (name: string) => ({
          status: 'ok',
          definition: { name, source: 'user', nodes: [], edges: [] },
        })),
        save: vi.fn(async () => ({ status: 'ok', name: 'x', path: '', nodes: 0, edges: 0 })),
        remove: vi.fn(async () => ({ status: 'ok', deleted: 'x' })),
        copy: vi.fn(async () => ({ status: 'ok', name: 'x', path: '' })),
        graph: vi.fn(async () => ({ status: 'ok', name: 'x', nodes: [], edges: [] })),
      },
      definitionRuns: {
        start: vi.fn(async () => ({
          status: 'ok',
          run_id: 'wf_test1',
          run: { run_id: 'wf_test1', definition: 'plan_execute_auto', status: 'completed',
                 completed_nodes: ['planner'], failures: [], findings: [],
                 segment_idx: 1, segments_total: 1, replan_count: 0, replan_max: 3,
                 elapsed_s: 1 } as DefinitionRunSnapshot,
        })),
        approve: vi.fn(async () => ({
          status: 'ok', run_id: 'wf_test1',
          run: { run_id: 'wf_test1', definition: 'plan_execute_auto', status: 'completed',
                 completed_nodes: ['planner', 'evaluator'], failures: [], findings: [],
                 segment_idx: 1, segments_total: 1, replan_count: 0, replan_max: 3, elapsed_s: 1 },
        })),
        status: vi.fn(async () => ({ status: 'ok', run_id: 'wf_test1', run: {} })),
        detail: vi.fn(async () => ({ status: 'ok', run: {}, segments: [], node_outputs: [], approvals: [] })),
        remove: vi.fn(async () => ({ status: 'ok', deleted: 'wf_test1' })),
      },
    },
    ApiError: class extends Error {},
  }
})

vi.mock('lucide-react', () => {
  const Stub = () => null
  return {
    ArrowLeft: Stub, Play: Stub, Plus: Stub, Save: Stub, Pencil: Stub,
    Trash2: Stub, Copy: Stub, RefreshCw: Stub, Loader2: Stub,
    Bot: Stub, CalendarCheck: Stub, ClipboardList: Stub, Gauge: Stub,
    Code2: Stub, Wrench: Stub, Check: Stub, X: Stub,
    Clock: Stub, CheckCircle: Stub, XCircle: Stub, AlertCircle: Stub,
    ArrowRight: Stub, FileJson: Stub,
    Boxes: Stub, ListChecks: Stub, ChevronDown: Stub, ChevronUp: Stub,
    Search: Stub, LayoutGrid: Stub, Undo2: Stub, Redo2: Stub,
  }
})

vi.mock('@xyflow/react', async () => {
  const react = await import('react')
  return {
    ReactFlow: ({ children }: { children?: React.ReactNode }) =>
      react.createElement('div', { 'data-testid': 'reactflow' }, children),
    ReactFlowProvider: ({ children }: { children?: React.ReactNode }) =>
      react.createElement(react.Fragment, null, children),
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    addEdge: (conn: unknown, edges: unknown[]) => [...edges, conn],
    useReactFlow: () => ({
      screenToFlowPosition: (p: { x: number; y: number }) => p,
    }),
    useNodesState: (init: unknown[]) => {
      const [nodes, setNodes] = react.useState(init)
      return [nodes, setNodes, () => {}]
    },
    useEdgesState: (init: unknown[]) => {
      const [edges, setEdges] = react.useState(init)
      return [edges, setEdges, () => {}]
    },
  }
})

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return { ...actual, useNavigate: () => () => {} }
})

import { api } from '../api/client'
import { useSessionStore } from '../stores/session'

beforeEach(() => {
  useSessionStore.setState({ currentSessionId: 'sess-1' })
  vi.mocked(api.definitions.list).mockClear()
  vi.mocked(api.definitions.save).mockClear()
  vi.mocked(api.definitionRuns.start).mockClear()
})

describe('DefinitionWorkflowPage', () => {
  const openDefsTab = async () => {
    fireEvent.click(screen.getByRole('button', { name: '定义库' }))
    await waitFor(() => expect(screen.getByText('plan_execute_auto')).toBeTruthy())
  }

  it('switches between 节点库 and 定义库 tabs', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    // Default tab is the node palette
    expect(screen.getByText('节点库')).toBeTruthy()
    expect(screen.getByText('子 Agent')).toBeTruthy()
    await openDefsTab()
    // Back to palette
    fireEvent.click(screen.getByText('节点库'))
    expect(screen.getByText('子 Agent')).toBeTruthy()
  })

  it('loads and lists definitions with source badges', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await openDefsTab()
    expect(screen.getByText('my_flow')).toBeTruthy()
    expect(screen.getAllByText(/内置|用户/).length).toBeGreaterThan(0)
  })

  it('enters edit mode and saves a definition', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await openDefsTab()
    fireEvent.click(screen.getByText('my_flow'))
    await waitFor(() => expect(api.definitions.get).toHaveBeenCalledWith('my_flow'))
    // Editor visible with the save button
    await waitFor(() => expect(screen.getByText('保存定义')).toBeTruthy())
    fireEvent.click(screen.getByText('保存定义'))
    await waitFor(() => expect(api.definitions.save).toHaveBeenCalled())
    const payload = vi.mocked(api.definitions.save).mock.calls[0][0]
    expect(payload).toHaveProperty('name', 'my_flow')
    expect(payload).toHaveProperty('nodes')
  })

  it('stays in edit mode after saving (shows 已保存 hint)', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await openDefsTab()
    fireEvent.click(screen.getByText('my_flow'))
    await waitFor(() => expect(screen.getByText('保存定义')).toBeTruthy())
    fireEvent.click(screen.getByText('保存定义'))
    await waitFor(() => expect(api.definitions.save).toHaveBeenCalled())
    // still in the editor, not kicked back to the empty state
    await waitFor(() => expect(screen.getByText(/已保存/)).toBeTruthy())
    expect(screen.getByText('保存定义')).toBeTruthy()
  })

  it('opens a fresh canvas for a new definition and clears it when switching', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await openDefsTab()
    fireEvent.click(screen.getByText('my_flow'))
    await waitFor(() => expect(screen.getByText('保存定义')).toBeTruthy())
    // Empty canvas placeholder
    expect(screen.getByText('空画布')).toBeTruthy()
    // New definition resets the editor to an empty canvas as well
    fireEvent.click(screen.getByRole('button', { name: '节点库' }))
    fireEvent.click(screen.getByText('新建定义'))
    expect(screen.getByText('空画布')).toBeTruthy()
  })

  it('adds a node from the palette and clears the empty canvas hint', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await openDefsTab()
    fireEvent.click(screen.getByText('my_flow'))
    await waitFor(() => expect(screen.getByText('保存定义')).toBeTruthy())
    expect(screen.getByText('空画布')).toBeTruthy()
    fireEvent.click(screen.getByText('生成计划'))
    await waitFor(() => expect(screen.queryByText('空画布')).not.toBeInTheDocument())
  })

  it('filters the palette by search', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await openDefsTab()
    fireEvent.click(screen.getByText('my_flow'))
    await waitFor(() => expect(screen.getByText('保存定义')).toBeTruthy())
    const search = screen.getByPlaceholderText('搜索节点…')
    fireEvent.change(search, { target: { value: '生成计划' } })
    expect(screen.getByText('生成计划')).toBeTruthy()
    expect(screen.queryByText('子 Agent')).not.toBeInTheDocument()
  })

  it('starts a run from the run drawer', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await openDefsTab()
    fireEvent.click(screen.getByText('plan_execute_auto'))
    await waitFor(() => expect(screen.getByText('保存定义')).toBeTruthy())
    // Open the bottom run drawer via the top bar 运行 button
    fireEvent.click(screen.getByText('运行'))
    await waitFor(() => expect(screen.getByText('启动运行')).toBeTruthy())
    const objective = screen.getByPlaceholderText(/例：找出沪深300/)
    fireEvent.change(objective, { target: { value: '研究动量' } })
    fireEvent.click(screen.getByText('启动运行'))
    await waitFor(() => expect(api.definitionRuns.start).toHaveBeenCalled())
    const args = vi.mocked(api.definitionRuns.start).mock.calls[0]
    expect(args[0]).toBe('sess-1')
    expect(args[1]).toBe('plan_execute_auto')
    expect(args[2]).toBe('研究动量')
    await waitFor(() => expect(screen.getByText('wf_test1')).toBeTruthy())
  })
})

describe('ApprovalDialog', () => {
  it('renders plan preview and approve/reject actions', () => {
    const onApprove = vi.fn()
    const onReject = vi.fn()
    render(
      <ApprovalDialog
        open
        runId="wf_x"
        planPreview="计划步骤：plan_1 → plan_2"
        busy={false}
        onApprove={onApprove}
        onReject={onReject}
        onClose={() => {}}
      />,
    )
    expect(screen.getByText('工作流等待人工确认')).toBeTruthy()
    expect(screen.getByText(/plan_1 → plan_2/)).toBeTruthy()
    fireEvent.click(screen.getByText('批准执行'))
    expect(onApprove).toHaveBeenCalled()
    fireEvent.click(screen.getByText('拒绝并重规划'))
    expect(onReject).toHaveBeenCalled()
  })

  it('passes edit notes to approve/reject', () => {
    const onReject = vi.fn()
    render(
      <ApprovalDialog
        open
        runId="wf_x"
        planPreview=""
        busy={false}
        onApprove={() => {}}
        onReject={onReject}
        onClose={() => {}}
      />,
    )
    const notes = screen.getByPlaceholderText(/例：换一个方向/)
    fireEvent.change(notes, { target: { value: '先验证数据' } })
    fireEvent.click(screen.getByText('拒绝并重规划'))
    expect(onReject).toHaveBeenCalledWith('先验证数据')
  })
})

describe('JSON import', () => {
  const GOOD_JSON = JSON.stringify({
    name: 'imported_flow',
    nodes: [
      { id: 'p', type: 'planner', label: '生成计划', config: { max_steps: 6 } },
      { id: 'e', type: 'evaluator', label: '评估', config: {} },
    ],
    edges: [{ source: 'p', target: 'e' }],
  })

  const openImportDialog = async () => {
    fireEvent.click(screen.getByText('导入'))
    await waitFor(() => expect(screen.getByText('导入工作流定义 (JSON)')).toBeTruthy())
  }

  it('imports JSON into the canvas (edit without saving)', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('子 Agent')).toBeTruthy())
    await openImportDialog()
    const ta = screen.getByPlaceholderText(/my_workflow/)
    fireEvent.change(ta, { target: { value: GOOD_JSON } })
    fireEvent.click(screen.getByText('导入到画布（不保存）'))
    // Editor opens with the imported definition name (no save API call)
    await waitFor(() => expect(screen.getByDisplayValue('imported_flow')).toBeTruthy())
    expect(screen.getByText('保存定义')).toBeTruthy()
    expect(api.definitions.save).not.toHaveBeenCalled()
  })

  it('validates and saves JSON via API', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('子 Agent')).toBeTruthy())
    await openImportDialog()
    const ta = screen.getByPlaceholderText(/my_workflow/)
    fireEvent.change(ta, { target: { value: GOOD_JSON } })
    fireEvent.click(screen.getByText('校验并保存'))
    await waitFor(() => expect(api.definitions.save).toHaveBeenCalled())
    const payload = vi.mocked(api.definitions.save).mock.calls[0][0]
    expect(payload.name).toBe('imported_flow')
    expect(payload.nodes).toHaveLength(2)
    expect(payload.edges).toEqual([{ source: 'p', target: 'e' }])
  })

  it('rejects invalid JSON with an error message', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('子 Agent')).toBeTruthy())
    await openImportDialog()
    const ta = screen.getByPlaceholderText(/my_workflow/)
    fireEvent.change(ta, { target: { value: '{not json' } })
    fireEvent.click(screen.getByText('校验并保存'))
    await waitFor(() => expect(screen.getByText(/JSON 解析失败/)).toBeTruthy())
    expect(api.definitions.save).not.toHaveBeenCalled()
  })
})
