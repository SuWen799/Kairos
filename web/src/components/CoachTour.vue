<script setup>
/** v2.7 新手引导（指向式）：高亮框圈住目标 + 说明小卡，上一步/下一步/跳过。
 *  首启自动播（App.vue 控制），帮助页可重看；进度记 localStorage。 */
import { onMounted, onUnmounted, ref } from 'vue'

const emit = defineEmits(['done'])

const STEPS = [
  { sel: '#tb-side', place: 'bottom', title: '会话索引',
    text: '收起/展开左侧栏。左边是你的所有任务，按工作空间分组。' },
  { sel: '.side', place: 'right', title: '任务时间线',
    text: '所有会话按时间自动分组（今天 / 近 7 天 / 更早），不用建文件夹。点「新对话」直接开下一件事。' },
  { sel: '.input-card', place: 'top', title: '输入框：干活的主入口',
    text: '输入框上方可以切模式（对话 / 规划 / 目标）、换模型、调审批权限档；左侧回形针按钮传图片/PDF/Word，话筒按钮按住说话。' },
  { sel: '#tb-info', place: 'bottom', title: '仪表面板',
    text: '按需查看：动态（事件与待确认）、产物（生成的文件可预览/复制）、运行（审计与预算）。默认收起不碍眼。' },
  { sel: '#tb-settings', place: 'bottom', title: '设置',
    text: '沙箱档位、并行子代理、Agent 中调中枢（派活给 Claude Code/Codex/豆包）、技能库都在这里配。' },
  { sel: '#tb-help', place: 'bottom', title: '帮助',
    text: '模式/权限说明和常用指令示例。忘了怎么用随时来，本引导也能在这里重看。' },
]

const idx = ref(0)
const box = ref(null)     // {top,left,w,h}
const tip = ref(null)     // {top,left}
const tipPlace = ref('bottom')
const cur = ref(STEPS[0])
let raf = 0

function measure() {
  const step = STEPS[idx.value]
  const el = document.querySelector(step.sel)
  if (!el) { next(); return }
  el.scrollIntoView?.({ block: 'nearest' })
  const r = el.getBoundingClientRect()
  const pad = 8
  box.value = {
    top: Math.max(4, r.top - pad), left: Math.max(4, r.left - pad),
    w: r.width + pad * 2, h: r.height + pad * 2,
  }
  // 说明卡位置：优先 place 方向，放不下换对侧
  const tipW = 320, tipH = 150
  let top, left, place = step.place
  if (place === 'right') {
    top = r.top + r.height / 2 - tipH / 2
    left = r.right + pad + 12
    if (left + tipW > innerWidth - 8) place = 'left'
  }
  if (place === 'left') {
    top = r.top + r.height / 2 - tipH / 2
    left = r.left - pad - 12 - tipW
    if (left < 8) place = 'right'
  }
  if (place === 'right') { left = r.right + pad + 12 }
  else if (place === 'left') { left = r.left - pad - 12 - tipW }
  else {
    left = Math.min(Math.max(8, r.left + r.width / 2 - tipW / 2), innerWidth - tipW - 8)
    if (place === 'bottom') {
      top = r.bottom + pad + 14
      if (top + tipH > innerHeight - 8) place = 'top'
    }
    if (place === 'top') {
      top = r.top - pad - 14 - tipH
      if (top < 8) place = 'bottom'
    }
  }
  tipPlace.value = place
  tip.value = { top: Math.max(8, top), left: Math.max(8, left) }
  cur.value = step
}

function next() {
  if (idx.value >= STEPS.length - 1) return done()
  idx.value++
  measure()
}
function prev() {
  if (idx.value <= 0) return
  idx.value--
  measure()
}
function done() { emit('done') }
function onKey(e) {
  if (e.key === 'Escape') done()
  else if (e.key === 'Enter' || e.key === 'ArrowRight') next()
  else if (e.key === 'ArrowLeft') prev()
}
function onResize() {
  cancelAnimationFrame(raf)
  raf = requestAnimationFrame(measure)
}

onMounted(() => {
  measure()
  addEventListener('resize', onResize)
  addEventListener('keydown', onKey)
})
onUnmounted(() => {
  removeEventListener('resize', onResize)
  removeEventListener('keydown', onKey)
  cancelAnimationFrame(raf)
})
</script>

<template>
  <div class="tour">
    <!-- 高亮圈：巨阴影把其余区域压暗；圈本身不挡点击 -->
    <div v-if="box" class="spot" :style="{
      top: box.top + 'px', left: box.left + 'px',
      width: box.w + 'px', height: box.h + 'px'
    }"></div>

    <!-- 说明卡 -->
    <div v-if="tip" class="tip" :style="{ top: tip.top + 'px', left: tip.left + 'px' }">
      <div class="tip-step">{{ idx + 1 }} / {{ STEPS.length }}</div>
      <div class="tip-title">{{ cur.title }}</div>
      <div class="tip-text">{{ cur.text }}</div>
      <div class="tip-btns">
        <button class="btn" @click="done">跳过</button>
        <button v-if="idx > 0" class="btn" @click="prev">上一步</button>
        <button class="btn primary" @click="next">{{ idx >= STEPS.length - 1 ? '开始使用' : '下一步' }}</button>
      </div>
      <span class="tip-arrow" :class="tipPlace"></span>
    </div>
  </div>
</template>

<style scoped>
.tour { position: fixed; inset: 0; z-index: 150; }
.spot {
  position: fixed; border-radius: 12px;
  box-shadow: 0 0 0 9999px rgb(15 23 32 / 0.55);
  outline: 2px solid var(--acc);
  outline-offset: 2px;
  pointer-events: none;
  transition: all 0.25s var(--ease);
}
.tip {
  position: fixed; width: 320px;
  background: var(--bg1); border-radius: 14px;
  box-shadow: 0 8px 32px rgb(27 39 51 / 0.18), 0 0 0 1px var(--line);
  padding: 16px 18px 14px;
  animation: tipIn 0.22s var(--ease);
}
@keyframes tipIn {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: none; }
}
.tip-step {
  font-family: var(--mono); font-size: var(--fs-xs); color: var(--acc);
  margin-bottom: 6px; letter-spacing: 0.06em;
}
.tip-title { font-size: var(--fs-base); font-weight: 620; color: var(--tx); margin-bottom: 6px; }
.tip-text { font-size: var(--fs-sm); color: var(--tx2); line-height: 1.75; margin-bottom: 14px; }
.tip-btns { display: flex; gap: 8px; justify-content: flex-end; }
</style>
