<!-- ==================== TradingView Signal K 线图表 ==================== -->
<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { api, errorMessage } from '../api/client'
import type { RealtimeKlineMessage, SignalChartCandle, SignalChartData } from '../types'

/** 图表定位所需的 Signal 标识与可见标题。 */
const props = defineProps<{ signalId: string; symbol: string; timeframe: string; signalPrice: string }>()
const container = ref<HTMLDivElement | null>(null)
const loading = ref(true)
const error = ref('')
const ema14 = ref('—')
const ema50 = ref('—')
const mode = ref<'snapshot' | 'realtime'>('realtime')
const liveConnected = ref(false)
const DEFAULT_VISIBLE_CANDLES = 300
const RIGHT_EMPTY_CANDLES = 20
let chart: IChartApi | null = null
let candleSeries: ISeriesApi<'Candlestick'> | null = null
let ema14Series: ISeriesApi<'Line'> | null = null
let ema50Series: ISeriesApi<'Line'> | null = null
let volumeSeries: ISeriesApi<'Histogram'> | null = null
let resizeObserver: ResizeObserver | null = null
let snapshotData: SignalChartData | null = null
let liveSocket: WebSocket | null = null
let reconnectTimer: number | undefined
let lastRealtimeCandleTime: number | null = null
let currentPricePrecision: number | null = null
let disposed = false

const modeOptions = [
  { label: '检测快照', value: 'snapshot' },
  { label: '实时行情', value: 'realtime' },
]

/** 将 Lightweight Charts 时间值转换为 Unix 毫秒。 */
function timeToMilliseconds(time: Time): number {
  if (typeof time === 'number') return time * 1000
  if (typeof time === 'string') return Date.parse(`${time}T00:00:00Z`)
  return Date.UTC(time.year, time.month - 1, time.day)
}

/** 使用固定 UTC+8 时区格式化十字光标时间，避免依赖浏览器所在时区。 */
function formatUtc8Time(time: Time): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false, hourCycle: 'h23',
  }).format(timeToMilliseconds(time))
}

/** 根据时间轴刻度级别生成 UTC+8 标签。 */
function formatUtc8Tick(time: Time, tickMarkType: number): string {
  const options: Intl.DateTimeFormatOptions = { timeZone: 'Asia/Shanghai', hour12: false, hourCycle: 'h23' }
  if (tickMarkType === 0) options.year = 'numeric'
  else if (tickMarkType === 1) options.month = 'short'
  else if (tickMarkType === 2) { options.month = '2-digit'; options.day = '2-digit' }
  else { options.hour = '2-digit'; options.minute = '2-digit' }
  return new Intl.DateTimeFormat('zh-CN', options).format(timeToMilliseconds(time))
}

/** 更新标题图例中的最新 EMA 值。 */
function updateLegend(candle?: SignalChartCandle) {
  ema14.value = candle?.ema14 == null ? '—' : Number(candle.ema14).toLocaleString('zh-CN', { maximumFractionDigits: 8 })
  ema50.value = candle?.ema50 == null ? '—' : Number(candle.ema50).toLocaleString('zh-CN', { maximumFractionDigits: 8 })
}

/** 根据价格数量级保留约三位有效数字，避免低价币被固定两位小数截断。 */
function resolvePricePrecision(price: number): number {
  if (!Number.isFinite(price) || price === 0) return 2
  return Math.min(12, Math.max(2, 2 - Math.floor(Math.log10(Math.abs(price)))))
}

/** 为蜡烛和双 EMA 同步应用动态价格精度。 */
function applyPricePrecision(price: number) {
  const precision = resolvePricePrecision(price)
  if (precision === currentPricePrecision) return
  currentPricePrecision = precision
  const priceFormat = { type: 'price' as const, precision, minMove: 10 ** -precision }
  candleSeries?.applyOptions({ priceFormat })
  ema14Series?.applyOptions({ priceFormat })
  ema50Series?.applyOptions({ priceFormat })
}

/** 将完整窗口写入全部图表序列，并恢复默认观察范围。 */
function setChartCandles(items: SignalChartCandle[]) {
  const latest = items[items.length - 1]
  if (latest) applyPricePrecision(Number(latest.close))
  candleSeries?.setData(items.map((item) => ({
    time: item.time as UTCTimestamp,
    open: Number(item.open), high: Number(item.high), low: Number(item.low), close: Number(item.close),
  })))
  ema14Series?.setData(items.filter((item) => item.ema14 != null).map((item) => ({
    time: item.time as UTCTimestamp, value: Number(item.ema14),
  })))
  ema50Series?.setData(items.filter((item) => item.ema50 != null).map((item) => ({
    time: item.time as UTCTimestamp, value: Number(item.ema50),
  })))
  volumeSeries?.setData(items.map((item) => ({
    time: item.time as UTCTimestamp,
    value: Number(item.volume),
    color: Number(item.close) >= Number(item.open) ? 'rgba(20,128,94,.35)' : 'rgba(197,77,74,.35)',
  })))
  updateLegend(latest)
  lastRealtimeCandleTime = latest?.time ?? null
  const lastLogicalIndex = items.length - 1
  if (lastLogicalIndex >= 0) {
    chart?.timeScale().setVisibleLogicalRange({
      from: Math.max(0, lastLogicalIndex - DEFAULT_VISIBLE_CANDLES + 1),
      to: lastLogicalIndex + RIGHT_EMPTY_CANDLES,
    })
  }
}

/** 将 WebSocket 增量同步到蜡烛、成交量和双 EMA 序列。 */
function updateRealtimeCandle(item: SignalChartCandle) {
  const isNewCandle = lastRealtimeCandleTime !== item.time
  const time = item.time as UTCTimestamp
  applyPricePrecision(Number(item.close))
  candleSeries?.update({
    time, open: Number(item.open), high: Number(item.high), low: Number(item.low), close: Number(item.close),
  })
  volumeSeries?.update({
    time, value: Number(item.volume),
    color: Number(item.close) >= Number(item.open) ? 'rgba(20,128,94,.35)' : 'rgba(197,77,74,.35)',
  })
  if (item.ema14 != null) ema14Series?.update({ time, value: Number(item.ema14) })
  if (item.ema50 != null) ema50Series?.update({ time, value: Number(item.ema50) })
  updateLegend(item)
  lastRealtimeCandleTime = item.time
  if (isNewCandle) chart?.timeScale().scrollToRealTime()
}

/** 关闭当前页面的实时连接和重连计时器。 */
function closeRealtime() {
  if (reconnectTimer) window.clearTimeout(reconnectTimer)
  reconnectTimer = undefined
  if (liveSocket) {
    liveSocket.onclose = null
    liveSocket.close()
    liveSocket = null
  }
  liveConnected.value = false
}

/** 订阅当前交易对和周期，并在异常断开后自动恢复。 */
function connectRealtime() {
  closeRealtime()
  if (disposed || mode.value !== 'realtime') return
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
  const base = import.meta.env.DEV
    ? `${protocol}://${location.hostname}:8000/api`
    : `${protocol}://${location.host}/api`
  const socket = new WebSocket(`${base}/ws/klines/${encodeURIComponent(props.symbol)}/${encodeURIComponent(props.timeframe)}`)
  liveSocket = socket
  socket.onopen = () => {
    liveConnected.value = true
    socket.send('ready')
  }
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data) as RealtimeKlineMessage
    if (message.type === 'kline' && mode.value === 'realtime') updateRealtimeCandle(message.data)
  }
  socket.onclose = () => {
    liveConnected.value = false
    liveSocket = null
    if (!disposed && mode.value === 'realtime') {
      reconnectTimer = window.setTimeout(connectRealtime, 3000)
    }
  }
}

/** 获取实时市场窗口，再建立对应交易对和周期的增量订阅。 */
async function loadRealtime() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.realtimeChart(props.symbol, props.timeframe)
    if (mode.value !== 'realtime') return
    setChartCandles(data.candles)
    connectRealtime()
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    loading.value = false
  }
}

/** 获取无未来数据快照并创建 TradingView Lightweight Charts 图表。 */
async function renderChart() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.signalChart(props.signalId)
    snapshotData = data
    await nextTick()
    if (!container.value) return
    chart = createChart(container.value, {
      width: container.value.clientWidth,
      height: container.value.clientHeight,
      layout: { background: { type: ColorType.Solid, color: '#ffffff' }, textColor: '#5f6c7b' },
      grid: { vertLines: { color: '#edf0f3' }, horzLines: { color: '#edf0f3' } },
      rightPriceScale: { borderColor: '#dce1e7', scaleMargins: { top: 0.08, bottom: 0.22 } },
      timeScale: {
        borderColor: '#dce1e7',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: RIGHT_EMPTY_CANDLES,
        tickMarkFormatter: formatUtc8Tick,
      },
      localization: { locale: 'zh-CN', timeFormatter: formatUtc8Time },
    })
    candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#14805e', downColor: '#c54d4a', borderVisible: false,
      wickUpColor: '#14805e', wickDownColor: '#c54d4a',
    })
    ema14Series = chart.addSeries(LineSeries, { color: '#2563eb', lineWidth: 2, priceLineVisible: false, lastValueVisible: true, title: 'EMA14' })
    ema50Series = chart.addSeries(LineSeries, { color: '#d97706', lineWidth: 2, priceLineVisible: false, lastValueVisible: true, title: 'EMA50' })
    volumeSeries = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: '', priceLineVisible: false, lastValueVisible: false })
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    createSeriesMarkers(candleSeries, [{
      time: data.signal_open_time as UTCTimestamp,
      // 实时模式中的完整 K 线可能继续变化，Signal 必须固定在检测价格而不是最终最高价。
      position: 'atPriceTop', price: Number(props.signalPrice),
      shape: 'arrowDown', color: '#d39b24', text: 'Signal',
    }])
    setChartCandles(data.candles)
    // 移动端断点会同时改变容器宽高，图表必须同步两者，避免内部 Canvas 保留桌面高度。
    resizeObserver = new ResizeObserver(([entry]) => chart?.applyOptions({
      width: entry.contentRect.width,
      height: entry.contentRect.height,
    }))
    resizeObserver.observe(container.value)
    if (mode.value === 'realtime') await loadRealtime()
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    loading.value = false
  }
}

onMounted(renderChart)
watch(mode, (value) => {
  if (!chart) return
  if (value === 'realtime') void loadRealtime()
  else {
    closeRealtime()
    error.value = ''
    if (snapshotData) setChartCandles(snapshotData.candles)
  }
})
onUnmounted(() => {
  disposed = true
  closeRealtime()
  resizeObserver?.disconnect()
  chart?.remove()
})
</script>

<template>
  <section class="chart-band">
    <div class="chart-head">
      <div class="chart-copy"><h2>{{ symbol }} · {{ timeframe }}</h2><span>{{ mode === 'realtime' ? '实时行情' : '检测时刻快照' }} · UTC+8</span></div>
      <div class="chart-tools">
        <div class="chart-legend"><span><i class="ema14" />EMA14 {{ ema14 }}</span><span><i class="ema50" />EMA50 {{ ema50 }}</span></div>
        <div class="mode-control">
          <span v-if="mode === 'realtime'" :class="['live-state', { connected: liveConnected }]">{{ liveConnected ? '已连接' : '连接中' }}</span>
          <el-segmented v-model="mode" :options="modeOptions" size="small" />
        </div>
      </div>
    </div>
    <div v-if="error" class="chart-error">{{ error }}</div>
    <div ref="container" class="chart-canvas" v-loading="loading" />
    <a class="chart-credit" href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView</a>
  </section>
</template>

<style scoped>
.chart-band { position: relative; margin-bottom: 24px; border: 1px solid #dfe4e9; border-radius: 7px; overflow: hidden; background: #fff; }
.chart-head { min-height: 66px; display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 12px 18px; border-bottom: 1px solid #e5e9ed; }
.chart-head h2 { margin: 0 0 4px; font-size: 16px; letter-spacing: 0; }
.chart-copy > span { color: #7d8997; font-size: 12px; }
.chart-tools { display: flex; align-items: center; gap: 22px; }
.chart-legend { display: flex; flex-wrap: wrap; gap: 18px; font-variant-numeric: tabular-nums; }
.chart-legend span { color: #4d5967; font-size: 12px; }
.chart-legend i { display: inline-block; width: 16px; height: 3px; margin-right: 7px; vertical-align: middle; }
.chart-legend i.ema14 { background: #2563eb; }
.chart-legend i.ema50 { background: #d97706; }
.mode-control { display: flex; align-items: center; gap: 9px; }
.live-state { color: #8a6570; font-size: 11px; white-space: nowrap; }
.live-state::before { content: ''; display: inline-block; width: 6px; height: 6px; margin-right: 5px; border-radius: 50%; background: #b8c0c9; vertical-align: 1px; }
.live-state.connected { color: #27755e; }
.live-state.connected::before { background: #1b9a72; }
.chart-canvas { width: 100%; height: 560px; }
.chart-error { padding: 12px 18px; color: #c54d4a; background: #fff1f0; }
.chart-credit { position: absolute; right: 12px; bottom: 7px; z-index: 2; color: #7d8997; font-size: 10px; text-decoration: none; }
@media (max-width: 620px) {
  .chart-head { align-items: flex-start; flex-direction: column; gap: 8px; }
  .chart-tools { width: 100%; align-items: flex-start; flex-direction: column; gap: 8px; }
  .mode-control { width: 100%; justify-content: space-between; }
  .chart-canvas { height: 430px; }
  .chart-legend { gap: 10px; }
}
</style>
