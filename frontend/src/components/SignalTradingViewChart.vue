<!-- ==================== TradingView K 线图表 ==================== -->
<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type LogicalRange,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { api, errorMessage } from '../api/client'
import type { ChartLogicalViewport, RealtimeKlineMessage, SignalChartCandle, SignalChartData } from '../types'
import { resolvePricePrecision } from '../utils/price'
import CoinIcon from './CoinIcon.vue'
import ContractFundFlowChart from './ContractFundFlowChart.vue'

/** K 线图表定位、标题及可选 Signal 快照信息。 */
interface SignalTradingViewChartProps {
  /** Signal 详情传入 ID 后可在检测快照与实时行情之间切换。 */
  signalId?: string
  symbol: string
  timeframe: string
  isTradfi?: boolean
}

const props = withDefaults(defineProps<SignalTradingViewChartProps>(), { isTradfi: false })
const container = ref<HTMLDivElement | null>(null)
const loading = ref(true)
const error = ref('')
const EMA_PERIODS = [9, 14, 21, 50, 100, 200] as const
type EmaPeriod = typeof EMA_PERIODS[number]
const primaryEmaPeriod = ref<EmaPeriod>(14)
const secondaryEmaPeriod = ref<EmaPeriod>(50)
const primaryEmaValue = ref('—')
const secondaryEmaValue = ref('—')
const latestCandle = ref<SignalChartCandle | null>(null)
const candleCount = ref(0)
const latestPriceDirection = ref<'up' | 'down' | 'flat'>('flat')
const mode = ref<'snapshot' | 'realtime'>('realtime')
const liveConnected = ref(false)
const selectedTimeframe = ref(props.timeframe)
const timeframeOptions = ref<string[]>([props.timeframe])
const chartViewport = ref<ChartLogicalViewport | null>(null)
const crosshairTime = ref<number | null>(null)
const DEFAULT_VISIBLE_CANDLES = 200
const RIGHT_EMPTY_CANDLES = 30
let chart: IChartApi | null = null
let candleSeries: ISeriesApi<'Candlestick'> | null = null
let primaryEmaSeries: ISeriesApi<'Line'> | null = null
let secondaryEmaSeries: ISeriesApi<'Line'> | null = null
let volumeSeries: ISeriesApi<'Histogram'> | null = null
let signalMarkers: ISeriesMarkersPluginApi<Time> | null = null
let resizeObserver: ResizeObserver | null = null
let snapshotData: SignalChartData | null = null
let liveSocket: WebSocket | null = null
let reconnectTimer: number | undefined
let lastRealtimeCandleTime: number | null = null
let currentPricePrecision: number | null = null
let realtimeRequestId = 0
let disposed = false
let visibleCandles: SignalChartCandle[] = []

/**
 * 发布 K 线当前逻辑窗口，让历史长度不同的资金流图仍能按同一根 K 线定位。
 */
function publishChartViewport(range: LogicalRange | null = chart?.timeScale().getVisibleLogicalRange() ?? null) {
  // 两个接口对当前未收盘 K 线的可见时点可能短暂不同，使用倒数第二根稳定时间桶作为共同锚点。
  const anchorIndex = Math.max(0, visibleCandles.length - 2)
  const anchor = visibleCandles[anchorIndex]
  if (!range || !anchor) return
  chartViewport.value = {
    anchorTime: anchor.time,
    anchorIndex,
    from: range.from,
    to: range.to,
  }
}

const modeOptions = [
  { label: '检测快照', value: 'snapshot' },
  { label: '实时行情', value: 'realtime' },
]
const hasSnapshot = computed(() => Boolean(props.signalId))

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

/** 读取指定周期 EMA，兼容 JSON 将数字键序列化为字符串的规则。 */
function emaValue(candle: SignalChartCandle | undefined, period: EmaPeriod): string | null {
  return candle?.emas[String(period)] ?? null
}

/** 格式化并更新标题图例中的两组当前 EMA 值。 */
function updateLegend(candle?: SignalChartCandle) {
  const format = (value: string | null) => value == null
    ? '—'
    : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 8 })
  primaryEmaValue.value = format(emaValue(candle, primaryEmaPeriod.value))
  secondaryEmaValue.value = format(emaValue(candle, secondaryEmaPeriod.value))
}

/** 按图表当前动态精度格式化行情价格。 */
function formatMarketPrice(value?: string): string {
  if (value == null) return '—'
  const number = Number(value)
  return Number.isFinite(number)
    ? number.toLocaleString('zh-CN', {
        minimumFractionDigits: currentPricePrecision ?? 2,
        maximumFractionDigits: currentPricePrecision ?? 2,
      })
    : '—'
}

/** 紧凑格式化本根 K 线成交量。 */
function formatMarketVolume(value?: string): string {
  if (value == null) return '—'
  const number = Number(value)
  return Number.isFinite(number)
    ? new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 2 }).format(number)
    : '—'
}

/** 格式化 Signal 快照价格，去除数据库小数末尾的无效零。 */
function formatSignalPrice(value: string): string {
  const number = Number(value)
  return Number.isFinite(number)
    ? number.toLocaleString('en-US', { useGrouping: false, maximumFractionDigits: 12 })
    : value
}

/** 计算当前 K 线相对开盘价的涨跌幅。 */
function candleChangePercent(candle: SignalChartCandle | null): number {
  if (!candle || Number(candle.open) === 0) return 0
  return (Number(candle.close) - Number(candle.open)) / Number(candle.open) * 100
}

/** 为蜡烛和两条可选 EMA 同步应用动态价格精度。 */
function applyPricePrecision(price: number) {
  const precision = resolvePricePrecision(price)
  if (precision === currentPricePrecision) return
  currentPricePrecision = precision
  const priceFormat = { type: 'price' as const, precision, minMove: 10 ** -precision }
  candleSeries?.applyOptions({ priceFormat })
  primaryEmaSeries?.applyOptions({ priceFormat })
  secondaryEmaSeries?.applyOptions({ priceFormat })
}

/** 按当前手动选择的周期重绘两条 EMA，不重新请求行情数据。 */
function refreshEmaSeries() {
  const toSeriesData = (period: EmaPeriod) => visibleCandles
    .filter((item) => emaValue(item, period) != null)
    .map((item) => ({
      time: item.time as UTCTimestamp,
      value: Number(emaValue(item, period)),
    }))
  primaryEmaSeries?.applyOptions({ title: `EMA${primaryEmaPeriod.value}` })
  secondaryEmaSeries?.applyOptions({ title: `EMA${secondaryEmaPeriod.value}` })
  primaryEmaSeries?.setData(toSeriesData(primaryEmaPeriod.value))
  secondaryEmaSeries?.setData(toSeriesData(secondaryEmaPeriod.value))
  updateLegend(visibleCandles[visibleCandles.length - 1])
}

/** 将 Binance 周期转换为秒，用于判断 Signal 时刻实际归属哪根 K 线。 */
function timeframeSeconds(timeframe: string): number | null {
  const match = /^(\d+)([mhdw])$/.exec(timeframe)
  if (!match) return null
  const unit = match[2] as 'm' | 'h' | 'd' | 'w'
  const unitSeconds = { m: 60, h: 3600, d: 86400, w: 604800 }[unit]
  return Number(match[1]) * unitSeconds
}

/**
 * 将 Signal 标记绑定到当前周期中实际包含检测时刻的蜡烛。
 * 当前窗口缺失目标蜡烛时主动隐藏标记，避免标到相邻但不对应的 K 线上。
 */
function updateSignalMarker(items: SignalChartCandle[]) {
  if (!signalMarkers || !snapshotData) return
  const signalCandle = snapshotData.candles.find((item) => item.is_signal)
  const duration = timeframeSeconds(selectedTimeframe.value)
  if (!signalCandle || duration == null) {
    signalMarkers.setMarkers([])
    return
  }
  const containingCandle = items.find((item) => (
    item.time <= snapshotData!.signal_time && snapshotData!.signal_time < item.time + duration
  ))
  if (!containingCandle) {
    signalMarkers.setMarkers([])
    return
  }
  signalMarkers.setMarkers([{
    time: containingCandle.time as UTCTimestamp,
    // Signal 价格来自不可变快照，横坐标则跟随当前周期所归属的蜡烛。
    position: 'atPriceTop', price: Number(signalCandle.close),
    shape: 'arrowDown', color: '#d39b24', text: `Signal @${formatSignalPrice(signalCandle.close)}`,
  }])
}

/** 将完整窗口写入全部图表序列，并恢复默认观察范围。 */
function setChartCandles(items: SignalChartCandle[]) {
  visibleCandles = items
  candleCount.value = items.length
  const latest = items[items.length - 1]
  if (latest) applyPricePrecision(Number(latest.close))
  latestPriceDirection.value = 'flat'
  latestCandle.value = latest ?? null
  candleSeries?.setData(items.map((item) => ({
    time: item.time as UTCTimestamp,
    open: Number(item.open), high: Number(item.high), low: Number(item.low), close: Number(item.close),
  })))
  refreshEmaSeries()
  volumeSeries?.setData(items.map((item) => ({
    time: item.time as UTCTimestamp,
    value: Number(item.volume),
    color: Number(item.close) >= Number(item.open) ? 'rgba(20,128,94,.35)' : 'rgba(197,77,74,.35)',
  })))
  updateSignalMarker(items)
  lastRealtimeCandleTime = latest?.time ?? null
  const lastLogicalIndex = items.length - 1
  if (lastLogicalIndex >= 0) {
    chart?.timeScale().setVisibleLogicalRange({
      from: Math.max(0, lastLogicalIndex - DEFAULT_VISIBLE_CANDLES + 1),
      to: lastLogicalIndex + RIGHT_EMPTY_CANDLES,
    })
    publishChartViewport()
  }
}

/** 将 WebSocket 增量同步到蜡烛、成交量和当前选择的两条 EMA 序列。 */
function updateRealtimeCandle(item: SignalChartCandle) {
  const isNewCandle = lastRealtimeCandleTime !== item.time
  const time = item.time as UTCTimestamp
  const previousPrice = Number(latestCandle.value?.close)
  const currentPrice = Number(item.close)
  if (Number.isFinite(previousPrice) && currentPrice > previousPrice) latestPriceDirection.value = 'up'
  else if (Number.isFinite(previousPrice) && currentPrice < previousPrice) latestPriceDirection.value = 'down'
  applyPricePrecision(Number(item.close))
  latestCandle.value = item
  if (isNewCandle) visibleCandles.push(item)
  else if (visibleCandles.length) visibleCandles[visibleCandles.length - 1] = item
  candleCount.value = visibleCandles.length
  candleSeries?.update({
    time, open: Number(item.open), high: Number(item.high), low: Number(item.low), close: Number(item.close),
  })
  volumeSeries?.update({
    time, value: Number(item.volume),
    color: Number(item.close) >= Number(item.open) ? 'rgba(20,128,94,.35)' : 'rgba(197,77,74,.35)',
  })
  const primaryValue = emaValue(item, primaryEmaPeriod.value)
  const secondaryValue = emaValue(item, secondaryEmaPeriod.value)
  if (primaryValue != null) primaryEmaSeries?.update({ time, value: Number(primaryValue) })
  if (secondaryValue != null) secondaryEmaSeries?.update({ time, value: Number(secondaryValue) })
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
  const socket = new WebSocket(`${base}/ws/klines/${encodeURIComponent(props.symbol)}/${encodeURIComponent(selectedTimeframe.value)}`)
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
  const requestId = ++realtimeRequestId
  const timeframe = selectedTimeframe.value
  // 周期变化后先断开旧订阅，避免旧周期的增量数据混入新图表。
  closeRealtime()
  loading.value = true
  error.value = ''
  try {
    const data = await api.realtimeChart(props.symbol, timeframe)
    if (requestId !== realtimeRequestId || mode.value !== 'realtime' || timeframe !== selectedTimeframe.value) return
    setChartCandles(data.candles)
    connectRealtime()
  } catch (reason) {
    if (requestId === realtimeRequestId) error.value = errorMessage(reason)
  } finally {
    if (requestId === realtimeRequestId) loading.value = false
  }
}

/** 读取运行中的监控周期，确保切换项与后端实时行情能力保持一致。 */
async function loadTimeframeOptions() {
  try {
    const config = await api.config()
    // 保持系统配置中的周期顺序；Signal 自身周期仅在配置已移除它时补到末尾。
    timeframeOptions.value = Array.from(new Set([...config.timeframes, props.timeframe]))
  } catch {
    // 配置接口异常不影响图表主流程，至少保留 Signal 自身周期可用。
    timeframeOptions.value = [props.timeframe]
  }
}

/** 创建 TradingView Lightweight Charts 图表，并按需加载 Signal 快照。 */
async function renderChart() {
  loading.value = true
  error.value = ''
  try {
    // 市场池入口没有 Signal 上下文，只创建实时图表；详情页仍先保留不可变快照。
    const data = props.signalId ? await api.signalChart(props.signalId) : null
    snapshotData = data
    await nextTick()
    if (!container.value) return
    chart = createChart(container.value, {
      width: container.value.clientWidth,
      height: container.value.clientHeight,
      layout: { background: { type: ColorType.Solid, color: '#ffffff' }, textColor: '#5f6c7b' },
      grid: { vertLines: { color: '#edf0f3' }, horzLines: { color: '#edf0f3' } },
      rightPriceScale: { borderColor: '#dce1e7', scaleMargins: { top: 0.08, bottom: 0.22 } },
      leftPriceScale: { visible: true, borderVisible: false, minimumWidth: 72 },
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
    primaryEmaSeries = chart.addSeries(LineSeries, { color: '#2563eb', lineWidth: 2, priceLineVisible: false, lastValueVisible: true, title: 'EMA14' })
    secondaryEmaSeries = chart.addSeries(LineSeries, { color: '#d97706', lineWidth: 3, priceLineVisible: false, lastValueVisible: true, title: 'EMA50' })
    volumeSeries = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: '', priceLineVisible: false, lastValueVisible: false })
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    const signalCandle = data?.candles.find((item) => item.is_signal)
    if (signalCandle) {
      // 只在 Y 轴标出不可变的 Signal 快照价，不绘制横线以免遮挡 K 线。
      candleSeries.createPriceLine({
        price: Number(signalCandle.close),
        color: '#d39b24',
        lineVisible: false,
        axisLabelVisible: true,
        axisLabelColor: '#d39b24',
        axisLabelTextColor: '#ffffff',
        title: 'Signal',
      })
    }
    signalMarkers = createSeriesMarkers(candleSeries, [])
    chart.priceScale('right').applyOptions({ minimumWidth: 84 })
    chart.timeScale().subscribeVisibleLogicalRangeChange(publishChartViewport)
    chart.subscribeCrosshairMove((param) => {
      crosshairTime.value = typeof param.time === 'number' ? param.time : null
    })
    if (data) setChartCandles(data.candles)
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

onMounted(() => {
  void loadTimeframeOptions()
  void renderChart()
})
watch(mode, (value) => {
  if (!chart) return
  if (value === 'realtime') void loadRealtime()
  else if (hasSnapshot.value) {
    realtimeRequestId += 1
    closeRealtime()
    error.value = ''
    // 检测快照只对 Signal 原始周期有不可变的盘中数据，切回快照时同步恢复该周期。
    selectedTimeframe.value = props.timeframe
    if (snapshotData) setChartCandles(snapshotData.candles)
    loading.value = false
  }
})
watch(selectedTimeframe, (value) => {
  if (!chart) return
  // 选择其他周期代表查看当前市场行情；快照模式没有其他周期的盘中不可变数据。
  if (mode.value === 'snapshot' && value !== props.timeframe) {
    mode.value = 'realtime'
    return
  }
  if (mode.value === 'realtime') void loadRealtime()
})
watch([primaryEmaPeriod, secondaryEmaPeriod], refreshEmaSeries)
onUnmounted(() => {
  disposed = true
  closeRealtime()
  resizeObserver?.disconnect()
  chart?.timeScale().unsubscribeVisibleLogicalRangeChange(publishChartViewport)
  chart?.remove()
})
</script>

<template>
  <section class="chart-band">
    <div class="chart-head">
      <div class="chart-primary">
        <div class="chart-copy"><h2><CoinIcon :symbol="symbol" :size="16" />{{ symbol }}<el-tooltip v-if="isTradfi" content="TradFi"><el-tag class="chart-tradfi" type="warning" effect="plain" size="small">T</el-tag></el-tooltip><span>· {{ selectedTimeframe }}</span></h2><span>{{ mode === 'realtime' ? '实时行情' : '检测时刻快照' }} · UTC+8 · 共 {{ candleCount }} 根 K 线</span></div>
        <div v-if="latestCandle" class="market-strip">
          <div class="market-item latest"><span>最新价格</span><strong :class="latestPriceDirection">{{ formatMarketPrice(latestCandle.close) }}</strong></div>
          <div class="market-item"><span>变化</span><strong :class="candleChangePercent(latestCandle) >= 0 ? 'up' : 'down'">{{ candleChangePercent(latestCandle) >= 0 ? '+' : '' }}{{ candleChangePercent(latestCandle).toFixed(2) }}%</strong></div>
          <div class="market-item"><span>开</span><strong>{{ formatMarketPrice(latestCandle.open) }}</strong></div>
          <div class="market-item"><span>高</span><strong>{{ formatMarketPrice(latestCandle.high) }}</strong></div>
          <div class="market-item"><span>低</span><strong>{{ formatMarketPrice(latestCandle.low) }}</strong></div>
          <div class="market-item"><span>收</span><strong>{{ formatMarketPrice(latestCandle.close) }}</strong></div>
          <div class="market-item"><span>量</span><strong>{{ formatMarketVolume(latestCandle.volume) }}</strong></div>
        </div>
      </div>
      <div class="chart-tools">
        <div class="chart-legend">
          <label class="ema-control">
            <i class="ema-primary" />
            <el-select v-model="primaryEmaPeriod" size="small" aria-label="第一条 EMA 周期">
              <el-option
                v-for="period in EMA_PERIODS" :key="period" :label="`EMA${period}`"
                :value="period" :disabled="period === secondaryEmaPeriod"
              />
            </el-select>
            <span>{{ primaryEmaValue }}</span>
          </label>
          <label class="ema-control">
            <i class="ema-secondary" />
            <el-select v-model="secondaryEmaPeriod" size="small" aria-label="第二条 EMA 周期">
              <el-option
                v-for="period in EMA_PERIODS" :key="period" :label="`EMA${period}`"
                :value="period" :disabled="period === primaryEmaPeriod"
              />
            </el-select>
            <span>{{ secondaryEmaValue }}</span>
          </label>
        </div>
        <div class="mode-control">
          <el-segmented v-model="selectedTimeframe" :options="timeframeOptions" size="small" aria-label="K 线周期" />
          <span v-if="mode === 'realtime'" :class="['live-state', { connected: liveConnected }]">{{ liveConnected ? '已连接' : '连接中' }}</span>
          <el-segmented v-if="hasSnapshot" v-model="mode" :options="modeOptions" size="small" />
        </div>
      </div>
    </div>
    <div v-if="error" class="chart-error">{{ error }}</div>
    <div ref="container" class="chart-canvas" v-loading="loading" />
    <a class="chart-credit" href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView</a>
  </section>
  <ContractFundFlowChart
    :symbol="symbol"
    :timeframe="selectedTimeframe"
    :viewport="chartViewport"
    :crosshair-time="crosshairTime"
  />
</template>

<style scoped>
.chart-band { position: relative; margin-bottom: 24px; border: 1px solid #dfe4e9; border-radius: 7px; overflow: hidden; background: #fff; }
.chart-head { min-height: 66px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px 20px; padding: 12px 18px; border-bottom: 1px solid #e5e9ed; }
.chart-head h2 { margin: 0 0 4px; font-size: 16px; letter-spacing: 0; display: flex; align-items: center; gap: 5px; }
.chart-tradfi { font-weight: 650; }
.chart-copy > span { color: #7d8997; font-size: 12px; }
.chart-primary { min-width: 0; display: flex; align-items: center; flex-wrap: wrap; gap: 12px 24px; }
.chart-copy { flex: 0 0 auto; }
.chart-tools { min-width: 0; display: flex; align-items: center; justify-content: flex-end; gap: 22px; }
.market-strip { display: flex; flex-wrap: wrap; gap: 8px 16px; font-variant-numeric: tabular-nums; }
.market-item { display: grid; gap: 2px; white-space: nowrap; }
.market-item span { color: #8a95a2; font-size: 13px; }
.market-item strong { color: #303b48; font-size: 12px; font-weight: 600; }
.market-item.latest strong { font-size: 14px; transition: color .15s ease; }
.market-item strong.up { color: #14805e; }
.market-item strong.down { color: #c54d4a; }
.chart-legend { display: flex; flex-wrap: wrap; gap: 18px; font-variant-numeric: tabular-nums; }
.ema-control { display: flex; align-items: center; gap: 7px; color: #4d5967; font-size: 12px; }
.ema-control :deep(.el-select) { width: 86px; }
.ema-control > span { min-width: 70px; font-variant-numeric: tabular-nums; }
.chart-legend i { display: inline-block; width: 16px; height: 3px; margin-right: 7px; vertical-align: middle; }
.chart-legend i.ema-primary { background: #2563eb; }
.chart-legend i.ema-secondary { background: #d97706; }
.mode-control { display: flex; align-items: center; flex-wrap: wrap; justify-content: flex-end; gap: 9px; }
.live-state { color: #8a6570; font-size: 11px; white-space: nowrap; }
.live-state::before { content: ''; display: inline-block; width: 6px; height: 6px; margin-right: 5px; border-radius: 50%; background: #b8c0c9; vertical-align: 1px; }
.live-state.connected { color: #27755e; }
.live-state.connected::before { background: #1b9a72; }
.chart-canvas { width: 100%; height: 560px; }
.chart-error { padding: 12px 18px; color: #c54d4a; background: #fff1f0; }
.chart-credit { position: absolute; right: 12px; bottom: 7px; z-index: 2; color: #7d8997; font-size: 10px; text-decoration: none; }
@media (max-width: 620px) {
  .chart-head { align-items: flex-start; flex-direction: column; gap: 8px; }
  .chart-primary { width: 100%; align-items: flex-start; flex-direction: column; gap: 8px; }
  .chart-tools { width: 100%; align-items: flex-start; flex-direction: column; gap: 8px; }
  .market-strip { gap: 8px 14px; }
  .mode-control { width: 100%; justify-content: flex-start; }
  .chart-canvas { height: 430px; }
  .chart-legend { gap: 10px; }
}
</style>
