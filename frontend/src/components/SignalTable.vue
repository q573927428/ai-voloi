<!-- ==================== Signal 数据表格 ==================== -->
<script setup lang="ts">
import { View } from '@element-plus/icons-vue'
import type { SignalSnapshot } from '../types'
import { resolvePricePrecision } from '../utils/price'
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
</script>

<template>
  <el-table :data="rows" :loading="loading" height="530" stripe empty-text="暂无符合条件的 Signal">
    <el-table-column label="检测时间" min-width="166"><template #default="{ row }">{{ dateTime(row.detected_at) }}</template></el-table-column>
    <el-table-column prop="symbol" label="交易对" min-width="180" fixed="left"><template #default="{ row }"><MarketSymbol :symbol="row.symbol" :is-tradfi="row.is_tradfi" /></template></el-table-column>
    <el-table-column prop="timeframe" label="周期" width="72" />
    <el-table-column label="当前价格" min-width="116" align="right"><template #default="{ row }"><span class="number">{{ price(row.current_price) }}</span></template></el-table-column>
    <el-table-column label="进度" width="90" align="right"><template #default="{ row }">{{ Number(row.progress_percent).toFixed(1) }}%</template></el-table-column>
    <el-table-column label="预计成交量" min-width="120" align="right"><template #default="{ row }">{{ compact(row.estimated_volume) }}</template></el-table-column>
    <el-table-column label="EMA" min-width="100" align="right"><template #default="{ row }">{{ compact(row.volume_ema) }}</template></el-table-column>
    <el-table-column label="Volume Ratio" min-width="120" align="right"><template #default="{ row }"><span class="positive">{{ Number(row.volume_ratio).toFixed(2) }}x</span></template></el-table-column>
    <el-table-column label="OI 变化" min-width="108" align="right"><template #default="{ row }"><div class="oi-change"><span class="positive">+{{ Number(row.oi_change_percent).toFixed(3) }}%</span><small>{{ oiWindow(row.oi_lookback_minutes) }}</small></div></template></el-table-column>
    <el-table-column label="24h 成交额" min-width="116" align="right"><template #default="{ row }">{{ compact(row.quote_volume_24h) }}</template></el-table-column>
    <el-table-column label="" width="58" fixed="right">
      <template #default="{ row }"><el-tooltip content="查看完整快照"><el-button text circle :icon="View" aria-label="查看完整快照" @click="$emit('open', row.id)" /></el-tooltip></template>
    </el-table-column>
  </el-table>
</template>
