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
          <!-- 下载 PDF：报告的价值在于对外出具（报送、归档、作为责任凭据），
               仅存在于系统界面里的报告无法完成这些用途。 -->
          <el-button
            v-if="report"
            size="small"
            type="primary"
            :icon="Download"
            :loading="downloading"
            @click="downloadPdf"
          >
            下载 PDF
          </el-button>
        </div>
      </div>

      <!-- 未签发时给出明确警示：这是只读用户判断「可否采纳」的关键依据 -->
      <el-alert
        v-if="review && !review.is_final"
        type="warning"
        show-icon
        :closable="false"
        style="margin-bottom: 14px"
        title="该报告尚未签发"
        :description="reviewStatusNote(review.review_status)"
      />
      <el-alert
        v-else-if="review?.human_verdict === 'unqualified'"
        type="error"
        show-icon
        :closable="false"
        style="margin-bottom: 14px"
        title="该报告未通过复核，未予签发"
        description="复核决定为「退回报告」。报告不予签发，不可作为正式依据；评估结论仍在下方完整保留。"
      />

      <!-- 机器结论与人工复核决定并列（与审核人员看到的同一套信息，但只读）。
           ⚠️ 两者判定对象不同：左边是 AI 判工程质量，右边是人工判「报告能否出具」。
           必须成对展示并各带对象说明，否则「复核合格」会被误读成「工程合格」。 -->
      <div class="review-panel">
        <div class="review-panel__col">
          <div class="review-panel__title">
            评估结论
            <el-tag size="small" effect="plain" style="margin-left: 8px">AI 生成</el-tag>
          </div>
          <div class="review-panel__object">{{ VERDICT_OBJECT_NOTE }}</div>
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
            报告复核与签发
            <el-tag
              size="small"
              :type="review?.review_status === 'signed' ? 'success' : review?.review_status === 'rejected' ? 'danger' : 'warning'"
              effect="plain"
              style="margin-left: 8px"
            >
              {{ review?.review_status_label ?? reviewStatusText(review?.review_status) }}
            </el-tag>
          </div>
          <div class="review-panel__object">{{ REVIEW_DECISION_OBJECT_NOTE }}</div>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="复核决定">
              <span
                v-if="review?.human_verdict"
                class="tag"
                :class="`tag--${reviewDecisionTone(review.human_verdict)}`"
              >{{ review.review_decision_label ?? reviewDecisionLabel(review.human_verdict) }}</span>
              <span v-else class="muted">尚未复核</span>
            </el-descriptions-item>
            <el-descriptions-item label="复核人">
              <template v-if="review?.reviewed_by_name">
                {{ review.reviewed_by_name }}
                <span v-if="review.reviewed_by_position" class="muted small">（{{ review.reviewed_by_position }}）</span>
                <span v-if="review.reviewed_at" class="small muted"> · {{ fmtTime(review.reviewed_at) }}</span>
              </template>
              <span v-else class="muted">-</span>
            </el-descriptions-item>
            <el-descriptions-item label="签发状态">
              <template v-if="review?.is_final">
                <span class="tag tag--ok">已签发</span>
                <span class="small muted" style="margin-left: 6px">可作为正式依据</span>
              </template>
              <template v-else>
                <span class="tag tag--warn">未签发</span>
                <span class="small muted" style="margin-left: 6px">不得作为正式依据</span>
              </template>
            </el-descriptions-item>
            <el-descriptions-item v-if="review?.review_comment" label="复核意见">
              {{ review.review_comment }}
            </el-descriptions-item>
          </el-descriptions>
        </div>
      </div>

      <!-- 解释「为什么可以 不符合 + 接受报告」：不说清这一点，用户仍会困惑 -->
      <div v-if="review?.human_verdict && report?.overall_verdict" class="dual-note">
        <el-icon><InfoFilled /></el-icon>
        <span>
          <strong>{{ DUAL_VERDICT_NOTE }}</strong> ——
          本报告的评估结论（工程质量）是「{{ verdictLabel(report.overall_verdict) }}」，
          而复核决定针对的是<strong>报告本身</strong>：{{ review.review_decision_label ?? reviewDecisionLabel(review.human_verdict) }}。
          <template v-if="review.is_final">该报告已签发，评估结论按原样生效。</template>
          <template v-else>该报告未签发。</template>
        </span>
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
import { Download, InfoFilled } from '@element-plus/icons-vue'
import {
  evalApi,
  reviewApi,
  type EvalReport,
  type EvalTaskDetail,
  type ReviewStatusInfo,
} from '@/api'
import { saveBlob } from '@/api/http'
import MarkdownView from '@/components/MarkdownView.vue'
import {
  DUAL_VERDICT_NOTE,
  REVIEW_DECISION_OBJECT_NOTE,
  RISK_LABELS,
  RISK_TONES,
  VERDICT_OBJECT_NOTE,
  fmtTime,
  reviewDecisionLabel,
  reviewDecisionTone,
  reviewStatusNote,
  reviewStatusText,
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
const downloading = ref(false)
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

/**
 * 下载报告 PDF。
 *
 * ⚠️ 两点容易做错：
 * 1. 必须走 `evalApi.downloadReportPdf`（二进制），不能用普通 GET ——
 *    后者会尝试把 PDF 当 JSON 信封解析而失败；
 * 2. 文件名从后端 `Content-Disposition` 解析（含中文），
 *    不能在前端用标题拼 —— 后端做了安全字符过滤，前端拼会不一致。
 */
async function downloadPdf() {
  if (!report.value) return
  downloading.value = true
  try {
    const { blob, filename, handled } = await evalApi.downloadReportPdf(taskId)

    // ⚠️ handled=true 表示浏览器装了下载管理器扩展（IDM/迅雷等），
    // 扩展已在**网络层**拦截并自行完成下载，页面里的 fetch 只能拿到
    // 被取消的空响应（204 / 0 字节）。此时**不能**再 saveBlob(空 blob) ——
    // 那会在磁盘上留下一个 0 字节的损坏文件（用户打开发现打不开）。
    // 实测此时 IDM 已完成 19.54 KB 的正常下载。
    if (handled) {
      ElMessage.success('报告已开始下载（由浏览器或下载管理器接管）')
      return
    }

    saveBlob(blob, filename.endsWith('.pdf') ? filename : `${filename}.pdf`)
    // 提示里点明「未签发」的报告下载的是参考件，避免被误当作正式文件流转
    if (review.value && !review.value.is_final) {
      ElMessage.warning('已下载。该报告尚未签发，仅供内部参考')
    } else {
      ElMessage.success('报告已下载')
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '下载失败')
  } finally {
    downloading.value = false
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
  margin-bottom: 4px;
}

/* 对象说明：必须紧贴结论值，用户才知道这个结论在判什么 */
.review-panel__object {
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--ink-500);
  margin-bottom: 8px;
}

/* 「判定对象不同，结论可并存」的解释条 */
.dual-note {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 12px;
  padding: 9px 12px;
  border-radius: 5px;
  font-size: 12px;
  line-height: 1.75;
  color: var(--ink-700);
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-7);
}

.dual-note .el-icon {
  margin-top: 2px;
  color: var(--el-color-primary);
  flex: 0 0 auto;
}

@media (max-width: 1100px) {
  .review-panel {
    grid-template-columns: 1fr;
  }
}
</style>
