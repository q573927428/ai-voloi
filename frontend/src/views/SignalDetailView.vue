<!-- ==================== Signal 完整快照页面 ==================== -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ArrowLeft } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { api, errorMessage } from '../api/client'
import MarketSymbol from '../components/MarketSymbol.vue'
import SignalTradingViewChart from '../components/SignalTradingViewChart.vue'
import type { SignalSnapshot } from '../types'

const route = useRoute()
const router = useRouter()
const signal = ref<SignalSnapshot | null>(null)
const loading = ref(false)
const n = (value?: string | null) => value == null ? '—' : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 6 })
const dt = (value?: string) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
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
  />
  <div v-if="signal" class="detail-grid" v-loading="loading">
    <section class="detail-section"><h2>K 线快照 · <MarketSymbol :symbol="signal.symbol" :is-tradfi="signal.is_tradfi" /> / {{ signal.timeframe }}</h2><div class="kv"><span>检测时间</span><strong>{{ dt(signal.detected_at) }}</strong></div><div class="kv"><span>K 线区间</span><strong>{{ dt(signal.open_time) }} - {{ dt(signal.close_time) }}</strong></div><div class="kv"><span>OHLC</span><strong>{{ n(signal.open) }} / {{ n(signal.high) }} / {{ n(signal.low) }} / {{ n(signal.current_price) }}</strong></div><div class="kv"><span>形成进度</span><strong>{{ Number(signal.progress_percent).toFixed(2) }}%</strong></div><div class="kv"><span>Quote Volume</span><strong>{{ n(signal.current_quote_volume) }}</strong></div></section>
    <section class="detail-section"><h2>成交量异常</h2><div class="kv"><span>当前成交量</span><strong>{{ n(signal.current_volume) }}</strong></div><div class="kv"><span>预计完整成交量</span><strong>{{ n(signal.estimated_volume) }}</strong></div><div class="kv"><span>Volume EMA{{ signal.volume_ema_period }}</span><strong>{{ n(signal.volume_ema) }}</strong></div><div class="kv"><span>触发阈值</span><strong>{{ n(signal.volume_multiplier) }}x</strong></div><div class="kv"><span>Volume Ratio</span><strong class="positive">{{ Number(signal.volume_ratio).toFixed(3) }}x</strong></div></section>
    <section class="detail-section"><h2>Open Interest</h2><div class="kv"><span>观察窗口</span><strong>{{ signal.oi_lookback_minutes == null ? '旧口径（K 线开盘起）' : `${signal.oi_lookback_minutes} 分钟` }}</strong></div><div class="kv"><span>起点 / 时间</span><strong>{{ n(signal.oldest_oi) }} · {{ dt(signal.oldest_timestamp) }}</strong></div><div class="kv"><span>终点 / 时间</span><strong>{{ n(signal.newest_oi) }} · {{ dt(signal.newest_timestamp) }}</strong></div><div class="kv"><span>绝对变化</span><strong>{{ n(signal.oi_change_absolute) }}</strong></div><div class="kv"><span>变化率</span><strong class="positive">+{{ Number(signal.oi_change_percent).toFixed(4) }}%</strong></div></section>
    <section class="detail-section"><h2>24h 市场快照</h2><div class="kv"><span>最新价格</span><strong>{{ n(signal.last_price) }}</strong></div><div class="kv"><span>价格变化</span><strong :class="Number(signal.price_change_percent_24h) >= 0 ? 'positive' : 'negative'">{{ n(signal.price_change_percent_24h) }}%</strong></div><div class="kv"><span>Quote Volume</span><strong>{{ n(signal.quote_volume_24h) }}</strong></div></section>
    <section class="detail-section indicator-section">
      <div class="indicator-head">
        <h2>趋势指标 · 完整 K 线</h2>
        <el-tag v-if="signal.technical_indicators" :type="alignmentType" effect="plain">{{ alignmentText }}</el-tag>
      </div>
      <template v-if="signal.technical_indicators">
        <div class="indicator-meta">截至 {{ dt(signal.technical_indicators.as_of) }} · {{ signal.technical_indicators.candle_count }} 根完整 K 线 · 收盘价 {{ n(signal.technical_indicators.source_close) }}</div>
        <div class="ema-table">
          <div class="ema-row ema-header"><span>周期</span><span>EMA</span><span>价格距离</span><span>斜率 / 根</span></div>
          <div v-for="item in emaRows" :key="item.period" class="ema-row">
            <strong>EMA{{ item.period }}</strong>
            <span>{{ n(item.value) }}</span>
            <strong :class="tone(item.distance_percent)">{{ n(item.distance_percent) }}%</strong>
            <strong :class="tone(item.slope_percent)">{{ n(item.slope_percent) }}%</strong>
          </div>
        </div>
        <div class="indicator-summary">
          <div class="kv"><span>ADX14</span><strong>{{ n(signal.technical_indicators.trend.adx.value) }}</strong></div>
          <div class="kv"><span>+DI14 / -DI14</span><strong><b class="positive">{{ n(signal.technical_indicators.trend.adx.plus_di) }}</b> / <b class="negative">{{ n(signal.technical_indicators.trend.adx.minus_di) }}</b></strong></div>
          <div class="kv"><span>ADX 斜率</span><strong :class="tone(signal.technical_indicators.trend.adx.slope)">{{ n(signal.technical_indicators.trend.adx.slope) }} 点/根</strong></div>
          <div class="kv"><span>ATR14 / ATR%</span><strong>{{ n(signal.technical_indicators.volatility.atr.value) }} / {{ n(signal.technical_indicators.volatility.atr.percent) }}%</strong></div>
        </div>
      </template>
      <template v-else>
        <div class="kv"><span>EMA14</span><strong>{{ n(signal.ema14) }}</strong></div>
        <div class="kv"><span>EMA50</span><strong>{{ n(signal.ema50) }}</strong></div>
        <div class="kv"><span>ADX14</span><strong>{{ n(signal.adx14) }}</strong></div>
        <div class="indicator-meta">旧 Signal 尚未回填结构化指标快照</div>
      </template>
    </section>
    <section class="detail-section" style="grid-column:1/-1"><h2>未来表现</h2><div v-if="signal.future_performance" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:18px"><div v-for="(value,key) in signal.future_performance" :key="key"><div class="metric-label">{{ key }}</div><strong :class="Number(value) >= 0 ? 'positive' : 'negative'">{{ value == null ? '待计算' : `${n(value)}%` }}</strong></div></div><el-empty v-else description="等待未来价格数据" :image-size="70" /></section>
  </div>
  <el-skeleton v-else :rows="12" animated />
</template>

<style scoped>
.indicator-section { grid-column: 1 / -1; }
.indicator-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.indicator-head h2 { margin-bottom: 0; }
.indicator-meta { margin: 10px 0 16px; color: #72808e; font-size: 12px; }
.ema-table { border-top: 1px solid #dfe4e9; border-bottom: 1px solid #dfe4e9; }
.ema-row { display: grid; grid-template-columns: 90px repeat(3, minmax(120px, 1fr)); gap: 16px; align-items: center; min-height: 42px; border-bottom: 1px solid #edf0f3; font-size: 13px; }
.ema-row:last-child { border-bottom: 0; }
.ema-row > span:not(:first-child), .ema-row > strong:not(:first-child) { text-align: right; }
.ema-header { min-height: 36px; color: #72808e; font-size: 12px; }
.indicator-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); column-gap: 36px; margin-top: 10px; }
@media (max-width: 720px) {
  .ema-table { overflow-x: auto; }
  .ema-row { min-width: 560px; }
  .indicator-summary { grid-template-columns: 1fr; }
}
</style>
