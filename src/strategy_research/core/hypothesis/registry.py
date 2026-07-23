"""Pure-code durable research hypothesis registry (P3-b).

The registry is intentionally small: local JSON storage, deterministic reads,
and no dependency on LLMs or live trading services.

Adapted from vibe-trading-ai 0.1.11 (MIT License, HKUDS).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HYPOTHESIS_STATUSES = (
    "exploring",
    "testing",
    "validated",
    "rejected",
    "monitoring",
)
_STATUS_SET = set(HYPOTHESIS_STATUSES)

# P3-C1: Valid status transitions (state machine)
VALID_TRANSITIONS: dict[str, set[str]] = {
    "exploring": {"testing", "rejected"},
    "testing": {"validated", "rejected", "exploring"},
    "validated": {"monitoring"},
    "monitoring": {"testing", "rejected"},
    "rejected": set(),  # terminal
}

_ENV_PATH = "QUANTNODES_RESEARCH_HYPOTHESES_PATH"
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]{2,}|[\u4e00-\u9fff]")


def _check_transition(from_status: str, to_status: str) -> None:
    """Validate that a status transition is legal.

    Raises:
        ValueError: If the transition is not in VALID_TRANSITIONS.
    """
    allowed = VALID_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise ValueError(
            f"invalid hypothesis transition: {from_status} -> {to_status}. "
            f"Allowed from {from_status}: {sorted(allowed) or '(terminal)'}"
        )


def default_hypotheses_path() -> Path:
    """Return the configured hypotheses JSON path.

    Returns:
        Env override path when ``QUANTNODES_RESEARCH_HYPOTHESES_PATH`` is set,
        otherwise ``~/.quantnodes-research/hypotheses.json``.
    """
    override = os.environ.get(_ENV_PATH, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".quantnodes-research" / "hypotheses.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _new_hypothesis_id(title: str, created_at: str, existing_ids: set[str]) -> str:
    seed = f"{title.strip().lower()}|{created_at}"
    base = "hyp_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    if base not in existing_ids:
        return base
    idx = 2
    while f"{base}_{idx}" in existing_ids:
        idx += 1
    return f"{base}_{idx}"


def _validate_status(status: str) -> str:
    normalized = str(status).strip().lower()
    if normalized not in _STATUS_SET:
        allowed = ", ".join(HYPOTHESIS_STATUSES)
        raise ValueError(f"unknown hypothesis status '{status}'. Allowed: {allowed}")
    return normalized


@dataclass
class Hypothesis:
    """A research hypothesis tracked across analysis and backtests.

    Attributes:
        hypothesis_id: Stable registry identifier.
        title: Short human-readable title.
        thesis: Research thesis or rationale.
        status: Lifecycle status.
        universe: Target universe, market, or asset set.
        signal_definition: Signal logic in plain text.
        data_sources: Data sources expected or used.
        skills: Relevant quant-research skills.
        run_cards: Linked backtest/run-card artifacts.
        invalidation_notes: Notes describing rejection or invalidation logic.
        parent_hypothesis_id: Parent in the hypothesis graph (for derived hypotheses).
        related_ids: Manually linked related hypotheses.
        contradicts_ids: Hypotheses this one contradicts.
        goal_id: Associated research goal id (if any).
        created_at: UTC creation timestamp.
        updated_at: UTC last update timestamp.
    """

    hypothesis_id: str
    title: str
    thesis: str
    status: str = "exploring"
    universe: str = ""
    signal_definition: str = ""
    data_sources: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    run_cards: list[dict[str, Any]] = field(default_factory=list)
    invalidation_notes: str = ""
    parent_hypothesis_id: str | None = None
    related_ids: list[str] = field(default_factory=list)
    contradicts_ids: list[str] = field(default_factory=list)
    goal_id: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the hypothesis to plain JSON-compatible data."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Hypothesis":
        """Build a hypothesis from persisted JSON data."""
        now = _utc_now()
        return cls(
            hypothesis_id=str(data.get("hypothesis_id", "")),
            title=str(data.get("title", "")),
            thesis=str(data.get("thesis", "")),
            status=_validate_status(str(data.get("status", "exploring"))),
            universe=str(data.get("universe", "")),
            signal_definition=str(data.get("signal_definition", "")),
            data_sources=_coerce_str_list(data.get("data_sources")),
            skills=_coerce_str_list(data.get("skills")),
            run_cards=list(data.get("run_cards") or data.get("backtests") or []),
            invalidation_notes=str(data.get("invalidation_notes", "")),
            parent_hypothesis_id=data.get("parent_hypothesis_id") or None,
            related_ids=_coerce_str_list(data.get("related_ids")),
            contradicts_ids=_coerce_str_list(data.get("contradicts_ids")),
            goal_id=data.get("goal_id") or None,
            created_at=str(data.get("created_at") or now),
            updated_at=str(data.get("updated_at") or data.get("created_at") or now),
        )


class HypothesisRegistry:
    """File-backed registry for research hypotheses.

    P3-E: Optionally backed by SQLite (via HypothesisStore). If ``db_path``
    is provided or ``HYPOTHESIS_USE_SQLITE`` env is set, delegates to
    HypothesisStore. Otherwise falls back to the legacy JSON file storage.
    """

    def __init__(self, path: Path | None = None, db_path: Path | None = None) -> None:
        """Initialize the registry.

        Args:
            path: Optional JSON storage path. Defaults to env override or
                ``~/.quantnodes-research/hypotheses.json``.
            db_path: Optional SQLite path. If provided, uses HypothesisStore.
        """
        import os
        self.path = path or default_hypotheses_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # P3-E: SQLite-backed mode
        if db_path is not None or os.environ.get("HYPOTHESIS_USE_SQLITE", "").strip():
            from .store import HypothesisStore
            self._store: HypothesisStore | None = HypothesisStore(db_path=db_path)
        else:
            self._store = None

    def create(
        self,
        *,
        title: str,
        thesis: str,
        status: str = "exploring",
        universe: str = "",
        signal_definition: str = "",
        data_sources: list[str] | None = None,
        skills: list[str] | None = None,
        invalidation_notes: str = "",
        parent_hypothesis_id: str | None = None,
        related_ids: list[str] | None = None,
        contradicts_ids: list[str] | None = None,
        goal_id: str | None = None,
    ) -> Hypothesis:
        """Create and persist a new hypothesis."""
        title = title.strip()
        thesis = thesis.strip()
        if not title:
            raise ValueError("title is required")
        if not thesis:
            raise ValueError("thesis is required")

        records = self.list()
        now = _utc_now()
        hyp = Hypothesis(
            hypothesis_id=_new_hypothesis_id(title, now, {h.hypothesis_id for h in records}),
            title=title,
            thesis=thesis,
            status=_validate_status(status),
            universe=universe.strip(),
            signal_definition=signal_definition.strip(),
            data_sources=_coerce_str_list(data_sources),
            skills=_coerce_str_list(skills),
            invalidation_notes=invalidation_notes.strip(),
            parent_hypothesis_id=parent_hypothesis_id,
            related_ids=_coerce_str_list(related_ids),
            contradicts_ids=_coerce_str_list(contradicts_ids),
            goal_id=goal_id,
            created_at=now,
            updated_at=now,
        )
        records.append(hyp)
        self._save(records)
        return hyp

    def update(
        self,
        hypothesis_id: str,
        *,
        title: str | None = None,
        thesis: str | None = None,
        status: str | None = None,
        universe: str | None = None,
        signal_definition: str | None = None,
        data_sources: list[str] | None = None,
        skills: list[str] | None = None,
        invalidation_notes: str | None = None,
        parent_hypothesis_id: str | None = None,
        related_ids: list[str] | None = None,
        contradicts_ids: list[str] | None = None,
        goal_id: str | None = None,
    ) -> Hypothesis:
        """Update an existing hypothesis.

        P3-C: status changes are validated against VALID_TRANSITIONS.
        P3-C: parent_hypothesis_id / related_ids / contradicts_ids / goal_id
              can be set or cleared (None clears).
        """
        records = self.list()
        hyp = self._find_required(records, hypothesis_id)
        if title is not None:
            hyp.title = title.strip()
        if thesis is not None:
            hyp.thesis = thesis.strip()
        if status is not None:
            new_status = _validate_status(status)
            if new_status != hyp.status:
                _check_transition(hyp.status, new_status)
            hyp.status = new_status
        if universe is not None:
            hyp.universe = universe.strip()
        if signal_definition is not None:
            hyp.signal_definition = signal_definition.strip()
        if data_sources is not None:
            hyp.data_sources = _coerce_str_list(data_sources)
        if skills is not None:
            hyp.skills = _coerce_str_list(skills)
        if invalidation_notes is not None:
            hyp.invalidation_notes = invalidation_notes.strip()
        if parent_hypothesis_id is not None:
            hyp.parent_hypothesis_id = parent_hypothesis_id or None
        if related_ids is not None:
            hyp.related_ids = _coerce_str_list(related_ids)
        if contradicts_ids is not None:
            hyp.contradicts_ids = _coerce_str_list(contradicts_ids)
        if goal_id is not None:
            hyp.goal_id = goal_id or None
        hyp.updated_at = _utc_now()
        self._save(records)
        return hyp

    def link_backtest(
        self,
        hypothesis_id: str,
        *,
        run_card_path: str = "",
        backtest_run_dir: str = "",
        metrics: dict[str, Any] | None = None,
        notes: str = "",
    ) -> Hypothesis:
        """Link a run card or backtest artifact to a hypothesis."""
        if not run_card_path and not backtest_run_dir:
            raise ValueError("run_card_path or backtest_run_dir is required")
        records = self.list()
        hyp = self._find_required(records, hypothesis_id)
        hyp.run_cards.append({
            "run_card_path": run_card_path,
            "backtest_run_dir": backtest_run_dir,
            "metrics": metrics or {},
            "notes": notes,
            "linked_at": _utc_now(),
        })
        hyp.updated_at = _utc_now()
        self._save(records)
        return hyp

    # ── P3-C: Relationship graph operations ─────────────────────

    def derive(
        self,
        *,
        parent_id: str,
        title: str,
        thesis: str,
        signal_definition: str = "",
    ) -> Hypothesis:
        """Create a child hypothesis derived from a parent.

        Inherits parent's universe, data_sources, and skills. Sets
        parent_hypothesis_id on the new hypothesis.
        """
        records = self.list()
        parent = self._find_required(records, parent_id)
        now = _utc_now()
        hyp = Hypothesis(
            hypothesis_id=_new_hypothesis_id(title, now, {h.hypothesis_id for h in records}),
            title=title.strip(),
            thesis=thesis.strip(),
            status="exploring",
            universe=parent.universe,
            signal_definition=signal_definition.strip() or parent.signal_definition,
            data_sources=list(parent.data_sources),
            skills=list(parent.skills),
            parent_hypothesis_id=parent_id,
            created_at=now,
            updated_at=now,
        )
        records.append(hyp)
        self._save(records)
        return hyp

    def link(self, hyp_id: str, related_id: str) -> Hypothesis:
        """Mark two hypotheses as related (bidirectional)."""
        records = self.list()
        hyp_a = self._find_required(records, hyp_id)
        hyp_b = self._find_required(records, related_id)
        if related_id not in hyp_a.related_ids:
            hyp_a.related_ids.append(related_id)
        if hyp_id not in hyp_b.related_ids:
            hyp_b.related_ids.append(hyp_id)
        hyp_a.updated_at = hyp_b.updated_at = _utc_now()
        self._save(records)
        return hyp_a

    def unlink(self, hyp_id: str, related_id: str) -> Hypothesis:
        """Remove bidirectional related link."""
        records = self.list()
        hyp_a = self._find_required(records, hyp_id)
        hyp_b = self._find_required(records, related_id)
        hyp_a.related_ids = [x for x in hyp_a.related_ids if x != related_id]
        hyp_b.related_ids = [x for x in hyp_b.related_ids if x != hyp_id]
        hyp_a.updated_at = hyp_b.updated_at = _utc_now()
        self._save(records)
        return hyp_a

    def contradicts(self, hyp_id: str, other_id: str, notes: str = "") -> Hypothesis:
        """Mark two hypotheses as contradicting (one-way from hyp_id's perspective)."""
        records = self.list()
        hyp_a = self._find_required(records, hyp_id)
        self._find_required(records, other_id)
        if other_id not in hyp_a.contradicts_ids:
            hyp_a.contradicts_ids.append(other_id)
        hyp_a.invalidation_notes = (
            f"{hyp_a.invalidation_notes}\nContradicts {other_id}: {notes}"
            if hyp_a.invalidation_notes
            else f"Contradicts {other_id}: {notes}"
        ).strip()
        hyp_a.updated_at = _utc_now()
        self._save(records)
        return hyp_a

    def link_goal(self, hyp_id: str, goal_id: str) -> Hypothesis:
        """Associate a hypothesis with a research goal."""
        records = self.list()
        hyp = self._find_required(records, hyp_id)
        hyp.goal_id = goal_id
        hyp.updated_at = _utc_now()
        self._save(records)
        return hyp

    def list_by_goal(self, goal_id: str) -> list[Hypothesis]:
        """Return all hypotheses linked to a given goal."""
        return [h for h in self.list() if h.goal_id == goal_id]

    def list_children(self, parent_id: str) -> list[Hypothesis]:
        """Return all child hypotheses of a parent."""
        return [h for h in self.list() if h.parent_hypothesis_id == parent_id]

    def list_contradictions(self, hyp_id: str) -> list[Hypothesis]:
        """Return all hypotheses that this one contradicts."""
        records = self.list()
        hyp = self._find_required(records, hyp_id)
        by_id = {h.hypothesis_id: h for h in records}
        return [by_id[cid] for cid in hyp.contradicts_ids if cid in by_id]

    def search(
        self,
        *,
        query: str = "",
        status: str | None = None,
        limit: int = 10,
    ) -> list[Hypothesis]:
        """Search hypotheses by text and/or status.

        Args:
            query: Text query over title, thesis, universe, signal, sources,
                skills, notes, and links.
            status: Optional status filter.
            limit: Maximum results.

        Returns:
            Matching hypotheses ordered by score then most recently updated.
        """
        status_filter = _validate_status(status) if status else None
        query_tokens = _tokenize(query)
        scored: list[tuple[int, Hypothesis]] = []
        for hyp in self.list():
            if status_filter and hyp.status != status_filter:
                continue
            haystack = json.dumps(hyp.to_dict(), ensure_ascii=False, sort_keys=True)
            if not query_tokens:
                score = 1
            else:
                hay_tokens = _tokenize(haystack)
                score = len(query_tokens & hay_tokens)
            if score > 0:
                scored.append((score, hyp))
        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        return [hyp for _, hyp in scored[: max(1, min(int(limit), 100))]]

    def list(self) -> list[Hypothesis]:
        """Load all hypotheses from storage."""
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid hypotheses storage JSON: {self.path}") from exc
        if not isinstance(raw, list):
            raise ValueError("hypotheses storage must contain a JSON list")
        return [Hypothesis.from_dict(item) for item in raw if isinstance(item, dict)]

    def get(self, hypothesis_id: str) -> Hypothesis | None:
        """Return a hypothesis by id or None if missing."""
        for hyp in self.list():
            if hyp.hypothesis_id == hypothesis_id:
                return hyp
        return None

    def _save(self, records: list[Hypothesis]) -> None:
        payload = [hyp.to_dict() for hyp in sorted(records, key=lambda h: h.created_at)]
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(self.path)

    @staticmethod
    def _find_required(records: list[Hypothesis], hypothesis_id: str) -> Hypothesis:
        for hyp in records:
            if hyp.hypothesis_id == hypothesis_id:
                return hyp
        raise KeyError(f"hypothesis not found: {hypothesis_id}")
