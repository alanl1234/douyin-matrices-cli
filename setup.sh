#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════
#  🎬 dy-cli 一键安装脚本
#  用法: bash setup.sh
# ═══════════════════════════════════════════════════════
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${CYAN}ℹ${NC} $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }
warn()    { echo -e "${YELLOW}⚠${NC} $1"; }
fail()    { echo -e "${RED}✗${NC} $1"; exit 1; }

echo ""
echo -e "${BOLD}╔═══════════════════════════════════╗${NC}"
echo -e "${BOLD}║  🎬 dy-cli 一键安装               ║${NC}"
echo -e "${BOLD}║  抖音命令行工具                   ║${NC}"
echo -e "${BOLD}╚═══════════════════════════════════╝${NC}"
echo ""

# ── 1. 检测 Python ──────────────────────────────────
info "检查 Python..."
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    fail "需要 Python 3.10+，请先安装: brew install python3"
fi
success "Python: $($PYTHON --version)"

# ── 2. 定位项目目录 ──────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
    PROJECT_DIR="$SCRIPT_DIR"
else
    PROJECT_DIR="$(pwd)"
fi

if [ ! -f "$PROJECT_DIR/pyproject.toml" ]; then
    fail "找不到 pyproject.toml，请在项目根目录运行此脚本"
fi
cd "$PROJECT_DIR"
success "项目目录: $PROJECT_DIR"

# ── 3. 创建虚拟环境 ──────────────────────────────────
VENV_DIR="$PROJECT_DIR/.venv"
if [ -d "$VENV_DIR" ] && { [ -f "$VENV_DIR/bin/activate" ] || [ -f "$VENV_DIR/Scripts/activate" ]; }; then
    success "虚拟环境已存在: $VENV_DIR"
else
    info "创建虚拟环境..."
    $PYTHON -m venv "$VENV_DIR"
    success "虚拟环境已创建"
fi

# ── 4. 激活并安装依赖 ────────────────────────────────
info "安装依赖..."
if [ -f "$VENV_DIR/Scripts/activate" ]; then
    source "$VENV_DIR/Scripts/activate"
else
    source "$VENV_DIR/bin/activate"
fi
pip install -e . --quiet 2>&1 | tail -1
success "依赖安装完成"

# ── 5. 验证 dy 命令 ─────────────────────────────────
if command -v dy &>/dev/null; then
    success "dy 命令可用: $(dy --version 2>&1)"
else
    fail "dy 命令安装失败"
fi

# ── 6. 安装 Playwright Chromium ──────────────────────
info "安装 Playwright Chromium (首次需要下载)..."
if $PYTHON -m playwright install chromium 2>/dev/null; then
    success "Playwright Chromium 已安装"
else
    warn "Playwright Chromium 安装失败，稍后手动运行: playwright install chromium"
fi

# ── 7. 生成激活脚本 ──────────────────────────────────
ACTIVATE_SCRIPT="$PROJECT_DIR/activate.sh"
cat > "$ACTIVATE_SCRIPT" << 'EOF'
#!/usr/bin/env bash
# 激活 dy-cli 环境
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/.venv/Scripts/activate" ]; then
    source "$SCRIPT_DIR/.venv/Scripts/activate"
elif [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
else
    echo "❌ 虚拟环境未找到，请先运行: bash setup.sh"
    return 1 2>/dev/null || exit 1
fi
echo "🎬 dy-cli 环境已激活，输入 dy --help 开始使用"
EOF
chmod +x "$ACTIVATE_SCRIPT"

# ── 完成 ─────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ 安装完成!${NC}"
echo -e "${GREEN}════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}接下来:${NC}"
echo ""
echo -e "  ${CYAN}# 方式一: 直接初始化 (推荐)${NC}"
echo -e "  source activate.sh && dy init"
echo ""
echo -e "  ${CYAN}# 方式二: 手动激活环境后使用${NC}"
if [ -f "$VENV_DIR/Scripts/activate" ]; then
echo -e "  source .venv/Scripts/activate"
else
echo -e "  source .venv/bin/activate"
fi
echo -e "  dy init"
echo ""
echo -e "  ${CYAN}# 以后每次使用前激活环境:${NC}"
echo -e "  source activate.sh"
echo ""
