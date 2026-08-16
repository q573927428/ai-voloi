<!-- ==================== 监控平台应用框架 ==================== -->
<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { DataAnalysis, Monitor, Setting, TrendCharts } from '@element-plus/icons-vue'
import { useSignalStore } from './stores/signals'

const route = useRoute()
const store = useSignalStore()
const title = computed(() => route.meta.title as string)
onMounted(store.connect)
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand"><span class="brand-mark">VO</span><span>VolOI</span></div>
      <nav aria-label="主导航">
        <RouterLink to="/"><el-icon><Monitor /></el-icon><span>运行概览</span></RouterLink>
        <RouterLink to="/signals"><el-icon><TrendCharts /></el-icon><span>Signal 监控</span></RouterLink>
        <RouterLink to="/configuration"><el-icon><Setting /></el-icon><span>参数配置</span></RouterLink>
      </nav>
      <div class="connection"><span :class="['status-dot', { online: store.connected }]" />{{ store.connected ? '实时通道已连接' : '实时通道连接中' }}</div>
    </aside>
    <main>
      <header class="topbar">
        <div><p class="eyebrow">BINANCE USDⓈ-M</p><h1>{{ title }}</h1></div>
        <el-icon :size="24"><DataAnalysis /></el-icon>
      </header>
      <div class="page"><RouterView /></div>
    </main>
  </div>
</template>
