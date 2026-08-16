<!-- ==================== 运行概览页面 ==================== -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Refresh, Search, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { api, errorMessage } from '../api/client'
import MarketSymbol from '../components/MarketSymbol.vue'
import SignalTable from '../components/SignalTable.vue'
import type { ActiveSymbol, DashboardStats, SignalSnapshot } from '../types'

const router = useRouter()
const stats = ref<DashboardStats | null>(null)
const signals = ref<SignalSnapshot[]>([])
const loading = ref(false)
const poolVisible = ref(false)
const poolLoading = ref(false)
const marketMode = ref<'all' | 'active'>('active')
const marketRows = ref<ActiveSymbol[]>([])
const poolKeyword = ref('')
let timer: number | undefined

const drawerTitle = computed(() => marketMode.value === 'all' ? '全部 USDT 永续' : '活跃交易池')

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

const snapshotTime = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false })

/** 同时刷新运行指标和最近 Signal，保持概览数据时间一致。 */
async function load() {
  loading.value = true
  try {
    const [summary, page] = await Promise.all([api.dashboard(), api.signals({ page_size: 10 })])
    stats.value = summary
    signals.value = page.items
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

/** 手动扫描用于运维检查，完成后立即刷新指标。 */
async function scan() {
  try {
    await api.runScanner()
    ElMessage.success('扫描已完成')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

/** 按概览指标打开对应市场列表，避免定时刷新时反复传输大列表。 */
async function openMarketDrawer(mode: 'all' | 'active') {
  marketMode.value = mode
  marketRows.value = []
  poolKeyword.value = ''
  poolVisible.value = true
  poolLoading.value = true
  try {
    marketRows.value = mode === 'all' ? await api.allMarkets() : await api.activeMarkets()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    poolLoading.value = false
  }
}

onMounted(() => { load(); timer = window.setInterval(load, 30000) })
onUnmounted(() => timer && window.clearInterval(timer))
</script>

<template>
  <div class="section-head">
    <span />
    <div><el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button><el-button type="primary" :icon="VideoPlay" @click="scan">立即扫描</el-button></div>
  </div>
  <div class="metric-grid" v-loading="loading && !stats">
    <div class="metric"><p class="metric-label">全部 USDT 永续</p><button class="metric-value metric-value-button" type="button" aria-label="查看全部 USDT 永续明细" @click="openMarketDrawer('all')">{{ stats?.total_symbols ?? '—' }}</button><div class="metric-note">Binance 当前可交易合约 · 点击查看明细</div></div>
    <div class="metric"><p class="metric-label">活跃交易池</p><button class="metric-value metric-value-button" type="button" aria-label="查看活跃交易池明细" @click="openMarketDrawer('active')">{{ stats?.active_symbols ?? '—' }}</button><div class="metric-note">通过 24h 成交额过滤 · 点击查看明细</div></div>
    <div class="metric"><p class="metric-label">WebSocket</p><p class="metric-value" style="font-size: 22px">{{ stats?.websocket_status ?? '—' }}</p><div class="metric-note">实时 K 线维护状态</div></div>
    <div class="metric"><p class="metric-label">最后扫描</p><p class="metric-value" style="font-size: 18px">{{ stats?.last_scan_at ? new Date(stats.last_scan_at).toLocaleTimeString('zh-CN', { hour12: false }) : '—' }}</p><div class="metric-note">严格对齐扫描边界</div></div>
    <div class="metric"><p class="metric-label">扫描耗时</p><p class="metric-value">{{ stats?.last_scan_duration_ms ?? '—' }}<small v-if="stats?.last_scan_duration_ms"> ms</small></p><div class="metric-note">最近一次完整扫描</div></div>
    <div class="metric"><p class="metric-label">今日 Signal</p><p class="metric-value">{{ stats?.today_signal_count ?? '—' }}</p><div class="metric-note">UTC 自然日累计</div></div>
  </div>
  <section class="section">
    <div class="section-head"><h2>最近 Signal</h2><el-button text @click="router.push('/signals')">查看全部</el-button></div>
    <div class="table-frame"><SignalTable :rows="signals" :loading="loading" @open="router.push(`/signals/${$event}`)" /></div>
  </section>

  <el-drawer v-model="poolVisible" :title="drawerTitle" size="min(860px, 100%)" destroy-on-close>
    <div class="pool-toolbar">
      <span>共 {{ marketRows.length }} 个交易对</span>
      <el-input v-model="poolKeyword" :prefix-icon="Search" clearable placeholder="搜索交易对" aria-label="搜索交易对" />
    </div>
    <el-table v-loading="poolLoading" :data="filteredMarkets" height="calc(100vh - 170px)" stripe :empty-text="marketMode === 'all' ? '暂无永续合约' : '暂无活跃交易对'">
      <el-table-column prop="symbol" label="交易对" min-width="190" fixed="left"><template #default="{ row }"><MarketSymbol :symbol="row.symbol" :is-tradfi="row.contract_type === 'TRADIFI_PERPETUAL'" /></template></el-table-column>
      <el-table-column prop="base_asset" label="基础资产" min-width="100" />
      <el-table-column v-if="marketMode === 'all'" label="活跃状态" min-width="100"><template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'info'" effect="plain" size="small">{{ row.is_active ? '已加入' : '未加入' }}</el-tag></template></el-table-column>
      <el-table-column label="最新价" min-width="140" align="right"><template #default="{ row }"><span class="number">{{ price(row.last_price) }}</span></template></el-table-column>
      <el-table-column label="24h 涨跌" min-width="110" align="right"><template #default="{ row }"><span :class="Number(row.price_change_percent_24h) >= 0 ? 'positive' : 'negative'">{{ row.price_change_percent_24h === null ? '—' : `${Number(row.price_change_percent_24h) >= 0 ? '+' : ''}${Number(row.price_change_percent_24h).toFixed(2)}%` }}</span></template></el-table-column>
      <el-table-column label="24h 成交额" min-width="130" align="right"><template #default="{ row }"><span class="number">{{ compact(row.quote_volume_24h) }} {{ row.quote_asset }}</span></template></el-table-column>
      <el-table-column label="快照时间" min-width="170"><template #default="{ row }">{{ snapshotTime(row.updated_at) }}</template></el-table-column>
    </el-table>
  </el-drawer>
</template>
