FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# PostgreSQL 客户端依赖（psycopg2-binary 需要 libpq）
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

# —— 先装依赖，缓存友好 ——
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# —— 拷代码（run.py 作为启动入口，比 shell 脚本更稳健）——
COPY . .

EXPOSE 8000 8501

# 默认：both 模式（同时起 API:8000 + UI:8501，不设 SERVICE_ROLE 也能跑）
# Railway 拆服务时，覆盖 Start Command 即可：
#   - API 服务:  python /app/run.py api
#   - UI  服务:  python /app/run.py ui
# SERVICE_ROLE 环境变量也支持（优先级低于命令行参数）
CMD ["python", "/app/run.py"]
