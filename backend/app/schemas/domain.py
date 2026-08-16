"""K 线、Ticker、OI、Signal 与配置的 Pydantic 领域结构。"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class KlineData(BaseModel):
    """缓存和采集层通用 K 线数据。"""
    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    is_closed: bool


class TickerData(BaseModel):
    """交易池过滤与 Signal 快照使用的 24h ticker。"""
    symbol: str
    last_price: Decimal
    price_change_percent: Decimal
    quote_volume: Decimal


class OIPoint(BaseModel):
    """带时间戳的 Open Interest 观察点。"""
    timestamp: datetime
    open_interest: Decimal


class ConfigValues(BaseModel):
    """已校验的完整扫描配置及文档默认值。"""
    timeframes: list[str] = ["15m", "30m", "1h", "4h", "1d"]
    min_24h_quote_volume: Decimal = Decimal("10000000")
    volume_ema_period: int = Field(default=12, ge=2, le=100)
    volume_multiplier: Decimal = Field(default=Decimal("1.5"), ge=Decimal("1"), le=Decimal("100"))
    min_progress_percent: Decimal = Field(default=Decimal("10"), ge=Decimal("0"), le=Decimal("100"))
    oi_change_threshold_percent: Decimal = Field(default=Decimal("0.05"), ge=Decimal("0"), le=Decimal("100"))
    scan_interval_minutes: int = Field(default=5, ge=1, le=60)


class ConfigUpdate(BaseModel):
    """配置接口允许局部修改的字段。"""
    min_24h_quote_volume: Decimal | None = Field(default=None, ge=0)
    volume_ema_period: int | None = Field(default=None, ge=2, le=100)
    volume_multiplier: Decimal | None = Field(default=None, ge=1, le=100)
    min_progress_percent: Decimal | None = Field(default=None, ge=0, le=100)
    oi_change_threshold_percent: Decimal | None = Field(default=None, ge=0, le=100)
    scan_interval_minutes: int | None = Field(default=None, ge=1, le=60)


class SignalRead(BaseModel):
    """API 和 WebSocket 输出的不可变 Signal 快照。"""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    symbol: str
    timeframe: str
    detected_at: datetime
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    current_price: Decimal
    current_volume: Decimal
    current_quote_volume: Decimal
    progress_percent: Decimal
    estimated_volume: Decimal
    volume_ema: Decimal
    volume_ema_period: int
    volume_ratio: Decimal
    volume_multiplier: Decimal
    ema14: Decimal | None
    ema50: Decimal | None
    rsi14: Decimal | None
    adx14: Decimal | None
    atr14: Decimal | None
    adx_slope: Decimal | None
    ema14_slope_percent: Decimal | None
    ema50_slope_percent: Decimal | None
    oldest_oi: Decimal
    newest_oi: Decimal
    oi_change_absolute: Decimal
    oi_change_percent: Decimal
    oldest_timestamp: datetime
    newest_timestamp: datetime
    last_price: Decimal
    price_change_percent_24h: Decimal
    quote_volume_24h: Decimal
    signal_type: str


class SignalPage(BaseModel):
    """Signal 列表的分页响应。"""
    items: list[SignalRead]
    total: int
    page: int
    page_size: int


class SignalChartCandle(BaseModel):
    """TradingView 图表使用的单根 K 线及对应 EMA 值。"""
    time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    ema14: Decimal | None = None
    ema50: Decimal | None = None
    is_signal: bool = False


class SignalChartData(BaseModel):
    """Signal 检测时刻的无未来数据图表快照。"""
    symbol: str
    timeframe: str
    signal_time: int
    signal_open_time: int
    candles: list[SignalChartCandle]


class RealtimeChartData(BaseModel):
    """当前市场图表窗口，包含最新完整 K 线和未收盘 K 线。"""
    symbol: str
    timeframe: str
    candles: list[SignalChartCandle]


class DashboardStats(BaseModel):
    """前端运行概览的聚合指标。"""
    total_symbols: int
    active_symbols: int
    websocket_status: str
    last_scan_at: datetime | None
    last_scan_duration_ms: int | None
    today_signal_count: int
    current_signal_count: int
