/** Markdown 渲染（v2.0 R2）：markdown-it + highlight.js。
 *  安全：html:false（LLM 输出的 HTML 标签一律按文本显示）；链接统一新窗+noopener。
 *  流式安全：尾部未闭合的 ``` 渲染前临时补闭合，防整段闪烁。
 *  代码块包一层头部栏（语言标签 + 复制按钮），复制走全局事件委托。 */
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/common'
import 'highlight.js/styles/github.css'

function highlight(code, lang) {
  try {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value
    }
    return hljs.highlightAuto(code).value
  } catch {
    return '' // 让 markdown-it 走默认 escape
  }
}

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  highlight
})

// 链接：新窗口打开 + 防 Reverse Tabnabbing
const defaultLinkOpen = md.renderer.rules.link_open ||
  ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options))
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, idx, options, env, self)
}

// 代码块：包头部栏（语言 + 复制按钮）
md.renderer.rules.fence = (tokens, idx, options, env, self) => {
  const token = tokens[idx]
  const lang = (token.info || '').trim().split(/\s+/)[0] || 'text'
  const inner = options.highlight
    ? options.highlight(token.content, lang) || md.utils.escapeHtml(token.content)
    : md.utils.escapeHtml(token.content)
  return `<div class="codeblock">` +
    `<div class="codebar"><span class="code-lang">${md.utils.escapeHtml(lang)}</span>` +
    `<button class="code-copy" type="button">复制</button></div>` +
    `<pre><code class="hljs language-${md.utils.escapeHtml(lang)}">${inner}</code></pre></div>`
}

/** 补闭合流式中的未结束代码块再渲染 */
export function renderMd(text) {
  if (!text) return ''
  let src = String(text)
  const fences = (src.match(/^\s{0,3}```/gm) || []).length
  if (fences % 2 === 1) src += '\n```'
  return md.render(src)
}

export default md
