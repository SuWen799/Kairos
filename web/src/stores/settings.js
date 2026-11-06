/** 全局设置 store（v2.0 R4/R5/R6/R7）：模型注册表 + 沙箱/多Agent/权限默认档 */
import { defineStore } from 'pinia'
import { apiGet, apiPut } from '../api'

export const useSettingsStore = defineStore('settings', {
  state: () => ({
    models: [],          // [{name,label,tier}]
    defaultModel: '',
    settings: { sandbox: 'basic', multi_agent: true, max_subagents: 3 },
    loaded: false
  }),
  actions: {
    async load() {
      try {
        const m = await apiGet('/api/models')
        this.models = m.models || []
        this.defaultModel = m.default || ''
        this.settings = await apiGet('/api/settings')
        this.loaded = true
      } catch { /* sidecar 重启间隙忽略 */ }
    },
    async save(patch) {
      this.settings = await apiPut('/api/settings', patch)
    }
  }
})
