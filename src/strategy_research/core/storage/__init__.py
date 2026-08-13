"""Shared persistence infrastructure (P2).

``sqlite`` — connection/transaction/JSON/id/migration primitives shared by
the SQLite stores (GoalStore, StudyStore, HypothesisStore, ...).
"""

from . import sqlite

__all__ = ["sqlite"]
