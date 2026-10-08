<template>
  <div class="markdown-body" v-html="html"></div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js/lib/common'
import { renderSafeHtml } from '../utils/sanitize'

const props = defineProps<{ content?: string | null }>()

marked.setOptions({
  gfm: true,
  breaks: false,
  // 代码高亮交给 highlight.js，避免自定义 renderer 的类型兼容问题
  highlight(code: string, lang: string) {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value
    }
    return hljs.highlightAuto(code).value
  },
} as never)

const html = computed(() => {
  const raw = props.content || ''
  if (!raw) return '<p class="muted">暂无内容</p>'
  // 必须净化：报告内容由大模型基于用户上传的文档生成，
  // 未净化时恶意文档可注入脚本，形成存储型 XSS（详见 utils/sanitize.ts）
  return renderSafeHtml(marked.parse(raw) as string)
})
</script>
