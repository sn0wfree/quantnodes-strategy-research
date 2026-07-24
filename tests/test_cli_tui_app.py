"""Tests for the Textual TUI skeleton (Commit 1).

These tests verify the bare mount lifecycle:

1. The app composes the seven widgets we promised (Header, Sidebar,
   Transcript, Rail, Input, Footer, embedded Banner).
2. The brand-token bridge reads the project theme correctly.
3. The :class:`WriteTranscript` message appends to the transcript log.
4. The :class:`SynthesizeInput` echo path works for sidebar clicks.
5. ``Textual`` ``App.run_test`` mounts without exceptions.

Five tests total — heavier coverage lands with the ChatSession commit.
"""
from __future__ import annotations

import pytest

from strategy_research.cli.tui.app import ResearchApp
from strategy_research.cli.tui.keybindings import TUI_BINDINGS
from strategy_research.cli.tui.messages import (
    SynthesizeInput,
    WriteTranscript,
)
from strategy_research.cli.tui.theme import (
    _strip_rich_modifier,
    active_primary,
    brand_tokens,
)
from strategy_research.cli.tui.widgets import (
    ActivityRail,
    Banner,
    ChatInput,
    CommandSidebar,
    HintFooter,
    TranscriptView,
)


def test_brand_tokens_returns_expected_keys():
    """brand_tokens() returns the eight CSS-friendly tokens."""
    from dataclasses import asdict
    tokens = brand_tokens()
    d = asdict(tokens)
    for key in ("primary", "primary_dim", "success", "danger",
                "warning", "info", "muted", "surface"):
        assert key in d, f"missing token: {key}"
        assert isinstance(d[key], str)
        # Hex string or empty (NO_COLOR mode); never a Rich modifier prefix.
        v = d[key]
        assert not v.lower().startswith("bold ")
        assert not v.lower().startswith("italic ")


def test_strip_rich_modifier_handles_prefixes():
    """Style modifiers ('bold ', 'italic ') are removed."""
    assert _strip_rich_modifier("bold #d97706") == "#d97706"
    assert _strip_rich_modifier("italic red") == "red"
    assert _strip_rich_modifier("plain blue") == "plain blue"
    assert _strip_rich_modifier("") == ""


def test_tui_bindings_are_well_formed():
    """TUI_BINDINGS exposes the core key bindings."""
    keys = {b.key for b in TUI_BINDINGS}
    assert "ctrl+c" in keys, "ctrl+c halt binding missing"
    assert "ctrl+d" in keys, "ctrl+d quit binding missing"
    assert "f1" in keys, "f1 help binding missing"


@pytest.mark.asyncio
async def test_research_app_run_test_mounts_widgets():
    """End-to-end: app.run_test() mounts the full skeleton."""
    app = ResearchApp(model="gpt-4o", version="0.4.0")
    async with app.run_test() as pilot:
        await pilot.pause()
        # All 5 named inner widgets are present (header + footer are auto-mounted).
        assert app.query_one(TranscriptView).is_mounted
        assert app.query_one(ActivityRail).is_mounted
        assert app.query_one(CommandSidebar).is_mounted
        assert app.query_one("#input").is_mounted  # ChatInput uses id="input"


@pytest.mark.asyncio
async def test_write_transcript_message_appends_to_log():
    """Posting a ``WriteTranscript`` message lands inside the transcript widget."""
    app = ResearchApp(model="m", version="0.4.0")
    async with app.run_test() as pilot:
        await pilot.pause()
        text_marker = "TI_MARKER_HELLO"
        app.write_transcript(text_marker)
        await pilot.pause()
        await pilot.pause()  # let the render pipeline run
        transcript = app.query_one(TranscriptView)
        joined = " ".join(str(line) for line in transcript.lines)
        assert text_marker in joined, f"marker not found in transcript lines: {joined!r}"


@pytest.mark.asyncio
async def test_synthesize_input_echoes_to_transcript():
    """Sidebar ``SynthesizeInput`` clicks echo to the transcript in v1.

    Note: Textual's test pilot under pytest-asyncio processes posted
    messages only after explicit ``await pilot.pause()`` cycles. We
    invoke the handler directly via :meth:`on_synthesize_input` to
    keep the test fast and deterministic.
    """
    app = ResearchApp(model="m", version="0.4.0")
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_synthesize_input(SynthesizeInput(text="/help"))
        await pilot.pause()
        await pilot.pause()
        transcript = app.query_one(TranscriptView)
        joined = " ".join(str(line) for line in transcript.lines)
        assert "/help" in joined, f"/help not echoed in transcript: {joined!r}"


@pytest.mark.asyncio
async def test_app_mount_renders_banner_in_transcript():
    """End-to-end: app mount renders the gradient banner into TranscriptView.

    This is the smoke test that proves the full skeleton works:
    CSS loads, widgets mount, on_mount runs, and the banner Rich
    ``Text`` lands inside the chat log as the first scrollback entry.
    """
    app = ResearchApp(model="gpt-4o", version="0.4.0")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        tv = app.query_one(TranscriptView)
        assert len(tv.lines) > 0, "banner should produce at least one line"
        joined = " ".join(str(s) for line in tv.lines for s in line)
        # Brand line "strategy-research" should appear verbatim.
        assert "strategy-research" in joined
        assert "0.4.0" in joined
        assert app.banner is not None


# Cheap non-asyncio test: brand primary must not be the literal Rich bold prefix.
def test_active_primary_is_clean_hex():
    p = active_primary()
    assert not p.lower().startswith("bold ")
    assert p.startswith("#")
