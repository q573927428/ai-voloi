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
DEFAULT_OI_CHANGE_THRESHOLD_PERCENT = Decimal("0.05")
OILookbackMinutes = Annotated[int, Field(ge=5, le=10080)]
OIChangeThresholdPercent = Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("100"))]


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


class EmaIndicatorRead(BaseModel):
    """单个 EMA 的值、斜率及相对最新完整 K 线收盘价的距离。"""

    period: int
    value: Decimal | None
    slope_percent: Decimal | None
    distance_percent: Decimal | None


class AdxIndicatorRead(BaseModel):
    """Wilder ADX 趋势强度、方向分量及单根变化。"""

    period: int = 14
    value: Decimal | None
    plus_di: Decimal | None
    minus_di: Decimal | None
    slope: Decimal | None


class MacdIndicatorRead(BaseModel):
    """标准 MACD(12, 26, 9) 最新值。"""

    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9
    line: Decimal | None
    signal: Decimal | None
    histogram: Decimal | None


class AtrIndicatorRead(BaseModel):
    """Wilder ATR 原始值及相对收盘价百分比。"""

    period: int = 14
    value: Decimal | None
    percent: Decimal | None


class BollingerIndicatorRead(BaseModel):
    """布林带(20, 2) 边界、带宽百分比和价格位置。"""

    period: int = 20
    standard_deviations: Decimal = Decimal("2")
    upper: Decimal | None
    middle: Decimal | None
    lower: Decimal | None
    bandwidth_percent: Decimal | None
    percent_b: Decimal | None


class TrendIndicatorsRead(BaseModel):
    """趋势类指标集合，EMA 键为周期字符串。"""

    ema: dict[str, EmaIndicatorRead]
    ema_alignment: str
    adx: AdxIndicatorRead


class MomentumIndicatorsRead(BaseModel):
    """动量类指标集合。"""

    rsi14: Decimal | None
    macd: MacdIndicatorRead


class VolatilityIndicatorsRead(BaseModel):
    """波动率类指标集合。"""

    atr: AtrIndicatorRead
    bollinger: BollingerIndicatorRead


class VolumeIndicatorsRead(BaseModel):
    """量价类指标集合；OBV 以响应历史窗口首根为零点。"""

    mfi14: Decimal | None
    obv: Decimal | None


class MarketIndicatorsRead(BaseModel):
    """对外技术指标响应及 Signal 中保存的同口径不可变快照。"""

    symbol: str
    timeframe: str
    as_of: datetime
    source_close: Decimal
    closed_candles_only: bool = True
    candle_count: int
    warmup_complete: bool
    version: str = "1.0"
    trend: TrendIndicatorsRead
    momentum: MomentumIndicatorsRead
    volatility: VolatilityIndicatorsRead
    volume: VolumeIndicatorsRead


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
    """已校验的完整扫描配置；OI 回看窗口和变化阈值均按 K 线周期独立设置。"""
    timeframes: list[str] = ["15m", "30m", "1h", "4h", "1d"]
    min_24h_quote_volume: Decimal = Decimal("10000000")
    volume_ema_period: int = Field(default=12, ge=2, le=100)
    volume_multiplier: Decimal = Field(default=Decimal("1.5"), ge=Decimal("1"), le=Decimal("100"))
    min_progress_percent: Decimal = Field(default=Decimal("10"), ge=Decimal("0"), le=Decimal("100"))
    oi_lookback_minutes_by_timeframe: dict[str, OILookbackMinutes] = Field(
        default_factory=lambda: DEFAULT_OI_LOOKBACK_MINUTES_BY_TIMEFRAME.copy()
    )
    oi_change_threshold_percent_by_timeframe: dict[str, OIChangeThresholdPercent] = Field(
        default_factory=lambda: {
            timeframe: DEFAULT_OI_CHANGE_THRESHOLD_PERCENT
            for timeframe in DEFAULT_OI_LOOKBACK_MINUTES_BY_TIMEFRAME
        }
    )
    scan_interval_minutes: int = Field(default=5, ge=1, le=60)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_oi_lookback(cls, data):
        """把旧版全局 OI 窗口及阈值迁移为逐周期配置。"""
        if not isinstance(data, dict):
            return data
        values = dict(data)
        timeframes = values.get("timeframes", ["15m", "30m", "1h", "4h", "1d"])
        if "oi_lookback_minutes_by_timeframe" not in values:
            legacy_lookback = values.pop("oi_lookback_minutes", None)
            if legacy_lookback is not None and int(legacy_lookback) != 15:
                # 旧配置若被用户主动修改过，则升级后继续对所有周期使用该值。
                values["oi_lookback_minutes_by_timeframe"] = {
                    timeframe: int(legacy_lookback) for timeframe in timeframes
                }
            else:
                values["oi_lookback_minutes_by_timeframe"] = {
                    timeframe: DEFAULT_OI_LOOKBACK_MINUTES_BY_TIMEFRAME.get(timeframe, 15)
                    for timeframe in timeframes
                }
        if "oi_change_threshold_percent_by_timeframe" not in values:
            # 旧版只有一个全局阈值，升级时复制到所有启用周期，确保扫描行为不变。
            legacy_threshold = values.pop(
                "oi_change_threshold_percent", DEFAULT_OI_CHANGE_THRESHOLD_PERCENT
            )
            values["oi_change_threshold_percent_by_timeframe"] = {
                timeframe: legacy_threshold for timeframe in timeframes
            }
        return values

    def oi_lookback_for(self, timeframe: str) -> int:
        """返回指定 K 线周期的 OI 回看分钟数，未知周期使用 15 分钟兜底。"""
        return self.oi_lookback_minutes_by_timeframe.get(
            timeframe,
            DEFAULT_OI_LOOKBACK_MINUTES_BY_TIMEFRAME.get(timeframe, 15),
        )

    def oi_change_threshold_for(self, timeframe: str) -> Decimal:
        """返回指定 K 线周期的 OI 变化阈值，未知周期使用 0.05% 兜底。"""
        return self.oi_change_threshold_percent_by_timeframe.get(
            timeframe,
            DEFAULT_OI_CHANGE_THRESHOLD_PERCENT,
        )


class ConfigUpdate(BaseModel):
    """配置接口允许局部修改的字段，包括逐 K 线周期的 OI 回看窗口及阈值。"""
    min_24h_quote_volume: Decimal | None = Field(default=None, ge=0)
    volume_ema_period: int | None = Field(default=None, ge=2, le=100)
    volume_multiplier: Decimal | None = Field(default=None, ge=1, le=100)
    min_progress_percent: Decimal | None = Field(default=None, ge=0, le=100)
    oi_lookback_minutes_by_timeframe: dict[str, OILookbackMinutes] | None = None
    oi_change_threshold_percent_by_timeframe: dict[str, OIChangeThresholdPercent] | None = None
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
    technical_indicators: MarketIndicatorsRead | None = None
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
