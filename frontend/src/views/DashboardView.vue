<!-- ==================== 运行概览页面 ==================== -->
<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { Refresh, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { api, errorMessage } from '../api/client'
import MarketPoolDrawer from '../components/MarketPoolDrawer.vue'
import SignalTable from '../components/SignalTable.vue'
import type { DashboardStats, SignalSnapshot } from '../types'

const router = useRouter()
const stats = ref<DashboardStats | null>(null)
const signals = ref<SignalSnapshot[]>([])
const loading = ref(false)
const scanning = ref(false)
const poolVisible = ref(false)
const poolMode = ref<'all' | 'active'>('active')
let timer: number | undefined

/** 同时刷新运行指标和最近 Signal，保持概览数据时间一致。 */
async function load() {
  // 防止重复点击"刷新"或定时器与手动刷新并发
  if (loading.value) return
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
  // 防止扫描进行中重复点击，避免触发多次扫描任务
  if (scanning.value) return
  scanning.value = true
  try {
    await api.runScanner()
    ElMessage.success('扫描已完成')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    scanning.value = false
  }
}

/** 按概览指标打开对应市场列表，具体加载逻辑由抽屉组件负责。 */
function openMarketDrawer(mode: 'all' | 'active') {
  poolMode.value = mode
  poolVisible.value = true
}

onMounted(() => { load(); timer = window.setInterval(load, 30000) })
onUnmounted(() => timer && window.clearInterval(timer))
</script>

<template>
  <div class="metric-grid" v-loading="loading && !stats">
    <div class="metric"><p class="metric-label">全部 USDT 永续</p><button class="metric-value metric-value-button" type="button" aria-label="查看全部 USDT 永续明细" @click="openMarketDrawer('all')">{{ stats?.total_symbols ?? '—' }}</button><div class="metric-note">Binance 当前可交易合约</div></div>
    <div class="metric"><p class="metric-label">活跃交易池</p><button class="metric-value metric-value-button" type="button" aria-label="查看活跃交易池明细" @click="openMarketDrawer('active')">{{ stats?.active_symbols ?? '—' }}</button><div class="metric-note">通过 24h 成交额过滤</div></div>
    <div class="metric"><p class="metric-label">WebSocket</p><p class="metric-value metric-ws"><span class="status-dot" :class="{ online: stats?.websocket_status === 'connected' }"></span>{{ stats?.websocket_status ?? '—' }}</p><div class="metric-note">实时 K 线维护状态</div></div>
    <div class="metric"><p class="metric-label">最后扫描</p><p class="metric-value">{{ stats?.last_scan_at ? new Date(stats.last_scan_at).toLocaleTimeString('zh-CN', { hour12: false }) : '—' }}</p><div class="metric-note">严格对齐扫描边界</div></div>
    <div class="metric"><p class="metric-label">扫描耗时</p><p class="metric-value">{{ stats?.last_scan_duration_ms ?? '—' }}<small v-if="stats?.last_scan_duration_ms"> ms</small></p><div class="metric-foot"><span class="metric-note">最近一次完整扫描</span><el-button size="small" :icon="Refresh" :loading="loading" :disabled="loading" @click="load">刷新</el-button></div></div>
    <div class="metric"><p class="metric-label">今日 Signal</p><p class="metric-value">{{ stats?.today_signal_count ?? '—' }}</p><div class="metric-foot"><span class="metric-note">UTC+8 自然日累计</span><el-button size="small" type="primary" :icon="VideoPlay" :loading="scanning" :disabled="scanning" @click="scan">扫描</el-button></div></div>
  </div>
  <section class="section">
    <div class="section-head"><h2>最近 Signal</h2><el-button text @click="router.push('/signals')">查看全部</el-button></div>
    <div class="table-frame"><SignalTable :rows="signals" :loading="loading" prioritize-resonance @open="router.push(`/signals/${$event}`)" /></div>
  </section>

  <MarketPoolDrawer v-model="poolVisible" :mode="poolMode" />
</template>
