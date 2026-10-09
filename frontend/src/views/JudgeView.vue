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
        <el-table-column prop="title" label="任务" min-width="200" show-overflow-tooltip />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <span class="tag" :class="`tag--${stateTone(row.current_state)}`">
              {{ stateLabel(row.current_state) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="机器结论" width="100" align="center">
          <template #default="{ row }">
            <span v-if="row.machine_verdict" class="tag tag--info">
              {{ GRADE_LABELS[row.machine_verdict] ?? row.machine_verdict }}
            </span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="转复核原因" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="small muted">{{ row.guard_reason || 'Judge 评分低于阈值' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="完成时间" width="150">
          <template #default="{ row }">
            <span class="small muted">{{ row.finished_at ? fmtTime(row.finished_at) : '-' }}</span>
          </template>
        </el-table-column>
        <!-- 行内直接裁定：审核人员看到队列就能立刻给出结论，不必先进详情 -->
        <el-table-column label="裁定" width="190" align="right">
          <template #default="{ row }">
            <el-button link type="success" size="small" @click="openDecision(row.task_id, 'qualified', row.version)">
              判定合格
            </el-button>
            <el-button link type="danger" size="small" @click="openDecision(row.task_id, 'unqualified', row.version)">
              判定不合格
            </el-button>
            <el-button link type="primary" size="small" @click="open(row.task_id)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <!-- 裁定对话框（与任务详情页共用接口） -->
    <el-dialog
      v-model="decisionDialog"
      :title="decision.verdict === 'qualified' ? '判定合格并签发' : '判定不合格'"
      width="560px"
    >
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 12px"
        title="人工裁定不覆盖机器结论"
        description="两者将并列留存以便对比；判定不合格需填写依据，该分歧会作为模型迭代样本。"
      />
      <el-form label-width="90px">
        <el-form-item label="裁定依据" :required="decision.verdict === 'unqualified'">
          <el-input
            v-model="decision.comment"
            type="textarea"
            :rows="3"
            :placeholder="decision.verdict === 'unqualified' ? '必填：说明不合格的具体理由' : '可选'"
          />
        </el-form-item>
        <el-form-item v-if="decision.verdict === 'unqualified'" label="处置方式">
          <el-radio-group v-model="decision.rerun">
            <el-radio :value="true">驳回重跑</el-radio>
            <el-radio :value="false">直接落定不合格</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="decisionDialog = false">取消</el-button>
        <el-button
          :type="decision.verdict === 'qualified' ? 'success' : 'danger'"
          :loading="deciding"
          @click="submitDecision"
        >
          {{ decision.verdict === 'qualified' ? '确认合格并签发' : '确认不合格' }}
        </el-button>
      </template>
    </el-dialog>

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
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  evalApi,
  judgeApi,
  reviewApi,
  type EvalTaskDetail,
  type HumanVerdict,
  type JudgeDashboard,
  type PendingReviewItem,
} from '@/api'
import {
  DIMENSION_LABELS,
  GRADE_LABELS,
  fmtScore,
  fmtTime,
  stateLabel,
  stateTone,
  verdictLabel,
} from '@/utils/format'

const router = useRouter()
const dashboard = ref<JudgeDashboard | null>(null)
const tasks = ref<EvalTaskDetail[]>([])
/** 待复核队列（审核人员的工作面） */
const pending = ref<PendingReviewItem[]>([])
const loading = ref(false)

// ---- 行内裁定 ----
const decisionDialog = ref(false)
const deciding = ref(false)
const decisionTaskId = ref<string | null>(null)
const decisionVersion = ref<number | undefined>(undefined)
const decision = ref<{ verdict: HumanVerdict; comment: string; rerun: boolean }>({
  verdict: 'qualified',
  comment: '',
  rerun: true,
})

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
 * 使用后端 `GET /eval/reviews/pending`：其判定标准是「**尚无人工裁定**」的
 * 已出报告任务，与看板的 `needs_human_count`（历史累计被标记的评审记录，含已处理）
 * 是**不同口径** —— 之前用前端筛状态会导致「看板显示 3 个、队列却是空的」的困惑。
 * 改为后端统一口径后，两者含义在界面上也分别标注清楚。
 */
async function loadPending() {
  try {
    const result = await reviewApi.pending({ page: 1, page_size: 50 })
    pending.value = result.items
  } catch {
    // 队列加载失败不阻塞看板；用户可点刷新重试
    pending.value = []
  }
}

/** 打开裁定对话框（与任务详情页同一套接口） */
function openDecision(taskId: string, verdict: HumanVerdict, version: number) {
  decisionTaskId.value = taskId
  decisionVersion.value = version
  decision.value = { verdict, comment: '', rerun: true }
  decisionDialog.value = true
}

async function submitDecision() {
  const taskId = decisionTaskId.value
  if (!taskId) return
  const { verdict, comment, rerun } = decision.value

  if (verdict === 'unqualified' && !comment.trim()) {
    ElMessage.warning('判定不合格必须填写裁定依据')
    return
  }
  if (verdict === 'qualified' && !comment.trim()) {
    decision.value.comment = '同意 AI 结论，予以签发'
  }

  deciding.value = true
  try {
    await reviewApi.decide(taskId, {
      verdict,
      comment: decision.value.comment,
      rerun: verdict === 'unqualified' ? rerun : undefined,
      expected_version: decisionVersion.value,
    })
    if (verdict === 'qualified') {
      ElMessage.success('已判定合格并签发')
    } else if (rerun) {
      ElMessage.success('已判定不合格，任务已驳回重跑')
    } else {
      ElMessage.warning('已判定不合格，报告不予签发')
    }
    decisionDialog.value = false
    // 看板数字与队列都要刷新（裁定会同时影响两者）
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '裁定失败')
  } finally {
    deciding.value = false
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
