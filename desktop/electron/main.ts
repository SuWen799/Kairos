/**
 * Kairos 桌面壳主进程（设计 4.1 / 需求 11.6）：
 * spawnSidecar → waitHealthy → createWindow/createTray → watchdog(30s) → 优雅停机。
 *
 * 端口策略（设计文档第 8 章诚实声明二选一）：固定高位端口 + 探测占用，
 * 弃用「端口 0 + stdout 回报」（Windows 缓冲坑）。
 */
import { app, BrowserWindow, Tray, Menu, ipcMain, nativeImage, crashReporter, shell, screen } from 'electron'
import { ChildProcess, spawn } from 'child_process'
import * as fs from 'fs'
import * as http from 'http'
import * as net from 'net'
import * as os from 'os'
import * as path from 'path'

// ── 常量 ───────────────────────────────────────────────
const PORT_BASE = 8630            // 探测起点（8626 是裸跑 sidecar 的默认口）
const HEALTH_TIMEOUT_MS = 30_000  // waitHealthy 上限
const WATCHDOG_INTERVAL_MS = 30_000
const WATCHDOG_MAX_FAILS = 3

let sidecar: ChildProcess | null = null
let backendPort = 0
let mainWindow: BrowserWindow | null = null
let petWindow: BrowserWindow | null = null
let tray: Tray | null = null
let watchdogTimer: ReturnType<typeof setInterval> | null = null
let consecutiveFails = 0
let shuttingDown = false
// 桌宠拖拽：cursor 与窗口左上角的偏移（drag-start 时记录，drag-move 中应用）
let petDragOffset = { x: 0, y: 0 }

// ── 工具 ───────────────────────────────────────────────
function logDir(): string {
  const dir = path.join(app.getPath('userData'), 'logs')
  fs.mkdirSync(dir, { recursive: true })
  return dir
}

function log(line: string): void {
  const ts = new Date().toISOString()
  fs.appendFileSync(path.join(logDir(), 'main.log'), `[${ts}] ${line}\n`)
  console.log(`[kairos] ${line}`)
}

/** 遥测/崩溃上报开关（需求 M5：默认关，用户显式开启才上报）。 */
function telemetryEnabled(): boolean {
  try {
    const cfg = path.join(app.getPath('userData'), 'telemetry.json')
    return fs.existsSync(cfg) &&
      JSON.parse(fs.readFileSync(cfg, 'utf-8')).enabled === true
  } catch {
    return false
  }
}

/** crashReporter：崩溃转储始终本地留档（可诊断），仅遥测开启时才考虑上传。
 *  当前无崩溃接收服务器——uploadToServer 恒 false，诚实声明见 README。 */
function startCrashReporter(): void {
  try {
    crashReporter.start({
      uploadToServer: false, // 无接收端；遥测开关就绪后按 telemetryEnabled() 接服务器
      compress: true,
      submitURL: telemetryEnabled() ? (process.env.KAIROS_CRASH_URL || '') : ''
    })
    log(`crashReporter started (telemetry=${telemetryEnabled()})`)
  } catch (e) {
    log(`crashReporter failed: ${String(e)}`)
  }
}

/** 自动更新（M5，需求：electron-updater + 诚实降级）：
 *  仅发布态且装了 electron-updater 才启用；否则托盘留"检查更新"打开发布页。 */
async function setupAutoUpdater(): Promise<void> {
  if (!app.isPackaged) return
  try {
    // 变量指示符防构建期静态解析（包缺失时运行时 catch 降级）
    const updaterName = 'electron-updater'
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const mod: any = await import(/* @vite-ignore */ updaterName)
    const autoUpdater = mod.autoUpdater
    autoUpdater.logger = null
    autoUpdater.on('update-downloaded', () => {
      log('update downloaded')
      mainWindow?.webContents.send('update:ready')
    })
    autoUpdater.on('error', (e: unknown) => log(`updater: ${String(e)}`))
    autoUpdater.checkForUpdatesAndNotify().catch((e: unknown) => log(`update check: ${String(e)}`))
  } catch {
    log('electron-updater 未安装，自动更新降级为手动（诚实声明）')
  }
}

/** 从 PORT_BASE 起探测空闲端口（设计：固定高位端口 + 探测占用） */
function probeFreePort(start = PORT_BASE): Promise<number> {
  return new Promise((resolve, reject) => {
    let port = start
    const tryNext = (): void => {
      if (port > start + 100) return reject(new Error('no free port in range'))
      const srv = net.createServer()
      srv.once('error', () => { port += 1; tryNext() })
      srv.once('listening', () => srv.close(() => resolve(port)))
      srv.listen(port, '127.0.0.1')
    }
    tryNext()
  })
}

function httpGetStatus(url: string, timeoutMs = 2000): Promise<number> {
  return new Promise((resolve) => {
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      res.resume()
      resolve(res.statusCode ?? 0)
    })
    req.on('timeout', () => { req.destroy(); resolve(0) })
    req.on('error', () => resolve(0))
  })
}

// ── sidecar 生命周期 ───────────────────────────────────
/** 开发态 spawn venv python run.py；发布态拉 resources/backend/backend.exe（PyInstaller onedir） */
function spawnSidecar(port: number): ChildProcess {
  const dev = !app.isPackaged
  const projectRoot = dev
    ? path.resolve(__dirname, '..', '..', '..')      // desktop/out/main → desktop → Kairos 根
    : process.resourcesPath
  // dev：kairos/ 源码目录（run.py）；打包：backend/（exe 同级，作为 cwd）
  const kairosDir = dev ? path.join(projectRoot, 'kairos') : path.join(projectRoot, 'backend')

  const python = process.env.KAIROS_PYTHON
    || (dev
      ? 'C:/Users/<user>/.workbuddy/binaries/python/envs/kairos/Scripts/python.exe'
      : path.join(projectRoot, 'backend', 'backend.exe'))

  const cmd = dev ? python : python
  const args = dev ? ['run.py'] : []

  const out = fs.openSync(path.join(logDir(), 'sidecar.log'), 'a')
  // 打包态专属：数据落 userData（安装目录只读），手机端 SPA 指 extraResources/web-dist
  const packagedEnv = dev ? {} : {
    KAIROS_DATA_DIR: path.join(app.getPath('userData'), 'data'),
    KAIROS_WEB_DIST: path.join(projectRoot, 'web-dist')
  }
  const child = spawn(cmd, args, {
    cwd: kairosDir,
    stdio: ['ignore', out, out],
    env: {
      ...process.env,
      // M3 重构：恒绑 0.0.0.0（手机遥控地基）；远端是否放行由 data/lan.json 控制
      //（默认拒绝，server.py 中间件对非 loopback 一律 403，除非用户在设置里开启手机连接）
      KAIROS_HOST: '0.0.0.0',
      KAIROS_PORT: String(port),
      PYTHONUTF8: '1',   // 防 GBK（秋招通同款经验）
      ...packagedEnv
    },
    windowsHide: true
  })
  log(`sidecar spawned pid=${child.pid} port=${port} cmd=${cmd} ${args.join(' ')}`)

  child.on('error', (e) => log(`sidecar spawn error: ${String(e)}`))
  child.on('exit', (code) => {
    log(`sidecar exited code=${code}`)
    if (!shuttingDown) scheduleRespawn()
  })
  return child
}

/** 探活失败连续 3 次 → 宽限一次重启（需求 11.6 watchdog） */
function scheduleRespawn(): void {
  if (shuttingDown) return
  log('watchdog: respawning sidecar...')
  try { sidecar?.kill() } catch { /* already dead */ }
  setTimeout(async () => {
    if (shuttingDown) return
    try {
      backendPort = await probeFreePort()
      sidecar = spawnSidecar(backendPort)
      const ok = await waitHealthy(backendPort, HEALTH_TIMEOUT_MS)
      log(`respawn healthy=${ok}`)
    } catch (e) {
      log(`respawn failed: ${String(e)}`)
    }
  }, 1500)
}

function waitHealthy(port: number, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs
  return new Promise((resolve) => {
    const tick = async (): Promise<void> => {
      const code = await httpGetStatus(`http://127.0.0.1:${port}/health`)
      if (code === 200) return resolve(true)
      if (Date.now() > deadline) return resolve(false)
      setTimeout(tick, 500)
    }
    tick()
  })
}

function startWatchdog(): void {
  watchdogTimer = setInterval(async () => {
    if (shuttingDown || !backendPort) return
    const code = await httpGetStatus(`http://127.0.0.1:${backendPort}/health`)
    if (code === 200) {
      consecutiveFails = 0
      return
    }
    consecutiveFails += 1
    log(`watchdog: health fail ${consecutiveFails}/${WATCHDOG_MAX_FAILS} (http=${code})`)
    if (consecutiveFails >= WATCHDOG_MAX_FAILS) {
      consecutiveFails = 0
      scheduleRespawn()
    }
  }, WATCHDOG_INTERVAL_MS)
}

// ── 窗口与托盘 ─────────────────────────────────────────
// 图标资源：开发态 desktop/resources，打包后 <install>/resources（electron-builder extraResources）
function resourcesDir(): string {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'resources')
    : path.join(app.getAppPath(), 'resources')
}

function createWindow(): void {
  // 全应用菜单移除（File/Edit/View/Window 默认菜单栏去掉，UI 全自绘）
  Menu.setApplicationMenu(null)
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 600,
    show: false,
    backgroundColor: '#ffffff',
    icon: path.join(resourcesDir(), 'icon.ico'),
    // 自绘标题栏：隐藏原生标题条，右上角保留 Windows 原生窗口控制钮（overlay 配色跟 v4 白昼）
    titleBarStyle: 'hidden',
    titleBarOverlay: {
      color: '#f6f8fb',
      symbolColor: '#1b2733',
      height: 36
    },
    webPreferences: {
      preload: path.join(__dirname, '../preload/preload.js'),
      contextIsolation: true,   // 需求 9.3
      sandbox: true,
      nodeIntegration: false
    }
  })

  if (process.env.ELECTRON_RENDERER_URL) {
    mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    mainWindow.loadFile(path.join(__dirname, '../renderer/index.html'))
  }
  // 渲染层 console 桥到 main.log（排障期监控，能直接看到渲染层真实报错）
  mainWindow.webContents.on('console-message', (...args: any[]) => {
    const d: any = args[1]
    if (d && typeof d === 'object' && 'message' in d) {
      log(`renderer[${d.level}]: ${String(d.message).slice(0, 300)}`)
    } else {
      log(`renderer: ${String(args[2] ?? '').slice(0, 300)} (src=${args[4] ?? '?'})`)
    }
  })
  mainWindow.webContents.once('did-finish-load', async () => {
    log(`did-finish-load url=${mainWindow?.webContents.getURL()}`)
    try {
      const probe = await mainWindow?.webContents.executeJavaScript(
        'JSON.stringify({hasKairos: !!window.kairos, port: window.kairos ? "ipc" : "none", href: location.href.slice(0, 60)})')
      log(`renderer probe: ${probe}`)
    } catch (e) {
      log(`renderer probe fail: ${String(e)}`)
    }
  })
  mainWindow.once('ready-to-show', () => {
    // 开机自启带 --hidden 参数：不弹主窗，托盘常驻
    if (!process.argv.includes('--hidden')) mainWindow?.show()
  })
  // 关窗口 = 隐藏到托盘（托盘「退出」才真正停机）
  mainWindow.on('close', (e) => {
    if (!shuttingDown) {
      e.preventDefault()
      mainWindow?.hide()
    }
  })
}

function createTray(): void {
  // v3 星芒图形标（32px tray.png）；缺文件时回落空图不崩
  let icon = nativeImage.createFromPath(path.join(resourcesDir(), 'tray.png'))
  if (icon.isEmpty()) icon = nativeImage.createEmpty()
  tray = new Tray(icon)
  tray.setToolTip('Kairos')
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '打开主控台', click: () => { mainWindow?.show(); mainWindow?.focus() } },
    { label: '召唤 / 收回桌宠', click: () => togglePetWindow() },
    { label: '暂停全部任务 (kill switch)', click: killSwitch },
    { label: '检查更新', click: () => { void shell.openExternal('https://github.com/SuWen799/Kairos/releases') } },
    { type: 'separator' },
    { label: '退出', click: () => { shuttingDown = true; app.quit() } }
  ]))
}

/** 托盘 kill switch：POST /api/runtime/stop（端点 M2 落地，先标记+审计） */
async function killSwitch(): Promise<void> {
  log('kill switch triggered')
  try {
    await fetch(`http://127.0.0.1:${backendPort}/api/runtime/stop`, { method: 'POST' })
  } catch (e) {
    log(`kill switch call failed: ${String(e)}`)
  }
}

// ── 桌宠（Codex Pets 式状态伴侣）────────────────────────
// 透明置顶无边框小窗，渲染层 ?view=pet 挂 PetWidget。
// 点击穿透：默认 setIgnoreMouseEvents(true,{forward:true})，渲染层 hover 到
// 本体包围盒时经 pet:hover 临时关穿透（Windows 必须 frame:false 才能真透明）。
function petPosPath(): string {
  return path.join(app.getPath('userData'), 'pet-pos.json')
}

function loadPetPos(): { x?: number; y?: number } {
  try { return JSON.parse(fs.readFileSync(petPosPath(), 'utf-8')) } catch { return {} }
}

function savePetPos(x: number, y: number): void {
  try { fs.writeFileSync(petPosPath(), JSON.stringify({ x, y })) } catch { /* 磁盘只读就算了 */ }
}

/** 桌宠开关记忆：下次启动按上次状态恢复（首次默认召唤） */
function petOpenPath(): string {
  return path.join(app.getPath('userData'), 'pet-open.json')
}

function loadPetOpen(): boolean {
  try { return JSON.parse(fs.readFileSync(petOpenPath(), 'utf-8')).open !== false } catch { return true }
}

function savePetOpen(open: boolean): void {
  try { fs.writeFileSync(petOpenPath(), JSON.stringify({ open })) } catch { /* 忽略 */ }
}

function petLoadUrl(): { url?: string; file?: string; query: Record<string, string> } {
  const query = { view: 'pet' }
  if (process.env.ELECTRON_RENDERER_URL) {
    return { url: process.env.ELECTRON_RENDERER_URL, query }
  }
  return { file: path.join(__dirname, '../renderer/index.html'), query }
}

function createPetWindow(): void {
  if (petWindow) return
  const pos = loadPetPos()
  const wa = screen.getPrimaryDisplay().workAreaSize
  petWindow = new BrowserWindow({
    width: 150,
    height: 185,
    x: pos.x ?? wa.width - 175,   // 默认右下角，留出任务栏
    y: pos.y ?? wa.height - 205,
    transparent: true,
    frame: false,                  // Windows 透明窗口必须无框，否则透明区发黑
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    hasShadow: false,              // 避免一圈灰影破坏"贴纸感"
    webPreferences: {
      preload: path.join(__dirname, '../preload/preload.js'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false
    }
  })
  petWindow.setAlwaysOnTop(true, 'screen-saver')  // 压过普通置顶窗口
  petWindow.setIgnoreMouseEvents(true, { forward: true })  // 默认穿透，hover 本体再收
  const target = petLoadUrl()
  if (target.url) void petWindow.loadURL(target.url + '/?view=pet')
  else void petWindow.loadFile(target.file!, { query: target.query })
  // 渲染层 console 桥到 main.log（桌宠窗口排障）
  petWindow.webContents.on('console-message', (...args: any[]) => {
    const d: any = args[1]
    const msg = d && typeof d === 'object' && 'message' in d ? d.message : String(args[2] ?? '')
    if (/error|fail|uncaught/i.test(msg)) log(`pet-renderer: ${msg.slice(0, 240)}`)
  })
  petWindow.webContents.once('did-finish-load', () => {
    log(`pet did-finish-load url=${petWindow?.webContents.getURL().slice(0, 80)}`)
  })
  petWindow.on('closed', () => { petWindow = null })
  savePetOpen(true)
  log(`pet window created pos=(${pos.x ?? '?'},${pos.y ?? '?'})`)
}

function closePetWindow(): void {
  const win = petWindow
  petWindow = null
  if (win) {
    const [x, y] = win.getPosition()
    savePetPos(x, y)
    win.close()
  }
  savePetOpen(false)
}

function togglePetWindow(): boolean {
  if (petWindow) { closePetWindow(); return false }
  createPetWindow()
  return true
}

// ── 优雅停机（设计 4.1：SIGTERM → 等退出 → 强杀）────────
async function gracefulShutdown(): Promise<void> {
  shuttingDown = true
  if (watchdogTimer) clearInterval(watchdogTimer)
  if (!sidecar) return
  log('graceful shutdown: stopping sidecar...')
  sidecar.kill()  // Windows 上 = TerminateProcess；SIGTERM 语义由 PyInstaller 包装处理
  await new Promise<void>((resolve) => {
    const t = setTimeout(() => { try { sidecar?.kill('SIGKILL') } catch { /* noop */ } resolve() }, 5000)
    sidecar?.once('exit', () => { clearTimeout(t); resolve() })
  })
  log('sidecar stopped')
}

// ── app 生命周期 ───────────────────────────────────────
// GPU 进程在远程桌面/嵌套 Electron 环境易崩（0xC0000005），禁用硬件加速保底
app.disableHardwareAcceleration()
app.commandLine.appendSwitch('in-process-gpu')  // GPU 并入主进程，规避独立 GPU 进程崩溃
app.commandLine.appendSwitch('no-sandbox')      // 嵌套 Electron（宿主=WorkBuddy）下 GPU 沙箱初始化失败

if (!app.requestSingleInstanceLock()) {   // 防多开（鸡大虾同款经验）
  app.quit()
} else {
  app.on('second-instance', () => {
    mainWindow?.show()
    mainWindow?.focus()
  })

  app.whenReady().then(async () => {
    startCrashReporter()
    void setupAutoUpdater()
    try {
      backendPort = await probeFreePort()
      sidecar = spawnSidecar(backendPort)
      const ok = await waitHealthy(backendPort, HEALTH_TIMEOUT_MS)
      if (!ok) log('sidecar health check timeout (UI 仍启动，watchdog 会重试)')
      startWatchdog()
      createWindow()
      createTray()
      // 桌宠：按上次开关状态恢复（首次默认召唤）
      if (loadPetOpen()) createPetWindow()
    } catch (e) {
      log(`startup failed: ${String(e)}`)
    }
  })

  app.on('before-quit', (e) => {
    if (!shuttingDown) {
      e.preventDefault()
      void gracefulShutdown().then(() => app.quit())
    }
  })

  app.on('window-all-closed', () => { /* 托盘常驻，不退出 */ })
}

// ── IPC：渲染层取后端端口（preload 经 contextBridge 暴露）──
ipcMain.handle('backend:info', () => ({ port: backendPort }))

// v2.5：弹窗态把 overlay 系统钮一起调暗（scrim 盖不住非客户区）
ipcMain.handle('titlebar:overlay', (_e, opts: { color?: string; symbolColor?: string }) => {
  try {
    mainWindow?.setTitleBarOverlay(opts)
  } catch (e) {
    log(`setTitleBarOverlay failed: ${String(e)}`)
  }
})

// ── 桌宠 IPC ───────────────────────────────────────────
ipcMain.handle('pet:toggle', () => togglePetWindow())
ipcMain.handle('pet:open-main', () => {
  mainWindow?.show()
  mainWindow?.focus()
})

// ── 开机自启（默认关；设置界面开关）─────────────────────
ipcMain.handle('app:autostart', (_e, on?: boolean) => {
  if (typeof on === 'boolean') {
    app.setLoginItemSettings({ openAtLogin: on, args: ['--hidden'] })
  }
  return app.getLoginItemSettings().openAtLogin
})

/** hover 本体包围盒 → 关穿透收事件；离开 → 恢复穿透（forward:true 持续转发 mousemove） */
ipcMain.on('pet:hover', (e, hit: boolean) => {
  const win = BrowserWindow.fromWebContents(e.sender) ?? petWindow
  if (!win || win.isDestroyed()) return
  try { win.setIgnoreMouseEvents(!hit, { forward: true }) } catch { /* 窗口正关 */ }
})

/** 自实现拖拽（不用 -webkit-app-region：它会吞 mousemove，与穿透切换冲突） */
ipcMain.on('pet:drag-start', () => {
  if (!petWindow) return
  const cur = screen.getCursorScreenPoint()
  const [wx, wy] = petWindow.getPosition()
  petDragOffset = { x: cur.x - wx, y: cur.y - wy }
})

ipcMain.on('pet:drag-move', () => {
  if (!petWindow) return
  const cur = screen.getCursorScreenPoint()
  const wa = screen.getPrimaryDisplay().workArea
  const x = Math.min(Math.max(cur.x - petDragOffset.x, wa.x - 40), wa.x + wa.width - 110)
  const y = Math.min(Math.max(cur.y - petDragOffset.y, wa.y - 8), wa.y + wa.height - 95)
  petWindow.setPosition(x, y)
})

ipcMain.on('pet:drag-end', () => {
  if (!petWindow) return
  const [x, y] = petWindow.getPosition()
  savePetPos(x, y)
})
