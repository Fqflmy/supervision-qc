<template>
  <div style="display: grid; grid-template-columns: 1fr 320px; gap: 16px; align-items: start">
    <section class="panel" style="display: flex; flex-direction: column; min-height: calc(100vh - 150px)">
      <div class="panel__head">
        <div class="panel__title">规范智能问答</div>
        <div class="panel__hint">
          严格基于检索到的条款作答，每条结论标注引用依据；检索不到时明确告知而非推测
        </div>
      </div>

      <div class="chat-thread" ref="threadRef">
        <div v-if="!messages.length" class="empty" style="padding: 40px 0">
          <div style="font-size: 13px; margin-bottom: 12px">输入工程规范相关问题，例如：</div>
          <div style="display: flex; flex-wrap: wrap; gap: 8px; justify-content: center">
            <el-tag
              v-for="item in suggestions"
              :key="item"
              effect="plain"
              style="cursor: pointer"
              @click="ask(item)"
            >
              {{ item }}
            </el-tag>
          </div>
        </div>

        <div
          v-for="(msg, index) in messages"
          :key="index"
          class="chat-msg"
          :class="msg.role === 'user' ? 'chat-msg--user' : 'chat-msg--assistant'"
        >
          <div class="chat-avatar">{{ msg.role === 'user' ? '我' : 'AI' }}</div>
          <div style="max-width: 82%">
            <div class="chat-bubble">{{ msg.text }}</div>

            <template v-if="msg.citations?.length">
              <div style="margin-top: 10px">
                <div class="small muted" style="margin-bottom: 6px">
                  引用依据（{{ msg.citations.length }} 条）
                </div>
                <div v-for="cite in msg.citations" :key="cite.chunk_id" class="citation">
                  <div class="citation__head">
                    <span class="tag tag--info">依据{{ cite.index }}</span>
                    <span>{{ cite.spec_code }} {{ cite.spec_name }}</span>
                  </div>
                  <div class="citation__body">{{ cite.content }}</div>
                  <div class="citation__meta">
                    相关度 {{ cite.relevance_score?.toFixed(3) }} · 分块 #{{ cite.chunk_id }}
                  </div>
                </div>
              </div>
            </template>

            <div v-if="msg.meta" class="small muted" style="margin-top: 6px">{{ msg.meta }}</div>
          </div>
        </div>

        <div v-if="loading" class="chat-msg chat-msg--assistant">
          <div class="chat-avatar">AI</div>
          <div class="chat-bubble"><el-icon class="is-loading"><Loading /></el-icon> 正在检索规范条款并生成回答…</div>
        </div>
      </div>

      <div style="margin-top: auto; padding-top: 14px">
        <el-input
          v-model="query"
          type="textarea"
          :rows="3"
          resize="none"
          placeholder="请输入问题（Ctrl + Enter 发送），例如：混凝土浇筑入模温度有什么要求？"
          @keydown.ctrl.enter.prevent="ask()"
        />
        <div style="display: flex; align-items: center; gap: 10px; margin-top: 10px">
          <span class="small muted">Ctrl + Enter 发送</span>
          <div class="toolbar__spacer"></div>
          <el-button v-if="messages.length" size="small" @click="clear">清空对话</el-button>
          <el-button type="primary" :loading="loading" :disabled="!query.trim()" @click="ask()">发送</el-button>
        </div>
      </div>
    </section>

    <aside>
      <section class="panel">
        <div class="panel__head"><div class="panel__title">检索设置</div></div>
        <el-form label-position="top" size="small">
          <el-form-item label="知识库">
            <el-select v-model="kbIds" multiple collapse-tags placeholder="全部知识库" style="width: 100%">
              <el-option v-for="kb in kbs" :key="kb.id" :label="kb.name" :value="kb.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="保留条款数">
            <el-slider v-model="topK" :min="3" :max="20" show-input />
          </el-form-item>
        </el-form>
      </section>

      <section class="panel" v-if="lastDebug">
        <div class="panel__head"><div class="panel__title">最近一次检索链路</div></div>
        <div class="small">
          <div style="margin-bottom: 8px">
            <span class="muted">子查询：</span>
            <div v-for="(q, i) in lastDebug.subQueries" :key="i" class="mono" style="margin-top: 3px">
              {{ i + 1 }}. {{ q }}
            </div>
          </div>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="意图">{{ lastDebug.intent }}</el-descriptions-item>
            <el-descriptions-item label="双通道召回">{{ lastDebug.channels }}</el-descriptions-item>
            <el-descriptions-item label="RRF 融合">{{ lastDebug.fused }}</el-descriptions-item>
            <el-descriptions-item label="图谱扩展">{{ lastDebug.kgExpanded }}</el-descriptions-item>
            <el-descriptions-item label="耗时">{{ lastDebug.latency }} ms</el-descriptions-item>
          </el-descriptions>
        </div>
      </section>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { kbApi, retrievalApi, type ChatResult, type KnowledgeBase } from '@/api'

interface Message {
  role: 'user' | 'assistant'
  text: string
  citations?: ChatResult['citations']
  meta?: string
}

const query = ref('')
const loading = ref(false)
const messages = ref<Message[]>([])
const threadRef = ref<HTMLElement | null>(null)
const kbs = ref<KnowledgeBase[]>([])
const kbIds = ref<number[]>([])
const topK = ref(8)
const lastDebug = ref<{
  subQueries: string[]
  intent: string
  channels: string
  fused: number
  kgExpanded: number
  latency: number
} | null>(null)

const suggestions = [
  '混凝土浇筑入模温度有什么要求？',
  '脚手架连墙件如何设置？',
  '灌注桩沉渣厚度要求是多少？',
  '检验批质量验收合格的标准是什么？',
  '脚手架扣件螺栓拧紧扭力矩要求？',
]

async function ask(preset?: string) {
  const text = (preset ?? query.value).trim()
  if (!text || loading.value) return

  messages.value.push({ role: 'user', text })
  query.value = ''
  loading.value = true
  await scrollToBottom()

  try {
    const result = await retrievalApi.chat({
      query: text,
      kb_ids: kbIds.value.length ? kbIds.value : undefined,
      top_k: topK.value,
    })

    messages.value.push({
      role: 'assistant',
      text: result.answer,
      citations: result.citations,
      meta: `检索耗时 ${result.latency_ms} ms · 子查询 ${result.sub_queries.length} 个${
        result.no_evidence ? ' · 未检索到依据' : ''
      }`,
    })

    // 同时拉取一次结构化的检索明细，展示链路指标
    const detail = await retrievalApi.search({
      query: text,
      kb_ids: kbIds.value.length ? kbIds.value : undefined,
      top_k: topK.value,
    })
    const recall = (detail.debug?.recall ?? {}) as unknown as Record<string, unknown>
    const channels = (recall.channels ?? {}) as Record<string, number>
    lastDebug.value = {
      subQueries: detail.sub_queries,
      intent: String((detail.understanding?.intent as string) ?? '-'),
      channels: `BM25 ${channels.bm25 ?? 0} / 向量 ${channels.dense ?? 0}`,
      fused: Number(recall.fused ?? 0),
      kgExpanded: Number(detail.debug?.kg_expanded ?? 0),
      latency: detail.latency_ms,
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '问答失败')
    messages.value.push({ role: 'assistant', text: '请求失败，请检查后端服务与模型配置。' })
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}

function clear() {
  messages.value = []
  lastDebug.value = null
}

async function scrollToBottom() {
  await nextTick()
  const el = threadRef.value?.parentElement
  if (el) el.scrollTop = el.scrollHeight
}

const hasCitations = computed(() => messages.value.some((m) => m.citations?.length))

onMounted(async () => {
  try {
    kbs.value = await kbApi.listKbs()
  } catch {
    /* 知识库列表失败不阻塞问答 */
  }
  void hasCitations
})
</script>
