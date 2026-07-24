#!/bin/sh
# WeHouse 云端启动脚本
# 根据环境变量 SERVICE_ROLE 启动不同组件：
#   SERVICE_ROLE=api   -> 仅启动 FastAPI（监听 $PORT，默认 8000）
#   SERVICE_ROLE=ui    -> 仅启动 Streamlit（监听 $PORT，适配 Railway 单端口暴露）
#   SERVICE_ROLE=both  -> 在一台机器上同时起 API($PORT) + Streamlit(8501，内网可用)
# 默认：both（不设置 SERVICE_ROLE 也能跑起来，便于测试）

set -e

# —— 兼容 Windows CRLF 换行符 ——
# 防止用户在 Windows 编辑脚本时引入 \r，导致 case 分支永远走不进去
# 同时 trim 前后空白 + 强制转小写，便于比较
ROLE_RAW="${SERVICE_ROLE:-both}"
ROLE=$(printf '%s' "$ROLE_RAW" | tr -d '\r' | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
PORT="${PORT:-8000}"

# 兜底：清洗后还是空，默认 both
if [ -z "$ROLE" ]; then
  ROLE="both"
fi

echo "[start.sh] SERVICE_ROLE(raw='${ROLE_RAW}', clean='${ROLE}'), PORT=${PORT}"

case "$ROLE" in
  api)
    echo "[start.sh] Starting FastAPI (uvicorn) on :${PORT}"
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
    ;;
  ui)
    echo "[start.sh] Starting Streamlit on :${PORT}"
    exec streamlit run frontend/streamlit_app.py \
      --server.port "${PORT}" \
      --server.address 0.0.0.0 \
      --server.headless true \
      --server.enableXsrfProtection false
    ;;
  both)
    echo "[start.sh] Starting FastAPI(:${PORT}) + Streamlit(:8501) simultaneously"
    uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" &
    PID_API=$!
    streamlit run frontend/streamlit_app.py \
      --server.port 8501 \
      --server.address 0.0.0.0 \
      --server.headless true \
      --server.enableXsrfProtection false &
    PID_UI=$!
    # 任一进程退出都结束整个脚本
    trap "kill ${PID_API} ${PID_UI} 2>/dev/null; exit 0" INT TERM
    wait -n "${PID_API}" "${PID_UI}"
    STATUS=$?
    kill ${PID_API} ${PID_UI} 2>/dev/null || true
    exit ${STATUS}
    ;;
  *)
    echo "[start.sh] WARNING: 未知 SERVICE_ROLE(raw='${ROLE_RAW}', clean='${ROLE}')，回退默认 both 模式" >&2
    # 不要 exit 1！回退到 both 模式保证先启动成功，避免 Healthcheck 无限失败
    uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" &
    PID_API=$!
    streamlit run frontend/streamlit_app.py \
      --server.port 8501 \
      --server.address 0.0.0.0 \
      --server.headless true \
      --server.enableXsrfProtection false &
    PID_UI=$!
    trap "kill ${PID_API} ${PID_UI} 2>/dev/null; exit 0" INT TERM
    wait -n "${PID_API}" "${PID_UI}"
    STATUS=$?
    kill ${PID_API} ${PID_UI} 2>/dev/null || true
    exit ${STATUS}
    ;;
esac
