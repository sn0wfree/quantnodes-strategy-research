# Docs Index

> 170 个文档的导航。按主题分 7 类；每类给入口与代表，完整清单见 `docs/` 文件名。
> 原则：**设计先行**（每个结构改动先设计文档后实施）——因此同一主题常有 `*-design.md` + 执行/验收记录。

## 0. 从这里开始

| 场景 | 入口 |
|---|---|
| 想 10 分钟了解项目 | `code-map.md` · `architecture-framework.svg` |
| 想知道现在干净吗 | `structure-debt-resurvey-20261008.md`（第二轮）· `structure-debt-survey-20261008.md`（第一轮对比用） |
| 想知道扩展点放哪 | **`extension-points.md`**（7 机制分工 + 决策树） |
| 想知道接下来做什么 | `architecture-harness-recommendations-20261008.md` §3-4 + 各 `-design.md` 验收记录 |

## 1. 架构总览

`code-map.md` · `architecture-framework.svg` · `architecture-review.md` · `agent-architecture-review.md` · `autoresearch-design.md` · `backtest-engine-design.md` · `engine-comparison.md`

## 2. Study / Goal 子系统（产品主链）

`goal-design.md` · `goal-workflow-cookbook.md`（手册） · `goal-integration-plan.md` · `study-*` 系列

**本轮重构链**：`phase-engine-refactor-plan-20261008.md`（总纲 Step 0-3）→ `step1-round-outcome-design.md` → `step2-dispatch-table-design.md` → `step3-run-loop-extraction-design.md`（各含验收记录）

## 3. Agent 运行时（A1-A7 + 记忆/压缩/工具）

`a1-budget-stop-policy.md` · `a2-tool-gateway-toolsets.md` · `a3-tool-result-views.md` · `a4-memory-retriever.md` · `a5-a7-runtime-swarm-eval.md` · `agent-tools-reference.md`（手册） · `compact-design.md` · `compaction-*` 系列 · `chat-agent-refactor-phase*` 系列（10 期）

## 4. Harness / CI / Eval

`ci-sharding-strategy.md`（LPT 分片 + flaky 治理） · `test-marker-lanes-design.md`（llm/e2e/slow 三层） · `p0-guardrails-design.md`（pre-commit + eval 跟幅门禁） · `eval-case-sources.md` · `deep-evaluation-20260929.md` · `p1.5-cleanup-batch-design.md`（清扫批次执行记录） · `p2-c901-final-design.md`

## 5. 结构债与清理（两轮梳理）

`structure-debt-survey-20261008.md` → `structure-debt-resurvey-20261008.md`（同日对比） · `structure-debt-plan.md`（W1-W5 历史） · `phase-wrapup-20260929.md` · `current-state-survey-20260929.md` · `quantskills-borrow-list.md`

## 6. 调研报告（外部学习）

`agentic-design-application.md` · `Agentic 设计框架与 Prime Agent 调研报告.md` · `AI因子挖掘Agent研报梳理与QuantaAlpha深研.md` · `deepseek-harness-research.md` · `agentquant-research-20260902.md`

## 7. 专题修复记录（备查）

`*-fix.md` 系列（compaction / context-overflow / chat-sse / claim-validation 等） · `api-test-debt-fix.md` · `dag-viz-and-checkpoint-merge.md`

---

**维护约定**：新文档放进对应类目；`*-design.md` 与验收记录成对出现（本次系列的纪律）；超过 250 个文档时拆子目录。
