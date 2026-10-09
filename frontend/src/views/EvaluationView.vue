<template>
  <div>
    <div class="panel">
      <div class="toolbar">
        <el-select v-model="filters.state" placeholder="全部状态" clearable style="width: 150px" @change="load">
          <el-option v-for="(label, value) in STATE_LABELS" :key="value" :label="label" :value="value" />
        </el-select>
        <el-checkbox v-model="filters.mine" @change="load">仅看我发起的</el-checkbox>
        <el-button @click="load">刷新</el-button>
        <div class="toolbar__spacer"></div>
        <!-- 只有具备 eval:write 的角色能发起评估；审核人员/只读用户进来是为了查看与复核 -->
        <el-tooltip v-if="!auth.canStartEval" content="当前角色无发起评估权限（需监理工程师）" placement="bottom">
          <span>
            <el-button type="primary" :icon="Plus" disabled>新建评估任务</el-button>
          </span>
        </el-tooltip>
        <el-button v-else type="primary" :icon="Plus" @click="createVisible = true">新建评估任务</el-button>
      </div>

      <!-- 角色职责提示：让不同身份一进来就知道该干什么 -->
      <el-alert
        v-if="!auth.canStartEval"
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
        :title="roleHintTitle"
        :description="roleHintDesc"
      />

      <el-table v-loading="loading" :data="tasks" size="default" empty-text="暂无评估任务">
        <el-table-column prop="title" label="任务名称" min-width="220" show-overflow-tooltip />
        <el-table-column prop="specialty" label="专业" width="110" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <span class="tag" :class="`tag--${stateTone(row.current_state)}`">
              {{ stateLabel(row.current_state) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="进度" width="140">
          <template #default="{ row }">
            <el-progress :percentage="Math.round((row.progress ?? 0) * 100)" :stroke-width="8" />
            <span class="small muted mono">{{ Math.round((row.progress ?? 0) * 100) }}%</span>
          </template>
        </el-table-column>
        <el-table-column label="迭代 / 无进展" width="120" align="center">
          <template #default="{ row }">
            <span class="mono small">{{ row.iteration_count }} / {{ row.no_progress_rounds }}</span>
          </template>
        </el-table-column>
        <el-table-column label="Token" width="100" align="right">
          <template #default="{ row }"><span class="mono small">{{ row.total_tokens.toLocaleString() }}</span></template>
        </el-table-column>
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }"><span class="small muted">{{ fmtTime(row.created_at, 'MM-DD HH:mm') }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row.id)">详情</el-button>
            <el-button
              link
              type="success"
              size="small"
              :loading="runningId === row.id"
              @click="runTask(row)"
            >
              执行
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div style="display: flex; justify-content: flex-end; margin-top: 14px">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          @current-change="load"
          @size-change="load"
        />
      </div>
    </div>

    <!-- 新建任务 -->
    <el-dialog v-model="createVisible" title="新建质量评估任务" width="720px" top="6vh">
      <el-form :model="form" label-width="100px" size="default">
        <el-form-item label="任务名称">
          <el-input v-model="form.title" placeholder="如 地下室剪力墙混凝土施工质量评估" />
        </el-form-item>
        <el-form-item label="所属项目" required>
          <!-- 项目归属决定数据隔离：非管理员只能建在自己被授权的项目下。
               只授权一个项目时自动选中并置灰，无需用户操心。 -->
          <el-select
            v-model="form.project_id"
            placeholder="请选择项目"
            style="width: 100%"
            :disabled="projects.length <= 1"
          >
            <el-option
              v-for="p in projects"
              :key="p.id"
              :label="`${p.name}（${p.code}）`"
              :value="p.id"
            />
          </el-select>
          <div v-if="!projects.length" class="panel__hint" style="color: var(--el-color-danger)">
            当前账号未被授权任何项目，无法创建评估任务。请联系管理员在「用户与授权」中分配。
          </div>
          <div v-else-if="projects.length === 1" class="panel__hint">
            已自动选择你唯一被授权的项目。
          </div>
        </el-form-item>
        <el-form-item label="评估类型">
          <el-select v-model="form.eval_type" style="width: 220px">
            <el-option label="检验批质量评估" value="inspection_lot" />
            <el-option label="分项工程质量评估" value="sub_item" />
            <el-option label="专项问题核查" value="special_check" />
            <el-option label="资料合规性核查" value="document_check" />
          </el-select>
        </el-form-item>
        <el-form-item label="专业">
          <el-select v-model="form.specialty" placeholder="请选择" style="width: 220px">
            <el-option v-for="s in specialties" :key="s" :label="s" :value="s" />
          </el-select>
        </el-form-item>
        <el-form-item label="评估对象">
          <el-input v-model="form.part" placeholder="如 地下室剪力墙 / 二层顶板" />
        </el-form-item>
        <el-form-item label="项目名称">
          <el-input v-model="form.project_name" placeholder="可选" />
        </el-form-item>
        <el-form-item label="问题描述">
          <el-input v-model="form.description" type="textarea" :rows="3" placeholder="补充背景信息，如施工季节、异常现象等" />
        </el-form-item>

        <el-form-item label="待评材料">
          <div style="width: 100%">
            <div v-for="(rec, index) in form.records" :key="index" class="record-row">
              <el-input v-model="rec.name" placeholder="材料名称，如 混凝土浇筑记录" style="width: 190px" />
              <el-input
                v-model="rec.content"
                type="textarea"
                :rows="2"
                placeholder="材料内容（关键数据会被 Agent 用于条款比对）"
                style="flex: 1"
              />
              <el-button link type="danger" @click="form.records.splice(index, 1)">删除</el-button>
            </div>
            <el-button size="small" @click="form.records.push({ name: '', content: '' })">+ 添加材料</el-button>
          </div>
        </el-form-item>

        <el-form-item label="知识库">
          <el-select v-model="form.kb_ids" multiple placeholder="默认全部知识库" style="width: 100%">
            <el-option v-for="kb in kbs" :key="kb.id" :label="kb.name" :value="kb.id" />
          </el-select>
        </el-form-item>

        <el-collapse>
          <el-collapse-item title="收敛守卫参数（高级）" name="advanced">
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0 18px">
              <el-form-item label="最大迭代">
                <el-input-number v-model="form.max_iterations" :min="1" :max="50" />
              </el-form-item>
              <el-form-item label="无进展上限">
                <el-input-number v-model="form.no_progress_limit" :min="1" :max="10" />
              </el-form-item>
              <el-form-item label="任务超时(s)">
                <el-input-number v-model="form.task_timeout_seconds" :min="30" :max="7200" :step="30" />
              </el-form-item>
              <el-form-item label="Token 预算">
                <el-input-number v-model="form.token_budget" :min="1000" :max="2000000" :step="10000" />
              </el-form-item>
            </div>
          </el-collapse-item>
        </el-collapse>
      </el-form>

      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="createTask">创建任务</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { evalApi, kbApi, usersApi, type EvalTask, type KnowledgeBase, type ProjectItem } from '@/api'
import { STATE_LABELS, fmtTime, stateLabel, stateTone } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

const specialties = ['结构工程', '地基基础', '装饰装修', '屋面工程', '给排水', '电气工程', '施工安全']

const tasks = ref<EvalTask[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const runningId = ref<string | null>(null)
const filters = reactive({ state: '', mine: false })

/**
 * 角色职责提示。
 *
 * 评估任务页对不同角色的意义完全不同：工程师在这里**发起**评估，
 * 审核人员在这里**找待复核的任务**。若只把按钮禁用而不说明，
 * 审核人员会以为「功能坏了」。因此按角色给出明确的下一步动作。
 */
const roleHintTitle = computed(() => {
  if (auth.canReview) return '你当前是审核角色：请到「质量评审」处理待复核结论'
  return '当前角色为只读：可查看授权范围内的评估结果'
})

const roleHintDesc = computed(() => {
  if (auth.canReview) {
    return '评估任务由监理工程师发起；审核人员在「质量评审」中查看评分、确认结论或提交人工复核意见。'
  }
  return '如需发起评估，请联系管理员为你的账号分配「监理工程师」角色。'
})

const kbs = ref<KnowledgeBase[]>([])
/** 当前用户被授权的项目（创建任务时选择，决定数据隔离归属） */
const projects = ref<ProjectItem[]>([])
const createVisible = ref(false)
const creating = ref(false)
const form = reactive({
  title: '',
  project_id: null as number | null,
  eval_type: 'inspection_lot',
  specialty: '结构工程',
  part: '',
  project_name: '',
  description: '',
  records: [{ name: '混凝土浇筑记录', content: '' }] as { name: string; content: string }[],
  kb_ids: [] as number[],
  max_iterations: 12,
  no_progress_limit: 2,
  task_timeout_seconds: 900,
  token_budget: 200000,
})

async function load() {
  loading.value = true
  try {
    const result = await evalApi.list({
      page: page.value,
      page_size: pageSize.value,
      state: filters.state || undefined,
      mine: filters.mine || undefined,
    })
    tasks.value = result.items
    total.value = result.meta.total
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

function openDetail(id: string) {
  router.push({ name: 'eval-detail', params: { id } })
}

async function runTask(row: EvalTask) {
  runningId.value = row.id
  try {
    const result = await evalApi.submit(row.id)
    ElMessage.success(`已提交后台执行（当前状态 ${stateLabel(result.current_state)}），可稍后刷新查看进度`)
    setTimeout(load, 3000)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '提交失败')
  } finally {
    runningId.value = null
  }
}

async function createTask() {
  if (!form.part && !form.description) {
    ElMessage.warning('请至少填写评估对象或问题描述')
    return
  }
  // 项目归属是数据隔离的依据，必须在提交前确定
  if (!form.project_id) {
    ElMessage.warning(
      projects.value.length
        ? '请选择所属项目'
        : '当前账号未被授权任何项目，请联系管理员分配后再创建任务',
    )
    return
  }
  creating.value = true
  try {
    const options: Record<string, unknown> = {
      max_iterations: form.max_iterations,
      no_progress_limit: form.no_progress_limit,
      task_timeout_seconds: form.task_timeout_seconds,
      token_budget: form.token_budget,
    }
    const result = await evalApi.create({
      project_id: form.project_id,
      eval_type: form.eval_type,
      specialty: form.specialty,
      title: form.title || `${form.part || '评估对象'}质量评估`,
      object: {
        part: form.part || undefined,
        project_name: form.project_name || undefined,
        description: form.description || undefined,
        records: form.records.filter((r) => r.content.trim()),
      },
      kb_ids: form.kb_ids.length ? form.kb_ids : undefined,
      options,
    })
    ElMessage.success('任务创建成功')
    createVisible.value = false
    await load()
    router.push({ name: 'eval-detail', params: { id: result.task_id } })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '创建失败')
  } finally {
    creating.value = false
  }
}

onMounted(async () => {
  await load()
  try {
    kbs.value = await kbApi.listKbs()
  } catch {
    /* 忽略 */
  }
  await loadProjects()
})

/**
 * 加载当前用户被授权的项目。
 *
 * 非管理员创建任务必须带 project_id（数据隔离依据），但此前没有接口
 * 让普通用户知道自己被授权了哪些项目 —— 工程师因此无法实际发起评估。
 * `GET /me/projects` 补上了这个缺口；只授权一个项目时自动选中。
 */
async function loadProjects() {
  try {
    const result = await usersApi.myProjects()
    projects.value = result.items
    if (result.can_auto_select && result.items.length === 1) {
      form.project_id = result.items[0].id
    } else if (result.items.length > 1 && !form.project_id) {
      form.project_id = null
    }
  } catch {
    projects.value = []
  }
}
</script>

<style scoped>
.record-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin-bottom: 8px;
}
</style>
