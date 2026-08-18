<!-- ==================== 合约资金流与持仓变化图表 ==================== -->
<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import {
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { api, errorMessage } from '../api/client'
import type { ChartLogicalViewport, ContractFundFlowData, ContractFundFlowPoint, FundFlowRegime } from '../types'
import { resolvePricePrecision } from '../utils/price'

/** 合约资金流图表定位信息，周期、逻辑窗口和十字光标均与上方 K 线同步。 */
interface ContractFundFlowChartProps {
  symbol: string
  timeframe: string
  viewport: ChartLogicalViewport | null
  crosshairTime: number | null
}

const props = defineProps<ContractFundFlowChartProps>()
const container = ref<HTMLDivElement | null>(null)
const loading = ref(false)
const error = ref('')
const data = ref<ContractFundFlowData | null>(null)
const hoveredPoint = ref<ContractFundFlowPoint | null>(null)
const tooltipStyle = ref<Record<string, string>>({})
let chart: IChartApi | null = null
let netFlowSeries: ISeriesApi<'Histogram'> | null = null
let priceSeries: ISeriesApi<'Line'> | null = null
let oiSeries: ISeriesApi<'Histogram'> | null = null
let resizeObserver: ResizeObserver | null = null
let refreshTimer: number | undefined
let requestId = 0

const regimeMeta: Record<FundFlowRegime, { label: string; type: 'success' | 'danger' | 'warning' | 'info' }> = {
  new_longs: { label: '新增多头', type: 'success' },
  new_shorts: { label: '新增空头', type: 'danger' },
  short_covering: { label: '空头回补', type: 'warning' },
  long_closing: { label: '多头平仓', type: 'danger' },
  mixed: { label: '方向分歧', type: 'info' },
  insufficient_data: { label: 'OI 数据不足', type: 'info' },
}

/** 将大额 USDT 和 OI 数量格式化为紧凑、可比较的读数。 */
function compact(value?: string | number | null): string {
  if (value == null) return '—'
  const number = Number(value)
  return Number.isFinite(number)
    ? new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 2 }).format(number)
    : '—'
}

/** 百分比统一展示方向符号，空值表示该时间桶没有新的 OI 采样。 */
function percent(value?: string | number | null): string {
  if (value == null) return '—'
  const number = Number(value)
  return Number.isFinite(number) ? `${number > 0 ? '+' : ''}${number.toFixed(2)}%` : '—'
}

/**
 * 使用与 K 线图相同的动态精度展示完整价格。
 * 价格轴必须避免紧凑缩写，否则用户无法直接与上方 K 线刻度对照。
 */
function formatPrice(value: number, precision: number): string {
  return Number.isFinite(value)
    ? value.toLocaleString('zh-CN', {
        minimumFractionDigits: precision,
        maximumFractionDigits: precision,
      })
    : '—'
}

/** 将 Lightweight Charts 时间值转换为 Unix 毫秒。 */
function timeToMilliseconds(time: Time | number): number {
  if (typeof time === 'number') return time * 1000
  if (typeof time === 'string') return Date.parse(`${time}T00:00:00Z`)
  return Date.UTC(time.year, time.month - 1, time.day)
}

/** 使用固定 UTC+8 输出横轴、十字光标和提示框时间。 */
function formatTime(time: Time | number, detailed = false): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
    ...(detailed ? { year: 'numeric' } : {}),
    hour12: false,
  }).format(timeToMilliseconds(time))
}

/** 根据时间轴刻度级别生成 UTC+8 标签。 */
function formatTick(time: Time, tickMarkType: number): string {
  const options: Intl.DateTimeFormatOptions = { timeZone: 'Asia/Shanghai', hour12: false, hourCycle: 'h23' }
  if (tickMarkType === 0) options.year = 'numeric'
  else if (tickMarkType === 1) options.month = 'short'
  else if (tickMarkType === 2) { options.month = '2-digit'; options.day = '2-digit' }
  else { options.hour = '2-digit'; options.minute = '2-digit' }
  return new Intl.DateTimeFormat('zh-CN', options).format(timeToMilliseconds(time))
}

/**
 * 用共同锚点换算逻辑索引差，使资金流柱与上方同一时间的 K 线占用完全相同的 X 坐标。
 */
function applyViewport() {
  if (!chart || !data.value || !props.viewport) return
  const anchorIndex = data.value.points.findIndex((point) => point.time === props.viewport!.anchorTime)
  if (anchorIndex < 0) return
  const indexShift = anchorIndex - props.viewport.anchorIndex
  chart.timeScale().setVisibleLogicalRange({
    from: props.viewport.from + indexShift,
    to: props.viewport.to + indexShift,
  })
}

/** 将最新接口数据写入共享时间轴上的资金流、价格和 OI 两个区域。 */
function setChartData() {
  if (!chart || !data.value || !netFlowSeries || !priceSeries || !oiSeries) return
  const points = data.value.points
  const latestPrice = Number(points[points.length - 1]?.close)
  const pricePrecision = resolvePricePrecision(latestPrice)
  priceSeries.applyOptions({
    priceFormat: { type: 'price', precision: pricePrecision, minMove: 10 ** -pricePrecision },
  })
  netFlowSeries.setData(points.map((point) => ({
    time: point.time as UTCTimestamp,
    value: Number(point.net_taker_flow),
    color: Number(point.net_taker_flow) >= 0 ? '#159a72' : '#d84f62',
  })))
  priceSeries.setData(points.map((point) => ({
    time: point.time as UTCTimestamp,
    value: Number(point.close),
  })))
  oiSeries.setData(points
    .filter((point) => point.open_interest_change_percent != null)
    .map((point) => ({
      time: point.time as UTCTimestamp,
      value: Number(point.open_interest_change_percent),
      color: Number(point.open_interest_change_percent) >= 0 ? '#3976c4' : '#8a6570',
    })))
  applyViewport()
}

/** 在图表范围内显示指定资金流点的详细提示框。 */
function showPointTooltip(point: ContractFundFlowPoint, x: number, y: number) {
  if (!container.value) return
  hoveredPoint.value = point
  const tooltipWidth = 238
  const left = Math.min(x + 14, Math.max(8, container.value.clientWidth - tooltipWidth - 8))
  const top = Math.max(8, Math.min(y + 14, container.value.clientHeight - 132))
  tooltipStyle.value = { left: `${left}px`, top: `${top}px` }
}

/** 更新悬浮明细；自定义浮层保留原图的资金流、价格、OI 和状态信息。 */
function handleCrosshairMove(param: MouseEventParams<Time>) {
  if (!param.time || !param.point || !data.value) {
    hoveredPoint.value = null
    return
  }
  const time = typeof param.time === 'number' ? param.time : null
  const point = time == null ? null : data.value.points.find((item) => item.time === time)
  if (point) showPointTooltip(point, param.point.x, param.point.y)
  else hoveredPoint.value = null
}

/** 创建与 K 线使用同一引擎和价格轴宽度的双区域资金流图。 */
function createFundFlowChart() {
  if (!container.value) return
  chart = createChart(container.value, {
    width: container.value.clientWidth,
    height: container.value.clientHeight,
    layout: { background: { type: ColorType.Solid, color: '#ffffff' }, textColor: '#5f6c7b' },
    grid: { vertLines: { color: '#edf0f3' }, horzLines: { color: '#edf0f3' } },
    leftPriceScale: { visible: true, minimumWidth: 72, borderColor: '#dce1e7' },
    rightPriceScale: { visible: true, minimumWidth: 84, borderColor: '#dce1e7' },
    timeScale: {
      borderColor: '#dce1e7', timeVisible: true, secondsVisible: false,
      rightOffset: 30, tickMarkFormatter: formatTick,
    },
    localization: { locale: 'zh-CN', timeFormatter: (time: Time) => formatTime(time, true) },
    // 下方图只跟随上方 K 线，避免单独拖动后破坏逐柱对应关系。
    handleScroll: false,
    handleScale: false,
  })
  netFlowSeries = chart.addSeries(HistogramSeries, {
    title: '主动净流', priceScaleId: 'left', priceLineVisible: false, lastValueVisible: false,
    priceFormat: { type: 'custom', formatter: compact, minMove: 0.01 },
  }, 0)
  priceSeries = chart.addSeries(LineSeries, {
    title: '价格', color: '#c28a0a', lineWidth: 2, priceScaleId: 'right',
    priceLineVisible: false, lastValueVisible: true,
  }, 0)
  oiSeries = chart.addSeries(HistogramSeries, {
    title: 'OI 变化', priceScaleId: 'left', priceLineVisible: false, lastValueVisible: false,
    priceFormat: { type: 'custom', formatter: (value: number) => `${value.toFixed(1)}%`, minMove: 0.1 },
  }, 1)
  netFlowSeries.priceScale().applyOptions({ scaleMargins: { top: 0.14, bottom: 0.12 } })
  priceSeries.priceScale().applyOptions({ scaleMargins: { top: 0.14, bottom: 0.12 } })
  oiSeries.priceScale().applyOptions({ scaleMargins: { top: 0.2, bottom: 0.12 } })
  chart.panes()[0]?.setHeight(250)
  chart.panes()[1]?.setHeight(100)
  chart.subscribeCrosshairMove(handleCrosshairMove)
  resizeObserver = new ResizeObserver(([entry]) => chart?.applyOptions({
    width: entry.contentRect.width,
    height: entry.contentRect.height,
  }))
  resizeObserver.observe(container.value)
}

/** 拉取当前交易对资金流；请求序号防止快速切换周期时旧响应覆盖新图。 */
async function load() {
  const currentRequest = ++requestId
  loading.value = true
  error.value = ''
  try {
    // 取 500 根以覆盖 K 线默认 200 根观察窗口及用户向左查看的常用范围。
    const response = await api.contractFundFlow(props.symbol, props.timeframe, 500)
    if (currentRequest !== requestId) return
    data.value = response
    await nextTick()
    setChartData()
  } catch (reason) {
    if (currentRequest === requestId) {
      data.value = null
      netFlowSeries?.setData([])
      priceSeries?.setData([])
      oiSeries?.setData([])
      error.value = errorMessage(reason)
    }
  } finally {
    if (currentRequest === requestId) loading.value = false
  }
}

onMounted(() => {
  createFundFlowChart()
  void load()
  refreshTimer = window.setInterval(load, 60_000)
})

watch(() => [props.symbol, props.timeframe], () => void load())
watch(() => props.viewport, applyViewport)
watch(() => props.crosshairTime, (time) => {
  if (!chart || !priceSeries) return
  if (time == null) {
    chart.clearCrosshairPosition()
    hoveredPoint.value = null
    return
  }
  const point = data.value?.points.find((item) => item.time === time)
  if (point) {
    chart.setCrosshairPosition(Number(point.close), time as UTCTimestamp, priceSeries)
    // 程序化同步十字光标不会触发鼠标事件，需要同时主动更新详细提示框。
    const x = chart.timeScale().timeToCoordinate(time as UTCTimestamp)
    const y = priceSeries.priceToCoordinate(Number(point.close))
    if (x != null && y != null) showPointTooltip(point, x, y)
  } else {
    chart.clearCrosshairPosition()
    hoveredPoint.value = null
  }
})

onUnmounted(() => {
  requestId += 1
  if (refreshTimer) window.clearInterval(refreshTimer)
  resizeObserver?.disconnect()
  chart?.unsubscribeCrosshairMove(handleCrosshairMove)
  chart?.remove()
})
</script>

<template>
  <section class="fund-flow-band">
    <div class="fund-flow-head">
      <div class="fund-flow-title">
        <div class="title-line">
          <h2>合约资金流</h2>
          <span>{{ symbol }} · {{ timeframe }} · {{ data?.points.length ?? 0 }} 根</span>
          <el-tag v-if="data" :type="regimeMeta[data.summary.regime].type" effect="plain" size="small">{{ regimeMeta[data.summary.regime].label }}</el-tag>
        </div>
        <span>主动买卖成交额差与 Open Interest 联合判断</span>
      </div>
      <div v-if="data" class="fund-flow-summary">
        <div><span>主动净流</span><strong :class="Number(data.summary.net_taker_flow) >= 0 ? 'positive' : 'negative'">{{ compact(data.summary.net_taker_flow) }} USDT</strong></div>
        <div><span>OI 变化</span><strong :class="Number(data.summary.open_interest_change_percent) >= 0 ? 'oi-up' : 'negative'">{{ percent(data.summary.open_interest_change_percent) }}</strong></div>
        <div><span>价格变化</span><strong :class="Number(data.summary.price_change_percent) >= 0 ? 'positive' : 'negative'">{{ percent(data.summary.price_change_percent) }}</strong></div>
      </div>
      <el-tooltip content="刷新资金流">
        <el-button class="refresh-button" circle text :icon="Refresh" aria-label="刷新资金流" :loading="loading" @click="load" />
      </el-tooltip>
    </div>
    <div v-if="error" class="fund-flow-error">{{ error }}</div>
    <div ref="container" class="fund-flow-canvas" v-loading="loading">
      <div class="fund-flow-legend" aria-hidden="true">
        <span><i class="net" />主动净流</span>
        <span><i class="price" />价格</span>
        <span><i class="oi" />OI 变化</span>
      </div>
      <div v-if="hoveredPoint" class="fund-flow-tooltip" :style="tooltipStyle">
        <strong>{{ formatTime(hoveredPoint.time, true) }}</strong>
        <span>主动净流：{{ compact(hoveredPoint.net_taker_flow) }} USDT</span>
        <span>价格：{{ formatPrice(Number(hoveredPoint.close), resolvePricePrecision(Number(hoveredPoint.close))) }}（{{ percent(hoveredPoint.price_change_percent) }}）</span>
        <span>OI 变化：{{ compact(hoveredPoint.open_interest_change) }}（{{ percent(hoveredPoint.open_interest_change_percent) }}）</span>
        <span>状态：{{ regimeMeta[hoveredPoint.regime].label }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.fund-flow-band { margin-bottom: 24px; border: 1px solid #dfe4e9; border-radius: 7px; overflow: hidden; background: #fff; }
.fund-flow-head { min-height: 72px; display: flex; align-items: center; gap: 18px; padding: 12px 18px; border-bottom: 1px solid #e5e9ed; }
.fund-flow-title { min-width: 220px; display: grid; gap: 5px; }
.title-line { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.title-line h2 { margin: 0; font-size: 16px; letter-spacing: 0; }
.title-line > span, .fund-flow-title > span { color: #7d8997; font-size: 12px; }
.fund-flow-summary { min-width: 0; margin-left: auto; display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 10px 24px; font-variant-numeric: tabular-nums; }
.fund-flow-summary > div { display: grid; gap: 3px; white-space: nowrap; }
.fund-flow-summary span { color: #8a95a2; font-size: 11px; }
.fund-flow-summary strong { color: #303b48; font-size: 13px; }
.fund-flow-summary .positive { color: #14805e; }
.fund-flow-summary .negative { color: #c54d4a; }
.fund-flow-summary .oi-up { color: #3976c4; }
.refresh-button { flex: 0 0 auto; }
.fund-flow-canvas { position: relative; width: 100%; height: 390px; }
.fund-flow-legend { position: absolute; z-index: 3; top: 7px; left: 80px; display: flex; gap: 16px; color: #687482; font-size: 11px; pointer-events: none; }
.fund-flow-legend span { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
.fund-flow-legend i { width: 12px; height: 3px; display: inline-block; }
.fund-flow-legend .net { height: 8px; background: #159a72; }
.fund-flow-legend .price { background: #c28a0a; }
.fund-flow-legend .oi { height: 8px; background: #3976c4; }
.fund-flow-tooltip { position: absolute; z-index: 4; width: 238px; display: grid; gap: 4px; padding: 9px 11px; border-radius: 5px; background: rgba(25, 31, 38, .94); color: #f4f6f8; font-size: 12px; line-height: 1.35; pointer-events: none; box-shadow: 0 4px 14px rgba(25, 31, 38, .16); }
.fund-flow-tooltip strong { margin-bottom: 1px; font-weight: 650; }
.fund-flow-error { padding: 12px 18px; color: #c54d4a; background: #fff1f0; }
@media (max-width: 620px) {
  .fund-flow-head { align-items: flex-start; flex-wrap: wrap; gap: 10px; }
  .fund-flow-title { min-width: 0; width: calc(100% - 48px); }
  .fund-flow-summary { width: 100%; margin-left: 0; justify-content: flex-start; gap: 10px 20px; }
  .refresh-button { position: absolute; right: 18px; }
  .fund-flow-canvas { height: 350px; }
  .fund-flow-legend { left: 76px; gap: 10px; }
}
</style>
