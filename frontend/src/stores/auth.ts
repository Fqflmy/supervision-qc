import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi, type User } from '@/api'
import { tokenStore } from '@/api/http'
import { roleCan, roleIsAdmin, roleLabel as labelOfRole, type Permission } from '@/utils/permissions'

/**
 * 各角色的默认落地页。
 *
 * 与该角色的**主要职责**对应（不是「能访问什么」，而是「该先干什么」）：
 * - ``admin``      管用户与项目授权 -> 用户与授权页
 * - ``kb_manager`` 维护规范库 -> 知识库管理
 * - ``engineer``   发起质量评估 -> 评估任务
 * - ``expert``     人工复核 -> 质量评审（待复核队列）
 * - ``viewer``     只读 -> 运行总览
 *
 * 这是**体验层**的落地页选择，不代表权限：真正的边界在后端，
 * 前端路由与菜单都会按权限点过滤。
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
  const isAdmin = computed(() => roleIsAdmin(role.value))
  const roleLabel = computed(() => labelOfRole(role.value))

  /** 权限判定：菜单显示与按钮可用性都走它，规则来自 utils/permissions */
  function can(permission: Permission): boolean {
    return roleCan(role.value, permission)
  }

  // 常用能力的语义化别名（避免各页面重复写权限点字符串）
  const canWriteKb = computed(() => can('kb:write'))
  const canReview = computed(() => can('eval:review'))
  const canStartEval = computed(() => can('eval:write'))
  const canManageSystem = computed(() => can('admin:*'))
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
    roleLabel,
    isAdmin,
    can,
    canWriteKb,
    canReview,
    canStartEval,
    canManageSystem,
    home,
    login,
    fetchProfile,
    logout,
  }
})
