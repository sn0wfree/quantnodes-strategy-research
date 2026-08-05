# Agent 工具参考手册 (Tool Reference)

系统化梳理 AgentLoop 注册的全部 32 个内置工具：用法、输入参数、预期输出、前置条件、注意事项、以及输出后对 agent 的后续引导建议。

> 维护约定：工具源位于 `src/strategy_research/core/agent/builtin_tools/`。
> 本文档应与 `build_default_registry()` 的输出保持一致；新增/修改工具时请同步更新。

---

## 工具范式 v2（设计定稿）

> 状态：**设计定稿，尚未实施**。本范式由 9 个维度的逐项讨论收敛而成（2026-08-05）。
> 实施计划见下文"实施计划"；P3 迁移完成后，本文档各工具条目将按说明书模板重写。

### 范式总览（9 维度决策）

| 维度 | 定稿 |
|---|---|
| **本体** | 工具 = `name + 声明 + 执行体`；无状态机；工具间可互相调用（一般不），组合深度 = 1 |
| **被动学习** | 记录被动（event_log + trace.jsonl 双源，agent 正常使用即落盘）+ 扫描主动（定期挖掘）；双粒度挖掘（同 turn 合作性共现 / 跨 turn 流程性序列），沉淀标准不同；**后置实现（P6）** |
| **组合形态** | 声明式配置（`steps` + 参数映射符号）→ 组合库（workspace `tools/combo/*.yml`）→ 运行时实例化注册；只支持线性步骤，复杂逻辑改手写工具——**配置不滑向 DSL** |
| **定义形态** | 源码单源说明书（统一模板，docstring 约定节）+ 注册时 inspect 自动生成**简略版**预置 prompt + `tool_help` 按需返回**详细版**；消灭手写 `parameters` dict 双写 |
| **错误范式** | C 混合：业务失败 `return err_actionable(...)`；意外异常 `raise` → `BaseTool` 顶层统一结构化兜底；语义区分：return = 确定性失败（不重试）、raise = 意外（transient 可重试） |
| **上下文与副作用** | 显式 `ToolContext`（workspace / session_id / progress），注入参数从 LLM schema 剥离；声明式 `effects`（写 DB / 写 FS / 网络），`is_readonly` 作为派生布尔保留兼容 |
| **参数容错** | 框架统一层：按说明书类型声明驱动（JSON 字符串 parse / 单键包裹解包 / 类型强转），仅在声明类型与收到类型不匹配时触发；`safe_get_param` 退役 |
| **注册发现** | C 分层：显式核心清单 + 能力组注册函数（`check_available` 依赖门控保留）+ 组合库加载器；契约测试保障 注册表 ↔ `__all__` ↔ 说明书 ↔ docs 一致 |
| **编排引导** | 三层面：简略版目录（常驻，只答"哪个工具合适"）/ 详细版说明书（按需，含使用时机/相关工具/错误处理范式）/ `err_actionable.fix`（运行时 debug）；fix_msg 与说明书同源；高频流程沉淀为组合工具 |

### 工具说明书模板（统一约定，最终定稿）

每个工具在源码 docstring 中按约定章节书写说明书；注册时与查询时各生成一版。

**类型/默认值单源**：`execute` 使用显式签名（`def execute(self, ctx, strategy_name: str, yaml_path: str | None = None)`）——参数名/类型/默认值由签名与注解承担（框架容错层消费 `__annotations__`，简略版必填参数由此派生）；docstring 只写语义。存量 `**kwargs` 工具迁移完成前，注册时回退 `parameters.required`。

```
简略版（注册时 inspect 生成 → 预置 prompt，轻量目录）：
  - name[类别]: 用途一句话；必填: p1,p2；副作用: 只读|写DB,写FS,网络

  字段来源：
    name        类名
    用途一句话   docstring 首行（PEP 257 摘要行，与详细版同源，~20 字）
    类别         领域分类：文件/回测/因子/行情/分析/技能/Web/Goal/Shell
                （组合工具 = 配置指定，默认按子工具主类别推断）
    必填参数     显式签名无默认值参数（只列名，不列类型/可选）
    副作用       effects 声明（写DB / 写FS / 网络，可组合；未声明回退 is_readonly）
  预算：33 工具 ≈ 600-700 token/轮（现状全参数渲染的 1/3~1/5）

详细版（tool_help(name) 实时读源码 → docstring 原文，按需返回）：
  # <name> 工具说明书
  版本          x.y.z（语义版本）
  变更          当前版关键变更摘要（历史归 git）
  ## 用途       2-4 句：做什么、典型场景、何时不用（与其他工具的分工边界）
  ## 参数       每个参数的语义、默认值含义、参数间关系（类型/默认值由签名
              单源，不在此重复）
  ## 示例       1-2 个最小可用调用示例（JSON 形式，含必填参数）
  ## 边界       前置条件、上下文安全行为、幂等性、单位/格式约定
  ## 错误处理范式 各失败场景：触发条件 → fix 指引 → 是否可重试 → 是否幂等
  ## 相关工具   前置工具 / 结果消费工具（可选）
```

- docstring 约定节：`版本:` / `变更:` / `## 用途` / `## 参数` / `## 示例` / `## 边界` / `## 错误处理范式` / `## 相关工具`
- `tool_help` 返回 docstring **原文**（markdown，无解析层）；组合工具说明书两版从组合配置（steps + 映射符号）生成

### 组合工具细节

- **配置**：workspace `tools/combo/<name>.yml`：`name + description + steps: [{tool, params_map}]`，参数映射用简单符号（如 `step2.param = step1.result.code`）
- **执行**：`registry.execute` 工具级调用子工具（复用已注册执行路径，对 agent 无新增抽象层）；同一 `ToolContext` 传递
- **中间结果**：正常路径**不进上下文**；默认返回 `{status: ok, result: <最后一步关键输出>}`，配置可声明 `with_summary` 返回步骤摘要；报错时**完整错误透传** + 失败步骤/工具定位
- **副作用**：父工具 effects = 子工具配置并集；嵌套深度 = 1（组合不组合组合工具）
- **沉淀闭环**：被动学习挖掘 → 规则初筛 + 组合提案 + 人工确认 → 写入组合库；高频复用的组合可"提升"为手写工具固化进显式清单

### 实施计划

| 阶段 | 内容 | 状态 |
|---|---|---|
| P1 | 说明书基础设施：模板规范 + 注册时解析器（简略版）+ `tool_help` 工具 | **实施中** |
| P2 | BaseTool 骨架 v2：ToolContext / 框架统一容错层 / 错误兜底 / effects；loop 注入单点化（sync/async 合一） | 待实施 |
| P3 | 33 个存量工具迁移（一次性切换，无旧 `parameters` dict 回退）：docstring 说明书填写、删手写解析与样板、effects 声明 | 待实施 |
| P4 | 分层注册重构 + 组合库：核心显式清单 + 能力组 + 组合库加载器 + 组合执行器 | 待实施 |
| P5 | 契约测试（注册表↔`__all__`↔说明书↔docs 一致性；fix_msg 与说明书同源断言）+ 文档同步 | 待实施 |
| P6 | 被动学习：挖掘器（双源双粒度共现）+ 评估器（规则初筛 + 提案 + 人工确认）+ 沉淀（组合库 + 回馈统计） | 后置立项 |

### 对本文档其余部分的衔接

- **通用约定**（下方）：P2/P3 落地后更新——`workspace`/`session_id` 从 schema 剥离、副作用改为 effects 声明
- **各工具条目**（§1-§8）：P3 迁移完成后按说明书模板重写；迁移完成前本节保持现行参考
- **工具后续引导策略（next_step 取舍）**：由维度 6"三层面引导"继承并演进——返回值仍保持纯净（`next_step` 不回归），引导迁入说明书与运行时 fix

---

## 总览

| 类别 | 工具 | 副作用 | 白名单角色 |
|------|------|--------|-----------|
| 文件/代码 | `read_file` `list_files` `write_file` `git_diff` | write_file 写文件 | 多角色 |
| 回测 | `run_backtest` `list_history` `strategy_compare` `drawdown_analysis` `benchmark_comparison` | run_backtest 写 runs/ | strategist / backtest_diagnostics |
| 因子 | `compute_factor` `factor_analysis` `factor_cross_sectional_analysis` `factor_quintile_returns` `factor_ic_decay` `factor_turnover` | 只读 | factor_analyst / researcher |
| 行情数据 | `get_market_data` `import_data` `list_data_sources` `search_symbol` | get_market_data(persist=True) / import 写 DuckDB | researcher / data_quality / strategist |
| 其他分析 | `options_pricing` `pattern_recognition` | 只读 | — |
| 技能 | `list_skills` `load_skill` | 只读 | 通用 |
| Web | `web_search` `read_url` `read_document` | 只读(需依赖) | researcher / strategist |
| Goal | `create_goal` `add_evidence` `complete_goal` `get_goal_status` `list_goals` | 写 goals.db | 通用 |

**通用约定：**

- 所有工具返回 JSON 字符串。成功：`{"status": "ok", ...}`；失败：`{"status": "error", "error", "received", "expected", "fix", "tool"}`。
- `workspace` 与 `session_id` 由 AgentLoop 自动注入，LLM 无需（也不应猜测）传值——但 schema 中仍列出。
- 有副作用的工具（`is_readonly=False`）：`write_file` `run_backtest` `get_market_data` `import_data` `create_goal` `add_evidence` `complete_goal`。
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
- **预期输出**：`{run, strategy, metrics, status}`。
- **前置条件**：`strategies/<name>/config.yaml` 存在且合法；所需行情已入 DuckDB（`data.source: auto+duckdb` 或 `duckdb`）。
- **注意事项**：
  - 数据为空时错误响应自带 `workflow` 提示：`get_market_data(persist=True)` → `run_backtest`。
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
> 前置条件：至少一个资产已通过 `get_market_data(persist=True)`（或 `import_data`）入库。

### compute_factor

- **用途**：单资产计算因子表达式，返回序列样本。
- **输入**：`workspace`(自动)、`factor_code`(必填，如 `ts_mean(close, 20)/ts_mean(close,60)-1`)、`asset`(可选，默认第一个)、`factor_name`(可选)、`n_samples`(默认 5)。
- **预期输出**：`{factor_name, factor_code, asset, n_total, n_non_null, sample, first_date, last_date}`。
- **注意事项**：
  - 需单资产宽表；数据来自 DuckDB `ohlcv`，按 asset 过滤后 `set_index('date')`。
  - 指定 asset 不存在时报错并列出可用资产前 10。
  - 因子算子语法以 `templates/.skills/factor-research.md` 为准——**不要猜测算子**。
- **后续引导**：`factor_analysis` 算 IC/IR，或 `factor_cross_sectional_analysis` 跨资产验证。

### factor_analysis

- **用途**：单资产因子 IC/IR 统计。
- **输入**：`workspace`(自动)、`factor_code`(必填)、`asset`(可选)、`forward_days`(默认 5)。
- **预期输出**：`{factor_code, asset, forward_days, ic_mean, spearman_ic, n_observations}`。
- **注意事项**：对齐后样本 < 10 报错 "insufficient data"。
- **后续引导**：跨资产验证或直接构建策略。

### factor_cross_sectional_analysis

- **用途**：跨资产截面 IC（Pearson + Spearman）、IR、IC>0 比例。
- **输入**：`workspace`(自动)、`factor_code`(必填)、`universe`(逗号分隔代码或 `all`，默认 `all`)、`start_date`/`end_date`(可选)、`forward_days`(默认 5)。
- **预期输出**：`{factor_code, n_assets, n_dates, forward_days, ic_pearson_mean, ic_pearson_std, ir, ic_pearson_gt0_ratio, ic_spearman_mean, ic_spearman_std, sample_dates}`。
- **注意事项**：
  - **需 ≥3 资产**，因子计算成功也需 ≥3，有效 IC 观测 ≥5。
  - `universe` 中不存在的代码会报错。
- **后续引导**：`factor_quintile_returns` 看分组单调性，`factor_ic_decay` 看衰减。

### factor_quintile_returns

- **用途**：按因子值分组（默认 5 组）的平均前向收益 + 多空价差。
- **输入**：`workspace`(自动)、`factor_code`(必填)、`universe`(默认 `all`)、`start_date`/`end_date`(可选)、`n_groups`(默认 5)、`holding_period`(默认 5)。
- **预期输出**：`{factor_code, n_groups, holding_period, n_assets_used, Q1..Qn_mean_return, long_short_spread}`。
- **注意事项**：需 `n_groups*2` 资产下限。
- **后续引导**：看 Q1→Qn 单调性与价差符号；`factor_ic_decay`/`factor_turnover` 深入。

### factor_ic_decay

- **用途**：多前向窗口（默认 1,5,10,20,60 天）的 IC 衰减曲线。
- **输入**：`workspace`(自动)、`factor_code`(必填)、`universe`(默认 `all`)、`start_date`/`end_date`(可选)、`horizons`(逗号分隔，默认 `1,5,10,20,60`)。
- **预期输出**：`{factor_code, n_assets, ic_decay: [{horizon, ic_mean, ic_std, ir, n_periods}]}`。
- **注意事项**：需 ≥3 资产因子计算成功。
- **后续引导**：根据最佳 horizon 进入策略构建。

### factor_turnover

- **用途**：因子排名稳定性（低换手 = 稳定因子）。
- **输入**：`workspace`(自动)、`factor_code`(必填)、`universe`(默认 `all`)、`start_date`/`end_date`(可选)、`rebalance_freq`(默认 5)。
- **预期输出**：`{factor_code, n_assets, n_periods, rebalance_freq_days, avg_turnover, median_turnover, std_turnover, avg_rank_stability}`。
- **注意事项**：需 ≥3 资产；采样期 < 2 报错。
- **后续引导**：低换手因子适合实盘；进截面复核 + 回测。

---

## 4. 行情数据

### get_market_data

- **用途**：按 fallback 链获取 OHLCV 行情，**写入 workspace DuckDB 并返回摘要**（全量数据不进 prompt）。已与 `commit_market_data` 合并——fetch + persist 一步完成。
- **输入**：`codes`(必填，list[str] 或 "A,B,C" 字符串或 JSON 字符串)、`start_date`/`end_date`(必填，ISO 日期)、`interval`(默认 `1D`)、`source`(可选覆盖)、`max_rows`(默认 500)、`persist`(默认 True，写 DuckDB)、`strategy_name`(默认 `default`)、`workspace`(自动注入，persist=True 时必需)。
- **预期输出**：`{summary: {code: {rows, status, first_close, last_close, close_min, close_max, avg_volume}}, preview: {code: [前5行]}, persisted: bool, strategy_name, persisted_rows, meta: {codes, start_date, end_date, interval, source, total_rows}}`。
- **前置条件**：至少一个数据源可用（`list_data_sources` 可查）；网络可用；persist=True 需 workspace（AgentLoop 自动注入）。
- **注意事项**：
  - **上下文安全设计**：全量行情直接写 DuckDB，**不进入 LLM prompt**；返回值仅含 summary+preview。这是 context 溢出修复的核心（见 `docs/context-overflow-fix.md`）。
  - `source` 指定且不可用 → 报错；不指定 → `detect_market` 自动选源。
  - **纯数字代码（如 `510300`）会被误判为 FRED/macro**；A 股代码务必带后缀 `600519.SH`/`000858.SZ`。
  - `codes` 形状错误时容错：支持 list / 逗号分隔字符串 / JSON 字符串 / 单键 dict 包裹。
  - `persist=True`（默认）写入 `price_data`（`ohlcv` 视图可见），`run_backtest`/`compute_factor`/`factor_*` 立即可用。`persist=False` 仅查看不写库。
  - **幂等**：`INSERT OR REPLACE` 按 (strategy_name, asset_code, date) 覆盖，重复获取不重复插入。
- **后续引导**：无需额外步骤——persist=True 已入库，可直接 `run_backtest` 或因子分析。

### import_data

- **用途**：手动/外部 OHLCV 数据导入 DuckDB（**非推荐主流程**）。
- **输入**：`workspace`(自动)、`data`(必填，`{asset_code: [records]}`)、`strategy_name`(默认 `default`)。
- **预期输出**：`{imported, n_codes, strategy_name, message}`。
- **注意事项**：
  - **主流程为 `get_market_data(persist=True)`**；`import_data` 仅用于粘贴外部数据/CSV。
  - `data` 支持各种 LLM 错误包裹（单键 dict、JSON 字符串）的容错。
  - 记录需含 `trade_date`/`date` + OHLCV；缺 close 列会报错。
- **后续引导**：导入后可 `run_backtest` 或 `factor_analysis`。

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

### 工作流 A：数据获取 → 回测（推荐，一步入库）

```
get_market_data(codes=['600519.SH','000858.SZ'], start_date='2023-01-01',
                end_date='2024-12-31', persist=True, strategy_name='mom')
  └─ fetch + 写入 DuckDB 一步完成，返回 summary/preview（不进 prompt）
run_backtest(strategy_name='mom')
```

> 已合并 `commit_market_data`：`get_market_data` 的 `persist=True`（默认）直接把行情写入 DuckDB，无需二次调用。

### 工作流 B：因子研究四连

```
factor_cross_sectional_analysis(factor_code='ts_mean(close,20)/ts_mean(close,60)-1', universe='all')
  └─ 截面 IC
factor_quintile_returns(factor_code=..., universe='all')
  └─ 分组单调性
factor_ic_decay(factor_code=..., horizons='1,5,10,20,60')
  └─ 衰减
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
8. **`import_data` 已降级**：主流程为 `get_market_data(persist=True)` 一步入库；`import_data` 仅用于手动/外部数据。

---

## 工具后续引导策略（next_step 取舍）

> 本节记录业界调研结论与本项目的最终决策。

### 业界调研结论

**Anthropic《Building Effective Agents》(2024-12)**：

- 工具定义即 **ACI**（Agent-Computer Interface）——应投入与 HCI 同等的精力打磨；引导内容推荐放在 **tool description**（何时用、示例、边界、与其它工具的区分），而不是返回值。
- **返回值应是干净的 ground truth 数据**；不建议夹带指令性文本。
- 流程编排由 **workflow（预定义代码路径）或 system prompt** 承担，不由工具返回值承担。
- 错误恢复给 retry hint（`fix` 字段）是普遍实践——本项目 `err_actionable` 的 `received/expected/fix` 即此范式。

**业界横向对比**：

| 系统 | 是否有"下一步"提示 | 形式 |
|------|-------------------|------|
| OpenAI Function Calling | ❌ 无 next_step 字段 | 引导在 function description + system prompt |
| Anthropic Tool Use | ❌ 无 | description 里写 "use after X"；返回值纯净 |
| LangChain / LangGraph | ❌ 无 | 流程用 agent/harness 编排 |
| GitHub Copilot 等代码 agent | ❌ 工具不返回建议 | planner 单独决策 |
| **API 分页/游标**（GitHub cursor 等） | ✅ 有 `next_cursor` | 唯一被业界认可的"返回值带下一步"范式——且**仅在状态依赖**时存在 |

### 最终决策

**所有 `next_step` 字段已移除。** 通用"下一步建议"（run_backtest→list_history、因子四连互相指向、import_data→run_backtest 等）不符合业界惯例且跨角色不可达，全部删除。

**唯一的契约式场景通过合并解决**：`get_market_data` 原本需要"先返回摘要、再 `commit_market_data` 入库"的两步契约。经评审，将该两步**合并为一步**——`get_market_data(persist=True)` 直接写入 DuckDB 并返回摘要，`commit_market_data` 工具退役。由此消除了唯一需要"返回值带下一步"的场景，与业界"返回值纯净"的 ACI 惯例对齐。

**引导归属**：
- 工具 description 写明 `何时用` / `边界` / `persist` 语义（ACI 范式）。
- 工作流（数据流、因子研究顺序）由 `chat.md` / `SYSTEM_PROMPT_HEADER` / skill 文档承担。
- 返回值保持纯净数据（summary + preview），不含指令性文本。
