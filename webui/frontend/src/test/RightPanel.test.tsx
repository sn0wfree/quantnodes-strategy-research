// RightPanel — merged single panel renders 3 cards: token usage,
// performance curve, goal & study.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

vi.mock('../api/client', async () => {
  return {
    api: {
      goal: { getStatus: vi.fn() },
      study: { start: vi.fn(), status: vi.fn(), list: vi.fn() },
    },
    ApiError: class extends Error {},
  }
})

import { RightPanel } from '../components/layout/RightPanel'
import { useLayoutStore } from '../stores/layout'
import { useGoalStore, type Goal } from '../stores/goal'
import { useStudyStore } from '../stores/study'
import { useSystemStore } from '../stores/system'
import { useSessionStore } from '../stores/session'
import { useChatStore } from '../stores/chat'

function makeGoal(): Goal {
  return {
    goal_id: 'g-1',
    session_id: 's-1',
    status: 'active',
    objective: '分析 20 日动量因子 IC',
    progress_percent: 40,
    criteria: [
      { criterion_id: 'c-1', text: '计算 IC 序列', status: 'covered', required: true, evidence_count: 2 },
      { criterion_id: 'c-2', text: '保存结果', status: 'pending', required: true, evidence_count: 0 },
    ],
    evidence_count: 2,
  }
}

function setupStores() {
  useLayoutStore.setState({ rightPanelVisible: false })
  useGoalStore.setState({ currentGoal: makeGoal() })
  useStudyStore.setState({ current: null, list: [], busy: false, error: '' })
  useSystemStore.setState({
    workspacePath: '/workspace',
    modelInfo: { context_tokens: 1_000_000, source: 'fetched' } as never,
  } as never)
  useSessionStore.setState({ currentSessionId: 's-1' } as never)
  useChatStore.setState({
    messages: new Map(),
    tokensUsed: new Map([['s-1', 120_000]]),
  } as never)
}

describe('RightPanel (merged panel)', () => {
  beforeEach(() => {
    setupStores()
  })

  it('renders the two cards: token, goal & progress', () => {
    render(<RightPanel />)
    expect(screen.getByText('Token 使用情况')).toBeTruthy()
    expect(screen.getByText('目标 & 进度')).toBeTruthy()
    expect(screen.getByText('表现曲线')).toBeTruthy()
  })

  it('token card shows usage percent and message count', () => {
    render(<RightPanel />)
    expect(screen.getByText(/12\.0%/)).toBeTruthy()
    expect(screen.getByText('消息')).toBeTruthy()
  })

  it('goal section shows the active goal and its criteria', () => {
    render(<RightPanel />)
    expect(screen.getByText('分析 20 日动量因子 IC')).toBeTruthy()
    expect(screen.getByText('研究标准')).toBeTruthy()
    expect(screen.getByText('计算 IC 序列')).toBeTruthy()
  })

  it('shows empty state when no goal exists', () => {
    useGoalStore.setState({ currentGoal: null })
    render(<RightPanel />)
    expect(screen.getByText('暂无活跃目标')).toBeTruthy()
  })
})
