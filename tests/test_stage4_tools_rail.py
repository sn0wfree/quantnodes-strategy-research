"""Tests for Stage 4 - ToolsRail unified timeline."""
from __future__ import annotations

from strategy_research.cli.tui.widgets.tools_rail import (
    ToolsRail,
    TimelineEntry,
)


class TestTimelineEntry:
    def test_default_fields(self):
        e = TimelineEntry(kind="tool", label="read_file", status="running")
        assert e.duration_ms is None
        assert e.detail == ""
        assert e.iter is None


class TestToolsRailHandleEvent:
    def _rail(self) -> ToolsRail:
        return ToolsRail()

    def test_tool_call_adds_running_entry(self):
        rail = self._rail()
        rail.handle_event("tool_call", {"tool": "read_file", "iter": 1})
        assert len(rail._timeline) == 1
        assert rail._timeline[0].label == "read_file"
        assert rail._timeline[0].status == "running"
        assert rail._timeline[0].iter == 1

    def test_tool_result_updates_to_done(self):
        rail = self._rail()
        rail.handle_event("tool_call", {"tool": "read_file"})
        rail.handle_event("tool_result", {"tool": "read_file", "status": "ok", "elapsed_ms": 500})
        assert rail._timeline[0].status == "done"
        assert rail._timeline[0].duration_ms == 500

    def test_tool_result_error_status(self):
        rail = self._rail()
        rail.handle_event("tool_call", {"tool": "search"})
        rail.handle_event("tool_result", {"tool": "search", "status": "error", "elapsed_ms": 100})
        assert rail._timeline[0].status == "error"

    def test_tool_result_backward_compat_ok(self):
        rail = self._rail()
        rail.handle_event("tool_call", {"tool": "search"})
        rail.handle_event("tool_result", {"tool": "search", "ok": True, "elapsed_ms": 200})
        assert rail._timeline[0].status == "done"

    def test_tool_progress_updates_detail(self):
        rail = self._rail()
        rail.handle_event("tool_call", {"tool": "download"})
        rail.handle_event("tool_progress", {"tool": "download", "message": "50% done"})
        assert rail._timeline[0].detail == "50% done"

    def test_tool_heartbeat_updates_detail(self):
        rail = self._rail()
        rail.handle_event("tool_call", {"tool": "analyze"})
        rail.handle_event("tool_heartbeat", {"tool": "analyze", "detail": "still working"})
        assert rail._timeline[0].detail == "still working"

    def test_compact_adds_entry(self):
        rail = self._rail()
        rail.handle_event("compact", {
            "layer": "microcompact",
            "iteration": 1,
            "before_tokens": 12000,
            "after_tokens": 4000,
        })
        assert len(rail._timeline) == 1
        assert rail._timeline[0].kind == "compact"
        assert "compacted" in rail._timeline[0].label

    def test_iter_start_updates_iter_counter(self):
        rail = self._rail()
        rail.handle_event("iter_start", {"iteration": 3, "max_iterations": 10})
        assert rail._iter == 3
        assert rail._iter_max == 10

    def test_timeline_trimmed_to_max(self):
        rail = self._rail()
        rail._max_timeline = 3
        for i in range(5):
            rail.handle_event("tool_call", {"tool": f"t{i}"})
        assert len(rail._timeline) == 3
        assert rail._timeline[0].label == "t2"
        assert rail._timeline[-1].label == "t4"

    def test_clear_timeline(self):
        rail = self._rail()
        rail.handle_event("tool_call", {"tool": "x"})
        rail.clear_timeline()
        assert len(rail._timeline) == 0


class TestToolsRailRender:
    def _rail(self) -> ToolsRail:
        return ToolsRail()

    def test_render_running_entry_shows_ellipsis(self):
        rail = self._rail()
        rail.handle_event("tool_call", {"tool": "backtest"})
        content = rail._render_timeline()
        assert "backtest" in content
        assert "..." in content

    def test_render_done_entry_shows_duration(self):
        rail = self._rail()
        rail.handle_event("tool_call", {"tool": "backtest"})
        rail.handle_event("tool_result", {"tool": "backtest", "status": "ok", "elapsed_ms": 5200})
        content = rail._render_timeline()
        assert "5.2s" in content

    def test_render_detail_shown_as_sub_line(self):
        rail = self._rail()
        rail.handle_event("tool_call", {"tool": "download"})
        rail.handle_event("tool_progress", {"tool": "download", "message": "fetching"})
        content = rail._render_timeline()
        assert "└" in content
        assert "fetching" in content

    def test_render_empty_timeline_shows_idle(self):
        rail = self._rail()
        content = rail._render_timeline()
        assert "(idle)" in content

    def test_render_footer_shows_iter(self):
        rail = self._rail()
        rail.set_iter(3, 10)
        content = rail._render_footer()
        assert "iter 3/10" in content
