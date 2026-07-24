"""Shared pytest fixtures for CLI tests.

Extracted from duplicated ``_reset_halt`` definitions across 5 test files.
"""
from __future__ import annotations

import pytest

from strategy_research.cli.halt import clear_halt


@pytest.fixture(autouse=True)
def _reset_halt():
    """Reset HALT before and after every test."""
    clear_halt()
    yield
    clear_halt()
