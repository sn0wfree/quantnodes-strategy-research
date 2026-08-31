"""Archived from tests/test_webui_visual.py — dead tests, kept for reference.

Every test below was @pytest.mark.skip'd because the code under test
was removed in the P4/P8/Phase-A cleanups (see each skip reason).
Not collected: tests/conftest.py sets collect_ignore_glob=["attic/*"].
"""

import pytest  # noqa: F401 — retained from the archived sources


@pytest.mark.skip(
    reason="DAG 页已改版为定义管理页（DefinitionWorkflowPage），旧 __workflowStore "
           "dagNodes 注入不再被消费；DAG 视觉由 test_webui_catalog 的 catalog_dag-* 覆盖"
)
class TestDAGVisualization:
    """DAG 可视化 — React Flow 节点 + 边。

    验证 5 种场景:
    - dag_visualization: 默认混合状态 (backward compat baseline)
    - dag_all_pending: 全部 pending
    - dag_mid_running: 部分 running + completed + pending
    - dag_all_completed: 全部完成 (progress=100%)
    - dag_has_failed: 1 个 failed + 其他 completed/pending
    """

    DAG_STATES = {
        "all_pending": {
            "dagNodes": [
                {"id": "plan", "label": "Plan Research", "status": "pending"},
                {"id": "data", "label": "Load Market Data", "status": "pending"},
                {"id": "alpha", "label": "Compute Alpha", "status": "pending"},
                {"id": "report", "label": "Generate Report", "status": "pending"},
            ],
            "dagEdges": [
                {"id": "e1", "source": "plan", "target": "data"},
                {"id": "e2", "source": "data", "target": "alpha"},
                {"id": "e3", "source": "alpha", "target": "report"},
            ],
            "executionProgress": 0.0,
        },
        "mid_running": {
            "dagNodes": [
                {"id": "plan", "label": "Plan Research", "status": "completed"},
                {"id": "data", "label": "Load Market Data", "status": "completed"},
                {"id": "alpha", "label": "Compute Alpha", "status": "running"},
                {"id": "report", "label": "Generate Report", "status": "pending"},
            ],
            "dagEdges": [
                {"id": "e1", "source": "plan", "target": "data"},
                {"id": "e2", "source": "data", "target": "alpha"},
                {"id": "e3", "source": "alpha", "target": "report"},
            ],
            "executionProgress": 0.5,
        },
        "all_completed": {
            "dagNodes": [
                {"id": "plan", "label": "Plan Research", "status": "completed"},
                {"id": "data", "label": "Load Market Data", "status": "completed"},
                {"id": "alpha", "label": "Compute Alpha", "status": "completed"},
                {"id": "report", "label": "Generate Report", "status": "completed"},
            ],
            "dagEdges": [
                {"id": "e1", "source": "plan", "target": "data"},
                {"id": "e2", "source": "data", "target": "alpha"},
                {"id": "e3", "source": "alpha", "target": "report"},
            ],
            "executionProgress": 1.0,
        },
        "has_failed": {
            "dagNodes": [
                {"id": "plan", "label": "Plan Research", "status": "completed"},
                {"id": "data", "label": "Load Market Data", "status": "failed"},
                {"id": "alpha", "label": "Compute Alpha", "status": "pending"},
                {"id": "report", "label": "Generate Report", "status": "pending"},
            ],
            "dagEdges": [
                {"id": "e1", "source": "plan", "target": "data"},
                {"id": "e2", "source": "data", "target": "alpha"},
                {"id": "e3", "source": "alpha", "target": "report"},
            ],
            "executionProgress": 0.25,
        },
    }

    @pytest.fixture
    def dag_page(self, context: BrowserContext, app_url: str) -> Iterator[Page]:
        """登录后注入空 DAG store — 各 test 自己再注入 state。"""
        page = context.new_page()
        try:
            import uuid as _uuid

            import requests

            api = requests.Session()
            api.base_url = app_url  # type: ignore[attr-defined]

            username = f"dag_{_uuid.uuid4().hex[:8]}"
            r = api.post(
                f"{api.base_url}/api/auth/register",
                json={"username": username, "password": "p"},
            )
            token = r.json()["access_token"]

            page.goto(f"{app_url}/login")
            page.wait_for_selector("input[type='password']", timeout=10_000)
            page.evaluate(
                """({token}) => {
                    localStorage.setItem('sr-auth', JSON.stringify({
                        state: { token, user: { username: 'dag' } }, version: 0
                    }));
                }""",
                {"token": token},
            )
            # DAG 页路由是 /dag
            page.goto(f"{app_url}/dag")
            page.wait_for_selector("header", timeout=10_000)
            page.wait_for_function(
                "() => typeof window.__workflowStore !== 'undefined'", timeout=5000
            )

            # 等 React Flow 容器出现 — 必须先注入至少 1 个节点
            # (React Flow v12 在 nodes=[] 时会卸载容器元素)
            page.evaluate(
                """() => {
                    window.__workflowStore.setState({
                        dagNodes: [{ id: '__bootstrap', label: 'Bootstrap', status: 'pending' }],
                        dagEdges: [],
                        executionProgress: 0.0,
                    });
                }"""
            )
            page.wait_for_selector(".react-flow", timeout=10_000)
            page.wait_for_timeout(500)

            yield page
        finally:
            page.close()

    def test_dag_visualization_baseline(self, dag_page: Page):
        """默认 DAG 视图 — 混合状态 (backward compat baseline)。"""
        # 注入 baseline 默认 mixed 状态
        dag_page.evaluate(
            """() => {
                window.__workflowStore.setState({
                    dagNodes: [
                        { id: 'plan', label: 'Plan Research', status: 'completed' },
                        { id: 'data', label: 'Load Market Data', status: 'completed' },
                        { id: 'alpha', label: 'Compute Alpha', status: 'running' },
                        { id: 'report', label: 'Generate Report', status: 'pending' },
                    ],
                    dagEdges: [
                        { id: 'e1', source: 'plan', target: 'data' },
                        { id: 'e2', source: 'data', target: 'alpha' },
                        { id: 'e3', source: 'alpha', target: 'report' },
                    ],
                    executionProgress: 0.5,
                });
            }"""
        )

        try:
            dag_page.wait_for_selector(".react-flow__node", timeout=10_000)
        except Exception:
            pytest.skip("React Flow nodes not rendered")
            return

        # 等 fitView 布局动画完成
        dag_page.wait_for_timeout(800)

        react_flow = dag_page.locator(".react-flow").first
        actual = _screenshot_element(dag_page, react_flow, "dag_visualization")
        _assert_or_update(actual, "dag_visualization", max_diff_ratio=0.02)

    @pytest.mark.parametrize("state_name", list(DAG_STATES.keys()))
    def test_dag_state_snapshots(self, dag_page: Page, state_name: str):
        """DAG 多状态快照 — 验证 4 种节点组合的视觉表现。

        4 种状态:
        - all_pending: 全部 pending (灰色边框, 空进度条)
        - mid_running: 部分 running (蓝色脉冲图标) + completed (绿色 check)
        - all_completed: 全部完成 (绿色, 100% 进度条)
        - has_failed: 1 个 failed (红色 X 图标) + 其他 completed/pending
        """
        config = self.DAG_STATES[state_name]

        # 注入对应状态
        dag_page.evaluate(
            """(config) => {
                window.__workflowStore.setState({
                    dagNodes: config.dagNodes,
                    dagEdges: config.dagEdges,
                    executionProgress: config.executionProgress,
                });
            }""",
            config,
        )

        # 等 React Flow 重新布局
        try:
            dag_page.wait_for_selector(".react-flow__node", timeout=10_000)
        except Exception:
            pytest.skip(f"React Flow nodes not rendered for {state_name}")
            return
        dag_page.wait_for_timeout(1000)

        react_flow = dag_page.locator(".react-flow").first
        if react_flow.count() == 0:
            pytest.skip(f"React Flow not rendered for {state_name}")

        actual = _screenshot_element(dag_page, react_flow, f"dag_{state_name}")
        _assert_or_update(actual, f"dag_{state_name}", max_diff_ratio=0.02)
