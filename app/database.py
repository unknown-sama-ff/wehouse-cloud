"""
数据库连接与 Session 管理
基于 SQLAlchemy 连接 Supabase PostgreSQL
"""

import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

_DATABASE_URL = os.getenv("DATABASE_URL")
if not _DATABASE_URL:
    raise RuntimeError("环境变量 DATABASE_URL 未设置，无法连接数据库")

_connect_args = {}
if _DATABASE_URL.startswith("sqlite"):
    _connect_args["check_same_thread"] = False

engine = create_engine(
    _DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=300,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖注入：提供数据库 Session，保证结束后关闭"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """
    应用启动时创建所有表
    生产环境建议使用 Alembic 等迁移工具，这里提供自动化建表以便一键部署
    """
    import app.models  # noqa: F401  触发模型注册

    Base.metadata.create_all(bind=engine)
