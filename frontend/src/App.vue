<template>
  <router-view />
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { tokenStore } from '@/api/http'

const auth = useAuthStore()

onMounted(async () => {
  // 刷新页面后恢复用户信息与权限
  if (tokenStore.access && !auth.user) {
    await auth.fetchProfile()
  }
})
</script>
