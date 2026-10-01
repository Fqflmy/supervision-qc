<template>
  <div class="markdown-body" v-html="html"></div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js/lib/common'

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
  return marked.parse(raw) as string
})
</script>
