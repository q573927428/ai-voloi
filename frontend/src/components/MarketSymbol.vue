<!-- ==================== 交易对及 TradFi 分类标识 ==================== -->
<script setup lang="ts">
/** 统一展示交易对名称，并为传统金融永续合约附加醒目标识。 */
defineProps<{ symbol: string; isTradfi?: boolean }>()
</script>

<template>
  <span class="market-symbol"><strong>{{ symbol }}</strong><el-tooltip v-if="isTradfi" content="TradFi"><el-tag class="tradfi-tag" type="warning" effect="plain" size="small">T</el-tag></el-tooltip></span>
</template>
