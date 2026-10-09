<!--
  按角色分流的入口页。

  为什么需要它：路由根路径原先硬编码 redirect 到 /dashboard，
  所有角色登录后都会被送到同一个页面 —— 审核人员看不到待复核队列，
  知识库管理员看不到规范库。改为进入本页后再按**服务端返回的角色**决定去向。

  注意：这只是体验层分流。各页面的可访问性仍由路由 meta.roles 与后端接口把关，
  改前端跳转不能提权。
-->
<template>
  <div class="role-home" v-loading="true" element-loading-text="正在进入…"></div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { homeForRole, useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

onMounted(async () => {
  // 刷新页面时 store 可能是空的，需先取回用户信息才能判断角色
  if (!auth.user) {
    await auth.fetchProfile()
  }
  router.replace({ name: homeForRole(auth.user?.role) })
})
</script>

<style scoped>
.role-home {
  min-height: 40vh;
}
</style>
