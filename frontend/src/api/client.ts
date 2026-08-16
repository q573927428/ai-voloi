// ==================== 后端 API 客户端 ====================
import axios from 'axios'
import type { ActiveSymbol, DashboardStats, RealtimeChartData, ScannerConfig, SignalChartData, SignalPage, SignalQuery, SignalSnapshot } from '../types'

const client = axios.create({ baseURL: '/api', timeout: 15000 })

export const api = {
  dashboard: () => client.get<DashboardStats>('/dashboard').then(({ data }) => data),
  allMarkets: () => client.get<ActiveSymbol[]>('/markets/all').then(({ data }) => data),
  activeMarkets: () => client.get<ActiveSymbol[]>('/markets/active').then(({ data }) => data),
  signals: (params: SignalQuery) => client.get<SignalPage>('/signals', { params }).then(({ data }) => data),
  signal: (id: string) => client.get<SignalSnapshot>(`/signals/${id}`).then(({ data }) => data),
  signalChart: (id: string) => client.get<SignalChartData>(`/signals/${id}/chart`).then(({ data }) => data),
  realtimeChart: (symbol: string, timeframe: string) => client
    .get<RealtimeChartData>(`/markets/${symbol}/${timeframe}/chart`)
    .then(({ data }) => data),
  config: () => client.get<ScannerConfig>('/config').then(({ data }) => data),
  updateConfig: (payload: Partial<ScannerConfig>) => client.patch<ScannerConfig>('/config', payload).then(({ data }) => data),
  runScanner: () => client.post('/scanner/run').then(({ data }) => data),
}

/** 将 HTTP 异常转换为适合页面展示的简短信息。 */
export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) return error.response?.data?.detail || error.message
  return error instanceof Error ? error.message : '未知错误'
}
