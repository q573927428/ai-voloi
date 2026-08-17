<!-- ==================== Signal 实时监控页面 ==================== -->
<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { Refresh, Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { api, errorMessage } from '../api/client'
import SignalTable from '../components/SignalTable.vue'
import type { SignalSnapshot } from '../types'

const router = useRouter()
const rows = ref<SignalSnapshot[]>([])
const total = ref(0)
const loading = ref(false)
const symbol = ref('')
const timeframe = ref('')
const sortBy = ref<'detected_at' | 'volume_ratio' | 'oi_change_percent'>('detected_at')
const page = ref(1)

/** 按当前筛选、排序与页码加载 Signal。 */
async function load() {
  loading.value = true
  try {
    const data = await api.signals({ symbol: symbol.value || undefined, timeframe: timeframe.value || undefined, sort_by: sortBy.value, page: page.value, page_size: 30 })
    rows.value = data.items
    // 分页以完整共振组为单位，避免同一交易对的多周期信号跨页。
    total.value = data.group_total
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

watch([timeframe, sortBy, page], load)
onMounted(load)
</script>

<template>
  <div class="toolbar">
    <el-input v-model="symbol" clearable placeholder="搜索交易对" :prefix-icon="Search" style="width: 220px" @keyup.enter="page = 1; load()" @clear="load" />
    <el-select v-model="timeframe" placeholder="全部周期" clearable style="width: 130px"><el-option v-for="item in ['15m','30m','1h','4h','1d']" :key="item" :label="item" :value="item" /></el-select>
    <el-select v-model="sortBy" style="width: 170px"><el-option label="按检测时间" value="detected_at" /><el-option label="按 Volume Ratio" value="volume_ratio" /><el-option label="按 OI 变化" value="oi_change_percent" /></el-select>
    <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
  </div>
  <div class="table-frame"><SignalTable :rows="rows" :loading="loading" :prioritize-resonance="sortBy === 'detected_at'" @open="router.push(`/signals/${$event}`)" /></div>
  <div style="display:flex;justify-content:flex-end;padding-top:16px"><el-pagination v-model:current-page="page" :page-size="30" :total="total" layout="total, prev, pager, next" /></div>
</template>
