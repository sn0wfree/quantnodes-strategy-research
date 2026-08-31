"""Archived from tests/test_compact_full_pipeline.py — dead tests, kept for reference.

Every test below was @pytest.mark.skip'd because the code under test
was removed in the P4/P8/Phase-A cleanups (see each skip reason).
Not collected: tests/conftest.py sets collect_ignore_glob=["attic/*"].
"""

import pytest  # noqa: F401 — retained from the archived sources


@pytest.mark.skip(reason="L1 layer removed in Phase A; A4 will replace these")
class TestCompactMessagesL1Only:
    def test_microcompact_applied(self):
        pass


@pytest.mark.skip(reason="L1 layer removed in Phase A; A4 will replace these")
class TestCompactMessagesForceAll:
    def test_force_zero_threshold(self):
        pass

    def test_force_mode_l1_runs(self):
        pass


@pytest.mark.skip(reason="L3 layer removed in Phase A; A4 will replace these")
class TestCompactMessagesL3:
    def test_hard_truncate_applied(self):
        pass

    def test_truncate_preserves_system(self):
        pass


@pytest.mark.skip(reason="L3 layer removed in Phase A; A4 will replace these")
class TestCompactMessagesDedup:
    def test_empty_short_summary_dedup(self):
        """Empty/short/whitespace summary → L4 result ignored."""
        llm = FakeLLM(responses=[""])
        cfg = CompactConfig(threshold_tokens=100, tail_turns=1, preserve_recent_tokens=500)
        msgs = _make_msgs(10, content_len=300)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=llm,
        )
        assert summary is None
        assert not any("llm_summarize" in layer for layer in applied)

    def test_whitespace_summary_dedup(self):
        llm = FakeLLM(responses=["  \n  \t  "])
        cfg = CompactConfig(threshold_tokens=100, tail_turns=1, preserve_recent_tokens=500)
        msgs = _make_msgs(10, content_len=300)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=llm,
        )
        assert summary is None

    def test_same_as_recent_text_dedup(self):
        """If summary text is the same as recent text, treat as dedup."""
        llm = FakeLLM(responses=["same as recent"])
        cfg = CompactConfig(threshold_tokens=100, tail_turns=1, preserve_recent_tokens=500)
        msgs = _make_msgs(10, content_len=300)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=llm,
        )
        # Even if summary matches recent text, it's still returned
        # (dedup detection is informational, not rejection)
        assert summary is not None or summary is None  # depends on implementation


@pytest.mark.skip(reason="L1 layer removed in Phase A; A4 will replace these")
class TestCompactMessagesFixToolPairs:
    def test_fix_tool_pairs_called(self):
        """After compaction, _fix_tool_pairs repairs orphans."""
        cfg = CompactConfig(
            threshold_tokens=50,
            hard_truncate_ratio=0.0,
            collapse_keep_recent=1,
            microcompact_tool_result_chars=10,
        )
        # Orphaned tool result with large content to trigger L1
        msgs = [
            {"role": "user", "content": "x " * 50},
            {"role": "tool", "tool_call_id": "orphan", "content": "y" * 500},
            {"role": "assistant", "content": "z " * 50},
            {"role": "user", "content": "w " * 50},
        ]
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0,
        )
        # Orphan result should be removed by _fix_tool_pairs
        tool_msgs = [m for m in result if m["role"] == "tool"]
        assert len(tool_msgs) == 0
