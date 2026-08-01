"""Tests for api/container.py (Phase 3.2)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestBuildContainer(unittest.TestCase):

    def test_basic_build(self):
        from strategy_research.api.container import build_container

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            container = build_container(
                workspace_path=Path(tmpdir),
                db_path=db_path,
            )
            self.assertEqual(container.workspace_path, Path(tmpdir))
            self.assertEqual(container.db_path, db_path)
            self.assertIsNotNone(container.event_bus)
            self.assertIsNotNone(container.event_bus_v2)
            self.assertIsNotNone(container.session_store)
            self.assertIsNotNone(container.session_service)

    def test_services_wired_correctly(self):
        """Services share the same EventBus / EventBusV2 instances."""
        from strategy_research.api.container import build_container

        with TemporaryDirectory() as tmpdir:
            container = build_container(
                workspace_path=Path(tmpdir),
                db_path=Path(tmpdir) / "test.db",
            )
            # EventBusV2 should wrap the legacy EventBus
            self.assertIs(container.event_bus_v2.event_bus, container.event_bus)
            # SessionService should use EventBusV2
            self.assertIs(container.session_service.event_bus, container.event_bus_v2)
            # SessionStore should match db_path
            self.assertEqual(container.session_store.db_path, Path(tmpdir) / "test.db")

    def test_default_db_path(self):
        """When db_path is not given, derive from workspace_path."""
        from strategy_research.api.container import build_container

        with TemporaryDirectory() as tmpdir:
            container = build_container(workspace_path=Path(tmpdir))
            self.assertEqual(
                container.db_path,
                Path(tmpdir) / "quantnodes_strategy_research_user.db",
            )

    def test_no_workspace(self):
        """build_container works without workspace_path."""
        from strategy_research.api.container import build_container

        container = build_container(db_path=Path("/tmp/x.db"))
        self.assertIsNone(container.workspace_path)
        self.assertEqual(container.db_path, Path("/tmp/x.db"))


class TestBuildContainerWithFactories(unittest.TestCase):

    def test_event_bus_factory_override(self):
        from strategy_research.api.container import build_container

        fake_bus = MagicMock(name="fake_event_bus")
        fake_bus._sse_attached = True  # Skip SSE attach
        container = build_container(
            db_path=Path("/tmp/x.db"),
            event_bus_factory=lambda: fake_bus,
        )
        self.assertIs(container.event_bus, fake_bus)

    def test_session_service_factory_override(self):
        from strategy_research.api.container import build_container

        fake_svc = MagicMock(name="fake_session_service")
        container = build_container(
            db_path=Path("/tmp/x.db"),
            session_service_factory=lambda store, bus: fake_svc,
        )
        self.assertIs(container.session_service, fake_svc)


class TestAttachContainer(unittest.TestCase):

    def test_attach_and_get(self):
        from strategy_research.api.container import (
            attach_container,
            build_container,
            get_container,
        )

        # Fake app with .state
        class FakeApp:
            pass

        app = FakeApp()
        app.state = type("State", (), {})()

        container = build_container(db_path=Path("/tmp/x.db"))
        attach_container(app, container)

        # get_container works
        request = MagicMock()
        request.app = app
        result = get_container(request)
        self.assertIs(result, container)

    def test_get_container_without_attach_raises(self):
        from strategy_research.api.container import get_container

        class FakeApp:
            pass

        app = FakeApp()
        app.state = type("State", (), {})()

        request = MagicMock()
        request.app = app

        with self.assertRaises(RuntimeError):
            get_container(request)


class TestResetContainer(unittest.TestCase):

    def test_reset_detaches(self):
        from strategy_research.api.container import (
            attach_container,
            build_container,
            reset_container,
        )

        class FakeApp:
            pass

        app = FakeApp()
        app.state = type("State", (), {})()

        attach_container(app, build_container(db_path=Path("/tmp/x.db")))
        self.assertTrue(hasattr(app.state, "_container"))

        reset_container(app)
        self.assertFalse(hasattr(app.state, "_container"))


class TestServicesFromContainer(unittest.TestCase):

    def test_extracts_all_services(self):
        from strategy_research.api.container import (
            build_container,
            services_from_container,
        )

        container = build_container(db_path=Path("/tmp/x.db"))
        services = services_from_container(container)
        self.assertIn("_event_bus", services)
        self.assertIn("_event_bus_v2", services)
        self.assertIn("_session_service", services)
        # Same instances
        self.assertIs(services["_event_bus"], container.event_bus)
        self.assertIs(services["_event_bus_v2"], container.event_bus_v2)
        self.assertIs(services["_session_service"], container.session_service)


class TestContainerIsFrozen(unittest.TestCase):

    def test_immutable(self):
        """AppContainer is a frozen dataclass — mutation raises."""
        from strategy_research.api.container import build_container

        container = build_container(db_path=Path("/tmp/x.db"))
        with self.assertRaises(Exception):
            container.db_path = Path("/tmp/other.db")  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()