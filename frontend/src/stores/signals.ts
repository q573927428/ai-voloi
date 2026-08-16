// ==================== Signal 实时状态仓库 ====================
import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { SignalSnapshot } from '../types'

export const useSignalStore = defineStore('signals', () => {
  const latest = ref<SignalSnapshot[]>([])
  const connected = ref(false)
  let socket: WebSocket | null = null
  let retryTimer: number | undefined

  /** 建立实时推送连接，并在异常断开后有限延时重连。 */
  function connect() {
    if (socket && socket.readyState < WebSocket.CLOSING) return
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    // 开发模式直接连接后端，生产环境通过同源 Nginx 反向代理，二者均可由环境变量覆盖。
    const defaultUrl = import.meta.env.DEV
      ? `${protocol}://${location.hostname}:8000/api/ws/signals`
      : `${protocol}://${location.host}/api/ws/signals`
    socket = new WebSocket(import.meta.env.VITE_WS_URL || defaultUrl)
    socket.onopen = () => {
      connected.value = true
      socket?.send('ready')
    }
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data)
      if (message.type === 'signal') latest.value = [message.data, ...latest.value].slice(0, 100)
    }
    socket.onclose = () => {
      connected.value = false
      retryTimer = window.setTimeout(connect, 3000)
    }
  }

  /** 主动释放连接，供应用卸载或测试使用。 */
  function disconnect() {
    if (retryTimer) window.clearTimeout(retryTimer)
    socket?.close()
    socket = null
  }

  return { latest, connected, connect, disconnect }
})
