<!-- ==================== Signal 数据表格 ==================== -->
<script setup lang="ts">
import { computed } from 'vue'
import { View } from '@element-plus/icons-vue'
import type { SignalSnapshot } from '../types'
import { resolvePricePrecision } from '../utils/price'
import FuturePerformanceSparkline from './FuturePerformanceSparkline.vue'
import MarketSymbol from './MarketSymbol.vue'

/** 表格输入与跳转事件，rows 为不可变快照列表。 */
const props = defineProps<{ rows: SignalSnapshot[]; loading?: boolean; prioritizeResonance?: boolean }>()
defineEmits<{ open: [id: string] }>()

type GroupedSignalRow = SignalSnapshot & {
  groupIndex: number
  groupSize: number
}

const compact = (value: string) => Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 2 }).format(Number(value))
const dateTime = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false })

/** 按价格数量级显示有效精度，同时省略无意义的尾随零。 */
function price(value: string): string {
  const number = Number(value)
  return number.toLocaleString('zh-CN', { maximumFractionDigits: resolvePricePrecision(number) })
}

/** 将扫描进度约束在进度条支持的 0–100 范围内，避免异常数据造成视觉溢出。 */
function progressPercent(value: string): number {
  const number = Number(value)
  if (!Number.isFinite(number)) return 0
  return Math.min(100, Math.max(0, number))
}

/** 进度条标签保留一位小数，与原进度文本精度一致。 */
function progressLabel(percentage: number): string {
  return `${percentage.toFixed(1)}%`
}

/** K 线越接近完成，进度条越偏向暖色，连续色相便于直观看出完成程度。 */
function progressColor(percentage: number): string {
  const normalized = Math.min(100, Math.max(0, percentage)) / 100
  const hue = 207 - 189 * normalized
  const saturation = 46 + 18 * normalized
  return `hsl(${hue.toFixed(1)}  ${saturation.toFixed(1)}% 46%)`
}

/** 将 Signal 实际使用的 OI 回看分钟数格式化为紧凑周期标签。 */
function oiWindow(minutes: number | null) {
  if (minutes == null) return '旧口径'
  if (minutes % 1440 === 0) return `${minutes / 1440}d`
  if (minutes % 60 === 0) return `${minutes / 60}h`
  return `${minutes}m`
}

/** 将 Binance 周期转为分钟，用于组内排序和颜色映射。 */
function timeframeMinutes(timeframe: string): number {
  const match = /^(\d+)([mhdw])$/i.exec(timeframe)
  if (!match) return 0

  const unitMinutes = { m: 1, h: 60, d: 1440, w: 10080 }[match[2].toLowerCase() as 'm' | 'h' | 'd' | 'w']
  return Number(match[1]) * unitMinutes
}

/** 按周期时长返回标签色阶，时间越长颜色越深。 */
function timeframeClass(timeframe: string): string {
  const minutes = timeframeMinutes(timeframe)
  if (minutes <= 15) return 'timeframe-level-1'
  if (minutes <= 30) return 'timeframe-level-2'
  if (minutes <= 60) return 'timeframe-level-3'
  if (minutes <= 240) return 'timeframe-level-4'
  return 'timeframe-level-5'
}

/** 格式化最大涨跌幅；接口中的正负号直接表达价格变化方向。 */
function performancePercent(value: string | null | undefined): string {
  if (value == null || value === '') return '待计算'
  const number = Number(value)
  if (!Number.isFinite(number)) return '待计算'
  return `${number > 0 ? '+' : ''}${number.toFixed(3)}%`
}

/** 最大值和最小值按实际符号着色，绝对涨跌幅超过 5% 时加粗。 */
function performanceClass(value: string | null | undefined): string {
  if (value == null || value === '' || !Number.isFinite(Number(value))) return 'pending'
  const number = Number(value)
  const toneClass = number >= 0 ? 'positive' : 'negative'
  return Math.abs(number) > 5 ? `${toneClass} performance-strong` : toneClass
}

/** 普通正数保持默认文字色，仅超过 5x 或 5% 时使用深绿色突出显示。 */
function standoutMetricClass(value: string): string {
  return Number(value) > 5 ? 'signal-metric standout-metric' : 'signal-metric'
}

/**
 * 将同一轮扫描中同交易对的多周期 Signal 收拢在一起。
 * 切换指标排序时保留接口返回的组顺序；默认按时间排序时，同一扫描内优先显示共振周期更多的交易对。
 */
const groupedRows = computed<GroupedSignalRow[]>(() => {
  const groups = new Map<string, { rank: number; rows: SignalSnapshot[] }>()
  props.rows.forEach((row, rank) => {
    const key = `${row.detected_at}\u0000${row.symbol}`
    const group = groups.get(key)
    if (group) group.rows.push(row)
    else groups.set(key, { rank, rows: [row] })
  })

  const orderedGroups = [...groups.values()]
  if (props.prioritizeResonance) {
    orderedGroups.sort((left, right) => {
      const detectedDifference = new Date(right.rows[0].detected_at).getTime() - new Date(left.rows[0].detected_at).getTime()
      if (detectedDifference !== 0) return detectedDifference
      if (right.rows.length !== left.rows.length) return right.rows.length - left.rows.length
      const leftLongest = Math.max(...left.rows.map((row) => timeframeMinutes(row.timeframe)))
      const rightLongest = Math.max(...right.rows.map((row) => timeframeMinutes(row.timeframe)))
      return rightLongest - leftLongest || left.rank - right.rank
    })
  }

  return orderedGroups.flatMap(({ rows }) => rows
    .sort((left, right) => timeframeMinutes(left.timeframe) - timeframeMinutes(right.timeframe))
    .map((row, groupIndex) => ({ ...row, groupIndex, groupSize: rows.length })))
})

/** 检测时间和交易对在共振组内只显示一次，周期指标仍逐行展示。 */
function groupSpan({ row, columnIndex }: { row: GroupedSignalRow; columnIndex: number }) {
  if (columnIndex > 1) return [1, 1]
  return row.groupIndex === 0 ? [row.groupSize, 1] : [0, 0]
}

/** 用分隔线强化相邻 Signal 组的视觉边界。 */
function groupRowClass({ row }: { row: GroupedSignalRow }): string {
  return row.groupIndex === 0 ? 'signal-group-start' : ''
}
</script>

<template>
  <el-table :data="groupedRows" :loading="loading" :span-method="groupSpan" :row-class-name="groupRowClass" row-key="id" height="630" stripe empty-text="暂无符合条件的 Signal">
    <el-table-column label="检测时间" min-width="160"><template #default="{ row }">{{ dateTime(row.detected_at) }}</template></el-table-column>
    <el-table-column prop="symbol" label="交易对" min-width="170" fixed="left"><template #default="{ row }"><div class="symbol-group"><MarketSymbol :symbol="row.symbol" :is-tradfi="row.is_tradfi" /><el-tag v-if="row.groupSize > 1" class="resonance-tag" type="warning" effect="light" size="small">{{ row.groupSize }} 周期共振</el-tag></div></template></el-table-column>
    <el-table-column prop="timeframe" label="周期" width="72" align="center"><template #default="{ row }"><el-tag class="timeframe-tag" :class="timeframeClass(row.timeframe)" effect="plain" size="small">{{ row.timeframe }}</el-tag></template></el-table-column>
    <el-table-column label="当前价格" min-width="110" align="right"><template #default="{ row }"><span class="number">{{ price(row.current_price) }}</span></template></el-table-column>
    <el-table-column label="K线进度" width="140"><template #default="{ row }"><el-progress class="signal-progress" :percentage="progressPercent(row.progress_percent)" :stroke-width="8" :format="progressLabel" :color="progressColor" /></template></el-table-column>
    <el-table-column label="预计量" min-width="100" align="right"><template #default="{ row }">{{ compact(row.estimated_volume) }}</template></el-table-column>
    <el-table-column label="EMA量" min-width="100" align="right"><template #default="{ row }">{{ compact(row.volume_ema) }}</template></el-table-column>
    <el-table-column label="量比" min-width="80" align="right"><template #default="{ row }"><span :class="standoutMetricClass(row.volume_ratio)">{{ Number(row.volume_ratio).toFixed(2) }}x</span></template></el-table-column>
    <el-table-column label="OI 变化" min-width="108" align="right"><template #default="{ row }"><div class="oi-change"><span :class="standoutMetricClass(row.oi_change_percent)">+{{ Number(row.oi_change_percent).toFixed(3) }}%</span><small>{{ oiWindow(row.oi_lookback_minutes) }}</small></div></template></el-table-column>
    <el-table-column label="24h 成交额" min-width="116" align="right"><template #default="{ row }">{{ compact(row.quote_volume_24h) }}</template></el-table-column>
    <el-table-column label="最大涨幅" min-width="100" align="right"><template #default="{ row }"><span :class="performanceClass(row.future_performance?.max_rise_percent)">{{ performancePercent(row.future_performance?.max_rise_percent) }}</span></template></el-table-column>
    <el-table-column label="最大跌幅" min-width="100" align="right"><template #default="{ row }"><span :class="performanceClass(row.future_performance?.max_drop_percent)">{{ performancePercent(row.future_performance?.max_drop_percent) }}</span></template></el-table-column>
    <el-table-column label="未来表现" width="180" align="center"><template #default="{ row }"><FuturePerformanceSparkline :performance="row.future_performance" /></template></el-table-column>
    <el-table-column label="" width="58" fixed="right">
      <template #default="{ row }"><el-tooltip content="查看完整快照"><el-button text circle :icon="View" aria-label="查看完整快照" @click="$emit('open', row.id)" /></el-tooltip></template>
    </el-table-column>
  </el-table>
</template>

<style scoped>
.pending { color: #8a95a1; font-size: 12px; }
.performance-strong { font-weight: 900; }
.signal-metric { font-weight: 650; }
.standout-metric { display: inline-block; color: #00b77d; font-weight: 950; }
.signal-progress { width: 100%; }
.signal-progress :deep(.el-progress__text) { min-width: 43px; font-size: 12px !important; font-variant-numeric: tabular-nums; }
.symbol-group { display: flex; flex-direction: column; align-items: flex-start; gap: 5px; }
.resonance-tag { font-weight: 600; }
.timeframe-tag { min-width: 42px; font-weight: 600; }
.timeframe-level-1 { --el-tag-bg-color: #edf6ff; --el-tag-border-color: #c9dff2; --el-tag-text-color: #517493; }
.timeframe-level-2 { --el-tag-bg-color: #dcecff; --el-tag-border-color: #adceed; --el-tag-text-color: #416b91; }
.timeframe-level-3 { --el-tag-bg-color: #c6dff7; --el-tag-border-color: #8fbbe0; --el-tag-text-color: #315f86; }
.timeframe-level-4 { --el-tag-bg-color: #a9cdea; --el-tag-border-color: #70a8d1; --el-tag-text-color: #245577; }
.timeframe-level-5 { --el-tag-bg-color: #84b3d5; --el-tag-border-color: #568fba; --el-tag-text-color: #17445f; }
:deep(.el-table__body tr.signal-group-start:not(:first-child) > td.el-table__cell) { border-top: 1px solid #cbd5df; }
</style>
