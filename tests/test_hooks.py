import pytest
from pathlib import Path
from strategy_research.core.hooks import (
    AgentHook,
    AgentHookAdapter,
    AgentHookContext,
    CompositeHook,
    NoOpHook,
    UnifiedHook,
    maybe_await,
)


class TestMaybeAwait:
    @pytest.mark.asyncio
    async def test_sync_function(self):
        def sync_fn(x: int) -> int:
            return x * 2
        result = await maybe_await(sync_fn, 5)
        assert result == 10

    @pytest.mark.asyncio
    async def test_async_function(self):
        async def async_fn(x: int) -> int:
            return x * 3
        result = await maybe_await(async_fn, 5)
        assert result == 15

    @pytest.mark.asyncio
    async def test_value(self):
        result = await maybe_await(42)
        assert result == 42


class TestAgentHookContext:
    def test_defaults(self):
        ctx = AgentHookContext()
        assert ctx.iteration == 0
        assert ctx.messages == []
        assert ctx.response is None
        assert ctx.usage == {}
        assert ctx.tool_calls == []
        assert ctx.error is None
        assert ctx.cancelled is False

    def test_mutable(self):
        ctx = AgentHookContext()
        ctx.iteration = 5
        ctx.messages.append({"role": "user", "content": "test"})
        assert ctx.iteration == 5
        assert len(ctx.messages) == 1


class TestNoOpHook:
    def test_name(self):
        hook = NoOpHook()
        assert hook.name == "noop"

    def test_wants_streaming(self):
        hook = NoOpHook()
        assert hook.wants_streaming() is False

    def test_before_iteration(self):
        hook = NoOpHook()
        ctx = AgentHookContext()
        hook.before_iteration(ctx)  # Should not raise

    def test_finalize_content(self):
        hook = NoOpHook()
        ctx = AgentHookContext()
        result = hook.finalize_content(ctx, "test")
        assert result == "test"


class TestCompositeHook:
    def test_empty(self):
        hook = CompositeHook()
        assert len(hook) == 0

    def test_add_remove(self):
        hook = CompositeHook()
        noop = NoOpHook()
        hook.add(noop)
        assert len(hook) == 1
        hook.remove("noop")
        assert len(hook) == 0

    def test_clear(self):
        hook = CompositeHook()
        hook.add(NoOpHook())
        hook.add(NoOpHook())
        hook.clear()
        assert len(hook) == 0

    def test_wants_streaming(self):
        hook = CompositeHook()
        assert hook.wants_streaming() is False
        hook.add(NoOpHook())
        assert hook.wants_streaming() is False

    @pytest.mark.asyncio
    async def test_before_iteration(self):
        hook = CompositeHook()
        hook.add(NoOpHook())
        ctx = AgentHookContext()
        await hook.before_iteration(ctx)  # Should not raise

    @pytest.mark.asyncio
    async def test_error_isolation(self):
        class FailingHook(AgentHook):
            name = "failing"
            def before_iteration(self, ctx):
                raise RuntimeError("hook failed")

        hook = CompositeHook()
        hook.add(FailingHook())
        hook.add(NoOpHook())
        ctx = AgentHookContext()
        await hook.before_iteration(ctx)  # Should not raise


class TestAgentHook:
    def test_base_name(self):
        hook = AgentHook()
        assert hook.name == "base"

    def test_wants_streaming(self):
        hook = AgentHook()
        assert hook.wants_streaming() is False

    def test_finalize_content(self):
        hook = AgentHook()
        ctx = AgentHookContext()
        result = hook.finalize_content(ctx, "test")
        assert result == "test"


class TestUnifiedHook:
    def test_wants_streaming(self):
        hook = UnifiedHook()
        assert hook.wants_streaming() is False

    def test_finalize(self):
        hook = UnifiedHook()
        result = hook.finalize(None, "test")
        assert result == "test"


class TestAgentHookAdapter:
    def test_wants_streaming(self):
        adapter = AgentHookAdapter(NoOpHook())
        assert adapter.wants_streaming() is False

    def test_finalize(self):
        adapter = AgentHookAdapter(NoOpHook())
        result = adapter.finalize(None, "test")
        assert result == "test"

    def test_none_hook(self):
        adapter = AgentHookAdapter(None)
        assert adapter.wants_streaming() is False
