"""
Railway 启动器：根据环境变量 SERVICE_ROLE 选择启动方式
用纯 Python 实现，不依赖 shell 脚本（避免 CRLF、case 分支等新手坑）
用法：
  python run.py            # 默认 both 模式（SERVICE_ROLE 环境变量决定，默认 both）
  python run.py api        # 只起 API（监听 $PORT 或 8000）
  python run.py ui         # 只起 Streamlit（监听 $PORT 或 8000）
  python run.py both       # API($PORT/8000) + Streamlit(8501)
"""
import os
import sys
import time
import subprocess
from pathlib import Path


THIS_DIR = Path(__file__).resolve().parent


def _role_from_env() -> str:
    raw = (os.getenv("SERVICE_ROLE") or "both").strip().lower()
    if raw in {"api", "ui", "both"}:
        return raw
    print(f"[run.py] WARNING: SERVICE_ROLE='{raw}' 无效，回退为 both")
    return "both"


def _port() -> int:
    try:
        return int(os.getenv("PORT", "8000"))
    except Exception:
        return 8000


def _run_api(port: int) -> subprocess.Popen:
    cmd = [
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", "0.0.0.0", "--port", str(port),
        "--log-level", "info",
    ]
    print(f"[run.py] 启动 API (uvicorn) on :{port}  cmd={' '.join(cmd)}")
    return subprocess.Popen(cmd, cwd=str(THIS_DIR))


def _run_ui(port: int) -> subprocess.Popen:
    cmd = [
        sys.executable, "-m", "streamlit", "run",
        str(THIS_DIR / "frontend" / "streamlit_app.py"),
        "--server.port", str(port),
        "--server.address", "0.0.0.0",
        "--server.headless", "true",
        "--server.enableXsrfProtection", "false",
        "--browser.gatherUsageStats", "false",
    ]
    print(f"[run.py] 启动 Streamlit UI on :{port}  cmd={' '.join(cmd)}")
    return subprocess.Popen(cmd, cwd=str(THIS_DIR))


def main() -> int:
    if len(sys.argv) > 1:
        role = sys.argv[1].strip().lower()
        if role not in {"api", "ui", "both"}:
            print(f"[run.py] ERROR: 参数必须是 api | ui | both，收到: {sys.argv[1]}")
            return 2
    else:
        role = _role_from_env()

    port = _port()
    print(f"[run.py] SERVICE_ROLE={role}, PORT={port}, cwd={THIS_DIR}")

    if role == "api":
        proc = _run_api(port)
        try:
            return proc.wait()
        finally:
            proc.kill() if proc.poll() is None else None

    if role == "ui":
        proc = _run_ui(port)
        try:
            return proc.wait()
        finally:
            proc.kill() if proc.poll() is None else None

    # both 模式：API($PORT/8000) + UI(8501)
    p_api = _run_api(port)
    time.sleep(2)
    p_ui = _run_ui(8501)
    try:
        while True:
            if p_api.poll() is not None:
                print(f"[run.py] API 进程退出 code={p_api.returncode}，整体退出")
                return p_api.returncode or 0
            if p_ui.poll() is not None:
                print(f"[run.py] UI 进程退出 code={p_ui.returncode}，整体退出")
                return p_ui.returncode or 0
            time.sleep(1)
    finally:
        for p in (p_api, p_ui):
            try:
                if p and p.poll() is None:
                    p.terminate()
                    p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
