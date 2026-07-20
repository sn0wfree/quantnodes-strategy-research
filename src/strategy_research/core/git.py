"""Git 操作工具。"""
from __future__ import annotations

import subprocess
from pathlib import Path


def git_commit(workspace_path: Path, message: str) -> bool:
    """提交当前更改。"""
    try:
        subprocess.run(
            ["git", "add", "."],
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def git_reset(workspace_path: Path) -> bool:
    """重置到上一次提交。"""
    try:
        subprocess.run(
            ["git", "reset", "--hard", "HEAD~1"],
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def git_get_hash(workspace_path: Path, short: bool = True) -> str:
    """获取当前 commit hash。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short" if short else "HEAD"],
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return ""
