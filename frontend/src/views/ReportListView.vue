<template>
  <div v-loading="loading">
    <section class="panel">
      <div class="panel__head">
        <div class="panel__title">质量评估报告</div>
        <div style="display: flex; gap: 8px; align-items: center">
          <el-select
            v-model="filters.state"
            placeholder="全部状态"
            clearable
            style="width: 150px"
            @change="load"
          >
            <el-option v-for="(label, value) in STATE_LABELS" :key="value" :label="label" :value="value" />
          </el-select>
          <el-button @click="load">刷新</el-button>
        </div>
      </div>

      <el-alert
        v-if="scopeWarning"
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
        :title="scopeWarning"
      />

      <el-table :data="tasks" size="default" empty-text="暂无评估报告">
        <el-table-column prop="title" label="任务名称" min-width="220" show-overflow-tooltip />
        <el-table-column prop="specialty" label="专业" width="110" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <span class="tag" :class="`tag--${stateTone(row.current_state)}`">
              {{ stateLabel(row.current_state) }}
            </span>
          </template>
        </el-table-column>
        <!-- ⚠️ 两列必须同时可见，且列头写明判定对象：
             「评估结论」= AI 判工程质量；「报告复核」= 人工判报告能否出具。
             只显示其中一列会让用户把「报告被退回」误读成「工程不合格」。 -->
        <el-table-column width="120" align="center">
          <template #header>
            <div class="col-head">
              <div>评估结论</div>
              <div class="col-head__note">AI 判工程质量</div>
            </div>
          </template>
          <template #default="{ row }">
            <span v-if="row.verdict" class="tag" :class="`tag--${verdictTone(row.verdict)}`">
              {{ verdictLabel(row.verdict) }}
            </span>
            <span v-else class="muted small">-</span>
          </template>
        </el-table-column>
        <el-table-column label="风险" width="80" align="center">
          <template #default="{ row }">
            <span v-if="row.risk_level" class="tag" :class="`tag--${RISK_TONES[row.risk_level] ?? 'muted'}`">
              {{ RISK_LABELS[row.risk_level] ?? row.risk_level }}
            </span>
            <span v-else class="muted small">-</span>
          </template>
        </el-table-column>
        <!-- 复核状态是只读用户最需要的信息：未签发的报告不得作为正式依据。
             值用「已退回」而非「不合格」—— 后者会与工程质量结论混淆。 -->
        <el-table-column width="130" align="center">
          <template #header>
            <div class="col-head">
              <div>报告复核</div>
              <div class="col-head__note">人工判报告</div>
            </div>
          </template>
          <template #default="{ row }">
            <el-tooltip :content="reviewStatusNote(row.review_status)" placement="top">
              <span
                class="tag"
                :class="{
                  'tag--ok': row.review_status === 'signed',
                  'tag--danger': row.review_status === 'rejected',
                  'tag--warn': row.review_status !== 'signed' && row.review_status !== 'rejected',
                }"
              >{{ reviewStatusText(row.review_status) }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="完成时间" width="170">
          <template #default="{ row }">
            <span class="small muted">{{ row.finished_at ? fmtTime(row.finished_at) : '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row.id)">查看报告</el-button>
            <!-- 列表页也能直接下载：查看后往往就是要导出去报送/归档，
                 多一次跳转是多余的。 -->
            <el-button
              link
              type="primary"
              size="small"
              :loading="downloadingId === row.id"
              @click="downloadPdf(row)"
            >下载</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div style="margin-top: 14px; display: flex; justify-content: flex-end">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          @current-change="load"
          @size-change="load"
        />
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { saveBlob } from '@/api/http'
import { evalApi, systemApi, type EvalTask } from '@/api'
import { useAuthStore } from '@/stores/auth'
import {
  RISK_LABELS,
  RISK_TONES,
  STATE_LABELS,
  fmtTime,
  reviewStatusNote,
  reviewStatusText,
  stateLabel,
  stateTone,
  verdictLabel,
  verdictTone,
} from '@/utils/format'

/**
 * 评估报告列表（只读视角）。
 *
 * 为什么需要独立页面
 * ----------------
 * 「评估任务」页是**发起方**的工作台（含新建任务、执行、迭代参数等），
 * 对只读用户不合适；但只读用户**确实需要**查看其授权项目的评估报告
 * （后端早已放行 `eval:read`，前端却一直没有入口 —— 属「有权限没入口」）。
 *
 * 本页只呈现报告本身：结论、风险、复核/签发状态、完成时间，
 * 不含任何写操作，因此对所有具备 `eval:read` 的角色都安全。
 */
const router = useRouter()
const auth = useAuthStore()

const tasks = ref<(EvalTask & { verdict?: string | null; risk_level?: string | null })[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const filters = reactive({ state: '' })

/** 未授权任何项目时给出可执行提示，而不是让用户对着空列表猜 */
const scopeWarning = computed(() => {
  if (auth.isAdmin) return ''
  const projects = (auth.user as { project_ids?: number[] } | null)?.project_ids ?? []
  if (projects.length === 0) {
    return '你尚未被授权任何项目，仅能看到公共规范库相关内容。请联系管理员在「用户与授权」中分配项目。'
  }
  return ''
})

async function load() {
  loading.value = true
  try {
    // with_report=true：后端在列表里直接带出结论与风险等级，
    // 避免前端**逐条查报告接口**（N+1 请求，任务多时明显变慢）。
    const result = await evalApi.list({
      page: page.value,
      page_size: pageSize.value,
      state: filters.state || undefined,
      with_report: true,
    })
    tasks.value = result.items
    total.value = result.meta.total
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

/** 正在下载的行 id（用于按钮 loading，避免重复点击） */
const downloadingId = ref<string | null>(null)

/**
 * 下载报告 PDF。
 *
 * ⚠️ 必须走 evalApi.downloadReportPdf（二进制），不能用普通 GET ——
 * 后者会把 PDF 当 JSON 信封解析而失败。
 * 文件名由后端 Content-Disposition 决定（含中文，前端拼会与后端过滤规则不一致）。
 */
async function downloadPdf(row: { id: string; review_status?: string }) {
  downloadingId.value = row.id
  try {
    const { blob, filename } = await evalApi.downloadReportPdf(row.id)
    saveBlob(blob, filename.endsWith('.pdf') ? filename : `${filename}.pdf`)
    // 未签发的报告下载的是参考件，必须提示，避免被误当正式文件流转
    if (row.review_status && row.review_status !== 'signed') {
      ElMessage.warning('已下载。该报告尚未签发，仅供内部参考')
    } else {
      ElMessage.success('报告已下载')
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '下载失败')
  } finally {
    downloadingId.value = null
  }
}

function openDetail(id: string) {
  // 跳到**只读报告详情**而非任务详情：任务详情是发起方/复核方工作台，
  // 含 Token 消耗、迭代次数、执行轨迹与写操作按钮，对只读用户既无用也不该看。
  router.push({ name: 'report-detail', params: { id } })
}

onMounted(async () => {
  // 先补齐用户信息（刷新页面时 store 可能为空，影响 scopeWarning 判断）
  if (!auth.user) {
    try {
      await auth.fetchProfile()
    } catch {
      /* 忽略：拉取失败时不影响列表加载 */
    }
  }
  await load()
  // 触发一次健康检查以复用系统状态（失败不影响主流程）
  systemApi.health().catch(() => undefined)
})
</script>
