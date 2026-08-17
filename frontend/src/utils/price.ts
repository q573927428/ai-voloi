// ==================== 行情价格格式化工具 ====================

/**
 * 根据价格数量级动态调整精度。
 * 个位价格至少保留三位小数，极小价格最多保留十二位小数。
 */
export function resolvePricePrecision(price: number): number {
  if (!Number.isFinite(price) || price === 0) return 3
  const absolutePrice = Math.abs(price)
  const minimumPrecision = absolutePrice < 10 ? 4 : 3
  return Math.min(12, Math.max(minimumPrecision, 3 - Math.floor(Math.log10(absolutePrice))))
}
