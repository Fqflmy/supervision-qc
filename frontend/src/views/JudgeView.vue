<template>
  <div v-loading="loading">
    <div class="metric-grid" style="margin-bottom: 16px">
      <div class="metric">
        <div class="metric__label">评审次数</div>
        <div class="metric__value">{{ dashboard?.review_count ?? 0 }}</div>
        <div class="metric__foot">LLM-as-Judge 累计执行</div>
      </div>
      <div class="metric">
        <div class="metric__label">平均总分</div>
        <div class="metric__value">{{ fmtScore(dashboard?.avg_total_score) }}<span class="metric__unit">/ 5.00</span></div>
        <div class="metric__foot">低分阈值 {{ dashboard?.threshold ?? '-' }}</div>
      </div>
      <div class="metric">
        <div class="metric__label">待人工复核</div>
        <div class="metric__value">{{ dashboard?.needs_human_count ?? 0 }}</div>
        <!-- 口径说明：这是「历史上被 Judge 标记 needs_human 的评审记录数」，
             与下方「待复核队列」（当前处于 NEED_HUMAN/DEGRADED 的任务）不是同一个数 ——
             前者含已处理过的记录。不写清楚会让人以为队列漏了数据。 -->
        <div class="metric__foot">累计被标记的评审（含已处理）</div>
      </div>
      <div class="metric">
        <div class="metric__label">模型分歧</div>
        <div class="metric__value">{{ dashboard?.conflict_count ?? 0 }}</div>
        <div class="metric__foot">双模型维度分歧超阈值</div>
      </div>
      <div class="metric">
        <div class="metric__label">幻觉引用累计</div>
        <div class="metric__value">{{ dashboard?.hallucination_total ?? 0 }}</div>
        <div class="metric__foot">知识库中不存在的条款引用</div>
      </div>
    </div>

    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start">
      <section class="panel">
        <div class="panel__head">
          <div class="panel__title">各维度平均得分</div>
          <div class="panel__hint">满分 5.00</div>
        </div>
        <div v-for="(score, dimension) in dashboard?.dimension_averages ?? {}" :key="dimension" class="dim-row">
          <div class="dim-row__label">{{ dimension }}</div>
          <el-progress
            :percentage="Math.round((score / 5) * 100)"
            :stroke-width="10"
            :color="score >= 4 ? '#15803d' : score >= 3 ? '#b45309' : '#b91c1c'"
          />
          <div class="dim-row__score mono">{{ score.toFixed(2) }}</div>
        </div>
        <div v-if="!Object.keys(dashboard?.dimension_averages ?? {}).length" class="empty">暂无评审数据</div>
      </section>

      <section class="panel">
        <div class="panel__head">
          <div class="panel__title">评分等级分布</div>
        </div>
        <div v-for="(count, grade) in dashboard?.grade_distribution ?? {}" :key="grade" class="grade-row">
          <span class="tag" :class="gradeTone(grade)">{{ GRADE_LABELS[grade] ?? grade }}</span>
          <div class="grade-row__bar">
            <div class="grade-row__fill" :style="{ width: `${gradePercent(count)}%` }"></div>
          </div>
          <span class="mono small">{{ count }}</span>
        </div>
        <div v-if="!Object.keys(dashboard?.grade_distribution ?? {}).length" class="empty">暂无评审数据</div>

        <div style="margin-top: 16px">
          <div class="panel__hint" style="margin-bottom: 8px">当前维度权重</div>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item
              v-for="(weight, dimension) in dashboard?.weights ?? {}"
              :key="dimension"
              :label="DIMENSION_LABELS[dimension] ?? dimension"
            >
              <span class="mono">{{ (weight * 100).toFixed(0) }}%</span>
            </el-descriptions-item>
          </el-descriptions>
        </div>
      </section>
    </div>

    <!-- 待复核队列：审核人员的**工作面**。
         看板只给统计数字（「待人工复核 3」），若不列出具体是哪些任务，
         审核人员无法知道该处理什么 —— 这是审核角色的核心诉求。 -->
    <section class="panel">
      <div class="panel__head">
        <div class="panel__title">待复核队列</div>
        <div style="display: flex; gap: 8px; align-items: center">
          <span class="panel__hint">当前处于「待人工复核 / 降级」状态的任务</span>
          <el-button size="small" @click="loadPending">刷新</el-button>
        </div>
      </div>
      <el-table :data="pending" size="small" empty-text="当前没有待复核任务">
        <el-table-column prop="title" label="任务" min-width="220" show-overflow-tooltip />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <span class="tag" :class="`tag--${stateTone(row.current_state)}`">
              {{ stateLabel(row.current_state) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="转复核原因" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="small muted">{{ row.guard_reason || 'Judge 评分低于阈值' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="完成时间" width="160">
          <template #default="{ row }">
            <span class="small muted">{{ row.finished_at ? fmtTime(row.finished_at) : '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110" align="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="open(row.id)">去复核</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section class="panel">
      <div class="panel__head">
        <div class="panel__title">最近完成任务与评审结果</div>
        <el-button size="small" @click="load">刷新</el-button>
      </div>
      <el-table :data="tasks" size="small" empty-text="暂无数据">
        <el-table-column prop="title" label="任务" min-width="200" show-overflow-tooltip />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <span class="tag" :class="`tag--${stateTone(row.current_state)}`">
              {{ stateLabel(row.current_state) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="Judge 总分" width="110" align="center">
          <template #default="{ row }">
            <span class="mono">{{ row.judge?.total_score != null ? fmtScore(row.judge.total_score) : '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="等级" width="90" align="center">
          <template #default="{ row }">
            <span v-if="row.judge?.grade" class="tag" :class="gradeTone(row.judge.grade)">
              {{ GRADE_LABELS[row.judge.grade] ?? row.judge.grade }}
            </span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="复核" width="90" align="center">
          <template #default="{ row }">
            <span v-if="row.judge?.needs_human" class="tag tag--warn">待复核</span>
            <span v-else-if="row.judge" class="tag tag--ok">已通过</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="迭代" width="70" align="center">
          <template #default="{ row }"><span class="mono small">{{ row.iteration_count }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="open(row.id)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { evalApi, judgeApi, type EvalTask, type EvalTaskDetail, type JudgeDashboard } from '@/api'
import {
  DIMENSION_LABELS,
  GRADE_LABELS,
  fmtScore,
  fmtTime,
  stateLabel,
  stateTone,
} from '@/utils/format'

const router = useRouter()
const dashboard = ref<JudgeDashboard | null>(null)
const tasks = ref<EvalTaskDetail[]>([])
/** 待复核队列（审核人员的工作面） */
const pending = ref<EvalTask[]>([])
const loading = ref(false)

function gradeTone(grade?: string | null) {
  if (grade === 'excellent' || grade === 'good') return 'tag--ok'
  if (grade === 'qualified') return 'tag--info'
  return 'tag--danger'
}

function gradePercent(count: number): number {
  const total = Object.values(dashboard.value?.grade_distribution ?? {}).reduce((a, b) => a + b, 0)
  return total ? Math.round((count / total) * 100) : 0
}

function open(id: string) {
  router.push({ name: 'eval-detail', params: { id } })
}

async function load() {
  loading.value = true
  try {
    dashboard.value = await judgeApi.dashboard()
    const list = await evalApi.list({ page: 1, page_size: 10 })
    // 拉取详情以获得 Judge 结果字段
    const details = await Promise.all(
      list.items.map((task) => evalApi.detail(task.id).catch(() => null)),
    )
    tasks.value = details.filter((item): item is EvalTaskDetail => item !== null)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
  await loadPending()
}

/**
 * 拉取待复核队列。
 *
 * 后端已支持 `GET /eval/tasks?state=NEED_HUMAN`，但审核人员此前只能自己去
 * 评估任务页手筛状态 —— 而该页已按职责从审核角色菜单中移除。
 * 因此必须在这里给出直接入口，否则「待人工复核 3 个」只是个数字、无法落地。
 *
 * DEGRADED 同样计入：降级任务也需要人工确认。
 */
async function loadPending() {
  try {
    const [needHuman, degraded] = await Promise.all([
      evalApi.list({ page: 1, page_size: 50, state: 'NEED_HUMAN' }),
      evalApi.list({ page: 1, page_size: 50, state: 'DEGRADED' }),
    ])
    const merged = [...needHuman.items, ...degraded.items]
    // 按完成时间倒序，最近触发的排前面
    merged.sort((a, b) => String(b.finished_at ?? '').localeCompare(String(a.finished_at ?? '')))
    pending.value = merged
  } catch {
    // 队列加载失败不阻塞看板；用户可点刷新重试
    pending.value = []
  }
}

onMounted(load)
</script>

<style scoped>
.dim-row {
  display: grid;
  grid-template-columns: 150px 1fr 56px;
  align-items: center;
  gap: 12px;
  padding: 6px 0;
}

.dim-row__label {
  font-size: 12.5px;
  color: var(--ink-700);
}

.dim-row__score {
  text-align: right;
  color: var(--ink-700);
}

.grade-row {
  display: grid;
  grid-template-columns: 80px 1fr 44px;
  align-items: center;
  gap: 12px;
  padding: 6px 0;
}

.grade-row__bar {
  height: 10px;
  background: var(--surface-alt);
  border-radius: 5px;
  overflow: hidden;
}

.grade-row__fill {
  height: 100%;
  background: var(--brand);
  border-radius: 5px;
  transition: width 0.3s;
}

.grade-row span.mono {
  text-align: right;
}
</style>
