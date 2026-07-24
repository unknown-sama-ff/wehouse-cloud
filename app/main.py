"""
FastAPI 主入口
- 挂载 /api/houses 路由
- 健康检查 GET /health
- CORS 中间件
- 启动时自动建表（失败不崩，日志记录）
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db, is_db_available
from app.routers.houses import router as houses_router
from app.routers.parse import router as parse_router

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def _parse_cors_origins(raw: str) -> list[str]:
    raw = (raw or "").strip()
    if not raw or raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时建表（失败不抛异常，容错）"""
    try:
        init_db()
        if is_db_available():
            logger.info("✅ 数据库初始化完成，连接可用")
        else:
            logger.warning(
                "⚠️  数据库初始化完成但连接不可用，请检查："
                "1) Railway Variables DATABASE_URL 凭据 2) Railway IPv6 是否已开启 "
                "3) Supabase Network Restrictions 是否允许 IPv6 或 IPv4 出口"
            )
    except Exception as e:
        # 绝对不抛异常，保证应用一定起来（哪怕 DB 坏了，/health 依然 200）
        logger.exception("❌ 数据库初始化失败（不影响应用启动）: %s", e)
    yield


app = FastAPI(
    title="WeHouse 云端房源管理 API",
    description="微信房产消息自动采集系统的云端后端",
    version="1.0.0",
    lifespan=lifespan,
)

cors_origins = _parse_cors_origins(os.getenv("CORS_ORIGINS", "*"))
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=(cors_origins != ["*"]),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(houses_router)
app.include_router(parse_router)


@app.get("/health", tags=["system"])
def health_check() -> dict:
    """
    简单健康检查接口（永远 200，除非进程死了）
    返回 db_status 字段可以一眼看出 DB 是否通
    """
    db_ok = is_db_available()
    return {
        "status": "ok",
        "service": "wehouse-cloud",
        "db_status": "connected" if db_ok else "unavailable",
    }
