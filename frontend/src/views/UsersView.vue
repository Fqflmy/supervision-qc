<template>
  <div v-loading="loading">
    <!-- 授权缺口提示：非管理员且无项目授权时，登录后看不到任何项目数据 -->
    <el-alert
      v-if="scope?.warning"
      type="warning"
      show-icon
      :closable="false"
      style="margin-bottom: 16px"
      title="当前账号尚未被授权任何项目"
      :description="scope.warning"
    />

    <section class="panel">
      <div class="panel__head">
        <div class="panel__title">用户与项目授权</div>
        <div style="display: flex; gap: 8px; align-items: center">
          <el-input
            v-model="keyword"
            placeholder="搜索用户名 / 姓名"
            clearable
            style="width: 200px"
            size="small"
            @keyup.enter="reload"
            @clear="reload"
          />
          <el-button size="small" @click="reload">查询</el-button>
          <el-button size="small" type="primary" @click="openCreate">新建用户</el-button>
        </div>
      </div>

      <div class="panel__hint" style="margin-bottom: 10px">
        非管理员用户必须至少授权一个项目，否则登录后看不到任何项目数据（仅公共规范库可见）。
      </div>

      <el-table :data="users" size="small" style="width: 100%">
        <el-table-column prop="username" label="用户名" min-width="130">
          <template #default="{ row }">
            <span class="mono">{{ row.username }}</span>
            <el-tag v-if="!row.is_active" type="info" size="small" style="margin-left: 6px">已停用</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="full_name" label="姓名" min-width="110">
          <template #default="{ row }">{{ row.full_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="角色" width="130">
          <template #default="{ row }">
            <el-tag size="small" :type="roleTone(row.role)">{{ roleLabel(row.role) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="授权项目" min-width="220">
          <template #default="{ row }">
            <template v-if="row.role === 'admin'">
              <span class="muted small">不受限（管理员）</span>
            </template>
            <template v-else-if="row.project_ids?.length">
              <el-tag
                v-for="pid in row.project_ids"
                :key="pid"
                size="small"
                effect="plain"
                style="margin-right: 4px"
              >
                {{ projectLabel(pid) }}
              </el-tag>
            </template>
            <template v-else>
              <span class="tag tag--warn">未授权</span>
            </template>
          </template>
        </el-table-column>
        <el-table-column label="最近登录" width="160">
          <template #default="{ row }">
            <span class="small muted">{{ row.last_login_at ? fmtTime(row.last_login_at) : '从未登录' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" align="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="openProjects(row)">项目授权</el-button>
            <el-button
              size="small"
              link
              :type="row.is_active ? 'danger' : 'success'"
              :disabled="row.id === scope?.user_id"
              @click="toggleStatus(row)"
            >
              {{ row.is_active ? '停用' : '启用' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div style="display: flex; justify-content: flex-end; margin-top: 12px">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          small
          @current-change="loadUsers"
          @size-change="reload"
        />
      </div>
    </section>

    <!-- 新建用户 -->
    <el-dialog v-model="createVisible" title="新建用户" width="520px">
      <el-form ref="createFormRef" :model="createForm" :rules="rules" label-width="96px" size="small">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="createForm.username" placeholder="登录名，2–64 字符" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="createForm.password" type="password" show-password placeholder="至少 8 位" />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="createForm.full_name" placeholder="可选" />
        </el-form-item>
        <el-form-item label="角色" prop="role">
          <el-select v-model="createForm.role" style="width: 100%">
            <el-option v-for="item in roleOptions" :key="item.value" :label="item.label" :value="item.value">
              <span>{{ item.label }}</span>
              <span class="muted small" style="float: right">{{ item.hint }}</span>
            </el-option>
          </el-select>
          <div class="panel__hint" style="margin-top: 4px">{{ currentRoleHint }}</div>
        </el-form-item>
        <el-form-item label="授权项目" prop="project_ids">
          <el-select
            v-model="createForm.project_ids"
            multiple
            filterable
            placeholder="选择该用户可访问的项目"
            style="width: 100%"
            :disabled="createForm.role === 'admin'"
          >
            <el-option
              v-for="p in projects"
              :key="p.id"
              :label="`${p.name}（${p.code}）`"
              :value="p.id"
            />
          </el-select>
          <div class="panel__hint" style="margin-top: 4px">
            {{ createForm.role === 'admin' ? '管理员不受项目隔离限制，无需授权。' : '至少选择一个项目。' }}
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="createVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 项目授权 -->
    <el-dialog v-model="projectsVisible" title="项目授权" width="480px">
      <div class="panel__hint" style="margin-bottom: 10px">
        用户 <span class="mono">{{ editing?.username }}</span>
        （{{ roleLabel(editing?.role ?? '') }}）可访问以下项目：
      </div>
      <el-select
        v-model="editingProjectIds"
        multiple
        filterable
        placeholder="选择项目"
        style="width: 100%"
      >
        <el-option
          v-for="p in projects"
          :key="p.id"
          :label="`${p.name}（${p.code}）`"
          :value="p.id"
        />
      </el-select>
      <div class="panel__hint" style="margin-top: 8px">
        清空后该用户将看不到任何项目数据（接口会拒绝非管理员清空授权）。
      </div>
      <template #footer>
        <el-button size="small" @click="projectsVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="submitProjects">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import {
  usersApi,
  type AssignableRole,
  type ManagedUser,
  type MyScope,
  type ProjectItem,
} from '@/api'
import { fmtTime } from '@/utils/format'

const users = ref<ManagedUser[]>([])
const projects = ref<ProjectItem[]>([])
const scope = ref<MyScope | null>(null)
const keyword = ref('')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const loading = ref(false)
const saving = ref(false)

const createVisible = ref(false)
const projectsVisible = ref(false)
const editing = ref<ManagedUser | null>(null)
const editingProjectIds = ref<number[]>([])
const createFormRef = ref<FormInstance>()

/** 角色说明：让管理员清楚每个角色能做什么，而不是只看到英文标识 */
const roleOptions: Array<{ value: AssignableRole; label: string; hint: string }> = [
  { value: 'engineer', label: '监理工程师', hint: '上传规范 / 发起评估' },
  { value: 'expert', label: '审核人员', hint: '人工复核、确认结论' },
  { value: 'viewer', label: '普通用户', hint: '只读查询' },
  { value: 'kb_manager', label: '知识库管理员', hint: '知识库与图谱维护' },
  { value: 'admin', label: '系统管理员', hint: '全部权限' },
]

const ROLE_LABELS: Record<string, string> = {
  admin: '系统管理员',
  kb_manager: '知识库管理员',
  engineer: '监理工程师',
  expert: '审核人员',
  viewer: '普通用户',
}

function roleLabel(role?: string): string {
  return ROLE_LABELS[role ?? ''] ?? role ?? '-'
}

function roleTone(role?: string): 'success' | 'warning' | 'info' | 'danger' | '' {
  switch (role) {
    case 'admin':
      return 'danger'
    case 'kb_manager':
      return 'warning'
    case 'engineer':
      return 'success'
    case 'expert':
      return 'warning'
    default:
      return 'info'
  }
}

const createForm = ref({
  username: '',
  password: '',
  full_name: '',
  role: 'engineer' as AssignableRole,
  project_ids: [] as number[],
})

const currentRoleHint = computed(
  () => roleOptions.find((r) => r.value === createForm.value.role)?.hint ?? '',
)

const rules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 2, max: 64, message: '长度需在 2–64 之间', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 位', trigger: 'blur' },
  ],
  role: [{ required: true, message: '请选择角色', trigger: 'change' }],
  project_ids: [
    {
      // 前端先拦一道，后端也会校验；这里给出更即时的反馈
      validator: (_rule, value: number[], callback) => {
        if (createForm.value.role !== 'admin' && (!value || value.length === 0)) {
          callback(new Error('非管理员必须至少授权一个项目'))
          return
        }
        callback()
      },
      trigger: 'change',
    },
  ],
}

const projectMap = computed(() => {
  const map = new Map<number, ProjectItem>()
  for (const p of projects.value) map.set(p.id, p)
  return map
})

function projectLabel(id: number): string {
  const p = projectMap.value.get(id)
  return p ? p.code : `#${id}`
}

async function loadProjects() {
  try {
    projects.value = await usersApi.listProjects()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载项目列表失败')
  }
}

async function loadScope() {
  try {
    scope.value = await usersApi.myScope()
  } catch {
    /* 提示信息非关键，失败静默 */
  }
}

async function loadUsers() {
  loading.value = true
  try {
    const data = await usersApi.listUsers({
      page: page.value,
      page_size: pageSize.value,
      keyword: keyword.value || undefined,
    })
    users.value = data.items
    total.value = data.meta.total
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载用户列表失败')
  } finally {
    loading.value = false
  }
}

function reload() {
  page.value = 1
  loadUsers()
}

function openCreate() {
  createForm.value = {
    username: '',
    password: '',
    full_name: '',
    role: 'engineer',
    project_ids: [],
  }
  createVisible.value = true
  createFormRef.value?.clearValidate()
}

async function submitCreate() {
  const form = createFormRef.value
  if (!form) return
  const valid = await form.validate().catch(() => false)
  if (!valid) return

  saving.value = true
  try {
    await usersApi.createUser({
      username: createForm.value.username.trim(),
      password: createForm.value.password,
      full_name: createForm.value.full_name || null,
      role: createForm.value.role,
      project_ids: createForm.value.role === 'admin' ? [] : createForm.value.project_ids,
    })
    ElMessage.success('用户已创建')
    createVisible.value = false
    reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '创建失败')
  } finally {
    saving.value = false
  }
}

function openProjects(row: ManagedUser) {
  editing.value = row
  editingProjectIds.value = [...(row.project_ids ?? [])]
  projectsVisible.value = true
}

async function submitProjects() {
  if (!editing.value) return
  saving.value = true
  try {
    await usersApi.updateProjects(editing.value.id, editingProjectIds.value)
    ElMessage.success('项目授权已更新')
    projectsVisible.value = false
    loadUsers()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '更新失败')
  } finally {
    saving.value = false
  }
}

async function toggleStatus(row: ManagedUser) {
  const action = row.is_active ? '停用' : '启用'
  try {
    await ElMessageBox.confirm(`确认${action}用户「${row.username}」？`, '请确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await usersApi.updateStatus(row.id, !row.is_active)
    ElMessage.success(`已${action}`)
    loadUsers()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : `${action}失败`)
  }
}

onMounted(() => {
  loadProjects()
  loadScope()
  loadUsers()
})
</script>
