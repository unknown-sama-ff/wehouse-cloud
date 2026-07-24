"""
SQLAlchemy 数据模型
对应 Supabase PostgreSQL houses 表
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class House(Base):
    """房源主表"""

    __tablename__ = "houses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(255), nullable=False, comment="消息来源（群名/联系人）")
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, comment="原始消息全文")
    area: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="小区名称")
    size: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="面积，如 118.5平")
    price: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="总价，如 328万")
    layout: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="户型，如 三室两厅")
    floor: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="楼层，如 15/28")
    contact: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="联系人与电话")
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        server_default="active",
        comment="状态：active 在售 / sold 已售 / expired 过期 / deleted 已删除",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=func.now(),
        onupdate=func.now(),
    )
