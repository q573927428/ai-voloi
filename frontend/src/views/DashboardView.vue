<!-- ==================== 运行概览页面 ==================== -->
<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { Refresh, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { api, errorMessage } from '../api/client'
import SignalTable from '../components/SignalTable.vue'
import type { DashboardStats, SignalSnapshot } from '../types'

const router = useRouter()
const stats = ref<DashboardStats | null>(null)
const signals = ref<SignalSnapshot[]>([])
const loading = ref(false)
let timer: number | undefined

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

onMounted(() => { load(); timer = window.setInterval(load, 30000) })
onUnmounted(() => timer && window.clearInterval(timer))
</script>

<template>
  <div class="section-head">
    <span />
    <div><el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button><el-button type="primary" :icon="VideoPlay" @click="scan">立即扫描</el-button></div>
  </div>
  <div class="metric-grid" v-loading="loading && !stats">
    <div class="metric"><p class="metric-label">全部 USDT 永续</p><p class="metric-value">{{ stats?.total_symbols ?? '—' }}</p><div class="metric-note">Binance 当前可交易合约</div></div>
    <div class="metric"><p class="metric-label">活跃交易池</p><p class="metric-value">{{ stats?.active_symbols ?? '—' }}</p><div class="metric-note">通过 24h 成交额过滤</div></div>
    <div class="metric"><p class="metric-label">WebSocket</p><p class="metric-value" style="font-size: 22px">{{ stats?.websocket_status ?? '—' }}</p><div class="metric-note">实时 K 线维护状态</div></div>
    <div class="metric"><p class="metric-label">最后扫描</p><p class="metric-value" style="font-size: 18px">{{ stats?.last_scan_at ? new Date(stats.last_scan_at).toLocaleTimeString('zh-CN', { hour12: false }) : '—' }}</p><div class="metric-note">严格对齐扫描边界</div></div>
    <div class="metric"><p class="metric-label">扫描耗时</p><p class="metric-value">{{ stats?.last_scan_duration_ms ?? '—' }}<small v-if="stats?.last_scan_duration_ms"> ms</small></p><div class="metric-note">最近一次完整扫描</div></div>
    <div class="metric"><p class="metric-label">今日 Signal</p><p class="metric-value">{{ stats?.today_signal_count ?? '—' }}</p><div class="metric-note">UTC 自然日累计</div></div>
  </div>
  <section class="section">
    <div class="section-head"><h2>最近 Signal</h2><el-button text @click="router.push('/signals')">查看全部</el-button></div>
    <div class="table-frame"><SignalTable :rows="signals" :loading="loading" @open="router.push(`/signals/${$event}`)" /></div>
  </section>
</template>
