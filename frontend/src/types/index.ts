// ==================== 前端领域类型 ====================

/** 仪表盘聚合指标，对应后端 /dashboard 响应。 */
export interface DashboardStats {
  total_symbols: number
  active_symbols: number
  websocket_status: string
  last_scan_at: string | null
  last_scan_duration_ms: number | null
  today_signal_count: number
  current_signal_count: number
}

/** 永续合约明细，数值字段来自最近一次市场快照。 */
export interface ActiveSymbol {
  symbol: string
  base_asset: string
  quote_asset: string
  contract_type: string
  is_active: boolean
  last_price: string | null
  price_change_percent_24h: string | null
  quote_volume_24h: string | null
  funding_rate: string | null
  updated_at: string
}

/** 单个 EMA 的值、单根斜率和相对完整 K 线收盘价距离。 */
export interface EmaIndicator {
  period: number
  value: string | null
  slope_percent: string | null
  distance_percent: string | null
}

/** ADX 趋势强度与正负方向分量。 */
export interface AdxIndicator {
  period: number
  value: string | null
  plus_di: string | null
  minus_di: string | null
  slope: string | null
}

/** 标准 MACD(12, 26, 9) 最新观察点。 */
export interface MacdIndicator {
  fast_period: number
  slow_period: number
  signal_period: number
  line: string | null
  signal: string | null
  histogram: string | null
}

/** ATR 原始值及相对收盘价百分比。 */
export interface AtrIndicator {
  period: number
  value: string | null
  percent: string | null
}

/** 布林带边界及标准化带宽、价格位置。 */
export interface BollingerIndicator {
  period: number
  standard_deviations: string
  upper: string | null
  middle: string | null
  lower: string | null
  bandwidth_percent: string | null
  percent_b: string | null
}

/** 分组技术指标接口及 Signal 固化快照。 */
export interface MarketIndicators {
  symbol: string
  timeframe: string
  as_of: string
  source_close: string
  closed_candles_only: boolean
  candle_count: number
  warmup_complete: boolean
  version: string
  trend: {
    ema: Record<string, EmaIndicator>
    ema_alignment: 'bullish' | 'bearish' | 'mixed' | 'insufficient_data'
    adx: AdxIndicator
  }
  momentum: {
    rsi14: string | null
    macd: MacdIndicator
  }
  volatility: {
    atr: AtrIndicator
    bollinger: BollingerIndicator
  }
  volume: {
    mfi14: string | null
    obv: string | null
  }
}

/** Signal 检测后各观察周期的价格变化，以及已计算观察点的最大涨跌幅。 */
export interface SignalFuturePerformance {
  price_change_5m_percent: string | null
  price_change_15m_percent: string | null
  price_change_30m_percent: string | null
  price_change_1h_percent: string | null
  price_change_4h_percent: string | null
  price_change_8h_percent: string | null
  price_change_12h_percent: string | null
  price_change_16h_percent: string | null
  price_change_1d_percent: string | null
  price_change_2d_percent: string | null
  max_rise_percent: string | null
  max_drop_percent: string | null
}

/** 不可变 Signal 市场快照，字段保留检测时刻的原始数值。 */
export interface SignalSnapshot {
  id: string
  symbol: string
  is_tradfi: boolean
  timeframe: string
  detected_at: string
  open_time: string
  close_time: string
  open: string
  high: string
  low: string
  current_price: string
  current_volume: string
  current_quote_volume: string
  progress_percent: string
  estimated_volume: string
  volume_ema: string
  volume_ema_period: number
  volume_ratio: string
  volume_multiplier: string
  ema14: string | null
  ema50: string | null
  rsi14: string | null
  adx14: string | null
  atr14: string | null
  adx_slope: string | null
  ema14_slope_percent: string | null
  ema50_slope_percent: string | null
  technical_indicators: MarketIndicators | null
  fund_flow_snapshot: SignalFundFlowSnapshot | null
  oldest_oi: string
  newest_oi: string
  oi_change_absolute: string
  oi_change_percent: string
  oi_lookback_minutes: number | null
  oldest_timestamp: string
  newest_timestamp: string
  last_price: string
  price_change_percent_24h: string
  quote_volume_24h: string
  funding_rate: string | null
  signal_type: string
  future_performance?: SignalFuturePerformance | null
}

/** Signal 检测时刻固化的主动资金流与 OI 联合状态。 */
export interface SignalFundFlowSnapshot {
  version: string
  calculated_at: string
  quote_volume: string
  taker_buy_quote_volume: string
  taker_sell_quote_volume: string
  net_taker_flow: string
  taker_buy_ratio_percent: string | null
  price_change_percent: string | null
  open_interest_change: string
  open_interest_change_percent: string
  regime: FundFlowRegime
}

/** TradingView 图表中的 K 线和六组可选 EMA 数据点。 */
export interface SignalChartCandle {
  time: number
  open: string
  high: string
  low: string
  close: string
  volume: string
  emas: Record<string, string | null>
  is_signal: boolean
}

/** 无未来数据的 Signal 图表快照响应。 */
export interface SignalChartData {
  symbol: string
  timeframe: string
  signal_time: number
  signal_open_time: number
  candles: SignalChartCandle[]
}

/** 实时模式使用的最新市场图表窗口。 */
export interface RealtimeChartData {
  symbol: string
  timeframe: string
  candles: SignalChartCandle[]
}

/** 实时 K 线 WebSocket 的增量消息。 */
export interface RealtimeKlineMessage {
  type: 'kline'
  data: SignalChartCandle
}

/** 单个合约资金流时间桶，联合主动成交、价格和 Open Interest。 */
export interface ContractFundFlowPoint {
  time: number
  close: string
  quote_volume: string
  taker_buy_quote_volume: string
  taker_sell_quote_volume: string
  net_taker_flow: string
  price_change_percent: string | null
  open_interest: string | null
  open_interest_value: string | null
  open_interest_change: string | null
  open_interest_change_percent: string | null
  regime: FundFlowRegime
}

/** 资金流窗口的价格、持仓与主动成交汇总。 */
export interface ContractFundFlowSummary {
  net_taker_flow: string
  price_change_percent: string | null
  open_interest_change: string | null
  open_interest_change_percent: string | null
  regime: FundFlowRegime
}

export type FundFlowRegime =
  | 'new_longs'
  | 'new_shorts'
  | 'short_covering'
  | 'long_closing'
  | 'mixed'
  | 'insufficient_data'

/** 合约资金流组件接口响应。 */
export interface ContractFundFlowData {
  symbol: string
  timeframe: string
  points: ContractFundFlowPoint[]
  summary: ContractFundFlowSummary
}

/** Signal 分页响应。 */
export interface SignalPage {
  items: SignalSnapshot[]
  /** 过滤条件下的周期信号明细总数。 */
  total: number
  /** 按“检测时间 + 交易对”去重后的共振组总数，用于分页。 */
  group_total: number
  page: number
  /** 每页共振组数，每组可包含多条周期明细。 */
  page_size: number
}

/** 可在线调整的扫描参数。 */
export interface ScannerConfig {
  timeframes: string[]
  min_24h_quote_volume: number
  volume_ema_period: number
  volume_multiplier: number
  min_progress_percent: number
  /** 每个 K 线周期用于计算 OI 变化率的独立回看分钟数。 */
  oi_lookback_minutes_by_timeframe: Record<string, number>
  /** 每个 K 线周期独立触发 Signal 的 OI 变化百分比阈值。 */
  oi_change_threshold_percent_by_timeframe: Record<string, number>
  scan_interval_minutes: number
}

/** Signal 列表查询条件。 */
export interface SignalQuery {
  symbol?: string
  timeframe?: string
  sort_by?: 'detected_at' | 'volume_ratio' | 'oi_change_percent'
  sort_order?: 'asc' | 'desc'
  page?: number
  page_size?: number
}
