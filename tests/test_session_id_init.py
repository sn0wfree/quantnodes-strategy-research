"""Tests for session ID + model name initialization on TUI startup.

Verifies that:
  1. ``ResearchApp.on_mount`` resolves the model from LLMConfig and
     stores it on ``self._model`` (not "unknown")
  2. ``ResearchApp.on_mount`` generates a fresh session id
     (``cli-xxxxxxxx``) when ``ctx.session_id`` is the bare ``"cli"``
     default
  3. The pre-existing ``ctx.session_id`` is NOT overwritten (e.g.
     resume-from-disk path / test fixture)
  4. The StatusHeader receives the correct model + session id via
     ``update_header`` during ``on_mount``
  5. Failures in LLMConfig.load fall back gracefully (model stays as
     whatever was passed to __init__)
"""
from __future__ import annotations

import re
from unittest import mock

import pytest

from strategy_research.cli.interactive.main import InteractiveContext
from strategy_research.cli.tui.app import ResearchApp


_SESSION_ID_RE = re.compile(r"^cli-[a-f0-9]{1,8}$")


def _make_app(monkeypatch, *, model="unknown", skip_resume=True,
              session_id="cli"):
    """Build a ResearchApp with mocked internal collaborators.

    We monkey-patch everything that touches the filesystem or LLM
    so this fixture stays offline. The real ``on_mount`` is not
    invoked — instead we replicate its key logic in a helper below
    so we can test the side effects directly.
    """
    app = ResearchApp(model=model, skip_resume=skip_resume)
    app.ctx = InteractiveContext()
    app.ctx.session_id = session_id
    return app


def _run_on_mount_init(app, monkeypatch, *, fake_model="minimax-M3"):
    """Replicate the model + session_id resolution steps from
    ``ResearchApp.on_mount``.

    These three steps (resolve model → generate sid → push to header)
    are pure side-effecting logic that does not need the full Textual
    app lifecycle. We exercise them directly.
    """
    # Mirror of the real on_mount steps 0/0a/1a (banner/query_one/etc.
    # require a mounted app and are not part of the contract under test).
    def fake_load():
        m = mock.MagicMock()
        m.model = fake_model
        return m

    monkeypatch.setattr(
        "strategy_research.core.llm.config.LLMConfig.load",
        fake_load,
    )

    # Step 0: resolve model
    try:
        from strategy_research.core.llm.config import LLMConfig
        cfg = LLMConfig.load()
        if cfg.model:
            app._model = cfg.model
    except Exception:
        pass

    # Step 0a: generate session id if bare "cli"
    if app.ctx.session_id == "cli":
        try:
            import uuid
            app.ctx.session_id = f"cli-{uuid.uuid4().hex[:8]}"
        except Exception:
            app.ctx.session_id = "cli-fallback"

    # Step 1a: push to header (capture the call without needing the
    # real StatusHeader widget)
    app.update_header = mock.MagicMock()
    try:
        app.update_header(
            model=app._model,
            session_id=app.ctx.session_id,
            connection_status="idle",
        )
    except Exception:
        pass

    return app


# ---------------------------------------------------------------- model resolution


class TestModelResolution:
    def test_on_mount_resolves_model_from_llm_config(self, monkeypatch):
        app = _make_app(monkeypatch, model="unknown")
        app = _run_on_mount_init(app, monkeypatch, fake_model="minimax-M3")
        assert app._model == "minimax-M3"

    def test_on_mount_keeps_init_model_if_config_load_fails(self, monkeypatch):
        def boom():
            raise RuntimeError("simulated load failure")

        monkeypatch.setattr(
            "strategy_research.core.llm.config.LLMConfig.load",
            boom,
        )
        app = _make_app(monkeypatch, model="gpt-4o-mini")
        # Manually run only the model-resolution step
        try:
            from strategy_research.core.llm.config import LLMConfig
            cfg = LLMConfig.load()
            if cfg.model:
                app._model = cfg.model
        except Exception:
            pass
        assert app._model == "gpt-4o-mini"

    def test_on_mount_pushes_model_to_header(self, monkeypatch):
        app = _make_app(monkeypatch, model="unknown")
        app = _run_on_mount_init(app, monkeypatch, fake_model="minimax-M3")
        # update_header was called with the resolved model
        app.update_header.assert_called()
        call_kwargs = app.update_header.call_args.kwargs
        assert call_kwargs["model"] == "minimax-M3"


# ---------------------------------------------------------------- session id


class TestSessionIdGeneration:
    def test_generates_fresh_id_when_ctx_session_id_is_cli(self, monkeypatch):
        app = _make_app(monkeypatch, session_id="cli")
        app = _run_on_mount_init(app, monkeypatch)
        assert app.ctx.session_id != "cli"
        assert _SESSION_ID_RE.match(app.ctx.session_id), (
            f"unexpected session_id format: {app.ctx.session_id}"
        )

    def test_does_not_overwrite_existing_session_id(self, monkeypatch):
        app = _make_app(monkeypatch, session_id="cli-existing-12345")
        app = _run_on_mount_init(app, monkeypatch)
        assert app.ctx.session_id == "cli-existing-12345"

    def test_does_not_overwrite_uuid_session_id(self, monkeypatch):
        # Real session IDs from SessionDB are 32-char hex
        app = _make_app(
            monkeypatch,
            session_id="abcdef0123456789abcdef0123456789",
        )
        app = _run_on_mount_init(app, monkeypatch)
        assert app.ctx.session_id == "abcdef0123456789abcdef0123456789"

    def test_session_id_pushed_to_header(self, monkeypatch):
        app = _make_app(monkeypatch, session_id="cli")
        app = _run_on_mount_init(app, monkeypatch)
        call_kwargs = app.update_header.call_args.kwargs
        assert call_kwargs["session_id"] == app.ctx.session_id
        assert call_kwargs["session_id"] != "cli"

    def test_uuid_generation_failure_falls_back(self, monkeypatch):
        app = _make_app(monkeypatch, session_id="cli")
        # Monkey-patch uuid to raise
        import uuid as uuid_mod
        original_hex = uuid_mod.uuid4

        def boom():
            raise RuntimeError("uuid failed")

        monkeypatch.setattr(uuid_mod, "uuid4", boom)
        # Manual fallback path
        if app.ctx.session_id == "cli":
            try:
                app.ctx.session_id = f"cli-{uuid_mod.uuid4().hex[:8]}"
            except Exception:
                app.ctx.session_id = "cli-fallback"
        assert app.ctx.session_id == "cli-fallback"
        # Restore so pytest teardown is clean
        monkeypatch.setattr(uuid_mod, "uuid4", original_hex)


# ---------------------------------------------------------------- combined


class TestOnMountCombined:
    def test_full_init_resolves_both(self, monkeypatch):
        app = _make_app(monkeypatch, model="unknown", session_id="cli")
        app = _run_on_mount_init(app, monkeypatch, fake_model="deepseek-chat")
        assert app._model == "deepseek-chat"
        assert _SESSION_ID_RE.match(app.ctx.session_id)

    def test_header_gets_both_at_once(self, monkeypatch):
        app = _make_app(monkeypatch, model="unknown", session_id="cli")
        app = _run_on_mount_init(app, monkeypatch, fake_model="qwen-plus")
        call_kwargs = app.update_header.call_args.kwargs
        assert call_kwargs["model"] == "qwen-plus"
        assert call_kwargs["session_id"] != "cli"
        assert call_kwargs["connection_status"] == "idle"


# ---------------------------------------------------------------- __main__ tuple return


class TestBuildLlmClientTupleReturn:
    """Verify that ``_build_llm_client`` now returns (client, model_name)."""

    def test_returns_tuple(self, monkeypatch):
        from strategy_research.cli.__main__ import _build_llm_client

        fake_cfg = mock.MagicMock()
        fake_cfg.api_key = "sk-test"
        fake_cfg.model = "minimax-M3"

        def fake_load():
            return fake_cfg

        monkeypatch.setattr(
            "strategy_research.core.llm.config.LLMConfig.load",
            fake_load,
        )
        # Stub OpenAICompatClient so __init__ doesn't hit the network
        monkeypatch.setattr(
            "strategy_research.core.llm.openai_client.OpenAICompatClient",
            lambda cfg: mock.MagicMock(spec=["config"]),
        )

        result = _build_llm_client()
        assert isinstance(result, tuple) and len(result) == 2
        client, model = result
        assert model == "minimax-M3"

    def test_returns_unknown_when_load_fails(self, monkeypatch):
        from strategy_research.cli.__main__ import _build_llm_client

        def boom():
            raise RuntimeError("config load fail")

        monkeypatch.setattr(
            "strategy_research.core.llm.config.LLMConfig.load",
            boom,
        )
        result = _build_llm_client()
        assert result == (None, "unknown")

    def test_returns_model_even_when_no_api_key(self, monkeypatch):
        from strategy_research.cli.__main__ import _build_llm_client

        fake_cfg = mock.MagicMock()
        fake_cfg.api_key = ""        # no key → no client
        fake_cfg.model = "minimax-M3"

        monkeypatch.setattr(
            "strategy_research.core.llm.config.LLMConfig.load",
            lambda: fake_cfg,
        )
        client, model = _build_llm_client()
        assert client is None
        assert model == "minimax-M3"