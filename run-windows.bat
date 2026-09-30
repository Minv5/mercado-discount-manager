@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [*] 首次运行，正在创建 Python 虚拟环境并安装依赖...
    python -m venv .venv
    if errorlevel 1 (
        echo [!] 未检测到 Python，请先前往 https://www.python.org 安装 Python 3.11+（勾选 Add Python to PATH），或直接从 GitHub Releases 下载免安装版 exe 压缩包。
        pause
        exit /b 1
    )
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)

start "" ".venv\Scripts\pythonw.exe" "desktop-pyside\app.py"
