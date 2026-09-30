@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [*] 正在创建 Python 虚拟环境并安装打包依赖...
    python -m venv .venv
    if errorlevel 1 (
        echo [!] 未检测到 Python，请先安装 Python 3.11+。
        pause
        exit /b 1
    )
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)

".venv\Scripts\python.exe" "scripts\build-windows.py"
if errorlevel 1 (
    echo [!] 打包失败，请检查上方错误输出。
    pause
    exit /b 1
)

echo.
echo [*] 打包完成！输出目录：dist-win\美客多活动管家\美客多活动管家.exe
pause
