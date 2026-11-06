/** 会话 store（设计 4.3 stores/session.ts 的 M1 版 + v2.0 扩展）：三栏 UI 的唯一状态源 */
import { defineStore } from 'pinia'
import { apiGet, apiPost, apiPatch, streamChat } from '../api'

export const useSessionStore = defineStore('session', {
  state: () => ({
    sessions: [],        // [{id,title,project,updated_at,mode,autonomy,model}]
    currentId: null,     // ULID
    messages: [],        // [{id,seq,role,content}]
    streaming: false,
    liveTrace: [],       // 当前 run 的工具事件 [{type,tool,ok}]（含 subagent 前缀行）
    lastRunId: null,     // 最近一次 run（InfoPane 事件面板轮询用）
    lastError: '',
    confirmTick: 0       // v2.0：确认队列脏标记（发消息后 InfoPane 确认页刷新）
  }),
  getters: {
    current(state) {
      return state.sessions.find((s) => s.id === state.currentId) || null
    },
    mode(state) {
      return this.current?.mode || 'chat'
    },
    autonomy(state) {
      return this.current?.autonomy || 'standard'
    }
  },
  actions: {
    async loadSessions() {
      this.sessions = await apiGet('/api/sessions')
      if (!this.currentId && this.sessions.length) {
        await this.openSession(this.sessions[0].id)
      } else if (this.currentId) {
        // 同步当前会话的 mode/autonomy（别处改过）
        const cur = this.sessions.find((s) => s.id === this.currentId)
        if (cur && this.current) Object.assign(this.current, cur)
      }
    },
    async openSession(id) {
      this.currentId = id
      this.liveTrace = []
      this.messages = await apiGet(`/api/sessions/${id}/messages`)
    },
    async newSession() {
      const s = await apiPost('/api/sessions', { title: null })
      this.sessions.unshift({ ...s, updated_at: null })
      await this.openSession(s.id)
    },
    /** v2.0：会话级字段切换（模式/权限档/模型/goal 绑定） */
    async patchSession(fields) {
      if (!this.currentId) return
      const r = await apiPatch(`/api/sessions/${this.currentId}`, fields)
      if (this.current) Object.assign(this.current, r)
      // plan 批准切回 chat 后要刷新（plan_md 已入上下文）
      if ('mode' in fields || 'plan_md' in fields) {
        await this.loadSessions()
      }
      return r
    },
    /** 发消息：SSE 逐事件更新（tool_start/tool_done/message_final/run_done/subagent.*） */
    async sendMessage(text) {
      if (!this.currentId || this.streaming) return
      this.lastError = ''
      this.messages.push({ id: `tmp_${Date.now()}`, role: 'user', content: text })
      this.messages.push({ id: 'tmp_wait', role: 'assistant', content: '' })
      this.streaming = true
      this.liveTrace = []
      try {
        await streamChat(this.currentId, text, (ev) => {
          if (ev.type === 'run_started') {
            this.lastRunId = ev.run_id
          } else if (ev.type === 'message.delta') {
            // M2：LLM 正文增量，实时长到占位消息上
            const wait = [...this.messages].reverse().find((m) => m.id === 'tmp_wait')
            if (wait) wait.content += ev.delta || ''
          } else if (ev.type === 'subagent.start') {
            this.liveTrace.push({ type: 'subagent', tool: `⟨${ev.label || ev.role}⟩ 启动`, sub: true })
          } else if (ev.type === 'subagent.step' && ev.tool) {
            this.liveTrace.push({ type: 'subagent', tool: `⟨${ev.label || ev.role}⟩ ${ev.tool}`, sub: true })
          } else if (ev.type === 'subagent.done') {
            this.liveTrace.push({ type: 'subagent', tool: `⟨${ev.label || ev.role}⟩ 完成`, sub: true, done: true, ok: ev.ok })
          } else if (ev.type === 'tool_start') {
            this.liveTrace.push({ type: 'tool_start', tool: ev.tool })
          } else if (ev.type === 'tool_done') {
            const t = this.liveTrace.find((x) => x.type === 'tool_start' && x.tool === ev.tool && !x.done)
            if (t) { t.done = true; t.ok = ev.ok } else { this.liveTrace.push({ type: 'tool_done', tool: ev.tool, ok: ev.ok, done: true }) }
          } else if (ev.type === 'usage') {
            // v2.8：本轮真实 token 消耗挂到占位消息上（历史消息用 tokens 总数）
            const wait = [...this.messages].reverse().find((m) => m.id === 'tmp_wait' || m.id?.startsWith?.('final_'))
            if (wait) {
              wait.usage = { prompt_tokens: ev.prompt_tokens, completion_tokens: ev.completion_tokens }
              wait.tokens = (ev.prompt_tokens || 0) + (ev.completion_tokens || 0)
            }
          } else if (ev.type === 'message_final') {
            const wait = [...this.messages].reverse().find((m) => m.id === 'tmp_wait')
            if (wait) { wait.id = `final_${Date.now()}`; wait.content = ev.answer }
            if (ev.usage) {
              const m = [...this.messages].reverse().find((x) => x.id?.startsWith?.('final_'))
              if (m) { m.usage = ev.usage; m.tokens = ev.usage.total }
            }
          } else if (ev.type === 'error') {
            this.lastError = ev.message
          }
        })
      } catch (e) {
        this.lastError = String(e)
      } finally {
        this.streaming = false
        this.confirmTick++
        const wait = this.messages.find((m) => m.id === 'tmp_wait')
        if (wait && !wait.content) wait.content = this.lastError ? `（出错）${this.lastError}` : '（无返回）'
        // 刷新会话列表排序（后端 updated_at 变了）
        this.loadSessions().catch(() => {})
      }
    }
  }
})
