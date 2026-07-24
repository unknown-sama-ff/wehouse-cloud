"""
数据库连接与 Session 管理
基于 SQLAlchemy 连接 Supabase PostgreSQL

【容错优化】
- 如果环境变量 DATABASE_URL 未设置 OR 初始化连不上 DB：不 raise，降级为 None
- 应用仍然可以正常启动，/health 返回 ok
- 后续真正访问数据库的接口会返回 503（提示 DATABASE_URL 有问题）
- 这样 Railway 部署时不会因为 DB 还没配好而直接崩掉（Healthcheck 过不了）
"""

import logging
import os
import time
from typing import Generator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

logger = logging.getLogger(__name__)

_DATABASE_URL = os.getenv("DATABASE_URL")
_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))

engine = None
SessionLocal = None
_DATABASE_URL_EFFECTIVE: Optional[str] = None


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""


# ————————————————————————————————————————————————————————————
# 初始化（允许失败，不抛异常）
# ————————————————————————————————————————————————————————————
def _initialize_engine() -> None:
    global engine, SessionLocal, _DATABASE_URL_EFFECTIVE
    if not _DATABASE_URL:
        logger.warning("⚠️  环境变量 DATABASE_URL 未设置，数据库功能降级为不可用")
        return

    _connect_args = {"connect_timeout": _CONNECT_TIMEOUT}
    if _DATABASE_URL.startswith("sqlite"):
        _connect_args = {"check_same_thread": False}

    try:
        engine = create_engine(
            _DATABASE_URL,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=300,
            connect_args=_connect_args,
        )
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        _DATABASE_URL_EFFECTIVE = _DATABASE_URL
        logger.info("✅ SQLAlchemy engine 创建成功 (DATABASE_URL 已读取)")
    except Exception as e:
        logger.exception("❌ 创建数据库 engine 失败: %s", e)
        engine = None
        SessionLocal = None


_initialize_engine()


def is_db_available() -> bool:
    """健康检查 / 路由 里可以用来判断 DB 是否可用"""
    if engine is None:
        return False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI 依赖注入：提供数据库 Session
    如果 DB 不可用，抛出 HTTP 503，让调用方友好提示而不是 500
    """
    if SessionLocal is None or engine is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail=(
                "Database not available. "
                "请检查 Railway Variables 里 DATABASE_URL 是否正确配置，"
                "以及 Supabase 侧 IPv4 / Network Restrictions 是否允许 Railway 出口 IP 访问 5432。"
            ),
        )
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """
    应用启动时创建所有表
    ⚠️  不会抛出异常！如果 DB 连不上，只是打日志跳过，应用依然可以正常启动和监听端口
    """
    import app.models  # noqa: F401  触发模型注册

    if engine is None:
        logger.warning("⏭️  跳过 init_db(): engine 未初始化（DATABASE_URL 可能有问题，见上方日志）")
        return

    deadline = time.time() + _CONNECT_TIMEOUT
    last_err: Optional[Exception] = None
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            Base.metadata.create_all(bind=engine)
            logger.info("✅ init_db() 数据库表初始化成功，尝试次数: %d", attempt)
            return
        except SQLAlchemyError as e:
            last_err = e
            logger.warning(
                "⚠️  init_db() 第 %d 次建表失败（%.1fs 后重试）: %s",
                attempt,
                2.0,
                str(e).splitlines()[0][:200],
            )
            time.sleep(2.0)

    logger.error(
        "❌ init_db() 建表超时 %.1fs，放弃。Supabase DB 可能网络不通或凭据错误: %s",
        _CONNECT_TIMEOUT,
        str(last_err).splitlines()[0][:500] if last_err else "unknown",
    )
