"""Tests for progress.py — ProgressEvent, emit_progress, HeartbeatTimer."""
from __future__ import annotations
import sys, unittest, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from strategy_research.core.agent.progress import (
    ProgressEvent,
    emit_progress,
    HeartbeatTimer,
    _set_emitter,
    _get_emitter,
)


class TestProgressEvent(unittest.TestCase):

    def test_to_dict(self) -> None:
        e = ProgressEvent(tool="search", stage="loading", current=1, total=5, message="fetching")
        d = e.to_dict()
        self.assertEqual(d["tool"], "search")
        self.assertEqual(d["stage"], "loading")
        self.assertEqual(d["current"], 1)
        self.assertEqual(d["total"], 5)
        self.assertEqual(d["message"], "fetching")
        self.assertIn("elapsed_s", d)
        self.assertIn("ts", d)

    def test_to_dict_elapsed_rounded(self) -> None:
        e = ProgressEvent(elapsed_s=1.23456)
        d = e.to_dict()
        self.assertEqual(d["elapsed_s"], 1.23)

    def test_default_values(self) -> None:
        e = ProgressEvent()
        self.assertEqual(e.tool, "")
        self.assertEqual(e.stage, "")
        self.assertIsNone(e.current)
        self.assertIsNone(e.total)
        self.assertEqual(e.message, "")
        self.assertGreater(e.ts, 0)


class TestEmitter(unittest.TestCase):

    def setUp(self) -> None:
        _set_emitter(None)

    def tearDown(self) -> None:
        _set_emitter(None)

    def test_get_emitter_returns_none_by_default(self) -> None:
        self.assertIsNone(_get_emitter())

    def test_set_and_get_emitter(self) -> None:
        def fn(e): pass
        _set_emitter(fn)
        self.assertIs(_get_emitter(), fn)

    def test_set_none_clears(self) -> None:
        _set_emitter(lambda e: None)
        _set_emitter(None)
        self.assertIsNone(_get_emitter())


class TestEmitProgress(unittest.TestCase):

    def setUp(self) -> None:
        _set_emitter(None)

    def tearDown(self) -> None:
        _set_emitter(None)

    def test_noop_when_no_emitter(self) -> None:
        emit_progress(stage="test")  # should not raise

    def test_emits_to_active_emitter(self) -> None:
        received = []
        _set_emitter(lambda e: received.append(e))
        emit_progress(stage="test", current=1, total=10, message="working")
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].stage, "test")
        self.assertEqual(received[0].current, 1)

    def test_emitter_exception_does_not_raise(self) -> None:
        _set_emitter(lambda e: (_ for _ in ()).throw(RuntimeError("fail")))
        emit_progress(stage="test")  # should not raise


class TestHeartbeatTimer(unittest.TestCase):

    def test_basic_heartbeat(self) -> None:
        received = []
        with HeartbeatTimer(tool_name="test", interval=0.1, emit=lambda d: received.append(d)):
            time.sleep(1.0)
        self.assertGreater(len(received), 0)
        self.assertIn("tool", received[0])
        self.assertEqual(received[0]["tool"], "test")
        self.assertIn("elapsed_s", received[0])

    def test_interval_clamped_to_0_5(self) -> None:
        received = []
        with HeartbeatTimer(tool_name="t", interval=0.01, emit=lambda d: received.append(d)):
            time.sleep(1.0)
        self.assertGreater(len(received), 0)

    def test_emitter_exception_does_not_crash_thread(self) -> None:
        with HeartbeatTimer(tool_name="t", interval=0.1, emit=lambda d: (_ for _ in ()).throw(RuntimeError("fail"))):
            time.sleep(1.0)


if __name__ == "__main__":
    unittest.main()
