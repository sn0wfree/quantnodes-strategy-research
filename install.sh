#!/usr/bin/env bash
# Strategy Research 一键安装脚本
#
# Usage:
#   ./install.sh                      # 完整安装（构建前端 + 后端）
#   ./install.sh --dev                # 开发模式（不构建前端）
#   ./install.sh --frontend           # 仅构建前端
#   ./install.sh --backend            # 仅安装后端依赖
#   ./install.sh --uninstall          # 卸载
#   ./install.sh --help               # 显示帮助
#
# 启动 Web UI:
#   quantnodes-strategy-research serve --host 0.0.0.0 --port 87183
#
# 访问 http://localhost:87183

set -e

# 默认参数
MODE="full"
SKIP_FRONTEND=false
SKIP_BACKEND=false

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()    { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()   { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()    { echo -e "${RED}[ERROR]${NC} $*"; }
header() { echo -e "\n${BLUE}=== $* ===${NC}"; }

# 解析脚本所在目录（兼容直接运行和软链）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PIP_BIN="${PIP_BIN:-pip3}"

# ============================================================
# 帮助
# ============================================================
show_help() {
    cat <<EOF
Strategy Research 一键安装脚本

Usage:
  $0 [options]

Options:
  (无参数)       完整安装：后端 + 前端依赖 + 构建前端
  --dev          开发模式：后端 + 前端依赖（不构建）
  --frontend     仅构建前端
  --backend      仅安装后端依赖
  --uninstall    卸载（删除包 + node_modules + 构建产物）
  --help         显示此帮助

Examples:
  $0                              # 完整安装
  $0 --dev                        # 开发模式
  quantnodes-strategy-research serve --host 0.0.0.0 --port 87183
EOF
}

# ============================================================
# 检查命令
# ============================================================
check_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        err "$1 未安装。请先安装 $1"
        exit 1
    fi
}

# ============================================================
# 安装后端
# ============================================================
install_backend() {
    header "安装后端依赖"
    check_command "$PYTHON_BIN"
    check_command "$PIP_BIN"

    log "Python: $($PYTHON_BIN --version)"
    log "pip:    $($PIP_BIN --version)"

    cd "$PROJECT_ROOT"
    log "安装包: api + dev + webui-api"
    $PIP_BIN install -e ".[api,dev,webui-api]"
    log "后端安装完成 ✓"
}

# ============================================================
# 安装前端依赖
# ============================================================
install_frontend_deps() {
    header "安装前端依赖"
    check_command npm

    cd "$PROJECT_ROOT/webui/frontend"
    log "npm: $(npm --version)"
    log "Node: $(node --version)"
    npm install --legacy-peer-deps
    log "前端依赖安装完成 ✓"
}

# ============================================================
# 构建前端
# ============================================================
build_frontend() {
    header "构建前端"
    cd "$PROJECT_ROOT/webui/frontend"
    log "运行 npm run build ..."
    npm run build
    log "前端构建完成 → webui/static/ ✓"
}

# ============================================================
# 卸载
# ============================================================
uninstall_all() {
    header "卸载"
    warn "删除 Python 包 ..."
    $PIP_BIN uninstall -y quantnodes-strategy-research 2>/dev/null || true

    warn "删除前端 node_modules ..."
    rm -rf "$PROJECT_ROOT/webui/frontend/node_modules"

    warn "删除前端构建产物 ..."
    rm -rf "$PROJECT_ROOT/webui/static"

    log "卸载完成 ✓"
}

# ============================================================
# 显示使用说明
# ============================================================
show_usage() {
    header "使用说明"

    cat <<EOF
✅ 安装完成！

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
EOF
}

# ============================================================
# 解析参数
# ============================================================
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            show_help
            exit 0
            ;;
        --dev)
            MODE="dev"
            shift
            ;;
        --frontend)
            MODE="frontend"
            shift
            ;;
        --backend)
            MODE="backend"
            shift
            ;;
        --uninstall)
            MODE="uninstall"
            shift
            ;;
        *)
            err "未知参数: $1"
            show_help
            exit 1
            ;;
    esac
done

# ============================================================
# 主入口
# ============================================================
case "$MODE" in
    uninstall)
        uninstall_all
        ;;
    backend)
        install_backend
        ;;
    frontend)
        install_frontend_deps
        build_frontend
        ;;
    dev)
        install_backend
        install_frontend_deps
        show_usage
        ;;
    full|"")
        install_backend
        install_frontend_deps
        build_frontend
        show_usage
        ;;
esac