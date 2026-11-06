<script setup>
/** 第三栏 · 仪表面板（v2.5 重组）：动态(事件+待确认) / 产物 / 运行(审计+预算) */
import { onMounted, onUnmounted, ref, watch } from 'vue'
import Icon from './Icon.vue'
import { apiGet, apiPost } from '../api'
import { useSessionStore } from '../stores/session'
import { renderMd } from '../lib/md'

const store = useSessionStore()
// M3 移动抽屉：多根组件 class 穿透失效（v2.5 踩过的坑），必须走显式 prop 绑根元素
defineProps({ drawerOpen: Boolean })
const events = ref([])
const auditRows = ref([])
const chainOk = ref(null)
const budgetRows = ref([])
const tab = ref('live')
let pollTimer = null

/* ── 待确认计数（v3.1：审批卡挪进对话流，这里只留角标提示）── */
const confirmations = ref([])

async function loadConfirmations() {
  try {
    confirmations.value = await apiGet('/api/confirmations')
  } catch { confirmations.value = [] }
}
const pendingCfm = () => confirmations.value.filter((c) => c.status === 'pending').length

/* ── 工单（v3.0 M-S1）────────────────────────────── */
const tickets = ref([])

const TICKET_BADGE = {
  queued: '排队', working: '执行中', waiting_human: '待人工', ready: '已完成',
  blocked: '受阻', failed: '失败', done: '已结束', cancelled: '已叫停'
}

async function loadTickets() {
  try { tickets.value = await apiGet('/api/tickets') } catch { tickets.value = [] }
}

async function openTicketSession(t) {
  if (t.session_id && t.session_id !== store.currentId) await store.openSession(t.session_id)
}

async function stopTicket(t) {
  await apiPost(`/api/tickets/${t.id}/stop`, {})
  await loadTickets()
}

/* ── 发现（v3.2 M-S2 事件源观察档）────────────────── */
const triggers = ref([])
const trigBusy = ref('')

async function loadTriggers() {
  try { triggers.value = await apiGet('/api/triggers?status=observed') } catch { triggers.value = [] }
}

async function promoteTrigger(t) {
  if (trigBusy.value) return
  trigBusy.value = t.id
  try {
    await apiPost(`/api/triggers/${t.id}/promote`, {})
    await Promise.all([loadTriggers(), loadTickets()])
  } finally { trigBusy.value = '' }
}

async function dismissTrigger(t) {
  if (trigBusy.value) return
  trigBusy.value = t.id
  try {
    await apiPost(`/api/triggers/${t.id}/dismiss`, {})
    await loadTriggers()
  } finally { trigBusy.value = '' }
}

/* ── 定时任务（automations 管理）────────────────── */
const autos = ref([])
const autoBusy = ref('')

async function loadAutomations() {
  try { autos.value = await apiGet('/api/automations') } catch { autos.value = [] }
}

async function toggleAuto(a) {
  if (autoBusy.value) return
  autoBusy.value = a.id
  try {
    await apiPost(`/api/automations/${a.id}/toggle`, {})
    await loadAutomations()
  } finally { autoBusy.value = '' }
}

/* ── 产物预览 ────────────────────────────────────── */
const artifacts = ref([])
const preview = ref(null)
const previewLoading = ref(false)
const copied = ref('')

async function loadArtifacts() {
  try { artifacts.value = await apiGet('/api/workspace/files') } catch { artifacts.value = [] }
}

function fmtSize(n) {
  if (n < 1024) return `${n} B`
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1048576).toFixed(1)} MB`
}

async function openPreview(a) {
  previewLoading.value = true
  preview.value = { ...a }
  try {
    const d = await apiGet(`/api/workspace/file?path=${encodeURIComponent(a.path)}`)
    preview.value = { ...a, ...d }
  } catch (e) {
    preview.value = { ...a, content: `（读取失败：${e}）` }
  } finally {
    previewLoading.value = false
  }
}
function closePreview() { preview.value = null }

async function copyText(text, tag) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = tag
    setTimeout(() => { copied.value = '' }, 1600)
  } catch { /* 剪贴板权限 */ }
}
async function copyPath() {
  if (preview.value) copyText(preview.value.path, 'path')
}
async function openFile() {
  if (preview.value) await apiPost('/api/workspace/open', { path: preview.value.path })
}
async function revealFile() {
  if (preview.value) await apiPost('/api/workspace/reveal', { path: preview.value.path })
}

/* ── 事件 / 审计 / 预算 ─────────────────────────── */
async function pollEvents() {
  if (!store.lastRunId) return
  try {
    events.value = await apiGet(`/api/runs/${store.lastRunId}/events`)
  } catch { /* sidecar 重启间隙忽略 */ }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(() => { pollEvents(); loadConfirmations(); loadTriggers() }, 2500)
  pollEvents()
  loadConfirmations()
  loadTriggers()
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

async function loadAudit() {
  try {
    auditRows.value = await apiGet('/api/audit?limit=40')
    const v = await apiGet('/api/audit/verify')
    chainOk.value = v.broken_at === null
  } catch { auditRows.value = [] }
}
async function loadBudget() {
  try { budgetRows.value = await apiGet('/api/budgets') } catch { budgetRows.value = [] }
}

function switchTab(t) {
  tab.value = t
  if (t === 'live') startPolling()
  else {
    stopPolling()
    if (t === 'artifacts') loadArtifacts()
    if (t === 'run') { loadAudit(); loadBudget() }
  }
}

function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}

onMounted(() => { startPolling(); loadBudget(); loadArtifacts(); loadTickets(); loadAutomations() })
onUnmounted(stopPolling)
watch(() => store.lastRunId, () => { if (tab.value === 'live') startPolling() })
watch(() => store.confirmTick, () => { if (tab.value === 'live') { loadConfirmations(); loadTickets() } })
</script>

<template>
  <aside class="info" :class="{ 'drawer-open': drawerOpen }">
    <div class="head">
      <span class="plate">observatory</span>
      <span v-if="pendingCfm()" class="badge">{{ pendingCfm() }}</span>
      <span class="dot" style="margin-left:auto"></span>
    </div>

    <div class="tabs">
      <button class="tab" :class="{ active: tab === 'live' }" @click="switchTab('live')">
        动态{{ pendingCfm() || triggers.length ? ` ·${pendingCfm() + triggers.length}` : '' }}
      </button>
      <button class="tab" :class="{ active: tab === 'artifacts' }" @click="switchTab('artifacts')">产物</button>
      <button class="tab" :class="{ active: tab === 'run' }" @click="switchTab('run')">运行</button>
    </div>

    <div class="body">
      <!-- 动态：工单置顶 + 待确认 + 事件流 -->
      <template v-if="tab === 'live'">
        <div v-for="t in tickets.slice(0, 8)" :key="t.id" class="tkt" :class="{ live: t.status === 'working' }">
          <div class="cfm-head">
            <span class="cfm-action tkt-title" @click="openTicketSession(t)">{{ t.title }}</span>
            <span class="tkt-badge" :data-st="t.status">{{ TICKET_BADGE[t.status] || t.status }}</span>
          </div>
          <div class="tkt-meta">
            <span>{{ fmtTime(t.updated_at) }}</span>
            <span v-if="t.error" class="tkt-err" :title="t.error">出错</span>
            <span style="flex:1"></span>
            <button v-if="['queued','working','waiting_human'].includes(t.status)"
                    class="btn danger tkt-stop" @click="stopTicket(t)">叫停</button>
          </div>
        </div>

        <!-- 发现（M-S2 观察档）：事件源产出，一键转工单 -->
        <template v-if="triggers.length">
          <span class="plate" style="display:block;margin:2px 0 8px">发现（待你处理）</span>
          <div v-for="t in triggers" :key="t.id" class="tkt trig">
            <div class="cfm-head">
              <span class="cfm-action tkt-title" :title="t.payload">{{ t.title }}</span>
              <span class="trig-kind">{{ t.source_kind }}</span>
            </div>
            <div v-if="t.assessment" class="trig-assess">{{ t.assessment }}</div>
            <div v-else-if="t.gate !== 'pass'" class="trig-assess gate-block">{{ t.gate }}</div>
            <div class="tkt-meta">
              <span>{{ fmtTime(t.created_at) }}</span>
              <span style="flex:1"></span>
              <button class="btn primary trig-btn" :disabled="trigBusy === t.id" @click="promoteTrigger(t)">
                {{ trigBusy === t.id ? '…' : '转工单' }}
              </button>
              <button class="btn trig-btn" :disabled="trigBusy === t.id" @click="dismissTrigger(t)">忽略</button>
            </div>
          </div>
        </template>

        <div v-if="!events.length && !pendingCfm() && !triggers.length" class="quiet">
          发起一次对话，<br>工具调用会逐条落进事件流。
        </div>
        <div v-for="e in events" :key="e.id" class="ev">
          <span class="ev-seq">#{{ e.seq }}</span>
          <span class="ev-type">{{ e.type }}</span>
          <span class="ev-time">{{ fmtTime(e.created_at) }}</span>
        </div>
      </template>

      <!-- 产物 -->
      <template v-else-if="tab === 'artifacts'">
        <div v-if="!artifacts.length" class="quiet">
          工作区还没有产物。<br>让 Kairos 写报告/导出台账，文件会落在这里。
        </div>
        <button v-for="a in artifacts" :key="a.path" class="art-row" @click="openPreview(a)">
          <span class="art-name">{{ a.name }}</span>
          <span class="art-size">{{ fmtSize(a.size) }}</span>
        </button>
      </template>

      <!-- 运行：定时任务 + 审计链 + 预算 -->
      <template v-else>
        <span class="plate">定时任务（automations）</span>
        <div v-if="!autos.length" class="quiet">
          还没有定时任务。对话里说「每天早上八点帮我盯一下XX」，或让 Kairos 自己建。
        </div>
        <div v-for="a in autos" :key="a.id" class="tkt auto">
          <div class="cfm-head">
            <span class="cfm-action tkt-title" :title="a.prompt">{{ a.name }}</span>
            <span class="tkt-badge" :data-st="a.enabled ? 'working' : 'blocked'">{{ a.enabled ? '启用' : '停用' }}</span>
          </div>
          <div class="tkt-meta">
            <span class="trig-kind">{{ a.cron }}</span>
            <span style="flex:1"></span>
            <button class="btn trig-btn" :disabled="autoBusy === a.id" @click="toggleAuto(a)">
              {{ a.enabled ? '停用' : '启用' }}
            </button>
          </div>
        </div>
        <span class="plate" style="display:block;margin-top:14px">审计链</span>
        <div class="chain" :class="chainOk ? 'ok' : 'bad'">
          {{ chainOk === null ? '校验中…' : chainOk ? '哈希链完整 ✓' : '哈希链损坏 ✗' }}
        </div>
        <div v-for="a in auditRows" :key="a.id" class="ev">
          <span class="ev-type">{{ a.event_type }}</span>
          <span class="ev-time">{{ a.actor }}</span>
        </div>
        <span class="plate" style="display:block;margin-top:14px">token 预算</span>
        <div v-if="!budgetRows.length" class="quiet">暂无消耗记录。</div>
        <div v-for="b in budgetRows" :key="b.scope + b.period" class="bud">
          <div class="bud-scope">{{ b.scope }} · {{ b.period }}</div>
          <div class="bud-bar">
            <div class="bud-fill" :style="{ width: Math.min(100, b.used / b.limit * 100) + '%' }"></div>
          </div>
          <div class="bud-nums">{{ b.used.toLocaleString() }} / {{ b.limit.toLocaleString() }}</div>
        </div>
      </template>
    </div>
  </aside>

  <!-- 产物预览大窗 -->
  <div v-if="preview" class="modal-scrim" @click.self="closePreview">
    <div class="modal wide">
      <div class="modal-head">
        <span class="modal-title">{{ preview.name }}</span>
        <span class="art-meta">{{ fmtSize(preview.size || 0) }} · {{ preview.path }}</span>
        <button class="btn" @click="copyText(preview.content || '', 'md')">
          {{ copied === 'md' ? '已复制 ✓' : '复制内容' }}
        </button>
        <button class="btn" @click="copyPath">{{ copied === 'path' ? '已复制 ✓' : '复制路径' }}</button>
        <button class="btn" @click="revealFile">在文件夹中显示</button>
        <button class="btn primary" @click="openFile">打开文件</button>
        <button class="modal-x" @click="closePreview"><Icon name="x" :size="15" /></button>
      </div>
      <div class="modal-body">
        <div v-if="previewLoading" class="quiet">读取中…</div>
        <template v-else>
          <!-- eslint-disable-next-line vue/no-v-html -->
          <div v-if="preview.ext === '.md' && preview.content != null" class="doc-body md" v-html="renderMd(preview.content)"></div>
          <iframe v-else-if="preview.html != null" class="art-frame" sandbox="" :srcdoc="preview.html"></iframe>
          <pre v-else-if="preview.content != null" class="art-pre">{{ preview.content }}</pre>
          <div v-else class="quiet">
            该格式（{{ preview.ext || '二进制' }}）不支持内嵌预览，点上方「打开文件」用系统程序查看。
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.info {
  width: 280px; flex-shrink: 0;
  background: var(--bg1);
  display: flex; flex-direction: column;
}
.head {
  display: flex; align-items: center;
  padding: 24px 20px 16px;
  border-bottom: 1px solid var(--line);
}
.badge {
  margin-left: 8px;
  background: var(--err); color: #fff;
  font-size: var(--fs-xs); font-family: var(--mono);
  border-radius: 9px; padding: 0 7px; line-height: 18px;
}
.tabs { display: flex; border-bottom: 1px solid var(--line); }
.tab {
  flex: 1;
  background: none; border: none; cursor: pointer;
  padding: 9px 0 8px;
  font-family: var(--mono); font-size: var(--fs-xs);
  letter-spacing: 0.14em; text-transform: uppercase;
  color: var(--tx3);
  border-bottom: 1px solid transparent;
  margin-bottom: -1px;
  transition: color 0.18s var(--ease), border-color 0.18s var(--ease);
}
.tab.active { color: var(--acc); border-color: var(--acc-dim); }
.body { flex: 1; overflow-y: auto; padding: 12px 20px; }
.quiet { font-size: var(--fs-xs); color: var(--tx3); line-height: 2; }

.chain { font-size: var(--fs-xs); font-family: var(--mono); padding: 6px 0 10px; }
.chain.ok { color: var(--ok); }
.chain.bad { color: var(--err); }

.ev {
  display: flex; align-items: baseline; gap: 8px;
  font-family: var(--mono); font-size: var(--fs-xs);
  color: var(--tx2);
  padding: 6px 0;
  border-bottom: 1px solid var(--line);
}
.ev-seq { color: var(--tx3); }
.ev-type { flex: 1; }
.ev-time { color: var(--tx3); }

/* 工单卡（v3.0 M-S1）*/
.tkt { border: 1px solid var(--line); border-left: 3px solid var(--acc-dim); border-radius: 8px; padding: 9px 12px; margin-bottom: 8px; background: var(--bg0); }
.tkt.live { border-left-color: var(--acc); background: var(--acc-dim); }
.tkt-title { cursor: pointer; }
.tkt-title:hover { color: var(--acc); }
.tkt-badge { font-size: var(--fs-xs); font-family: var(--mono); padding: 1px 8px; border-radius: 999px; background: var(--bg1); color: var(--tx2); border: 1px solid var(--line); }
.tkt-badge[data-st="working"] { color: var(--acc); border-color: var(--acc-dim); background: var(--acc-dim); }
.tkt-badge[data-st="ready"] { color: var(--ok); border-color: var(--ok); }
.tkt-badge[data-st="failed"], .tkt-badge[data-st="blocked"] { color: var(--err); border-color: var(--err); }
.tkt-meta { display: flex; align-items: center; gap: 8px; font-size: var(--fs-xs); color: var(--tx3); margin-top: 4px; }

/* 发现（M-S2 观察档）与定时任务卡 */
.tkt.trig { border-left-color: var(--warn, #d97706); }
.trig-kind { font-family: var(--mono); font-size: var(--fs-xs); color: var(--tx3); background: var(--bg1); border-radius: 5px; padding: 1px 6px; }
.trig-assess { font-size: var(--fs-xs); color: var(--tx2); line-height: 1.6; margin-top: 4px; }
.trig-assess.gate-block { color: var(--err); }
.trig-btn { padding: 3px 10px; font-size: var(--fs-xs); }
.tkt-err { color: var(--err); }
.tkt-stop { font-size: var(--fs-xs); padding: 2px 8px; }
.cfm-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.cfm-action { font-family: var(--mono); font-size: var(--fs-xs); color: var(--tx); font-weight: 620; }

.art-row {
  width: 100%; display: flex; align-items: baseline; gap: 8px;
  background: none; border: none; border-bottom: 1px solid var(--line);
  cursor: pointer; text-align: left; padding: 8px 2px;
  color: inherit;
  transition: background 0.15s var(--ease);
}
.art-row:hover { background: var(--bg2); }
.art-name {
  flex: 1; min-width: 0;
  font-size: var(--fs-xs); font-family: var(--mono); color: var(--tx2);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.art-row:hover .art-name { color: var(--acc); }
.art-size { font-family: var(--mono); font-size: var(--fs-xs); color: var(--tx3); flex-shrink: 0; }

.bud { margin-bottom: 16px; }
.bud-scope { font-family: var(--mono); font-size: var(--fs-xs); color: var(--tx2); margin-bottom: 5px; }
.bud-bar { height: 3px; background: var(--bg3); border-radius: 2px; overflow: hidden; }
.bud-fill { height: 100%; background: var(--acc-dim); transition: width 0.4s var(--ease); }
.bud-nums { font-family: var(--mono); font-size: var(--fs-xs); color: var(--tx3); margin-top: 4px; }
</style>
