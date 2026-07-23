# quantnodes-strategy-research

**QuantNodes 策略研究子项目** — AI 驱动的量化策略自动研究框架

> ⚠️ **项目状态**：本项目原为 [QuantNodes](https://github.com/sn0wfree/quantnodes) 整体大项目中的一个模块（`research/strategy-research/`），现**剥离独立开发**，作为 QuantNodes 生态的子项目进行快速迭代。后续会在适当时机合回主仓库。

---

## 📑 目录

- [项目背景](#项目背景)
- [核心特性](#核心特性)
- [安装](#安装)
- [快速开始](#快速开始-30-秒)
- [CLI 命令](#cli-命令13-个)
- [工作区结构](#工作区结构)
- [数据源](#数据源)
- [因子体系](#因子体系)
- [系统架构](#系统架构)
- [更新状况](#更新状况)
- [开发](#开发)
- [许可证](#许可证)

---

## 项目背景

### 起源

本项目源自 QuantNodes 整体大项目中的 **strategy-research** 模块（路径：`research/strategy-research/`）。QuantNodes 是一个综合性的量化投资平台，包含多个相互依赖的子模块。

### 为什么剥离？

为了**快速迭代和独立发布**，本模块现以独立项目形式开发：

| 优势 | 说明 |
|---|---|
| **独立版本控制** | 不受主仓库其他模块影响 |
| **更快发布周期** | 单独打 tag、单独发 PyPI |
| **清晰依赖关系** | 仅依赖核心库（httpx、duckdb、pandas 等）|
| **便于引用** | 其他项目可单独引用 |
| **独立测试** | 独立 CI/CD，不被主仓库拖累 |

### 与主项目关系

- **上游**：主仓库 `sn0wfree/quantnodes`
- **本仓库**：`sn0wfree/quantnodes-strategy-research`
- **依赖方向**：本项目 → 仅依赖通用 Python 库（无 QuantNodes 内部依赖）
- **未来**：核心功能稳定后，会以 PR 形式合回主仓库

---

## 核心特性

### 🎯 核心能力

- ✅ **完整工作区管理**：`init` / `evaluate` / `reproduce` / `run` 全流程
- ✅ **多数据源**：Tencent / Eastmoney / Akshare / Tushare / YFinance / Local / FRED / iFinD
- ✅ **460+ 因子库**：Alpha101 / GTJA191 / Qlib158 / Academic / Fundamental
- ✅ **AI Agent 真跑**：6 个工具 + 沙箱 + 3 层上下文压缩
- ✅ **Workflow 层**：DAG 调度 + Controller + 4 种 Executor + Grounding
- ✅ **Hook 系统**：借鉴 llmwikify 13 事件点
- ✅ **Memory 系统**：FTS5 + Recency boost + Write dedup
- ✅ **Session 管理**：SQLite + FTS5 + 触发器同步 + 限流 + 监控
- ✅ **PyPI 发布**：已发布 v0.2.0（自动发布 workflow）

### 🏗️ 技术栈

- **Python 3.10+**
- **数据**：DuckDB / SQLite / Pandas
- **LLM**：OpenAI 兼容（httpx 零依赖）
- **测试**：pytest（3,770+ 测试）
- **借鉴**：vibe-trading-ai 0.1.11（HKUDS，MIT）/ llmwikify（Hook 系统）

---

## 安装

### 方式 1：独立安装（推荐）

```bash
# 克隆仓库
git clone https://github.com/sn0wfree/quantnodes-strategy-research.git
cd quantnodes-strategy-research

# 开发模式安装
pip install -e .

# 验证安装
quantnodes-research --help
```

### 方式 2：从 PyPI 安装

```bash
pip install quantnodes-strategy-research
```

### 方式 3：作为 QuantNodes 子模块

```bash
# 在 QuantNodes 主仓库中
git clone https://github.com/sn0wfree/quantnodes-strategy-research.git research/strategy-research
pip install -e research/strategy-research
```

### 环境变量

设置至少一个 LLM API Key：

```bash
# 任选其一
export OPENAI_API_KEY=sk-xxx        # OpenAI
export DEEPSEEK_API_KEY=sk-xxx      # DeepSeek
export KIMI_API_KEY=sk-xxx          # Moonshot Kimi
export QWEN_API_KEY=sk-xxx          # Alibaba Qwen
export ANTHROPIC_API_KEY=sk-xxx     # Anthropic Claude

# 可选：自定义 base URL 和模型
export OPENAI_BASE_URL=https://api.deepseek.com/v1
export OPENAI_MODEL=deepseek-chat
```

---

## 快速开始（30 秒）

```bash
# 1. 启动前检查
quantnodes-research preflight /path/to/ws

# 2. 初始化工作区（自动跑 baseline 回测）
quantnodes-research init /tmp/demo_ws

# 3. 查看状态
quantnodes-research status /tmp/demo_ws

# 4. 手动复跑（修改 strategy.py 后）
quantnodes-research evaluate /tmp/demo_ws

# 5. 复现某个历史 run
quantnodes-research reproduce /tmp/demo_ws run_0001
```

**首次运行输出：**

```
✓ 创建 README.md
✓ 创建 config.yaml
✓ 创建 .prompts/ (11 个提示词)
✓ 创建 .skills/ (10 份方法论)
✓ 创建 strategies/test_strat/
✓ 初始化 DuckDB: /tmp/demo_ws/data.duckdb
✓ 初始化 Git 仓库
✓ 导入 DataFrame: 10 个资产, 504 个日期
  baseline: Calmar=0.599 Sharpe=0.927 MaxDD=-0.155 AnnRet=0.093
✓ 运行 baseline 回测 (buy and hold HS300)
```

---

## CLI 命令（32 个 = 13 + 19）

| 命令 | 用途 | 示例 |
|---|---|---|
| `init` | 初始化工作区（含 baseline 回测）| `init /tmp/ws` |
| `init --force` | 非空目录强制初始化 | `init /tmp/ws --force` |
| `init --no-baseline` | 跳过 baseline 回测（更快）| `init /tmp/ws --no-baseline` |
| `preflight` | 启动前环境检查（4 项）| `preflight /tmp/ws` |
| `status` | 查看工作区状态 | `status /tmp/ws` |
| `evaluate` | 复跑当前 strategy.py 并写新 run_XXXX | `evaluate /tmp/ws` |
| `run` | 通用回测（带 action/description）| `run /tmp/ws --action integrate` |
| `reproduce` | 复现历史 run | `reproduce /tmp/ws run_0001` |
| `validate` | 验证因子（IC/IR/6 维评分）| `validate /tmp/ws --factor 'ts_return(close, 20)'` |
| `list` | 列出历史实验 | `list /tmp/ws --limit 10` |
| `import` | 导入价格数据 | `import /tmp/ws --strategy x --source akshare --codes 600519.SH` |
| `autoresearch` | 自动化研究循环（10 角色串行）| `autoresearch /tmp/ws --max-rounds 5` |
| `session stats` | 查看写入统计 | `session stats` |
| `session list` | 列出会话 | `session list` |
| `goal start` | 创建研究目标（取代当前目标）| `goal start --session-id s1 --objective "..."` |
| `goal status` | 查看目标状态 | `goal status --session-id s1` |
| `goal evidence` | 追加证据 | `goal evidence --session-id s1 --text "..." --criterion-id c1` |
| `goal audit` | 写完成审计 | `goal audit --session-id s1 --criterion-id c1 --result satisfied` |
| `goal complete` | 完成目标 | `goal complete --session-id s1 --audit-file audit.json` |
| `goal list` | 列出会话的所有目标 | `goal list --session-id s1` |
| `goal cancel` | 取消当前目标 | `goal cancel --session-id s1` |
| `hypothesis create` | 创建研究假设 | `hypothesis create --title "..." --thesis "..."` |
| `hypothesis list` | 列出假设 | `hypothesis list --status testing` |
| `hypothesis show` | 显示单个假设 | `hypothesis show hyp_abc123def456` |
| `hypothesis update` | 更新假设 | `hypothesis update hyp_abc --status validated` |
| `hypothesis search` | 搜索假设 | `hypothesis search --query "momentum"` |
| `hypothesis link` | 链接回测结果 | `hypothesis link hyp_abc --run-card /path/run_card.json` |
| `validate-run` | 跑验证工具（MC/Bootstrap/WF）| `validate-run /tmp/ws/strategies/m/runs/run_0001 --monte-carlo --bootstrap --walk-forward` |
| `portfolio run` | 组合回测 | `portfolio run --config portfolio.yaml --output-dir ./runs/portfolio` |
| `portfolio list` | 列出所有策略 | `portfolio list --strategy-dir ./strategies` |
| `portfolio show` | 显示组合结果 | `portfolio show ./runs/portfolio_001` |
| `portfolio correlate` | 策略相关性矩阵 | `portfolio correlate --strategy-dir ./strategies` |
| `api serve` | 启动 HTTP API 服务器 | `api serve --host 0.0.0.0 --port 8765` |
| `webui serve` | 启动 Web UI 仪表盘 | `webui serve --host 0.0.0.0 --port 8766` |

### `preflight` 输出示例

```
======================================================================
  quantnodes-research Pre-flight Check
======================================================================
  [FAIL]   LLM Provider         [CRITICAL]
           未配置任何 LLM API key
           → Agent 无法调用 LLM...
  [OK]     DuckDB
           writable: /tmp/ws/data.duckdb
  [OK]     Data Sources
           5 个可用: tencent, akshare, yfinance, eastmoney, local
  [SKIP]   OHLCV Integrity
           无 price_data 数据
======================================================================
  ❌ 1 项 CRITICAL 检查失败，agent 无法启动
```

### `evaluate` 输出示例

```
🔄 复跑策略: test_strat

✅ 复跑成功: run_0002
   Calmar   = 0.5989
   Sharpe   = 0.9273
   MaxDD    = -0.1550
   AnnRet   = 0.0928
   AnnVol   = 0.1001
   Sortino  = 1.5064
   Turnover = 5.0400

📁 详见: /tmp/ws/strategies/test_strat/runs/run_0002
```

---

## 工作区结构

```
/path/to/workspace/
├── README.md              # Agent 入口
├── config.yaml            # 工作区配置（数据源/回测参数/成本/风控）
├── data.duckdb            # 共享数据库（9 张表）
├── .git/
├── .prompts/              # 11 个 Subagent 提示词
│   ├── orchestrator.md
│   ├── researcher.md
│   ├── factor_analyst.md
│   ├── strategist.md
│   ├── critic.md
│   ├── data_quality.md
│   ├── portfolio_construction.md
│   ├── risk_controller.md
│   ├── attribution_analyst.md
│   ├── anti_overfit_analyst.md
│   ├── backtest_diagnostics.md
│   └── critic.md
└── .skills/               # 10 份方法论
    ├── data-routing.md
    ├── factor-research.md
    ├── backtest-diagnose.md
    ├── correlation-analysis.md
    ├── ml-strategy.md
    ├── performance-attribution.md
    ├── quant-statistics.md
    ├── risk-analysis.md
    ├── sector-rotation.md
    └── research-discipline.md
└── strategies/
    └── {strategy_name}/
        ├── program.md     # 策略 playbook（必读）
        ├── prepare.py     # 目标函数（Agent 不改）
        ├── strategy.py    # Agent 唯一可改（PARAMS/FACTOR_EXPRS/FACTOR_WEIGHT_METHOD）
        └── runs/
            ├── results.tsv
            └── run_XXXX/
                ├── strategy.py    # 快照
                ├── run.log        # stdout
                ├── metrics.json   # 8 项指标
                ├── run_card.json  # Trust Layer (SHA-256)
                └── run_card.md    # 人读版
```

---

## 数据源（5+ 个 loader）

通过 `data.source` 字段配置：

| Loader | 市场 | 鉴权 | 自动 fallback |
|---|---|---|---|
| `tencent` | A 股 | ❌ | 是 |
| `eastmoney` | A 股 + 港股 | ❌ | 是 |
| `akshare` | 全市场 | ❌ | 是 |
| `tushare` | A 股 + 期货 + 基金 | ✓ | 是 |
| `yfinance` | 美股 + 港股 + 加密 | ❌ | 是 |
| `local` | 自定义 CSV/Parquet | ❌ | **否**（避免静默降级）|
| `fred` | 美国宏观 | ✓ | 否 |
| `ifind` | 宏观 + 港美股 | ✓ | 否 |

`FALLBACK_CHAINS`（自动 fallback 链）：
```python
"a_share": ["tencent", "eastmoney", "akshare", "tushare", "local"]
"hk":     ["eastmoney", "yfinance", "akshare", "ifind", "local"]
"us":     ["yfinance", "akshare", "ifind", "local"]
"macro":  ["fred", "ifind", "akshare", "tushare", "local"]
"crypto": ["yfinance", "akshare", "local"]
```

---

## 因子体系（460+ 因子）

5 个 Zoo 库（注册式 API，无需手写算子）：

| Zoo | 数量 | 来源 |
|---|---|---|
| `alpha101` | 101 | Kakushadze (2015) "101 Formulaic Alphas", arXiv:1601.00991 |
| `gtja191` | 191 | 国泰君安证券 2014 短周期 alpha 因子 |
| `qlib158` | 154 | Microsoft Qlib Alpha158 (Apache-2.0, pin commit d5379c52) |
| `academic` | 10 | Fama-French / Carhart / Jegadeesh / Amihud 等 |
| `fundamental` | 4 | ROE / earnings yield / gross profitability / asset growth |

```python
from strategy_research.core.alpha_zoo_adapter import AlphaZooAdapter

adapter = AlphaZooAdapter()
alphas = adapter.list_alphas(zoo="gtja191", theme="momentum")
df = adapter.compute_as_wide("gtja191_001", prices_panel)
```

**YAML 配置示例：**

```yaml
factors:
  # 表达式因子
  - name: momentum_20d
    code: ts_return(close, 20)
    weight: 0.5
  # Alpha Zoo 因子（需 yaml-driven 回测）
  - name: gtja_mom
    alpha_id: gtja191_005
    weight: 0.3
  # Alpha Zoo 因子组合
  - name: composite
    alpha_ids: [alpha101_001, gtja191_010]
    combination: equal
```

---

## 系统架构

### 三层架构

```
┌─────────────────────────────────────────────────────────────┐
│  Workflow 层（P1.5）                                          │
│  - WorkflowController（DAG 调度）                            │
│  - AgentExecutor Protocol（接口）                           │
│  - Agent Validators（验证）                                  │
│  - Grounding Provider（市场数据预取）                       │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│  Agent 层（P1）                                              │
│  - AgentLoop（ReAct 循环 + 3 层压缩）                       │
│  - BaseTool + ToolRegistry（6 个工具）                      │
│  - Sandbox（AST guard + 路径白名单）                        │
│  - ContextBuilder（system + user prompt）                   │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│  Hook + Memory + Session 层（P2）                            │
│  - Hook 系统（llmwikify 模式，13 事件点）                    │
│  - Memory（FTS5 + Recency boost + Dedup）                    │
│  - Session（SQLite + FTS5 + 触发器同步 + 限流）             │
└─────────────────────────────────────────────────────────────┘
```

### Hook 系统（P2）

借鉴 llmwikify 的 13 事件点 Hook 系统：

```python
from strategy_research.core.hooks import AgentHook, CompositeHook, AgentHookContext

class MyHook(AgentHook):
    name = "my_hook"
    
    def after_iteration(self, ctx: AgentHookContext):
        print(f"Iteration {ctx.iteration} done")

composite = CompositeHook([MyHook()])
ctx = AgentHookContext(iteration=1)
asyncio.run(composite.after_iteration(ctx))
```

**13 个事件点**：
`wants_streaming / before_iteration / after_iteration / on_stream / on_stream_end / emit_reasoning / emit_reasoning_end / before_execute_tools / after_tool_executed / on_tool_error / on_confirmation / finalize_content / on_error`

### Memory 系统（P2）

- **FTS5 全文搜索**（全局索引）
- **Recency boost**（时间衰减）
- **Write dedup**（SHA-256）
- **Context injection**（`<recalled-memories>` 块）

```python
from strategy_research.core.memory import PersistentMemory

memory = PersistentMemory()
memory.add("factor", "Momentum works in large caps", "feedback", "Momentum")
results = memory.find_relevant("momentum")
context = memory.format_context_for_prompt("momentum")
```

### Session 管理（P2）

- **SQLite + FTS5**（跨 workspace 搜索）
- **触发器自动同步**（INSERT/UPDATE/DELETE）
- **限流器**（可配置，默认 80,000 条/秒）
- **JSONL 监控**（写入指标）

```bash
# 查看写入统计
$ quantnodes-research session stats

# 列出会话
$ quantnodes-research session list
```

**性能基准：**
- 1,000 条插入：1.15s
- 100,000 条插入：4.42s（22,625 条/秒）
- 搜索：35,000~55,000 次/秒
- 触发器自动同步：✅ 无应用层代码

---

## 更新状况

### 路线图进度

| 阶段 | 范围 | 状态 | 详细说明 |
|---|---|---|---|
| **P0** | 修通 init（`.format()`/DuckDB/OHLCV/默认因子/CLI/preflight/eastmoney）| ✅ 完成 | 详见 [enhancement.md §2](docs/enhancement.md) |
| **P1** | Agent 真跑（替换 stub 接通 LLM，6 个工具，沙箱，3 层压缩）| ✅ 完成 | PR #4-#6 + AgentLoop 改造 |
| **P1.5** | Workflow 层（DAG 调度 + Controller + 4 种 Executor + Grounding）| ✅ 完成 | 88 测试 |
| **P2** | Hook + Memory + Session（llmwikify 模式 + FTS5 + 触发器同步）| ✅ 完成 | 116 测试 |
| **P3** | Goal + Hypothesis + Validation（MC + Bootstrap + WF）| ✅ 完成 | 283 新测试（162 Goal + 58 Hypothesis + 40 Validation + 23 Integration） |
| **backtest-overhaul** | Phase 1+2+3（dataclass + 17-key metrics + market_detection + run_card + AST guard）| ✅ 完成 | 233 测试 |
| **P4-b** | Portfolio 多策略组合回测（combiner + correlation + VaR/CVaR）| ✅ 完成 | 27 测试 |
| **P4-d** | HTTP API server mode（FastAPI + 6 routers + Swagger）| ✅ 完成 | 15 测试 |
| **P4-c** | Web UI dashboard（FastAPI + Jinja + HTMX, 7 页面）| ✅ 完成 | 9 测试 |
| **Backtest Engine** | bar-by-bar 执行引擎 + 9 市场引擎 + AST guard + artifacts | ✅ 完成 | 104 测试 |

### 测试统计

- **4,311+ 测试通过**
- **0 回归**
- 测试覆盖：P0 + P1 + P1.5 + P2 + P3 + backtest-overhaul + P4 + Backtest Engine 全覆盖
- CLI 子命令：13 → **32**（+7 goal + 6 hypothesis + 1 validate-run + 4 portfolio + 1 api + 1 webui）

### 版本发布

| 版本 | 日期 | 说明 |
|---|---|---|
| **v0.2.0** | 2026-07-22 | 已发布到 PyPI（自动发布 workflow） |
| **v0.3.0** | 2026-07-22 | 本地 marker，未推送 PyPI；包含 P3 + backtest-overhaul + P4（Portfolio / API / WebUI）|

### 下一步计划

1. **合回主仓库** sn0wfree/quantnodes：v0.4.0 稳定后启动
2. **持续优化**：性能 + 用户体验 + 更多 validation 市场（CRYPTO/FUTURES/FOREX）

---

## 开发

### 安装开发依赖

```bash
pip install -e ".[dev]"
```

### 运行测试

```bash
# 全部测试
pytest                                    # 3,770 passed

# 单跑特定模块
pytest tests/test_preflight.py -v         # preflight 测试
pytest tests/test_cli_init.py -v           # init 测试
pytest tests/test_workflow_e2e.py -v       # Workflow e2e 测试
pytest tests/test_session.py -v            # Session 测试

# 性能测试
pytest tests/test_session_triggers.py -v   # 触发器同步性能
```

### 代码检查

```bash
# Lint
ruff check .

# 类型检查（可选）
mypy src/strategy_research/
```

### 测试覆盖（3,770+ 个测试）

| 模块 | 测试数 | 状态 |
|---|---|---|
| `test_workflow_*.py` | 88 | ✅ |
| `test_hooks.py` | 23 | ✅ |
| `test_memory_fts5.py` | 12 | ✅ |
| `test_memory_enhance.py` | 11 | ✅ |
| `test_session.py` | 34 | ✅ |
| `test_session_triggers.py` | 6 | ✅ |
| `test_session_rate_limiter.py` | 12 | ✅ |
| `test_session_metrics.py` | 9 | ✅ |
| `test_integration.py` | 9 | ✅ |
| P0 + P1 测试 | 3,608 | ✅ |

### 文档结构

```
docs/
├── enhancement.md                # 借鉴路线图（P0-P3）
├── workflow-design.md            # P1.5 Workflow 设计
├── vibe-trading-survey.md        # vibe-trading 调研（1805 行）
├── vibe-trading-credits.md       # 借鉴致谢
├── autoresearch-design.md        # autoresearch 设计
├── llm-config-template.yaml      # LLM 配置模板
└── backtest-overhaul/            # 回测重构

examples/
├── demo_workflow.py              # 工作流演示
└── session_example.py            # Session 使用示例
```

---

## 借鉴致谢

本项目借鉴了以下开源项目：

- **vibe-trading-ai 0.1.11**（HKUDS，MIT License）
  - Agent 工具、Trace、Progress、Memory 等
  - 详见 `docs/vibe-trading-credits.md`
- **llmwikify**（MIT License）
  - Hook 系统（13 事件点）
  - AgentLoop 设计模式

---

## 设计理念

- **Karpathy 极简**: 框架提供工具和循环指引，Agent 自主决策
- **Skill/Harness 模式**: 外部 Agent 读 prompt 后自主决策
- **通用性**: 通过 `prepare.evaluate()` 目标函数接口适配不同策略
- **实验可复现**: 每次实验保存 SHA-256 快照到 `run_card.json`，可随时复现
- **磁盘优先**: 所有指令写在文件里 — Agent 中途崩溃可从同套文件恢复 context
- **Hook 解耦**: 通过 Hook 系统实现横切关注点（日志、监控、归档）的解耦

---

## 许可证

MIT

---

## 相关链接

- **主仓库**：[sn0wfree/quantnodes](https://github.com/sn0wfree/quantnodes)
- **本仓库**：[sn0wfree/quantnodes-strategy-research](https://github.com/sn0wfree/quantnodes-strategy-research)
- **PyPI**：[quantnodes-strategy-research](https://pypi.org/project/quantnodes-strategy-research/)
- **问题反馈**：[GitHub Issues](https://github.com/sn0wfree/quantnodes-strategy-research/issues)