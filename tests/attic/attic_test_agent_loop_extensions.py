"""Archived from tests/test_agent_loop_extensions.py — dead tests, kept for reference.

Every test below was @pytest.mark.skip'd because the code under test
was removed in the P4/P8/Phase-A cleanups (see each skip reason).
Not collected: tests/conftest.py sets collect_ignore_glob=["attic/*"].
"""

import pytest  # noqa: F401 — retained from the archived sources


    @pytest.mark.skip(
        reason="L1 _smart_microcompact removed in Phase A; long tool outputs "
        "are now truncated by _serialize_message during L4 (covered in "
        "test_compact_serialization.py::test_tool_call_long_args_truncated)"
    )
    def test_long_tool_result_trimmed(self):
        """Tool results > microcompact_tool_result_limit chars should be trimmed."""
        mock = MockLLM([
            # Iteration 1: LLM decides to use tool
            tool_resp([ToolCall(id="c1", name="read", arguments={"path": "x"})]),
            # After tool result, LLM summarization may consume a response
            text_resp("Summary of read_file"),
            # Final response
            text_resp("done"),
        ])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategies").mkdir()
            (ws / "x").write_text("A" * 3000)
            loop = AgentLoop(
                stream_mode=False,
                config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry(),
                workspace=ws,
                threshold_tokens=100,
                no_progress_window=10,
            )
            loop.client.chat = mock.chat
            r = loop.run("x")
            # Should have triggered microcompact (L1)
            assert any("microcompact" in a for a in r.compression_applied)


    @pytest.mark.skip(
        reason="L3 _hard_truncate removed in Phase A; compression is L4-only."
    )
    def test_truncate_triggered_at_high_threshold(self):
        """With very low threshold, truncate should trigger."""
        mock = MockLLM([
            tool_resp([ToolCall(id=f"c{i}", name="read",
                                arguments={"path": f"file_{i}.py"})])
            for i in range(15)
        ] + [text_resp("Summary"), text_resp("done")])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategies").mkdir()
            loop = AgentLoop(
                stream_mode=False,
                config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry(),
                workspace=ws,
                threshold_tokens=30,  # extremely low
                no_progress_window=10,
            )
            loop.client.chat = mock.chat
            r = loop.run("loop")
            # truncate should have been applied
            assert any("truncate" in a for a in r.compression_applied)


    @pytest.mark.skip(
        reason="L4-only compression (Phase A) requires a user message in the "
        "recent turns; a single-user-message + tool-call-heavy conversation "
        "always hits the L4 safety abort. Compression triggering is covered "
        "in test_compact_error_propagation.py and test_compact_opencode_style.py."
    )
    def test_trace_records_compression(self, tmp_path):
        """Trace should record compression events."""
        mock = MockLLM([
            tool_resp([ToolCall(id=f"c{i}", name="read",
                                arguments={"path": f"file_{i}.py"})])
            for i in range(10)
        ] + [text_resp("Summary"), text_resp("done")])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategies").mkdir()
            trace_dir = ws / "trace"
            loop = AgentLoop(
                stream_mode=False,
                config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry(),
                workspace=ws,
                threshold_tokens=50,
                no_progress_window=10,
                trace_dir=trace_dir,
            )
            loop.client.chat = mock.chat
            r = loop.run("loop")
            lines = Path(r.trace_path).read_text().splitlines()
            events = [json.loads(l) for l in lines if l.strip()]
            compression_events = [e for e in events if e["type"] == "compression"]
            assert len(compression_events) > 0


    @pytest.mark.skip(
        reason="L4-only compression (Phase A) requires a user message in the "
        "recent turns; a single-user-message + tool-call-heavy conversation "
        "always hits the L4 safety abort. Compression triggering is covered "
        "in test_compact_error_propagation.py and test_compact_opencode_style.py."
    )
    def test_loop_with_memory_and_compression(self, tmp_path):
        """Loop with memory + aggressive compression."""
        # Pre-populate memory
        mem_writer = __import__(
            "strategy_research.core.memory.persistent",
            fromlist=["PersistentMemory"],
        ).PersistentMemory(memory_dir=tmp_path / "memory")
        mem_writer.add("note1", "body", description="obs")
        mem = __import__(
            "strategy_research.core.memory.persistent",
            fromlist=["PersistentMemory"],
        ).PersistentMemory(memory_dir=tmp_path / "memory")

        mock = MockLLM([
            tool_resp([ToolCall(id=f"c{i}", name="read",
                                arguments={"path": f"file_{i}.py"})])
            for i in range(8)
        ] + [text_resp("Summary"), text_resp("done")])

        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "strategies").mkdir()

        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            memory=mem,
            workspace=ws,
            threshold_tokens=50,  # aggressive compression
            no_progress_window=10,
        )
        loop.client.chat = mock.chat
        r = loop.run("improve based on memory")
        assert r.iterations >= 5  # compression may reduce iterations
        assert r.compression_applied  # compression should have triggered
