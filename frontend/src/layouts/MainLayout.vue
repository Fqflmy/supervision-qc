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
          <el-tag size="small" effect="plain">{{ roleLabel }}</el-tag>
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

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const healthStatus = ref<string>('')
const version = ref('1.0.0')
const envLabel = ref('')

const ROLE_LABELS: Record<string, string> = {
  admin: '系统管理员',
  kb_manager: '知识库管理员',
  engineer: '监理工程师',
  expert: '质量评估专家',
  viewer: '只读访客',
}

/** 全部菜单项。`roles` 为空表示所有登录用户可见。 */
const allMenus = [
  { name: 'dashboard', title: '运行总览', icon: DataBoard },
  { name: 'knowledge', title: '知识库管理', icon: Files },
  { name: 'graph', title: '知识图谱', icon: Share },
  { name: 'chat', title: '智能问答', icon: ChatDotRound },
  { name: 'evaluation', title: '评估任务', icon: Checked },
  { name: 'judge', title: '质量评审', icon: Medal },
  { name: 'users', title: '用户与授权', icon: UserFilled, roles: ['admin'] },
  { name: 'system', title: '系统与审计', icon: Setting, roles: ['admin'] },
]

/**
 * 按角色过滤菜单。
 * 与路由的 meta.roles 保持同一套规则 —— 菜单不显示点不进去的入口，
 * 但真正的权限边界仍在后端（接口返回 403）。
 */
const menus = computed(() =>
  allMenus.filter((m) => !m.roles || m.roles.includes(auth.user?.role ?? '')),
)

const roleLabel = computed(() => ROLE_LABELS[auth.user?.role ?? ''] ?? auth.user?.role ?? '-')

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
