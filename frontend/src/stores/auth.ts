import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi, type User } from '@/api'
import { tokenStore } from '@/api/http'

/**
 * 各角色的默认落地页。
 *
 * 与后端 ROLE_PERMISSIONS 对应：
 * - ``admin``      全权限，且需要管用户与项目授权 -> 用户与授权页
 * - ``kb_manager`` 有 ``kb:write`` 但无 ``eval:write`` -> 知识库管理
 * - ``engineer``   有 ``eval:write`` -> 评估任务（其主要工作）
 * - ``expert``     有 ``eval:review`` -> 质量评审（待复核队列）
 * - ``viewer``     只读 -> 运行总览
 *
 * 这是**体验层**的落地页选择，不代表权限：真正的边界在后端，
 * 前端路由与菜单都会按角色过滤。
 */
export const ROLE_HOME: Record<string, string> = {
  admin: 'users',
  kb_manager: 'knowledge',
  engineer: 'evaluation',
  expert: 'judge',
  viewer: 'dashboard',
}

export function homeForRole(role?: string | null): string {
  return ROLE_HOME[(role ?? '').toLowerCase()] ?? 'dashboard'
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const isAuthenticated = computed(() => Boolean(tokenStore.access))
  const role = computed(() => user.value?.role ?? '')
  const isAdmin = computed(() => role.value === 'admin')
  const canWriteKb = computed(() => ['admin', 'kb_manager'].includes(role.value))
  const canReview = computed(() => ['admin', 'expert'].includes(role.value))
  /** 当前用户的默认落地页 */
  const home = computed(() => homeForRole(role.value))

  async function login(username: string, password: string) {
    loading.value = true
    error.value = null
    try {
      const result = await authApi.login(username, password)
      tokenStore.set(result.access_token, result.refresh_token)
      user.value = result.user
      return result.user
    } catch (err) {
      error.value = err instanceof Error ? err.message : '登录失败'
      throw err
    } finally {
      loading.value = false
    }
  }

  async function fetchProfile() {
    if (!tokenStore.access) return null
    try {
      user.value = await authApi.me()
      return user.value
    } catch {
      user.value = null
      return null
    }
  }

  async function logout() {
    try {
      if (tokenStore.access) await authApi.logout()
    } catch {
      /* 登出失败不阻塞本地清理 */
    }
    tokenStore.clear()
    user.value = null
  }

  return {
    user,
    loading,
    error,
    isAuthenticated,
    role,
    isAdmin,
    canWriteKb,
    canReview,
    home,
    login,
    fetchProfile,
    logout,
  }
})
