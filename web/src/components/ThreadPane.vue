<script setup>
/** 第二栏 · 观测记录：用户=批注式右栏，Kairos=全宽文档流（Markdown 渲染+朗读）；
 *  输入卡（WorkBuddy 式）：上传/语音转文字/模式/模型/权限/发送（v2.8 全 SVG 图标）；
 *  plan 模式产出 → 「转为计划送审」；工具轨迹含子代理行；消息带真实 token 消耗 */
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useSessionStore } from '../stores/session'
import { useSettingsStore } from '../stores/settings'
import { apiGet, apiPost, apiUpload } from '../api'
import { renderMd } from '../lib/md'
import Icon from './Icon.vue'

const store = useSessionStore()
const settings = useSettingsStore()
const draft = ref('')
const scroller = ref(null)
const goals = ref([])
const planSent = ref('')
const fileInput = ref(null)

/* ── 附件（v2.5）─────────────────────────────────── */
const attachments = ref([])   // [{name,saved_path,text_path,kind,text_len,note}]
const uploading = ref(false)

async function onPickFile(e) {
  const f = e.target.files?.[0]
  if (!f) return
  e.target.value = ''
  uploading.value = true
  try {
    const r = await apiUpload('/api/upload', f)
    attachments.value.push(r)
  } catch (err) {
    store.lastError = `上传失败：${err}`
  } finally {
    uploading.value = false
  }
}

function removeAttach(i) { attachments.value.splice(i, 1) }
function attachIcon(a) {  // 返回 Icon 名（v2.8 去 emoji）
  if (a.kind === 'image') return 'image'
  if (a.kind === 'pdf') return 'file-text'
  if (a.kind === 'docx') return 'file-text'
  return 'file'
}

/* v2.8：token 消耗显示（本轮 SSE usage / 历史 messages.tokens） */
function tokFmt(n) {
  if (!n) return ''
  return n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'k' : String(n)
}
function usageLabel(m) {
  if (m.role !== 'assistant') return ''
  if (m.usage && (m.usage.prompt_tokens || m.usage.completion_tokens)) {
    return `↑${tokFmt(m.usage.prompt_tokens)} ↓${tokFmt(m.usage.completion_tokens)} tokens`
  }
  if (m.tokens) return `${tokFmt(m.tokens)} tokens`
  return ''
}

/* ── 语音转文字（v2.5）：MediaRecorder → /api/stt ── */
const recording = ref(false)
const sttOk = ref(true)
let recorder = null
let chunks = []

async function toggleRecord() {
  if (recording.value) {
    recorder?.stop()
    return
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    recorder = new MediaRecorder(stream)
    chunks = []
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data) }
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop())
      recording.value = false
      if (!chunks.length) return
      const blob = new Blob(chunks, { type: chunks[0].type || 'audio/webm' })
      uploading.value = true
      try {
        const r = await apiUpload('/api/stt', new File([blob], 'speech.webm', { type: blob.type }))
        draft.value = (draft.value ? draft.value + ' ' : '') + (r.text || '').trim()
      } catch (err) {
        store.lastError = `语音识别失败：${String(err).slice(0, 160)}`
        sttOk.value = false
      } finally {
        uploading.value = false
      }
    }
    recorder.start()
    recording.value = true
  } catch (e) {
    store.lastError = '无法访问麦克风：' + e
  }
}

/* ── TTS 朗读（v2.5）：浏览器 speechSynthesis，零后端 ── */
const speakingId = ref('')
function toggleSpeak(m) {
  if (!window.speechSynthesis) { store.lastError = '当前环境不支持语音合成'; return }
  if (speakingId.value === m.id) {
    window.speechSynthesis.cancel()
    speakingId.value = ''
    return
  }
  window.speechSynthesis.cancel()
  const u = new SpeechSynthesisUtterance(String(m.content || '').slice(0, 4000))
  u.lang = 'zh-CN'
  u.rate = 1.05
  u.onend = () => { speakingId.value = '' }
  u.onerror = () => { speakingId.value = '' }
  speakingId.value = m.id
  window.speechSynthesis.speak(u)
}

/* ── 待确认（v3.1）：审批挪进对话流，右栏不再弹卡 ── */
const confirmations = ref([])

async function loadConfirmations() {
  try { confirmations.value = await apiGet('/api/confirmations') } catch { confirmations.value = [] }
}

/* 只展示当前会话的待审批（无 session_id 的全局项也显示） */
const pendingCfms = computed(() => confirmations.value.filter((c) => {
  if (c.status !== 'pending') return false
  try {
    const d = JSON.parse(c.payload)
    return !d.session_id || d.session_id === store.currentId
  } catch { return true }
}))

function cfmPayload(c) {
  try {
    const d = JSON.parse(c.payload)
    if (c.action === 'plan' && d.plan_md) return d.plan_md
    if (d && typeof d === 'object') return JSON.stringify(d, null, 2)
  } catch { /* 纯文本 */ }
  return c.payload
}

async function decide(c, decision) {
  /* v3.2 提案-哈希 HITL：回传审阅时看到的那行 payload 的哈希；
     服务端比对不一致（内容被改写）则拒绝本次决策（409），防"所见≠所执行"。 */
  try {
    await apiPost(`/api/confirmations/${c.id}/decide`, { decision, expected_hash: c.content_hash || '' })
  } catch (e) {
    if (String(e && e.message).includes('409')) {
      alert('内容哈希校验未通过：队列中的内容与你刚才审阅的不一致（可能已被改写），本次决策已被拒绝。请刷新后重新审阅。')
    } else {
      alert(`决策失败：${(e && e.message) || e}`)
    }
    await loadConfirmations()
    return
  }
  await loadConfirmations()
  if (c.action === 'plan' && decision === 'approved') {
    // 批准即开工：切到计划所属会话并自动发执行指令（走完整 SSE 管线，进度实时可见）
    try {
      const d = JSON.parse(c.payload)
      if (d.session_id) {
        if (store.currentId !== d.session_id) await store.openSession(d.session_id)
        store.sendMessage('计划已批准，请按计划开始执行。').catch(() => {})
      }
    } catch { /* payload 异常时仅刷新不开工 */ }
  }
  store.confirmTick++
}

let cfmTimer = null

/* ── 模式 / 模型 / 权限（v2.0）───────────────────── */
const EXAMPLES = ['盯一下腾讯校招页，有更新就告诉我', '把今天的面试复盘记到长期记忆', '这周投递台账汇总一下']

const MODES = [
  { v: 'chat', label: '对话' },
  { v: 'plan', label: '规划' },
  { v: 'goal', label: '目标' }
]
const AUTONOMY = [
  { v: 'standard', label: '审批' },
  { v: 'semi', label: '半自动' },
  { v: 'auto', label: '自动' }
]

async function setMode(m) {
  if (m === 'goal') {
    try { goals.value = await apiGet('/api/goals') } catch { goals.value = [] }
    const active = goals.value.filter((g) => g.status === 'active')
    if (!store.current?.goal_ref && active.length === 1) {
      await store.patchSession({ mode: 'goal', goal_ref: String(active[0].id) })
      return
    }
    if (!active.length) {
      draft.value = 'goal_add 登记一个长期目标：'
      await store.patchSession({ mode: 'goal' })
      return
    }
  }
  await store.patchSession({ mode: m, ...(m === 'goal' ? {} : { goal_ref: null }) })
}
async function bindGoal(e) { await store.patchSession({ goal_ref: e.target.value || null }) }
async function setModel(e) { await store.patchSession({ model: e.target.value || null }) }
async function setAutonomy(e) { await store.patchSession({ autonomy: e.target.value }) }

async function submitPlan(content) {
  await apiPost('/api/plans', { session_id: store.currentId, plan_md: content })
  planSent.value = content
  store.confirmTick++
}

/* ── 发送：附件引用块拼进消息 ────────────────────── */
async function send() {
  const text = draft.value.trim()
  if (!text || store.streaming) return
  let full = text
  if (attachments.value.length) {
    const lines = attachments.value.map((a) =>
      `- 附件「${a.name}」（${a.saved_path}${a.text_path ? `，全文见 ${a.text_path}` : ''}${a.text_len ? `，${a.text_len} 字` : ''}）${a.note ? `：${a.note}` : ''}`)
    full = `【本次附带 ${attachments.value.length} 个附件】\n${lines.join('\n')}\n\n${text}`
    attachments.value = []
  }
  draft.value = ''
  await store.sendMessage(full)
}

function useExample(e) { draft.value = e }

function onCopyClick(e) {
  const btn = e.target.closest('.code-copy')
  if (!btn) return
  const pre = btn.closest('.codeblock')?.querySelector('pre code')
  if (pre) navigator.clipboard.writeText(pre.textContent || '').then(() => {
    btn.textContent = '已复制 ✓'
    setTimeout(() => { btn.textContent = '复制' }, 1600)
  })
}

watch(() => store.messages.length, async () => {
  await nextTick()
  scroller.value?.scrollTo({ top: scroller.value.scrollHeight })
})
watch(() => store.liveTrace.length, async () => {
  await nextTick()
  scroller.value?.scrollTo({ top: scroller.value.scrollHeight })
})
watch(() => store.messages[store.messages.length - 1]?.content, async (nv, ov) => {
  if (store.streaming && nv && ov !== undefined) {
    await nextTick()
    const el = scroller.value
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 120) {
      el.scrollTo({ top: el.scrollHeight })
    }
  }
})
watch(() => store.currentId, () => { attachments.value = [] })

onMounted(async () => {
  document.addEventListener('click', onCopyClick)
  if (!settings.loaded) settings.load()
  try { sttOk.value = (await apiGet('/api/stt/status')).configured || true } catch { /* 忽略 */ }
  loadConfirmations()
  cfmTimer = setInterval(loadConfirmations, 2500)
})
onUnmounted(() => {
  document.removeEventListener('click', onCopyClick)
  window.speechSynthesis?.cancel()
  if (cfmTimer) { clearInterval(cfmTimer); cfmTimer = null }
})
</script>

<template>
  <main class="thread">
    <div ref="scroller" class="scroll" @click="onCopyClick">
      <div class="col">
      <!-- 空状态 -->
      <div v-if="!store.messages.length" class="first">
        <h1>交代一件事。</h1>
        <p class="lede">Kairos 会自己拆步骤、调工具、记结论——你只管说要什么。</p>
        <div class="tries">
          <span class="plate">try</span>
          <button v-for="e in EXAMPLES" :key="e" class="try" @click="useExample(e)">{{ e }}</button>
        </div>
      </div>

      <template v-for="m in store.messages" :key="m.id">
        <div v-if="m.role === 'user'" class="user-row">
          <div class="user-note">
            <span class="plate">you</span>
            <div class="user-text">{{ m.content }}</div>
          </div>
        </div>
        <div v-else class="doc">
          <div class="doc-head">
            <span class="plate by">kairos</span>
            <span class="hairline"></span>
            <span v-if="usageLabel(m)" class="tok-chip">{{ usageLabel(m) }}</span>
            <button class="tts" :class="{ on: speakingId === m.id }"
                    :title="speakingId === m.id ? '停止朗读' : '朗读'"
                    @click="toggleSpeak(m)">
              <Icon :name="speakingId === m.id ? 'stop' : 'volume'" :size="14" />
            </button>
          </div>
          <!-- eslint-disable-next-line vue/no-v-html -->
          <div class="doc-body md" v-html="renderMd(m.content) || '　'"></div>
          <div v-if="store.mode === 'plan' && !store.streaming && m.content && m.content !== planSent"
               class="plan-card">
            <span class="txt">这是一份计划草案。转为正式计划送入审批队列，批准后切回对话模式按计划执行。</span>
            <button class="btn primary" @click="submitPlan(m.content)">转为计划送审</button>
          </div>
          <div v-else-if="store.mode === 'plan' && m.content === planSent" class="plan-card">
            <span class="txt">计划已送审 → 审批卡出现在下方对话流里，点「批准并开工」后自动执行。</span>
          </div>
        </div>
      </template>

      <!-- 审批卡（v3.1）：和常规 agent 一样在对话流里批准 -->
      <div v-for="c in pendingCfms" :key="c.id" class="doc cfm-doc">
        <div class="doc-head">
          <span class="plate by">{{ c.action === 'plan' ? '计划审批' : (c.action || '待确认') }}</span>
          <span class="hairline"></span>
        </div>
        <pre class="cfm-payload">{{ cfmPayload(c) }}</pre>
        <div class="cfm-btns">
          <button class="btn primary" @click="decide(c, 'approved')">{{ c.action === 'plan' ? '批准并开工' : '批准' }}</button>
          <button class="btn danger" @click="decide(c, 'rejected')">拒绝</button>
        </div>
      </div>

      <div v-if="store.streaming && store.liveTrace.length" class="rail">
        <div v-for="(t, i) in store.liveTrace" :key="i" class="rail-row" :class="{ sub: t.sub }">
          <span class="rail-dot" :class="{ ok: t.done && t.ok, err: t.done && !t.ok, run: !t.done }"></span>
          <span class="rail-name">{{ t.tool }}</span>
          <span class="rail-state">{{ t.done ? (t.ok ? 'done' : 'failed') : 'running' }}</span>
        </div>
      </div>
      <div v-if="store.streaming && !store.liveTrace.length" class="thinking">
        <span class="dot" style="display:inline-block;margin-right:10px"></span>思考中
      </div>
      </div>
    </div>

    <!-- 输入卡（v2.5：真正的框） -->
    <div class="composer">
      <div class="input-card" :class="{ 'recording': recording }">
        <!-- 附件 chips -->
        <div v-if="attachments.length" class="att-row">
          <span v-for="(a, i) in attachments" :key="a.saved_path" class="att-chip"
                :title="`${a.saved_path}${a.note ? ' · ' + a.note : ''}`">
            <Icon :name="attachIcon(a)" :size="13" />
            {{ a.name }}
            <button class="att-x" @click="removeAttach(i)"><Icon name="x" :size="11" /></button>
          </span>
        </div>
        <textarea v-model="draft" rows="2" placeholder="说吧。可以拖一句话进来，也可以带上文件。"
                  @keydown.enter.exact.prevent="send"></textarea>
        <div class="card-tools">
          <button class="tool" title="上传图片/Word/PDF" @click="fileInput.click()"><Icon name="paperclip" :size="16" /></button>
          <button class="tool" :class="{ rec: recording }" title="语音输入（说完了再点一下停止）"
                  @click="toggleRecord"><Icon name="mic" :size="16" /></button>
          <input ref="fileInput" type="file" hidden
                 accept=".png,.jpg,.jpeg,.bmp,.gif,.webp,.pdf,.docx,.txt,.md,.csv,.json,.html">
          <span v-if="uploading" class="plate" style="color:var(--acc)">处理中…</span>
          <span v-if="recording" class="rec-hint">录音中…</span>

          <span style="flex:1"></span>

          <div class="chip-seg">
            <button v-for="m in MODES" :key="m.v" class="chip"
                    :class="{ active: store.mode === m.v }"
                    @click="setMode(m.v)">{{ m.label }}</button>
          </div>
          <select v-if="store.mode === 'goal' && goals.length" class="mini-select"
                  :value="store.current?.goal_ref || ''" @change="bindGoal">
            <option value="">— 绑定目标 —</option>
            <option v-for="g in goals.filter(g => g.status === 'active')" :key="g.id" :value="String(g.id)">
              {{ g.title }}
            </option>
          </select>
          <select v-if="settings.models.length > 1" class="mini-select"
                  :value="store.current?.model || settings.defaultModel" @change="setModel"
                  title="本会话模型">
            <option v-for="m in settings.models" :key="m.name" :value="m.name">{{ m.label }}</option>
          </select>
          <select class="mini-select" :class="{ 'chip-warn': store.autonomy !== 'standard' }"
                  :value="store.autonomy" @change="setAutonomy" title="审批权限档">
            <option v-for="a in AUTONOMY" :key="a.v" :value="a.v">{{ a.label }}</option>
          </select>
          <button class="send" :disabled="store.streaming || (!draft.trim() && !attachments.length)"
                  @click="send">{{ store.streaming ? '···' : '发送' }}</button>
        </div>
      </div>
    </div>
  </main>
</template>

<style scoped>
.thread { flex: 1; display: flex; flex-direction: column; min-width: 0; background: var(--bg0); }
.scroll { flex: 1; overflow-y: auto; padding: 36px 48px 20px; scrollbar-gutter: stable both-edges; }

/* 空状态 */
.first { max-width: 62ch; margin: 12vh auto 0; }
.first h1 {
  font-family: var(--serif);
  font-weight: 500;
  font-size: 2.4rem;
  letter-spacing: -0.01em;
  margin: 0 0 10px;
  color: var(--tx);
}
.lede { color: var(--tx2); margin: 0 0 34px; }
.tries { display: flex; flex-direction: column; gap: 2px; align-items: flex-start; }
.tries .plate { display: block; margin-bottom: 8px; }
.try {
  background: none; border: none; padding: 4px 0;
  color: var(--tx2); font-size: var(--fs-sm); cursor: pointer;
  text-align: left;
  border-bottom: 1px solid transparent;
  transition: color 0.18s var(--ease), border-color 0.18s var(--ease);
}
.try:hover { color: var(--acc); border-color: var(--acc-dim); }
.try::before { content: '→ '; color: var(--tx3); }

/* 用户批注 */
.user-row { display: flex; justify-content: flex-end; margin: 26px 0; }
.user-note {
  max-width: 58%;
  border-right: 2px solid var(--pri);
  padding: 2px 14px 2px 0;
  text-align: right;
}
.user-note .plate { color: var(--acc-dim); }
.user-text { font-size: var(--fs-base); color: var(--tx); white-space: pre-wrap; word-break: break-word; }

/* 对话列：与输入卡同轴——宽度弹性（上限 1000px），侧栏/右栏收起时内容真正"整体展开"变宽，
   而不是只平移位置 */
.scroll > .col { max-width: 1000px; margin: 0 auto; width: 100%; }

/* Kairos 文档流：宽度与输入卡对齐（撑满 .col，左右边缘上下对齐） */
.doc { margin: 0 auto 30px; }
.doc-head { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.hairline { flex: 1; height: 1px; background: var(--line); }
.tts {
  background: none; border: none; cursor: pointer;
  font-size: 13px; color: var(--tx3); padding: 3px 6px; border-radius: 5px;
  display: inline-flex; align-items: center;
  transition: background 0.15s var(--ease), color 0.15s var(--ease);
}
.tts:hover { background: var(--bg2); color: var(--acc); }
.tts.on { color: var(--acc); background: var(--bg2); }
.tok-chip {
  font-family: var(--mono); font-size: var(--fs-xs); color: var(--tx3);
  letter-spacing: 0.03em; user-select: none;
}
.doc-body { font-size: var(--fs-base); line-height: calc(var(--rh) + 0.05); white-space: pre-wrap; word-break: break-word; }
.doc-body.md { white-space: normal; }

/* 工具时间线 */
.rail { margin: 4px auto 22px; border-left: 1px solid var(--line); padding-left: 0; }

/* 审批卡（v3.1）：对话流内批准 */
.cfm-doc {
  border: 1px solid var(--acc-dim); border-radius: 10px;
  padding: 16px 18px; margin-bottom: 26px; background: var(--bg0);
}
.cfm-payload {
  font-family: var(--mono); font-size: var(--fs-xs); color: var(--tx2);
  background: var(--bg1); border: 1px solid var(--line); border-radius: 8px;
  padding: 12px; margin: 0 0 12px;
  white-space: pre-wrap; word-break: break-word;
  max-height: 320px; overflow-y: auto;
}
.cfm-btns { display: flex; gap: 10px; }
.rail-row {
  display: flex; align-items: baseline; gap: 10px;
  padding: 5px 0 5px 16px;
  position: relative;
  font-family: var(--mono);
}
.rail-row.sub { padding-left: 32px; }
.rail-row.sub .rail-name { color: var(--acc); }
.rail-dot {
  position: absolute; left: -3.5px; top: 12px;
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--tx3);
}
.rail-row.sub .rail-dot { left: 12.5px; }
.rail-dot.run { background: var(--acc); animation: breathe 1.6s var(--ease) infinite; }
.rail-dot.ok { background: var(--ok); }
.rail-dot.err { background: var(--err); }
.rail-name { font-size: var(--fs-xs); color: var(--tx2); }
.rail-state { font-size: var(--fs-xs); color: var(--tx3); margin-left: auto; letter-spacing: 0.1em; text-transform: uppercase; }
.thinking { color: var(--tx3); font-size: var(--fs-sm); padding: 8px 0 20px; display: flex; align-items: center; }

/* 输入卡：真正的框 */
.composer { padding: 8px 48px 18px; background: var(--bg0); }
.input-card {
  max-width: 1000px; margin: 0 auto;
  border: 1px solid var(--line-hi); border-radius: 14px;
  background: var(--bg1);
  padding: 10px 14px 8px;
  transition: border-color 0.18s var(--ease), box-shadow 0.18s var(--ease);
}
.input-card:focus-within {
  border-color: var(--acc-dim);
  box-shadow: 0 2px 16px rgb(62 123 250 / 0.10);
}
.input-card.recording { border-color: var(--err); box-shadow: 0 2px 16px rgb(213 72 74 / 0.15); }
.input-card textarea {
  width: 100%; resize: none;
  background: none; border: none; outline: none;
  color: var(--tx);
  font-family: var(--sans);
  font-size: var(--fs-base);
  line-height: 1.6;
  padding: 4px 0 8px;
  max-height: 30vh;
}
.input-card textarea::placeholder { color: var(--tx3); }
.card-tools { display: flex; align-items: center; gap: 6px; }
.tool {
  background: none; border: none; cursor: pointer;
  font-size: 15px; color: var(--tx2); padding: 5px 7px; border-radius: 7px;
  display: inline-flex; align-items: center;
  transition: background 0.15s var(--ease), color 0.15s var(--ease);
}
.tool:hover { background: var(--bg2); color: var(--acc); }
.tool.rec { background: #fdf1f1; color: var(--err); animation: breathe 1.2s var(--ease) infinite; }
.rec-hint { font-size: var(--fs-xs); color: var(--err); font-family: var(--mono); }
.att-row { display: flex; flex-wrap: wrap; gap: 6px; padding: 2px 0 8px; }
.att-chip {
  display: inline-flex; align-items: center; gap: 6px;
  background: var(--bg0); border: 1px solid var(--line-hi); border-radius: 8px;
  font-size: var(--fs-xs); color: var(--tx2); padding: 4px 10px;
  max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.att-x { background: none; border: none; cursor: pointer; color: var(--tx3); padding: 0 2px; display: inline-flex; align-items: center; }
.att-x:hover { color: var(--err); }
.send {
  background: var(--acc); border: none; cursor: pointer;
  color: #fff; font-size: var(--fs-sm);
  padding: 6px 18px; border-radius: 9px;
  transition: background 0.15s var(--ease), opacity 0.15s var(--ease);
}
.send:hover { background: #275fc4; }
.send:disabled { opacity: 0.35; cursor: default; }
</style>
