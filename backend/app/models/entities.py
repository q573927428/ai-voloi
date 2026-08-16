"""监控平台全部 PostgreSQL 表模型与关系约束。"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, utc_now

NUM = Numeric(36, 12)


class Symbol(Base, TimestampMixin):
    """Binance 永续合约及最近一次 24h 市场快照。"""
    __tablename__ = "symbols"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    base_asset: Mapped[str] = mapped_column(String(20))
    quote_asset: Mapped[str] = mapped_column(String(20), default="USDT")
    contract_type: Mapped[str] = mapped_column(String(20), default="PERPETUAL")
    status: Mapped[str] = mapped_column(String(20), default="TRADING")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_price: Mapped[Decimal | None] = mapped_column(NUM)
    price_change_percent_24h: Mapped[Decimal | None] = mapped_column(NUM)
    quote_volume_24h: Mapped[Decimal | None] = mapped_column(NUM, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Kline(Base, TimestampMixin):
    """按交易对、周期和开盘时间唯一保存的历史 K 线。"""
    __tablename__ = "klines"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "open_time", name="uq_kline_identity"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("symbols.symbol", ondelete="CASCADE"), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), index=True)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[Decimal] = mapped_column(NUM)
    high: Mapped[Decimal] = mapped_column(NUM)
    low: Mapped[Decimal] = mapped_column(NUM)
    close: Mapped[Decimal] = mapped_column(NUM)
    volume: Mapped[Decimal] = mapped_column(NUM)
    quote_volume: Mapped[Decimal] = mapped_column(NUM)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=True)


class OpenInterestSnapshot(Base, TimestampMixin):
    """成交量候选在 OI 判断时使用的原始观察点。"""
    __tablename__ = "open_interest_snapshots"
    __table_args__ = (Index("ix_oi_symbol_timeframe_timestamp", "symbol", "timeframe", "timestamp"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("symbols.symbol", ondelete="CASCADE"))
    timeframe: Mapped[str] = mapped_column(String(8))
    open_interest: Mapped[Decimal] = mapped_column(NUM)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Signal(Base, TimestampMixin):
    """Signal 生成时的不可变完整市场与阈值快照。"""
    __tablename__ = "signals"
    __table_args__ = (Index("ix_signal_detected_symbol", "detected_at", "symbol"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    symbol: Mapped[str] = mapped_column(ForeignKey("symbols.symbol"), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[Decimal] = mapped_column(NUM)
    high: Mapped[Decimal] = mapped_column(NUM)
    low: Mapped[Decimal] = mapped_column(NUM)
    current_price: Mapped[Decimal] = mapped_column(NUM)
    current_volume: Mapped[Decimal] = mapped_column(NUM)
    current_quote_volume: Mapped[Decimal] = mapped_column(NUM)
    progress_percent: Mapped[Decimal] = mapped_column(NUM)
    estimated_volume: Mapped[Decimal] = mapped_column(NUM)
    volume_ema: Mapped[Decimal] = mapped_column(NUM)
    volume_ema_period: Mapped[int] = mapped_column(Integer)
    volume_ratio: Mapped[Decimal] = mapped_column(NUM, index=True)
    volume_multiplier: Mapped[Decimal] = mapped_column(NUM)
    ema14: Mapped[Decimal | None] = mapped_column(NUM)
    ema50: Mapped[Decimal | None] = mapped_column(NUM)
    rsi14: Mapped[Decimal | None] = mapped_column(NUM)
    adx14: Mapped[Decimal | None] = mapped_column(NUM)
    atr14: Mapped[Decimal | None] = mapped_column(NUM)
    adx_slope: Mapped[Decimal | None] = mapped_column(NUM)
    ema14_slope_percent: Mapped[Decimal | None] = mapped_column(NUM)
    ema50_slope_percent: Mapped[Decimal | None] = mapped_column(NUM)
    oldest_oi: Mapped[Decimal] = mapped_column(NUM)
    newest_oi: Mapped[Decimal] = mapped_column(NUM)
    oi_change_absolute: Mapped[Decimal] = mapped_column(NUM)
    oi_change_percent: Mapped[Decimal] = mapped_column(NUM, index=True)
    # 旧 Signal 使用“K线开盘至检测时”的口径，因此迁移后保持 NULL，避免伪造历史参数。
    oi_lookback_minutes: Mapped[int | None] = mapped_column(Integer)
    oldest_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    newest_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_price: Mapped[Decimal] = mapped_column(NUM)
    price_change_percent_24h: Mapped[Decimal] = mapped_column(NUM)
    quote_volume_24h: Mapped[Decimal] = mapped_column(NUM)
    signal_type: Mapped[str] = mapped_column(String(40), default="VOLUME_OI_ANOMALY")
    future_performance: Mapped["SignalFuturePerformance | None"] = relationship(back_populates="signal", uselist=False)


class SignalFuturePerformance(Base, TimestampMixin):
    """Signal 在各观察周期的未来收益与区间盈亏。"""
    __tablename__ = "signal_future_performance"
    signal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("signals.id", ondelete="CASCADE"), primary_key=True)
    return_5m: Mapped[Decimal | None] = mapped_column(NUM)
    return_15m: Mapped[Decimal | None] = mapped_column(NUM)
    return_30m: Mapped[Decimal | None] = mapped_column(NUM)
    return_1h: Mapped[Decimal | None] = mapped_column(NUM)
    return_4h: Mapped[Decimal | None] = mapped_column(NUM)
    return_1d: Mapped[Decimal | None] = mapped_column(NUM)
    max_profit_percent: Mapped[Decimal | None] = mapped_column(NUM)
    max_loss_percent: Mapped[Decimal | None] = mapped_column(NUM)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    signal: Mapped[Signal] = relationship(back_populates="future_performance")


class ScannerRun(Base):
    """单次扫描的工作量、耗时和异常审计记录。"""
    __tablename__ = "scanner_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    symbol_count: Mapped[int] = mapped_column(Integer, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    oi_request_count: Mapped[int] = mapped_column(Integer, default=0)
    signal_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)


class SystemConfig(Base):
    """可动态更新的业务配置 JSON 文档。"""
    __tablename__ = "system_config"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
