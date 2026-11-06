<script setup>
/** 第一栏 · 会话索引：时间轴自动分组（今天/近 7 天/更早）+ 轻量工作区
 *  （v2.9：默认全部+时间线零管理；想归档时悬浮会话行一键打工作区标签，可按工作区过滤） */
import { computed, ref } from 'vue'
import { useSessionStore } from '../stores/session'
import { apiPatch } from '../api'
import Icon from './Icon.vue'

const store = useSessionStore()
const collapsed = ref({})      // {group: bool}
const wsFilter = ref('')       // '' = 全部
const assignFor = ref(null)    // 正在打工作区标签的会话 id（弹小面板）
const wsDraft = ref('')

const workspaces = computed(() => {
  const set = new Set()
  for (const s of store.sessions) {
    if (s.project && s.project !== 'default') set.add(s.project)
  }
  return [...set]
})

function bucketOf(ts) {
  if (!ts) return 'earlier'
  const d = new Date(ts)
  if (isNaN(d)) return 'earlier'
  const now = new Date()
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const t = d.getTime()
  if (t >= startToday) return 'today'
  if (t >= startToday - 6 * 86400000) return 'week'
  return 'earlier'
}

const GROUPS = [
  { key: 'today', label: '今天' },
  { key: 'week', label: '近 7 天' },
  { key: 'earlier', label: '更早' },
]

const groups = computed(() => {
  const map = { today: [], week: [], earlier: [] }
  for (const s of store.sessions) {
    if (wsFilter.value === '') {
      map[bucketOf(s.updated_at)].push(s)
    } else if ((s.project || 'default') === wsFilter.value) {
      map[bucketOf(s.updated_at)].push(s)
    }
  }
  return GROUPS.filter((g) => map[g.key].length).map((g) => [g.label, map[g.key]])
})

function toggleGroup(p) { collapsed.value = { ...collapsed.value, [p]: !collapsed.value[p] } }

async function assignWs(sid, name) {
  const v = (name || '').trim()
  assignFor.value = null
  wsDraft.value = ''
  try {
    await apiPatch(`/api/sessions/${sid}`, { project: v || null })
    await store.loadSessions()
  } catch { /* 静默：下次操作再试 */ }
}

function fmt(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  if (isNaN(d)) return ''
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return sameDay ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`
}
</script>

<template>
  <aside class="side">
    <div class="masthead">
      <div class="wordmark">Kairos</div>
      <div class="plate">personal agent</div>
    </div>

    <button class="new" @click="store.newSession()">
      <span class="plus">＋</span> 新对话
      <kbd>⌘N</kbd>
    </button>

    <!-- 工作区过滤（可选）：默认全部=纯时间线；悬浮会话行可打标签 -->
    <div class="ws-row">
      <button class="ws-chip" :class="{ active: wsFilter === '' }" @click="wsFilter = ''">全部</button>
      <button v-for="w in workspaces" :key="w" class="ws-chip"
              :class="{ active: wsFilter === w }" @click="wsFilter = w">{{ w }}</button>
    </div>

    <div class="rule"><span class="plate">timeline</span></div>

    <nav class="list">
      <div v-for="[label, sessions] in groups" :key="label" class="group">
        <button class="ghead" @click="toggleGroup(label)">
          <span class="g-arrow" :class="{ fold: collapsed[label] }">▾</span>
          <span class="g-name">{{ label }}</span>
          <span class="g-count">{{ sessions.length }}</span>
        </button>
        <template v-if="!collapsed[label]">
          <div v-for="s in sessions" :key="s.id" class="row-wrap">
            <button class="row" :class="{ active: s.id === store.currentId }"
                    @click="store.openSession(s.id)">
              <span class="indicator"></span>
              <span class="body">
                <span class="title">{{ s.title || '未命名' }}</span>
                <span class="time">
                  {{ fmt(s.updated_at) }}<template v-if="s.project && s.project !== 'default'"> · {{ s.project }}</template>
                </span>
              </span>
            </button>
            <button v-if="assignFor !== s.id" class="ws-tag" title="归档到工作区"
                    @click.stop="assignFor = s.id; wsDraft = s.project !== 'default' ? s.project : ''">
              <Icon name="folder" :size="12" />
            </button>
            <div v-else class="ws-pop" @click.stop>
              <input v-model="wsDraft" class="ws-input" placeholder="工作区名（新建或已有）"
                     @keydown.enter="assignWs(s.id, wsDraft)"
                     @keydown.esc="assignFor = null">
              <div class="ws-pop-btns">
                <button class="btn" @click="assignWs(s.id, '')">默认</button>
                <button class="btn" @click="assignFor = null">取消</button>
                <button class="btn primary" @click="assignWs(s.id, wsDraft)">保存</button>
              </div>
            </div>
          </div>
        </template>
      </div>
      <div v-if="!store.sessions.length" class="empty">
        还没有记录。<br>第一件事，从右边开始。
      </div>
    </nav>
  </aside>
</template>

<style scoped>
.side {
  width: 248px; flex-shrink: 0;
  background: var(--bg1);
  display: flex; flex-direction: column;
  padding: 22px 0 0;
}
.masthead { padding: 0 22px 20px; }
.wordmark {
  font-family: var(--serif);
  font-style: italic;
  font-weight: 580;
  font-size: var(--fs-lg);
  color: var(--tx);
  line-height: 1.2;
}
.masthead .plate { margin-top: 3px; }

.new {
  margin: 0 14px 18px;
  display: flex; align-items: center; gap: 8px;
  background: none; border: none;
  border-top: 1px solid var(--line); border-bottom: 1px solid var(--line);
  color: var(--acc);
  font-size: var(--fs-sm);
  padding: 10px 8px;
  cursor: pointer;
  text-align: left;
  transition: background 0.18s var(--ease), color 0.18s var(--ease);
}
.new:hover { background: var(--bg2); }
.new .plus { font-size: var(--fs-base); }
.new kbd {
  margin-left: auto;
  font-family: var(--mono); font-size: var(--fs-xs);
  color: var(--tx3);
}

.rule { padding: 0 22px 8px; border-bottom: 1px solid var(--line); }

.list { flex: 1; overflow-y: auto; padding: 6px 0 16px; }
.ghead {
  width: 100%; display: flex; align-items: center; gap: 6px;
  background: none; border: none; cursor: pointer;
  padding: 10px 16px 6px; text-align: left;
  color: inherit;
}
.ghead:hover .g-name { color: var(--tx2); }
.g-arrow {
  font-size: 10px; color: var(--tx3);
  transition: transform 0.18s var(--ease);
}
.g-arrow.fold { transform: rotate(-90deg); }
.g-name {
  font-family: var(--mono); font-size: var(--fs-xs);
  letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--tx3);
  transition: color 0.18s var(--ease);
}
.g-count {
  margin-left: auto;
  font-family: var(--mono); font-size: var(--fs-xs);
  color: var(--tx3);
  background: var(--bg2); border-radius: 8px; padding: 0 7px; line-height: 16px;
}
.row {
  width: 100%; display: flex; align-items: stretch;
  background: none; border: none; cursor: pointer;
  text-align: left; padding: 0;
  color: inherit;
}
.indicator { width: 2px; flex-shrink: 0; background: transparent; transition: background 0.2s var(--ease); }
.row.active .indicator { background: var(--acc); }
.body {
  flex: 1; min-width: 0;
  padding: 9px 16px 9px 14px;
  border-bottom: 1px solid var(--line);
  transition: background 0.18s var(--ease);
}
.row:hover .body { background: var(--bg2); }
.row.active .body { background: var(--bg2); }
.title {
  display: block;
  font-size: var(--fs-sm);
  color: var(--tx2);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.row.active .title { color: var(--tx); }
.time {
  display: block;
  font-family: var(--mono);
  font-size: var(--fs-xs);
  color: var(--tx3);
  margin-top: 1px;
}
.empty { padding: 20px 22px; font-size: var(--fs-sm); color: var(--tx3); line-height: 1.8; }

/* v2.9 工作区：过滤 chips + 行悬浮归档 */
.ws-row { display: flex; flex-wrap: wrap; gap: 5px; padding: 0 16px 10px; }
.ws-chip {
  background: none; border: 1px solid var(--line-hi); border-radius: 999px;
  color: var(--tx3); font-size: var(--fs-xs); padding: 2px 10px; cursor: pointer;
  transition: color 0.15s var(--ease), border-color 0.15s var(--ease), background 0.15s var(--ease);
}
.ws-chip:hover { color: var(--acc); border-color: var(--acc-dim); }
.ws-chip.active { color: var(--acc); border-color: var(--acc-dim); background: #e9effe; }
.row-wrap { position: relative; }
.ws-tag {
  position: absolute; right: 10px; top: 50%; transform: translateY(-50%);
  width: 22px; height: 22px; border-radius: 6px;
  background: var(--bg1); border: 1px solid var(--line); color: var(--tx3);
  display: none; align-items: center; justify-content: center; cursor: pointer;
  box-shadow: 0 1px 6px rgb(27 39 51 / 0.10);
}
.row-wrap:hover .ws-tag { display: flex; }
.ws-tag:hover { color: var(--acc); border-color: var(--acc-dim); }
.ws-pop {
  position: absolute; right: 8px; top: 34px; z-index: 20;
  width: 200px; padding: 10px;
  background: var(--bg1); border-radius: 10px;
  box-shadow: 0 6px 24px rgb(27 39 51 / 0.18), 0 0 0 1px var(--line);
}
.ws-input {
  width: 100%; box-sizing: border-box;
  background: var(--bg0); border: 1px solid var(--line-hi); border-radius: 7px;
  color: var(--tx); font-size: var(--fs-xs); padding: 6px 9px; outline: none;
}
.ws-input:focus { border-color: var(--acc-dim); }
.ws-pop-btns { display: flex; gap: 6px; justify-content: flex-end; margin-top: 8px; }
.ws-pop-btns .btn { padding: 3px 9px; font-size: var(--fs-xs); }
</style>
