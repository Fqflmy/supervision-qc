import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi, type User } from '@/api'
import { tokenStore } from '@/api/http'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const isAuthenticated = computed(() => Boolean(tokenStore.access))
  const role = computed(() => user.value?.role ?? '')
  const isAdmin = computed(() => role.value === 'admin')
  const canWriteKb = computed(() => ['admin', 'kb_manager'].includes(role.value))
  const canReview = computed(() => ['admin', 'expert'].includes(role.value))

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
    login,
    fetchProfile,
    logout,
  }
})
