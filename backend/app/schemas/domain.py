"""K 线、Ticker、OI、Signal 与配置的 Pydantic 领域结构。"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


# 各 K 线周期默认使用同长度的 OI 回看窗口，使成交量与持仓变化处于一致时间尺度。
DEFAULT_OI_LOOKBACK_MINUTES_BY_TIMEFRAME = {
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}
OILookbackMinutes = Annotated[int, Field(ge=5, le=10080)]


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


class FundingRateData(BaseModel):
    """永续合约最近一次资金费率快照。"""
    symbol: str
    funding_rate: Decimal


class OIPoint(BaseModel):
    """带时间戳的 Open Interest 观察点。"""
    timestamp: datetime
    open_interest: Decimal


class ConfigValues(BaseModel):
    """已校验的完整扫描配置；OI 窗口按 K 线周期独立设置。"""
    timeframes: list[str] = ["15m", "30m", "1h", "4h", "1d"]
    min_24h_quote_volume: Decimal = Decimal("10000000")
    volume_ema_period: int = Field(default=12, ge=2, le=100)
    volume_multiplier: Decimal = Field(default=Decimal("1.5"), ge=Decimal("1"), le=Decimal("100"))
    min_progress_percent: Decimal = Field(default=Decimal("10"), ge=Decimal("0"), le=Decimal("100"))
    oi_lookback_minutes_by_timeframe: dict[str, OILookbackMinutes] = Field(
        default_factory=lambda: DEFAULT_OI_LOOKBACK_MINUTES_BY_TIMEFRAME.copy()
    )
    oi_change_threshold_percent: Decimal = Field(default=Decimal("0.05"), ge=Decimal("0"), le=Decimal("100"))
    scan_interval_minutes: int = Field(default=5, ge=1, le=60)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_oi_lookback(cls, data):
        """把旧版全局 OI 窗口迁移为逐周期配置，并保留用户的非默认自定义值。"""
        if not isinstance(data, dict) or "oi_lookback_minutes_by_timeframe" in data:
            return data
        values = dict(data)
        legacy = values.pop("oi_lookback_minutes", None)
        timeframes = values.get("timeframes", ["15m", "30m", "1h", "4h", "1d"])
        if legacy is not None and int(legacy) != 15:
            # 旧配置若被用户主动修改过，则升级后继续对所有周期使用该值。
            values["oi_lookback_minutes_by_timeframe"] = {
                timeframe: int(legacy) for timeframe in timeframes
            }
        else:
            values["oi_lookback_minutes_by_timeframe"] = {
                timeframe: DEFAULT_OI_LOOKBACK_MINUTES_BY_TIMEFRAME.get(timeframe, 15)
                for timeframe in timeframes
            }
        return values

    def oi_lookback_for(self, timeframe: str) -> int:
        """返回指定 K 线周期的 OI 回看分钟数，未知周期使用 15 分钟兜底。"""
        return self.oi_lookback_minutes_by_timeframe.get(
            timeframe,
            DEFAULT_OI_LOOKBACK_MINUTES_BY_TIMEFRAME.get(timeframe, 15),
        )


class ConfigUpdate(BaseModel):
    """配置接口允许局部修改的字段，包括逐 K 线周期的 OI 回看窗口。"""
    min_24h_quote_volume: Decimal | None = Field(default=None, ge=0)
    volume_ema_period: int | None = Field(default=None, ge=2, le=100)
    volume_multiplier: Decimal | None = Field(default=None, ge=1, le=100)
    min_progress_percent: Decimal | None = Field(default=None, ge=0, le=100)
    oi_lookback_minutes_by_timeframe: dict[str, OILookbackMinutes] | None = None
    oi_change_threshold_percent: Decimal | None = Field(default=None, ge=0, le=100)
    scan_interval_minutes: int | None = Field(default=None, ge=1, le=60)


class SignalRead(BaseModel):
    """API 和 WebSocket 输出的不可变 Signal 快照。"""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    symbol: str
    is_tradfi: bool = False
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
    oi_lookback_minutes: int | None
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


class ActiveSymbolRead(BaseModel):
    """永续合约交易对、活跃池状态及最近一次市场快照。"""
    model_config = ConfigDict(from_attributes=True)
    symbol: str
    base_asset: str
    quote_asset: str
    contract_type: str
    is_active: bool
    last_price: Decimal | None
    price_change_percent_24h: Decimal | None
    quote_volume_24h: Decimal | None
    funding_rate: Decimal | None
    updated_at: datetime
