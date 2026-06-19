@echo off
chcp 65001 >nul
title 一维火驱物理模拟装置 - T9110
cd /d "%~dp0"

echo ========================================
echo  一维火驱物理模拟装置 - T9110
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python。
    echo 请先到 https://www.python.org 安装 Python，
    echo 安装时务必勾选 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)

echo [1/2] 检查并安装依赖（首次运行需要联网，之后可跳过）...
python -m pip install -r requirements.txt
echo.

echo [2/2] 启动程序...
python t9110_cloudmap.py

if errorlevel 1 (
    echo.
    echo [程序异常退出] 请把上面的报错信息截图发给开发者。
    pause
)
