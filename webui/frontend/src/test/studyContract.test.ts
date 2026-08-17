/**
 * Study API 契约一致性测试。
 *
 * 后端 route 已全部挂 Pydantic response_model（api/schemas/study.py），
 * OpenAPI schema 是唯一真相源（webui/frontend/openapi.json 提交入仓）。
 * 前端 client.ts 里 study 手写类型必须与 schema 的关键字段对齐，
 * 否则 ResponseValidationError / 类型漂移会在运行时才暴露。
 *
 * 此测试只验证关键字段存在性 + 字段名一致性（防漂移的 smoke test）；
 * 完整类型走 openapi-typescript 生成的 types.gen.ts。
 */
import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

import type {
  StudyStartResponse,
  StudyStatusResponse,
  StudyListResponse,
  StudySummaryResponse,
  StudyControlResponse,
  StudyDirectivesResponse,
  StudyRoundSummary,
  MetricTarget,
  StudySummary,
  LeverScoreSummary,
  StudyDirectiveItem,
} from '../api/client'

// eslint-disable-next-line @typescript-eslint/no-require-imports
const openapi = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../../openapi.json'), 'utf-8')
)

function schemaRef(ref: string) {
  const name = ref.split('/').pop() ?? ''
  return openapi.components?.schemas?.[name]
}

describe('study API contract (openapi.json ↔ client.ts)', () => {
  it('openapi.json has study paths', () => {
    for (const p of [
      '/api/study/start',
      '/api/study/list',
      '/api/study/status',
      '/api/study/{study_id}/summary',
      '/api/study/{study_id}/rounds',
      '/api/study/{study_id}/pause',
      '/api/study/{study_id}/resume',
      '/api/study/{study_id}/cancel',
      '/api/study/{study_id}/directive',
      '/api/study/{study_id}/directives',
      '/api/study/{study_id}/journal',
      '/api/study/{study_id}/guidance',
      '/api/study/{study_id}/rounds/{round_num}/summary_md',
    ]) {
      expect(openapi.paths[p], `missing path ${p}`).toBeDefined()
    }
  })

  it('summary response includes metric_targets + last_traceback', () => {
    const summary = openapi.paths['/api/study/{study_id}/summary']
    const ref = summary.get.responses['200'].content['application/json'].schema
    const model = schemaRef(ref.$ref)
    expect(model).toBeDefined()
    for (const field of [
      'study_id', 'execution_status', 'current_round', 'metric_targets',
      'last_traceback', 'recent_rounds', 'scoreboard', 'goal_snapshot',
    ]) {
      expect(model.properties[field], `missing summary field ${field}`).toBeDefined()
    }
  })

  it('round shape uses round_num (never round)', () => {
    const rounds = openapi.paths['/api/study/{study_id}/rounds']
    const ref = rounds.get.responses['200'].content['application/json'].schema
    const model = schemaRef(ref.$ref)
    expect(model).toBeDefined()
    // rounds array items reference StudyRoundModel
    const items = model.properties.rounds.items
    const roundModel = schemaRef(items.$ref)
    expect(roundModel.properties.round_num).toBeDefined()
    expect(roundModel.properties.round).toBeUndefined()
    expect(roundModel.properties.factor_failures).toBeDefined()
    expect(roundModel.properties.verdict_reason).toBeDefined()
    expect(roundModel.properties.error).toBeDefined()
  })

  it('rounds total is a number from DB COUNT (pagination honest)', () => {
    const rounds = openapi.paths['/api/study/{study_id}/rounds']
    const ref = rounds.get.responses['200'].content['application/json'].schema
    const model = schemaRef(ref.$ref)
    expect(model.properties.total.type).toBe('integer')
    expect(model.properties.offset.type).toBe('integer')
    expect(model.properties.limit.type).toBe('integer')
  })

  it('list response includes next_cursor for keyset pagination', () => {
    const list = openapi.paths['/api/study/list']
    const ref = list.get.responses['200'].content['application/json'].schema
    const model = schemaRef(ref.$ref)
    expect(model.properties.next_cursor).toBeDefined()
  })

  it('client.ts hand-written types cover the core fields', () => {
    // Compile-time checks: accessing a missing key errors (ts-expect-error
    // would fire if the key did not exist).
    const status = {} as StudyStatusResponse
    status.study_id satisfies string | undefined
    status.execution_status satisfies string | undefined
    status.metric_targets satisfies MetricTarget[] | undefined
    status.goal_snapshot satisfies unknown

    const start = {} as StudyStartResponse
    start.study_id satisfies string
    start.execution_status satisfies string

    const list = {} as StudyListResponse
    list.studies satisfies StudySummary[]

    const summary = {} as StudySummaryResponse
    summary.recent_rounds satisfies StudyRoundSummary[]
    summary.scoreboard satisfies LeverScoreSummary[]

    const ctrl = {} as StudyControlResponse
    ctrl.action satisfies string

    const dirs = {} as StudyDirectivesResponse
    dirs.directives satisfies StudyDirectiveItem[]
  })

  it('client.ts round summary uses round_num (never round)', () => {
    const r = {} as StudyRoundSummary
    r.round_num satisfies number
    // @ts-expect-error — round must NOT exist on the wire type
    r.round satisfies number
  })
})
