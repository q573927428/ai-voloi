// ==================== 页面路由 ====================
import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from './views/DashboardView.vue'
import SignalMonitorView from './views/SignalMonitorView.vue'
import SignalDetailView from './views/SignalDetailView.vue'
import ConfigurationView from './views/ConfigurationView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: DashboardView, meta: { title: '运行概览' } },
    { path: '/signals', component: SignalMonitorView, meta: { title: 'Signal 监控' } },
    { path: '/signals/:id', component: SignalDetailView, meta: { title: 'Signal 详情' } },
    { path: '/configuration', component: ConfigurationView, meta: { title: '参数配置' } },
  ],
})
