# quantnodes-strategy-research

通用策略自动研究框架 — Karpathy autoresearch 极简 + 多 Agent 增强 + 因子研发流水线

---

## 安装

```bash
# 开发模式安装
pip install -e ~/Public/QuantNodes/research/strategy-research

# 或作为 QuantNodes 的一部分
pip install -e ~/Public/QuantNodes
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

第一次跑会得到类似：
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

## CLI 命令（11 个）

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

设置 `OPENAI_API_KEY`（或 DEEPSEEK_API_KEY / KIMI_API_KEY / QWEN_API_KEY / ANTHROPIC_API_KEY 任一）后，`LLM Provider` 变 `[OK]`，rc=0 可启动。

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
"a_share": ["tencent", "mootdx", "eastmoney", "baostock", "akshare", "tushare", "local"]
"hk":     ["eastmoney", "yahoo", "futu", "yfinance", "akshare", "local"]
"us":     ["yahoo", "stooq", "sina", "eastmoney", "yfinance", "tiingo", "fmp", "finnhub", "alphavantage", "akshare", "local"]
"crypto": ["okx", "ccxt", "yfinance", "local"]
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

YAML 配置示例：
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

## 设计理念

- **Karpathy 极简**: 框架提供工具和循环指引，**不调 LLM**（P1 阶段接通）
- **Skill/Harness 模式**: 外部 Agent 读 prompt 后自主决策
- **通用性**: 通过 `prepare.evaluate()` 目标函数接口适配不同策略
- **实验可复现**: 每次实验保存 SHA-256 快照到 `run_card.json`，可随时复现
- **磁盘优先**: 所有指令写在文件里 — Agent 中途崩溃可从同套文件恢复 context
- **借鉴来源**: 借鉴 vibe-trading-ai 0.1.11 (HKUDS, MIT) 的设计模式（详见 `docs/enhancement.md`）

---

## 借鉴路线图（[docs/enhancement.md](docs/enhancement.md)）

| 阶段 | 范围 | 状态 |
|---|---|---|
| **P0** | 修通 init（`.format()`/DuckDB/OHLCV/默认因子/CLI/preflight/eastmoney）| ✅ 完成 |
| **P1** | Agent 真跑（替换 stub 接通 LLM）| 待启动 |
| **P2** | Skills + Swarm + Memory（11 个角色 DAG）| 待 P1 完成 |
| **P3** | Goal + Hypothesis + Validation（MC + Bootstrap + WF）| 待 P2 完成 |

---

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行全部测试
pytest                                    # 3221 passed
pytest tests/test_preflight.py -v         # 单跑 preflight 测试
pytest tests/test_cli_init.py -v           # 单跑 init 测试

# 代码检查
ruff check .
```

### 测试覆盖（P0 新增 77 个）

- `tests/test_cli_init.py` — `_render_template` / `cmd_init` / `cmd_evaluate`
- `tests/test_preflight.py` — 4 项 check + 总入口
- `tests/test_ohlcv_save.py` — `save_ohlcv_data` / `generate_sample_ohlcv_data`
- `tests/test_eastmoney_loader.py` — secid 映射 / loader 行为 / fallback chain

---

## 许可证

MIT