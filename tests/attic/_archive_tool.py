"""Archive @pytest.mark.skip dead tests into tests/attic/ (one-time tool).

Method-level skips only; class-level/environment skipif stays untouched.
"""
import ast
from pathlib import Path

TARGETS = [
    "test_workflow_e2e.py",
    "test_swarm.py",
    "test_v05_unit_models.py",
    "test_goal_workflow_phase3.py",
    "test_goal_workflow_v053.py",
    "test_compact_full_pipeline.py",
    "test_agent_loop_extensions.py",
    "test_webui_visual.py",
]
TESTS = Path("tests")
ATTIC = TESTS / "attic"


def _is_mark_skip(decorator: ast.expr) -> bool:
    """Match @pytest.mark.skip(...) — NOT skipif."""
    text = ast.unparse(decorator)
    return "pytest.mark.skip(" in text and "skipif" not in text


def archive(fname: str) -> None:
    src_path = TESTS / fname
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)

    spans = []  # (start0, end0, unparse)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            if any(_is_mark_skip(d) for d in node.decorator_list):
                spans.append((node.lineno - 1, node.end_lineno, node.name))

    if not spans:
        print(f"{fname}: no method-level skips found — skip")
        return

    attic_name = f"attic_{fname}"
    attic_path = ATTIC / attic_name
    blocks = []
    for start0, end0, name in sorted(spans, reverse=True):
        blocks.append("".join(lines[start0:end0]))
        # swallow one trailing blank line after the method
        del_end = end0
        if del_end < len(lines) and lines[del_end].strip() == "":
            del_end += 1
        del lines[start0:del_end]

    header = (
        f'"""Archived from tests/{fname} — dead tests, kept for reference.\n\n'
        f"Every test below was @pytest.mark.skip'd because the code under "
        f"test\nwas removed in the P4/P8/Phase-A cleanups (see each skip "
        f'reason).\nNot collected: tests/conftest.py sets '
        f'collect_ignore_glob=["attic/*"].\n"""\n\n'
        f"import pytest  # noqa: F401 — retained from the archived sources\n\n\n"
    )
    attic_path.write_text(header + "\n\n".join(reversed(blocks)), encoding="utf-8")
    src_path.write_text("".join(lines), encoding="utf-8")
    print(f"{fname}: archived {len(spans)} tests -> tests/attic/{attic_name}")


for f in TARGETS:
    archive(f)
