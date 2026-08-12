# Study v2 M0 预研事实清单

> 依据：`docs/study-longhorizon-v2-design.md`（v2 权威设计）
> 目的：M1-M3 实施前的事实基准。所有行号为调研时点（2026-08）实测。

## 1. ToolContext workspace 注入链（M2 工具参数化依据）

### 1.1 完整链路（实测）

```
study 创建（api/routers/study.py:163-165）
  └─ mkdir strategies/<name> + _create_minimal_strategy（PARAMS/FACTOR_EXPRS/
     FACTOR_WEIGHT_METHOD 最小模板，study.py:29-40）
runner._run_one_round（core/study/runner.py:300-432）
  └─ run_researcher_phase / run_execution_phase / run_evaluation_phase
       （autoresearch.py:1754/1823/…）——三阶段均显式传 run_dir ✅
  └─ _make_spawn_fn(path, strategy_name, state, ...)（autoresearch.py:1813）
  └─ run_agent_via_llm(role, workspace_path, strategy_name, ...)
       （role_factory.py:139-190）
  └─ build_agent_loop(workspace_path=...)（role_factory.py:83-136）
  └─ AgentLoop(workspace=workspace_path, ...)
  └─ loop.py:1113-1141（工具调用前注入）：
       kwargs["workspace"] = self.workspace        # legacy 参数
       kwargs["ctx"] = ToolContext(workspace=self.workspace, session_id=…, …)
  └─ tool.invoke(kwargs) → WriteFileTool.execute(ctx, path, content)
  └─ builtin_tools/__init__.py:394
       wl = PathWhitelist(workspace=workspace)      # ★写死 DEFAULT_WRITE_ROOTS
```

### 1.2 结论

- workspace 注入链**通畅**（AgentLoop → ToolContext → 工具 execute），无需改动
- `PathWhitelist.__init__` 已支持 `write_roots` / `read_roots` 参数
  （sandbox.py:166-174），默认 `strategies/templates/memory/logs`
  （sandbox.py:137-142）与 `…+data/docs/.`（L144-152）
- **唯一接线缺口**：WriteFileTool.execute（builtin_tools/__init__.py:394）
  写死默认 roots——study 场景需让 write_file 允许 `study/` 根。接线候选：
  (a) ToolContext 增加 `write_roots` 字段（loop.py:1132 构造处透传，build_
  agent_loop/run_agent_via_llm 加参数）——链路长但清晰，M2 采用
  (b) 全局/环境注入——不采用（隐式状态）

## 2. create_session 幂等性（M1 微 session 依据）

- `SessionService.create_session`（api/session/service.py:84-112）：**幂等**——
  `SELECT id FROM sessions` 存在则跳过 INSERT，直接返回元数据（L95-105）
- `POST /api/sessions` 端点（web_session.py:830）→ service.create_session
- **dag 模式已有样板**（workflow.py:383-389）：
  `session_id = f"dag:{dag_id}"` → `create_session(WebSessionCreate(title=…, id=session_id), request)`
  `WebSessionCreate.id` 注释明示支持显式 id（web_session.py:35）
- **结论**：M1 直接复用 `f"study:{id}"` + 同一调用模式即可，幂等已保证

## 3. 回测链内部路径逐点（M2 依据）

### 3.1 run_backtest_script（core/backtest.py:257-372）——脚本模式

| 动作 | 位置 | 路径来源 | M2 改造 |
|---|---|---|---|
| strategy_dir 解析 | L275 | `ws/strategies/<name>` **硬编码** | 参数化（study 传 run 目录所在轮/执行目录） |
| run_dir（已显式） | L263/282-283 | 传参；run_name 取 `run_dir.name` | ✅ 无需改 |
| save_run_snapshot | L285→135-146 | src=`strategy_dir/strategy.py`+config.yaml → run_dir | 随 strategy_dir 参数化 |
| run_strategy 执行 | L287→226-254 | 执行 `strategy_dir/strategy.py`，**cwd=strategy_dir**（L239） | ★cwd 语义需验证（见 §5） |
| factor_failures | L291-306 | run_dir + strategy_dir（后读先删） | 随参数化 |
| run.log | L308-309 | `run_dir/run.log` | ✅ 已参数化 |
| parse_run_log → metrics | L311 | run.log | ✅ |
| git_get_hash | L313 | workspace_path | ✅ 不变 |
| metrics.json | L324→166-169 | `run_dir/metrics.json`（NaN→null） | ✅ |
| results.tsv 落行 | L325→172-207 | `strategy_dir/runs/results.tsv` | ★见 §3.3 |
| DuckDB save_backtest_result | L327-343 | strategy_name 为键 | study 用标签名即可 ✅ |
| write_run_card | L346-364 | run_dir（run_card.json/md，含 strategy_paths=run_dir 内） | ✅ |

### 3.2 run_backtest_from_yaml（core/backtest.py:379+）——YAML 模式

| 动作 | 位置 | 路径来源 | 改造 |
|---|---|---|---|
| yaml_path 默认 | L388 | `ws/strategies/<name>/config.yaml` 硬编码 | 参数化 |
| strategy_dir | L400-404 | 硬编码（create_run_dir/snapshot） | 参数化 |
| results.tsv | L433 | strategy_dir/runs | ★同 §3.3 |
| DuckDB + weight/nav history | L436-456 | strategy_name 键 | ✅ |
| equity_curve.csv | L458+ | strategy_dir 派生（待 M2 逐行确认） | 随参数化 |

### 3.3 results.tsv 双写入点与 round 列

- **append 模式**：backtest.py `update_results_tsv`（L172-207）——13 列表头
  `run/commit/action/calmar/sharpe/max_dd/ann_return/turnover/factors_added/
  factors_removed/params_changed/status/description`（L182-184），无 round 列；
  `status` 在 index **11**（description 12）
- **原位更新**：runner.py `_update_results_tsv`（L520-533）——按 run_name
  前缀匹配行，`parts[11] = verdict`（len≥12 检查）
- **读取**：read_current_state（autoresearch.py:142-176）——`best_calmar =
  float(parts[3])`（calmar 列索引 **3**）；`total_runs = 行数-1`；`recent_runs`
  取最后 10 行
- **★round 列位置建议**：**尾部追加**（index 13）——不破坏 parts[3]/parts[11]
  既有索引；CLI 旧行 round 列留空兼容；复合匹配键 `(parts[13], parts[0])`
- runner._update_results_tsv 需同时改：匹配键 + round 列（若 runner 侧仍
  原位更新 verdict）

### 3.4 工具层硬编码全清单（★设计文档 7.2 未覆盖的新发现）

strategist 白名单**含 run_backtest**（role_factory.py:41），agent 可在 ReAct
内直接调用，形成与主循环并行的第二条回测路径。以下工具内部硬编码
`strategies/<name>/`（**决策：全部参数化，非移除**，见 §6.1）：

| # | 位置 | 工具/函数 | 硬编码 |
|---|---|---|---|
| T1 | builtin_tools/__init__.py:589 | ConfigLoadStep | `ws/strategies/<name>/config.yaml` |
| T2 | builtin_tools/__init__.py:546-548 | RunBacktestTool artifacts | `runs/<name>/<run>/{equity_curve,metrics,run_card}` |
| T3 | builtin_tools/__init__.py:713 | EngineRunStep → run_backtest_from_yaml | strategy_name 派生全部路径 |
| T4 | builtin_tools/__init__.py:1118 | list_history | `strategies/<name>/runs/results.tsv` |
| T5 | builtin_tools/__init__.py:2530 | drawdown_analysis | `strategies/<name>/runs` |
| T6 | builtin_tools/__init__.py:2699 | benchmark_comparison | `strategies/<name>/runs` |
| T7 | data_tools.py:701 | check_data（source=config） | `strategies/<name>/config.yaml` |
| T8 | display_tools.py:324 | show_report | `ws/runs/<name>/<run>/report.html` |
| T9 | display_tools.py（show_chart） | show_chart | 读 run 产物（M2 确认） |

**统一参数化机制**：`ToolContext` 新增 `strategy_dir` / `runs_dir` /
`results_tsv` 三字段（Optional[Path]）→ 工具内回退：
`ctx.strategy_dir or workspace/strategies/<name>`（CLI 传 None 行为不变）。
注入链：run_agent_via_llm/build_agent_loop 加参数 → AgentLoop → loop.py:1132
构造 ToolContext → 工具读 ctx。

**主链对应改造**：run_backtest_script（backtest.py:275 的 strategy_dir 解析）
与 run_backtest_from_yaml（L388/400/433）均加 `strategy_dir`/`results_tsv`/
`runs_dir` 三参数（默认=旧拼接），EngineRunStep 透传。

### 3.5 read_current_state 双参数（M2）

- 现状：`strategy_dir = ws/strategies/<name>`（autoresearch.py:142）→
  strategy.py（L145）+ results.tsv（L149）
- M2：拆 `strategy_file` + `results_tsv` 双参数（默认=旧拼接）；调用者：
  runner.py:325（`read_current_state(path, strategy)`）与 CLI 路径

### 3.6 _create_run_dir（M2）

- runner.py:326 `_create_run_dir(path, strategy)` → autoresearch.py:1713 附近，
  从 `strategies/<name>/runs` 扫 run_NNNN 取 max+1；study 场景改为扫
  `rounds/round_N/` 取轮内 max+1（与设计 §8.1/§3.2 的「轮内独立编号 +
  恢复重跑 max+1」一致）

## 4. runner._run_one_round 现状（M3 三产物接入点）

- 主循环：`_run_loop`（runner.py:184-298）：cooldown L296、stagnation L264、
  max_rounds L270、AEGIS idle 早停 L276-291、预算 L257、达标 L247（`meets_
  metric_targets` 现仅按 metrics 判定——M3 改 E2 口径）、goal 闭环 L248 →
  `_complete_goal`（L486-515，append_evidence+complete_lite）
- 单轮：`_run_one_round`（runner.py:300-432）：read_current_state L325 →
  `_create_run_dir` L326 → 三阶段（L341/364/373 均传 run_dir）→ novelty 门
  （L351-361，aborted 轮）→ verdict L377 → `_update_results_tsv` L380 →
  `generate_run_summary`+`save_run_summary`（L392-394）→ attribution L396 →
  journal/regression/scoreboard（L401-423）
- **M3 接入点**：L325/L326 改为 study 目录参数化；L380 TSV 复合匹配；L392
  后接 manifest/summary.md/journal.md 三产物；journal 账本（L404-411）与
  文件级 journal.md 并存（设计 §8.5 E3）

## 5. 遗留待确认项（M2 开工时验证）

| # | 项 | 验证方法 |
|---|---|---|
| F1 | run_strategy `cwd=strategy_dir`（backtest.py:239）在 study 场景（执行目录=run_XXXX）下策略脚本相对路径引用是否仍正常 | M2 写 pytest：最小策略模板在自定义目录下跑通回测 |
| F2 | run_backtest_from_yaml 的 equity_curve.csv 导出（L458+）完整路径链 | M2 逐行确认 |
| F3 | 工具层 tools_override 移除清单对 read_file 的影响：read_roots 需含 `study`（读 runs 产物/guidance 等） | M2 接线时确认 PathWhitelist read_roots 注入点（同 §1.2(a)） |
| F4 | show_chart/show_report 移除后 display 事件是否影响前端（当前前端未实现 study 面板，低风险） | M8 前端阶段确认 |

## 6. 对设计文档的修正建议（M2 实施前已回写 ✅）

1. **§7.2 已补工具层段落**（决策=全部参数化 T1-T9，非移除）：ToolContext 三
   字段注入链 + 回退语义；M2 范围与风险表已同步更新
2. **§7.3 TSV 段已补**：round 列尾部追加（保护 parts[3]/parts[11] 索引）、
   backtest.py update_results_tsv（append）与 runner._update_results_tsv
   （原位）双点改造
3. **§6.2 初始策略模板内容**：PARAMS/FACTOR_EXPRS/FACTOR_WEIGHT_METHOD
   三字段最小模板（study.py:29-40 现有逻辑直接复用）——M3 实施时落实
4. **§7.2 backtest 行号**：strategy_dir 硬编码在 L275；run_backtest_from_yaml
   L388/400/433 三处——实施时对照

## 7. M1 预研结论（微 session + 并行，含变体 3 修订）

- `create_study`（store.py:228）生成 study_id 后回填 `studies.session_id =
  "study:{id}"`；`create_session(study:{id}, ...)` 幂等（§2）✅ 无阻塞
- **变体 3 定稿**（用户确认）：微 session 保留为执行身份 + 事件频道；新增
  `owner_session_id` 列仅作**归属查询**用途（`get_active_study` /
  `list_studies` 改按 owner_session_id 查询，v1「按会话查我的 study」语义
  保留）；账本写入经 goal `_require_mutable_goal` 解耦（删 session 匹配 +
  current 检查，保留 expected_goal_id/status 校验）后接受任何执行身份——
  runner 账本写入零改动，evidence 落库强制 goal.session_id（goal/store.py:790）
- 查询适配点：`study/store.py:489` get_active_study、`:503` list_studies 的
  `WHERE session_id` → `WHERE owner_session_id`
- scheduler 并行改造（设计 §5.2）：`_session_loop` 消费队列 create_task 不
  等待 + 全局 semaphore（SR_STUDY_MAX_CONCURRENT=3）；`_active_tasks` /
  `_dispatch_tasks` 登记；`mark_session_processing('study:{id}')` 调用保留
  （无实际互斥，service.py:78 按 session key 隔离）
- **测试影响**：`test_goal_account_usage.py:203`（wrong_session_id 期望拒绝）
  是唯一依赖 session 强校验的现有测试 → 更新为期望通过
