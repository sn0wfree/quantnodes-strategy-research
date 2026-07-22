"""集成测试 — Hook + Memory + Session 完整流程。"""

import pytest
import time
import tempfile
from pathlib import Path
from strategy_research.core.hooks import AgentHook, CompositeHook, AgentHookContext
from strategy_research.core.memory import PersistentMemory, MemoryFTS5
from strategy_research.core.session import SessionDB, SessionManager, RateLimiter, MetricsLogger


class TestHookMemoryIntegration:
    """Hook + Memory 集成测试。"""

    def test_hook_with_memory(self, tmp_path):
        """测试 Hook 可以访问 Memory。"""
        memory = PersistentMemory(tmp_path / "memory")
        memory.add("test", "Test content", "project", "Test memory")

        class MemoryHook(AgentHook):
            name = "memory"
            def __init__(self, mem):
                self.memory = mem
            def after_iteration(self, ctx):
                # Hook 可以读取 memory
                results = self.memory.find_relevant("test")
                assert len(results) > 0

        hook = CompositeHook([MemoryHook(memory)])
        ctx = AgentHookContext(iteration=1)
        # 需要异步调用
        import asyncio
        asyncio.run(hook.after_iteration(ctx))

    def test_memory_format_context(self, tmp_path):
        """测试 Memory format_context_for_prompt。"""
        memory = PersistentMemory(tmp_path / "memory")
        memory.add("momentum", "Momentum factor works", "feedback", "Momentum")
        memory.add("value", "Value factor underperforms", "feedback", "Value")

        context = memory.format_context_for_prompt("momentum")
        assert "<recalled-memories>" in context
        assert "momentum" in context


class TestSessionDBIntegration:
    """SessionDB 集成测试。"""

    def test_full_session_workflow(self, tmp_path):
        """测试完整会话工作流。"""
        db = SessionDB(tmp_path / "test.db")
        
        # 创建会话
        session = db.create_session("s1", workspace="/tmp/ws")
        assert session.id == "s1"

        # 添加消息
        db.add_message("s1", "user", "Hello, I want to analyze momentum")
        db.add_message("s1", "assistant", "I'll help you analyze the momentum factor")
        db.add_message("s1", "user", "What's the IC mean?")

        # 获取消息
        messages = db.get_messages("s1")
        assert len(messages) == 3
        assert messages[0].role == "user"
        assert messages[1].role == "assistant"

        # 搜索消息
        results = db.search_messages("momentum")
        assert len(results) >= 1

        # 删除会话
        result = db.delete_session("s1")
        assert result is True
        assert db.get_session("s1") is None

    def test_batch_insert_with_rate_limit(self, tmp_path):
        """测试批量插入 + 限流。"""
        limiter = RateLimiter(max_per_second=1000)
        metrics = MetricsLogger()
        db = SessionDB(tmp_path / "test.db", rate_limiter=limiter, metrics_logger=metrics)
        
        db.create_session("s1")
        
        messages = [
            {"role": "user", "content": f"Message {i}"}
            for i in range(100)
        ]
        
        count = db.add_message_batch("s1", messages)
        assert count == 100
        
        # 验证统计
        stats = metrics.get_stats()
        assert stats["total_writes"] == 1
        assert stats["total_messages"] == 100

    def test_rate_limiter_config(self, tmp_path):
        """测试限流器配置。"""
        limiter = RateLimiter(max_per_second=500)
        db = SessionDB(tmp_path / "test.db", rate_limiter=limiter)
        
        assert db.rate_limiter.max_per_second == 500

    def test_metrics_jsonl(self, tmp_path):
        """测试 JSONL 日志。"""
        log_path = tmp_path / "metrics.jsonl"
        metrics = MetricsLogger(log_path=log_path)
        db = SessionDB(tmp_path / "test.db", metrics_logger=metrics)
        
        db.create_session("s1")
        db.add_message("s1", "user", "test message")
        
        assert log_path.exists()
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1


class TestEndToEnd:
    """端到端测试。"""

    def test_complete_workflow(self, tmp_path):
        """测试完整工作流：创建 → 消息 → 搜索 → 归档。"""
        # 1. 创建会话
        db = SessionDB(tmp_path / "sessions.db")
        session = db.create_session("test_session", workspace=str(tmp_path))
        
        # 2. 添加消息
        db.add_message("test_session", "user", "I want to backtest momentum strategy")
        db.add_message("test_session", "assistant", "Running backtest for momentum...")
        db.add_message("test_session", "user", "What's the Sharpe ratio?")
        db.add_message("test_session", "assistant", "Sharpe ratio is 0.85")
        
        # 3. 搜索
        results = db.search_messages("momentum")
        assert len(results) >= 1
        
        results = db.search_messages("Sharpe")
        assert len(results) >= 1
        
        # 4. 获取统计
        stats = db.metrics_logger.get_stats()
        assert stats["total_writes"] == 4
        assert stats["total_messages"] == 4
        
        # 5. 列出会话
        sessions = db.list_sessions()
        assert len(sessions) == 1
        
        # 6. 删除
        db.delete_session("test_session")
        assert db.get_session("test_session") is None

    def test_memory_and_session_together(self, tmp_path):
        """测试 Memory + Session 联合使用。"""
        # Memory
        memory = PersistentMemory(tmp_path / "memory")
        memory.add("research", "Momentum works well in large caps", "feedback", "Momentum")
        
        # Session
        db = SessionDB(tmp_path / "sessions.db")
        db.create_session("s1")
        db.add_message("s1", "user", "Analyze momentum strategy")
        db.add_message("s1", "assistant", "Based on previous research, momentum works well in large caps")
        
        # 联合搜索
        memory_results = memory.find_relevant("momentum")
        session_results = db.search_messages("momentum")
        
        assert len(memory_results) >= 1
        assert len(session_results) >= 1

    def test_performance_under_load(self, tmp_path):
        """测试高负载性能。"""
        limiter = RateLimiter(max_per_second=10000)
        metrics = MetricsLogger()
        db = SessionDB(tmp_path / "test.db", rate_limiter=limiter, metrics_logger=metrics)
        
        db.create_session("s1")
        
        start = time.time()
        for i in range(1000):
            db.add_message("s1", "user", f"Message {i} with some content")
        elapsed = time.time() - start
        
        stats = metrics.get_stats()
        assert stats["total_writes"] == 1000
        assert stats["success_rate"] == 1.0
        print(f"\n1000 messages: {elapsed:.3f}s ({1000/elapsed:.0f} msg/sec)")
