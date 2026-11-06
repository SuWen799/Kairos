<script setup>
/** v2.6 配置向导（CC Switch 式）：选服务商 → 贴 Key 自动发现模型 → 加载进系统
 *  → 第 4 步外挂 Agent 接入（可选，可跳过）。
 *  首次启动（无 Key）自动进入；帮助页可重新运行。 */
import { computed, ref } from 'vue'
import { apiGet, apiPost } from '../api'

const emit = defineEmits(['done'])

const step = ref(1)
const presets = ref({})
const preset = ref('deepseek')
const baseUrl = ref('')
const apiKey = ref('')
const discovered = ref([])      // [model_id]
const selected = ref({})        // {model_id: bool}
const defaultModel = ref('')
const busy = ref(false)
const error = ref('')
const hubAgents = ref([])       // 第 4 步：外挂 Agent 注册表
const copied = ref('')          // 刚复制了安装命令的 agent name
const manualModel = ref('')     // 手动补模型 ID（方舟 ep-xxx / doubao-* 等）

async function loadHub() {
  try { hubAgents.value = await apiGet('/api/hub') } catch { hubAgents.value = [] }
}

const prettyName = (id) => {
  const map = { 'deepseek-flash': 'DeepSeek Flash', 'deepseek-v4-pro': 'DeepSeek Pro' }
  if (map[id]) return map[id]
  if (/^doubao/i.test(id)) return '豆包 ' + id.replace(/^doubao[-_]?/i, '')
  return id
}

async function init() {
  try {
    const s = await apiGet('/api/setup/status')
    presets.value = s.presets || {}
    if (s.base_url && !s.needs_setup) baseUrl.value = s.base_url
  } catch { presets.value = { deepseek: { label: 'DeepSeek 官方', base_url: 'https://api.deepseek.com/v1' } } }
}
init()

const presetList = computed(() => Object.entries(presets.value).map(([k, v]) => ({ k, ...v })))

function pick(k) {
  preset.value = k
  baseUrl.value = presets.value[k]?.base_url || ''
  step.value = 2
  error.value = ''
}

async function discover() {
  if (!apiKey.value.trim()) { error.value = '先把 API Key 粘进来'; return }
  busy.value = true
  error.value = ''
  try {
    const r = await apiPost('/api/setup/discover', {
      preset: preset.value,
      base_url: preset.value === 'custom' ? baseUrl.value.trim() : null,
      api_key: apiKey.value.trim()
    })
    discovered.value = r.models || []
    selected.value = Object.fromEntries(r.models.map((m) => [m, true]))
    defaultModel.value = r.models[0] || ''
    if (!discovered.value.length) {
      // /models 拉不到（如方舟走 ep-xxx）：进第 3 步手动补模型 ID
      error.value = '该 Key 名下没自动发现模型——第 3 步可手动填模型 ID'
    }
    step.value = 3
  } catch (e) {
    error.value = String(e).replace(/^Error:?\s*/, '')
  } finally {
    busy.value = false
  }
}

async function finish() {
  const models = discovered.value.filter((m) => selected.value[m]).map((m) => ({
    name: prettyName(m), model: m,
    tier: /pro|reasoner|r\d/i.test(m) ? 'reasoning' : 'default'
  }))
  if (!models.length) { error.value = '至少勾一个模型'; return }
  busy.value = true
  error.value = ''
  try {
    await apiPost('/api/setup/finish', {
      preset: preset.value,
      base_url: preset.value === 'custom' ? baseUrl.value.trim() : null,
      api_key: apiKey.value.trim(),
      models, default_model: prettyName(defaultModel.value)
    })
    loadHub()
    step.value = 4
  } catch (e) {
    error.value = String(e).replace(/^Error:?\s*/, '')
  } finally {
    busy.value = false
  }
}

function copyInstall(a) {
  const cmd = a.kind === 'cli' ? (a.install || '') : ''
  if (!cmd) return
  navigator.clipboard?.writeText(cmd)
  copied.value = a.name
  setTimeout(() => { if (copied.value === a.name) copied.value = '' }, 1600)
}

/* 手动补模型 ID：方舟等 /models 拉不全的服务商用 */
function addManualModel() {
  const id = manualModel.value.trim()
  if (!id || discovered.value.includes(id)) return
  discovered.value.push(id)
  selected.value[id] = true
  if (!defaultModel.value) defaultModel.value = id
  manualModel.value = ''
}
</script>

<template>
  <div class="wiz">
    <div class="wiz-card">
      <img class="wiz-logo" src="../assets/logo-mark.png" alt="">
      <div class="wiz-title">Kairos · 初始配置</div>
      <div class="wiz-sub">只贴一个 API Key，模型自动发现——其它都不用管。</div>

      <!-- Step 1：选服务商 -->
      <template v-if="step === 1">
        <div class="wiz-label">第一步 · 选你的模型服务商</div>
        <button v-for="p in presetList" :key="p.k" class="prov" @click="pick(p.k)">
          <span class="prov-name">{{ p.label }}</span>
          <span class="prov-url">{{ p.base_url || '填 base_url' }}</span>
          <span class="prov-go">→</span>
        </button>
      </template>

      <!-- Step 2：贴 Key -->
      <template v-else-if="step === 2">
        <div class="wiz-label">第二步 · 粘贴 {{ presets[preset]?.label || '服务商' }} 的 API Key</div>
        <input v-model="apiKey" class="wiz-input" type="password" placeholder="sk-..."
               @keydown.enter="discover">
        <input v-if="preset === 'custom'" v-model="baseUrl" class="wiz-input"
               placeholder="base_url（OpenAI 兼容，如 https://xx.com/v1）">
        <div class="wiz-hint">Key 只存进本机系统凭据管理器，不会上传到任何地方。</div>
        <div class="wiz-btns">
          <button class="btn" @click="step = 1">上一步</button>
          <button class="btn primary" :disabled="busy" @click="discover">
            {{ busy ? '检测中…' : '检测并拉取模型' }}
          </button>
        </div>
      </template>

      <!-- Step 3：选模型 -->
      <template v-else-if="step === 3">
        <div class="wiz-label">第三步 · 勾要启用的模型（自动发现 {{ discovered.length }} 个）</div>
        <div class="mlist">
          <label v-for="m in discovered" :key="m" class="mrow">
            <input v-model="selected[m]" type="checkbox">
            <span class="mname">{{ prettyName(m) }}</span>
            <span class="mid">{{ m }}</span>
            <input v-model="defaultModel" type="radio" :value="m" title="设为默认">
          </label>
          <div v-if="!discovered.length" class="mrow mrow-empty">暂无自动发现的模型——用下方输入框手动加</div>
        </div>
        <div class="manual-add">
          <input v-model="manualModel" class="wiz-input" style="margin-bottom:0"
                 placeholder="手动加模型 ID（火山方舟 ep-xxx / doubao-seed-* 填这里）"
                 @keydown.enter="addManualModel">
          <button class="btn" @click="addManualModel">添加</button>
        </div>
        <div class="wiz-hint">末列圆点选默认模型；之后可在输入框上方随时切换。</div>
        <div class="wiz-btns">
          <button class="btn" @click="step = 2">上一步</button>
          <button class="btn primary" :disabled="busy" @click="finish">
            {{ busy ? '写入中…' : '下一步' }}
          </button>
        </div>
      </template>

      <!-- Step 4：外挂 Agent 接入（可选，可跳过） -->
      <template v-else-if="step === 4">
        <div class="wiz-label">第四步 · 外挂 Agent（可选，以后在设置里随时配）</div>
        <div class="alist">
          <div v-for="a in hubAgents" :key="a.name" class="arow">
            <div class="arow-top">
              <span class="aname">{{ a.label }}</span>
              <span class="abadge" :class="a.kind === 'cli' ? (a.available ? 'ok' : 'no') : 'gui'">
                {{ a.kind === 'cli' ? (a.available ? '已检测' : '未安装') : (a.kind === 'manual' ? '复制模式' : '自定义') }}
              </span>
            </div>
            <div class="ahint">{{ a.setup_hint || a.note }}</div>
            <div v-if="a.kind === 'cli' && !a.available && a.install" class="ainstall">
              <code>{{ a.install }}</code>
              <button class="btn mini" @click="copyInstall(a)">{{ copied === a.name ? '已复制 ✓' : '复制' }}</button>
            </div>
          </div>
        </div>
        <div class="wiz-hint">这五个都是真执行器：装好并登录授权后，Kairos 直接把任务派过去跑，结果带回来。豆包不用在这里配——回第二步选「火山方舟」贴 Key，豆包模型直接进模型注册表。不配也完全不影响 Kairos 本体干活。</div>
        <div class="wiz-btns">
          <button class="btn" @click="$emit('done')">跳过，以后再说</button>
          <button class="btn primary" @click="$emit('done')">完成</button>
        </div>
      </template>

      <div v-if="error" class="wiz-err">{{ error }}</div>
    </div>
  </div>
</template>

<style scoped>
.wiz {
  position: fixed; inset: 0; z-index: 200;
  background: var(--bg0);
  display: flex; align-items: center; justify-content: center;
}
.wiz-card {
  width: min(520px, 92vw);
  background: var(--bg1); border-radius: 18px;
  box-shadow: 0 1px 10px rgb(27 39 51 / 0.05), 0 0 0 1px var(--line);
  padding: 36px 36px 28px;
  display: flex; flex-direction: column; align-items: center;
}
.wiz-logo { width: 48px; height: 48px; border-radius: 12px; margin-bottom: 14px; }
.wiz-title { font-family: var(--serif); font-style: italic; font-weight: 580; font-size: 1.4rem; }
.wiz-sub { font-size: var(--fs-sm); color: var(--tx2); margin: 6px 0 26px; }
.wiz-label { align-self: flex-start; font-size: var(--fs-sm); color: var(--tx2); margin-bottom: 12px; }
.prov {
  width: 100%; display: flex; align-items: center; gap: 12px;
  background: var(--bg0); border: 1px solid var(--line-hi); border-radius: 12px;
  padding: 13px 16px; margin-bottom: 8px; cursor: pointer; text-align: left;
  transition: border-color 0.15s var(--ease), box-shadow 0.15s var(--ease);
}
.prov:hover { border-color: var(--acc-dim); box-shadow: 0 2px 12px rgb(62 123 250 / 0.10); }
.prov-name { font-size: var(--fs-base); color: var(--tx); font-weight: 600; }
.prov-url { flex: 1; font-family: var(--mono); font-size: var(--fs-xs); color: var(--tx3); }
.prov-go { color: var(--acc); }
.wiz-input {
  width: 100%;
  background: var(--bg0); border: 1px solid var(--line-hi); border-radius: 10px;
  color: var(--tx); font-family: var(--mono); font-size: var(--fs-sm);
  padding: 11px 14px; outline: none; margin-bottom: 8px;
}
.wiz-input:focus { border-color: var(--acc-dim); }
.wiz-hint { font-size: var(--fs-xs); color: var(--tx3); margin: 6px 0 0; align-self: flex-start; }
.wiz-btns { display: flex; gap: 10px; margin-top: 20px; align-self: flex-end; }
.mlist {
  width: 100%; max-height: 260px; overflow-y: auto;
  border: 1px solid var(--line-hi); border-radius: 12px; background: var(--bg0);
}
.mrow {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px; border-bottom: 1px solid var(--line); cursor: pointer;
}
.mrow:last-child { border-bottom: none; }
.mrow-empty { color: var(--tx3); font-size: var(--fs-xs); cursor: default; }
.manual-add { display: flex; gap: 8px; width: 100%; margin-top: 8px; }
.manual-add .wiz-input { flex: 1; font-size: var(--fs-xs); }
.mname { font-size: var(--fs-sm); color: var(--tx); }
.mid { flex: 1; font-family: var(--mono); font-size: var(--fs-xs); color: var(--tx3); }
.wiz-err {
  margin-top: 14px; font-size: var(--fs-xs); color: var(--err);
  background: #fdf1f1; border: 1px solid #f2c9c9; border-radius: 8px; padding: 8px 12px;
}
/* 第 4 步：外挂 Agent */
.alist {
  width: 100%; max-height: 300px; overflow-y: auto;
  border: 1px solid var(--line-hi); border-radius: 12px; background: var(--bg0);
}
.arow { padding: 11px 14px; border-bottom: 1px solid var(--line); }
.arow:last-child { border-bottom: none; }
.arow-top { display: flex; align-items: center; gap: 10px; }
.aname { font-size: var(--fs-sm); color: var(--tx); font-weight: 600; }
.abadge {
  margin-left: auto; font-size: var(--fs-xs); font-family: var(--mono);
  padding: 2px 8px; border-radius: 999px;
}
.abadge.ok { color: var(--ok); background: #e8f5ec; }
.abadge.no { color: var(--tx3); background: var(--bg2); }
.abadge.gui { color: var(--acc); background: #e9effe; }
.ahint { font-size: var(--fs-xs); color: var(--tx3); line-height: 1.7; margin-top: 4px; }
.ainstall {
  display: flex; align-items: center; gap: 8px; margin-top: 6px;
}
.ainstall code {
  flex: 1; font-family: var(--mono); font-size: var(--fs-xs);
  background: var(--bg2); border-radius: 6px; padding: 5px 9px;
  white-space: nowrap; overflow-x: auto;
}
.btn.mini { padding: 4px 10px; font-size: var(--fs-xs); flex-shrink: 0; }
</style>
