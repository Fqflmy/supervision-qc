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
        <div class="app-header__title">{{ currentTitle }}</div>
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
 * 各角色实际菜单（⬇ 与 audit 脚本的契约一致，改动需同步更新契约）：
 *
 *   系统管理员    8 项  总览 / 知识库管理 / 知识图谱 / 问答 / 评估任务 / 质量评审 / 用户与授权 / 系统与审计
 *   知识库管理员  3 项  知识库管理 / 知识图谱 / 智能问答
 *   监理工程师    3 项  评估任务 / 知识图谱 / 智能问答
 *   审核人员      2 项  质量评审 / 智能问答
 *   普通用户      2 项  规范查询 / 智能问答
 *
 * 归类的依据是**职责**而非「能不能读到」：
 * - 运行总览是平台运营视角（全库规模、组件状态、检索链路参数），归管理员；
 * - 知识图谱对规范维护者（构建）与工程师（查引用链）有直接价值，对纯只读用户没有；
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

function isActive(name: string): boolean {
  return route.path === `/${name}` || route.path.startsWith(`/${name}/`)
}

function go(name: string) {
  if (!isActive(name)) router.push({ name })
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
