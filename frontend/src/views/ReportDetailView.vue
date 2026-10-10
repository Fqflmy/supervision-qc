<template>
  <div v-loading="loading">
    <!-- 报告头部：结论与签发状态是只读用户最需要先看到的 -->
    <section class="panel">
      <div class="panel__head">
        <div>
          <div class="panel__title">{{ task?.title || '质量评估报告' }}</div>
          <div class="report-meta">
            <span v-if="task?.specialty">{{ task.specialty }}</span>
            <span v-if="task?.finished_at" class="sep">·</span>
            <span v-if="task?.finished_at">完成于 {{ fmtTime(task.finished_at) }}</span>
            <span v-if="report?.id" class="sep">·</span>
            <span v-if="report?.id" class="mono">报告编号 {{ report.id.slice(0, 8) }}</span>
          </div>
        </div>
        <div style="display: flex; gap: 8px; align-items: center">
          <el-button size="small" @click="router.back()">返回</el-button>
          <el-button size="small" :loading="loading" @click="load">刷新</el-button>
        </div>
      </div>

      <!-- 未签发时给出明确警示：这是只读用户判断「可否采纳」的关键依据 -->
      <el-alert
        v-if="review && !review.is_final"
        type="warning"
        show-icon
        :closable="false"
        style="margin-bottom: 14px"
        title="该报告尚未经人工复核签发"
        description="未签发的报告仅供内部参考，不得作为对外出具的正式依据。"
      />
      <el-alert
        v-else-if="review?.human_verdict === 'unqualified'"
        type="error"
        show-icon
        :closable="false"
        style="margin-bottom: 14px"
        title="该报告已被人工复核判定为不合格"
        description="报告不予签发，不可作为正式依据。"
      />

      <!-- 机器结论与人工裁定并列（与审核人员看到的同一套信息，但只读） -->
      <div class="review-panel">
        <div class="review-panel__col">
          <div class="review-panel__title">
            评估结论<el-tag size="small" effect="plain" style="margin-left: 8px">AI 生成</el-tag>
          </div>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="总体结论">
              <span v-if="report?.overall_verdict" class="tag" :class="`tag--${verdictTone(report.overall_verdict)}`">
                {{ verdictLabel(report.overall_verdict) }}
              </span>
              <span v-else class="muted">-</span>
            </el-descriptions-item>
            <el-descriptions-item label="风险等级">
              <span v-if="report?.risk_level" class="tag" :class="`tag--${RISK_TONES[report.risk_level] ?? 'muted'}`">
                {{ RISK_LABELS[report.risk_level] ?? report.risk_level }}
              </span>
              <span v-else class="muted">-</span>
            </el-descriptions-item>
            <el-descriptions-item label="依据条款数">{{ report?.basis_count ?? '-' }}</el-descriptions-item>
            <el-descriptions-item label="问题项数">{{ report?.non_compliance_count ?? '-' }}</el-descriptions-item>
          </el-descriptions>
        </div>

        <div class="review-panel__col">
          <div class="review-panel__title">
            复核与签发
            <el-tag
              size="small"
              :type="review?.review_status === 'signed' ? 'success' : review?.review_status === 'rejected' ? 'danger' : 'warning'"
              effect="plain"
              style="margin-left: 8px"
            >
              {{ review?.review_status_label ?? '待复核' }}
            </el-tag>
          </div>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="复核结论">
              <span
                v-if="review?.human_verdict"
                class="tag"
                :class="review.human_verdict === 'qualified' ? 'tag--ok' : 'tag--danger'"
              >{{ review.human_verdict_label }}</span>
              <span v-else class="muted">尚未裁定</span>
            </el-descriptions-item>
            <el-descriptions-item label="签认人">
              {{ review?.reviewed_by_name || '-' }}
              <span v-if="review?.reviewed_at" class="small muted"> · {{ fmtTime(review.reviewed_at) }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="是否签发">
              <span v-if="review?.is_final" class="tag tag--ok">已签发生效</span>
              <span v-else class="tag tag--warn">未签发</span>
            </el-descriptions-item>
            <el-descriptions-item v-if="review?.review_comment" label="裁定依据">
              {{ review.review_comment }}
            </el-descriptions-item>
          </el-descriptions>
        </div>
      </div>
    </section>

    <!-- 报告正文 -->
    <section class="panel">
      <div class="panel__head">
        <div class="panel__title">报告正文</div>
      </div>
      <template v-if="report">
        <el-tabs v-model="tab">
          <el-tab-pane label="渲染报告" name="render">
            <MarkdownView :content="report.markdown" />
          </el-tab-pane>
          <el-tab-pane label="条款比对明细" name="matches">
            <el-table :data="matches" size="small" empty-text="无条款比对结果">
              <el-table-column label="规范" width="150">
                <template #default="{ row }"><span class="mono">{{ row.spec_code || '-' }}</span></template>
              </el-table-column>
              <el-table-column label="条款" width="100">
                <template #default="{ row }"><span class="mono">{{ row.clause_no || '-' }}</span></template>
              </el-table-column>
              <el-table-column label="判定" width="120">
                <template #default="{ row }">
                  <span class="tag" :class="`tag--${verdictTone(row.verdict)}`">{{ verdictLabel(row.verdict) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="风险" width="80" align="center">
                <template #default="{ row }">
                  <span v-if="row.risk_level" class="tag" :class="`tag--${RISK_TONES[row.risk_level] ?? 'muted'}`">
                    {{ RISK_LABELS[row.risk_level] ?? row.risk_level }}
                  </span>
                  <span v-else class="muted">-</span>
                </template>
              </el-table-column>
              <el-table-column prop="evidence" label="判定依据" min-width="280" show-overflow-tooltip />
              <el-table-column prop="remediation" label="整改建议" min-width="280" show-overflow-tooltip />
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
      <div v-else class="empty">该任务尚未生成报告</div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  evalApi,
  reviewApi,
  type EvalReport,
  type EvalTaskDetail,
  type ReviewStatusInfo,
} from '@/api'
import MarkdownView from '@/components/MarkdownView.vue'
import {
  RISK_LABELS,
  RISK_TONES,
  fmtTime,
  verdictLabel,
  verdictTone,
} from '@/utils/format'

/**
 * 报告详情（只读视角）。
 *
 * 与「任务详情」（EvalDetailView）的区别
 * ------------------------------------
 * 任务详情是**发起方/复核方的工作台**：含迭代次数、Token 消耗、执行轨迹、
 * 重新执行与裁定按钮 —— 对只读用户既无用也不该看。
 *
 * 本页只呈现**报告本身与签发状态**，不含任何写操作，
 * 因此对所有具备 `eval:read` 的角色都安全；
 * 只读用户从此页能明确判断「这份报告可否采纳」（是否已签发）。
 */
const route = useRoute()
const router = useRouter()

const taskId = String(route.params.id)
const task = ref<EvalTaskDetail | null>(null)
const report = ref<EvalReport | null>(null)
const review = ref<ReviewStatusInfo | null>(null)
const matches = ref<Record<string, unknown>[]>([])
const loading = ref(false)
const tab = ref('render')

async function load() {
  loading.value = true
  try {
    // 任务信息用于展示标题与专业（不含运维字段的渲染）
    const detail = await evalApi.detail(taskId)
    task.value = detail

    if (!detail.report_id) {
      report.value = null
      review.value = null
      matches.value = []
      return
    }

    report.value = await evalApi.report(taskId)
    // 条款明细来自任务详情（报告内容里也有，但详情接口已整理好字段）
    matches.value = (detail.matches ?? []) as unknown as Record<string, unknown>[]

    try {
      review.value = await reviewApi.status(taskId)
    } catch {
      review.value = null
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载报告失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.report-meta {
  margin-top: 5px;
  font-size: 12px;
  color: var(--ink-500);
}

.report-meta .sep {
  margin: 0 6px;
  color: var(--line);
}

/* 结论与签发状态并列：两者是不同来源的判定，视觉上必须分开 */
.review-panel {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  margin-bottom: 4px;
}

.review-panel__col {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px 12px;
  background: var(--surface-alt, #fafafa);
}

.review-panel__title {
  display: flex;
  align-items: center;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-900);
  margin-bottom: 8px;
}

@media (max-width: 1100px) {
  .review-panel {
    grid-template-columns: 1fr;
  }
}
</style>
