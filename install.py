#!/usr/bin/env python3
"""Strategy Research 一键安装脚本（跨平台：Windows / Linux / macOS）。

Usage:
    python install.py                  # 完整安装
    python install.py --dev            # 开发模式（不构建前端）
    python install.py --frontend       # 仅构建前端
    python install.py --backend        # 仅安装后端依赖
    python install.py --uninstall      # 卸载
    python install.py --help           # 显示帮助

启动 Web UI:
    quantnodes-strategy-research serve --host 0.0.0.0 --port 87183
    # 浏览器访问 http://localhost:87183
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# ANSI 颜色（Windows 10+ 支持）
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
NC = "\033[0m"


def log(msg: str) -> None:
    print(f"{GREEN}[INFO]{NC} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}[WARN]{NC} {msg}")


def err(msg: str) -> None:
    print(f"{RED}[ERROR]{NC} {msg}")


def header(msg: str) -> None:
    print(f"\n{BLUE}=== {msg} ==={NC}")


def run(cmd: list[str] | str, cwd: Path | None = None) -> None:
    """运行命令，失败时退出。"""
    cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
    log(f"$ {cmd_str}")
    result = subprocess.run(
        cmd,
        cwd=cwd,
        shell=isinstance(cmd, str),
        env={**__import__("os").environ, "PYTHONUNBUFFERED": "1"},
    )
    if result.returncode != 0:
        err(f"命令失败（退出码 {result.returncode}）")
        sys.exit(result.returncode)


def check_command(name: str) -> None:
    """检查命令是否存在。"""
    if shutil.which(name) is None:
        err(f"{name} 未安装。请先安装 {name}")
        sys.exit(1)


def get_pip() -> str:
    """获取 pip 可执行文件路径。"""
    # 优先使用 python -m pip（更可靠）
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            check=True,
            capture_output=True,
        )
        return f"{sys.executable} -m pip"
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # 退而求其次：直接用 pip / pip3
    for candidate in ("pip3", "pip"):
        if shutil.which(candidate):
            return candidate
    err("未找到 pip。请先安装 pip")
    sys.exit(1)


# ============================================================
# 安装步骤
# ============================================================
def install_backend(project_root: Path, pip: str) -> None:
    """安装 Python 后端依赖。"""
    header("安装后端依赖")

    check_command(sys.executable)

    # 获取版本信息
    py_ver = subprocess.run(
        [sys.executable, "--version"], capture_output=True, text=True
    ).stdout.strip()
    log(f"Python: {py_ver}")
    log(f"pip:    {pip}")

    log("安装包: api + dev + webui-api")
    run([*pip.split(), "install", "-e", ".[api,dev,webui-api]"], cwd=project_root)
    log("后端安装完成 ✓")


def install_frontend_deps(frontend_dir: Path) -> None:
    """安装前端 npm 依赖。"""
    header("安装前端依赖")

    check_command("npm")

    log(f"Node: {subprocess.run(['node', '--version'], capture_output=True, text=True).stdout.strip()}")
    log(f"npm:  {subprocess.run(['npm', '--version'], capture_output=True, text=True).stdout.strip()}")

    run(["npm", "install", "--legacy-peer-deps"], cwd=frontend_dir)
    log("前端依赖安装完成 ✓")


def build_frontend(frontend_dir: Path) -> None:
    """构建前端（输出到 webui/static/）。"""
    header("构建前端")

    log("运行 npm run build ...")
    run(["npm", "run", "build"], cwd=frontend_dir)
    log("前端构建完成 → webui/static/ ✓")


def uninstall_all(project_root: Path, frontend_dir: Path, pip: str) -> None:
    """卸载：删除包 + node_modules + 构建产物。"""
    header("卸载")

    warn("删除 Python 包 ...")
    subprocess.run(
        [*pip.split(), "uninstall", "-y", "quantnodes-strategy-research"],
        capture_output=True,
    )

    warn("删除前端 node_modules ...")
    if (frontend_dir / "node_modules").exists():
        shutil.rmtree(frontend_dir / "node_modules")

    warn("删除前端构建产物 ...")
    static_dir = project_root / "webui" / "static"
    if static_dir.exists():
        shutil.rmtree(static_dir)

    log("卸载完成 ✓")


def show_usage() -> None:
    """显示使用说明。"""
    header("使用说明")

    print("""✅ 安装完成！

📦 启动 Web UI（推荐）：
   quantnodes-strategy-research serve --host 0.0.0.0 --port 87183
   # 浏览器访问 http://localhost:87183

📦 启动 TUI（终端界面）：
   quantnodes-strategy-research

📦 启动纯 API：
   quantnodes-strategy-research api serve --port 8765

🌍 环境变量（必填 OPENAI_API_KEY）：
   export OPENAI_API_KEY="sk-..."                  # LLM API key
   export OPENAI_BASE_URL="https://api.openai.com/v1"  # 可选：自定义 endpoint
   export OPENAI_MODEL="gpt-4o-mini"                   # 可选：默认模型
   export CORS_ORIGINS="http://localhost:3000"         # 可选：CORS
   export STATIC_DIR="/path/to/webui/static"           # 可选：自定义前端路径

🧪 运行测试：
   pytest tests/test_webui_api.py tests/test_webui_e2e.py -v   # 后端 22 测试
   cd webui/frontend && npm test                                 # 前端 27 测试

🛠  开发模式：
   后端: quantnodes-strategy-research serve --reload --port 87183
   前端: cd webui/frontend && npm run dev   # http://localhost:5173
""")


# ============================================================
# CLI
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Strategy Research 一键安装脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dev", action="store_true",
        help="开发模式（不构建前端）",
    )
    parser.add_argument(
        "--frontend", action="store_true",
        help="仅构建前端",
    )
    parser.add_argument(
        "--backend", action="store_true",
        help="仅安装后端依赖",
    )
    parser.add_argument(
        "--uninstall", action="store_true",
        help="卸载",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).parent.resolve()
    project_root = script_dir
    frontend_dir = project_root / "webui" / "frontend"
    pip = get_pip()

    # 确定模式
    if args.uninstall:
        uninstall_all(project_root, frontend_dir, pip)
        return 0

    if args.backend:
        install_backend(project_root, pip)
        return 0

    if args.frontend:
        install_frontend_deps(frontend_dir)
        build_frontend(frontend_dir)
        return 0

    if args.dev:
        install_backend(project_root, pip)
        install_frontend_deps(frontend_dir)
        show_usage()
        return 0

    # 默认：完整安装
    install_backend(project_root, pip)
    install_frontend_deps(frontend_dir)
    build_frontend(frontend_dir)
    show_usage()
    return 0


if __name__ == "__main__":
    sys.exit(main())