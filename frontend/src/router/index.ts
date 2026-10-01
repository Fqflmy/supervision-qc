import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'
import { tokenStore } from '@/api/http'

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

router.beforeEach((to) => {
  const isPublic = to.meta.public === true
  if (!isPublic && !tokenStore.access) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (isPublic && tokenStore.access && to.name === 'login') {
    return { name: 'dashboard' }
  }
  return true
})

router.afterEach((to) => {
  const title = (to.meta.title as string) || ''
  document.title = title ? `${title} · 工程监理质量智能评估系统` : '工程监理质量智能评估系统'
})

export default router
