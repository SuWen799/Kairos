<script setup>
/** v2.8 统一图标：自绘 SVG 线条风（stroke=currentColor，1.6px，随主题变色）。
 *  用法：<Icon name="paperclip" :size="16" />。禁止再往 UI 里塞 emoji。 */
defineProps({
  name: { type: String, required: true },
  size: { type: Number, default: 16 }
})

const PATHS = {
  menu: { lines: [[4, 7, 20, 7], [4, 12, 20, 12], [4, 17, 20, 17]] },
  gauge: { paths: ['M3 12h4l3-8 4 16 3-8h4'] },
  gear: {
    circles: [[12, 12, 3.2]],
    paths: ['M12 2.8v2.4', 'M12 18.8v2.4', 'M21.2 12h-2.4', 'M5.2 12H2.8',
            'M18.5 5.5l-1.7 1.7', 'M7.2 16.8l-1.7 1.7', 'M18.5 18.5l-1.7-1.7',
            'M7.2 7.2L5.5 5.5']
  },
  x: { lines: [[6, 6, 18, 18], [18, 6, 6, 18]] },
  paperclip: { paths: ['M21.4 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48'] },
  mic: {
    rects: [[9, 2, 6, 11, 3]],
    paths: ['M19 10a7 7 0 0 1-14 0', 'M12 19v3']
  },
  volume: {
    paths: ['M11 5L6 9H2v6h4l5 4V5z', 'M15.5 8.5a5 5 0 0 1 0 7', 'M18.4 5.6a9 9 0 0 1 0 12.8']
  },
  'volume-off': {
    paths: ['M11 5L6 9H2v6h4l5 4V5z', 'M23 9l-6 6', 'M17 9l6 6']
  },
  stop: { rects: [[7, 7, 10, 10, 2]] },
  send: { paths: ['M22 2L11 13', 'M22 2l-7 20-4-9-9-4 20-7z'] },
  image: {
    rects: [[3, 3, 18, 18, 2]],
    circles: [[8.5, 8.5, 1.5]],
    paths: ['M21 15l-5-5L5 21']
  },
  'file-text': {
    paths: ['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z',
            'M14 2v6h6', 'M16 13H8', 'M16 17H8', 'M10 9H8']
  },
  file: { paths: ['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z', 'M14 2v6h6'] },
  token: { paths: ['M4 6h16', 'M4 12h10', 'M4 18h16'] },
  folder: { paths: ['M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z'] },
  plus: { lines: [[12, 5, 12, 19], [5, 12, 19, 12]] },
  trash: { paths: ['M3 6h18', 'M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2', 'M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6'] },
  info: { circles: [[12, 12, 10]], paths: ['M12 16v-4', 'M12 8h.01'] },
  help: { circles: [[12, 12, 10]], paths: ['M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3', 'M12 17h.01'] },
}
</script>

<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none"
       stroke="currentColor" stroke-width="1.6" stroke-linecap="round"
       stroke-linejoin="round" aria-hidden="true" style="display:block">
    <path v-for="(d, i) in (PATHS[name]?.paths || [])" :key="'p' + i" :d="d" />
    <line v-for="(l, i) in (PATHS[name]?.lines || [])" :key="'l' + i"
          :x1="l[0]" :y1="l[1]" :x2="l[2]" :y2="l[3]" />
    <circle v-for="(c, i) in (PATHS[name]?.circles || [])" :key="'c' + i"
            :cx="c[0]" :cy="c[1]" :r="c[2]" />
    <rect v-for="(r, i) in (PATHS[name]?.rects || [])" :key="'r' + i"
          :x="r[0]" :y="r[1]" :width="r[2]" :height="r[3]" :rx="r[4] || 0" />
  </svg>
</template>
