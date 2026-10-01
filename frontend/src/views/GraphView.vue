<template>
  <div>
    <div class="panel">
      <div class="panel__head">
        <div class="panel__title">知识图谱统计</div>
        <div style="display: flex; gap: 8px">
          <el-button size="small" @click="loadStats">刷新</el-button>
        </div>
      </div>
      <div class="metric-grid">
        <div class="metric">
          <div class="metric__label">图谱状态</div>
          <div class="metric__value" style="font-size: 18px">
            {{ stats?.available ? '已连接' : '不可用' }}
          </div>
          <div class="metric__foot">Neo4j 5.x · {{ stats?.available ? '图谱增强已启用' : '检索层自动跳过图谱扩展' }}</div>
        </div>
        <div class="metric">
          <div class="metric__label">条款节点</div>
          <div class="metric__value">{{ stats?.clauses ?? 0 }}</div>
          <div class="metric__foot">Clause 节点总数</div>
        </div>
        <div class="metric">
          <div class="metric__label">规范节点</div>
          <div class="metric__value">{{ stats?.specs ?? 0 }}</div>
          <div class="metric__foot">Spec 节点总数</div>
        </div>
        <div class="metric">
          <div class="metric__label">引用关系</div>
          <div class="metric__value">{{ stats?.references ?? 0 }}</div>
          <div class="metric__foot">REFERENCES 边总数</div>
        </div>
      </div>
    </div>

    <div style="display: grid; grid-template-columns: 360px 1fr; gap: 16px; align-items: start">
      <section class="panel">
        <div class="panel__head"><div class="panel__title">图谱构建</div></div>
        <el-form label-position="top" size="small">
          <el-form-item label="选择规范文档">
            <el-select v-model="docId" placeholder="请选择" filterable style="width: 100%">
              <el-option
                v-for="doc in docs"
                :key="doc.id"
                :label="`${doc.spec_code} ${doc.spec_name}`"
                :value="doc.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="抽取并发度">
            <el-slider v-model="concurrency" :min="1" :max="8" show-input />
          </el-form-item>
          <el-button
            type="primary"
            style="width: 100%"
            :loading="extracting"
            :disabled="!docId || !auth.canWriteKb"
            @click="extract"
          >
            执行实体关系抽取
          </el-button>
          <div class="small muted" style="margin-top: 10px; line-height: 1.8">
            抽取会调用大模型解析每条条款的实体与关系，并写入 Neo4j 与 clause_ref 冗余索引。
            条款较多时耗时较长，请在任务队列空闲时执行。
          </div>
        </el-form>
      </section>

      <section class="panel">
        <div class="panel__head">
          <div class="panel__title">条款引用链追踪</div>
          <div class="panel__hint">正向（引用他人）/ 反向（被引用）</div>
        </div>
        <div class="toolbar">
          <el-input v-model="clauseNo" placeholder="条款号，如 5.2.3" style="width: 180px" @keyup.enter="trace" />
          <el-select v-model="direction" style="width: 130px">
            <el-option label="双向" value="both" />
            <el-option label="正向引用" value="out" />
            <el-option label="反向被引用" value="in" />
          </el-select>
          <el-select v-model="depth" style="width: 120px">
            <el-option v-for="d in [1, 2, 3, 4, 5]" :key="d" :label="`深度 ${d}`" :value="d" />
          </el-select>
          <el-button type="primary" :loading="tracing" @click="trace">追踪</el-button>
        </div>

        <div v-if="chain">
          <div class="small muted" style="margin-bottom: 8px">
            根节点 {{ chain.clause_no }} · 关联 {{ chain.nodes.length }} 个条款 · 边 {{ chain.edges.length }} 条
            <span v-if="chain.available === false"> · 图谱不可用，以下为数据库冗余索引结果</span>
          </div>
          <el-table :data="chain.edges" size="small" empty-text="未找到关联条款" max-height="360">
            <el-table-column label="来源" width="110">
              <template #default="{ row }"><span class="mono">{{ row.source || '-' }}</span></template>
            </el-table-column>
            <el-table-column label="关系" width="150">
              <template #default="{ row }">
                <span class="tag tag--info">{{ (row.relations || []).join(' / ') || '-' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="目标" width="110">
              <template #default="{ row }"><span class="mono">{{ row.target || '-' }}</span></template>
            </el-table-column>
            <el-table-column label="规范" width="130">
              <template #default="{ row }"><span class="small">{{ row.spec_code || '-' }}</span></template>
            </el-table-column>
            <el-table-column label="置信度" width="90">
              <template #default="{ row }">
                <span class="mono small">{{ row.confidence ?? '-' }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <div v-else class="empty">输入条款号后点击「追踪」查看引用链</div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, kbApi, type SpecDoc } from '@/api'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const auth = useAuthStore()

interface GraphStats {
  available: boolean
  clauses?: number
  specs?: number
  references?: number
}

interface ChainResult {
  clause_no: string
  available?: boolean
  nodes: Record<string, unknown>[]
  edges: { source?: string; target?: string; relations?: string[]; spec_code?: string; confidence?: number }[]
}

const stats = ref<GraphStats | null>(null)
const docs = ref<SpecDoc[]>([])
const docId = ref<number | null>(null)
const concurrency = ref(4)
const extracting = ref(false)

const clauseNo = ref('')
const direction = ref('both')
const depth = ref(3)
const tracing = ref(false)
const chain = ref<ChainResult | null>(null)

async function loadStats() {
  try {
    stats.value = (await kbApi.kgStats()) as unknown as GraphStats
  } catch {
    stats.value = { available: false }
  }
}

async function extract() {
  if (!docId.value) return
  extracting.value = true
  try {
    const result = (await kbApi.extractKg(docId.value, concurrency.value)) as Record<string, unknown>
    const extraction = (result.extraction ?? {}) as Record<string, number>
    ElMessage.success(
      `抽取完成：条款 ${extraction.chunks ?? 0} 条，实体 ${extraction.entities ?? 0} 个，` +
        `引用关系 ${extraction.references ?? 0} 条，文档内链接 ${result.intra_doc_links ?? 0} 条`,
    )
    await loadStats()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '抽取失败')
  } finally {
    extracting.value = false
  }
}

async function trace() {
  if (!clauseNo.value.trim()) {
    ElMessage.warning('请输入条款号')
    return
  }
  tracing.value = true
  try {
    chain.value = await api.get<ChainResult>(
      `/kb/kg/clauses/${encodeURIComponent(clauseNo.value.trim())}/refs`,
      { direction: direction.value, depth: depth.value },
    )
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '追踪失败')
  } finally {
    tracing.value = false
  }
}

onMounted(async () => {
  await loadStats()
  try {
    const list = await kbApi.listDocs({ page: 1, page_size: 100 })
    docs.value = list.items
    const preset = route.query.doc ? Number(route.query.doc) : null
    docId.value = preset ?? docs.value[0]?.id ?? null
  } catch {
    /* 忽略 */
  }
})
</script>
