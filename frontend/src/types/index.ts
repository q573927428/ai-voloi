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

/** 不可变 Signal 市场快照，字段保留检测时刻的原始数值。 */
export interface SignalSnapshot {
  id: string
  symbol: string
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
  oldest_oi: string
  newest_oi: string
  oi_change_absolute: string
  oi_change_percent: string
  oldest_timestamp: string
  newest_timestamp: string
  last_price: string
  price_change_percent_24h: string
  quote_volume_24h: string
  signal_type: string
  future_performance?: Record<string, string | null> | null
}

/** TradingView 图表中的 K 线和双 EMA 数据点。 */
export interface SignalChartCandle {
  time: number
  open: string
  high: string
  low: string
  close: string
  volume: string
  ema14: string | null
  ema50: string | null
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

/** Signal 分页响应。 */
export interface SignalPage {
  items: SignalSnapshot[]
  total: number
  page: number
  page_size: number
}

/** 可在线调整的扫描参数。 */
export interface ScannerConfig {
  timeframes: string[]
  min_24h_quote_volume: number
  volume_ema_period: number
  volume_multiplier: number
  min_progress_percent: number
  oi_change_threshold_percent: number
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
