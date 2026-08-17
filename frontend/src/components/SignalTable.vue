<!-- ==================== Signal 数据表格 ==================== -->
<script setup lang="ts">
import { View } from '@element-plus/icons-vue'
import type { SignalSnapshot } from '../types'
import { resolvePricePrecision } from '../utils/price'
import FuturePerformanceSparkline from './FuturePerformanceSparkline.vue'
import MarketSymbol from './MarketSymbol.vue'

/** 表格输入与跳转事件，rows 为不可变快照列表。 */
defineProps<{ rows: SignalSnapshot[]; loading?: boolean }>()
defineEmits<{ open: [id: string] }>()

const compact = (value: string) => Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 2 }).format(Number(value))
const dateTime = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false })

/** 按价格数量级显示有效精度，同时省略无意义的尾随零。 */
function price(value: string): string {
  const number = Number(value)
  return number.toLocaleString('zh-CN', { maximumFractionDigits: resolvePricePrecision(number) })
}

/** 将 Signal 实际使用的 OI 回看分钟数格式化为紧凑周期标签。 */
function oiWindow(minutes: number | null) {
  if (minutes == null) return '旧口径'
  if (minutes % 1440 === 0) return `${minutes / 1440}d`
  if (minutes % 60 === 0) return `${minutes / 60}h`
  return `${minutes}m`
}

/** 按周期时长返回标签色阶，时间越长颜色越深。 */
function timeframeClass(timeframe: string): string {
  const match = /^(\d+)([mhdw])$/i.exec(timeframe)
  if (!match) return 'timeframe-level-1'

  const unitMinutes = { m: 1, h: 60, d: 1440, w: 10080 }[match[2].toLowerCase() as 'm' | 'h' | 'd' | 'w']
  const minutes = Number(match[1]) * unitMinutes
  if (minutes <= 15) return 'timeframe-level-1'
  if (minutes <= 30) return 'timeframe-level-2'
  if (minutes <= 60) return 'timeframe-level-3'
  if (minutes <= 240) return 'timeframe-level-4'
  return 'timeframe-level-5'
}

/** 格式化最大盈亏；尚未计算或异常数据不展示为虚假的 0%。 */
function performancePercent(value?: string | null): string {
  if (value == null || value === '') return '待计算'
  const number = Number(value)
  if (!Number.isFinite(number)) return '待计算'
  return `${number > 0 ? '+' : ''}${number.toFixed(3)}%`
}

/** 最大盈亏字段按实际正负着色，因为当前已计算区间的最小值也可能仍为正。 */
function performanceClass(value?: string | null): string {
  if (value == null || value === '' || !Number.isFinite(Number(value))) return 'pending'
  return Number(value) >= 0 ? 'positive' : 'negative'
}
</script>

<template>
  <el-table :data="rows" :loading="loading" height="630" stripe empty-text="暂无符合条件的 Signal">
    <el-table-column label="检测时间" min-width="166"><template #default="{ row }">{{ dateTime(row.detected_at) }}</template></el-table-column>
    <el-table-column prop="symbol" label="交易对" min-width="180" fixed="left"><template #default="{ row }"><MarketSymbol :symbol="row.symbol" :is-tradfi="row.is_tradfi" /></template></el-table-column>
    <el-table-column prop="timeframe" label="周期" width="72" align="center"><template #default="{ row }"><el-tag class="timeframe-tag" :class="timeframeClass(row.timeframe)" effect="plain" size="small">{{ row.timeframe }}</el-tag></template></el-table-column>
    <el-table-column label="当前价格" min-width="116" align="right"><template #default="{ row }"><span class="number">{{ price(row.current_price) }}</span></template></el-table-column>
    <el-table-column label="进度" width="90" align="right"><template #default="{ row }">{{ Number(row.progress_percent).toFixed(1) }}%</template></el-table-column>
    <el-table-column label="预计量" min-width="120" align="right"><template #default="{ row }">{{ compact(row.estimated_volume) }}</template></el-table-column>
    <el-table-column label="EMA量" min-width="100" align="right"><template #default="{ row }">{{ compact(row.volume_ema) }}</template></el-table-column>
    <el-table-column label="量比" min-width="80" align="right"><template #default="{ row }"><span class="positive">{{ Number(row.volume_ratio).toFixed(2) }}x</span></template></el-table-column>
    <el-table-column label="OI 变化" min-width="108" align="right"><template #default="{ row }"><div class="oi-change"><span class="positive">+{{ Number(row.oi_change_percent).toFixed(3) }}%</span><small>{{ oiWindow(row.oi_lookback_minutes) }}</small></div></template></el-table-column>
    <el-table-column label="24h 成交额" min-width="116" align="right"><template #default="{ row }">{{ compact(row.quote_volume_24h) }}</template></el-table-column>
    <el-table-column label="最大盈利" min-width="100" align="right"><template #default="{ row }"><span :class="performanceClass(row.future_performance?.max_profit_percent)">{{ performancePercent(row.future_performance?.max_profit_percent) }}</span></template></el-table-column>
    <el-table-column label="最大亏损" min-width="100" align="right"><template #default="{ row }"><span :class="performanceClass(row.future_performance?.max_loss_percent)">{{ performancePercent(row.future_performance?.max_loss_percent) }}</span></template></el-table-column>
    <el-table-column label="未来表现" width="180" align="center"><template #default="{ row }"><FuturePerformanceSparkline :performance="row.future_performance" /></template></el-table-column>
    <el-table-column label="" width="58" fixed="right">
      <template #default="{ row }"><el-tooltip content="查看完整快照"><el-button text circle :icon="View" aria-label="查看完整快照" @click="$emit('open', row.id)" /></el-tooltip></template>
    </el-table-column>
  </el-table>
</template>

<style scoped>
.pending { color: #8a95a1; font-size: 12px; }
.timeframe-tag { min-width: 42px; font-weight: 600; }
.timeframe-level-1 { --el-tag-bg-color: #edf6ff; --el-tag-border-color: #c9dff2; --el-tag-text-color: #517493; }
.timeframe-level-2 { --el-tag-bg-color: #dcecff; --el-tag-border-color: #adceed; --el-tag-text-color: #416b91; }
.timeframe-level-3 { --el-tag-bg-color: #c6dff7; --el-tag-border-color: #8fbbe0; --el-tag-text-color: #315f86; }
.timeframe-level-4 { --el-tag-bg-color: #a9cdea; --el-tag-border-color: #70a8d1; --el-tag-text-color: #245577; }
.timeframe-level-5 { --el-tag-bg-color: #84b3d5; --el-tag-border-color: #568fba; --el-tag-text-color: #17445f; }
</style>
