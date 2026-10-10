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
        <el-table-column prop="username" label="登录账号" min-width="130">
          <template #default="{ row }">
            <span class="mono">{{ row.username }}</span>
            <el-tag v-if="!row.is_active" type="info" size="small" style="margin-left: 6px">已停用</el-tag>
          </template>
        </el-table-column>
        <!-- 人员身份：账号是登录凭据，这一列才是「这个人是谁」。
             报告签认要落到具体的人，因此姓名/工号/单位/部门/岗位必须可见。 -->
        <el-table-column label="人员身份" min-width="220">
          <template #default="{ row }">
            <div>{{ row.full_name || '（未填写姓名）' }}</div>
            <div class="small muted">
              <span v-if="row.employee_no" class="mono">工号 {{ row.employee_no }}</span>
              <!-- 单位与部门分两段展示：混在一行会看不清层级（单位 → 部门 → 岗位） -->
              <span v-if="row.employee_no && row.org_name" class="sep">·</span>
              <span v-if="row.org_name">{{ row.org_name }}</span>
              <span v-if="row.org_name && row.department" class="sep">·</span>
              <span v-if="row.department">{{ row.department }}</span>
              <span v-if="(row.org_name || row.department) && row.position" class="sep">·</span>
              <span v-if="row.position">{{ row.position }}</span>
            </div>
          </template>
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
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.is_active ? 'success' : 'info'" effect="plain">
              {{ row.is_active ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近登录" width="150">
          <template #default="{ row }">
            <span class="small muted">{{ row.last_login_at ? fmtTime(row.last_login_at) : '从未登录' }}</span>
          </template>
        </el-table-column>
        <!-- 操作列用下拉菜单：操作项多（授权/编辑/改密/停用/删除），
             平铺按钮会挤占列宽且不易扫读。 -->
        <el-table-column label="操作" width="120" align="right" fixed="right">
          <template #default="{ row }">
            <el-dropdown trigger="click" @command="(cmd: string) => onCommand(cmd, row)">
              <el-button size="small">
                操作<el-icon class="el-icon--right"><ArrowDown /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="edit">编辑资料与角色</el-dropdown-item>
                  <el-dropdown-item command="projects">项目授权</el-dropdown-item>
                  <el-dropdown-item command="password">重置密码</el-dropdown-item>
                  <el-dropdown-item command="status" divided :disabled="row.id === scope?.user_id">
                    {{ row.is_active ? '停用账号' : '启用账号' }}
                  </el-dropdown-item>
                  <el-dropdown-item command="delete" divided :disabled="row.id === scope?.user_id">
                    删除用户
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
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
    <el-dialog v-model="createVisible" title="新建用户" width="620px">
      <el-form ref="createFormRef" :model="createForm" :rules="rules" label-width="96px" size="small">
        <el-form-item label="登录账号" prop="username">
          <el-input v-model="createForm.username" placeholder="登录名，2–64 字符" />
          <div class="panel__hint">登录凭据与审计标识，创建后不可修改。</div>
        </el-form-item>
        <el-form-item label="初始密码" prop="password">
          <el-input v-model="createForm.password" type="password" show-password placeholder="至少 8 位" />
        </el-form-item>

        <el-divider content-position="left">
          <span class="small muted">人员身份</span>
        </el-divider>

        <el-form-item label="姓名">
          <el-input v-model="createForm.full_name" placeholder="真实姓名，用于报告签认" />
        </el-form-item>
        <el-form-item label="工号">
          <el-input v-model="createForm.employee_no" placeholder="单位内部唯一，如 JL0123" />
        </el-form-item>
        <el-form-item label="所属单位">
          <el-input v-model="createForm.org_name" placeholder="如 某某工程监理有限公司" />
        </el-form-item>
        <el-form-item label="所属部门">
          <el-input v-model="createForm.department" placeholder="如 项目监理部" />
        </el-form-item>
        <el-form-item label="职务/岗位">
          <el-input v-model="createForm.position" placeholder="如 总监理工程师" />
        </el-form-item>
        <el-form-item label="执业证号">
          <el-input v-model="createForm.cert_no" placeholder="如 注册监理工程师证号" />
        </el-form-item>
        <el-form-item label="签认署名">
          <el-input v-model="createForm.signature" placeholder="报告中显示的署名；留空则用姓名" />
        </el-form-item>

        <el-divider content-position="left">
          <span class="small muted">权限与授权</span>
        </el-divider>

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

    <!-- 编辑资料与角色 -->
    <el-dialog v-model="editVisible" title="编辑用户" width="620px">
      <el-form :model="editForm" label-width="110px">
        <el-form-item label="登录账号">
          <el-input :model-value="editing?.username" disabled />
          <div class="panel__hint">登录凭据与审计标识，创建后不可修改。</div>
        </el-form-item>

        <el-divider content-position="left">
          <span class="small muted">人员身份</span>
        </el-divider>

        <el-form-item label="姓名">
          <el-input v-model="editForm.full_name" placeholder="真实姓名，用于报告签认" />
        </el-form-item>
        <el-form-item label="工号">
          <el-input v-model="editForm.employee_no" placeholder="单位内部唯一，如 JL0123" />
        </el-form-item>
        <el-form-item label="所属单位">
          <el-input v-model="editForm.org_name" placeholder="如 某某工程监理有限公司" />
        </el-form-item>
        <el-form-item label="所属部门">
          <el-input v-model="editForm.department" placeholder="如 项目监理部" />
        </el-form-item>
        <el-form-item label="职务/岗位">
          <el-input v-model="editForm.position" placeholder="如 总监理工程师" />
        </el-form-item>
        <el-form-item label="执业证号">
          <el-input v-model="editForm.cert_no" placeholder="如 注册监理工程师证号" />
        </el-form-item>
        <el-form-item label="签认署名">
          <el-input v-model="editForm.signature" placeholder="报告中显示的署名；留空则用姓名" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="editForm.email" placeholder="可选" />
        </el-form-item>
        <el-form-item label="电话">
          <el-input v-model="editForm.phone" placeholder="可选" />
        </el-form-item>

        <el-divider content-position="left">
          <span class="small muted">权限与授权</span>
        </el-divider>

        <el-form-item label="角色">
          <el-select v-model="editForm.role" style="width: 100%" :disabled="editing?.id === scope?.user_id">
            <el-option v-for="r in roleOptions" :key="r.value" :label="r.label" :value="r.value">
              <span>{{ r.label }}</span>
              <span class="muted small" style="margin-left: 8px">{{ r.hint }}</span>
            </el-option>
          </el-select>
          <div class="panel__hint">
            <strong>角色变更立即生效</strong>（权限由服务端角色派生）。
            不能修改当前登录账号的角色 —— 避免把自己降权后无法恢复。
          </div>
        </el-form-item>
        <el-form-item label="授权项目">
          <el-select
            v-model="editForm.project_ids"
            multiple
            filterable
            placeholder="选择项目"
            style="width: 100%"
            :disabled="editForm.role === 'admin'"
          >
            <el-option
              v-for="p in projects"
              :key="p.id"
              :label="`${p.name}（${p.code}）`"
              :value="p.id"
            />
          </el-select>
          <div class="panel__hint">
            {{ editForm.role === 'admin'
              ? '管理员不受项目隔离限制，无需授权。'
              : '至少选择一个项目，否则该用户登录后看不到任何项目数据。' }}
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="editVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 重置密码 -->
    <el-dialog v-model="passwordVisible" title="重置密码" width="480px">
      <el-alert
        type="warning"
        show-icon
        :closable="false"
        style="margin-bottom: 14px"
        title="本系统没有自助找回密码"
        description="管理员重置是用户忘记密码后唯一的恢复途径。请通过可靠渠道将新密码告知本人。"
      />
      <el-form :model="passwordForm" label-width="100px">
        <el-form-item label="用户">
          <el-input :model-value="editing?.username" disabled />
        </el-form-item>
        <el-form-item label="新密码" required>
          <el-input
            v-model="passwordForm.new_password"
            type="password"
            show-password
            placeholder="至少 8 位"
          />
        </el-form-item>
        <el-form-item label="确认密码" required>
          <el-input
            v-model="passwordForm.confirm"
            type="password"
            show-password
            placeholder="再次输入"
          />
        </el-form-item>
        <el-form-item label="安全策略">
          <el-checkbox v-model="passwordForm.must_change">
            要求该用户下次登录后修改密码
          </el-checkbox>
          <div class="panel__hint">
            建议勾选：避免管理员设置的临时密码被长期使用。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="passwordVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="submitPassword">确认重置</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { ArrowDown } from '@element-plus/icons-vue'
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

// ---- 编辑 / 重置密码 ----
const editVisible = ref(false)
const passwordVisible = ref(false)
const editForm = ref({
  // ---- 人员身份 ----
  full_name: '',
  employee_no: '',
  org_name: '',
  department: '',
  position: '',
  cert_no: '',
  signature: '',
  email: '',
  phone: '',
  // ---- 权限与授权 ----
  role: 'engineer' as AssignableRole,
  project_ids: [] as number[],
})
const passwordForm = ref({ new_password: '', confirm: '', must_change: true })

/** 操作列下拉菜单分发 */
function onCommand(cmd: string, row: ManagedUser) {
  switch (cmd) {
    case 'edit':
      openEdit(row)
      break
    case 'projects':
      openProjects(row)
      break
    case 'password':
      openPassword(row)
      break
    case 'status':
      toggleStatus(row)
      break
    case 'delete':
      removeUser(row)
      break
    default:
      break
  }
}

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

/**
 * 新建用户的空白表单。
 *
 * 用**工厂函数**而不是各处写对象字面量：新增字段时只需改这一处，
 * 否则极易漏改（实际踩过：加了身份字段后 ``openCreate()`` 里的重置对象
 * 漏了新字段，导致表单残留上一次输入）。
 */
function emptyCreateForm() {
  return {
    username: '',
    password: '',
    // ---- 人员身份 ----
    full_name: '',
    employee_no: '',
    org_name: '',
    department: '',
    position: '',
    cert_no: '',
    signature: '',
    // ---- 权限与授权 ----
    role: 'engineer' as AssignableRole,
    project_ids: [] as number[],
  }
}

const createForm = ref(emptyCreateForm())

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
  createForm.value = emptyCreateForm()
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
    const f = createForm.value
    await usersApi.createUser({
      username: f.username.trim(),
      password: f.password,
      // ---- 人员身份（空串转 null，避免写入空字符串）----
      full_name: f.full_name || null,
      employee_no: f.employee_no || null,
      org_name: f.org_name || null,
      department: f.department || null,
      position: f.position || null,
      cert_no: f.cert_no || null,
      signature: f.signature || null,
      role: f.role,
      project_ids: f.role === 'admin' ? [] : f.project_ids,
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

/** 打开编辑对话框：先用列表行数据回填，再拉一次详情确保是最新值 */
function openEdit(row: ManagedUser) {
  editing.value = row
  editForm.value = formFromUser(row)
  editVisible.value = true

  // 列表可能不是最新（他人刚改过），详情接口回填一次
  usersApi
    .getUser(row.id)
    .then((fresh) => {
      editing.value = fresh
      editForm.value = formFromUser(fresh)
    })
    .catch(() => undefined)
}

/** 把后端用户对象映射成表单模型（集中一处，避免新增字段时漏改） */
function formFromUser(u: ManagedUser) {
  return {
    full_name: u.full_name ?? '',
    employee_no: u.employee_no ?? '',
    org_name: u.org_name ?? '',
    department: u.department ?? '',
    position: u.position ?? '',
    cert_no: u.cert_no ?? '',
    signature: u.signature ?? '',
    email: u.email ?? '',
    phone: u.phone ?? '',
    role: (u.role as AssignableRole) ?? 'engineer',
    project_ids: [...(u.project_ids ?? [])],
  }
}

async function submitEdit() {
  if (!editing.value) return
  const id = editing.value.id

  // 非管理员必须至少一个项目 —— 与后端校验一致，前端先拦一道给出更好的提示
  if (editForm.value.role !== 'admin' && editForm.value.project_ids.length === 0) {
    ElMessage.warning('非管理员用户必须至少授权一个项目')
    return
  }

  saving.value = true
  try {
    // 身份字段一并提交（空字符串转 null，避免把「清空」写成空串）
    const f = editForm.value
    await usersApi.updateUser(id, {
      full_name: f.full_name || null,
      employee_no: f.employee_no || null,
      org_name: f.org_name || null,
      department: f.department || null,
      position: f.position || null,
      cert_no: f.cert_no || null,
      signature: f.signature || null,
      email: f.email || null,
      phone: f.phone || null,
      role: f.role,
      project_ids: f.project_ids,
    })
    ElMessage.success('用户信息已更新')
    editVisible.value = false
    loadUsers()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '更新失败')
  } finally {
    saving.value = false
  }
}

function openPassword(row: ManagedUser) {
  editing.value = row
  passwordForm.value = { new_password: '', confirm: '', must_change: true }
  passwordVisible.value = true
}

async function submitPassword() {
  if (!editing.value) return
  const { new_password, confirm, must_change } = passwordForm.value

  if (new_password.length < 8) {
    ElMessage.warning('密码至少 8 位')
    return
  }
  if (new_password !== confirm) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }

  saving.value = true
  try {
    const result = await usersApi.resetPassword(editing.value.id, {
      new_password,
      must_change,
    })
    ElMessage.success(
      result.must_change_password
        ? `已重置「${result.username}」的密码，该用户下次登录需修改密码`
        : `已重置「${result.username}」的密码`,
    )
    passwordVisible.value = false
    loadUsers()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '重置失败')
  } finally {
    saving.value = false
  }
}

/**
 * 删除用户。
 *
 * 默认走**软删除（停用）**：保留历史评估记录与审计链 ——
 * 评估结论需要可追溯责任人，删掉账号会让「谁发起的评估」永久丢失。
 * 若确认该用户没有任何业务数据，用户可选择物理删除。
 */
async function removeUser(row: ManagedUser) {
  // 先尝试物理删除，若后端因存在业务数据拒绝（409），降级为「停用」确认
  try {
    await ElMessageBox.confirm(
      `即将删除用户「${row.username}」。\n\n` +
        '系统默认**停用**该账号：历史评估记录与审计留痕完整保留，可随时重新启用。\n' +
        '若确认要彻底删除（仅在该用户没有任何评估任务/规范文档/知识库时可用），请在下一步选择。',
      '删除用户',
      { type: 'warning', confirmButtonText: '继续', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  saving.value = true
  try {
    // 先试软删除（安全路径）
    const result = await usersApi.deleteUser(row.id, false)
    ElMessage.success(result.message)
    loadUsers()

    // 提示是否进一步物理删除 —— 只有用户明确要求才尝试（失败会给出原因）
    try {
      await ElMessageBox.confirm(
        '是否同时从数据库中彻底删除该账号？\n\n' +
          '仅当该用户没有关联业务数据时可删除；否则系统会拒绝并说明原因。',
        '是否物理删除',
        { type: 'info', confirmButtonText: '尝试彻底删除', cancelButtonText: '不用了' },
      )
    } catch {
      return
    }

    try {
      const hard = await usersApi.deleteUser(row.id, true)
      ElMessage.success(hard.message)
    } catch (err) {
      // 有关联业务数据时后端返回 409 并说明具体是哪些数据
      ElMessage.warning(err instanceof Error ? err.message : '物理删除被拒绝')
    }
    loadUsers()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  loadProjects()
  loadScope()
  loadUsers()
})
</script>
