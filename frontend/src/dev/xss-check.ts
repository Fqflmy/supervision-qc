/**
 * XSS 净化验证页（仅用于本地/CI 验证，不参与应用路由）。
 *
 * 为什么需要它
 * ------------
 * `src/utils/sanitize.ts` 是修复存储型 XSS 的核心。此前尝试用 vitest + jsdom
 * 做单测，但本机受限环境会静默丢弃 npm 的落盘结果（"added N packages" 后
 * node_modules 中并无文件），无法安装测试框架。
 *
 * 因此改为在**真实浏览器**中验证：本页把一组攻击载荷经 marked 渲染 + 净化后
 * 写入 DOM，再由 Playwright 读取 window.__xssProbe 判定是否被中和。
 * 这比 jsdom 更接近真实攻击面（jsdom 不执行脚本，看不到实际执行效果）。
 */
import { marked } from 'marked'
import { renderSafeHtml } from '../utils/sanitize'

interface ProbeResult {
  name: string
  payload: string
  html: string
  /** 净化后是否仍残留可执行/危险内容 */
  leaked: boolean
  /** 泄露原因 */
  reason: string
}

const PAYLOADS: Array<{ name: string; payload: string }> = [
  { name: 'script 标签', payload: '<script>window.__xssFired = true</script>' },
  { name: 'img onerror', payload: '<img src=x onerror="window.__xssFired = true">' },
  { name: 'svg onload', payload: '<svg onload="window.__xssFired = true"></svg>' },
  { name: 'iframe', payload: '<iframe src="javascript:window.__xssFired=true"></iframe>' },
  { name: 'javascript 伪协议', payload: '<a href="javascript:window.__xssFired=true">点我</a>' },
  { name: 'body onload', payload: '<body onload="window.__xssFired = true">' },
  { name: '事件属性 onclick', payload: '<div onclick="window.__xssFired = true">点我</div>' },
  { name: 'style 表达式', payload: '<style>body{background:url("javascript:alert(1)")}</style>' },
  { name: 'object/embed', payload: '<object data="data:text/html,<script>alert(1)</script>"></object>' },
  { name: 'form 表单劫持', payload: '<form action="//evil.example"><input name="x"></form>' },
  { name: '大写下标绕过', payload: '<IMG SRC=x ONERROR="window.__xssFired = true">' },
  { name: '嵌套混淆', payload: '<scr<script>ipt>window.__xssFired = true</scr</script>ipt>' },
  { name: 'Markdown 内嵌脚本', payload: '正文内容\n\n<script>window.__xssFired = true</script>\n\n后续段落' },
  { name: 'Markdown 链接注入', payload: '[正常链接](javascript:window.__xssFired=true)' },
]

/** 判定净化后的 HTML 是否仍含危险内容 */
function inspect(html: string): { leaked: boolean; reason: string } {
  const lowered = html.toLowerCase()
  if (/<script\b/.test(lowered)) return { leaked: true, reason: '残留 <script>' }
  if (/<iframe\b/.test(lowered)) return { leaked: true, reason: '残留 <iframe>' }
  if (/<object\b|<embed\b/.test(lowered)) return { leaked: true, reason: '残留 object/embed' }
  if (/<form\b|<input\b/.test(lowered)) return { leaked: true, reason: '残留 form/input' }
  if (/\son[a-z]+\s*=/.test(lowered)) return { leaked: true, reason: '残留 on* 事件属性' }
  if (/javascript\s*:/i.test(lowered)) return { leaked: true, reason: '残留 javascript: 伪协议' }
  if (/<style\b/.test(lowered)) return { leaked: true, reason: '残留 <style>' }
  return { leaked: false, reason: '' }
}

const container = document.getElementById('probe-host')!
const results: ProbeResult[] = []

for (const { name, payload } of PAYLOADS) {
  const rendered = marked.parse(payload) as string
  const safe = renderSafeHtml(rendered)
  const { leaked, reason } = inspect(safe)
  results.push({ name, payload, html: safe, leaked, reason })
  const node = document.createElement('div')
  node.className = 'probe-case'
  node.setAttribute('data-name', name)
  node.innerHTML = safe // 这里模拟组件里的 v-html
  container.appendChild(node)
}

// 正常 Markdown 必须仍然可用（防止净化过度）
const normalMarkdown = [
  '# 标题一',
  '',
  '**加粗** 与 *斜体*，以及 `行内代码`。',
  '',
  '- 列表项 A',
  '- 列表项 B',
  '',
  '| 条款 | 结论 |',
  '| --- | --- |',
  '| 5.3.3 | 不符合 |',
  '',
  '> 引用块',
  '',
  '```python',
  'print("hello")',
  '```',
  '',
  '[外链](https://example.com)',
].join('\n')
const normalHtml = renderSafeHtml(marked.parse(normalMarkdown) as string)
const normalNode = document.getElementById('normal-host')!
normalNode.innerHTML = normalHtml

// 把结果交给 Playwright 读取
;(window as unknown as Record<string, unknown>).__xssProbe = {
  results,
  xssFired: (window as unknown as Record<string, unknown>).__xssFired === true,
  normalHtml,
  normalChecks: {
    hasHeading: /<h1[^>]*>/.test(normalHtml),
    hasTable: /<table[^>]*>/.test(normalHtml),
    hasCodeBlock: /<pre[^>]*>|<code[^>]*>/.test(normalHtml),
    hasList: /<li[^>]*>/.test(normalHtml),
    hasBlockquote: /<blockquote[^>]*>/.test(normalHtml),
    hasAnchor: /<a [^>]*href="https:\/\/example\.com"/.test(normalHtml),
  },
}
