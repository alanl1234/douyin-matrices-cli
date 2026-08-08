@echo off
REM ═══════════════════════════════════════════════════════
REM  🎬 dy-cli Windows 安装脚本
REM ═══════════════════════════════════════════════════════

echo.
echo ╔═══════════════════════════════════╗
echo ║  🎬 dy-cli 一键安装               ║
echo ║  抖音命令行工具                   ║
echo ╚═══════════════════════════════════╝
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ✗ 需要 Python 3.10+，请先安装
    exit /b 1
)
echo ✓ Python 已安装

REM Create venv
if not exist ".venv" (
    echo ℹ 创建虚拟环境...
    python -m venv .venv
)
echo ✓ 虚拟环境已创建

REM Activate and install
call .venv\Scripts\activate.bat
pip install -e . --quiet
echo ✓ 依赖安装完成

REM Install Playwright Chromium
echo ℹ 安装 Playwright Chromium...
python -m playwright install chromium
echo ✓ Playwright Chromium 已安装

REM Verify
dy --version
if errorlevel 1 (
    echo ✗ dy 命令安装失败
    exit /b 1
)

echo.
echo ════════════════════════════════════
echo   ✅ 安装完成!
echo ════════════════════════════════════
echo.
echo   接下来:
echo     .venv\Scripts\activate.bat
echo     dy init
echo.
