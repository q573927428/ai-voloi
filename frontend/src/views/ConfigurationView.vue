<!-- ==================== 扫描参数配置页面 ==================== -->
<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Check, RefreshLeft } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '../api/client'
import type { ScannerConfig } from '../types'

const loading = ref(false)
const form = reactive<ScannerConfig>({ timeframes: [], min_24h_quote_volume: 10000000, volume_ema_period: 12, volume_multiplier: 1.5, min_progress_percent: 10, oi_lookback_minutes: 15, oi_change_threshold_percent: 0.05, scan_interval_minutes: 5 })

/** 将后端 Decimal 的 JSON 字符串归一化为数字输入框要求的 Number。 */
function applyConfig(data: ScannerConfig) {
  Object.assign(form, data, {
    min_24h_quote_volume: Number(data.min_24h_quote_volume),
    volume_multiplier: Number(data.volume_multiplier),
    min_progress_percent: Number(data.min_progress_percent),
    oi_change_threshold_percent: Number(data.oi_change_threshold_percent),
  })
}

/** 从服务端恢复已持久化配置。 */
async function load() {
  loading.value = true
  try { applyConfig(await api.config()) }
  catch (error) { ElMessage.error(errorMessage(error)) }
  finally { loading.value = false }
}

/** 保存配置；后端 Pydantic 会再次执行范围校验。 */
async function save() {
  loading.value = true
  try { applyConfig(await api.updateConfig(form)); ElMessage.success('配置已保存，交易池正在刷新') }
  catch (error) { ElMessage.error(errorMessage(error)) }
  finally { loading.value = false }
}
onMounted(load)
</script>

<template>
  <div class="config-form" v-loading="loading">
    <div class="form-row"><div class="form-label"><strong>24h 最小成交额</strong><span>低于该 USDT Quote Volume 的交易对不进入活跃池。</span></div><el-input-number v-model="form.min_24h_quote_volume" :min="0" :step="1000000" controls-position="right" style="width:100%" /></div>
    <div class="form-row"><div class="form-label"><strong>Volume EMA 周期</strong><span>仅使用最近已收盘的完整 K 线。</span></div><el-input-number v-model="form.volume_ema_period" :min="2" :max="100" controls-position="right" style="width:100%" /></div>
    <div class="form-row"><div class="form-label"><strong>成交量倍数</strong><span>预计成交量相对 EMA 的最低触发倍数。</span></div><el-input-number v-model="form.volume_multiplier" :min="1" :max="100" :step="0.1" :precision="2" controls-position="right" style="width:100%" /></div>
    <div class="form-row"><div class="form-label"><strong>最小 K 线进度</strong><span>进度过低时跳过预测，避免早期数据失真。</span></div><el-input-number v-model="form.min_progress_percent" :min="0" :max="100" :step="1" controls-position="right" style="width:100%"><template #suffix>%</template></el-input-number></div>
    <div class="form-row"><div class="form-label"><strong>OI 观察窗口</strong><span>所有 K 线周期统一比较最近这段时间，避免长周期累计偏置。</span></div><el-input-number v-model="form.oi_lookback_minutes" :min="5" :max="480" :step="5" controls-position="right" style="width:100%"><template #suffix>分钟</template></el-input-number></div>
    <div class="form-row"><div class="form-label"><strong>OI 变化阈值</strong><span>单位为百分比；默认 0.05 表示 0.05%，不是 5%。</span></div><el-input-number v-model="form.oi_change_threshold_percent" :min="0" :max="100" :step="0.01" :precision="3" controls-position="right" style="width:100%" /></div>
    <div class="form-row"><div class="form-label"><strong>扫描间隔</strong><span>按照服务器 UTC 时间的分钟边界执行。</span></div><el-input-number v-model="form.scan_interval_minutes" :min="1" :max="60" controls-position="right" style="width:100%" /></div>
    <div class="form-actions"><el-button :icon="RefreshLeft" @click="load">撤销</el-button><el-button type="primary" :icon="Check" @click="save">保存配置</el-button></div>
  </div>
</template>
