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

/** 全部菜单项。`perm` / `permAny` 为可访问所需权限（与路由 meta 同源）。 */
const allMenus = [
  { name: 'dashboard', title: '运行总览', icon: DataBoard },
  { name: 'knowledge', title: '知识库管理', icon: Files, perm: 'kb:write' as Permission },
  {
    name: 'graph',
    title: '知识图谱',
    icon: Share,
    permAny: ['kg:write', 'retrieval:read'] as Permission[],
  },
  { name: 'chat', title: '智能问答', icon: ChatDotRound, perm: 'retrieval:read' as Permission },
  { name: 'evaluation', title: '评估任务', icon: Checked, perm: 'eval:read' as Permission },
  { name: 'judge', title: '质量评审', icon: Medal, perm: 'judge:read' as Permission },
  { name: 'users', title: '用户与授权', icon: UserFilled, perm: 'admin:*' as Permission },
  { name: 'system', title: '系统与审计', icon: Setting, perm: 'admin:*' as Permission },
]

/**
 * 按权限点过滤菜单 —— 不同角色看到**不同的菜单集合**。
 *
 * 与路由 meta.perm / meta.permAny 使用同一份权限规则（utils/permissions），
 * 因此不会出现「菜单看得到但点进去被挡回」的不一致。
 *
 * 各角色实际菜单：
 *   管理员      全部 8 项
 *   知识库管理员 总览 / 知识库管理 / 知识图谱 / 智能问答 / 评估任务 / 质量评审
 *   监理工程师  总览 / 知识图谱 / 智能问答 / 评估任务 / 质量评审
 *   审核人员    总览 / 知识图谱 / 智能问答 / 评估任务 / 质量评审
 *   普通用户    总览 / 知识图谱 / 智能问答 / 评估任务 / 质量评审
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
