# tests/attic — 已归档的死测试

此处存放引用**已删除代码**的测试，仅作历史参考。**不会被 pytest 收集**
（`tests/conftest.py` 设置了 `collect_ignore_glob = ["attic/*"]`）。

每个文件开头 docstring 标注来源与死因（skip reason）。归档日期：2026-08-31。

| 文件 | 死因 |
|---|---|
| `attic_test_workflow_e2e.py` | WorkflowController/ControllerConfig/PromptBuilder 已删（P4/P8 cleanup） |
| `attic_test_swarm.py` | WorkflowController 不再驱动 `_execute_agent`（P4 unified） |
| `attic_test_v05_unit_models.py` | `_AgentConfigExecutor` 已从 goal.workflow 删除（P8 cleanup） |
| `attic_test_goal_workflow_phase3.py` | Mock controller / WorkflowController 已删（P4/P8 cleanup） |
| `attic_test_goal_workflow_v053.py` | `_build_controller` 已删（P8）、PromptBuilder 不再使用（P4） |
| `attic_test_compact_full_pipeline.py` | L1/L3 压缩层已在 Phase A 移除（行为由 test_compact_opencode_style.py 的 L4-only 测试覆盖） |
| `attic_test_agent_loop_extensions.py` | `_smart_microcompact`/`_hard_truncate` 已在 Phase A 移除 |
| `attic_test_webui_visual.py` | DAG 页改版为 DefinitionWorkflowPage，旧 `__workflowStore` 注入不再被消费 |

注：`_archive_tool.py` 是一次性归档脚本，保留供复查提取逻辑。
