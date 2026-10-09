<template>
  <div v-loading="loading">
    <div class="panel">
      <div class="panel__head">
        <div class="panel__title">{{ detail?.title || '评估任务详情' }}</div>
        <div style="display: flex; gap: 8px; align-items: center">
          <span class="tag" :class="`tag--${stateTone(detail?.current_state)}`">
            {{ stateLabel(detail?.current_state) }}
          </span>
          <el-button size="small" @click="router.back()">返回</el-button>
          <el-button size="small" type="primary" :loading="running" @click="run(false)">重新执行</el-button>
          <el-button
            size="small"
            :disabled="!canReviewAction"
            :loading="resuming"
            @click="reviewDialog = true"
          >
            人工复核
          </el-button>
        </div>
      </div>

      <el-descriptions :column="4" size="small" border>
        <el-descriptions-item label="任务 ID">
          <span class="mono small">{{ detail?.id }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="专业">{{ detail?.specialty || '-' }}</el-descriptions-item>
        <el-descriptions-item label="评估类型">{{ detail?.eval_type }}</el-descriptions-item>
        <el-descriptions-item label="检查点后端">{{ checkpointBackend }}</el-descriptions-item>
        <el-descriptions-item label="迭代次数">
          <span class="mono">{{ detail?.iteration_count }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="无进展轮次">
          <span class="mono">{{ detail?.no_progress_rounds }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="Token 消耗">
          <span class="mono">{{ (detail?.total_tokens ?? 0).toLocaleString() }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="进度">
          <el-progress :percentage="Math.round((detail?.progress ?? 0) * 100)" :stroke-width="8" />
        </el-descriptions-item>
      </el-descriptions>

      <el-alert
        v-if="detail?.guard_reason"
        :title="`收敛守卫：${detail.guard_reason}`"
        type="warning"
        show-icon
        :closable="false"
        style="margin-top: 12px"
      />
      <el-alert
        v-if="detail?.error_state"
        :title="`异常：${JSON.stringify(detail.error_state)}`"
        type="error"
        show-icon
        :closable="false"
        style="margin-top: 12px"
      />
    </div>

    <!-- 执行轨迹 -->
    <div class="panel">
      <div class="panel__head">
        <div class="panel__title">Agent 执行轨迹</div>
        <div class="panel__hint">五阶段流程，每阶段独立计时与产物摘要</div>
      </div>
      <div class="steps">
        <div
          v-for="step in detail?.steps ?? []"
          :key="step.id"
          class="step"
          :class="step.status === 'done' ? 'is-done' : 'is-skipped'"
        >
          <div class="step__name">{{ STEP_LABELS[step.step] ?? step.step }}</div>
          <div class="step__meta">
            {{ step.status === 'done' ? `${step.duration_ms ?? 0} ms` : '未执行' }}
          </div>
        </div>
      </div>
    </div>

    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start">
      <section class="panel">
        <div class="panel__head"><div class="panel__title">任务拆解（{{ detail?.subtasks.length ?? 0 }}）</div></div>
        <div v-for="task in detail?.subtasks ?? []" :key="task.id" class="subtask">
          <div class="subtask__head">
            <span class="tag tag--info">{{ task.seq }}</span>
            <span class="subtask__name">{{ task.name }}</span>
          </div>
          <div class="small" style="line-height: 1.8">
            <div><span class="muted">判定标准：</span>{{ task.criterion || '-' }}</div>
            <div><span class="muted">所需证据：</span>{{ task.required_evidence || '-' }}</div>
            <div class="mono" style="margin-top: 4px"><span class="muted">检索式：</span>{{ task.query }}</div>
          </div>
        </div>
        <div v-if="!detail?.subtasks.length" class="empty">暂无拆解结果</div>
      </section>

      <section class="panel">
        <div class="panel__head"><div class="panel__title">条款匹配结果（{{ detail?.matches.length ?? 0 }}）</div></div>
        <div v-for="match in detail?.matches ?? []" :key="match.id" class="match">
          <div class="match__head">
            <span class="tag" :class="`tag--${verdictTone(match.verdict)}`">{{ verdictLabel(match.verdict) }}</span>
            <span class="mono">{{ match.spec_code }} {{ match.clause_no }}</span>
            <span class="tag" :class="`tag--${RISK_TONES[match.risk_level ?? ''] ?? 'muted'}`">
              风险 {{ RISK_LABELS[match.risk_level ?? ''] ?? '-' }}
            </span>
            <span class="small muted mono">置信度 {{ match.confidence?.toFixed(2) ?? '-' }}</span>
          </div>
          <div class="small" style="line-height: 1.8">
            <div v-if="match.evidence"><span class="muted">证据：</span>{{ match.evidence }}</div>
            <div v-if="match.reasoning"><span class="muted">判定：</span>{{ match.reasoning }}</div>
            <div v-if="match.remediation"><span class="muted">整改：</span>{{ match.remediation }}</div>
          </div>
        </div>
        <div v-if="!detail?.matches.length" class="empty">暂无匹配结果</div>
      </section>
    </div>

    <div class="panel">
      <div class="panel__head">
        <div class="panel__title">评估报告</div>
        <div style="display: flex; gap: 8px; align-items: center">
          <span v-if="report?.overall_verdict" class="tag" :class="`tag--${verdictTone(report.overall_verdict)}`">
            总体 {{ verdictLabel(report.overall_verdict) }}
          </span>
          <el-button size="small" :loading="reportLoading" @click="loadReport(false)">加载报告</el-button>
          <el-button
            size="small"
            type="primary"
            :disabled="!report || !auth.canReview"
            :loading="judging"
            @click="runJudge"
          >
            触发质量评审
          </el-button>
        </div>
      </div>

      <template v-if="report">
        <!-- 人工裁定与签发（FR-AGT-11）：机器结论与人工裁定**并列展示、互不覆盖** -->
        <div class="review-panel">
          <div class="review-panel__col">
            <div class="review-panel__title">机器结论<el-tag size="small" effect="plain" style="margin-left: 8px">AI 生成</el-tag></div>
            <el-descriptions :column="1" size="small" border>
              <el-descriptions-item label="总体结论">
                <span v-if="report.overall_verdict" class="tag" :class="`tag--${verdictTone(report.overall_verdict)}`">
                  {{ verdictLabel(report.overall_verdict) }}
                </span>
                <span v-else class="muted">-</span>
              </el-descriptions-item>
              <el-descriptions-item label="风险等级">
                <span class="tag" :class="`tag--${RISK_TONES[report.risk_level ?? ''] ?? 'muted'}`">
                  {{ RISK_LABELS[report.risk_level ?? ''] ?? '-' }}
                </span>
              </el-descriptions-item>
              <el-descriptions-item label="生成模型">{{ report.generator_model || '-' }}</el-descriptions-item>
            </el-descriptions>
          </div>

          <div class="review-panel__col">
            <div class="review-panel__title">
              人工裁定
              <el-tag size="small" :type="reviewTagType" effect="plain" style="margin-left: 8px">
                {{ review?.review_status_label ?? '待复核' }}
              </el-tag>
            </div>
            <el-descriptions :column="1" size="small" border>
              <el-descriptions-item label="裁定结论">
                <span v-if="review?.human_verdict" class="tag" :class="review!.human_verdict === 'qualified' ? 'tag--ok' : 'tag--danger'">
                  {{ review!.human_verdict_label }}
                </span>
                <span v-else class="muted">尚未裁定</span>
              </el-descriptions-item>
              <el-descriptions-item label="签认人">
                {{ review?.reviewed_by_name || '-' }}
                <span v-if="review?.reviewed_at" class="small muted"> · {{ fmtTime(review.reviewed_at) }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="是否签发">
                <span v-if="review?.is_final" class="tag tag--ok">已签发生效</span>
                <span v-else class="tag tag--warn">未签发（不得作为正式依据）</span>
              </el-descriptions-item>
              <el-descriptions-item v-if="review?.review_comment" label="裁定依据">
                {{ review.review_comment }}
              </el-descriptions-item>
            </el-descriptions>

            <!-- 只有具备复核权限的角色能裁定 -->
            <div v-if="auth.canReview" style="margin-top: 10px; display: flex; gap: 8px">
              <el-button type="success" :icon="Select" @click="openDecision('qualified')">判定合格</el-button>
              <el-button type="danger" :icon="CloseBold" @click="openDecision('unqualified')">判定不合格</el-button>
            </div>
            <div v-else class="panel__hint" style="margin-top: 8px">
              当前角色无复核裁定权限（需审核人员或管理员）。
            </div>
          </div>
        </div>

        <!-- 机器结论与人工裁定分歧提示：这正是 FR-JDG-06 需要的迭代样本 -->
        <el-alert
          v-if="diverged"
          type="warning"
          show-icon
          :closable="false"
          style="margin-bottom: 14px"
          title="人工裁定与机器结论不一致"
          description="该分歧将记入模型迭代样本（FR-JDG-06 反馈闭环），用于后续提示词与模型优化。"
        />

        <el-descriptions :column="4" size="small" border style="margin-bottom: 14px">
          <el-descriptions-item label="依据条款数">{{ report.basis_count }}</el-descriptions-item>
          <el-descriptions-item label="问题项数">{{ report.non_compliance_count }}</el-descriptions-item>
          <el-descriptions-item label="风险等级">
            <span class="tag" :class="`tag--${RISK_TONES[report.risk_level ?? ''] ?? 'muted'}`">
              {{ RISK_LABELS[report.risk_level ?? ''] ?? '-' }}
            </span>
          </el-descriptions-item>
          <el-descriptions-item label="生成模型">{{ report.generator_model || '-' }}</el-descriptions-item>
        </el-descriptions>

        <el-tabs v-model="reportTab">
          <el-tab-pane label="渲染报告" name="render">
            <MarkdownView :content="report.markdown" />
          </el-tab-pane>
          <el-tab-pane label="Markdown 源文" name="raw">
            <el-input :model-value="report.markdown ?? ''" type="textarea" :rows="22" readonly />
          </el-tab-pane>
          <el-tab-pane label="质量评审" name="judge">
            <div v-if="report.judge">
              <div class="metric-grid" style="margin-bottom: 14px">
                <div class="metric">
                  <div class="metric__label">Judge 总分</div>
                  <div class="metric__value">{{ fmtScore(report.judge.total_score) }}</div>
                  <div class="metric__foot">
                    {{ report.judge.grade_label || GRADE_LABELS[report.judge.grade ?? ''] || report.judge.grade }}
                    · 阈值 {{ report.judge.threshold }}
                  </div>
                </div>
                <div class="metric">
                  <div class="metric__label">人工复核</div>
                  <div class="metric__value" style="font-size: 18px">
                    {{ report.judge.needs_human ? '需要' : '不需要' }}
                  </div>
                  <div class="metric__foot">{{ report.judge.comment || '未触发拦截' }}</div>
                </div>
                <div class="metric" v-if="report.judge.citation_check">
                  <div class="metric__label">引用准确率</div>
                  <div class="metric__value">
                    {{ (report.judge.citation_check.accuracy * 100).toFixed(0) }}<span class="metric__unit">%</span>
                  </div>
                  <div class="metric__foot">
                    幻觉引用 {{ report.judge.citation_check.hallucinations }} 条 / 共
                    {{ report.judge.citation_check.total }} 条
                  </div>
                </div>
              </div>

              <el-table :data="report.judge.scores" size="small">
                <el-table-column label="评分维度" width="180">
                  <template #default="{ row }">
                    {{ row.dimension_label || DIMENSION_LABELS[row.dimension] || row.dimension }}
                  </template>
                </el-table-column>
                <el-table-column label="得分" width="90" align="center">
                  <template #default="{ row }"><span class="mono">{{ fmtScore(row.score) }}</span></template>
                </el-table-column>
                <el-table-column label="权重" width="80" align="center">
                  <template #default="{ row }"><span class="mono">{{ row.weight?.toFixed(2) ?? '-' }}</span></template>
                </el-table-column>
                <el-table-column label="分歧" width="70" align="center">
                  <template #default="{ row }">
                    <span v-if="row.is_conflict" class="tag tag--warn">分歧</span>
                    <span v-else class="muted">-</span>
                  </template>
                </el-table-column>
                <el-table-column prop="comment" label="评审意见" min-width="320" />
              </el-table>

              <div v-if="report.judge.citation_check?.items?.length" style="margin-top: 14px">
                <div class="panel__hint" style="margin-bottom: 8px">引用逐条校验</div>
                <el-table :data="report.judge.citation_check.items" size="small" max-height="260">
                  <el-table-column label="规范" width="140">
                    <template #default="{ row }"><span class="mono">{{ row.spec_code || '-' }}</span></template>
                  </el-table-column>
                  <el-table-column label="条款" width="100">
                    <template #default="{ row }"><span class="mono">{{ row.clause_no || '-' }}</span></template>
                  </el-table-column>
                  <el-table-column label="存在性" width="110">
                    <template #default="{ row }">
                      <span class="tag" :class="row.exists ? 'tag--ok' : 'tag--danger'">
                        {{ row.exists ? '存在' : '幻觉引用' }}
                      </span>
                    </template>
                  </el-table-column>
                  <el-table-column label="来源" width="100">
                    <template #default="{ row }"><span class="small">{{ row.source }}</span></template>
                  </el-table-column>
                  <el-table-column label="语义一致" width="100">
                    <template #default="{ row }">
                      <span v-if="row.semantic_consistent === null" class="muted">-</span>
                      <span v-else class="tag" :class="row.semantic_consistent ? 'tag--ok' : 'tag--warn'">
                        {{ row.semantic_consistent ? '一致' : '不一致' }}
                      </span>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
            </div>
            <div v-else class="empty">尚未执行质量评审，点击右上角「触发质量评审」</div>
          </el-tab-pane>
        </el-tabs>
      </template>
      <div v-else class="empty">该任务尚未生成报告，请先执行评估</div>
    </div>

    <!-- 人工裁定（合格 / 不合格 + 终审签发） -->
    <el-dialog
      v-model="decisionDialog"
      :title="decision.verdict === 'qualified' ? '判定合格并签发' : '判定不合格'"
      width="640px"
    >
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 12px"
        :title="`机器结论：${report?.overall_verdict ? verdictLabel(report.overall_verdict) : '无'}`"
        description="人工裁定不会覆盖机器结论，两者将并列留存以便对比（分歧会作为模型迭代样本）。"
      />
      <el-form label-width="100px">
        <el-form-item label="裁定依据" :required="decision.verdict === 'unqualified'">
          <el-input
            v-model="decision.comment"
            type="textarea"
            :rows="3"
            :placeholder="
              decision.verdict === 'unqualified'
                ? '必填：说明不合格的具体理由（如引用条款已废止、缺少材料复验记录）'
                : '可选：如「已核对引用条款，同意 AI 结论」'
            "
          />
        </el-form-item>

        <!-- 判定不合格时选择处置方式 -->
        <template v-if="decision.verdict === 'unqualified'">
          <el-form-item label="处置方式">
            <el-radio-group v-model="decision.rerun">
              <el-radio :value="true">驳回重跑（带修订重新生成报告）</el-radio>
              <el-radio :value="false">直接落定不合格（重跑无意义）</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="条款修订">
            <div style="width: 100%">
              <div v-for="(item, index) in decision.items" :key="index" class="review-row">
                <el-select v-model="item.match_id" placeholder="选择条款匹配项" style="width: 100%">
                  <el-option
                    v-for="match in detail?.matches ?? []"
                    :key="match.id"
                    :label="`${match.spec_code ?? ''} ${match.clause_no ?? ''}（${verdictLabel(match.verdict)}）`"
                    :value="match.id"
                  />
                </el-select>
                <el-select v-model="item.verdict" placeholder="修订判定" style="width: 100%">
                  <el-option v-for="(label, value) in VERDICT_LABELS" :key="value" :label="label" :value="value" />
                </el-select>
                <el-button link type="danger" @click="decision.items.splice(index, 1)">删除</el-button>
              </div>
              <el-button size="small" @click="decision.items.push({ match_id: undefined, verdict: 'non_compliant' })">
                + 添加修订
              </el-button>
              <div class="panel__hint" style="margin-top: 4px">可选。修订会随本次裁定一并留痕。</div>
            </div>
          </el-form-item>
        </template>
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

    <!-- 终止任务（原「人工复核与续跑」保留：用于无法定论时中止） -->
    <el-dialog v-model="reviewDialog" title="人工介入与续跑" width="620px">
      <el-form label-width="90px">
        <el-form-item label="说明">
          <el-input v-model="reviewComment" type="textarea" :rows="3" placeholder="说明原因，将写入 human_feedback 反馈闭环" />
        </el-form-item>
        <el-form-item label="条款修订">
          <div style="width: 100%">
            <div v-for="(item, index) in reviewItems" :key="index" class="review-row">
              <el-select v-model="item.match_id" placeholder="选择条款匹配项" style="width: 100%">
                <el-option
                  v-for="match in detail?.matches ?? []"
                  :key="match.id"
                  :label="`${match.spec_code ?? ''} ${match.clause_no ?? ''}（${verdictLabel(match.verdict)}）`"
                  :value="match.id"
                />
              </el-select>
              <el-select v-model="item.verdict" placeholder="修订判定" style="width: 100%">
                <el-option v-for="(label, value) in VERDICT_LABELS" :key="value" :label="label" :value="value" />
              </el-select>
              <el-button link type="danger" @click="reviewItems.splice(index, 1)">删除</el-button>
            </div>
            <el-button size="small" @click="reviewItems.push({ match_id: undefined, verdict: 'compliant' })">
              + 添加修订
            </el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reviewDialog = false">取消</el-button>
        <el-button @click="resume('cancel')">终止任务</el-button>
        <el-button type="primary" :loading="resuming" @click="resume('continue')">提交并重新执行</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CloseBold, Select } from '@element-plus/icons-vue'
import {
  evalApi,
  judgeApi,
  reviewApi,
  type EvalReport,
  type EvalTaskDetail,
  type HumanVerdict,
  type ReviewStatusInfo,
} from '@/api'
import MarkdownView from '@/components/MarkdownView.vue'
import { useAuthStore } from '@/stores/auth'
import {
  DIMENSION_LABELS,
  GRADE_LABELS,
  RISK_LABELS,
  RISK_TONES,
  STEP_LABELS,
  VERDICT_LABELS,
  fmtScore,
  fmtTime,
  stateLabel,
  stateTone,
  verdictLabel,
  verdictTone,
} from '@/utils/format'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const taskId = String(route.params.id)
const detail = ref<EvalTaskDetail | null>(null)
const report = ref<EvalReport | null>(null)
const loading = ref(false)
const reportLoading = ref(false)
const running = ref(false)
const judging = ref(false)
const resuming = ref(false)
const reportTab = ref('render')
const reviewDialog = ref(false)
const reviewComment = ref('')
const reviewItems = ref<{ match_id?: number; verdict: string }[]>([])
const checkpointBackend = ref('-')

// ---- 人工裁定与签发（FR-AGT-11）----
/** 报告的人工裁定状态（机器结论仍在 report.overall_verdict 中，互不覆盖） */
const review = ref<ReviewStatusInfo | null>(null)
const decisionDialog = ref(false)
const deciding = ref(false)
const decision = ref<{
  verdict: HumanVerdict
  comment: string
  rerun: boolean
  items: { match_id?: number; verdict: string }[]
}>({ verdict: 'qualified', comment: '', rerun: true, items: [] })

/** 裁定状态徽标样式 */
const reviewTagType = computed(() => {
  const status = review.value?.review_status
  if (status === 'signed') return 'success'
  if (status === 'rejected') return 'danger'
  return 'warning'
})

/**
 * 人工裁定与机器结论是否分歧。
 * 这正是 FR-JDG-06 需要的迭代样本 —— 所以在界面上明确提示，而不是默默记录。
 */
const diverged = computed(() => {
  const human = review.value?.human_verdict
  const machine = report.value?.overall_verdict
  if (!human || !machine) return false
  // 机器「不合格」对应人工「不合格」不算分歧；其余组合不一致即为分歧
  return human === 'unqualified' ? machine !== 'unqualified' : machine === 'unqualified'
})

const canReviewAction = computed(
  () => ['NEED_HUMAN', 'DEGRADED'].includes(detail.value?.current_state ?? '') && auth.canReview,
)

/** 加载人工裁定状态（未裁定时接口也返回 pending，不报错） */
async function loadReview() {
  try {
    review.value = await reviewApi.status(taskId)
  } catch {
    // 无报告时接口 404 属正常，不打扰用户
    review.value = null
  }
}

function openDecision(verdict: HumanVerdict) {
  decision.value = {
    verdict,
    comment: '',
    // 判定不合格默认驳回重跑；「直接落定」由用户显式选择
    rerun: true,
    items: [],
  }
  decisionDialog.value = true
}

async function submitDecision() {
  const { verdict, comment, rerun, items } = decision.value

  // 判定不合格必须说明依据 —— 与后端校验一致，前端先拦一道给出更好的提示
  if (verdict === 'unqualified' && !comment.trim()) {
    ElMessage.warning('判定不合格必须填写裁定依据')
    return
  }
  if (verdict === 'qualified' && !comment.trim()) {
    decision.value.comment = '同意 AI 结论，予以签发'
  }

  // 改判（报告已签发过）需要二次确认
  if (review.value?.is_final && verdict === 'unqualified') {
    try {
      await ElMessageBox.confirm(
        '该报告此前已签发生效。改判为不合格将撤销签发，历次裁定记录会保留。确认继续？',
        '撤销签发确认',
        { type: 'warning', confirmButtonText: '确认改判', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }

  deciding.value = true
  try {
    const result = await reviewApi.decide(taskId, {
      verdict,
      comment: decision.value.comment,
      rerun: verdict === 'unqualified' ? rerun : undefined,
      corrected_matches: items.filter((i) => i.match_id).map((i) => ({ match_id: i.match_id, verdict: i.verdict })),
      expected_version: review.value?.version,
    })

    if (result.human_verdict === 'qualified') {
      ElMessage.success('已判定合格并签发，报告可作为正式依据')
    } else if (result.rerun) {
      ElMessage.success('已判定不合格，任务已驳回重跑（后台执行，稍后刷新查看）')
    } else {
      ElMessage.warning('已判定不合格，报告不予签发')
    }

    decisionDialog.value = false
    await Promise.all([load(), loadReport(true), loadReview()])
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '裁定失败')
  } finally {
    deciding.value = false
  }
}

async function load() {
  loading.value = true
  try {
    const data = await evalApi.detail(taskId)
    detail.value = data
    const snapshot = data.state_snapshot as Record<string, unknown> | null
    checkpointBackend.value = String(snapshot?.checkpoint_backend ?? '-')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

async function loadReport(silent = false) {
  // silent=true 用于首屏探测：报告尚未生成属于正常情况，不弹提示也不留 404 噪音
  if (!detail.value?.report_id) {
    report.value = null
    review.value = null
    if (!silent) ElMessage.info('该任务尚未生成报告，请先执行评估')
    return
  }
  reportLoading.value = true
  try {
    report.value = await evalApi.report(taskId)
    // 报告加载成功后同步拉取人工裁定状态（两者在同一面板并列展示）
    await loadReview()
  } catch (err) {
    report.value = null
    review.value = null
    if (!silent) ElMessage.warning(err instanceof Error ? err.message : '暂无报告')
  } finally {
    reportLoading.value = false
  }
}

async function run(fromResume: boolean) {
  // 注意：模板里必须写成 @click="run(false)"，否则 Vue 会把 PointerEvent 作为实参传入，
  // 导致 resume 变成真值、请求带上 resume=true 而报「任务已处于终态」。
  const resume = fromResume === true
  running.value = true
  try {
    const result = await evalApi.run(taskId, resume)
    ElMessage.success(
      `执行完成：${stateLabel(result.current_state)}，迭代 ${result.iteration_count} 次，` +
        `条款比对 ${result.matches.length} 项，耗时 ${(result.elapsed_ms / 1000).toFixed(1)}s`,
    )
    await load()
    // 执行后静默刷新报告（无报告时不会发起请求）
    await loadReport(true)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '执行失败')
  } finally {
    running.value = false
  }
}

async function runJudge() {
  if (!report.value) return
  judging.value = true
  try {
    const result = await judgeApi.score(report.value.id, { cross_model: true })
    ElMessage.success(
      `评审完成：总分 ${fmtScore(result.total_score as number)}，` +
        `${result.needs_human ? '已转人工复核' : '通过阈值'}`,
    )
    await loadReport(true)
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '评审失败')
  } finally {
    judging.value = false
  }
}

async function resume(action: 'continue' | 'cancel') {
  resuming.value = true
  try {
    const result = await evalApi.resume(taskId, {
      action,
      corrected_matches: reviewItems.value
        .filter((item) => item.match_id)
        .map((item) => ({ match_id: item.match_id, verdict: item.verdict })),
      comment: reviewComment.value || undefined,
    })
    ElMessage.success(action === 'cancel' ? '任务已终止' : `已提交重新执行（反馈 ${result.feedback} 条）`)
    reviewDialog.value = false
    reviewItems.value = []
    reviewComment.value = ''
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '提交失败')
  } finally {
    resuming.value = false
  }
}

onMounted(async () => {
  await load()
  // 首屏静默探测：无报告时不请求接口，避免无意义的 404
  await loadReport(true)
})
</script>

<style scoped>
.subtask,
.match {
  border-bottom: 1px dashed var(--line);
  padding: 10px 0;
}

.subtask:last-child,
.match:last-child {
  border-bottom: none;
}

.subtask__head,
.match__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}

.subtask__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-900);
}

.review-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}

/* 机器结论与人工裁定并列展示：两者是不同来源的判定，视觉上必须分开 */
.review-panel {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  margin-bottom: 14px;
}

.review-panel__col {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px 12px;
  background: var(--surface-2, #fafafa);
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
