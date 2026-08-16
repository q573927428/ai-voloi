<!-- ==================== TradingView Signal K 线图表 ==================== -->
<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import { api, errorMessage } from '../api/client'

/** 图表定位所需的 Signal 标识与可见标题。 */
const props = defineProps<{ signalId: string; symbol: string; timeframe: string }>()
const container = ref<HTMLDivElement | null>(null)
const loading = ref(true)
const error = ref('')
const ema14 = ref('—')
const ema50 = ref('—')
const DEFAULT_VISIBLE_CANDLES = 300
const RIGHT_EMPTY_CANDLES = 20
let chart: IChartApi | null = null
let resizeObserver: ResizeObserver | null = null

/** 获取无未来数据快照并创建 TradingView Lightweight Charts 图表。 */
async function renderChart() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.signalChart(props.signalId)
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
      },
      localization: { locale: 'zh-CN' },
    })
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: '#14805e', downColor: '#c54d4a', borderVisible: false,
      wickUpColor: '#14805e', wickDownColor: '#c54d4a',
    })
    candles.setData(data.candles.map((item) => ({
      time: item.time as UTCTimestamp,
      open: Number(item.open), high: Number(item.high), low: Number(item.low), close: Number(item.close),
    })))
    const ema14Series = chart.addSeries(LineSeries, { color: '#2563eb', lineWidth: 2, priceLineVisible: false, lastValueVisible: true, title: 'EMA14' })
    const ema50Series = chart.addSeries(LineSeries, { color: '#d97706', lineWidth: 2, priceLineVisible: false, lastValueVisible: true, title: 'EMA50' })
    ema14Series.setData(data.candles.filter((item) => item.ema14 != null).map((item) => ({ time: item.time as UTCTimestamp, value: Number(item.ema14) })))
    ema50Series.setData(data.candles.filter((item) => item.ema50 != null).map((item) => ({ time: item.time as UTCTimestamp, value: Number(item.ema50) })))
    const volumeSeries = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: '', priceLineVisible: false, lastValueVisible: false })
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    volumeSeries.setData(data.candles.map((item) => ({
      time: item.time as UTCTimestamp,
      value: Number(item.volume),
      color: Number(item.close) >= Number(item.open) ? 'rgba(20,128,94,.35)' : 'rgba(197,77,74,.35)',
    })))
    createSeriesMarkers(candles, [{
      time: data.signal_open_time as UTCTimestamp,
      position: 'aboveBar', shape: 'arrowDown', color: '#d39b24', text: 'Signal',
    }])
    const signalCandle = data.candles[data.candles.length - 1]
    ema14.value = signalCandle?.ema14 == null ? '—' : Number(signalCandle.ema14).toLocaleString('zh-CN', { maximumFractionDigits: 8 })
    ema50.value = signalCandle?.ema50 == null ? '—' : Number(signalCandle.ema50).toLocaleString('zh-CN', { maximumFractionDigits: 8 })
    // 默认聚焦最近 200 根，并在最新 K 线右侧保留 20 根的观察空间。
    const lastLogicalIndex = data.candles.length - 1
    chart.timeScale().setVisibleLogicalRange({
      from: Math.max(0, lastLogicalIndex - DEFAULT_VISIBLE_CANDLES + 1),
      to: lastLogicalIndex + RIGHT_EMPTY_CANDLES,
    })
    // 移动端断点会同时改变容器宽高，图表必须同步两者，避免内部 Canvas 保留桌面高度。
    resizeObserver = new ResizeObserver(([entry]) => chart?.applyOptions({
      width: entry.contentRect.width,
      height: entry.contentRect.height,
    }))
    resizeObserver.observe(container.value)
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    loading.value = false
  }
}

onMounted(renderChart)
onUnmounted(() => { resizeObserver?.disconnect(); chart?.remove() })
</script>

<template>
  <section class="chart-band">
    <div class="chart-head">
      <div><h2>{{ symbol }} · {{ timeframe }}</h2><span>检测时刻 K 线快照</span></div>
      <div class="chart-legend"><span><i class="ema14" />EMA14 {{ ema14 }}</span><span><i class="ema50" />EMA50 {{ ema50 }}</span></div>
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
.chart-head div > span { color: #7d8997; font-size: 12px; }
.chart-legend { display: flex; flex-wrap: wrap; gap: 18px; font-variant-numeric: tabular-nums; }
.chart-legend span { color: #4d5967; }
.chart-legend i { display: inline-block; width: 16px; height: 3px; margin-right: 7px; vertical-align: middle; }
.chart-legend i.ema14 { background: #2563eb; }
.chart-legend i.ema50 { background: #d97706; }
.chart-canvas { width: 100%; height: 560px; }
.chart-error { padding: 12px 18px; color: #c54d4a; background: #fff1f0; }
.chart-credit { position: absolute; right: 12px; bottom: 7px; z-index: 2; color: #7d8997; font-size: 10px; text-decoration: none; }
@media (max-width: 620px) {
  .chart-head { align-items: flex-start; flex-direction: column; gap: 8px; }
  .chart-canvas { height: 430px; }
  .chart-legend { gap: 10px; }
}
</style>
