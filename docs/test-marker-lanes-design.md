# P1 设计：测试 Marker 三层化

> 来源：`docs/architecture-harness-recommendations-20261008.md` §2.2（P1）
> 问题：12,366 测试无语义分层——分类靠人记（`test_session_triggers.py` 硬编码在 shard 脚本、`test_chat_real_llm` 混在日常跑）、`--reruns` 全量兜底导致 flaky 信号被噪声淹没
> 范围：`pyproject.toml`（注册 markers）、`tests/conftest.py`（集中白名单打标）、`.github/workflows/tests.yml`（PR lane 过滤）
> 状态：设计定稿，待实施

## 1. 三层 Lane

| Lane | 触发 | 选择器 | 目标时长 |
|---|---|---|---|
| **PR gate** | 每个 PR / push | `-m "not llm and not e2e and not slow"` | < 5 min（按分片并行） |
| merge 后全量 | push main | 无过滤（含三类） | < 15 min |
| nightly | 定时 | `llm` 类 + 真实 eval | 不限 |

**默认安全原则**：只把**确定的**文件列入三类白名单；漏分类 = 多跑（无害），误分类 = 测试从 PR gate 消失（有害）。所以白名单**宁窄勿宽**，每条目在 §3 附理由。

## 2. 实现：集中白名单 + 自动打标

**不用** 544 个文件手工打标（工作量大且每加测试都要记得打），**用** `tests/conftest.py::pytest_collection_modifyitems` 按**文件名模式**集中匹配：

```python
# tests/conftest.py
LLM_FILES = {"test_chat_real_llm.py", "test_minimax_search.py", "test_web_search.py"}
E2E_FILES = {"test_chat_e2e_playwright.py", "test_e2e_chat.py", "test_sse_e2e.py",
             "test_goal_e2e.py", ...}
SLOW_FILES = {"test_dead_code_no_imports.py", "test_bg_proc.py", ...}  # 见 §3

def pytest_collection_modifyitems(items):
    for item in items:
        name = Path(str(item.fspath)).name
        if name in LLM_FILES:    item.add_marker(pytest.mark.llm)
        if name in E2E_FILES:    item.add_marker(pytest.mark.e2e)
        if name in SLOW_FILES:   item.add_marker(pytest.mark.slow)
```

`pyproject.toml` 注册三个 marker（消 PytestUnknownMarkWarning）：

```toml
markers = [
    "llm: needs a real LLM/network (excluded from PR gate)",
    "e2e: end-to-end across process/browser boundaries (excluded from PR gate)",
    "slow: >~5s wall time (excluded from PR gate; see .shard-timings.json)",
]
```

**优点**：分类一处可审、新测试默认进 PR gate、白名单变更走 PR review。

**已知限制**：文件级粒度（同文件内混合快慢测试时整文件归类）。现状白名单文件都是整文件性质明确的，暂不支持函数级（需要时用 `item.nodeid` 前缀匹配扩展）。

## 3. 白名单初版（每条附理由）

### `llm`（真 LLM / 真外网）

| 文件 | 理由 |
|---|---|
| `test_chat_real_llm.py` | 名称即事实：真 LLM 调用（18.4s） |
| `test_web_search.py` | DDGS 真外网查询 |
| `test_minimax_search.py` | 真 MiniMax search API |

### `e2e`（跨进程/浏览器/完整服务栈）

| 文件 | 理由 |
|---|---|
| `test_chat_e2e_playwright.py` | 浏览器驱动 |
| `test_e2e_chat.py` | 完整 chat 栈 |
| `test_sse_e2e.py` | SSE 端到端 |
| `test_goal_e2e.py` | goal 全链路（6.7s） |
| `test_study_e2e_api.py` / `test_study_e2e_integration.py` | study 全链路 |
| `test_p3_integration.py` / `test_integration.py` | 多子系统集成（15.4s，同时归 slow？——**归 e2e 即可**，标签互斥非必须，同文件可多标） |

### `slow`（timings ≥5s，来源 `.shard-timings.json` 实测）

| 文件 | 实测 |
|---|---|
| `test_dead_code_no_imports.py` | **75.2s**（全库最慢：AST 全仓扫描） |
| `test_bg_proc.py` | 19.3s |
| `test_backtest.py` | 16.9s |
| `test_decide_wired.py` | 16.3s |
| `test_cli_init.py` | 9.4s |
| `test_llm_retry.py` | 8.4s |
| `test_mcp_data_tools.py` | 7.2s |
| `test_backtest_metrics_contract.py` | 6.2s |
| `test_config_runner_import.py` | 6.0s |
| `test_data_readiness.py` | 5.8s |

（实施时按实测 ≥5s 完整扫一遍 timings 补齐，本列表为设计时 top。）

**不动**：`test_session_triggers.py` 的 shard 硬编码排除（那是 sharding 关注点，与语义 lane 正交；后续可改为 `slow` marker 后统一）。

## 4. CI 改动（`tests.yml`）

`unit` job 的 pytest 调用追加 marker 过滤：

```yaml
python -m pytest $(...) -q --tb=short --reruns=2 --reruns-delay=1 --durations=20 \
  -m "not llm and not e2e and not slow"
```

- 12 个分片 job 都加同一过滤 → PR gate 只跑 fast 集
- `eval-sim` job 不变
- **merge 后全量**：push main 触发同 workflow——为区分，加 job 条件？**简化**：第一版 PR 与 push 同过滤（主防反馈时长）；全量跑移到 nightly 已有 pytest 面（或后续加 `if: github.event_name == 'push'` 不过滤的 job）——**本文档记为 follow-up**，不阻塞第一版

## 5. 验收标准

| # | 项 | 通过条件 |
|---|---|---|
| 1 | marker 注册 | `pytest --markers` 列出 llm/e2e/slow，无 UnknownMark 警告 |
| 2 | 打标正确 | `pytest --collect-only -m llm`（e2e/slow 同）= 白名单文件集合 |
| 3 | PR 过滤有效 | `pytest -m "not llm and not e2e and not slow" --collect-only` 数量 < 12,366，且不含白名单 node |
| 4 | 分层数量 | 记录三类各多少 test（预期 llm ~几十、e2e ~百级、slow ~百级） |
| 5 | 过滤后可跑 | PR 选择器实跑一轮 0 failed（有旧 flaky 记录者除外，见 §3.3 A/B 判定流程） |
| 6 | 现有排除不回归 | `test_session_triggers` 行为不变 |

## 6. 后续（本文档不做）

- push main 的无过滤全量 job（§4 follow-up）
- 函数级打标（§2 限制）
- `slow` 与 `pytest_split`/sharding 的联动（替代 `DEFAULT_SKIP` 硬编码）
