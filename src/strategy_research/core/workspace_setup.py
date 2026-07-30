"""Smart workspace templates scaffold.

When the server starts, this module ensures the workspace's ``templates/``
directory mirrors the package's ``templates/`` tree. Existing files in
the workspace are **never** overwritten, so user customizations are
preserved.

Recursive walk semantics:

- If a path in package is a directory, ensure it exists in workspace.
- If a path in package is a file, copy it **only** if workspace doesn't
  already have it.
- ``.prompts/`` is excluded (those are agent role prompts loaded from
  the package, not workspace-visible content).
- Idempotent: safe to call on every server start (including ``--reload``).
- New package versions automatically add new files on next start.

The function is deliberately defensive: missing package dir, partial
trees, or permission errors are reported in the returned dict but do
**not** raise — the server should still start, even if scaffold fails.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .. import _TEMPLATES_DIR

logger = logging.getLogger(__name__)


# Top-level subdirectories of ``templates/`` that should NOT be mirrored
# into the workspace. Currently ``.prompts/`` contains agent role system
# prompts that are loaded from the package at runtime, not exposed to the
# LLM as workspace content.
_EXCLUDED_TOP_DIRS = frozenset({".prompts"})


def smart_init_workspace_templates(
    workspace: Path,
    *,
    verbose: bool = False,
) -> dict:
    """Recursively ensure workspace templates mirror package templates.

    Args:
        workspace: Workspace root path.
        verbose: If True, log every action; otherwise only summarize.

    Returns:
        ``{"copied": [str], "skipped": [str], "errors": [str]}`` — lists
        of relative paths (from workspace ``templates/``) for each
        category. Empty lists on success or full failure.
    """
    workspace = Path(workspace).resolve()
    ws_templates = workspace / "templates"
    pkg_templates = _TEMPLATES_DIR

    if not pkg_templates.exists():
        msg = f"package templates dir missing: {pkg_templates}"
        logger.warning(msg)
        return {"copied": [], "skipped": [], "errors": [msg]}

    copied: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    # 1. Ensure top-level templates dir exists.
    try:
        ws_templates.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        msg = f"mkdir {ws_templates}: {exc}"
        logger.warning(msg)
        return {"copied": [], "skipped": [], "errors": [msg]}

    # 2. Recursive walk over package templates (sorted for deterministic order).
    for src_path in sorted(pkg_templates.rglob("*")):
        rel = src_path.relative_to(pkg_templates)

        # Skip excluded top-level dirs (e.g. .prompts).
        if rel.parts and rel.parts[0] in _EXCLUDED_TOP_DIRS:
            continue

        dst_path = ws_templates / rel

        if src_path.is_dir():
            # Ensure directory exists in workspace.
            try:
                dst_path.mkdir(parents=True, exist_ok=True)
                if verbose:
                    logger.debug("dir ensured: %s", rel)
            except Exception as exc:
                errors.append(f"mkdir {rel}: {exc}")
                logger.warning("failed to mkdir %s: %s", rel, exc)
            continue

        # It's a file. Check if workspace already has it.
        if dst_path.exists():
            skipped.append(str(rel))
            if verbose:
                logger.debug("skipped (exists): %s", rel)
            continue

        # Copy missing file.
        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)
            copied.append(str(rel))
            if verbose:
                logger.info("scaffolded: %s", rel)
        except Exception as exc:
            errors.append(f"copy {rel}: {exc}")
            logger.warning("failed to copy %s: %s", rel, exc)

    # 3. Summary log.
    if errors:
        logger.warning(
            "Smart scaffold completed with %d errors: %s",
            len(errors),
            "; ".join(errors[:3]),
        )
    if copied:
        preview = ", ".join(copied[:5])
        if len(copied) > 5:
            preview += f" (+{len(copied) - 5} more)"
        logger.info(
            "Smart scaffold: %d new files, %d skipped (existing). New: %s",
            len(copied),
            len(skipped),
            preview,
        )
    elif skipped:
        logger.info(
            "Smart scaffold: workspace templates up-to-date (%d files present)",
            len(skipped),
        )

    return {"copied": copied, "skipped": skipped, "errors": errors}


__all__ = ["smart_init_workspace_templates"]
