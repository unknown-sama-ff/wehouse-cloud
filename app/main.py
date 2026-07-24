"""
FastAPI 主入口
- 挂载 /api/houses 路由
- 健康检查 GET /health
- CORS 中间件
- 启动时自动建表
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.routers.houses import router as houses_router
from app.routers.parse import router as parse_router


def _parse_cors_origins(raw: str) -> list[str]:
    raw = (raw or "").strip()
    if not raw or raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时建表"""
    try:
        init_db()
        logging.getLogger(__name__).info("数据库初始化完成")
    except Exception as e:
        logging.getLogger(__name__).exception("数据库初始化失败: %s", e)
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
    """简单健康检查接口"""
    return {"status": "ok", "service": "wehouse-cloud"}
