<!-- ==================== 交易池明细抽屉 ==================== -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '../api/client'
import MarketSymbol from './MarketSymbol.vue'
import type { ActiveSymbol } from '../types'

/**
 * 交易池明细抽屉
 *
 * 核心职责：
 * 1. 按 mode 区分"全部 USDT 永续"与"活跃交易池"两种列表
 * 2. 打开时按需从后端拉取市场快照，避免定时刷新时反复传输大列表
 * 3. 提供交易对关键字本地过滤
 */
const props = defineProps<{
  modelValue: boolean
  mode: 'all' | 'active'
}>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const poolLoading = ref(false)
const marketRows = ref<ActiveSymbol[]>([])
const poolKeyword = ref('')

const drawerTitle = computed(() => props.mode === 'all' ? '全部 USDT 永续' : '活跃交易池')

/** 按交易对或基础资产做本地关键字过滤。 */
const filteredMarkets = computed(() => {
  const keyword = poolKeyword.value.trim().toUpperCase()
  if (!keyword) return marketRows.value
  return marketRows.value.filter((market) =>
    market.symbol.includes(keyword) || market.base_asset.includes(keyword),
  )
})

const compact = (value: string | null) => value === null
  ? '—'
  : Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 2 }).format(Number(value))

const price = (value: string | null) => value === null
  ? '—'
  : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 12 })

/** Binance 返回小数费率，展示时换算为百分比并保留足够精度。 */
const fundingRate = (value: string | null) => value === null
  ? '—'
  : `${Number(value) >= 0 ? '+' : ''}${(Number(value) * 100).toFixed(4)}%`

const snapshotTime = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false })

/** 抽屉打开时拉取对应模式的市场列表，重复打开会重新加载最新快照。 */
async function load() {
  marketRows.value = []
  poolKeyword.value = ''
  poolLoading.value = true
  try {
    marketRows.value = props.mode === 'all' ? await api.allMarkets() : await api.activeMarkets()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    poolLoading.value = false
  }
}

watch(() => props.modelValue, (visible) => {
  if (visible) load()
})
</script>

<template>
  <el-drawer
    :model-value="modelValue"
    :title="drawerTitle"
    size="min(980px, 100%)"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="pool-toolbar">
      <span>共 {{ marketRows.length }} 个交易对</span>
      <el-input v-model="poolKeyword" :prefix-icon="Search" clearable placeholder="搜索交易对" aria-label="搜索交易对" />
    </div>
    <el-table
      v-loading="poolLoading"
      :data="filteredMarkets"
      height="calc(100vh - 170px)"
      stripe
      :empty-text="props.mode === 'all' ? '暂无永续合约' : '暂无活跃交易对'"
    >
      <el-table-column prop="symbol" label="交易对" min-width="160" fixed="left">
        <template #default="{ row }"><MarketSymbol :symbol="row.symbol" :base-asset="row.base_asset" :is-tradfi="row.contract_type === 'TRADIFI_PERPETUAL'" /></template>
      </el-table-column>
      <el-table-column prop="base_asset" label="基础资产" min-width="90" />
      <el-table-column v-if="props.mode === 'all'" label="活跃状态" width="80">
        <template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'info'" effect="plain" size="small">{{ row.is_active ? '已加入' : '未加入' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="最新价" min-width="100" align="right"><template #default="{ row }"><span class="number">{{ price(row.last_price) }}</span></template></el-table-column>
      <el-table-column label="24h 涨跌" min-width="110" align="right"><template #default="{ row }"><span :class="Number(row.price_change_percent_24h) >= 0 ? 'positive' : 'negative'">{{ row.price_change_percent_24h === null ? '—' : `${Number(row.price_change_percent_24h) >= 0 ? '+' : ''}${Number(row.price_change_percent_24h).toFixed(2)}%` }}</span></template></el-table-column>
      <el-table-column label="24h 成交额" min-width="110" align="right"><template #default="{ row }"><span class="number">{{ compact(row.quote_volume_24h) }} </span></template></el-table-column>
      <el-table-column label="资金费率" min-width="105" align="right"><template #default="{ row }"><span :class="row.funding_rate === null ? 'number' : Number(row.funding_rate) >= 0 ? 'positive' : 'negative'">{{ fundingRate(row.funding_rate) }}</span></template></el-table-column>
      <el-table-column label="快照时间" min-width="170"><template #default="{ row }">{{ snapshotTime(row.updated_at) }}</template></el-table-column>
    </el-table>
  </el-drawer>
</template>
