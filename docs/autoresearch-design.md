# quantnodes-research 自动化策略研发框架设计文档

> 本文档记录 autoresearch 框架的完整架构设计。
> Agent 启动时应先读 `README.md`(操作指南),本文档作为完整参考。

## 1. 项目概述

**quantnodes-research** 是一个面向 AI Agent 的自动化策略研发框架。

- **目标**: Agent 自主迭代策略,直至 Calmar ≥ 0.5
- **设计哲学**: 所有指令写在磁盘文件里 — Agent 中途崩溃可从同套文件恢复 context
- **运行模式**: `LOOP FOREVER` — 持续迭代不主动停 (用户可能在睡觉)
- **分工**: 框架 = 工程脚手架 (CLI / DuckDB / git / 算子库), Agent = 决策 + 因子 + 评估
- **边界**: Agent 不动 `prepare.py` / `data.duckdb` / `.git/` — 那是框架域

## 2. 架构总览

### 2.1 角色拓扑

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Main Agent (看门狗)                         │
│  启动 → 监控卡住/重复 → 最终停止确认 (非常苛刻)                      │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         Orchestrator                                │
│  Step 1: 读状态                                                    │
│  Step 2: spawn Researcher                                          │
│  Step 3: spawn Data Quality → Factor Analyst → Strategist → P.C.   │
│  Step 4: 保存 (框架自动)                                           │
│  Step 5: spawn Risk Controller → Attribution Analyst → Anti-overfit │
│  Step 6: 提交                                                      │
└─────────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ Data Quality │  │    Researcher    │  │  Factor Analyst  │
│              │  │                  │  │                  │
│ - NaN 比例   │  │ - 偏见自检       │  │ - 路径 A: 本地   │
│ - 交易日缺失 │  │ - 因子池评估     │  │   MCTS           │
│ - 价格异常   │  │ - 行动决策       │  │ - 路径 D: Alpha  │
│ - 除权因子   │  │ - Research       │  │   Zoo            │
│              │  │   Momentum       │  │ - IC/IR          │
│              │  │ - 假设           │  │ - 6 维评分       │
│              │  │                  │  │ - Mutual IC      │
│              │  │                  │  │ - IC 衰减        │
└──────────────┘  └──────────────────┘  └──────────────────┘
        │
        ▼
┌──────────────────┐                  ┌──────────────────┐
│    Strategist    │                  │ Portfolio        │
│                  │                  │ Construction     │
│ - 因子集成       │                  │                  │
│ - 参数优化       │                  │ - 风险平价       │
│ - 因子移除       │                  │ - 协方差估计     │
│                  │                  │ - 风险预算       │
└──────────────────┘                  └──────────────────┘
                                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ Risk Controller  │  │  Attribution     │  │ Anti-overfit     │
│                  │  │  Analyst         │  │ Analyst          │
│ - 风控阈值       │  │                  │  │                  │
│ - VaR/CVaR      │  │ - Brinson 归因   │  │ - 起点依赖       │
│ - Monte Carlo    │  │ - Fama-French    │  │ - 调仓日偏移     │
│ - 压力测试       │  │ - 牛熊捕获率     │  │ - 参数扰动       │
│ - 尾部风险       │  │                  │  │ - 消融实验       │
│                  │  │                  │  │ - Bootstrap      │
│                  │  │                  │  │ - MC 排列检验    │
└──────────────────┘  └──────────────────┘  └──────────────────┘

┌──────────────────┐
│ Backtest         │
│ Diagnostics      │
│                  │
│ - 错误分类       │
│ - 修复建议       │
└──────────────────┘
```

### 2.2 Agent 清单

| # | 角色 | 文件 | 核心问题 |
|---|------|------|---------|
| 0 | **Main Agent** (看门狗) | — (主进程) | 启动 / 监控 / 停止 |
| 1 | **Orchestrator** (调度) | `.prompts/orchestrator.md` | 谁在什么时候跑? |
| 2 | **Data Quality** (数据质量) | `.prompts/data_quality.md` | 数据干净吗? |
| 3 | **Researcher** (研究员) | `.prompts/researcher.md` | 下一步做什么? |
| 4 | **Factor Analyst** (因子分析师) | `.prompts/factor_analyst.md` | 哪些因子有效? |
| 5 | **Strategist** (策略师) | `.prompts/strategist.md` | 怎么集成到策略? |
| 6 | **Portfolio Construction** (组合构建) | `.prompts/portfolio_construction.md` | 权重怎么分配? |
| 7 | **Risk Controller** (风控官) | `.prompts/risk_controller.md` | 风险超标了吗? |
| 8 | **Attribution Analyst** (归因分析师) | `.prompts/attribution_analyst.md` | 收益来源是什么? |
| 9 | **Anti-overfit Analyst** (抗过拟合分析师) | `.prompts/anti_overfit_analyst.md` | 结果可信吗? |
| 10 | **Backtest Diagnostics** (回测诊断) | `.prompts/backtest_diagnostics.md` | 回测哪里出错了? |

## 3. Agent 角色定义

### 3.1 Main Agent (看门狗)

**职责**:启动 autoresearch loop,监控所有 Agent 是否卡住/重复,最终停止确认。

**不做的事**:
- 不跑 6 步循环 (Orchestrator 负责)
- 不做因子研究 / 策略评估 / 风控检查 (各角色负责)
- 不改 strategy.py (Strategist 负责)

**启动流程**:
```
读 README.md → 读 program.md → 读 strategy.py → 读 results.tsv
→ 确认工作区就绪
→ launch Orchestrator (传入 workspace path + strategy name)
```

**卡住检测** (任一命中 → interrupt 该 Agent):

| 检测项 | 阈值 | 动作 |
|--------|------|------|
| 同一个 action 连续出现 | ≥ 3 次 | interrupt Researcher, 重启时注入 "avoid_actions" |
| 同一个 hypothesis 连续出现 | ≥ 3 次 | interrupt Researcher |
| 同一个 factor candidate 被反复推荐 | ≥ 3 次 | interrupt Factor Analyst |
| Agent 输出 malformed JSON | 1 次 | interrupt 该 Agent, 重启 |
| Agent 输出包含回避语 | 1 次 | interrupt 该 Agent, 重启 |
| Agent 执行超时 | > 60s | interrupt 该 Agent, 重启 |
| Agent 输出与上轮完全相同 | 1 次 | interrupt 该 Agent, 重启 |

**停止条件** (必须 ALL 同时满足):

| 条件 | 阈值 |
|------|------|
| Calmar | ≥ 0.5 |
| Sharpe | ≥ 0.3 |
| MaxDD | ≤ -15% |
| 抗过拟合 | 6/6 全 pass |
| 因子池覆盖 | ≥ 80% (5/6 维) |
| 连续无改善轮数 | ≥ 10 轮 |
| 总轮数 | ≤ 50 轮 |

**紧急停止条件** (任一命中 → 立即停):

| 条件 | 说明 |
|------|------|
| 总轮数 = 50 | 硬上限 |
| 用户 Ctrl+C | 手动中断 |
| 所有 Agent 都被 interrupt 过且无改善 | 全部卡死 |
| DuckDB 写满 / 磁盘满 | 基础设施故障 |

### 3.2 Orchestrator (调度)

**职责**:跑 6 步循环,管理 Agent 间通信。

**每步 spawn 逻辑**:

| Step | 调度方 | 输入 | 输出 |
|------|--------|------|------|
| 1 读状态 | 自己 | `strategy.py`, `results.tsv` | 当前因子池 + 最近 run 摘要 |
| 2 决策 | spawn Researcher | 当前状态 + 上轮 Critic 反馈 | JSON `{action, hypothesis, avoid_actions}` |
| 3 执行 | spawn Data Quality → Factor Analyst → Strategist → Portfolio Construction | Step 2 action | 新 `strategy.py` + 回测 stdout |
| 4 保存 | 框架自动 | 回测 stdout | `runs/run_XXXX/...` + DuckDB |
| 5 评估 | spawn Risk Controller → Attribution Analyst → Anti-overfit Analyst | metrics + 当前策略 | JSON `{verdict, risk_rating, suggestions}` |
| 6 提交 | 自己 | verdict | `evaluate ... --status keep\|discard` |

**通信协议**:
- 共享文件: `strategy.py` / `results.tsv` / `runs/run_XXXX/` 是 Agent 间通信的唯一通道
- 同步: Subagent 阻塞完成, Orchestrator 拿到 JSON 再继续
- 不必 spawn: 读文件 + 推理类简单决策, Orchestrator 自己来

### 3.3 Data Quality (数据质量)

**职责**:在因子计算前验证数据质量。

**检查项**:
- NaN 比例 (< 5% pass)
- 交易日缺失 (连续 3 天以上 warning)
- 价格异常 (单日涨跌 > 20%)
- 除权因子 (需要检测)
- 数据指纹 (SHA-256)

**输入**: prices (from DuckDB)
**输出**: `{passed, warnings, data_fingerprint}`

**如果 data_quality 不通过**: Orchestrator 跳到 Step 4,记录 "data_quality_failed" 状态。

### 3.4 Researcher (研究员)

**职责**:偏见自检 + 因子池评估 + 行动决策 + Research Momentum。

**步骤**:
- Step 0: 偏见自检 (5 种偏见: 龙头/英文/叙事/确认/近因)
- Step 1: 因子池评估 (当前因子数,覆盖维度,缺少维度)
- Step 2: 行动决策 + Research Momentum (读 results.tsv 最近 10 轮,统计哪些 action 失败过,输出 "avoid_actions")
- Step 3: 假设 (hypothesis)

**输入**: 当前状态 + results.tsv 最近 10 轮
**输出**: `{action, hypothesis, avoid_actions, factor_direction}`

**action 类型**:
- `search_external`: 外部搜索因子
- `discover_local`: 本地算子挖掘
- `optimize_param`: 参数优化
- `remove_factor`: 因子移除

### 3.5 Factor Analyst (因子分析师)

**职责**:因子发现 (2 条路径) + 验证 (IC/IR/6D/Mutual IC/IC 衰减)。

**路径**:
- 路径 A: 本地算子 MCTS (含 LLM reasoning)
- 路径 D: Alpha Zoo (450+ 预置因子)

**验证流程**:
1. IC/IR 验证 (IC > 0.03, IR > 0.5)
2. 6 维评分 (Stability/Diversification/Turnover/Monotonicity/Coverage/Rank IC)
3. Mutual IC 去重 (|corr| < 0.7)
4. IC 衰减检查 (IC_5d >= 30% * IC_1d)

**输入**: prices + action + factor candidates
**输出**: `{path_used, candidates, rejected}`

### 3.6 Strategist (策略师)

**职责**:因子集成 + 参数优化 + 因子移除。

**操作**:
- 操作 1: 因子集成 (search_external 或 discover_local 后)
- 操作 2: 参数优化 (optimize_param)
- 操作 3: 因子移除 (remove_factor)

**输入**: 当前 strategy.py + candidates
**输出**: 修改 strategy.py (PARAMS / FACTOR_EXPRS / FACTOR_WEIGHT_METHOD)

### 3.7 Portfolio Construction (组合构建)

**职责**:风险平价 + 协方差估计 + 风险预算。

**方法**:
- 等权 (`equal`)
- 逆波动率 (`inv_vol`)
- 风险平价 (`risk_parity`)
- 最大分散化 (`max_diversification`)

**输入**: prices + strategy.py + Σ (协方差矩阵)
**输出**: `{weights, risk_contributions, diversification_ratio}`

### 3.8 Risk Controller (风控官)

**职责**:风控阈值 + VaR/CVaR + Monte Carlo + 压力测试 + 尾部风险。

**风控阈值**:
| 指标 | 阈值 |
|------|------|
| MaxDD | ≤ -15% |
| Calmar | ≥ 0.5 |
| Sharpe | ≥ 0.3 |
| 单资产权重 | ≤ 25% |
| 年化换手 | ≤ 600% |
| 因子数 | ≤ 30 |

**风险度量**:
- VaR/CVaR (历史模拟法)
- Monte Carlo (10000 条 GBM 路径)
- 压力测试 (2008/2015/2020/利率冲击/流动性枯竭)
- 尾部风险 (峰度/偏度)

**输入**: metrics + 当前策略
**输出**: `{risk_passed, risk_rating, var_95, cvar_95, stress_results}`

### 3.9 Attribution Analyst (归因分析师)

**职责**:Brinson 归因 + Fama-French + 牛熊捕获率。

**方法**:
- Brinson 归因 (配置效应 / 选股效应 / 交互效应)
- Fama-French (R_p - R_f = α + β_mkt × MKT + β_smb × SMB + β_hml × HML + β_mom × MOM)
- 牛熊捕获率 (牛市 > 100% → 跑赢, 熊市 < 100% → 防御)

**输入**: metrics + 当前策略
**输出**: `{alpha, beta_mkt, sector_allocation, stock_selection, bull_capture, bear_capture}`

### 3.10 Anti-overfit Analyst (抗过拟合分析师)

**职责**:6 种抗过拟合方法 + keep/discard 判断。

**6 种方法**:
1. 起点依赖: 3 起点 CV% < 25%
2. 调仓日偏移: ±5 日 Calmar 稳定 (CV% ≤ 15%)
3. 参数扰动: ±10% 退化 < 20%
4. 消融实验: 每关一项退化 >= 5%
5. Bootstrap: Sharpe 显著性 (t = Sharpe × sqrt(n) / sqrt(1 + 0.5×Sharpe²))
6. Monte Carlo 排列检验: 打乱交易顺序 1000 次, p < 0.05

**判断逻辑**:
- 目标函数改善 + 风控通过 + 统计显著 → keep
- 目标函数不变或退化 → discard
- 风控阈值触发 → discard
- 统计不显著 → 标记 warning

**输入**: metrics + 当前策略
**输出**: `{verdict: keep|discard, overfit_passed, methods_passed, suggestions}`

### 3.11 Backtest Diagnostics (回测诊断)

**职责**:错误分类 + 修复建议。

**错误分类**:
| 类型 | 症状 | 修复 |
|------|------|------|
| 零交易 | trade_count=0 | 检查信号逻辑 |
| 交易过晚 | 首笔 >2 年后 | 缩短 lookback |
| 资金闲置 | 利用率 <50% | 检查信号频率 |
| 期末持仓 | 结束时仍持仓 | 添加强制平仓 |

**修复原则**:精确修复,不重写;每次只修一个问题;最多 3 轮修复。

**输入**: run.log + metrics
**输出**: `{error_type, fix_suggestion, severity}`

## 4. 操作循环

### 4.1 调度图

```
                      ┌─────────────────── Main Agent ────────────────────┐
                      │                                                  │
   ┌────────────┐     │  Step 1 读状态    ───── 自己读                     │
   │ 上轮       │◀────┤                                                  │
   │ Anti-overfit│     │  Step 6 提交      ───── evaluate + git commit     │
   │ 反馈       │     │                                                  │
   └────────────┘     │  Step 2 决策         ── spawn ─→  Researcher     │
                      │                        ↓ action JSON             │
                      │  Step 3 执行                                     │
                      │    ├── DQ 检查 ────── spawn ─→  Data Quality     │
                      │    ├── 因子发现 ───── spawn ─→  Factor Analyst   │
                      │    ├── 策略集成 ───── spawn ─→  Strategist       │
                      │    └── 组合构建 ───── spawn ─→  Portfolio Constr. │
                      │                        ↓ 新 strategy.py          │
                      │  Step 4 保存 ─────── [框架自动]                   │
                      │        runs/run_XXXX/{metrics.json,              │
                      │                       run_card.json,              │
                      │                       run_card.md}                │
                      │  Step 5 评估                                     │
                      │    ├── 风控检查 ───── spawn ─→  Risk Controller  │
                      │    ├── 归因分析 ───── spawn ─→  Attribution      │
                      │    └── 抗过拟合 ───── spawn ─→  Anti-overfit     │
                      │                        ↓ verdict JSON             │
                      └──────────────────────────────────────────────────┘
```

### 4.2 通信协议

- **中介文件**: `strategy.py` / `results.tsv` / `runs/run_XXXX/` 是 Agent 间通信的唯一通道
- **JSON 协议**: spawn 时把任务序列化进 .prompts/ schema, 完成后 Subagent 写盘 + stdout 返回 JSON
- **同步**: Subagent 阻塞完成, Orchestrator 拿到 JSON 再进下一步
- **不必 spawn**: 读文件 + 推理类简单决策 Orchestrator 自己来

## 5. Git-as-log 系统

### 5.1 4 层索引设计

| 层 | 数据 | 用途 | 查询方式 |
|---|------|------|---------|
| **DuckDB** | 8 张表 (因子/验证/回测/指纹) | 主索引 | SQL |
| **results.tsv** | 每行一个 run | 快速人读 | tail / grep |
| **Git** | commit 历史 | 审计 + 恢复 + diff | git log / git diff / git checkout |
| **runs/** | 每个 run 的详细产物 | 详细分析 | cat metrics.json |

### 5.2 快速索引查询

**DuckDB SQL**:
```sql
-- 最近 10 个实验
SELECT run, calmar, sharpe, max_dd, status, action_type, hypothesis
FROM backtest_results
WHERE strategy_name = 'rot_alpha'
ORDER BY created_at DESC
LIMIT 10;

-- 所有 keep 实验, 按 Calmar 排序
SELECT run, calmar, sharpe, max_dd, hypothesis
FROM backtest_results
WHERE strategy_name = 'rot_alpha' AND status = 'keep'
ORDER BY calmar DESC;

-- 平均 Calmar (keep vs discard)
SELECT status, AVG(calmar), COUNT(*)
FROM backtest_results
WHERE strategy_name = 'rot_alpha'
GROUP BY status;
```

**Git**:
```bash
git log --oneline                    # 时间线
git log --oneline --grep="^keep:"   # 只看 keep
git log --oneline --grep="^discard:" # 只看 discard
git diff run_0003 run_0005          # 比较两个实验
git checkout HEAD                    # 恢复到最后状态
```

### 5.3 Commit message 格式

```
{status}: {strategy_name}/{run_name} | Calmar={X.XX} Sharpe={X.XX} MaxDD={X.XX%} | {action}: {hypothesis}
```

示例:
```
keep: rot_alpha/run_0003 | Calmar=0.71 Sharpe=0.82 MaxDD=-11.2% | discover_local: "加入量价因子提升IC"
discard: rot_alpha/run_0004 | Calmar=0.23 Sharpe=0.15 MaxDD=-28.5% | optimize_param: "top_n=3 过于集中"
```

### 5.4 实现改动

- `backtest.evaluate_experiment()`: 每次 run 都 commit (不只是 keep)
- `backtest.evaluate_experiment()`: commit message 格式改为 rich format
- `git.py`: 新增 `git_commit_rich()` 函数
- `db.py`: 扩展 `backtest_results` 表 (新增 hypothesis/verdict/risk_rating/action_type 字段)

## 6. 默认 Baseline

### 6.1 策略

默认 baseline = **buy and hold 沪深300**。

```python
PARAMS = {
    "top_n": 1,
    "max_weight": 1.0,
    "rebalance_freq": 999999,
}

FACTOR_EXPRS = []  # 无因子 — buy and hold

FACTOR_WEIGHT_METHOD = "equal"
```

### 6.2 基线回测

`cli.cmd_init` 时自动跑一次:
1. 生成 HS300 模拟数据 (`generate_sample_data(1, 600, "2020-01-01")`)
2. 灌入 DuckDB
3. 跑 `python strategy.py` (buy and hold)
4. 保存为 `runs/run_0000/` (status="baseline")
5. 更新 `results.tsv` (第一行: baseline metrics)
6. `git commit` (第一个 commit: "seed: HS300 buy-and-hold baseline")

### 6.3 用户覆盖

如果用户指定不同 baseline:
- `cli.cmd_init --baseline=custom_strategy.py`
- `cli.cmd_init --baseline=none` (跳过 baseline)

## 7. 停止条件

详见 §3.1 Main Agent 的停止条件。

## 8. 文件结构

### 8.1 工作区结构

```
workspace/
├── README.md                    ← Agent 第一个读: 操作指南
├── config.yaml                  ← workspace 配置
├── data.duckdb                  ← 8 张表状态中枢
├── .git/                        ← 实验版本控制
├── .prompts/                    ← 10 个 Subagent 角色说明书
│   ├── orchestrator.md
│   ├── data_quality.md
│   ├── researcher.md
│   ├── factor_analyst.md
│   ├── strategist.md
│   ├── portfolio_construction.md
│   ├── risk_controller.md
│   ├── attribution_analyst.md
│   ├── anti_overfit_analyst.md
│   └── backtest_diagnostics.md
├── .skills/                     ← 9+1 份方法论文档
│   ├── data-routing.md
│   ├── factor-research.md
│   ├── backtest-diagnose.md
│   ├── correlation-analysis.md
│   ├── ml-strategy.md
│   ├── performance-attribution.md
│   ├── quant-statistics.md
│   ├── risk-analysis.md
│   ├── sector-rotation.md
│   └── research-discipline.md   ← 新增
└── strategies/
    └── <name>/
        ├── strategy.py          ← Agent 唯一可改 (PARAMS / FACTOR_EXPRS / FACTOR_WEIGHT_METHOD)
        ├── program.md           ← 策略 playbook (必读)
        ├── prepare.py           ← 框架调用, 不要改
        └── runs/
            ├── results.tsv      ← 实验记录
            ├── run_0000/        ← baseline
            ├── run_0001/        ← 实验 1
            └── ...
```

### 8.2 .prompts/ 说明

每个 .prompts/*.md 文件定义一个 Subagent 角色:
- 角色名称
- 职责描述
- 输入/输出 schema (JSON)
- 步骤说明
- 规则/约束

### 8.3 .skills/ 说明

每个 .skills/*.md 文件定义一份方法论文档:
- 算子语法 (factor-research.md)
- 数据源路由 (data-routing.md)
- 风控阈值 (risk-analysis.md)
- 抗过拟合方法 (quant-statistics.md)
- 归因方法 (performance-attribution.md)
- 偏见自检 (research-discipline.md)

## 9. 实现计划

### 9.1 优先级

| 优先级 | 工作 | 文件 |
|--------|------|------|
| P0 | 写设计文档 | `docs/autoresearch-design.md` |
| P0 | 改 README | `templates/README.md` |
| P0 | 改 program.md | `templates/program.md` |
| P0 | 改 strategy.py | `templates/strategy.py` |
| P0 | 创建 research-discipline | `templates/.skills/research-discipline.md` |
| P1 | 创建/修改 10 个 .prompts | `templates/.prompts/` |
| P1 | 改 cli.py (baseline) | `src/strategy_research/cli.py` |
| P2 | 改 backtest.py (git-as-log) | `src/strategy_research/core/backtest.py` |
| P2 | 改 git.py (rich message) | `src/strategy_research/core/git.py` |
| P2 | 改 db.py (扩展表) | `src/strategy_research/core/db.py` |
