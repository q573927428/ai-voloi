<!-- ==================== Signal 完整快照页面 ==================== -->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ArrowLeft } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { api, errorMessage } from '../api/client'
import SignalTradingViewChart from '../components/SignalTradingViewChart.vue'
import type { SignalSnapshot } from '../types'

const route = useRoute()
const router = useRouter()
const signal = ref<SignalSnapshot | null>(null)
const loading = ref(false)
const n = (value?: string | null) => value == null ? '—' : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 6 })
const dt = (value?: string) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

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
    :signal-price="signal.current_price"
  />
  <div v-if="signal" class="detail-grid" v-loading="loading">
    <section class="detail-section"><h2>K 线快照 · {{ signal.symbol }} / {{ signal.timeframe }}</h2><div class="kv"><span>检测时间</span><strong>{{ dt(signal.detected_at) }}</strong></div><div class="kv"><span>K 线区间</span><strong>{{ dt(signal.open_time) }} - {{ dt(signal.close_time) }}</strong></div><div class="kv"><span>OHLC</span><strong>{{ n(signal.open) }} / {{ n(signal.high) }} / {{ n(signal.low) }} / {{ n(signal.current_price) }}</strong></div><div class="kv"><span>形成进度</span><strong>{{ Number(signal.progress_percent).toFixed(2) }}%</strong></div><div class="kv"><span>Quote Volume</span><strong>{{ n(signal.current_quote_volume) }}</strong></div></section>
    <section class="detail-section"><h2>成交量异常</h2><div class="kv"><span>当前成交量</span><strong>{{ n(signal.current_volume) }}</strong></div><div class="kv"><span>预计完整成交量</span><strong>{{ n(signal.estimated_volume) }}</strong></div><div class="kv"><span>Volume EMA{{ signal.volume_ema_period }}</span><strong>{{ n(signal.volume_ema) }}</strong></div><div class="kv"><span>触发阈值</span><strong>{{ n(signal.volume_multiplier) }}x</strong></div><div class="kv"><span>Volume Ratio</span><strong class="positive">{{ Number(signal.volume_ratio).toFixed(3) }}x</strong></div></section>
    <section class="detail-section"><h2>技术指标 · 完整 K 线</h2><div class="kv"><span>EMA14</span><strong>{{ n(signal.ema14) }}</strong></div><div class="kv"><span>EMA50</span><strong>{{ n(signal.ema50) }}</strong></div><div class="kv"><span>RSI14</span><strong>{{ n(signal.rsi14) }}</strong></div><div class="kv"><span>ADX14</span><strong>{{ n(signal.adx14) }}</strong></div><div class="kv"><span>ATR14</span><strong>{{ n(signal.atr14) }}</strong></div><div class="kv"><span>ADX 斜率</span><strong :class="Number(signal.adx_slope) >= 0 ? 'positive' : 'negative'">{{ n(signal.adx_slope) }} 点/根</strong></div><div class="kv"><span>EMA14 斜率</span><strong :class="Number(signal.ema14_slope_percent) >= 0 ? 'positive' : 'negative'">{{ n(signal.ema14_slope_percent) }}%/根</strong></div><div class="kv"><span>EMA50 斜率</span><strong :class="Number(signal.ema50_slope_percent) >= 0 ? 'positive' : 'negative'">{{ n(signal.ema50_slope_percent) }}%/根</strong></div></section>
    <section class="detail-section"><h2>Open Interest</h2><div class="kv"><span>起点 / 时间</span><strong>{{ n(signal.oldest_oi) }} · {{ dt(signal.oldest_timestamp) }}</strong></div><div class="kv"><span>终点 / 时间</span><strong>{{ n(signal.newest_oi) }} · {{ dt(signal.newest_timestamp) }}</strong></div><div class="kv"><span>绝对变化</span><strong>{{ n(signal.oi_change_absolute) }}</strong></div><div class="kv"><span>变化率</span><strong class="positive">+{{ Number(signal.oi_change_percent).toFixed(4) }}%</strong></div></section>
    <section class="detail-section"><h2>24h 市场快照</h2><div class="kv"><span>最新价格</span><strong>{{ n(signal.last_price) }}</strong></div><div class="kv"><span>价格变化</span><strong :class="Number(signal.price_change_percent_24h) >= 0 ? 'positive' : 'negative'">{{ n(signal.price_change_percent_24h) }}%</strong></div><div class="kv"><span>Quote Volume</span><strong>{{ n(signal.quote_volume_24h) }}</strong></div></section>
    <section class="detail-section" style="grid-column:1/-1"><h2>未来表现</h2><div v-if="signal.future_performance" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:18px"><div v-for="(value,key) in signal.future_performance" :key="key"><div class="metric-label">{{ key }}</div><strong :class="Number(value) >= 0 ? 'positive' : 'negative'">{{ value == null ? '待计算' : `${n(value)}%` }}</strong></div></div><el-empty v-else description="等待未来价格数据" :image-size="70" /></section>
  </div>
  <el-skeleton v-else :rows="12" animated />
</template>
