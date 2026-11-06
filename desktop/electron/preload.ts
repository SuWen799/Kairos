/**
 * contextBridge 最小 IPC（设计 4.1）：只暴露只读信息，不暴露 Node 能力。
 * 渲染层通过 window.kairos.backendPort() 拿到 sidecar 端口后直连 fetch/SSE。
 */
import { contextBridge, ipcRenderer } from 'electron'

console.log('[preload] loading...')
contextBridge.exposeInMainWorld('kairos', {
  backendPort: (): Promise<number> =>
    ipcRenderer.invoke('backend:info').then((info: { port: number }) => info.port),
  /** v2.5：弹窗盖不住 Windows 原生 overlay 钮（系统层最顶），打开弹窗时把 overlay 一起调暗 */
  setTitleBarOverlay: (opts: { color?: string; symbolColor?: string }): Promise<void> =>
    ipcRenderer.invoke('titlebar:overlay', opts),

  /* ── 桌宠（Codex Pets 式状态伴侣）── */
  /** 召唤/收回桌宠；返回 true=已召唤 */
  togglePet: (): Promise<boolean> => ipcRenderer.invoke('pet:toggle'),
  /** 打开主控台窗口 */
  openMain: (): Promise<void> => ipcRenderer.invoke('pet:open-main'),
  /** 开机自启开关；不传参=读当前状态（打包态生效，dev 下 Windows 也支持） */
  autostart: (on?: boolean): Promise<boolean> => ipcRenderer.invoke('app:autostart', on),
  /** hover 到宠物本体 → 临时关穿透收鼠标；离开 → 恢复穿透 */
  petHover: (hit: boolean): void => ipcRenderer.send('pet:hover', hit),
  /** 自实现拖拽三件套（app-region 会吞 mousemove，与穿透切换冲突） */
  petDragStart: (): void => ipcRenderer.send('pet:drag-start'),
  petDragMove: (): void => ipcRenderer.send('pet:drag-move'),
  petDragEnd: (): void => ipcRenderer.send('pet:drag-end')
})
console.log('[preload] kairos bridge exposed')
