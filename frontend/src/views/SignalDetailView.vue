<!-- ==================== Signal 完整快照页面 ==================== -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ArrowLeft } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { api, errorMessage } from '../api/client'
import MarketSymbol from '../components/MarketSymbol.vue'
import SignalTradingViewChart from '../components/SignalTradingViewChart.vue'
import type { SignalFuturePerformance, SignalSnapshot } from '../types'

const route = useRoute()
const router = useRouter()
const signal = ref<SignalSnapshot | null>(null)
const loading = ref(false)
const n = (value?: string | null) => value == null ? '—' : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 6 })
const dt = (value?: string) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

/** 展示交易对进入当前活跃周期后，等待多久才触发 Signal。 */
function activePoolAgeText(enteredAt: string | null, detectedAt: string): string {
  if (!enteredAt) return '旧 Signal 未记录'
  const seconds = Math.max(0, Math.floor((new Date(detectedAt).getTime() - new Date(enteredAt).getTime()) / 1000))
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (hours < 24) return minutes ? `${hours} 小时 ${minutes} 分钟` : `${hours} 小时`
  const days = Math.floor(hours / 24)
  const remainingHours = hours % 24
  return remainingHours ? `${days} 天 ${remainingHours} 小时` : `${days} 天`
}
const emaRows = computed(() => Object.values(signal.value?.technical_indicators?.trend.ema ?? {})
  .sort((left, right) => left.period - right.period))
const alignmentText = computed(() => ({
  bullish: '多头排列',
  bearish: '空头排列',
  mixed: '交错排列',
  insufficient_data: '数据不足',
}[signal.value?.technical_indicators?.trend.ema_alignment ?? 'insufficient_data']))
const alignmentType = computed(() => ({
  bullish: 'success',
  bearish: 'danger',
  mixed: 'warning',
  insufficient_data: 'info',
}[signal.value?.technical_indicators?.trend.ema_alignment ?? 'insufficient_data'] as 'success' | 'danger' | 'warning' | 'info'))
const tone = (value?: string | null) => value == null ? '' : Number(value) >= 0 ? 'positive' : 'negative'
const fundFlowRegimeText: Record<string, string> = {
  new_longs: '新增多头',
  new_shorts: '新增空头',
  short_covering: '空头回补',
  long_closing: '多头平仓',
  mixed: '方向分歧',
  insufficient_data: '数据不足',
}

/** 资金费率使用百分比口径展示，保留小费率识别所需精度。 */
function fundingRateText(value: string | null): string {
  if (value == null) return '—'
  const percent = Number(value) * 100
  return `${percent > 0 ? '+' : ''}${percent.toFixed(4)}%`
}

/** 未来价格变化指标采用显式标签和顺序，避免向页面暴露 API 字段名。 */
interface FuturePerformanceMetric {
  key: keyof SignalFuturePerformance
  label: string
}

const futurePerformanceMetrics: FuturePerformanceMetric[] = [
  { key: 'price_change_5m_percent', label: '5m 涨跌幅' },
  { key: 'price_change_15m_percent', label: '15m 涨跌幅' },
  { key: 'price_change_30m_percent', label: '30m 涨跌幅' },
  { key: 'price_change_1h_percent', label: '1h 涨跌幅' },
  { key: 'price_change_4h_percent', label: '4h 涨跌幅' },
  { key: 'price_change_8h_percent', label: '8h 涨跌幅' },
  { key: 'price_change_12h_percent', label: '12h 涨跌幅' },
  { key: 'price_change_16h_percent', label: '16h 涨跌幅' },
  { key: 'price_change_1d_percent', label: '1d 涨跌幅' },
  { key: 'price_change_2d_percent', label: '2d 涨跌幅' },
  { key: 'max_rise_percent', label: '最大涨幅' },
  { key: 'max_drop_percent', label: '最大跌幅' },
]

/** 格式化观察点涨跌幅，正负号直接表达价格变化方向。 */
function futurePerformanceText(value: string | null): string {
  if (value == null) return '待计算'
  const number = Number(value)
  if (!Number.isFinite(number)) return '待计算'
  return `${number > 0 ? '+' : ''}${n(String(number))}%`
}

/** 获取单个 Signal 的完整不可变快照及未来表现。 */
async function load() {
  loading.value = true
  try { signal.value = await api.signal(route.params.id as string) }
  catch (error) { ElMessage.error(errorMessage(error)) }
  finally { loading.value = false }
}
onMounted(load)
</script>

<template>
  <div class="section-head">
    <el-button text :icon="ArrowLeft" @click="router.back()">返回</el-button>
    <el-tag v-if="signal" type="warning" effect="plain">{{ signal.signal_type }}</el-tag>
  </div>
  <SignalTradingViewChart
    v-if="signal"
    :signal-id="signal.id"
    :symbol="signal.symbol"
    :timeframe="signal.timeframe"
    :is-tradfi="signal.is_tradfi"
  />
  <div v-if="signal" class="detail-grid" v-loading="loading">
    <section class="detail-section"><h2>K 线快照 · <MarketSymbol :symbol="signal.symbol" :is-tradfi="signal.is_tradfi" /> / {{ signal.timeframe }}</h2><div class="kv"><span>检测时间</span><strong>{{ dt(signal.detected_at) }}</strong></div><div class="kv"><span>K 线区间</span><strong>{{ dt(signal.open_time) }}<br>{{ dt(signal.close_time) }}</strong></div><div class="kv"><span>OHLC</span><strong>{{ n(signal.open) }} / {{ n(signal.high) }} / {{ n(signal.low) }} / {{ n(signal.current_price) }}</strong></div><div class="kv"><span>形成进度</span><strong>{{ Number(signal.progress_percent).toFixed(2) }}%</strong></div><div class="kv"><span>Quote Volume</span><strong>{{ n(signal.current_quote_volume) }}</strong></div></section>
    <section class="detail-section"><h2>成交量异常</h2><div class="kv"><span>当前成交量</span><strong>{{ n(signal.current_volume) }}</strong></div><div class="kv"><span>预计完整成交量</span><strong>{{ n(signal.estimated_volume) }}</strong></div><div class="kv"><span>Volume EMA{{ signal.volume_ema_period }}</span><strong>{{ n(signal.volume_ema) }}</strong></div><div class="kv"><span>触发阈值</span><strong>{{ n(signal.volume_multiplier) }}x</strong></div><div class="kv"><span>Volume Ratio</span><strong class="positive">{{ Number(signal.volume_ratio).toFixed(3) }}x</strong></div></section>
    <section class="detail-section"><h2>Open Interest</h2><div class="kv"><span>观察窗口</span><strong>{{ signal.oi_lookback_minutes == null ? '旧口径（K 线开盘起）' : `${signal.oi_lookback_minutes} 分钟` }}</strong></div><div class="kv"><span>起点 / 时间</span><strong>{{ n(signal.oldest_oi) }} · {{ dt(signal.oldest_timestamp) }}</strong></div><div class="kv"><span>终点 / 时间</span><strong>{{ n(signal.newest_oi) }} · {{ dt(signal.newest_timestamp) }}</strong></div><div class="kv"><span>绝对变化</span><strong>{{ n(signal.oi_change_absolute) }}</strong></div><div class="kv"><span>变化率</span><strong class="positive">+{{ Number(signal.oi_change_percent).toFixed(4) }}%</strong></div></section>
    <section class="detail-section"><h2>24h 市场快照</h2><div class="kv"><span>最新价格</span><strong>{{ n(signal.last_price) }}</strong></div><div class="kv"><span>价格变化</span><strong :class="Number(signal.price_change_percent_24h) >= 0 ? 'positive' : 'negative'">{{ n(signal.price_change_percent_24h) }}%</strong></div><div class="kv"><span>Quote Volume</span><strong>{{ n(signal.quote_volume_24h) }}</strong></div><div class="kv"><span>进入活跃池</span><strong>{{ signal.active_pool_entered_at ? dt(signal.active_pool_entered_at) : '旧 Signal 未记录' }}</strong></div><div class="kv"><span>入池至触发</span><strong>{{ activePoolAgeText(signal.active_pool_entered_at, signal.detected_at) }}</strong></div><div class="kv"><span>资金费率</span><strong :class="tone(signal.funding_rate)">{{ fundingRateText(signal.funding_rate) }}</strong></div></section>
    <section class="detail-section">
      <h2>合约资金流快照</h2>
      <template v-if="signal.fund_flow_snapshot">
        <div class="kv"><span>市场状态</span><strong>{{ fundFlowRegimeText[signal.fund_flow_snapshot.regime] ?? signal.fund_flow_snapshot.regime }}</strong></div>
        <div class="kv"><span>主动净流</span><strong :class="tone(signal.fund_flow_snapshot.net_taker_flow)">{{ n(signal.fund_flow_snapshot.net_taker_flow) }} USDT</strong></div>
        <div class="kv"><span>主动买入 <br> 主动卖出</span><strong>{{ n(signal.fund_flow_snapshot.taker_buy_quote_volume) }} <br> {{ n(signal.fund_flow_snapshot.taker_sell_quote_volume) }}</strong></div>
        <div class="kv"><span>主动买入占比</span><strong>{{ n(signal.fund_flow_snapshot.taker_buy_ratio_percent) }}%</strong></div>
        <div class="kv"><span>价格 / OI 变化</span><strong>{{ n(signal.fund_flow_snapshot.price_change_percent) }}% / {{ n(signal.fund_flow_snapshot.open_interest_change_percent) }}%</strong></div>
        <div class="kv"><span>计算时间 / 版本</span><strong>{{ dt(signal.fund_flow_snapshot.calculated_at) }} · {{ signal.fund_flow_snapshot.version }}</strong></div>
      </template>
      <div v-else class="indicator-meta">旧 Signal 未保存资金流快照</div>
    </section>
    <section class="detail-section indicator-section">
      <div class="indicator-head">
        <h2>技术指标 · 完整 K 线</h2>
        <div v-if="signal.technical_indicators" class="indicator-tags">
          <el-tag :type="signal.technical_indicators.warmup_complete ? 'success' : 'warning'" effect="plain">{{ signal.technical_indicators.warmup_complete ? '预热完成' : '预热中' }}</el-tag>
          <el-tag :type="alignmentType" effect="plain">{{ alignmentText }}</el-tag>
        </div>
      </div>
      <template v-if="signal.technical_indicators">
        <div class="indicator-meta">截至 {{ dt(signal.technical_indicators.as_of) }} · {{ signal.technical_indicators.candle_count }} 根完整 K 线 · 收盘价 {{ n(signal.technical_indicators.source_close) }} · 指标版本 {{ signal.technical_indicators.version }}</div>
        <div class="ema-table">
          <div class="ema-row ema-header"><span>周期</span><span>EMA</span><span>价格距离</span><span>斜率 / 根</span></div>
          <div v-for="item in emaRows" :key="item.period" class="ema-row">
            <strong>EMA{{ item.period }}</strong>
            <span>{{ n(item.value) }}</span>
            <strong :class="tone(item.distance_percent)">{{ n(item.distance_percent) }}%</strong>
            <strong :class="tone(item.slope_percent)">{{ n(item.slope_percent) }}%</strong>
          </div>
        </div>
        <div class="indicator-groups">
          <div class="indicator-group">
            <h3>趋势强度</h3>
            <div class="kv"><span>ADX14</span><strong>{{ n(signal.technical_indicators.trend.adx.value) }}</strong></div>
            <div class="kv"><span>+DI14 / -DI14</span><strong><b class="positive">{{ n(signal.technical_indicators.trend.adx.plus_di) }}</b> / <b class="negative">{{ n(signal.technical_indicators.trend.adx.minus_di) }}</b></strong></div>
            <div class="kv"><span>ADX 斜率</span><strong :class="tone(signal.technical_indicators.trend.adx.slope)">{{ n(signal.technical_indicators.trend.adx.slope) }} 点/根</strong></div>
          </div>
          <div class="indicator-group">
            <h3>动量</h3>
            <div class="kv"><span>RSI14</span><strong>{{ n(signal.technical_indicators.momentum.rsi14) }}</strong></div>
            <div class="kv"><span>MACD Line</span><strong :class="tone(signal.technical_indicators.momentum.macd.line)">{{ n(signal.technical_indicators.momentum.macd.line) }}</strong></div>
            <div class="kv"><span>MACD Signal</span><strong :class="tone(signal.technical_indicators.momentum.macd.signal)">{{ n(signal.technical_indicators.momentum.macd.signal) }}</strong></div>
            <div class="kv"><span>MACD Histogram</span><strong :class="tone(signal.technical_indicators.momentum.macd.histogram)">{{ n(signal.technical_indicators.momentum.macd.histogram) }}</strong></div>
          </div>
          <div class="indicator-group">
            <h3>波动率</h3>
            <div class="kv"><span>ATR14</span><strong>{{ n(signal.technical_indicators.volatility.atr.value) }}</strong></div>
            <div class="kv"><span>ATR14%</span><strong>{{ n(signal.technical_indicators.volatility.atr.percent) }}%</strong></div>
            <div class="kv"><span>布林上轨</span><strong>{{ n(signal.technical_indicators.volatility.bollinger.upper) }}</strong></div>
            <div class="kv"><span>布林中轨</span><strong>{{ n(signal.technical_indicators.volatility.bollinger.middle) }}</strong></div>
            <div class="kv"><span>布林下轨</span><strong>{{ n(signal.technical_indicators.volatility.bollinger.lower) }}</strong></div>
            <div class="kv"><span>布林带宽</span><strong>{{ n(signal.technical_indicators.volatility.bollinger.bandwidth_percent) }}%</strong></div>
            <div class="kv"><span>布林 %B</span><strong>{{ n(signal.technical_indicators.volatility.bollinger.percent_b) }}</strong></div>
          </div>
          <div class="indicator-group">
            <h3>量价</h3>
            <div class="kv"><span>MFI14</span><strong>{{ n(signal.technical_indicators.volume.mfi14) }}</strong></div>
            <div class="kv"><span>OBV</span><strong :class="tone(signal.technical_indicators.volume.obv)">{{ n(signal.technical_indicators.volume.obv) }}</strong></div>
          </div>
        </div>
      </template>
      <template v-else>
        <div class="kv"><span>EMA14</span><strong>{{ n(signal.ema14) }}</strong></div>
        <div class="kv"><span>EMA50</span><strong>{{ n(signal.ema50) }}</strong></div>
        <div class="kv"><span>RSI14</span><strong>{{ n(signal.rsi14) }}</strong></div>
        <div class="kv"><span>ADX14</span><strong>{{ n(signal.adx14) }}</strong></div>
        <div class="kv"><span>ATR14</span><strong>{{ n(signal.atr14) }}</strong></div>
        <div class="kv"><span>ADX 斜率</span><strong :class="tone(signal.adx_slope)">{{ n(signal.adx_slope) }} 点/根</strong></div>
        <div class="kv"><span>EMA14 斜率</span><strong :class="tone(signal.ema14_slope_percent)">{{ n(signal.ema14_slope_percent) }}%/根</strong></div>
        <div class="kv"><span>EMA50 斜率</span><strong :class="tone(signal.ema50_slope_percent)">{{ n(signal.ema50_slope_percent) }}%/根</strong></div>
        <div class="indicator-meta">旧 Signal 尚未回填结构化指标快照</div>
      </template>
    </section>
    <section class="detail-section" style="grid-column:1/-1"><h2>未来价格表现</h2><div v-if="signal.future_performance" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:18px"><div v-for="metric in futurePerformanceMetrics" :key="metric.key"><div class="metric-label">{{ metric.label }}</div><strong :class="tone(signal.future_performance[metric.key])">{{ futurePerformanceText(signal.future_performance[metric.key]) }}</strong></div></div><el-empty v-else description="等待未来价格数据" :image-size="70" /></section>
  </div>
  <el-skeleton v-else :rows="12" animated />
</template>

<style scoped>
.indicator-section { grid-column: 1 / -1; }
.indicator-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.indicator-head h2 { margin-bottom: 0; }
.indicator-tags { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.indicator-meta { margin: 10px 0 16px; color: #72808e; font-size: 12px; }
.ema-table { border-top: 1px solid #dfe4e9; border-bottom: 1px solid #dfe4e9; }
.ema-row { display: grid; grid-template-columns: 90px repeat(3, minmax(120px, 1fr)); gap: 16px; align-items: center; min-height: 42px; border-bottom: 1px solid #edf0f3; font-size: 13px; }
.ema-row:last-child { border-bottom: 0; }
.ema-row > span:not(:first-child), .ema-row > strong:not(:first-child) { text-align: right; }
.ema-header { min-height: 36px; color: #72808e; font-size: 12px; }
.indicator-groups { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); column-gap: 36px; margin-top: 22px; }
.indicator-group h3 { margin: 0 0 8px; color: #4f5c69; font-size: 13px; }
@media (max-width: 1200px) {
  .indicator-groups { grid-template-columns: repeat(2, minmax(0, 1fr)); row-gap: 22px; }
}
@media (max-width: 720px) {
  .ema-table { overflow-x: auto; }
  .ema-row { min-width: 560px; }
  .indicator-head { align-items: flex-start; }
  .indicator-groups { grid-template-columns: 1fr; row-gap: 22px; }
}
</style>
