<template>
  <div>
    <div class="metric-grid" style="margin-bottom: 16px">
      <div v-for="card in cards" :key="card.label" class="metric">
        <div class="metric__label">{{ card.label }}</div>
        <div class="metric__value">
          {{ card.value }}<span v-if="card.unit" class="metric__unit">{{ card.unit }}</span>
        </div>
        <div class="metric__foot">{{ card.foot }}</div>
      </div>
    </div>

    <div style="display: grid; grid-template-columns: 1.2fr 1fr; gap: 16px">
      <section class="panel">
        <div class="panel__head">
          <div class="panel__title">最近评估任务</div>
          <el-button link type="primary" size="small" @click="router.push({ name: 'evaluation' })">
            查看全部
          </el-button>
        </div>
        <el-table :data="tasks" size="small" :show-header="true" empty-text="暂无任务">
          <el-table-column prop="title" label="任务" min-width="180" show-overflow-tooltip />
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <span class="tag" :class="`tag--${stateTone(row.current_state)}`">
                {{ stateLabel(row.current_state) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="迭代" width="70" align="center">
            <template #default="{ row }"><span class="mono">{{ row.iteration_count }}</span></template>
          </el-table-column>
          <el-table-column label="耗时" width="90">
            <template #default="{ row }">
              <span class="mono small">{{ duration(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="70">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openTask(row.id)">详情</el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <div>
        <section class="panel">
          <div class="panel__head">
            <div class="panel__title">组件状态</div>
            <div class="panel__hint">启动自检结果</div>
          </div>
          <div v-for="item in components" :key="item.name" class="component-row">
            <div class="component-row__name">
              <span class="tag" :class="item.ok ? 'tag--ok' : 'tag--warn'">{{ item.ok ? '正常' : '降级' }}</span>
              <span>{{ item.name }}</span>
            </div>
            <div class="component-row__value mono small">{{ item.detail }}</div>
          </div>
        </section>

        <section class="panel">
          <div class="panel__head">
            <div class="panel__title">检索链路配置</div>
            <div v-if="cfgDenied" class="panel__hint">仅管理员可见</div>
          </div>
          <el-descriptions v-if="!cfgDenied" :column="1" size="small" border>
            <el-descriptions-item label="分块 / 重叠">{{ cfg.chunk_size }} / {{ cfg.chunk_overlap }} token</el-descriptions-item>
            <el-descriptions-item label="双通道召回">BM25 Top{{ cfg.bm25_top_k }} + 向量 Top{{ cfg.dense_top_k }}</el-descriptions-item>
            <el-descriptions-item label="RRF / 权重">k={{ cfg.rrf_k }}，{{ cfg.bm25_weight }} / {{ cfg.dense_weight }}</el-descriptions-item>
            <el-descriptions-item label="重排保留">{{ cfg.rerank_top_n }} 条</el-descriptions-item>
            <el-descriptions-item label="无依据阈值">{{ cfg.no_evidence_threshold }}</el-descriptions-item>
          </el-descriptions>
          <div v-else class="panel__hint" style="padding: 8px 0">
            运行配置（含检索参数）仅管理员可查看。当前账号可在「知识库管理」与「评估任务」中正常使用这些链路。
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import { evalApi, systemApi, type EvalTask, type HealthInfo, type MetricsInfo } from '@/api'
import { fmtTime, stateLabel, stateTone } from '@/utils/format'

const router = useRouter()
const metrics = ref<MetricsInfo | null>(null)
const health = ref<HealthInfo | null>(null)
const tasks = ref<EvalTask[]>([])
const cfg = ref<Record<string, number>>({})
/** 运行配置仅管理员可读；非管理员时面板显示说明而不是空白 */
const cfgDenied = ref(false)

const cards = computed(() => {
  const m = metrics.value
  const completed = m?.tasks_by_state?.COMPLETED ?? 0
  const needHuman = (m?.tasks_by_state?.NEED_HUMAN ?? 0) + (m?.tasks_by_state?.DEGRADED ?? 0)
  // 非管理员的指标是「按可见范围」统计的，脚注里说明，避免与管理员看到的数字对不上时误判
  const scopeFoot = m?.scope === 'visible' ? '（仅你可见范围）' : ''
  return [
    { label: '规范文档', value: m?.documents_total ?? 0, unit: '份', foot: `已入库并建立索引${scopeFoot}` },
    { label: '文本分块', value: m?.chunks_total ?? 0, unit: '块', foot: `条款级切分结果${scopeFoot}` },
    { label: '已完成评估', value: completed, unit: '个', foot: 'Agent 五阶段执行完毕' },
    { label: '待人工复核', value: needHuman, unit: '个', foot: '低分或降级任务' },
    { label: '累计 Token', value: (m?.tokens_total ?? 0).toLocaleString(), unit: '', foot: `模型消耗${scopeFoot}` },
  ]
})

const components = computed(() => {
  const c = health.value?.components ?? {}
  const db = (c.database ?? {}) as Record<string, unknown>
  const neo = (c.neo4j ?? {}) as Record<string, unknown>
  const emb = (c.embedding ?? {}) as Record<string, unknown>
  const rer = (c.reranker ?? {}) as Record<string, unknown>
  const llm = (c.llm ?? {}) as Record<string, unknown>
  return [
    { name: 'PostgreSQL', ok: Boolean(db.ok), detail: String(db.url_kind ?? '-') },
    { name: 'Neo4j 图谱', ok: Boolean(neo.ok), detail: String(neo.ok ? '已连接' : '不可用（图谱增强关闭）') },
    {
      name: 'Embedding',
      ok: !emb.degraded,
      detail: `${emb.name ?? '-'} · ${emb.dim ?? '-'}维`,
    },
    { name: 'Reranker', ok: !rer.degraded, detail: String(rer.name ?? '-') },
    {
      name: '大模型',
      ok: Boolean(llm.primary_key_configured),
      detail: `${llm.primary_model ?? '-'}${llm.primary_key_configured ? '' : ' · 未配置 Key'}`,
    },
  ]
})

function duration(row: EvalTask): string {
  if (!row.started_at || !row.finished_at) return '-'
  const ms = dayjs(row.finished_at).diff(dayjs(row.started_at))
  return ms > 0 ? `${(ms / 1000).toFixed(1)}s` : '-'
}

function openTask(id: string) {
  router.push({ name: 'eval-detail', params: { id } })
}

onMounted(async () => {
  // 逐个请求独立结算，不能用 Promise.all：
  // `/admin/config` 需要 admin 权限，监理工程师等角色会拿到 403；
  // 若用 Promise.all，一处失败会导致 metrics/health/tasks **全部丢弃** ——
  // 页面表现成「所有指标归零、组件全部降级、还弹一个红色权限错误」，
  // 很容易被误判成「服务坏了」或「数据丢了」。
  const [metricsRes, healthRes, tasksRes, configRes] = await Promise.allSettled([
    systemApi.metrics(),
    systemApi.health(),
    evalApi.list({ page: 1, page_size: 6 }),
    systemApi.config(),
  ])

  if (metricsRes.status === 'fulfilled') {
    metrics.value = metricsRes.value
  } else {
    ElMessage.warning(`运行指标加载失败：${describe(metricsRes.reason)}`)
  }

  if (healthRes.status === 'fulfilled') {
    health.value = healthRes.value
  } else {
    ElMessage.warning(`组件状态加载失败：${describe(healthRes.reason)}`)
  }

  if (tasksRes.status === 'fulfilled') {
    tasks.value = tasksRes.value.items
  }

  if (configRes.status === 'fulfilled') {
    cfg.value = (configRes.value.retrieval ?? {}) as Record<string, number>
    cfgDenied.value = false
  } else {
    // 运行配置仅管理员可读；非管理员不提示错误，改为在面板内说明
    cfgDenied.value = true
  }
})

/** 把异常压成一行可读信息 */
function describe(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason)
}
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

.component-row__name {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.component-row__value {
  color: var(--ink-500);
  text-align: right;
}

@media (max-width: 1100px) {
  div[style*='grid-template-columns: 1.2fr 1fr'] {
    grid-template-columns: 1fr !important;
  }
}
</style>
