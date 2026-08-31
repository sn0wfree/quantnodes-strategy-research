// StudyAgentPalette — catalog fetch, required agents locked, and the
// fixed 仅必选 behavior (clearOptional keeps ONLY required agents).

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

vi.mock('../api/client', () => ({
  api: { study: { agents: vi.fn() } },
}))

import { api } from '../api/client'
import { StudyAgentPalette } from '../components/study/StudyAgentPalette'

const mockAgents = vi.mocked(api.study.agents)

const CATALOG = {
  status: 'ok' as const,
  required: ['researcher', 'strategist'],
  agents: [
    { id: 'researcher', name: '研究员', category: 'research', description: '产生假设', keywords: ['hypothesis'] },
    { id: 'strategist', name: '策略师', category: 'planning', description: '编排策略', keywords: [] },
    { id: 'risk_controller', name: '风控', category: 'risk', description: '风险评估', keywords: ['risk'] },
  ],
}

describe('StudyAgentPalette', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockAgents.mockResolvedValue(CATALOG as never)
  })

  it('shows the loading state, then the catalog', async () => {
    render(
      <StudyAgentPalette
        selected={new Set()}
        onChange={vi.fn()}
        required={['researcher']}
      />,
    )
    expect(screen.getByText(/加载 agent catalog/)).toBeInTheDocument()
    expect(await screen.findByText('可用 Agents (3)')).toBeInTheDocument()
    expect(screen.getByText('研究员')).toBeInTheDocument()
    expect(screen.getByText('必选')).toBeInTheDocument() // researcher row badge
  })

  it('required agents render a disabled checked checkbox', async () => {
    render(
      <StudyAgentPalette
        selected={new Set(['researcher'])}
        onChange={vi.fn()}
        required={['researcher']}
      />,
    )
    const boxes = await screen.findAllByRole('checkbox')
    const box = boxes[0] as HTMLInputElement // researcher is first in the catalog
    expect(box.checked).toBe(true)
    expect(box.disabled).toBe(true)
    // optional agents stay enabled
    expect((boxes[2] as HTMLInputElement).disabled).toBe(false)
  })

  it('toggling an optional agent calls onChange with the next set', async () => {
    const onChange = vi.fn()
    render(
      <StudyAgentPalette
        selected={new Set(['researcher'])}
        onChange={onChange}
        required={['researcher']}
      />,
    )
    const boxes = await screen.findAllByRole('checkbox')
    fireEvent.click(boxes[2]) // risk_controller (optional)
    expect(onChange).toHaveBeenCalledWith(new Set(['researcher', 'risk_controller']))
  })

  it('仅必选 keeps ONLY the required agents (not previously selected)', async () => {
    const onChange = vi.fn()
    render(
      <StudyAgentPalette
        selected={new Set(['researcher', 'risk_controller'])}
        onChange={onChange}
        required={['researcher']}
      />,
    )
    fireEvent.click(await screen.findByRole('button', { name: '仅必选' }))
    // risk_controller was selected but is optional → dropped
    expect(onChange).toHaveBeenCalledWith(new Set(['researcher']))
  })

  it('shows the error state when the catalog fetch fails', async () => {
    mockAgents.mockRejectedValueOnce(new Error('boom') as never)
    render(<StudyAgentPalette selected={new Set()} onChange={vi.fn()} />)
    expect(await screen.findByText(/agent 加载失败: boom/)).toBeInTheDocument()
  })
})
