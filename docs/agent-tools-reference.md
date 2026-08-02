# Agent 工具参考手册 (Tool Reference)

系统化梳理 AgentLoop 注册的全部 32 个内置工具：用法、输入参数、预期输出、前置条件、注意事项、以及输出后对 agent 的后续引导建议。

> 维护约定：工具源位于 `src/strategy_research/core/agent/builtin_tools/`。
> 本文档应与 `build_default_registry()` 的输出保持一致；新增/修改工具时请同步更新。

## 总览

| 类别 | 工具 | 副作用 | 白名单角色 |
|------|------|--------|-----------|
| 文件/代码 | `read_file` `list_files` `write_file` `git_diff` | write_file 写文件 | 多角色 |
| 回测 | `run_backtest` `list_history` `strategy_compare` `drawdown_analysis` `benchmark_comparison` | run_backtest 写 runs/ | strategist / backtest_diagnostics |
| 因子 | `compute_factor` `factor_analysis` `factor_cross_sectional_analysis` `factor_quintile_returns` `factor_ic_decay` `factor_turnover` | 只读 | factor_analyst / researcher |
| 行情数据 | `get_market_data` `commit_market_data` `import_data` `list_data_sources` `search_symbol` | get_market_data 写 parquet 缓存; commit/import 写 DuckDB | researcher / data_quality / strategist |
| 其他分析 | `options_pricing` `pattern_recognition` | 只读 | — |
| 技能 | `list_skills` `load_skill` | 只读 | 通用 |
| Web | `web_search` `read_url` `read_document` | 只读(需依赖) | researcher / strategist |
| Goal | `create_goal` `add_evidence` `complete_goal` `get_goal_status` `list_goals` | 写 goals.db | 通用 |

**通用约定：**

- 所有工具返回 JSON 字符串。成功：`{"status": "ok", ...}`；失败：`{"status": "error", "error", "received", "expected", "fix", "tool"}`。
- `workspace` 与 `session_id` 由 AgentLoop 自动注入，LLM 无需（也不应猜测）传值——但 schema 中仍列出。
- 有副作用的工具（`is_readonly=False`）：`write_file` `run_backtest` `get_market_data` `import_data` `commit_market_data` `create_goal` `add_evidence` `complete_goal`。
- 依赖检测：`web_search`/`read_document` 依赖对应 Python 包，缺失时被排除注册。

---

## 1. 文件 / 代码

### read_file

- **用途**：读取 workspace 内文件（只读）。
- **输入**：`workspace`(自动)、`path`(必填，相对路径)、`limit`(行数上限，可选)、`offset`(起始行，可选)。
- **允许读取根**：`strategies/` `templates/` `memory/` `logs/` `data/` `docs/` `.`。
- **预期输出**：`{path, content, total_lines, returned_lines}`。
- **前置条件**：文件存在于允许根目录内，UTF-8 编码。
- **注意事项**：
  - 读取目录会报 "not a regular file"——用 `list_files` 列目录。
  - 非 UTF-8（二进制/PDF）会报错——用 `read_document` 读 PDF。
  - 不要 `read_file` 读 `data.duckdb`（二进制）。
- **后续引导**：成功后 agent 已获得文件内容，可直接判断下一步（修改 / 运行 / 参考）。

### list_files

- **用途**：列出 workspace 目录/文件结构。
- **输入**：`workspace`(自动)、`path`(可选，默认 `.`)、`pattern`(glob 过滤，可选)。
- **预期输出**：`{path, entries: [{name, type, size}], count}`。
- **前置条件**：路径存在且为目录。
- **注意事项**：`path` 不存在报错并给出 `fix`。
- **后续引导**：探索 workspace 的第一步——任何行动前建议先 `list_files` 确认结构。

### write_file

- **用途**：写文件（受沙箱 + AST 校验保护）。
- **输入**：`workspace`(自动)、`path`(必填，相对路径)、`content`(必填)。
- **允许写入根**：`strategies/` `templates/` `memory/` `logs/`。
- **预期输出**：`{path, bytes_written}`。
- **注意事项**：
  - `.py` 文件经 AST 校验；含 `exec`/`eval`/被禁 import/dunder 访问会被拒绝。
  - 写路径超出允许根会报错。
- **后续引导**：写入策略文件后建议 `run_backtest(strategy_name=...)`。

### git_diff

- **用途**：查看 workspace 的 git 差异。
- **输入**：`workspace`(自动)、`staged`(仅暂存，可选)、`ref1`/`ref2`(对比提交，可选)、`pathspec`(限定路径，可选)、`max_lines`(默认 200)。
- **预期输出**：`{diff, total_lines, truncated, staged}`。
- **注意事项**：`pathspec` 不能以 `-` 开头（防注入）；非 git 仓库会报错；大 diff 会截断（用 pathspec 缩小）。
- **后续引导**：常用于策略改动评审。

---

## 2. 回测

### run_backtest

- **用途**：运行回测，读取 `strategies/<name>/config.yaml`，产出 runs/。
- **输入**：`workspace`(自动)、`strategy_name`(必填)、`action`(默认 "agent")、`description`(可选)、`yaml_path`(覆盖配置，可选)。
- **预期输出**：`{run, strategy, metrics, status, next_step}`。`next_step` 提示 `list_history`/`drawdown_analysis`/`benchmark_comparison`。
- **前置条件**：`strategies/<name>/config.yaml` 存在且合法；所需行情已入 DuckDB（`data.source: auto+duckdb` 或 `duckdb`）。
- **注意事项**：
  - 数据为空时错误响应自带 `workflow` 提示：`get_market_data` → `commit_market_data` → `run_backtest`。
  - `auto+duckdb`：DuckDB 缓存 + 在线刷新；`duckdb`：仅本地（需先导入）。
- **后续引导**：成功后用 `list_history` 对比历史，`drawdown_analysis` 看回撤，`benchmark_comparison` 对照基准。

### list_history

- **用途**：列出历史回测结果（读 `results.tsv`）。
- **输入**：`workspace`(自动)、`strategy_name`(过滤，可选)、`limit`(默认 20)。
- **预期输出**：`{source, n_rows, runs: [row...]}`；无结果时 `runs: []`（非错误）。
- **前置条件**：`strategies/<name>/runs/results.tsv` 存在。
- **注意事项**：未指定 strategy 时只扫第一个含 results.tsv 的策略目录。
- **后续引导**：用数据判断是否保留/回滚/继续实验。

### strategy_compare

- **用途**：多策略指标并排对比（读各策略最新 results.tsv 行）。
- **输入**：`workspace`(自动)、`strategy_names`(必填，逗号分隔)、`metrics`(默认 `sharpe,ann_return,max_dd,calmar,turnover,win_rate`)。
- **预期输出**：`{strategies, metrics, comparison: [{strategy, ...metric, run_name}]}`。
- **前置条件**：各策略 `runs/results.tsv` 存在且非空。
- **注意事项**：缺失的策略在 comparison 里带 `error` 字段，不整体失败。
- **后续引导**：对比后选优策略深入分析或回滚劣者。

### drawdown_analysis

- **用途**：分析最新一次回测的权益曲线回撤。
- **输入**：`workspace`(自动)、`strategy_name`(必填)、`top_n`(默认 5)。
- **预期输出**：`{strategy, run, equity_length, max_drawdown, current_drawdown, n_drawdown_periods, top_drawdowns}`。
- **前置条件**：最新 run 目录含 `equity.csv`/`equity_curve.csv`/`portfolio.csv`/`nav.csv` 之一，或 run.log 含 equity 数值。
- **注意事项**：找不到权益曲线时报错；权益点 < 10 报错。
- **后续引导**：依据回撤深度/恢复时长决定是否调整风控参数。

### benchmark_comparison

- **用途**：策略 vs 基准的 alpha/beta/跟踪误差/信息比率。
- **输入**：`workspace`(自动)、`strategy_name`(必填)、`benchmark_code`(必填，如 `000300.SH`)、`start_date`/`end_date`(可选)。
- **预期输出**：`{strategy, benchmark, n_periods, alpha_annualized, beta, tracking_error, information_ratio, max_relative_drawdown, ...}`。
- **前置条件**：策略有最新权益曲线；基准代码已在 DuckDB `ohlcv` 中。
- **注意事项**：基准查询用字符串拼接 `asset = '{benchmark_code}'`——仅接受已知代码。
- **后续引导**：alpha 显著且 IR 高 → 可保留；否则审视因子暴露。

---

## 3. 因子

> 所有因子工具从 DuckDB `ohlcv` 视图读数据（`ohlcv` 是 `price_data` 表的视图，含 `date/asset/open/high/low/close/volume`）。
> 前置条件：至少一个资产已通过 `get_market_data` + `commit_market_data`（或 `import_data`）入库。

### compute_factor

- **用途**：单资产计算因子表达式，返回序列样本。
- **输入**：`workspace`(自动)、`factor_code`(必填，如 `ts_mean(close, 20)/ts_mean(close,60)-1`)、`asset`(可选，默认第一个)、`factor_name`(可选)、`n_samples`(默认 5)。
- **预期输出**：`{factor_name, factor_code, asset, n_total, n_non_null, sample, first_date, last_date, next_step}`。
- **注意事项**：
  - 需单资产宽表；数据来自 DuckDB `ohlcv`，按 asset 过滤后 `set_index('date')`。
  - 指定 asset 不存在时报错并列出可用资产前 10。
  - 因子算子语法以 `templates/.skills/factor-research.md` 为准——**不要猜测算子**。
- **后续引导**：`factor_analysis` 算 IC/IR，或 `factor_cross_sectional_analysis` 跨资产验证。

### factor_analysis

- **用途**：单资产因子 IC/IR 统计。
- **输入**：`workspace`(自动)、`factor_code`(必填)、`asset`(可选)、`forward_days`(默认 5)。
- **预期输出**：`{factor_code, asset, forward_days, ic_mean, spearman_ic, n_observations, next_step}`。
- **注意事项**：对齐后样本 < 10 报错 "insufficient data"。
- **后续引导**：跨资产验证或直接构建策略。

### factor_cross_sectional_analysis

- **用途**：跨资产截面 IC（Pearson + Spearman）、IR、IC>0 比例。
- **输入**：`workspace`(自动)、`factor_code`(必填)、`universe`(逗号分隔代码或 `all`，默认 `all`)、`start_date`/`end_date`(可选)、`forward_days`(默认 5)。
- **预期输出**：`{factor_code, n_assets, n_dates, forward_days, ic_pearson_mean, ic_pearson_std, ir, ic_pearson_gt0_ratio, ic_spearman_mean, ic_spearman_std, sample_dates, next_step}`。
- **注意事项**：
  - **需 ≥3 资产**，因子计算成功也需 ≥3，有效 IC 观测 ≥5。
  - `universe` 中不存在的代码会报错。
- **后续引导**：`factor_quintile_returns` 看分组单调性，`factor_ic_decay` 看衰减。

### factor_quintile_returns

- **用途**：按因子值分组（默认 5 组）的平均前向收益 + 多空价差。
- **输入**：`workspace`(自动)、`factor_code`(必填)、`universe`(默认 `all`)、`start_date`/`end_date`(可选)、`n_groups`(默认 5)、`holding_period`(默认 5)。
- **预期输出**：`{factor_code, n_groups, holding_period, n_assets_used, Q1..Qn_mean_return, long_short_spread, next_step}`。
- **注意事项**：需 `n_groups*2` 资产下限。
- **后续引导**：看 Q1→Qn 单调性与价差符号；`factor_ic_decay`/`factor_turnover` 深入。

### factor_ic_decay

- **用途**：多前向窗口（默认 1,5,10,20,60 天）的 IC 衰减曲线。
- **输入**：`workspace`(自动)、`factor_code`(必填)、`universe`(默认 `all`)、`start_date`/`end_date`(可选)、`horizons`(逗号分隔，默认 `1,5,10,20,60`)。
- **预期输出**：`{factor_code, n_assets, ic_decay: [{horizon, ic_mean, ic_std, ir, n_periods}], next_step}`。
- **注意事项**：需 ≥3 资产因子计算成功。
- **后续引导**：根据最佳 horizon 进入策略构建。

### factor_turnover

- **用途**：因子排名稳定性（低换手 = 稳定因子）。
- **输入**：`workspace`(自动)、`factor_code`(必填)、`universe`(默认 `all`)、`start_date`/`end_date`(可选)、`rebalance_freq`(默认 5)。
- **预期输出**：`{factor_code, n_assets, n_periods, rebalance_freq_days, avg_turnover, median_turnover, std_turnover, avg_rank_stability, next_step}`。
- **注意事项**：需 ≥3 资产；采样期 < 2 报错。
- **后续引导**：低换手因子适合实盘；进截面复核 + 回测。

---

## 4. 行情数据

### get_market_data

- **用途**：按 fallback 链获取 OHLCV 行情，**写入 parquet 缓存并返回摘要**（不进 prompt）。
- **输入**：`codes`(必填，list[str] 或 "A,B,C" 字符串或 JSON 字符串)、`start_date`/`end_date`(必填，ISO 日期)、`interval`(默认 `1D`)、`source`(可选覆盖)、`max_rows`(默认 500)。
- **预期输出**：`{cached: {code: cache_key}, summary: {code: {rows, status, cache_key, first_close, last_close, close_min, close_max, avg_volume}}, preview: {code: [前5行]}, meta: {...}, next_step}`。
- **前置条件**：至少一个数据源可用（`list_data_sources` 可查）；网络可用。
- **注意事项**：
  - **上下文安全设计**：全量行情写 `~/.quantnodes-research/loader_cache/<key>.parquet`，**不进入 LLM prompt**。这是 context 溢出修复的核心（见 `docs/context-overflow-fix.md`）。
  - `source` 指定且不可用 → 报错；不指定 → `detect_market` 自动选源。
  - **纯数字代码（如 `510300`）会被误判为 FRED/macro**；A 股代码务必带后缀 `600519.SH`/`000858.SZ`。
  - `codes` 形状错误时容错：支持 list / 逗号分隔字符串 / JSON 字符串 / 单键 dict 包裹。
- **后续引导**：**必须调用 `commit_market_data`** 把缓存合并入 DuckDB，否则数据不可用于回测/因子。`next_step` 字段已给出示例。

### commit_market_data

- **用途**：把 `get_market_data` 的 parquet 缓存合并入 workspace DuckDB `price_data`。
- **输入**：`workspace`(自动)、`cache_keys`(必填，list)、`codes`(必填，list，与 cache_keys 平行)、`strategy_name`(默认 `default`)。
- **预期输出**：`{committed: [{code, rows}], total_rows, missing, strategy_name, next_step, message}`。
- **前置条件**：cache_keys 来自 `get_market_data` 返回；缓存文件存在。
- **注意事项**：`cache_keys` 与 `codes` 必须等长非空；`INSERT OR REPLACE` 幂等（重复 commit 不重复插入）。
- **后续引导**：`next_step` 指向 `run_backtest(strategy_name=...)`。

### import_data

- **用途**：手动/外部 OHLCV 数据导入 DuckDB（**非推荐主流程**）。
- **输入**：`workspace`(自动)、`data`(必填，`{asset_code: [records]}`)、`strategy_name`(默认 `default`)。
- **预期输出**：`{imported, n_codes, strategy_name, message, next_step}`。
- **注意事项**：
  - **推荐流程已改为 `get_market_data` → `commit_market_data`**；`import_data` 仅用于粘贴外部数据/CSV。
  - `data` 支持各种 LLM 错误包裹（单键 dict、JSON 字符串）的容错。
  - 记录需含 `trade_date`/`date` + OHLCV；缺 close 列会报错。
- **后续引导**：`next_step` 指向 `run_backtest` 或 `factor_analysis`。

### list_data_sources

- **用途**：列出注册的数据源与可用性/认证需求。
- **输入**：无。
- **预期输出**：`{n_sources, sources: [{name, available, markets, requires_auth}]}`。
- **前置条件**：无。
- **注意事项**：调用前会 `_ensure_registered()`。
- **后续引导**：确认哪个源可用后决定 `get_market_data` 是否需 `source` 覆盖。

### search_symbol

- **用途**：按名称/代码搜索 A 股标的（akshare）。
- **输入**：`query`(必填)、`market`(默认 `a_share`)、`limit`(默认 10)。
- **预期输出**：`{results: [{code, name, market, price, change_pct}], query, market, limit, n_results}`。
- **前置条件**：`akshare` 已安装、网络可用。
- **注意事项**：非 a_share 市场支持有限。
- **后续引导**：用搜索到的代码调用 `get_market_data`。

---

## 5. 其他分析

### options_pricing

- **用途**：Black-Scholes 期权定价 + Greeks。
- **输入**：`spot`/`strike`/`rate`/`volatility`/`time_to_expiry`/`option_type`(全必填)。
- **预期输出**：`{option_type, spot, strike, rate, volatility, time_to_expiry, price, delta, gamma, theta, vega, rho}`。
- **注意事项**：需 scipy；参数需为正；option_type 限 `call`/`put`。
- **后续引导**：无固定后续。

### pattern_recognition

- **用途**：趋势/支撑阻力/波动率挤压检测。
- **输入**：`workspace`(自动)、`asset`(可选)、`lookback`(默认 60)。
- **预期输出**：`{asset, lookback, current_price, range_pct, patterns: [{pattern, confidence, ...}]}`。
- **前置条件**：DuckDB 有 OHLCV；数据 ≥ 10 行。
- **注意事项**：基于 MA20/MA5 的简化启发式，非严格形态识别。
- **后续引导**：模式结果作为研究输入。

---

## 6. 技能

### list_skills

- **用途**：列出方法论技能（workspace `.skills/` 优先，合并内置 `templates/.skills/`）。
- **输入**：`workspace`(自动)、`category`(可选过滤)。
- **预期输出**：`{n_skills, categories, skills: [{name, category, description}]}`。
- **后续引导**：用 `load_skill` 读取感兴趣的技能全文。

### load_skill

- **用途**：加载技能全文。
- **输入**：`workspace`(自动)、`name`(必填)。
- **预期输出**：`{name, category, description, tags, content}`。
- **注意事项**：技能不存在时返回可用技能名列表（最多 20）。
- **后续引导**：按技能内工作流执行。

---

## 7. Web

### web_search

- **用途**：DuckDuckGo 搜索。
- **输入**：`query`(必填)、`max_results`(默认 10)。
- **预期输出**：搜索结果（title/URL/snippet）。
- **前置条件**：`duckduckgo_search` 已安装（未安装则工具不注册）。
- **注意事项**：网络可能受限；失败时给出可用修复建议。
- **后续引导**：对关键结果用 `read_url` 深入。

### read_url

- **用途**：抓取网页转 Markdown。
- **输入**：`url`(必填)、`max_chars`(默认 10000)。
- **预期输出**：网页正文 Markdown。
- **注意事项**：仅 http(s)；大页面按 max_chars 截断。
- **后续引导**：提取信息后用于研究/证据。

### read_document

- **用途**：PDF 提取文本（PyMuPDF）。
- **输入**：`path`(必填，绝对路径)、`max_pages`(默认 50)。
- **预期输出**：带页码标记的提取文本。
- **前置条件**：`fitz` (PyMuPDF) 已安装（未安装不注册）。
- **后续引导**：用提取内容辅助研究。

---

## 8. Goal

> Goal 工具写 `goals.db`。`session_id` 由 AgentLoop 自动注入（聊天场景），否则回退 `"default"`。
> 典型生命周期：`create_goal` → `add_evidence`(可多次) → `complete_goal`；用 `get_goal_status`/`list_goals` 查看。

### create_goal

- **用途**：创建/替换当前会话的研究目标。
- **输入**：`session_id`(自动)、`objective`(必填)、`criteria`(可选，list[str]，为空用默认)。
- **预期输出**：`{goal_id, goal_status, objective, progress_percent}`。
- **注意事项**：已存在目标会被取代（superseded）。
- **后续引导**：开展研究并用 `add_evidence` 记录证据。

### add_evidence

- **用途**：向当前目标追加证据。
- **输入**：`session_id`(自动)、`text`(必填)、`criterion_id`/`run_id`(可选)、`source_type`(默认 `evidence`)。
- **预期输出**：`{evidence_id, goal_id, progress_percent}`。
- **前置条件**：会话已有 active goal（否则提示先 `create_goal`）。
- **注意事项**：evidence 关联 criterion 可推动进度百分比。
- **后续引导**：证据累积完成后 `complete_goal`。

### complete_goal

- **用途**：完成当前目标（lite 模式，校验每个必填 criterion 有证据）。
- **输入**：`session_id`(自动)、`recap`(可选摘要)。
- **预期输出**：`{goal_id, goal_status, recap}`。
- **前置条件**：有 active goal。
- **注意事项**：缺证据的必填 criterion 会阻止完成。
- **后续引导**：完成后可 `create_goal` 开始新目标。

### get_goal_status

- **用途**：当前目标快照（进度/标准/证据数）。
- **输入**：`session_id`(自动)。
- **预期输出**：`{has_goal, goal_id, goal_status, objective, progress_percent, criteria_count, evidence_count, criteria}`；无目标时 `{has_goal: false}`。
- **后续引导**：根据进度决定继续/完成。

### list_goals

- **用途**：列出目标（可过滤）。
- **输入**：`session_id`(可选)、`status`(可选，`active`/`complete`/`abandoned`)、`limit`(默认 10)。
- **预期输出**：`{goals: [{goal_id, session_id, goal_status, objective, progress_percent, created_at}], count}`。
- **注意事项**：非法 status 值报错并提示合法枚举。
- **后续引导**：查看历史目标/恢复研究。

---

## 标准工作流

### 工作流 A：数据获取 → 入库 → 回测（推荐）

```
get_market_data(codes=['600519.SH','000858.SZ'], start_date='2023-01-01', end_date='2024-12-31')
  └─ 评估 summary/preview 数据质量（不进 prompt，安全）
commit_market_data(cache_keys=[...], codes=[...], strategy_name='mom')
  └─ 合并入 DuckDB（返回 next_step）
run_backtest(strategy_name='mom')
```

### 工作流 B：因子研究四连

```
factor_cross_sectional_analysis(factor_code='ts_mean(close,20)/ts_mean(close,60)-1', universe='all')
  └─ 截面 IC → next_step
factor_quintile_returns(factor_code=..., universe='all')
  └─ 分组单调性 → next_step
factor_ic_decay(factor_code=..., horizons='1,5,10,20,60')
  └─ 衰减 → next_step
factor_turnover(factor_code=..., rebalance_freq=5)
  └─ 稳定性 → 若稳定则 run_backtest
```

### 工作流 C：策略创建

```
list_files(path='strategies')          # 确认结构
write_file('strategies/<name>/strategy.py', ...)
write_file('strategies/<name>/config.yaml', ...)
run_backtest(strategy_name='<name>')   # 首次回测
```

### 工作流 D：策略评估与对比

```
list_history(strategy_name='<name>')
strategy_compare(strategy_names='a,b,c')
drawdown_analysis(strategy_name='<name>')
benchmark_comparison(strategy_name='<name>', benchmark_code='000300.SH')
```

### 工作流 E：Goal 驱动研究

```
create_goal(objective='...', criteria=['...'])
  → 研究过程中 add_evidence(text=..., criterion_id=...)
  → get_goal_status 看进度
  → complete_goal(recap='...')
```

### 工作流 F：外部资料研究

```
web_search(query='A股动量策略 实证')
read_url(url='https://...')
read_document(path='/abs/path/paper.pdf')
```

---

## 每角色工具白名单

见 `src/strategy_research/core/agent/role_factory.py::_ROLE_TOOL_WHITELIST`：

| 角色 | 白名单工具 |
|------|-----------|
| researcher | read_file, list_history, factor_analysis, web_search, read_url, get_market_data, search_symbol |
| data_quality | read_file, web_search, read_url, get_market_data, list_data_sources |
| factor_analyst | read_file, compute_factor, factor_analysis, get_market_data |
| strategist | read_file, write_file, run_backtest, git_diff, web_search, read_url, get_market_data |
| portfolio_construction | read_file, get_market_data |
| risk_controller | read_file, factor_analysis, get_market_data |
| attribution_analyst | read_file, factor_analysis |
| anti_overfit_analyst | read_file, list_history, factor_analysis |
| backtest_diagnostics | read_file, run_backtest, git_diff |
| critic | read_file, list_history |

> 注意：白名单用注册名；因子四连 / 策略对比等 Phase-4 工具不在任何角色白名单中——聊天模式（`allowed_tools=None`）才全量暴露。

---

## 已知缺陷与边界清单

1. **`data-routing.md` 引用的 7 个数据工具未实现**：`get_fund_flow` `get_dragon_tiger` `get_northbound_flow` `get_margin_trading` `get_block_trades` `get_shareholder_count` `get_lockup_expiry` `get_sector_info` `get_research_reports` `get_stock_news` `get_financial_statements` `get_options_chain` `get_macro_series` 均不存在。技能文档与实际注册不符（仅 `get_market_data` 等 5 个数据工具存在）。
2. **纯数字代码误判**：`detect_market('510300')` → `macro`（FRED 分支优先匹配数字）。规避：A 股代码带 `.SH/.SZ` 后缀。
3. **`000001.SH` 判为 index**：`is_index` 匹配优先于股票，回测以 `a_share` 股票为主要场景时注意。
4. **跨资产工具资产下限**：`factor_cross_sectional_analysis`/`factor_ic_decay`/`factor_turnover` 需 ≥3 资产；`factor_quintile_returns` 需 `n_groups*2`。
5. **`list_history` 无 strategy 参数时只扫第一个策略目录**。
6. **`drawdown_analysis`/`benchmark_comparison` 依赖权益曲线文件名约定**：`equity.csv`/`equity_curve.csv`/`portfolio.csv`/`nav.csv`。
7. **web 工具条件注册**：`web_search`/`read_document` 依赖包未装则不注册——`list_data_sources` 之外无直接前端指示。
8. **`import_data` 已降级**：描述与错误提示均引导走 `get_market_data` → `commit_market_data` 主流程。
