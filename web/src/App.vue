<script setup>
/** 三栏布局 · 白昼 v4：会话索引 | 观测记录（对话） | 仪表面板
 *  v2.0：标题栏工具钮（收起侧栏/设置/帮助/关于）+ 设置/关于/帮助弹窗
 *  移动端（≤900px，M3 PWA 单列）：顶栏 + 抽屉式 Side/Info，对话全宽 */
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useSessionStore } from './stores/session'
import { useSettingsStore } from './stores/settings'
import { apiGet, apiPost, apiDelete } from './api'
import SidePane from './components/SidePane.vue'
import ThreadPane from './components/ThreadPane.vue'
import InfoPane from './components/InfoPane.vue'
import SetupWizard from './components/SetupWizard.vue'
import CoachTour from './components/CoachTour.vue'
import PetWidget from './components/PetWidget.vue'
import Icon from './components/Icon.vue'
import logoMark from './assets/logo-mark.png'

const store = useSessionStore()
const settings = useSettingsStore()
const ready = ref(false)
const bootError = ref('')
const needToken = ref(false)
const tokenInput = ref('')
const isMobile = ref(false)
const drawer = ref('') // '' | 'side' | 'info'
// v2.8 桌宠视图：Electron 透明置顶小窗加载 ?view=pet，只渲染 PetWidget
const isPetView = new URLSearchParams(location.search).get('view') === 'pet'
// 仅 Electron 壳显示自绘标题栏（浏览器/PWA 无窗口控制钮，不加）
const isElectron = typeof window !== 'undefined' && !!window.kairos
let mq = null

// v2.0：UI 状态
const sideCollapsed = ref(localStorage.getItem('kairos_side_collapsed') === '1')
// v2.5：右栏默认不常驻（WorkBuddy 式——顶栏 ◍ 切换），运维面板按需看
const infoVisible = ref(localStorage.getItem('kairos_info_visible') === '1')
const showModal = ref('') // '' | 'settings' | 'about' | 'help' | 'setup'
const meta = ref(null)
const skills = ref([])
const agents = ref([])   // v2.6 Agent 中枢注册表
const needsSetup = ref(false)
// v3.3 能力库：MCP 服务器 + 长期记忆
const mcpServers = ref([])
const mcpStatus = ref({})
const mcpBusy = ref('')
const mcpForm = ref({ name: '', command: '', args: '', env: '' })
const mcpShowForm = ref(false)
const memories = ref([])
// v2.7 新手引导：首启自动播一次；帮助页可重看
const tourVisible = ref(false)
// v2.8 桌宠：召唤开关状态（仅 Electron 壳可用）
const petSummoned = ref(false)
// v2.9 开机自启（仅 Electron 壳可用）
const autostart = ref(false)
async function loadAutostart() {
  try { autostart.value = !!(await window.kairos?.autostart?.()) } catch { /* 非 Electron */ }
}
async function toggleAutostart() {
  try { autostart.value = !!(await window.kairos?.autostart?.(!autostart.value)) } catch { /* 忽略 */ }
}

function toggleSetupWizard() { needsSetup.value = true }
function startTour() {
  showModal.value = ''
  tourVisible.value = true
}
function onTourDone() {
  tourVisible.value = false
  localStorage.setItem('kairos_tour_done', '1')
}

const OVERLAY_NORMAL = { color: '#f6f8fb', symbolColor: '#1b2733' }
const OVERLAY_DIM = { color: '#dbe2ec', symbolColor: '#5b6b80' }

function toggleSide() {
  sideCollapsed.value = !sideCollapsed.value
  localStorage.setItem('kairos_side_collapsed', sideCollapsed.value ? '1' : '0')
}
function toggleInfo() {
  infoVisible.value = !infoVisible.value
  localStorage.setItem('kairos_info_visible', infoVisible.value ? '1' : '0')
}
function applyOverlay(dim) {
  window.kairos?.setTitleBarOverlay?.(dim ? OVERLAY_DIM : OVERLAY_NORMAL)
}
watch(showModal, (v) => { if (isElectron) applyOverlay(!!v) })

// v2.7 Agent 中枢：重新检测 + 复制安装命令
const copiedAgent = ref('')
async function loadHubAgents() {
  try { agents.value = await apiGet('/api/hub') } catch { agents.value = [] }
}
function copyInstall(a) {
  if (!a.install) return
  navigator.clipboard?.writeText(a.install)
  copiedAgent.value = a.name
  setTimeout(() => { if (copiedAgent.value === a.name) copiedAgent.value = '' }, 1600)
}

/* ── M3 手机连接（OpenDot 式设备配对）：开关 + 二维码 + 换 token ── */
const lan = ref(null)
const lanQr = ref('')
const lanBusy = ref(false)

async function loadLan() {
  try {
    lan.value = await apiGet('/api/lan/status')
    await renderLanQr()
  } catch { lan.value = null }
}

async function renderLanQr() {
  lanQr.value = ''
  if (!lan.value?.enabled || !lan.value?.token) return
  const url = (lan.value.urls?.[0] || '') + '?token=' + encodeURIComponent(lan.value.token)
  try {
    const QR = (await import('qrcode')).default
    lanQr.value = await QR.toDataURL(url, { width: 220, margin: 1, color: { dark: '#1b2733', light: '#ffffff' } })
  } catch { lanQr.value = '' }
}

async function toggleLan() {
  if (lanBusy.value || !lan.value) return
  lanBusy.value = true
  try {
    lan.value = await apiPost('/api/lan/toggle', { enabled: !lan.value.enabled })
    await renderLanQr()
  } catch (e) { lan.value = { ...(lan.value || {}), toggleError: String(e) } }
  finally { lanBusy.value = false }
}

async function rotateLan() {
  if (lanBusy.value) return
  lanBusy.value = true
  try {
    lan.value = await apiPost('/api/lan/rotate', {})
    await renderLanQr()
  } finally { lanBusy.value = false }
}

function copyLanLink() {
  if (!lan.value?.token) return
  const url = (lan.value.urls?.[0] || '') + '?token=' + encodeURIComponent(lan.value.token)
  navigator.clipboard?.writeText(url)
  copiedAgent.value = 'lan'
  setTimeout(() => { if (copiedAgent.value === 'lan') copiedAgent.value = '' }, 1600)
}

/* ── MCP 能力库（OpenDot 式管理）────────────────── */
async function loadMcp() {
  try {
    const d = await apiGet('/api/mcp/servers')
    mcpServers.value = d.servers || []
    mcpStatus.value = d.status || {}
  } catch { mcpServers.value = []; mcpStatus.value = {} }
}

function mcpStat(name) { return mcpStatus.value[name] || {} }

async function addMcp() {
  if (mcpBusy.value) return
  let args = [], env = {}
  try { args = mcpForm.value.args.trim() ? JSON.parse(mcpForm.value.args) : [] } catch { alert('参数必须是 JSON 数组，如 ["-y","@modelcontextprotocol/server-filesystem","C:/路径"]'); return }
  try { env = mcpForm.value.env.trim() ? JSON.parse(mcpForm.value.env) : {} } catch { alert('环境变量必须是 JSON 对象，如 {"APP_SECRET":"xxx"}'); return }
  mcpBusy.value = 'add'
  try {
    await apiPost('/api/mcp/servers', { name: mcpForm.value.name, command: mcpForm.value.command, args, env, enabled: true })
    mcpForm.value = { name: '', command: '', args: '', env: '' }
    mcpShowForm.value = false
    await loadMcp()
  } catch (e) { alert(String(e)) } finally { mcpBusy.value = '' }
}

async function toggleMcp(s) {
  if (mcpBusy.value) return
  mcpBusy.value = s.name
  try { await apiPost(`/api/mcp/servers/${encodeURIComponent(s.name)}/toggle`, {}); await loadMcp() }
  finally { mcpBusy.value = '' }
}

async function removeMcp(s) {
  if (mcpBusy.value || !confirm(`删除 MCP 服务器「${s.name}」？其桥接工具会立即摘除。`)) return
  mcpBusy.value = s.name
  try { await apiDelete(`/api/mcp/servers/${encodeURIComponent(s.name)}`); await loadMcp() }
  finally { mcpBusy.value = '' }
}

/* ── 长期记忆（可看、可忘）────────────────── */
async function loadMemories() {
  try { memories.value = await apiGet('/api/memory') } catch { memories.value = [] }
}

async function forgetMemory(m) {
  try { await apiDelete(`/api/memory/${m.id}`); await loadMemories() } catch { /* 忽略 */ }
}

/* ── 消息渠道（飞书遥控 / WorkBuddy 桥）────────── */
const chFeishu = ref({ configured: false, running: false })
const chForm = ref({ app_id: '', app_secret: '' })
const chBusy = ref('')
const chMsg = ref('')

async function loadChannels() {
  try {
    const d = await apiGet('/api/channels/status')
    chFeishu.value = d.feishu || { configured: false, running: false }
  } catch { chFeishu.value = { configured: false, running: false } }
}

async function saveFeishuCfg() {
  if (chBusy.value || !chForm.value.app_id.trim() || !chForm.value.app_secret.trim()) return
  chBusy.value = 'save'
  chMsg.value = ''
  try {
    const d = await apiPost('/api/channels/feishu/config', {
      app_id: chForm.value.app_id, app_secret: chForm.value.app_secret })
    chMsg.value = `已保存，通道状态：${d.started}。在飞书里给机器人发句话即可遥控（/pending 看待审批）。`
    chForm.value = { app_id: '', app_secret: '' }
    await loadChannels()
  } catch (e) { chMsg.value = String(e) } finally { chBusy.value = '' }
}

async function testFeishu() {
  if (chBusy.value) return
  chBusy.value = 'test'
  chMsg.value = ''
  try {
    const d = await apiPost('/api/channels/feishu/test', {})
    chMsg.value = d.ok ? '鉴权探活成功 ✓' : `探活失败：${d.msg}`
  } catch (e) { chMsg.value = String(e) } finally { chBusy.value = '' }
}

async function openModal(m) {
  showModal.value = m
  if (m === 'about' && !meta.value) {
    try { meta.value = await apiGet('/api/meta') } catch { meta.value = null }
  }
  if (m === 'settings') {
    try { skills.value = await apiGet('/api/skills') } catch { skills.value = [] }
    try { agents.value = await apiGet('/api/hub') } catch { agents.value = [] }
    loadLan()
    loadMcp()
    loadMemories()
    loadChannels()
    loadAutostart()
  }
}

async function onSetupDone() {
  needsSetup.value = false
  showModal.value = ''
  await boot()
  settings.load()
}

function onMedia(e) {
  isMobile.value = e.matches
  if (!e.matches) drawer.value = ''
}

async function boot() {
  ready.value = false
  bootError.value = ''
  needToken.value = false
  try {
    // v2.6：无 Key 时先进配置向导（CC Switch 式）
    try {
      const st = await apiGet('/api/setup/status')
      if (st.needs_setup) { needsSetup.value = true; return }
    } catch { /* sidecar 未起就按原流程走 */ }
    await store.loadSessions()
    ready.value = true
    if (!settings.loaded) settings.load()
    // v2.7：新用户首启播指向式引导（配置向导之后、界面就绪之后）
    if (!localStorage.getItem('kairos_tour_done')) {
      setTimeout(() => { if (ready.value && !needsSetup.value) tourVisible.value = true }, 600)
    }
  } catch (e) {
    const msg = String(e)
    if (msg.includes('401')) { needToken.value = true }
    else bootError.value = msg
  }
}

function saveToken() {
  localStorage.setItem('kairos_token', tokenInput.value.trim())
  boot()
}

onMounted(() => {
  mq = matchMedia('(max-width: 900px)')
  isMobile.value = mq.matches
  mq.addEventListener('change', onMedia)
  // M3 配对：扫码链接带 ?token=krs_... → 落 localStorage 后清掉（PWA scope 干净、不进历史）
  try {
    const u = new URL(location.href)
    const t = u.searchParams.get('token')
    if (t) {
      localStorage.setItem('kairos_token', t)
      u.searchParams.delete('token')
      history.replaceState(null, '', u.pathname + u.search + u.hash)
    }
  } catch { /* 非 http 环境忽略 */ }
})
onUnmounted(() => mq?.removeEventListener('change', onMedia))

// v2.8 桌宠召唤/收回（Electron 壳里才有 window.kairos.togglePet）
async function togglePet() {
  const on = await window.kairos?.togglePet?.()
  if (on !== undefined) petSummoned.value = on
}

if (!isPetView) boot()
</script>

<template>
  <!-- v2.8 桌宠视图：独立透明小窗，只渲染小灵体（不进主 UI） -->
  <PetWidget v-if="isPetView" />
  <div v-else class="shell" :class="{ 'electron': isElectron, 'mobile-tb': isElectron && isMobile }">
    <!-- 自绘标题栏（仅 Electron）：拖拽区 + 工具钮；右上角窗口控制钮走原生 overlay -->
    <header v-if="isElectron" class="titlebar">
      <template v-if="isMobile">
        <button class="mbtn tb-btn" aria-label="会话索引" @click="drawer = drawer === 'side' ? '' : 'side'"><Icon name="menu" :size="16" /></button>
        <div class="tb-mid">
          <img class="tb-logo" :src="logoMark" alt="">
          <span class="mword">Kairos</span>
          <span class="dot" style="margin-left:8px"></span>
        </div>
        <button class="mbtn tb-btn" aria-label="仪表面板" @click="drawer = drawer === 'info' ? '' : 'info'"><Icon name="gauge" :size="16" /></button>
      </template>
      <template v-else>
        <button id="tb-side" class="tb-btn" :title="sideCollapsed ? '展开侧栏' : '收起侧栏'" @click="toggleSide"><Icon name="menu" :size="16" /></button>
        <div class="tb-brand">
          <img class="tb-logo" :src="logoMark" alt="">
          <span class="mword">Kairos</span>
        </div>
        <div class="tb-drag"></div>
        <button id="tb-info" class="tb-btn wide-btn" :class="{ active: infoVisible }" title="仪表面板" @click="toggleInfo"><Icon name="gauge" :size="16" /></button>
        <button id="tb-settings" class="tb-btn wide-btn" @click="openModal('settings')"><Icon name="gear" :size="13" /> 设置</button>
        <button id="tb-help" class="tb-btn wide-btn" @click="openModal('help')"><Icon name="help" :size="13" /> 帮助</button>
        <button class="tb-btn wide-btn" @click="openModal('about')"><Icon name="info" :size="13" /> 关于</button>
      </template>
    </header>

    <!-- 浏览器/PWA 顶栏（桌面宽度）：同款工具钮 -->
    <header v-else-if="!isMobile" class="webbar">
      <button id="tb-side" class="tb-btn" :title="sideCollapsed ? '展开侧栏' : '收起侧栏'" @click="toggleSide"><Icon name="menu" :size="16" /></button>
      <div class="tb-brand">
        <img class="tb-logo" :src="logoMark" alt="">
        <span class="mword">Kairos</span>
      </div>
      <span style="flex:1"></span>
      <button id="tb-info" class="tb-btn wide-btn" :class="{ active: infoVisible }" @click="toggleInfo"><Icon name="gauge" :size="16" /></button>
      <button id="tb-settings" class="tb-btn wide-btn" @click="openModal('settings')"><Icon name="gear" :size="13" /> 设置</button>
      <button id="tb-help" class="tb-btn wide-btn" @click="openModal('help')"><Icon name="help" :size="13" /> 帮助</button>
      <button class="tb-btn wide-btn" @click="openModal('about')"><Icon name="info" :size="13" /> 关于</button>
    </header>

    <div v-if="needToken" class="boot">
      <img class="boot-logo" :src="logoMark" alt="Kairos">
      <div class="boot-mark">Kairos</div>
      <div class="boot-line">输入访问令牌（电脑端 data/lan_token.txt）</div>
      <input v-model="tokenInput" class="tok" placeholder="krs_..." @keydown.enter="saveToken">
      <button class="tokbtn" @click="saveToken">进入</button>
    </div>
    <div v-else-if="bootError" class="boot">
      <img class="boot-logo" :src="logoMark" alt="Kairos">
      <div class="boot-mark">Kairos</div>
      <div class="boot-line">信号中断 —— sidecar 未响应</div>
      <div class="boot-sub">{{ bootError }}</div>
      <div class="boot-sub">检查托盘图标，或查看 %APPDATA%/kairos-desktop/logs/</div>
    </div>
    <div v-else-if="!ready" class="boot">
      <img class="boot-logo" :src="logoMark" alt="Kairos">
      <div class="boot-mark">Kairos</div>
      <div class="boot-line"><span class="dot" style="display:inline-block;margin-right:10px"></span>正在校准仪器</div>
    </div>
    <div v-else class="layout" :class="{ mobile: isMobile }">
      <!-- 移动副栏：仅浏览器/PWA 显示（Electron 移动态按钮与铭牌都在自绘标题栏） -->
      <header v-if="isMobile && !isElectron" class="mbar">
        <button class="mbtn" aria-label="会话索引" @click="drawer = drawer === 'side' ? '' : 'side'"><Icon name="menu" :size="16" /></button>
        <div class="mbrand">
          <img class="mlogo" :src="logoMark" alt="">
          <span class="mword">Kairos</span>
          <span class="dot" style="margin-left:8px"></span>
        </div>
        <button class="mbtn" aria-label="设置" @click="openModal('settings')"><Icon name="gear" :size="16" /></button>
        <button class="mbtn" aria-label="仪表面板" @click="drawer = drawer === 'info' ? '' : 'info'"><Icon name="gauge" :size="16" /></button>
      </header>

      <div class="cols" :class="{
        'side-collapsed': sideCollapsed && !isMobile,
        'info-hidden': !infoVisible && !isMobile
      }">
        <SidePane :class="{ 'drawer-open': isMobile && drawer === 'side' }" />
        <ThreadPane />
        <InfoPane :drawer-open="isMobile && drawer === 'info'" />
      </div>
      <!-- v2.9：右栏贴边收起把手（常驻可见；桌面且非移动布局） -->
      <button v-if="!isMobile" class="info-handle" :class="{ open: infoVisible }"
              :title="infoVisible ? '收起仪表面板' : '展开仪表面板'"
              @click="toggleInfo">
        <span class="ih-arrow">{{ infoVisible ? '›' : '‹' }}</span>
      </button>
      <div v-if="isMobile && drawer" class="scrim" :class="isElectron ? 'scrim-tb' : ''" @click="drawer = ''"></div>
    </div>

    <!-- v2.6 配置向导（首启无 Key 自动弹；帮助页可重开） -->
    <SetupWizard v-if="needsSetup" @done="onSetupDone" />

    <!-- v2.7 新手指引（指向式，首启自动播；帮助页可重看） -->
    <CoachTour v-if="tourVisible && !needsSetup" @done="onTourDone" />

    <!-- 设置弹窗（v2.0：沙箱/多Agent 全局档；模型与权限档在输入框上方按会话切换） -->
    <div v-if="showModal === 'settings'" class="modal-scrim" @click.self="showModal = ''">
      <div class="modal">
        <div class="modal-head">
          <span class="modal-title">设置</span>
          <button class="modal-x" @click="showModal = ''"><Icon name="x" :size="15" /></button>
        </div>
        <div class="modal-body">
          <div class="set-group">
            <span class="plate">沙箱（run_python 执行隔离）</span>
            <div class="set-row">
              <span class="lab">沙箱档位<span class="sub">严格 = Windows Job Object：内存 512MB 上限 + 进程树回收；网络隔离暂不支持（诚实标注）</span></span>
              <div class="seg3">
                <button :class="{ active: settings.settings.sandbox === 'basic' }" @click="settings.save({ sandbox: 'basic' })">基础</button>
                <button :class="{ active: settings.settings.sandbox === 'strict' }" @click="settings.save({ sandbox: 'strict' })">严格</button>
              </div>
            </div>
          </div>
          <div class="set-group">
            <span class="plate">多 Agent（并行子代理）</span>
            <div class="set-row">
              <span class="lab">启用子代理<span class="sub">关闭后主对话不再派发 agent_run（工具表直接遮蔽）</span></span>
              <div class="seg3">
                <button :class="{ active: settings.settings.multi_agent }" @click="settings.save({ multi_agent: true })">开</button>
                <button :class="{ active: !settings.settings.multi_agent }" @click="settings.save({ multi_agent: false })">关</button>
              </div>
            </div>
            <div class="set-row">
              <span class="lab">并行子代理数<span class="sub">一次最多同时跑几个（1-6）</span></span>
              <select class="mini-select" :value="settings.settings.max_subagents"
                      @change="settings.save({ max_subagents: Number($event.target.value) })">
                <option v-for="n in [1, 2, 3, 4, 5, 6]" :key="n" :value="n">{{ n }}</option>
              </select>
            </div>
          </div>
          <div class="set-group">
            <span class="plate">手机连接（局域网遥控 · OpenDot 式配对）</span>
            <div class="set-row">
              <span class="lab">允许手机接入<span class="sub">手机与电脑连同一 WiFi；关闭时远端一律拒绝，token 每次轮换后旧手机立即失效</span></span>
              <div class="seg3">
                <button :class="{ active: lan?.enabled }" :disabled="lanBusy" @click="toggleLan">{{ lanBusy ? '…' : (lan?.enabled ? '开' : '关') }}</button>
              </div>
            </div>
            <template v-if="lan?.enabled">
              <div class="lan-box">
                <img v-if="lanQr" class="lan-qr" :src="lanQr" alt="扫码连接">
                <div class="lan-qr-holder" v-else>生成二维码…</div>
                <div class="lan-meta">
                  <div class="lan-url">{{ (lan.urls?.[0] || '') + '?token=' + lan.token }}</div>
                  <div class="quiet" style="margin-top:6px">手机浏览器扫左侧二维码（或复制链接打开），首次自动配对；也可「添加到主屏幕」当 App 用。手机和电脑必须在<b>同一个 WiFi</b>；Windows 首次会弹防火墙授权，选「允许」。</div>
                  <div style="display:flex;gap:8px;margin-top:10px">
                    <button class="btn" @click="copyLanLink">{{ copiedAgent === 'lan' ? '已复制 ✓' : '复制链接' }}</button>
                    <button class="btn" :disabled="lanBusy" @click="rotateLan">换配对码</button>
                  </div>
                </div>
              </div>
            </template>
            <div v-else class="quiet">默认关闭。开启后手机的浏览器访问本机 8630 端口即可遥控桌面 Agent：对话、审批、产物查看都在手机上同步可见。</div>
          </div>
          <div class="set-group" v-if="isElectron">
            <span class="plate">桌宠（Codex Pets 式状态伴侣）</span>
            <div class="set-row">
              <span class="lab">桌面小灵体<span class="sub">悬浮在所有窗口之上，实时反映 Agent 状态：干活中 / 等你审批 / 完成 / 出错；可拖拽、点击穿透不挡操作</span></span>
              <span style="display:flex;gap:8px">
                <button class="btn" :class="{ primary: !petSummoned }" @click="togglePet">{{ petSummoned ? '收回' : '召唤' }}</button>
              </span>
            </div>
            <div class="set-row">
              <span class="lab">开机自启<span class="sub">登录 Windows 后后台启动（托盘常驻不弹窗），随开随用</span></span>
              <div class="seg3">
                <button :class="{ active: autostart }" @click="toggleAutostart">{{ autostart ? '开' : '关' }}</button>
              </div>
            </div>
          </div>
          <div class="set-group">
            <span class="plate">Agent 中调中枢（派活给外部 Agent）</span>
            <div class="set-row">
              <span class="lab">重新运行配置向导<span class="sub">换 Key / 换服务商 / 配外挂 Agent 时用</span></span>
              <span style="display:flex;gap:8px">
                <button class="btn" @click="loadHubAgents">重新检测</button>
                <button class="btn primary" @click="showModal = ''; needsSetup = true">运行向导</button>
              </span>
            </div>
            <div v-for="a in agents" :key="a.name" class="agent-row">
              <div class="set-row" style="margin-bottom:0">
                <span class="lab">{{ a.label }}<span class="sub">{{ a.setup_hint || a.note }}</span></span>
                <span class="plate" :style="{ color: a.available ? 'var(--ok)' : 'var(--tx3)' }">
                  {{ a.kind === 'custom' ? '自定义' : (a.available ? '已检测' : '未安装') }}
                </span>
              </div>
              <div v-if="a.kind !== 'custom' && !a.available && a.install" class="agent-install">
                <code>{{ a.install }}</code>
                <button class="btn" @click="copyInstall(a)">{{ copiedAgent === a.name ? '已复制 ✓' : '复制安装命令' }}</button>
              </div>
            </div>
            <div class="quiet">五个都是真执行器：装好并登录授权后 Kairos 直接派活（各自用自己的账号计费，不用在 Kairos 里重复配 Key）。豆包不在这里——配置向导选「火山方舟」贴 Key，豆包模型直接进模型注册表当主力模型用。command 可在 config.local.json 的 agents 段自定义。</div>
          </div>
          <div class="set-group">
            <span class="plate">技能库（save_skill 固化的经验）</span>
            <div v-if="!skills.length" class="quiet">还没有技能。让 Kairos 做好一件事后它会自己 save_skill 固化。</div>
            <div v-for="s in skills" :key="s.name" class="set-row">
              <span class="lab">{{ s.name }}<span class="sub">{{ (s.content || '').slice(0, 80) }}…</span></span>
              <span class="plate">用过 {{ s.use_count }} 次</span>
            </div>
          </div>
          <div class="set-group">
            <span class="plate">MCP 服务器（能力库 · 外部工具接入）</span>
            <div v-for="s in mcpServers" :key="s.name" class="set-row">
              <span class="lab">{{ s.name }}<span class="sub">{{ s.command }} {{ (s.args || []).join(' ') }}</span></span>
              <span style="display:flex;gap:6px;align-items:center">
                <span class="plate" :style="{ color: mcpStat(s.name).connected ? 'var(--ok)' : 'var(--tx3)' }">
                  {{ mcpStat(s.name).connected ? `已连接 · ${mcpStat(s.name).tools?.length || 0} 工具` : (mcpStat(s.name).error ? '连接失败' : '未连接') }}
                </span>
                <button class="btn" :disabled="mcpBusy === s.name" @click="toggleMcp(s)">{{ s.enabled ? '停用' : '启用' }}</button>
                <button class="btn danger" :disabled="mcpBusy === s.name" @click="removeMcp(s)">删</button>
              </span>
            </div>
            <div v-if="!mcpServers.length" class="quiet">还没有 MCP 服务器。接入后其工具以 mcp_服务器名_工具名 进 Agent 工具表。</div>
            <div v-if="mcpShowForm" class="mcp-form">
              <input v-model="mcpForm.name" class="mcp-in" placeholder="名称，如 filesystem">
              <input v-model="mcpForm.command" class="mcp-in" placeholder="命令，如 npx">
              <input v-model="mcpForm.args" class="mcp-in" placeholder='参数 JSON 数组，如 ["-y","@modelcontextprotocol/server-filesystem","C:/路径"]'>
              <input v-model="mcpForm.env" class="mcp-in" placeholder='环境变量 JSON 对象（密钥），如 {"APP_SECRET":"xxx"}，可选'>
              <div style="display:flex;gap:8px;margin-top:8px">
                <button class="btn primary" :disabled="mcpBusy === 'add'" @click="addMcp">{{ mcpBusy === 'add' ? '连接中…' : '添加并连接' }}</button>
                <button class="btn" @click="mcpShowForm = false">取消</button>
              </div>
            </div>
            <div style="margin-top:8px">
              <button v-if="!mcpShowForm" class="btn" @click="mcpShowForm = true">+ 添加 MCP 服务器</button>
              <button class="btn" :disabled="mcpBusy !== ''" @click="loadMcp">刷新状态</button>
            </div>
            <div class="quiet">stdio 型 server 即填即用（npx/uvx 均可）；密钥只写进 mcp.json，界面不回显。连接失败看命令是否可执行、参数是否正确。</div>
          </div>
          <div class="set-group">
            <span class="plate">消息渠道（飞书遥控 · WorkBuddy 桥）</span>
            <div class="set-row">
              <span class="lab">飞书通道<span class="sub">WS 长连接免公网；配好后飞书私聊机器人即可遥控/审批（/pending /ok 命令 + 交互卡片）</span></span>
              <span class="plate" :style="{ color: chFeishu.running ? 'var(--ok)' : 'var(--tx3)' }">
                {{ chFeishu.configured ? (chFeishu.running ? '运行中' : '已配置·未运行') : '未配置' }}
              </span>
            </div>
            <div class="mcp-form">
              <input v-model="chForm.app_id" class="mcp-in" placeholder="飞书 app_id（cli_ 开头，开放平台建个应用就有了）">
              <input v-model="chForm.app_secret" class="mcp-in" type="password" placeholder="飞书 app_secret（保存后不回显）">
              <div style="display:flex;gap:8px;margin-top:8px">
                <button class="btn primary" :disabled="chBusy === 'save'" @click="saveFeishuCfg">{{ chBusy === 'save' ? '连接中…' : '保存并连接' }}</button>
                <button v-if="chFeishu.configured" class="btn" :disabled="chBusy === 'test'" @click="testFeishu">{{ chBusy === 'test' ? '探活中…' : '鉴权探活' }}</button>
              </div>
              <div v-if="chMsg" class="quiet">{{ chMsg }}</div>
            </div>
            <div class="quiet">WorkBuddy 桥已配进 WorkBuddy 的 mcp.json（kairos 服务器）：在 WorkBuddy 连接器里 Trust 后，直接说"用 kairos_chat 帮我…"即可遥控本机 Agent。</div>
          </div>
          <div class="set-group">
            <span class="plate">长期记忆（{{ memories.length }} 条 · 可看可忘）</span>
            <div v-if="!memories.length" class="quiet">还没有记忆。对话里说的偏好/背景/计划会按写入门控自动沉淀（只收用户级持久事实）。</div>
            <div v-for="m in memories.slice(0, 12)" :key="m.id" class="set-row">
              <span class="lab">{{ m.content }}<span class="sub">{{ m.kind }} · {{ (m.updated_at || '').slice(0, 10) }}</span></span>
              <button class="btn danger" @click="forgetMemory(m)">忘记</button>
            </div>
            <div class="quiet">「忘记」只删记忆本身，不动聊天历史。自动流程永不确认一条记忆——确认只能来自你的显式陈述。</div>
          </div>
          <div class="set-group">
            <span class="plate">模型注册表（config.local.json models）</span>
            <div v-for="m in settings.models" :key="m.name" class="set-row">
              <span class="lab">{{ m.label }}<span class="sub">{{ m.name }} · {{ m.tier }} 档</span></span>
              <span class="plate">{{ m.name === settings.defaultModel ? '默认' : '' }}</span>
            </div>
            <div class="quiet">会话级模型切换在输入框上方下拉；换 Key/加模型跑一遍配置向导（设置 → Agent 中调中枢 → 运行向导）。</div>
          </div>
        </div>
      </div>
    </div>

    <!-- 关于 -->
    <div v-if="showModal === 'about'" class="modal-scrim" @click.self="showModal = ''">
      <div class="modal">
        <div class="modal-head">
          <span class="modal-title">关于 Kairos</span>
          <button class="modal-x" @click="showModal = ''"><Icon name="x" :size="15" /></button>
        </div>
        <div class="modal-body" style="text-align:center;padding:34px 20px">
          <img :src="logoMark" alt="" style="width:56px;height:56px;border-radius:12px">
          <div class="boot-mark" style="margin:14px 0 4px">Kairos</div>
          <div class="art-meta">
            {{ meta ? `v${meta.version} · schema ${meta.schema}` : '…' }}
          </div>
          <div class="quiet" style="margin-top:10px">
            恰当的时刻（καιρός）——主动式个人 Agent：<br>
            桌面端 + 手机遥控 + 飞书通道 + 深度研究引擎。
          </div>
          <div v-if="meta" class="art-meta" style="margin-top:12px">
            沙箱 {{ meta.sandbox }} · 多 Agent {{ meta.multi_agent ? '开' : '关' }}
          </div>
        </div>
      </div>
    </div>

    <!-- 帮助 -->
    <div v-if="showModal === 'help'" class="modal-scrim" @click.self="showModal = ''">
      <div class="modal">
        <div class="modal-head">
          <span class="modal-title">帮助</span>
          <button class="modal-x" @click="showModal = ''"><Icon name="x" :size="15" /></button>
        </div>
        <div class="modal-body">
          <div class="set-row" style="margin-bottom:6px">
            <span class="lab">不熟悉界面？<span class="sub">6 步指向式新手引导，带你过一遍主要功能</span></span>
            <button class="btn primary" @click="startTour">重看新手引导</button>
          </div>
          <div class="set-group">
            <span class="plate">三种模式（输入框上方切换）</span>
            <div class="set-row"><span class="lab"><b>对话</b> —— 常规干活，全工具可用</span></div>
            <div class="set-row"><span class="lab"><b>规划</b> —— 只出计划不动手；产出后点「转为计划送审」，右栏「动态」页批准后自动按计划开工</span></div>
            <div class="set-row"><span class="lab"><b>目标</b> —— 绑定一个长期目标，每轮推进一步并记录进展</span></div>
          </div>
          <div class="set-group">
            <span class="plate">审批权限档</span>
            <div class="set-row"><span class="lab"><b>审批</b> —— 对外动作逐个人工确认（默认）</span></div>
            <div class="set-row"><span class="lab"><b>半自动</b> —— 本会话内同类动作批准一次后放行</span></div>
            <div class="set-row"><span class="lab"><b>自动</b> —— 非高危动作自动放行；高危（发送/删除类）永远强制人工</span></div>
          </div>
          <div class="set-group">
            <span class="plate">常用指令示例</span>
            <div class="set-row"><span class="lab">「盯一下 XX 公司校招页，有更新就告诉我」—— 登记盯页源</span></div>
            <div class="set-row"><span class="lab">「调研一下 XX 公司 2027 届算法岗待遇，多源核验」—— 自动拆子代理/deep_research</span></div>
            <div class="set-row"><span class="lab">「把这周投递台账导出成表格」—— 产物落工作区，右栏「产物」页可预览/复制/打开</span></div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.shell { height: 100%; display: flex; flex-direction: column; }

/* 自绘标题栏（仅 Electron）：拖拽 + 工具钮；桌面右侧留原生 overlay 控制区 */
.titlebar {
  height: 36px; flex-shrink: 0;
  display: flex; align-items: center; gap: 2px;
  padding: 0 12px 0 8px;
  background: var(--bg1);
  border-bottom: 1px solid var(--line);
  -webkit-app-region: drag;
  user-select: none;
}
.titlebar:not(:has(.tb-mid)) { padding-right: 148px; } /* 桌面：给 Windows overlay 三钮留位 */
.tb-btn {
  -webkit-app-region: no-drag;
  background: none; border: none; cursor: pointer;
  color: var(--tx2); font-size: 14px; line-height: 1;
  padding: 5px 9px; border-radius: 6px;
  display: inline-flex; align-items: center; gap: 5px;
  transition: background 0.18s var(--ease), color 0.18s var(--ease);
}
.tb-btn:hover { background: var(--bg2); color: var(--acc); }
.tb-btn.active { background: var(--bg2); color: var(--acc); }
.tb-btn.wide-btn { font-size: var(--fs-xs); font-family: var(--mono); letter-spacing: 0.08em; }
.tb-brand { display: flex; align-items: center; }
.tb-mid { flex: 1; display: flex; align-items: center; justify-content: center; }
.tb-drag { flex: 1; align-self: stretch; }
.tb-logo {
  width: 18px; height: 18px; border-radius: 4px;
  margin-right: 8px; display: block;
}

/* 浏览器/PWA 桌面顶栏 */
.webbar {
  height: 42px; flex-shrink: 0;
  display: flex; align-items: center; gap: 2px;
  padding: 0 14px;
  background: var(--bg1);
  border-bottom: 1px solid var(--line);
}
.webbar .tb-btn { -webkit-app-region: auto; }

.layout { flex: 1; min-height: 0; display: flex; flex-direction: column; }
/* v2.5 轮廓感：面板浮层化——留白 + 圆角 + 细描边，收起用滑出动画而非硬消失 */
.cols {
  flex: 1; min-height: 0; display: flex;
  padding: 8px; gap: 8px;
  overflow: hidden;
}
.cols > :deep(*) { border-radius: 14px; overflow: hidden; box-shadow: 0 1px 10px rgb(27 39 51 / 0.05), 0 0 0 1px var(--line); }
.cols > .side { transition: margin-left 0.26s var(--ease); border: none; }
.cols > :deep(.info) { transition: margin-right 0.26s var(--ease); border: none; }
.cols.side-collapsed > .side { margin-left: -264px; }
/* InfoPane 是多根组件（根上还有预览弹窗），父 scope 属性传不到 .info 上，
   必须 :deep() 否则收起规则永远匹配不到（v2.5 起回归的隐藏 bug） */
.cols.info-hidden > :deep(.info) { margin-right: -288px; }

/* v2.9 右栏贴边把手：面板展开时贴面板左缘，收起时贴窗口右缘 */
.info-handle {
  position: fixed; z-index: 25;
  top: 50%; right: 8px; transform: translateY(-50%);
  width: 20px; height: 60px;
  background: var(--bg1); color: var(--tx3);
  border: 1px solid var(--line); border-right: none;
  border-radius: 10px 0 0 10px;
  box-shadow: -2px 0 8px rgb(27 39 51 / 0.08);
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: right 0.26s var(--ease), color 0.18s var(--ease), background 0.18s var(--ease);
}
.info-handle:hover { color: var(--acc); background: var(--bg2); }
.info-handle.open { right: 284px; } /* 吸附在仪表面板左缘：跨缝 12px+8px，不再骑在对话面板上 */
.ih-arrow { font-size: 15px; line-height: 1; user-select: none; }

.boot {
  flex: 1; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 6px;
}
.boot-logo {
  width: 44px; height: 44px; border-radius: 10px;
  margin-bottom: 12px;
  display: block;
}
.boot-mark {
  font-family: var(--serif);
  font-style: italic;
  font-weight: 560;
  font-size: var(--fs-xl);
  letter-spacing: 0.01em;
  color: var(--tx);
  margin-bottom: 14px;
}
.boot-line { font-size: var(--fs-sm); color: var(--tx2); display: flex; align-items: center; }
.boot-sub { font-size: var(--fs-xs); color: var(--tx3); font-family: var(--mono); }
.tok {
  margin-top: 10px;
  background: var(--bg2); border: 1px solid var(--line-hi); border-radius: 8px;
  color: var(--tx); font-family: var(--mono); font-size: var(--fs-sm);
  padding: 8px 12px; width: 280px; outline: none;
}
.tok:focus { border-color: var(--acc-dim); }
.tokbtn {
  margin-top: 10px;
  background: none; border: 1px solid var(--acc-dim); border-radius: 8px;
  color: var(--acc); font-size: var(--fs-sm); padding: 8px 24px; cursor: pointer;
}
.tokbtn:hover { background: var(--bg2); }
.quiet { font-size: var(--fs-xs); color: var(--tx3); line-height: 1.9; }

/* v2.7 设置-Agent 中枢：安装命令行 */
.agent-row { margin-bottom: 2px; }

/* M3 手机连接：二维码 + 链接卡 */
.lan-box {
  display: flex; gap: 16px; align-items: flex-start;
  background: var(--bg2); border-radius: 12px;
  padding: 14px; margin-top: 8px;
}
.lan-qr {
  width: 128px; height: 128px; flex-shrink: 0;
  border-radius: 8px; border: 1px solid var(--line-hi);
  background: #fff;
}
.lan-qr-holder {
  width: 128px; height: 128px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  border-radius: 8px; border: 1px dashed var(--line-hi);
  color: var(--tx3); font-size: var(--fs-xs);
}
.lan-meta { flex: 1; min-width: 0; }

/* MCP 能力库表单 */
.mcp-form { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.mcp-in {
  background: var(--bg2); border: 1px solid var(--line-hi); border-radius: 8px;
  color: var(--tx); font-size: var(--fs-xs); font-family: var(--mono);
  padding: 7px 10px; outline: none; width: 100%; box-sizing: border-box;
}
.mcp-in:focus { border-color: var(--acc-dim); }
.lan-url {
  font-family: var(--mono); font-size: var(--fs-xs);
  background: var(--bg1); border: 1px solid var(--line);
  border-radius: 6px; padding: 6px 9px;
  white-space: nowrap; overflow-x: auto; color: var(--tx2);
}
.agent-install {
  display: flex; align-items: center; gap: 8px;
  padding: 2px 0 8px 0;
}
.agent-install code {
  flex: 1; font-family: var(--mono); font-size: var(--fs-xs);
  background: var(--bg2); border-radius: 6px; padding: 5px 9px;
  white-space: nowrap; overflow-x: auto; color: var(--tx2);
}

/* 移动顶栏（仅 mobile 类生效） */
.mobile .mbar {
  height: 44px; flex-shrink: 0;
  display: flex; align-items: center; gap: 4px;
  padding: 0 10px;
  background: var(--bg1);
  border-bottom: 1px solid var(--line);
}
.mbrand { flex: 1; display: flex; align-items: center; }
.mlogo {
  width: 20px; height: 20px; border-radius: 5px;
  margin-right: 7px; display: block;
}
.mword {
  font-family: var(--serif); font-style: italic; font-weight: 560;
  font-size: var(--fs-base); color: var(--tx);
}
.mbtn {
  background: none; border: none; cursor: pointer;
  color: var(--tx2); font-size: 17px; line-height: 1;
  padding: 8px 12px; border-radius: 6px;
  display: inline-flex; align-items: center;
  transition: background 0.18s var(--ease), color 0.18s var(--ease);
}
.mbtn:hover, .mbtn:active { background: var(--bg2); color: var(--acc); }
.scrim {
  position: fixed; inset: 44px 0 0 0; z-index: 30;
  background: rgb(27 39 51 / 0.35);
}
.scrim-tb { inset: 36px 0 0 0; } /* Electron 移动态：顶上是 36px 自绘标题栏 */
</style>

<style>
/* 移动抽屉（非 scoped：需要穿透子组件根元素；仅窄屏生效，桌面零影响） */
@media (max-width: 900px) {
  .layout.mobile .side,
  .layout.mobile .info {
    position: fixed;
    top: 44px; bottom: 0;
    width: min(300px, 86vw);
    z-index: 40;
    box-shadow: 6px 0 24px rgb(27 39 51 / 0.25);
    transition: transform 0.25s var(--ease);
  }
  /* Electron 移动态：顶上是 36px 自绘标题栏而非 44px mbar */
  .shell.mobile-tb .layout.mobile .side,
  .shell.mobile-tb .layout.mobile .info { top: 36px; }
  .layout.mobile .side { left: 0; transform: translateX(-105%); }
  .layout.mobile .info { right: 0; transform: translateX(105%); }
  .layout.mobile .side.drawer-open,
  .layout.mobile .info.drawer-open { transform: none; }
  .layout.mobile .thread .scroll { padding: 20px 18px 16px; }
  .layout.mobile .thread .composer { padding: 12px 18px 14px; }
}
</style>
