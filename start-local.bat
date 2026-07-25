@echo off
chcp 65001 >nul
title WeHouse Cloud 本地启动 (FastAPI + Streamlit)

REM ============================================================
REM 【用途】在本地电脑一键启动 wehouse-cloud（不用每次手动敲环境变量）
REM 【等价于】Railway Variables 在本地的临时副本
REM 【什么时候用】想改 wehouse-cloud 代码、先本地测通了再 push GitHub 时才用
REM 【日常不用】平时跑采集端只要开 wehouse\start.bat + 用 Railway 两个服务域名
REM ============================================================

cd /d "%~dp0"

echo ========================================
echo   WeHouse Cloud 本地启动（Both 模式）
echo   FastAPI  :8000  +  Streamlit :8501
echo ========================================
echo.

REM ============================================================
REM 1. 数据库（直接连你 Railway 正在用的 Supabase，这样本地和云端数据一致）
REM ============================================================
set DATABASE_URL=postgresql://postgres:100121146fyhouse@db.mhdypielgsxyiejdabvd.supabase.co:5432/postgres
echo [OK] DATABASE_URL = Supabase (wehouse 项目)

REM ============================================================
REM 2. Token（必须和 Railway Variables + 本地 config.json 三处一样！）
REM ============================================================
set SECRET_TOKEN=100121146fy
set WRITE_TOKEN=100121146fy
set READONLY_PASSWORD=
echo [OK] SECRET_TOKEN / WRITE_TOKEN = %SECRET_TOKEN%

REM ============================================================
REM 3. CORS（本地全放开）
REM ============================================================
set CORS_ORIGINS=*
echo [OK] CORS_ORIGINS = *

REM ============================================================
REM 4. 云端 LLM 解析（/api/parse 接口用）
REM    - 没填真 Key 也没关系，parse.py 会自动降级正则（永不 502）
REM ============================================================
set LLM_API_URL=https://api.moonshot.cn/v1/chat/completions
set LLM_API_KEY=sk-placeholder
set LLM_MODEL=kimi-latest
echo [OK] LLM_API_URL = %LLM_API_URL%  （LLM_API_KEY 是占位符，自动降级正则）

REM ============================================================
REM 5. UI 服务（Streamlit）用的 API_BASE
REM    - 本地 both 模式时 UI 连本地起的 FastAPI :8000
REM ============================================================
set API_BASE=http://localhost:8000
echo [OK] Streamlit API_BASE = http://localhost:8000

REM ============================================================
REM 6. 端口 + SERVICE_ROLE（默认 both）
REM ============================================================
set PORT=8000
set SERVICE_ROLE=both

echo.
echo ========================================
echo   启动中...
echo   API 接口（健康检查）: http://localhost:8000/health
echo   UI 管理后台         : http://localhost:8501
echo   关闭窗口或 Ctrl+C 即可停止
echo ========================================
echo.

REM ============================================================
REM 7. 确认 py 启动器存在，否则回退 python
REM ============================================================
set PY_CMD=
where py >nul 2>nul
if %errorlevel% equ 0 (
    set PY_CMD=py -3
) else (
    where python >nul 2>nul
    if %errorlevel% equ 0 (
        set PY_CMD=python
    ) else (
        echo [错误] 未检测到 Python，先安装官方 Python 3.9+ 并勾选 Add to PATH
        pause
        exit /b 1
    )
)

REM ============================================================
REM 8. 启动 Both 模式
REM ============================================================
%PY_CMD% run.py both

set EXIT=%errorlevel%
echo.
echo ========================================
echo 程序退出 code=%EXIT%
echo ========================================
pause
exit /b %EXIT%
