"""数据库声明基类、通用时间字段与 UTC 时钟。"""

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """生成带时区的 UTC 时间，避免数据库出现本地时间歧义。"""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """全部 SQLAlchemy 表模型的声明基类。"""
    pass


class TimestampMixin:
    """为需要审计创建时间的表提供统一字段。"""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
