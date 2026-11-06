/** 后端客户端：
 * - Electron 内：preload 暴露端口，直连 127.0.0.1:port
 * - 手机/PWA（M3）：同源（/app 托管），带 LAN token（localStorage.kairos_token）
 */
let _port = null

function lanToken() {
  try { return localStorage.getItem('kairos_token') || '' } catch { return '' }
}

function withToken(url) {
  const t = lanToken()
  if (!t) return url
  return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(t)
}

export async function backendPort() {
  if (_port) return _port
  if (window.kairos?.backendPort) {
    _port = await window.kairos.backendPort()
  } else {
    // 浏览器模式：同源（sidecar /app 托管）；file:// 或其它端口回落 8626
    _port = location.protocol.startsWith('http') && location.hostname !== '127.0.0.1'
      ? location.port || 80
      : (location.port ? Number(location.port) : 8626)
    if (location.hostname === '127.0.0.1' || location.hostname === 'localhost') {
      _port = location.port ? Number(location.port) : 8626
    }
  }
  return _port
}

async function baseUrl() {
  const port = await backendPort()
  // Electron 桌面壳：file:// 下 location.hostname 为空，必须走 IPC 拿到的端口直连
  // （旧逻辑按 hostname 分支 → file:// 时 origin 落回 "file://" → fetch 根本不上网）
  if (window.kairos?.backendPort) return `http://127.0.0.1:${port}`
  const isLocal = location.hostname === '127.0.0.1' || location.hostname === 'localhost'
  return isLocal ? `http://127.0.0.1:${port}` : location.origin
}

export async function apiGet(path) {
  const base = await baseUrl()
  const r = await fetch(withToken(base + path))
  if (!r.ok) throw new Error(`${path}: ${r.status}`)
  return r.json()
}

export async function apiPost(path, body) {
  const base = await baseUrl()
  const r = await fetch(withToken(base + path), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
  if (!r.ok) throw new Error(`${path}: ${r.status}`)
  return r.json()
}

export async function apiPatch(path, body) {
  const base = await baseUrl()
  const r = await fetch(withToken(base + path), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
  if (!r.ok) throw new Error(`${path}: ${r.status}`)
  return r.json()
}

export async function apiPut(path, body) {
  const base = await baseUrl()
  const r = await fetch(withToken(base + path), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
  if (!r.ok) throw new Error(`${path}: ${r.status}`)
  return r.json()
}

export async function apiDelete(path) {
  const base = await baseUrl()
  const r = await fetch(withToken(base + path), { method: 'DELETE' })
  if (!r.ok) throw new Error(`${path}: ${r.status}`)
  return r.json()
}

/** multipart 上传（附件 / 语音） */
export async function apiUpload(path, file) {
  const base = await baseUrl()
  const fd = new FormData()
  fd.append('file', file)
  const r = await fetch(withToken(base + path), { method: 'POST', body: fd })
  if (!r.ok) {
    const t = await r.text().catch(() => '')
    throw new Error(`${path}: ${r.status} ${t.slice(0, 160)}`)
  }
  return r.json()
}

/**
 * SSE 流式对话：POST /api/chat/stream。
 * onEvent({type, ...}) 逐事件回调；结束（done/error）resolve。
 */
export async function streamChat(sessionId, text, onEvent) {
  const base = await baseUrl()
  const r = await fetch(withToken(base + '/api/chat/stream'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, text })
  })
  if (!r.ok || !r.body) throw new Error(`chat/stream: ${r.status}`)

  const reader = r.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    // 事件帧以空行分隔；每帧 event: <type>\ndata: <json>
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      let type = 'message'
      let data = {}
      for (const line of frame.split('\n')) {
        if (line.startsWith('event: ')) type = line.slice(7).trim()
        else if (line.startsWith('data: ')) {
          try { data = JSON.parse(line.slice(6)) } catch { data = { raw: line.slice(6) } }
        }
      }
      if (type === 'done') return
      onEvent({ type, ...data })
    }
  }
}
