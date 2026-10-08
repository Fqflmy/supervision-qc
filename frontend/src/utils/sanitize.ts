/**
 * HTML 净化工具。
 *
 * 为什么需要
 * ----------
 * 评估报告通过 `v-html` 渲染 Markdown，而报告内容由大模型基于**用户上传的规范文档**
 * 生成。若不做净化，攻击者可构造含 `<script>` 或 `<img onerror=...>` 的 DOCX，
 * 经入库 → 生成报告 → 管理员打开报告页即执行，形成典型的**存储型 XSS**，
 * 且触发者是权限最高的管理员。
 *
 * `marked` 自 v5 起已移除内置净化（官方提示由调用方负责消毒），
 * 因此这里用 DOMPurify 做白名单净化。
 *
 * 白名单策略
 * ----------
 * 只放开报告实际需要的排版标签与属性；显式禁止 `<script>`、`<iframe>`、
 * `<object>`、`<embed>`、`<form>` 以及所有 `on*` 事件属性。
 * 链接允许 `http/https/mailto`，避免 `javascript:` 伪协议。
 */
import DOMPurify from 'dompurify'

/** 允许保留的标签（报告排版所需） */
const ALLOWED_TAGS = [
  // 结构
  'p', 'div', 'span', 'br', 'hr',
  // 标题
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  // 强调
  'strong', 'b', 'em', 'i', 'u', 's', 'del', 'mark', 'sub', 'sup',
  // 列表
  'ul', 'ol', 'li', 'dl', 'dt', 'dd',
  // 表格（报告含大量逐条比对表）
  'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col',
  // 代码
  'pre', 'code', 'kbd', 'samp',
  // 引用与链接
  'blockquote', 'a',
]

/** 允许保留的属性 */
const ALLOWED_ATTR = ['href', 'title', 'target', 'rel', 'colspan', 'rowspan', 'class']

/** 报告里用于语法高亮的 class 前缀（highlight.js 输出） */
const ALLOWED_URI_REGEXP = /^(?:(?:https?|mailto|tel):|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i

/**
 * 净化 HTML 字符串。
 *
 * @param dirty 待净化的 HTML
 * @returns 安全的 HTML，可直接用于 v-html
 */
export function sanitizeHtml(dirty: string): string {
  if (!dirty) return ''
  return DOMPurify.sanitize(dirty, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOWED_URI_REGEXP,
    // 禁止数据属性与自定义协议，避免绕过
    ALLOW_DATA_ATTR: false,
    // 强制所有链接新开页面时带上 rel，防止 reverse tabnabbing
    ADD_ATTR: ['target'],
    FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'form', 'input', 'button'],
    FORBID_ATTR: ['style', 'srcset', 'formaction', 'xlink:href'],
  })
}

/**
 * 给净化后的 HTML 中的外链补上 `target="_blank" rel="noopener noreferrer"`。
 *
 * 比在 DOMPurify 里做 hook 更简单，且便于单元测试。
 */
export function hardenLinks(html: string): string {
  return html.replace(
    /<a\s+([^>]*?)href=(["'])(.*?)\2([^>]*)>/gi,
    (match, before, quote, href, after) => {
      if (/^https?:\/\//i.test(href)) {
        const attrs = `${before} ${after}`.replace(/\s+/g, ' ')
        const hasTarget = /target=/i.test(attrs)
        const hasRel = /rel=/i.test(attrs)
        const extra = [
          hasTarget ? '' : 'target="_blank"',
          hasRel ? '' : 'rel="noopener noreferrer"',
        ]
          .filter(Boolean)
          .join(' ')
        return `<a ${before}href=${quote}${href}${quote}${after}${extra ? ' ' + extra : ''}>`
      }
      return match
    },
  )
}

/** 净化 + 加固外链，供组件直接调用 */
export function renderSafeHtml(html: string): string {
  return hardenLinks(sanitizeHtml(html))
}
