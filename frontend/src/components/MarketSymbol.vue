<!-- ==================== 交易对及 TradFi 分类标识 ==================== -->
<script setup lang="ts">
import CoinIcon from './CoinIcon.vue'

/**
 * 交易对展示组件
 *
 * 核心职责：
 * 1. 复用 CoinIcon 展示基础资产币种图标 + 交易对名称
 * 2. 为传统金融永续合约附加醒目标识
 */
withDefaults(defineProps<{
  symbol: string
  isTradfi?: boolean
  baseAsset?: string
}>(), { isTradfi: false, baseAsset: '' })
</script>

<template>
  <span class="market-symbol"><CoinIcon :symbol="symbol" :base-asset="baseAsset" /><strong>{{ symbol }}</strong><el-tooltip v-if="isTradfi" content="TradFi"><el-tag class="tradfi-tag" type="warning" effect="plain" size="small">T</el-tag></el-tooltip></span>
</template>
