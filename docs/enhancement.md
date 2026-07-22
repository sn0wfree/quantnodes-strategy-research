# 借鉴增强方案（P0 + P1）

> 对应调研：`docs/vibe-trading-survey.md`（1805 行，完整功能清单）
> 对应总计划：本文件即主执行计划
> 状态：**待启动**
> 创建日期：2026-07-22

---

## 1. 总览与决策摘要

### 1.1 已锁定决策

| 决策点 | 选择 |
|---|---|
| 借鉴来源 | vibe-trading-ai 0.1.11（HKUDS，MIT License） |
| 借鉴方式 | 整包复制关键模块 + 自写最小版 |
| 当前范围 | P0 + P1（5 周） |
| Skills 范围 | P0+P1 不涉及；P2 阶段再讨论 |
| CLI 名 | 保持 `quantnodes-research` |
| LLM 集成 | OpenAI 兼容通用（httpx，零 LangChain） |
| 沙箱策略 | AST guard + 路径白名单 |
| 测试策略 | 仅 e2e smoke test |
| 交付方式 | 按阶段 PR 交付（P0 → P1 → ...） |
| License 处理 | 保留 MIT，致敬原作者（HKUDS） |

### 1.2 阶段总览

| 阶段 | 时间 | 交付 | 净增行数 | 复制行数 |
|---|---|---|---|---|
| P0 修通 init | Week 1-2 | PR #1 | ~880 | 0 |
| P1 Agent 真跑 | Week 3-5 | PR #2 | ~1 775 | ~940 |
| **合计** | **5 周** | **2 PRs** | **~2 655** | **~940** |

### 1.3 与现有文档的关系

| 文档 | 关系 |
|---|---|
| `docs/vibe-trading-survey.md` | 总览（已完成，1805 行） |
| `docs/backtest-overhaul/README.md` Phase 1 | ⊆ 本计划 P0-T0.5 |
| `docs/backtest-overhaul/README.md` Phase 3 | ⊆ 本计划 P3（暂不执行） |
| `docs/autoresearch-design.md` | agent 角色定义，本次 P1 仅重写 `_spawn_agent` |

---

## 2. P0 — 修通 init（Week 1-2，PR #1）

### 2.1 目标

`quantnodes-research init /path/to/workspace` 真正能跑出 workspace，且 baseline 回测有真实指标。

### 2.2 现状痛点（来自子代理扫描）

| # | 问题 | 影响 |
|---|---|---|
| G2 | `cmd_init()` 在 `.format()` 上踩 `{}` 报错 | workspace 生成 crash |
| G3 | CLI 与 `core/db.py` 的 DuckDB schema 重复且不一致 | init 后 baseline 导入失败 |
| G4 | `data_import.py` 把 OHLCV 压成 close 面板再丢失 | 数据丢失 bug |
| G5 | 默认 `FACTOR_EXPRS=[]`，`prepare.evaluate()` 返空指标 | baseline 不是 buy-and-hold |
| G7 | `FALLBACK_CHAINS` 引用 mootdx/eastmoney/baostock 等未实现 loader | 数据源死链 |
| G10 | `cmd_init --baseline` 选项未实现 | 文档承诺的功能缺失 |
| G17 | 没有 CLI `evaluate` 子命令 | 无法手动复跑 |

### 2.3 TODO 清单

#### Week 1 — 修 bug + 接数据源

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T0.1 | 修 `.format()` bug（改 `string.Template`）| `cli.py::_render_template` | ☐ | `cmd_init` 不再 KeyError |
| T0.2 | 统一 DuckDB schema（抽出 `_INIT_SCHEMA_SQL` 常量）| `core/db.py` + `cli.py` | ☐ | 两处共用一份 SQL |
| T0.3 | 修复 OHLCV 丢失 | `core/data_import.py` + `core/db.py::save_price_data` | ☐ | OHLV 保留，volume 非 0 |
| T0.4 | baseline `FACTOR_EXPRS` 默认值 | `templates/prepare.py` | ☐ | 跑出真实指标 |
| T0.5 | 新增 `DataLoaderProtocol` | `core/data_source/base.py`（新）| ☐ | 接口定义清晰 |
| T0.6 | 新增 `LOADER_REGISTRY` + `FALLBACK_CHAINS` | `core/data_source/registry.py`（新）| ☐ | 装饰器注册可用 |
| T0.7 | 实现 tencent loader | `core/data_source/tencent_loader.py`（新）| ☐ | `is_available()` True |
| T0.8 | 实现 eastmoney loader | `core/data_source/eastmoney_loader.py`（新）| ☐ | A 股 + 港股 EOD |
| T0.9 | 实现 akshare loader | `core/data_source/akshare_loader.py`（新）| ☐ | 多市场覆盖 |

#### Week 2 — preflight + init baseline + CLI

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T0.10 | 实现 preflight（4 项 check）| `core/preflight.py`（新）| ☐ | Rich 表输出 |
| T0.11 | preflight 检查 LLM key | `core/preflight.py` | ☐ | 检测 `OPENAI_API_KEY / DEEPSEEK_API_KEY` |
| T0.12 | preflight 检查 DuckDB 可写 | `core/preflight.py` | ☐ | 创建/删除测试表 |
| T0.13 | preflight 检查数据源 | `core/preflight.py` | ☐ | tencent.eastmoney.fetch 测试 |
| T0.14 | `cmd_init` 自动 baseline 导入 | `cli.py::cmd_init` | ☐ | init 后立刻有 OHLCV |
| T0.15 | baseline 回测 + 写 `run_0000/` | `cli.py::cmd_init` | ☐ | metrics.json 8 项 |
| T0.16 | CLI `evaluate <strategy>` 子命令 | `cli.py`（argparse）| ☐ | 手动复跑可 |
| T0.17 | 删除 mootdx/baostock 占位 | `core/data_source/` | ☐ | fallback 链更新 |
| T0.18 | 跑现有测试 | `tests/` | ☐ | 全部 PASS |

### 2.4 P0 验收

```bash
$ pip install -e .
$ quantnodes-research init /tmp/ws
✓ workspace 已创建（不再 crash）
$ ls /tmp/ws/strategies/<n>/runs/
run_0000/
$ cat /tmp/ws/strategies/<n>/runs/run_0000/metrics.json
{"sharpe": 0.42, "calmar": 0.38, "max_dd": -0.18, ...}  # 8 项真实指标
$ quantnodes-research preflight /tmp/ws
✓ LLM provider: ok
✓ DuckDB: writable
✓ Data source (tencent): reachable
✓ OHLCV integrity: ok
$ quantnodes-research evaluate /tmp/ws --strategy momentum_20_60
✓ 重新跑通
$ pytest tests/ -v
... PASSED
```

### 2.5 P0 PR 内容

```diff
src/strategy_research/
├── cli.py                       M  +85 -65
├── core/
│   ├── db.py                    M  +20 -10
│   ├── data_import.py           M  +15 -10
│   ├── data_source/
│   │   ├── base.py              +  80
│   │   ├── registry.py          +  80
│   │   ├── tencent_loader.py    +  150
│   │   ├── eastmoney_loader.py  +  150
│   │   ├── akshare_loader.py    +  150
│   │   ├── mootdx_loader.py     -  -30
│   │   └── baostock_loader.py   -  -30
│   └── preflight.py             +  200
└── templates/
    └── prepare.py               M  +5
```

---

## 3. P1 — Agent 真跑起来（Week 3-5，PR #2）

### 3.1 目标

LLM agent 通过 ReAct 循环修改 `strategy.py` 并 commit；cross-run memory 保留；3 道停止条件生效。

### 3.2 复制清单（来自 vibe-trading-ai 0.1.11，MIT License）

| 编号 | 源文件 | 行数 | 目标路径 | 改动 |
|---|---|---|---|---|
| C1 | `src/agent/tools.py` | 94 | `core/agent/tools.py` | 仅改 import |
| C2 | `src/agent/frontmatter.py` | ~50 | `core/agent/frontmatter.py` | 无 |
| C3 | `src/agent/progress.py` | ~150 | `core/agent/progress.py` | 改 home dir |
| C4 | `src/agent/trace.py` | ~300 | `core/agent/trace.py` | 改 home dir |
| C5 | `src/tools/redaction.py` | ~80 | `core/agent/redaction.py` | 仅改 import |
| C6 | `src/memory/persistent.py` | 265 | `core/memory/persistent.py` | 改 `~/.quantnodes-research/memory/` |
| **合计** | | **~940** | | |

每个文件头加：
```python
# Adapted from vibe-trading-ai 0.1.11 (MIT License, HKUDS)
# Original: https://github.com/HKUDS/Vibe-Trading
# See docs/vibe-trading-credits.md
```

### 3.3 TODO 清单

#### Week 3 — 复制 + 核心工具

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T1.1 | 复制 6 个模块（C1-C6）| `core/agent/`, `core/memory/` | ☐ | 加 LICENSE 头 |
| T1.2 | 新建 `vibe-trading-credits.md` | `docs/` | ☐ | 列出 6 个文件 + LICENSE |
| T1.3 | OpenAI 兼容 LLM 客户端 | `core/llm/openai_client.py`（新）| ☐ | httpx 调通 OpenAI/DeepSeek/Kimi/Qwen |
| T1.4 | LLM 响应解析（tool_calls）| `core/llm/parser.py`（新）| ☐ | 解析 JSON |
| T1.5 | `read_file` 工具 | `core/agent/tools/read_file.py`（新）| ☐ | 读 workspace 内任意文件 |
| T1.6 | `write_file` 工具 | `core/agent/tools/write_file.py`（新）| ☐ | 路径白名单 |
| T1.7 | `run_backtest` 工具 | `core/agent/tools/run_backtest.py`（新）| ☐ | 调 `prepare.evaluate()` |
| T1.8 | `compute_factor` 工具 | `core/agent/tools/compute_factor.py`（新）| ☐ | 调 `core/compute_factor.py` |
| T1.9 | `git_diff` 工具 | `core/agent/tools/git_diff.py`（新）| ☐ | `git diff` 包装 |
| T1.10 | `list_history` 工具 | `core/agent/tools/list_history.py`（新）| ☐ | 列 runs |
| T1.11 | ToolRegistry 装配 | `core/agent/registry.py`（新）| ☐ | 6 个工具注册 |
| T1.12 | AST guard + 路径白名单 | `core/agent/sandbox.py`（新）| ☐ | 拦截 exec/eval/open/非法路径 |

#### Week 4 — AgentLoop mini + 集成

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T1.13 | ContextBuilder（tool list + workspace + memory）| `core/agent/context.py`（新）| ☐ | 系统 prompt 含工具列表 |
| T1.14 | mini AgentLoop ReAct | `core/agent/loop.py`（新，500 行）| ☐ | 循环跑通 |
| T1.15 | microcompact 层 | `core/agent/loop.py` | ☐ | 0.5×threshold 触发 |
| T1.16 | context_collapse 层 | `core/agent/loop.py` | ☐ | 0.7×threshold 触发 |
| T1.17 | HeartbeatTimer 接入 | `core/agent/loop.py` + `progress.py` | ☐ | 长工具调用 keepalive |
| T1.18 | TraceWriter 接入 | `core/agent/loop.py` + `trace.py` | ☐ | JSONL trace 写出 |
| T1.19 | PersistentMemory.snapshot 注入 | `core/agent/context.py` | ☐ | system prompt 冻结 |
| T1.20 | auto-recall `find_relevant` | `core/agent/loop.py` | ☐ | `<recalled-memories>` 注入 user msg |
| T1.21 | git commit after run | `core/agent/loop.py` | ☐ | 每 run 自动 commit |

#### Week 5 — autoresearch + e2e

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T1.22 | Calmar 目标停止（≥ target 持续 3 轮）| `core/autoresearch.py` | ☐ | 触发时停止 |
| T1.23 | 无改善停止（连续 5 轮 sharpe 改进 < 1%）| `core/autoresearch.py` | ☐ | 触发时停止 |
| T1.24 | stuck 检测（同 hash 出现 ≥ 5 次）| `core/autoresearch.py` | ☐ | 触发时停止 |
| T1.25 | CLI `--max-iter` + `--target-calmar` | `cli.py` argparse | ☐ | flag 可用 |
| T1.26 | run 摘要输出 | `core/agent/loop.py` + `cli.py` | ☐ | `[iter=N sharpe=X calmar=Y elapsed=Zs]` |
| T1.27 | e2e smoke test | `tests/test_e2e.py`（新）| ☐ | 4 个 testcase 全过 |
| T1.28 | README + docs 更新 | `README.md` + `docs/` | ☐ | 工作流说明 |

### 3.4 P1 验收

```bash
$ export OPENAI_API_KEY=sk-xxx
$ export OPENAI_BASE_URL=https://api.deepseek.com/v1
$ export OPENAI_MODEL=deepseek-chat

$ quantnodes-research init /tmp/ws
$ quantnodes-research run --workspace /tmp/ws \
    --max-iter 3 --target-calmar 0.5 \
    --prompt "试试 momentum_20_60，看 sharpe 能不能到 0.8"

[iter=1] sharpe=0.42 calmar=0.38 tools=4 elapsed=12s
[iter=2] sharpe=0.55 calmar=0.49 tools=3 elapsed=15s
[iter=3] sharpe=0.81 calmar=0.62 tools=2 elapsed=14s
✓ Calmar ≥ 0.5 持续 3 轮，停止

$ git -C /tmp/ws log --oneline
abc1234 agent: iter=1 momentum_20_60 sharpe=0.42
def5678 agent: iter=2 momentum_20_60 sharpe=0.55
789abcd agent: iter=3 momentum_20_60 sharpe=0.81

$ cat ~/.quantnodes-research/memory/MEMORY.md
# Memory Index (1 entries)
- [feedback] momentum_20_60 在中小盘失效

$ pytest tests/test_e2e.py -v
test_init_and_baseline PASSED
test_agent_loop_3_iter PASSED
test_git_commits PASSED
test_persistent_memory PASSED
```

### 3.5 P1 PR 内容

```diff
src/strategy_research/
├── cli.py                              M  +10 -55
├── core/
│   ├── agent/                          +  ~1 700
│   │   ├── tools.py                    +   94 (复制)
│   │   ├── frontmatter.py              +   50 (复制)
│   │   ├── progress.py                 +  150 (复制)
│   │   ├── trace.py                    +  300 (复制)
│   │   ├── redaction.py                +   80 (复制)
│   │   ├── loop.py                     +  500 (自写)
│   │   ├── context.py                  +  150 (自写)
│   │   ├── registry.py                 +   30 (自写)
│   │   ├── sandbox.py                  +  100 (自写)
│   │   └── tools/                      +  440 (自写 6 文件)
│   ├── llm/                            +  250
│   │   ├── openai_client.py            +  200
│   │   └── parser.py                   +   50
│   ├── memory/                         +  265 (复制)
│   │   └── persistent.py
│   └── autoresearch.py                 M  +60
└── tests/
    └── test_e2e.py                     +  100

docs/
├── vibe-trading-survey.md              ✓ 已存在
└── vibe-trading-credits.md             +   30

README.md                               M  +30
```

---

## 4. P2/P3 Roadmap 占位（本次不执行）

### P2 — Skills + Swarm + Memory（Week 6-8，**待 P0+P1 完成后讨论**）

**预计复制**：
- `src/agent/skills.py`（SkillsLoader，182 行）
- `src/swarm/{models,runtime,worker,grounding,store,task_store,presets}.py`
- 12 个 SKILL.md（精选）
- 11 个 swarm YAML preset（基于现有 `.prompts/` 角色）

**待讨论**：
- Skills 复制范围（12 / 30+ / 87）
- Swarm preset 范围（11 / 30）
- 是否替换 `_spawn_agent` 为 Swarm 主循环

### P3 — Goal + Hypothesis + Validation（Week 9-10，**待 P2 完成后讨论**）

**预计复制**：
- `src/goal/{models,store,policy,context}.py`
- `src/hypotheses/registry.py`
- `backtest/validation.py`
- `src/factors/bench_runner_strict.py`

---

## 5. 验收矩阵

| 指标 | 当前 | P0 后 | P1 后 |
|---|---|---|---|
| `cmd_init` 成功率 | 0% | 100% | 100% |
| baseline 指标 | 空 | 8 项真实 | 8 项真实 |
| 数据源可达 loader | 1（占位）| ≥ 3 | ≥ 3 |
| 启动健康检查 | 无 | 4 项 | 4 项 |
| agent LLM 真跑 | 否（stub）| 否 | **是** |
| 工具数 | 0 | 0 | 6 |
| persistent memory | 无 | 无 | MEMORY.md + auto-recall |
| git commit 自动化 | 无 | 无 | 每 run 自动 |
| autoresearch 停止 | 无 | 无 | 3 闸 |
| AST sandbox | 无 | 无 | guard + 路径白名单 |
| 仓库总行数 | ~4 000 | ~4 880 | ~6 655 |
| e2e 测试 | 无 | 无 | 1 套 4 case |

---

## 6. 风险与回退

| 风险 | 概率 | 对策 | 回退 |
|---|---|---|---|
| `.format()` bug 影响多个文件 | 高 | T0.1 全局搜改 | 手动逐文件修 |
| DuckDB schema 不一致 | 高 | T0.2 抽常量共用 | 暂保留两套 |
| LLM 写非法 strategy.py | 高 | T1.12 sandbox | 截断 + 提示 |
| httpx 流式响应出错 | 中 | 参考 OpenAI Python SDK | 降级非流式 |
| 复制代码 import 路径冲突 | 中 | 复制时统一改 `core.X` | 全部重写 |
| 5 周时间不够 | 中 | P0 必修；P1 部分可接受 | 砍 T1.22-24 |
| P0-P3 总计划超 16 周 | 中 | 按 PR 阶段交付 | P3 留待后续 |

---

## 7. 执行日志

> 执行时填充，记录每个 PR 的实际进度。

### PR #1（P0 - bug fixes）

- 启动日期：2026-07-22
- 提交人：ll
- commits：`c468e24` fix(cli): 修 .format() bug + 统一 DuckDB schema + 补 critic.md
- 测试结果：3144 → 3144 passed（无回归）
- 备注：3 个 bug 修复，net -72 行（删 99 加 27）

### PR #2（P0 - baseline + CLI + preflight + eastmoney）

- 启动日期：2026-07-22
- 提交人：ll
- commits：`f47d548` feat(cli): 修 OHLCV 丢失 + 加 cmd_evaluate/preflight + eastmoney loader + 默认因子
- 测试结果：3144 → 3144 passed（无回归）
- 备注：5 个 task + 1 extra，net +629 行

### PR #2.5（P0 - 77 个单元测试）

- 启动日期：2026-07-22
- 提交人：ll
- commits：`e1f05e6` test: 为 P0 新增功能补 77 个单元测试 + 修 OHLCV 生成器 invariant
- 测试结果：3144 → 3221 passed (+77)
- 备注：4 个新测试文件 + 修复 generate_sample_ohlcv_data invariant

### PR #3（P0 补完 + 文档/示例）

- 启动日期：2026-07-22
- 提交人：ll
- commits：`e5ab5ea` feat: PR3 补 P0 范围 + 文档示例 (与并行 agent 协作后状态)
- 内容：
  - **G11** 修 run_card 白名单：SCHEMA_VERSION 0.1 → 0.2，BACKTEST_SUMMARY_KEYS 增加 run/strategy/action
  - **G8** FactorStrategy 支持 alpha_id + alpha_ids（Alpha Zoo 单/组合因子）
  - **G10** cmd_init 加 `--no-baseline` 选项
  - **G12** cmd_init 写入完整 config.yaml（data/rebalance/cost/risk 节）
  - **G13** FactorStrategy 修 nlargest 在 wide factor_values 时的 crash
- 测试：新增 12 个 + 修复 2 个回归 → 3233 passed（无回归）
- 备注：更新 README.md + 新增 examples/demo_workflow.py

### PR #4（P1 - 复制 6 个 agent 基础模块）

- 启动日期：2026-07-22
- 提交人：ll
- commits：
  - `0defd47` feat(agent): PR4 commit 1 — 复制 6 个 agent 基础模块
- 内容：从 vibe-trading-ai 0.1.11 (HKUDS, MIT) 复制 6 个模块到 `core/agent/` 和 `core/memory/`
  - **tools.py** (94 行): BaseTool + ToolRegistry
  - **frontmatter.py** (40 行): YAML frontmatter 解析
  - **progress.py** (184 行): ProgressEvent + HeartbeatTimer
  - **trace.py** (284 行): TraceWriter（env var 重命名 VIBE_TRADING_ → STRATEGY_RESEARCH_）
  - **redaction.py** (209 行): redact_payload + is_sensitive_arg
  - **persistent.py** (368 行): PersistentMemory（路径改 ~/.quantnodes-research/memory/）
- 适配原则：**不修改文件内容（除上述必要适配），不加任何 header 注释**
- 归属文档：`docs/vibe-trading-credits.md`（新增，~80 行）
- 测试：3233 passed（无回归）
- 备注：PR4 拆为 commit 1（复制）+ commit 2（credits + verification）

---

## 附录：借鉴模块来源对照表

| 来源（vibe-trading-ai 0.1.11）| 行数 | License | 借鉴方式 |
|---|---|---|---|
| `src/agent/tools.py` | 94 | MIT | 整包复制 |
| `src/agent/frontmatter.py` | ~50 | MIT | 整包复制 |
| `src/agent/progress.py` | ~150 | MIT | 整包复制 |
| `src/agent/trace.py` | ~300 | MIT | 整包复制 |
| `src/tools/redaction.py` | ~80 | MIT | 整包复制 |
| `src/memory/persistent.py` | 265 | MIT | 整包复制 |

> 完整功能清单与设计模式见 `docs/vibe-trading-survey.md`（1805 行）。
> 所有借鉴代码均在文件头标注 `# Adapted from vibe-trading-ai 0.1.11 (MIT License, HKUDS)`。