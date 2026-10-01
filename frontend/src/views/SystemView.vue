<template>
  <div v-loading="loading">
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start">
      <section class="panel">
        <div class="panel__head">
          <div class="panel__title">服务健康</div>
          <el-button size="small" @click="loadHealth">刷新</el-button>
        </div>
        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item label="应用">{{ health?.app }} v{{ health?.version }}</el-descriptions-item>
          <el-descriptions-item label="环境">{{ health?.environment }}</el-descriptions-item>
          <el-descriptions-item label="总体状态">
            <span class="tag" :class="health?.status === 'ok' ? 'tag--ok' : 'tag--warn'">
              {{ health?.status === 'ok' ? '正常' : '降级' }}
            </span>
          </el-descriptions-item>
        </el-descriptions>

        <div style="margin-top: 12px">
          <div v-for="item in componentRows" :key="item.name" class="component-row">
            <div style="display: flex; align-items: center; gap: 8px">
              <span class="tag" :class="item.ok ? 'tag--ok' : 'tag--warn'">{{ item.ok ? '正常' : '降级' }}</span>
              <span class="small">{{ item.name }}</span>
            </div>
            <div class="mono small muted" style="text-align: right; max-width: 60%">{{ item.detail }}</div>
          </div>
        </div>
      </section>

      <section class="panel">
        <div class="panel__head">
          <div class="panel__title">运行指标</div>
          <el-button size="small" :loading="rebuilding" @click="rebuild">重建检索索引</el-button>
        </div>
        <div class="metric-grid">
          <div class="metric">
            <div class="metric__label">文档 / 分块</div>
            <div class="metric__value" style="font-size: 20px">
              {{ metrics?.documents_total ?? 0 }} / {{ metrics?.chunks_total ?? 0 }}
            </div>
          </div>
          <div class="metric">
            <div class="metric__label">累计 Token</div>
            <div class="metric__value" style="font-size: 20px">
              {{ (metrics?.tokens_total ?? 0).toLocaleString() }}
            </div>
          </div>
        </div>
        <div style="margin-top: 14px">
          <div class="panel__hint" style="margin-bottom: 8px">任务状态分布</div>
          <el-table :data="taskStateRows" size="small" max-height="200">
            <el-table-column label="状态" width="140">
              <template #default="{ row }">
                <span class="tag" :class="`tag--${stateTone(row.state)}`">{{ stateLabel(row.state) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="count" label="数量" align="right">
              <template #default="{ row }"><span class="mono">{{ row.count }}</span></template>
            </el-table-column>
          </el-table>
        </div>
      </section>
    </div>

    <section class="panel">
      <div class="panel__head">
        <div class="panel__title">运行配置（脱敏）</div>
        <el-button size="small" @click="loadConfig">刷新</el-button>
      </div>
      <el-tabs v-model="configTab">
        <el-tab-pane v-for="(section, name) in config" :key="name" :label="sectionLabel(String(name))" :name="String(name)">
          <el-descriptions :column="2" size="small" border>
            <el-descriptions-item v-for="(value, key) in section" :key="key" :label="String(key)">
              <span class="mono">{{ formatValue(value) }}</span>
            </el-descriptions-item>
          </el-descriptions>
        </el-tab-pane>
      </el-tabs>
    </section>

    <section class="panel">
      <div class="panel__head">
        <div class="panel__title">审计日志</div>
        <div class="toolbar" style="margin: 0">
          <el-select v-model="auditAction" placeholder="全部动作" clearable style="width: 150px" @change="loadAudit">
            <el-option v-for="(label, value) in AUDIT_ACTION_LABELS" :key="value" :label="label" :value="value" />
          </el-select>
          <el-button size="small" @click="loadAudit">刷新</el-button>
        </div>
      </div>
      <el-table :data="auditLogs" size="small" empty-text="暂无审计记录">
        <el-table-column label="时间" width="160">
          <template #default="{ row }"><span class="small mono">{{ fmtTime(row.created_at, 'MM-DD HH:mm:ss') }}</span></template>
        </el-table-column>
        <el-table-column label="操作人" width="110">
          <template #default="{ row }">{{ row.username || '-' }}</template>
        </el-table-column>
        <el-table-column label="动作" width="120">
          <template #default="{ row }">
            <span class="tag tag--info">{{ AUDIT_ACTION_LABELS[row.action] ?? row.action }}</span>
          </template>
        </el-table-column>
        <el-table-column label="对象" width="150">
          <template #default="{ row }">
            <span class="small">{{ row.object_type || '-' }}<span v-if="row.object_id"> #{{ row.object_id }}</span></span>
          </template>
        </el-table-column>
        <el-table-column label="结果" width="90">
          <template #default="{ row }">
            <span class="tag" :class="row.result === 'success' ? 'tag--ok' : 'tag--warn'">{{ row.result }}</span>
          </template>
        </el-table-column>
        <el-table-column label="IP" width="130">
          <template #default="{ row }"><span class="mono small">{{ row.ip || '-' }}</span></template>
        </el-table-column>
        <el-table-column label="trace_id" min-width="180">
          <template #default="{ row }"><span class="mono small muted">{{ row.trace_id || '-' }}</span></template>
        </el-table-column>
      </el-table>
      <div style="display: flex; justify-content: flex-end; margin-top: 12px">
        <el-pagination
          v-model:current-page="auditPage"
          :page-size="auditPageSize"
          :total="auditTotal"
          layout="total, prev, pager, next"
          @current-change="loadAudit"
        />
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { systemApi, type AuditLog, type HealthInfo, type MetricsInfo } from '@/api'
import { AUDIT_ACTION_LABELS, fmtTime, stateLabel, stateTone } from '@/utils/format'

const health = ref<HealthInfo | null>(null)
const metrics = ref<MetricsInfo | null>(null)
const config = ref<Record<string, Record<string, unknown>>>({})
const configTab = ref('')
const auditLogs = ref<AuditLog[]>([])
const auditAction = ref('')
const auditPage = ref(1)
const auditPageSize = ref(20)
const auditTotal = ref(0)
const loading = ref(false)
const rebuilding = ref(false)

const SECTION_LABELS: Record<string, string> = {
  retrieval: '检索参数',
  agent: 'Agent 与守卫',
  judge: '质量评审',
  llm: '大模型',
}

function sectionLabel(name: string): string {
  return SECTION_LABELS[name] ?? name
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '-'
  if (Array.isArray(value)) return value.join(', ')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

const componentRows = computed(() => {
  const c = health.value?.components ?? {}
  const db = (c.database ?? {}) as Record<string, unknown>
  const neo = (c.neo4j ?? {}) as Record<string, unknown>
  const emb = (c.embedding ?? {}) as Record<string, unknown>
  const rer = (c.reranker ?? {}) as Record<string, unknown>
  const llm = (c.llm ?? {}) as Record<string, unknown>
  const vec = (c.vector_index_default ?? {}) as Record<string, unknown>
  return [
    { name: 'PostgreSQL', ok: Boolean(db.ok), detail: String(db.url_kind ?? '-') },
    { name: 'Neo4j', ok: Boolean(neo.ok), detail: String(neo.ok ? '图谱增强可用' : '不可用，已跳过图谱扩展') },
    { name: 'Embedding', ok: !emb.degraded, detail: `${emb.name ?? '-'} / ${emb.dim ?? '-'} 维` },
    { name: 'Reranker', ok: !rer.degraded, detail: String(rer.name ?? '-') },
    {
      name: 'LLM 主模型',
      ok: Boolean(llm.primary_key_configured),
      detail: `${llm.primary_model ?? '-'}${llm.primary_key_configured ? '（已配置 Key）' : '（未配置 Key）'}`,
    },
    { name: '向量索引', ok: Number(vec.size ?? 0) > 0, detail: `${vec.size ?? 0} 条向量 / ${vec.dim ?? '-'} 维` },
  ]
})

const taskStateRows = computed(() =>
  Object.entries(metrics.value?.tasks_by_state ?? {}).map(([state, count]) => ({ state, count })),
)

async function loadHealth() {
  try {
    health.value = await systemApi.health()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '健康检查失败')
  }
}

async function loadConfig() {
  try {
    config.value = await systemApi.config()
    configTab.value = Object.keys(config.value)[0] ?? ''
  } catch {
    /* 非管理员角色可能无权限 */
  }
}

async function loadAudit() {
  try {
    const result = await systemApi.auditLogs({
      page: auditPage.value,
      page_size: auditPageSize.value,
      action: auditAction.value || undefined,
    })
    auditLogs.value = result.items
    auditTotal.value = result.meta.total
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '审计日志加载失败')
  }
}

async function rebuild() {
  rebuilding.value = true
  try {
    const stats = await systemApi.rebuildIndex()
    ElMessage.success(`索引重建完成：${stats.indexed ?? 0} 个分块`)
    metrics.value = await systemApi.metrics()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '重建失败')
  } finally {
    rebuilding.value = false
  }
}

onMounted(async () => {
  loading.value = true
  try {
    await Promise.all([
      loadHealth(),
      systemApi.metrics().then((m) => (metrics.value = m)),
      loadConfig(),
      loadAudit(),
    ])
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.component-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px dashed var(--line);
}

.component-row:last-child {
  border-bottom: none;
}
</style>
