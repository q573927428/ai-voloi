<!-- ==================== Signal 未来表现迷你曲线 ==================== -->
<script setup lang="ts">
import { computed } from 'vue'
import type { SignalFuturePerformance } from '../types'

/** 未来表现迷你曲线输入，performance 为空表示尚未建立计算记录。 */
const props = defineProps<{ performance?: SignalFuturePerformance | null }>()

/** 曲线点配置，顺序与后台未来表现的观察周期严格一致。 */
interface HorizonPoint {
  key: keyof SignalFuturePerformance
  label: string
}

/** SVG 中可绘制的数据点。 */
interface ChartPoint extends HorizonPoint {
  value: number | null
  x: number
  y: number | null
}

const horizons: HorizonPoint[] = [
  { key: 'price_change_5m_percent', label: '5m' },
  { key: 'price_change_15m_percent', label: '15m' },
  { key: 'price_change_30m_percent', label: '30m' },
  { key: 'price_change_1h_percent', label: '1h' },
  { key: 'price_change_4h_percent', label: '4h' },
  { key: 'price_change_8h_percent', label: '8h' },
  { key: 'price_change_12h_percent', label: '12h' },
  { key: 'price_change_16h_percent', label: '16h' },
  { key: 'price_change_1d_percent', label: '1d' },
  { key: 'price_change_2d_percent', label: '2d' },
]

const width = 156
const height = 42
const padding = 4

/** 将接口字符串转换为有限数值；空值和异常值均按待计算处理。 */
function numericValue(key: keyof SignalFuturePerformance): number | null {
  const raw = props.performance?.[key]
  if (raw == null || raw === '') return null
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
}

/** 格式化价格涨跌幅，正数显式带加号以便快速辨认方向。 */
function percent(value: number | null): string {
  if (value == null) return '待计算'
  return `${value > 0 ? '+' : ''}${value.toFixed(3)}%`
}

const values = computed(() => horizons.map(({ key }) => numericValue(key)))
const hasValues = computed(() => values.value.some(value => value != null))

/** 以 0 为固定参照计算纵坐标，避免全为正值或负值时产生误导。 */
const chartPoints = computed<ChartPoint[]>(() => {
  const available = values.value.filter((value): value is number => value != null)
  const min = Math.min(0, ...available)
  const max = Math.max(0, ...available)
  const span = max - min || 1
  return horizons.map((horizon, index) => {
    const value = values.value[index]
    return {
      ...horizon,
      value,
      x: padding + index * ((width - padding * 2) / (horizons.length - 1)),
      y: value == null ? null : padding + ((max - value) / span) * (height - padding * 2),
    }
  })
})

const zeroY = computed(() => {
  const available = values.value.filter((value): value is number => value != null)
  const min = Math.min(0, ...available)
  const max = Math.max(0, ...available)
  return padding + ((max - 0) / (max - min || 1)) * (height - padding * 2)
})

/** 按缺失点切分折线，待计算周期不会被跨越连接。 */
const segments = computed(() => {
  const result: string[] = []
  let current: string[] = []
  for (const point of chartPoints.value) {
    if (point.y == null) {
      if (current.length > 1) result.push(current.join(' '))
      current = []
      continue
    }
    current.push(`${point.x},${point.y}`)
  }
  if (current.length > 1) result.push(current.join(' '))
  return result
})
</script>

<template>
  <el-tooltip v-if="hasValues" placement="top" :show-after="200">
    <template #content>
      <div class="performance-tooltip">
        <div v-for="point in chartPoints" :key="point.key" class="performance-tooltip-row">
          <span>{{ point.label }}</span>
          <strong :class="point.value == null ? 'pending' : point.value >= 0 ? 'up' : 'down'">{{ percent(point.value) }}</strong>
        </div>
      </div>
    </template>
    <div class="sparkline" aria-label="未来表现曲线，悬浮查看各周期数值">
      <svg :viewBox="`0 0 ${width} ${height}`" role="img" aria-hidden="true">
        <line :x1="padding" :x2="width - padding" :y1="zeroY" :y2="zeroY" class="zero-line" />
        <polyline v-for="(points, index) in segments" :key="index" :points="points" class="change-line" />
        <circle
          v-for="point in chartPoints.filter(item => item.y != null)"
          :key="point.key"
          :cx="point.x"
          :cy="point.y!"
          r="2.25"
          :class="point.value! >= 0 ? 'point-up' : 'point-down'"
        />
      </svg>
    </div>
  </el-tooltip>
  <!-- 无数据时不创建 Tooltip，避免禁用状态下的明细插槽参与表格行高计算。 -->
  <div v-else class="sparkline sparkline-pending" aria-label="未来表现待计算">待计算</div>
</template>

<style scoped>
.sparkline { width: 156px; height: 42px; display: flex; align-items: center; justify-content: center; }
.sparkline svg { display: block; width: 156px; height: 42px; overflow: visible; }
.sparkline-pending { color: #8a95a1; font-size: 12px; }
.zero-line { stroke: #8f8f8f; stroke-width: 1; stroke-dasharray: 2 2; }
.change-line { fill: none; stroke: #536579; stroke-width: 1.75; stroke-linecap: round; stroke-linejoin: round; }
.point-up { fill: #14805e; }
.point-down { fill: #c54d4a; }
.performance-tooltip { display: grid; grid-template-columns: repeat(2, minmax(90px, 1fr)); gap: 5px 16px; min-width: 210px; }
.performance-tooltip-row { display: flex; justify-content: space-between; gap: 12px; font-size: 12px; }
.performance-tooltip-row > span { color: #b8c0ca; }
.performance-tooltip-row strong { font-weight: 600; }
.performance-tooltip-row .up { color: #58c7a2; }
.performance-tooltip-row .down { color: #f08b87; }
.performance-tooltip-row .pending { color: #aab3bd; font-weight: 400; }
</style>
