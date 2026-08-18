<!-- ==================== 合约资金流与持仓变化图表 ==================== -->
<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption, TooltipComponentFormatterCallbackParams } from 'echarts'
import { api, errorMessage } from '../api/client'
import type { ContractFundFlowData, FundFlowRegime } from '../types'
import { resolvePricePrecision } from '../utils/price'

/** 合约资金流图表定位信息，周期与上方 K 线保持同步。 */
interface ContractFundFlowChartProps {
  symbol: string
  timeframe: string
}

const props = defineProps<ContractFundFlowChartProps>()
const container = ref<HTMLDivElement | null>(null)
const loading = ref(false)
const error = ref('')
const data = ref<ContractFundFlowData | null>(null)
let chart: ECharts | null = null
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

/** 使用固定 UTC+8 输出横轴与提示框时间。 */
function formatTime(unixSeconds: number, detailed = false): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
    ...(detailed ? { year: 'numeric' } : {}),
    hour12: false,
  }).format(unixSeconds * 1000)
}

/** 根据最新接口数据生成共享时间轴的资金流、价格和 OI 双区域图。 */
function renderChart() {
  if (!chart || !data.value) return
  const points = data.value.points
  const categories = points.map((point) => formatTime(point.time))
  const latestPrice = Number(points[points.length - 1]?.close)
  const pricePrecision = resolvePricePrecision(latestPrice)
  const option: EChartsOption = {
    animationDuration: 250,
    grid: [
      { left: 66, right: 72, top: 28, height: '56%' },
      { left: 66, right: 72, top: '73%', height: '16%' },
    ],
    legend: {
      top: 0,
      left: 62,
      itemWidth: 12,
      itemHeight: 8,
      textStyle: { color: '#687482', fontSize: 11 },
      data: ['主动净流', '价格', 'OI 变化'],
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(25, 31, 38, .94)',
      borderWidth: 0,
      textStyle: { color: '#f4f6f8', fontSize: 12 },
      formatter: (params: TooltipComponentFormatterCallbackParams) => {
        const items = Array.isArray(params) ? params : [params]
        const index = items[0]?.dataIndex ?? 0
        const point = points[index]
        if (!point) return ''
        const state = regimeMeta[point.regime].label
        return [
          formatTime(point.time, true),
          `主动净流：${compact(point.net_taker_flow)} USDT`,
          `价格：${formatPrice(Number(point.close), pricePrecision)}（${percent(point.price_change_percent)}）`,
          `OI 变化：${compact(point.open_interest_change)}（${percent(point.open_interest_change_percent)}）`,
          `状态：${state}`,
        ].join('<br/>')
      },
    },
    xAxis: [
      { type: 'category', gridIndex: 0, data: categories, boundaryGap: true, axisLabel: { show: false }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#dce1e7' } } },
      { type: 'category', gridIndex: 1, data: categories, boundaryGap: true, axisLabel: { color: '#7d8997', fontSize: 10, interval: 'auto' }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#dce1e7' } } },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, position: 'left', axisLabel: { color: '#7d8997', fontSize: 10, formatter: (value: number) => compact(value) }, splitLine: { lineStyle: { color: '#edf0f3' } } },
      { type: 'value', gridIndex: 0, position: 'right', scale: true, axisLabel: { color: '#b18113', fontSize: 10, formatter: (value: number) => formatPrice(value, pricePrecision) }, splitLine: { show: false } },
      { type: 'value', gridIndex: 1, position: 'left', axisLabel: { color: '#7d8997', fontSize: 10, formatter: (value: number) => `${value.toFixed(1)}%` }, splitLine: { lineStyle: { color: '#edf0f3' } } },
    ],
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 }],
    series: [
      {
        name: '主动净流', type: 'bar', xAxisIndex: 0, yAxisIndex: 0,
        data: points.map((point) => ({
          value: Number(point.net_taker_flow),
          itemStyle: { color: Number(point.net_taker_flow) >= 0 ? '#159a72' : '#d84f62' },
        })),
        itemStyle: { color: '#159a72' },
        barMaxWidth: 10,
      },
      {
        name: '价格', type: 'line', xAxisIndex: 0, yAxisIndex: 1,
        data: points.map((point) => Number(point.close)),
        symbol: 'none', itemStyle: { color: '#c28a0a' }, lineStyle: { color: '#c28a0a', width: 1.5 },
      },
      {
        name: 'OI 变化', type: 'bar', xAxisIndex: 1, yAxisIndex: 2,
        data: points.map((point) => point.open_interest_change_percent == null ? null : ({
          value: Number(point.open_interest_change_percent),
          itemStyle: { color: Number(point.open_interest_change_percent) >= 0 ? '#3976c4' : '#8a6570' },
        })),
        itemStyle: { color: '#3976c4' },
        barMaxWidth: 10,
      },
    ],
  }
  chart.setOption(option, true)
}

/** 拉取当前交易对资金流；请求序号防止快速切换周期时旧响应覆盖新图。 */
async function load() {
  const currentRequest = ++requestId
  loading.value = true
  error.value = ''
  try {
    const response = await api.contractFundFlow(props.symbol, props.timeframe)
    if (currentRequest !== requestId) return
    data.value = response
    await nextTick()
    renderChart()
  } catch (reason) {
    if (currentRequest === requestId) {
      data.value = null
      chart?.clear()
      error.value = errorMessage(reason)
    }
  } finally {
    if (currentRequest === requestId) loading.value = false
  }
}

onMounted(() => {
  if (container.value) {
    chart = echarts.init(container.value)
    resizeObserver = new ResizeObserver(() => chart?.resize())
    resizeObserver.observe(container.value)
  }
  void load()
  refreshTimer = window.setInterval(load, 60_000)
})

watch(() => [props.symbol, props.timeframe], () => void load())

onUnmounted(() => {
  requestId += 1
  if (refreshTimer) window.clearInterval(refreshTimer)
  resizeObserver?.disconnect()
  chart?.dispose()
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
    <div ref="container" class="fund-flow-canvas" v-loading="loading" />
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
.fund-flow-canvas { width: 100%; height: 390px; }
.fund-flow-error { padding: 12px 18px; color: #c54d4a; background: #fff1f0; }
@media (max-width: 620px) {
  .fund-flow-head { align-items: flex-start; flex-wrap: wrap; gap: 10px; }
  .fund-flow-title { min-width: 0; width: calc(100% - 48px); }
  .fund-flow-summary { width: 100%; margin-left: 0; justify-content: flex-start; gap: 10px 20px; }
  .refresh-button { position: absolute; right: 18px; }
  .fund-flow-canvas { height: 350px; }
}
</style>
