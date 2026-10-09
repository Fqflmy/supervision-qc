import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'
import { tokenStore } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/DashboardView.vue'),
        meta: { title: '运行总览', icon: 'DataBoard' },
      },
      {
        path: 'knowledge',
        name: 'knowledge',
        component: () => import('@/views/KnowledgeView.vue'),
        meta: { title: '知识库管理', icon: 'Files' },
      },
      {
        path: 'knowledge/:id',
        name: 'doc-detail',
        component: () => import('@/views/DocDetailView.vue'),
        meta: { title: '规范详情', hidden: true },
      },
      {
        path: 'graph',
        name: 'graph',
        component: () => import('@/views/GraphView.vue'),
        meta: { title: '知识图谱', icon: 'Share' },
      },
      {
        path: 'chat',
        name: 'chat',
        component: () => import('@/views/ChatView.vue'),
        meta: { title: '智能问答', icon: 'ChatDotRound' },
      },
      {
        path: 'evaluation',
        name: 'evaluation',
        component: () => import('@/views/EvaluationView.vue'),
        meta: { title: '评估任务', icon: 'Checked' },
      },
      {
        path: 'evaluation/:id',
        name: 'eval-detail',
        component: () => import('@/views/EvalDetailView.vue'),
        meta: { title: '任务详情', hidden: true },
      },
      {
        path: 'judge',
        name: 'judge',
        component: () => import('@/views/JudgeView.vue'),
        meta: { title: '质量评审', icon: 'Medal' },
      },
      {
        path: 'users',
        name: 'users',
        component: () => import('@/views/UsersView.vue'),
        meta: { title: '用户与授权', icon: 'UserFilled', roles: ['admin'] },
      },
      {
        path: 'system',
        name: 'system',
        component: () => import('@/views/SystemView.vue'),
        meta: { title: '系统与审计', icon: 'Setting', roles: ['admin'] },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const isPublic = to.meta.public === true
  if (!isPublic && !tokenStore.access) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (isPublic && tokenStore.access && to.name === 'login') {
    return { name: 'dashboard' }
  }

  // 角色级访问控制：路由声明了 meta.roles 时校验当前用户角色。
  // 注意这是**前端体验**层面的拦截，真正的权限边界在后端（接口会返回 403）；
  // 这里只是避免用户点到无权访问的页面后看到一片空白或一堆报错。
  const required = to.meta.roles as string[] | undefined
  if (required?.length) {
    const auth = useAuthStore()
    // 刷新页面后 store 为空，需先拉取用户信息再判定
    if (!auth.user) {
      await auth.fetchProfile()
    }
    const role = auth.user?.role
    if (!role || !required.includes(role)) {
      return { name: 'dashboard' }
    }
  }
  return true
})

router.afterEach((to) => {
  const title = (to.meta.title as string) || ''
  document.title = title ? `${title} · 工程监理质量智能评估系统` : '工程监理质量智能评估系统'
})

export default router
