<script setup>
/** 桌宠 v2.1「光泽小机器人」（用户定稿：v2.2 细节版被否，回退到本版）：
 *  - 形态：透明置顶窗里的 SVG 小机器人，多层渐变+高光+环境光遮蔽+辉光滤镜做体积感
 *  - 状态机：offline / idle / working / waiting / ready / failed
 *    映射 sidecar 真实状态：pending confirmations→waiting，
 *    working/queued 工单→working，ready/failed 工单→对应态（一次性气泡）
 *  - 交互：hover 本体关点击穿透、可拖拽（主进程记位）、点击跳审批/闲聊、
 *    工具条（主控台 / 收起）
 *  - 轮询 4s：/api/tickets + /api/confirmations（与 InfoPane 同源） */
import { onMounted, onUnmounted, ref } from 'vue'
import { apiGet } from '../api'

const state = ref('idle')          // offline|idle|working|waiting|ready|failed
const bubble = ref('')             // 气泡文本（''=隐藏）
const hovered = ref(false)
const hop = ref(false)             // 点击时的小跳

let pollTimer = null
let chatterTimer = null
let bubbleTimer = null
let lastSeenReady = localStorage.getItem('kairos_pet_ready') || ''
let lastSeenFailed = localStorage.getItem('kairos_pet_failed') || ''
let prevBubbleKey = ''             // 状态变化才冒一次气泡
let dragging = false
let dragMoved = 0
let lastDragSend = 0

/* ── 状态轮询与映射 ── */
function short(t, n = 12) {
  t = String(t || '').trim().replace(/\s+/g, ' ')
  return t.length > n ? t.slice(0, n) + '…' : t
}

function showBubble(text, ms = 5000, key = '') {
  if (key && key === prevBubbleKey) return   // 同一事件不重复冒泡
  if (key) prevBubbleKey = key
  bubble.value = text
  if (bubbleTimer) clearTimeout(bubbleTimer)
  if (ms > 0) bubbleTimer = setTimeout(() => { bubble.value = '' }, ms)
}

async function poll() {
  let tickets = []
  let confs = []
  try {
    tickets = await apiGet('/api/tickets')
    confs = await apiGet('/api/confirmations')
    if (state.value === 'offline') showBubble('我回来啦~', 3000)
  } catch {
    if (state.value !== 'offline') { state.value = 'offline'; bubble.value = 'sidecar 睡着了…' }
    return
  }

  const pending = (confs || []).filter((c) => c.status === 'pending')
  const active = (tickets || []).find((t) => ['working', 'queued'].includes(t.status))
  const readyT = (tickets || []).find((t) => t.status === 'ready')
  const failedT = (tickets || []).find((t) => t.status === 'failed')

  let next = 'idle'
  let bText = ''
  let key = ''
  if (pending.length) {
    next = 'waiting'
    bText = `${pending.length} 个操作待审批`
    key = `wait:${pending.length}`
  } else if (active) {
    next = 'working'
    bText = `干活中：${short(active.title)}`
    key = `work:${active.id}`
  } else if (readyT && readyT.id !== lastSeenReady) {
    next = 'ready'
    bText = `「${short(readyT.title)}」好啦`
    lastSeenReady = readyT.id
    localStorage.setItem('kairos_pet_ready', lastSeenReady)
  } else if (failedT && failedT.id !== lastSeenFailed) {
    next = 'failed'
    bText = `「${short(failedT.title)}」卡住了…`
    lastSeenFailed = failedT.id
    localStorage.setItem('kairos_pet_failed', lastSeenFailed)
  }

  const changed = state.value !== next
  state.value = next
  if (next === 'waiting' || next === 'working') showBubble(bText, 0, key)   // 常驻气泡
  else if (next === 'ready' || next === 'failed') showBubble(bText, 7000, key || bText)
  else if (changed && next === 'idle') { bubble.value = ''; prevBubbleKey = '' }
}

/* idle 闲聊：低频率随机冒一句 */
const IDLE_LINES = ['我在呢~', '拖我可以挪位置', '点我开主控台', '今天也元气满满', '（眨眼）', '有活儿吩咐我']
function scheduleChatter() {
  chatterTimer = setTimeout(() => {
    if (state.value === 'idle' && !bubble.value) {
      showBubble(IDLE_LINES[Math.floor(Math.random() * IDLE_LINES.length)], 4000)
    }
    scheduleChatter()
  }, 22000 + Math.random() * 20000)
}

/* ── 点击穿透切换（hover 本体/工具条/气泡包围盒）── */
function inRect(r, x, y, pad = 0) {
  return x >= r.left - pad && x <= r.right + pad && y >= r.top - pad && y <= r.bottom + pad
}
function onMouseMove(e) {
  const x = e.clientX
  const y = e.clientY
  let hit = false
  const stage = document.querySelector('.pet-stage')
  if (stage) {
    // 已悬停时命中区外扩 14px：从本体移向 ✕ 按钮途中不出命中区，按钮才点得到
    hit = inRect(stage.getBoundingClientRect(), x, y, hovered.value ? 14 : 0)
  }
  if (!hit) {
    const tools = document.querySelector('.pet-tools')
    if (tools) {
      hit = inRect(tools.getBoundingClientRect(), x, y, 6)
    } else {
      // 工具条未渲染时的固定热区（右上角 64x28），保证第一次能悬停到它
      hit = x >= window.innerWidth - 64 && y <= 28
    }
  }
  if (hit !== hovered.value) {
    hovered.value = hit
    window.kairos?.petHover?.(hit)
  }
}
function onMouseLeaveDoc() {
  if (hovered.value) { hovered.value = false; window.kairos?.petHover?.(false) }
}

/* ── 拖拽（主进程按 cursor 偏移移窗）+ 点击判定 ── */
function onStageDown(e) {
  if (e.button !== 0) return
  dragging = true
  dragMoved = 0
  window.kairos?.petDragStart?.()
}
function onDocMove(e) {
  if (!dragging) return
  dragMoved += Math.abs(e.movementX || 0) + Math.abs(e.movementY || 0)
  // 轻节流：≥16ms 才发一次 IPC，防 mousemove 事件洪水
  const now = performance.now()
  if (dragMoved > 4 && now - lastDragSend >= 16) {
    lastDragSend = now
    window.kairos?.petDragMove?.()
  }
}
function onDocUp() {
  if (!dragging) return
  dragging = false
  window.kairos?.petDragEnd?.()
  if (dragMoved <= 4) onPetClick()
}
function onPetClick() {
  if (state.value === 'waiting' || state.value === 'ready' || state.value === 'offline') {
    openMain()
    return
  }
  hop.value = true
  setTimeout(() => { hop.value = false }, 500)
  showBubble(IDLE_LINES[Math.floor(Math.random() * IDLE_LINES.length)], 3000)
}

function openMain() {
  window.kairos?.openMain?.()
}
function closePet() {
  window.kairos?.togglePet?.()
}

onMounted(() => {
  // 桌宠视图：全局 body 是白底（styles.css），这里强制透明让窗口真透明
  document.documentElement.style.background = 'transparent'
  document.body.style.background = 'transparent'
  poll()
  pollTimer = setInterval(poll, 4000)
  scheduleChatter()
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mousemove', onDocMove)
  document.addEventListener('mouseup', onDocUp)
  document.addEventListener('mouseleave', onMouseLeaveDoc)
})
onUnmounted(() => {
  clearInterval(pollTimer)
  clearTimeout(chatterTimer)
  if (bubbleTimer) clearTimeout(bubbleTimer)
  document.removeEventListener('mousemove', onMouseMove)
  document.removeEventListener('mousemove', onDocMove)
  document.removeEventListener('mouseup', onDocUp)
  document.removeEventListener('mouseleave', onMouseLeaveDoc)
  window.kairos?.petHover?.(false)
})
</script>

<template>
  <div class="pet-root">
    <!-- hover 工具条 -->
    <div v-if="hovered" class="pet-tools">
      <button title="打开主控台" @click.stop="openMain">⌂</button>
      <button title="收起桌宠" @click.stop="closePet">✕</button>
    </div>

    <!-- 气泡 -->
    <transition name="pop">
      <div v-if="bubble" class="pet-bubble">{{ bubble }}</div>
    </transition>

    <!-- 本体 -->
    <div
      class="pet-stage"
      :class="[`st-${state}`, { hop, hoverable: hovered }]"
      @mousedown="onStageDown"
    >
      <svg viewBox="0 0 200 220" class="pet-svg" aria-hidden="true">
        <defs>
          <linearGradient id="bodyGrad" x1="0.15" y1="0.05" x2="0.9" y2="1">
            <stop offset="0" stop-color="#a5f3fc" />
            <stop offset="0.42" stop-color="#67a5fa" />
            <stop offset="1" stop-color="#4f46e5" />
          </linearGradient>
          <linearGradient id="bodyShade" x1="0" y1="0.45" x2="0" y2="1">
            <stop offset="0" stop-color="#1e1b4b" stop-opacity="0" />
            <stop offset="1" stop-color="#1e1b4b" stop-opacity="0.5" />
          </linearGradient>
          <radialGradient id="specGrad" cx="0.5" cy="0.5" r="0.5">
            <stop offset="0" stop-color="#ffffff" stop-opacity="0.85" />
            <stop offset="1" stop-color="#ffffff" stop-opacity="0" />
          </radialGradient>
          <linearGradient id="faceGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stop-color="#ffffff" />
            <stop offset="1" stop-color="#e6edfb" />
          </linearGradient>
          <radialGradient id="eyeGrad" cx="0.35" cy="0.3" r="0.9">
            <stop offset="0" stop-color="#5b6b8c" />
            <stop offset="0.55" stop-color="#232c44" />
            <stop offset="1" stop-color="#0c1222" />
          </radialGradient>
          <linearGradient id="goldGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stop-color="#fef3c7" />
            <stop offset="0.5" stop-color="#fbbf24" />
            <stop offset="1" stop-color="#d97706" />
          </linearGradient>
          <linearGradient id="armGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stop-color="#6d7ff7" />
            <stop offset="1" stop-color="#3f37c9" />
          </linearGradient>

          <clipPath id="bodyClip">
            <path d="M100 52 C147 52 161 88 161 119 C161 155 134 175 100 175 C66 175 39 155 39 119 C39 88 53 52 100 52 Z" />
          </clipPath>

          <filter id="f-soft" x="-40%" y="-40%" width="180%" height="180%">
            <feDropShadow dx="0" dy="7" stdDeviation="7" flood-color="#1e1b4b" flood-opacity="0.35" />
          </filter>
          <filter id="f-blur3" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation="3" />
          </filter>
          <filter id="f-blur1" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation="1.1" />
          </filter>
          <filter id="f-glow" x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur stdDeviation="3.2" result="b" />
            <feMerge>
              <feMergeNode in="b" /><feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        <!-- 地面影子（带虚化） -->
        <ellipse class="shadow" cx="100" cy="207" rx="42" ry="8" fill="#0f172a" opacity="0.18" filter="url(#f-blur3)" />

        <!-- 环境浮尘粒子 -->
        <g class="dust">
          <circle cx="44" cy="94" r="2.2" fill="#bae6fd" opacity="0.8" />
          <circle cx="158" cy="126" r="1.8" fill="#c7d2fe" opacity="0.7" />
          <circle cx="146" cy="50" r="1.4" fill="#fef3c7" opacity="0.8" />
        </g>

        <!-- working 顶上三跳点 -->
        <g class="dots" v-if="state === 'working'">
          <circle cx="76" cy="30" r="4" fill="#6366f1" filter="url(#f-glow)" />
          <circle cx="100" cy="24" r="4" fill="#818cf8" filter="url(#f-glow)" />
          <circle cx="124" cy="30" r="4" fill="#6366f1" filter="url(#f-glow)" />
        </g>

        <!-- 天线 + 星芒（Kairos 标志） -->
        <g class="antenna">
          <line x1="100" y1="54" x2="100" y2="32" stroke="#475569" stroke-width="3" stroke-linecap="round" />
          <circle cx="100" cy="32" r="3" fill="#94a3b8" />
          <g class="star" filter="url(#f-glow)">
            <path d="M100 10 L104.4 25.6 L120 30 L104.4 34.4 L100 50 L95.6 34.4 L80 30 L95.6 25.6 Z" fill="url(#goldGrad)" />
            <circle cx="96" cy="26" r="1.6" fill="#fffbeb" opacity="0.9" />
          </g>
        </g>

        <!-- 身体 -->
        <g class="body" filter="url(#f-soft)">
          <!-- 主体（蛋形黏团） -->
          <path d="M100 52 C147 52 161 88 161 119 C161 155 134 175 100 175 C66 175 39 155 39 119 C39 88 53 52 100 52 Z" fill="url(#bodyGrad)" />
          <!-- 底部遮蔽（同形叠加） -->
          <path d="M100 52 C147 52 161 88 161 119 C161 155 134 175 100 175 C66 175 39 155 39 119 C39 88 53 52 100 52 Z" fill="url(#bodyShade)" />

          <!-- 左上主高光（裁进身体） -->
          <g clip-path="url(#bodyClip)">
            <ellipse cx="76" cy="80" rx="34" ry="24" fill="url(#specGrad)" transform="rotate(-24 76 80)" />
            <!-- 底缘反光（rim light） -->
            <path d="M52 152 Q100 184 148 152 Q124 172 100 172 Q76 172 52 152 Z" fill="#7dd3fc" opacity="0.4" filter="url(#f-blur3)" />
          </g>

          <!-- 玻璃面板脸 -->
          <g>
            <rect x="57" y="76" width="86" height="64" rx="28" fill="url(#faceGrad)" opacity="0.96" />
            <rect x="57" y="76" width="86" height="64" rx="28" fill="none" stroke="#ffffff" stroke-opacity="0.85" stroke-width="1.6" />
            <!-- 面板顶部光泽条 -->
            <rect x="66" y="81" width="68" height="11" rx="5.5" fill="#ffffff" opacity="0.6" filter="url(#f-blur1)" />
          </g>

          <!-- 眼睛（按状态切换，带高光点） -->
          <g v-if="state === 'failed'" class="eyes">
            <path d="M79 95 L92 108 M92 95 L79 108" stroke="#1e293b" stroke-width="4" stroke-linecap="round" />
            <path d="M108 95 L121 108 M121 95 L108 108" stroke="#1e293b" stroke-width="4" stroke-linecap="round" />
          </g>
          <g v-else-if="state === 'ready'" class="eyes">
            <path d="M76 105 Q84 93 92 105" stroke="#1e293b" stroke-width="4.5" fill="none" stroke-linecap="round" />
            <path d="M108 105 Q116 93 124 105" stroke="#1e293b" stroke-width="4.5" fill="none" stroke-linecap="round" />
          </g>
          <g v-else-if="state === 'working'" class="eyes">
            <g class="blink">
              <rect x="77" y="99" width="15" height="6" rx="3" fill="url(#eyeGrad)" />
              <rect x="108" y="99" width="15" height="6" rx="3" fill="url(#eyeGrad)" />
            </g>
          </g>
          <g v-else class="eyes">
            <g :class="{ blink: true, wide: state === 'waiting' }">
              <ellipse cx="85" cy="102" rx="7.5" ry="10" fill="url(#eyeGrad)" />
              <ellipse cx="115" cy="102" rx="7.5" ry="10" fill="url(#eyeGrad)" />
              <!-- 眼内高光（灵动感关键） -->
              <circle cx="82.5" cy="98" r="2.6" fill="#ffffff" opacity="0.95" />
              <circle cx="112.5" cy="98" r="2.6" fill="#ffffff" opacity="0.95" />
              <circle cx="88" cy="106" r="1.2" fill="#cbd5f5" opacity="0.8" />
              <circle cx="118" cy="106" r="1.2" fill="#cbd5f5" opacity="0.8" />
            </g>
          </g>

          <!-- 嘴 -->
          <path v-if="state === 'ready'" d="M91 119 Q100 129 109 119" stroke="#1e293b" stroke-width="3.5" fill="none" stroke-linecap="round" />
          <path v-else-if="state === 'failed'" d="M92 124 Q100 117 108 124" stroke="#1e293b" stroke-width="3" fill="none" stroke-linecap="round" />
          <path v-else-if="state === 'working'" d="M94 121 L106 121" stroke="#1e293b" stroke-width="3" stroke-linecap="round" />
          <path v-else d="M95 120 Q100 126 105 120" stroke="#1e293b" stroke-width="3" fill="none" stroke-linecap="round" />

          <!-- 腮红（虚化） -->
          <ellipse cx="71" cy="115" rx="5.5" ry="3.2" fill="#fb7185" opacity="0.55" filter="url(#f-blur1)" />
          <ellipse cx="129" cy="115" rx="5.5" ry="3.2" fill="#fb7185" opacity="0.55" filter="url(#f-blur1)" />

          <!-- 手（渐变+体积） -->
          <g class="arm arm-l">
            <rect x="32" y="104" width="17" height="28" rx="8.5" fill="url(#armGrad)" />
            <rect x="34" y="107" width="6" height="10" rx="3" fill="#ffffff" opacity="0.28" />
          </g>
          <g class="arm arm-r">
            <rect x="151" y="104" width="17" height="28" rx="8.5" fill="url(#armGrad)" />
            <rect x="153" y="107" width="6" height="10" rx="3" fill="#ffffff" opacity="0.28" />
          </g>
        </g>

        <!-- 状态徽章（带辉光） -->
        <g v-if="state === 'waiting'" class="badge" filter="url(#f-glow)">
          <circle cx="158" cy="58" r="15" fill="url(#goldGrad)" />
          <circle cx="153" cy="52" r="4.5" fill="#ffffff" opacity="0.5" />
          <text x="158" y="65" text-anchor="middle" font-size="19" font-weight="bold" fill="#7c2d12">!</text>
        </g>
        <g v-if="state === 'ready'" class="badge" filter="url(#f-glow)">
          <circle cx="158" cy="58" r="15" fill="#22c55e" />
          <circle cx="153" cy="52" r="4.5" fill="#ffffff" opacity="0.45" />
          <path d="M149 58 L154 64 L164 52" stroke="#fff" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round" />
        </g>
        <g v-if="state === 'failed'" class="badge" filter="url(#f-glow)">
          <circle cx="158" cy="58" r="15" fill="#94a3b8" />
          <path d="M150 52 L162 66 M162 52 L150 66" stroke="#fff" stroke-width="4" stroke-linecap="round" />
        </g>

        <!-- offline 暗淡光晕 -->
        <circle v-if="state === 'offline'" cx="100" cy="26" r="18" fill="url(#specGrad)" opacity="0.25" />
      </svg>
    </div>
  </div>
</template>

<style scoped>
.pet-root {
  width: 100vw;
  height: 100vh;
  position: relative;
  background: transparent;
  overflow: hidden;
  user-select: none;
  cursor: default;
}

/* ── 气泡（小窗配小气泡） ── */
.pet-bubble {
  position: absolute;
  top: 4px;
  left: 50%;
  transform: translateX(-50%);
  max-width: 136px;
  padding: 6px 9px;
  background: #ffffff;
  color: #1b2733;
  font-size: 11px;
  line-height: 1.45;
  border-radius: 10px;
  border: 1.5px solid #dbe3ef;
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.14);
  z-index: 5;
  word-break: break-all;
}
.pet-bubble::after {
  content: '';
  position: absolute;
  bottom: -5px;
  left: 50%;
  transform: translateX(-50%) rotate(45deg);
  width: 9px;
  height: 9px;
  background: #ffffff;
  border-right: 1.5px solid #dbe3ef;
  border-bottom: 1.5px solid #dbe3ef;
}
.pop-enter-active, .pop-leave-active { transition: all 0.18s ease; }
.pop-enter-from, .pop-leave-to { opacity: 0; transform: translateX(-50%) translateY(5px) scale(0.92); }

/* ── 工具条 ── */
.pet-tools {
  position: absolute;
  top: 3px;
  right: 3px;
  display: flex;
  gap: 3px;
  z-index: 6;
}
.pet-tools button {
  width: 19px;
  height: 19px;
  border: 1px solid #dbe3ef;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.94);
  color: #1b2733;
  font-size: 10px;
  line-height: 1;
  cursor: pointer;
  padding: 0;
}
.pet-tools button:hover { background: #eef2f9; }

/* ── 本体舞台（小体型，保持 200:220 比例） ── */
.pet-stage {
  position: absolute;
  left: 50%;
  bottom: 0;
  width: 136px;
  height: 150px;
  transform: translateX(-50%);
  cursor: grab;
}
.pet-stage:active { cursor: grabbing; }
.pet-svg { width: 100%; height: 100%; display: block; overflow: visible; }

/* ── 通用动画 ── */
.body { transform-origin: 100px 175px; }
.shadow { transform-origin: 100px 207px; }
.eyes .blink, .eyes g.blink, .eyes rect.blink { transform-origin: center; transform-box: fill-box; }
.eyes .wide { transform: scale(1.12); }
.star { transform-origin: 100px 30px; }
.spec { transform-origin: center; transform-box: fill-box; }

/* idle：呼吸 + 眨眼 + 星芒闪 + 浮尘飘 */
.st-idle .body { animation: breathe 3.2s ease-in-out infinite; }
.st-idle .shadow { animation: shadowPulse 3.2s ease-in-out infinite; }
.st-idle .blink { animation: blink 4.2s infinite; }
.star { animation: twinkle 2.6s ease-in-out infinite; }
.dust circle { animation: floaty 5s ease-in-out infinite; }
.dust circle:nth-child(2) { animation-delay: 1.6s; }
.dust circle:nth-child(3) { animation-delay: 3.1s; }

/* working：左右摇摆 + 专注 + 跳点 */
.st-working .body { animation: wiggle 0.7s ease-in-out infinite; }
.st-working .star { animation-duration: 1s; }
.st-working .dots circle { animation: dotjump 0.9s ease-in-out infinite; }
.st-working .dots circle:nth-child(2) { animation-delay: 0.15s; }
.st-working .dots circle:nth-child(3) { animation-delay: 0.3s; }

/* waiting：急促弹跳 + 徽章晃 */
.st-waiting .body { animation: bounce 0.62s cubic-bezier(0.3, 0, 0.4, 1) infinite; }
.st-waiting .shadow { animation: shadowPulse 0.62s ease-in-out infinite; }
.st-waiting .badge { animation: badgeSwing 0.62s ease-in-out infinite; transform-origin: 158px 58px; }

/* ready：开心跳 */
.st-ready .body { animation: happy 1.1s ease-in-out infinite; }
.st-ready .shadow { animation: shadowPulse 1.1s ease-in-out infinite; }
.st-ready .badge { animation: badgePop 1.1s ease-in-out infinite; transform-origin: 158px 58px; }

/* failed：蔫 */
.st-failed .body { animation: droop 2.4s ease-in-out infinite; }
.st-failed .star { animation: twinkle 4.5s ease-in-out infinite; opacity: 0.4; }

/* offline：灰暗静止 */
.st-offline .body { opacity: 0.5; filter: saturate(0.2); }
.st-offline { filter: grayscale(0.6); }

/* 点击小跳 */
.hop .body { animation: hopOnce 0.5s cubic-bezier(0.3, 0, 0.3, 1) 1 !important; }

@keyframes breathe {
  0%, 100% { transform: translateY(0) scale(1); }
  50% { transform: translateY(-4px) scale(1.02, 0.98); }
}
@keyframes shadowPulse {
  0%, 100% { transform: scaleX(1); opacity: 0.18; }
  50% { transform: scaleX(0.84); opacity: 0.1; }
}
@keyframes blink {
  0%, 94%, 100% { transform: scaleY(1); }
  96%, 98% { transform: scaleY(0.08); }
}
@keyframes twinkle {
  0%, 100% { opacity: 1; transform: scale(1) rotate(0deg); }
  50% { opacity: 0.55; transform: scale(0.86) rotate(12deg); }
}
@keyframes floaty {
  0%, 100% { transform: translateY(0); opacity: 0.35; }
  50% { transform: translateY(-9px); opacity: 0.9; }
}
@keyframes wiggle {
  0%, 100% { transform: rotate(-3deg) translateY(0); }
  50% { transform: rotate(3deg) translateY(-3px); }
}
@keyframes dotjump {
  0%, 100% { transform: translateY(0); opacity: 0.5; }
  50% { transform: translateY(-8px); opacity: 1; }
}
@keyframes bounce {
  0%, 100% { transform: translateY(0) scale(1); }
  35% { transform: translateY(-12px) scale(0.96, 1.05); }
  70% { transform: translateY(0) scale(1.04, 0.95); }
}
@keyframes badgeSwing {
  0%, 100% { transform: rotate(-9deg); }
  50% { transform: rotate(9deg); }
}
@keyframes badgePop {
  0%, 100% { transform: scale(1); }
  40% { transform: scale(1.18); }
}
@keyframes happy {
  0%, 100% { transform: translateY(0) rotate(0deg); }
  30% { transform: translateY(-10px) rotate(-4deg); }
  60% { transform: translateY(0) rotate(3deg); }
}
@keyframes droop {
  0%, 100% { transform: translateY(2px) rotate(1.5deg); }
  50% { transform: translateY(4px) rotate(2.5deg); }
}
@keyframes hopOnce {
  0%, 100% { transform: translateY(0) scale(1); }
  40% { transform: translateY(-16px) scale(0.97, 1.04); }
  75% { transform: translateY(0) scale(1.05, 0.94); }
}
</style>
