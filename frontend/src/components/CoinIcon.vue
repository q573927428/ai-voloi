<!-- ==================== 币安合约资产图标（可独立复用，含文字占位兜底） ==================== -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import iconManifest from '../assets/binance-coin-icons.json'

/** 图标清单中的单个资产记录，由 sync-binance-icons.mjs 定期生成。 */
interface BinanceIconEntry {
  iconUrl: string | null
  localUrl: string | null
}

const iconEntries = iconManifest.assets as Record<string, BinanceIconEntry>

/**
 * 币种图标
 *
 * 核心职责：
 * 1. 接收交易对名称或显式基础资产，解析出币种标识
 * 2. 从定期抓取 Binance 合约页生成的清单加载官方资产图标，兼容加密币和 TradFi
 * 3. 清单或图片缺失时自动降级为
 *    "确定性配色 + 币种缩写"的文字占位，保证任何交易对都能呈现可辨识标识
 */
const props = withDefaults(defineProps<{
  symbol?: string
  baseAsset?: string
  size?: number
}>(), { symbol: '', baseAsset: '', size: 18 })

/** 图标加载失败时切到文字占位，避免展示破碎图片。 */
const iconFailed = ref(false)

/** 优先使用显式 baseAsset，缺失时从交易对符号中剥离计价资产以推断基础币种。 */
const base = computed(() => {
  if (props.baseAsset) return props.baseAsset
  const match = props.symbol.match(/^([A-Z0-9]+?)(USDT|FDUSD|USDC|BUSD|TUSD|EUR|BRL|TRY|BIDR|DAI|AUD|GBP|RUB|UAH|BKRW|IDRT|NGN|PLN|RON|USDP|XRP)$/)
  return match ? match[1] : props.symbol.replace(/USDT$/, '')
})

/** 优先使用同步到 public 的本地图标，否则直接使用 Binance 静态资源地址。 */
const iconUrl = computed(() => {
  const entry = iconEntries[base.value.toUpperCase()]
  return entry?.localUrl || entry?.iconUrl || ''
})

// 表格复用组件实例切换交易对时必须清除上一个图标的失败状态。
watch(iconUrl, () => {
  iconFailed.value = false
})

/** 占位缩写字取币种标识前 2 个字符，保持色块内可读。 */
const fallbackText = computed(() => base.value.slice(0, 2).toUpperCase())

/**
 * 根据币种名生成确定性的色板索引。
 * 同一币种永远是同一种颜色，不同币种通过哈希尽量分散，类似 GitHub 头像的确定性配色。
 */
const fallbackColor = computed(() => {
  // 预置深色系色板，避免与背景白色冲突且彼此区分明显
  const palette = ['#6f6ec9', '#4a90d9', '#d97706', '#14805e', '#b45309', '#0f766e', '#c54d4a', '#7c3aed', '#be185d']
  let hash = 0
  for (let i = 0; i < base.value.length; i += 1) {
    hash = (hash * 31 + base.value.charCodeAt(i)) >>> 0
  }
  return palette[hash % palette.length]
})

function onIconError() {
  iconFailed.value = true
}
</script>

<template>
  <img
    v-if="base && iconUrl && !iconFailed"
    class="coin-icon"
    :src="iconUrl"
    :alt="base"
    :title="base"
    :width="size"
    :height="size"
    loading="lazy"
    @error="onIconError"
  />
  <span
    v-else-if="base"
    class="coin-icon coin-fallback"
    :style="{ width: `${size}px`, height: `${size}px`, background: fallbackColor }"
    :title="base"
    role="img"
    :aria-label="base"
  >{{ fallbackText }}</span>
</template>
