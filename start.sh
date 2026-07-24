#!/bin/sh
# WeHouse 云端启动脚本
# 根据环境变量 SERVICE_ROLE 启动不同组件：
#   SERVICE_ROLE=api   -> 仅启动 FastAPI（监听 $PORT，默认 8000）
#   SERVICE_ROLE=ui    -> 仅启动 Streamlit（监听 $PORT，适配 Railway 单端口暴露）
#   SERVICE_ROLE=both  -> 在一台机器上同时起 API($PORT) + Streamlit(8501，内网可用)
# 默认：both

set -e

ROLE="${SERVICE_ROLE:-both}"
PORT="${PORT:-8000}"

echo "[start.sh] SERVICE_ROLE=${ROLE}, PORT=${PORT}"

case "$ROLE" in
  api)
    echo "[start.sh] 启动 FastAPI (uvicorn) on :${PORT}"
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
    ;;
  ui)
    echo "[start.sh] 启动 Streamlit on :${PORT}"
    exec streamlit run frontend/streamlit_app.py \
      --server.port "${PORT}" \
      --server.address 0.0.0.0 \
      --server.headless true \
      --server.enableXsrfProtection false
    ;;
  both)
    echo "[start.sh] 同时启动 FastAPI(:${PORT}) + Streamlit(:8501)"
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
    echo "[start.sh] 错误：未知 SERVICE_ROLE='${ROLE}'，可选: api | ui | both" >&2
    exit 1
    ;;
esac
