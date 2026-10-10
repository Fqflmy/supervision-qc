<template>
  <div class="app-shell">
    <aside class="app-sidebar">
      <div class="app-brand">
        <div class="app-brand__title">工程监理质量<br />智能评估系统</div>
        <div class="app-brand__sub">Supervision QC Platform</div>
      </div>
      <nav class="app-nav">
        <div
          v-for="item in menus"
          :key="item.name"
          class="app-nav__item"
          :class="{ 'is-active': isActive(item.name) }"
          @click="go(item.name)"
        >
          <el-icon :size="16"><component :is="item.icon" /></el-icon>
          <span class="app-nav__label">{{ item.title }}</span>
        </div>      </nav>
      <div class="app-sidebar__footer">
        <div>v{{ version }}</div>
        <div>{{ envLabel }}</div>
      </div>
    </aside>

    <div class="app-main">
      <header class="app-header">
        <div class="app-header__left">
          <!-- 面包屑：企业系统标配，让用户明确「我在哪、上一层是什么」。
               隐藏页（如任务详情）会显示为「评估任务 / 任务详情」，可点击返回列表。 -->
          <el-breadcrumb separator="/" class="app-breadcrumb">
            <el-breadcrumb-item v-for="(crumb, index) in breadcrumbs" :key="index">
              <span
                :class="{ 'app-breadcrumb__link': isCrumbClickable(crumb, index) }"
                @click="onCrumbClick(crumb, index)"
              >{{ crumb.label }}</span>
            </el-breadcrumb-item>
          </el-breadcrumb>
          <!-- 顶层页时面包屑已等于标题，再显示标题就是重复（视觉冗余）；
               只有详情页（面包屑有层级）才需要标题。 -->
          <div v-if="breadcrumbs.length > 1" class="app-header__title">{{ currentTitle }}</div>
        </div>
        <div class="app-header__right">
          <el-tag v-if="healthStatus" size="small" :type="healthStatus === 'ok' ? 'success' : 'warning'" effect="plain">
            服务 {{ healthStatus === 'ok' ? '正常' : '降级' }}
          </el-tag>
          <span class="small muted">{{ auth.user?.full_name || auth.user?.username || '-' }}</span>
          <!-- 角色标签带职责说明：不同角色登录后一眼看出「我这个身份该做什么」 -->
          <el-tooltip :content="roleDuty" placement="bottom">
            <el-tag size="small" effect="plain">{{ roleLabel }}</el-tag>
          </el-tooltip>
          <el-button link type="primary" size="small" @click="onLogout">退出</el-button>
        </div>
      </header>
      <main class="app-content">
        <!-- 管理员重置过密码后提醒用户自行修改。
             系统暂无自助改密页面，因此这里只作提示 —— 不假装拦截。 -->
        <el-alert
          v-if="auth.user?.must_change_password"
          type="warning"
          show-icon
          :closable="false"
          style="margin-bottom: 12px"
          title="你的密码已被管理员重置"
          description="为账号安全，请尽快自行修改密码；如需帮助请联系系统管理员。"
        />
        <router-view v-slot="{ Component }">
          <component :is="Component" />
        </router-view>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ChatDotRound,
  Checked,
  DataBoard,
  Document,
  Files,
  Medal,
  Setting,
  Share,
  UserFilled,
} from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { systemApi } from '@/api'
import type { Permission } from '@/utils/permissions'
import { ROLE_DUTIES } from '@/utils/permissions'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const healthStatus = ref<string>('')
const version = ref('1.0.0')
const envLabel = ref('')

/**
 * 全部菜单项。`perm` / `permAny` 为可访问所需权限（与路由 meta 同源）。
 *
 * 原则：**每个角色只看到与其职责相关的功能**，而不是「所有非管理员看到同一套」。
 */
const allMenus = [
  // 运行总览是**平台运营视角**（全库规模、组件状态、检索链路参数），归管理员。
  { name: 'dashboard', title: '运行总览', icon: DataBoard, perm: 'admin:*' as Permission },
  // 知识库：有 kb:write 的是「管理」（可上传/解析/发布），仅 kb:read 的是「查询」。
  // 页面内各写操作已按 canWriteKb 禁用，因此同一页面可安全地服务两种角色。
  // 标题随权限变化，避免只读用户看到「管理」二字以为有写权限。
  {
    name: 'knowledge',
    title: auth.can('kb:write') ? '知识库管理' : '规范查询',
    icon: Files,
    perm: 'kb:read' as Permission,
  },
  {
    name: 'graph',
    title: '知识图谱',
    icon: Share,
    permAny: ['kg:write', 'eval:write'] as Permission[],
  },
  { name: 'chat', title: '智能问答', icon: ChatDotRound, perm: 'retrieval:read' as Permission },
  // 评估报告（只读视角）：任何具备 eval:read 的角色都能查看**其授权项目内**的报告。
  // 与「评估任务」（发起方工作台，需 eval:write）区分 —— 只读用户不该看到发起与执行入口，
  // 但确实需要查看报告（此前后端放行、前端无入口，属「有权限没入口」）。
  { name: 'reports', title: '评估报告', icon: Document, perm: 'eval:read' as Permission },
  // 评估任务：发起评估是工程师的职责
  { name: 'evaluation', title: '评估任务', icon: Checked, perm: 'eval:write' as Permission },
  {
    name: 'judge',
    title: '质量评审',
    icon: Medal,
    // 审核人员的**工作面**（待复核队列）
    permAny: ['eval:review', 'judge:write'] as Permission[],
  },
  { name: 'users', title: '用户与授权', icon: UserFilled, perm: 'admin:*' as Permission },
  { name: 'system', title: '系统与审计', icon: Setting, perm: 'admin:*' as Permission },
]

/**
 * 按权限点过滤菜单 —— **每个角色看到与其职责相关的一组功能**。
 *
 * 与路由 meta.perm / meta.permAny 使用同一份权限规则（utils/permissions），
 * 因此不会出现「菜单看得到但点进去被挡回」的不一致。
 *
 * 各角色实际菜单（与 `scripts/build_reachability_matrix.py` 的实测矩阵一致，
 * 改动需同步更新 `docs/角色可达性矩阵.md` 与 `check-role-matrix.mjs` 契约）：
 *
 *   系统管理员    9 项  总览 / 知识库管理 / 知识图谱 / 问答 / 评估任务 /
 *                       评估报告 / 质量评审 / 用户与授权 / 系统与审计
 *   知识库管理员  4 项  知识库管理 / 知识图谱 / 智能问答 / 评估报告
 *   监理工程师    5 项  规范查询 / 知识图谱 / 智能问答 / 评估报告 / 评估任务
 *   审核人员      4 项  规范查询 / 智能问答 / 评估报告 / 质量评审
 *   普通用户      3 项  规范查询 / 智能问答 / 评估报告
 *
 * 归类的依据是**职责**而非「能不能读到」：
 * - 运行总览是平台运营视角（全库规模、组件状态、检索链路参数），归管理员；
 * - 知识图谱对规范维护者（构建）与工程师（查引用链）有直接价值，对纯只读用户没有；
 * - 评估报告是**只读视角**（所有角色），评估任务是**发起方工作台**（需 eval:write）；
 * - 质量评审是审核人员的工作面（待复核队列），只读用户在报告详情内看评分即可。
 */
const menus = computed(() =>
  allMenus.filter((m) => {
    if (m.permAny) return m.permAny.some((perm) => auth.can(perm))
    if (m.perm) return auth.can(m.perm)
    return true
  }),
)

const roleLabel = computed(() => auth.roleLabel)
/** 角色职责说明（悬停提示），与登录页的说明同源 */
const roleDuty = computed(() => ROLE_DUTIES[auth.role] ?? '')

const currentTitle = computed(() => {
  const meta = route.meta.title as string | undefined
  if (meta) return meta
  const found = menus.value.find((m) => route.path.startsWith(`/${m.name}`))
  return found?.title ?? '运行总览'
})

/**
 * 面包屑：让用户明确「当前在哪、上一层是什么」。
 *
 * 规则：
 * - 顶层菜单页：只显示**菜单标题**（而非路由 meta.title）—— 两者可能不同，
 *   例如知识库页对只读角色的菜单标题是「规范查询」，而 meta.title 是「知识库」。
 *   面包屑与侧边栏**用词必须一致**，否则用户会以为是两个地方。
 * - 隐藏页（如 `evaluation/:id`、`reports/:id`）：在前面补一层所属菜单，
 *   并设为可点击返回 —— 否则用户从列表点进详情后只能靠浏览器后退。
 *
 * `PARENT_OF` 声明「这个隐藏页属于哪个菜单」：由 meta.title 无法推导
 * （「任务详情」无法反推出属于「评估任务」还是「评估报告」）。
 */
const PARENT_OF: Record<string, string> = {
  'eval-detail': 'evaluation',
  'doc-detail': 'knowledge',
  'report-detail': 'reports',
}

const breadcrumbs = computed(() => {
  const name = String(route.name ?? '')
  const parentName = PARENT_OF[name]

  if (!parentName) {
    // 顶层页：优先用菜单标题（与侧边栏一致），回退到 meta.title
    const menu = menus.value.find((m) => m.name === name)
    return [{ label: menu?.title ?? currentTitle.value, to: '' }]
  }

  const parent = menus.value.find((m) => m.name === parentName)
  const crumbs: { label: string; to: string }[] = []
  if (parent) {
    crumbs.push({ label: parent.title, to: parent.name })
  }
  crumbs.push({ label: currentTitle.value, to: '' })
  return crumbs
})

/**
 * 面包屑是否可点击。
 *
 * ⚠️ 不能用 `Boolean(crumb.to)` 判断：「最后一项」的 `to` 是空字符串，
 * 但**上级项的 to 也可能为空**（父菜单对该角色不可见时）。因此统一用
 * 「是最后一项吗」+「有跳转目标吗」两个条件，并提取为方法 ——
 * 行内三元表达式里调用函数极易写成「表达式被求值但不执行」（实际踩过：
 * 点击后毫无反应）。
 */
function isCrumbClickable(crumb: { label: string; to: string }, index: number): boolean {
  return Boolean(crumb.to) && index < breadcrumbs.value.length - 1
}

function onCrumbClick(crumb: { label: string; to: string }, index: number): void {
  if (isCrumbClickable(crumb, index)) {
    go(crumb.to)
  }
}

/**
 * 菜单项是否处于激活态。
 *
 * ⚠️ 不能只看路径前缀：详情页 `/reports/:id` 会让 `/reports` 也「激活」，
 * 于是 `go('reports')` 里的「已激活就不跳转」判断认为无需跳转 ——
 * **在详情页点面包屑返回列表会毫无反应**（实际踩到）。
 *
 * 因此比较**路由名**：只有当前就在该路由时才视为激活。
 */
function isActive(name: string): boolean {
  if (route.name === name) return true
  // 详情页归属其父菜单（用于侧边栏高亮），但**不代表可以跳过跳转** ——
  // 是否跳转由 go() 用 route.name 判断。
  return route.path.startsWith(`/${name}/`)
}

/** 当前是否已经停在该路由（用于避免重复跳转） */
function isCurrentRoute(name: string): boolean {
  return route.name === name
}

function go(name: string) {
  // 已在该路由才跳过；在「父菜单的详情页」时仍需跳转回列表
  if (!isCurrentRoute(name)) router.push({ name })
}

async function onLogout() {
  await auth.logout()
  ElMessage.success('已退出登录')
  router.push({ name: 'login' })
}

onMounted(async () => {
  if (!auth.user) await auth.fetchProfile()
  try {
    const health = await systemApi.health()
    healthStatus.value = health.status
    version.value = health.version
    envLabel.value = health.environment
  } catch {
    healthStatus.value = 'degraded'
  }
})
</script>
