import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'
import { tokenStore } from '@/api/http'
import { homeForRole, useAuthStore } from '@/stores/auth'
import { roleCan, type Permission } from '@/utils/permissions'

/**
 * 各页面所需的权限点（`meta.perm`）。
 *
 * 菜单显示与路由守卫**共用这一份**声明，避免两处各写一套而分叉。
 * 未声明 perm 的页面只要登录即可访问。
 */
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
    children: [
      {
        // 入口分流：按登录用户的角色跳到对应首页（见 views/RoleHomeView.vue）。
        // 不能用 redirect: '/dashboard' —— 那会把所有角色送到同一个页面。
        path: '',
        name: 'home',
        component: () => import('@/views/RoleHomeView.vue'),
        meta: { title: '首页', hidden: true },
      },
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
        // 上传/解析/发布规范需要写权限；只读角色看知识库没有意义
        meta: { title: '知识库管理', icon: 'Files', perm: 'kb:write' },
      },
      {
        path: 'knowledge/:id',
        name: 'doc-detail',
        component: () => import('@/views/DocDetailView.vue'),
        meta: { title: '规范详情', hidden: true, perm: 'kb:read' },
      },
      {
        path: 'graph',
        name: 'graph',
        component: () => import('@/views/GraphView.vue'),
        // 图谱构建需 kg:write；工程师没有该权限，但其工作（查条款引用链）需要看图
        meta: { title: '知识图谱', icon: 'Share', perm: 'kg:write', permAny: ['kg:write', 'retrieval:read'] },
      },
      {
        path: 'chat',
        name: 'chat',
        component: () => import('@/views/ChatView.vue'),
        meta: { title: '智能问答', icon: 'ChatDotRound', perm: 'retrieval:read' },
      },
      {
        path: 'evaluation',
        name: 'evaluation',
        component: () => import('@/views/EvaluationView.vue'),
        // 审核人员只有 eval:review，进来是为了看复核队列，页面内会隐藏「新建」
        meta: { title: '评估任务', icon: 'Checked', perm: 'eval:read' },
      },
      {
        path: 'evaluation/:id',
        name: 'eval-detail',
        component: () => import('@/views/EvalDetailView.vue'),
        meta: { title: '任务详情', hidden: true, perm: 'eval:read' },
      },
      {
        path: 'judge',
        name: 'judge',
        component: () => import('@/views/JudgeView.vue'),
        meta: { title: '质量评审', icon: 'Medal', perm: 'judge:read' },
      },
      {
        path: 'users',
        name: 'users',
        component: () => import('@/views/UsersView.vue'),
        meta: { title: '用户与授权', icon: 'UserFilled', perm: 'admin:*' },
      },
      {
        path: 'system',
        name: 'system',
        component: () => import('@/views/SystemView.vue'),
        meta: { title: '系统与审计', icon: 'Setting', perm: 'admin:*' },
      },
    ],
  },
  // 未匹配路径回到入口分流页（按角色决定去向），而不是硬编码 /dashboard
  { path: '/:pathMatch(.*)*', redirect: { name: 'home' } },
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
    // 已登录却访问登录页：按角色回各自首页，而不是一律去总览
    const auth = useAuthStore()
    if (!auth.user) {
      await auth.fetchProfile()
    }
    return { name: homeForRole(auth.user?.role) }
  }

  // 角色级访问控制：按路由声明的权限点（meta.perm / meta.permAny）判定。
  // 注意这是**前端体验**层面的拦截，真正的权限边界在后端（接口会返回 403）；
  // 这里只是避免用户点到无权访问的页面后看到一片空白或一堆报错。
  const required = to.meta.perm as Permission | undefined
  const requiredAny = to.meta.permAny as Permission[] | undefined
  if (required || requiredAny?.length) {
    const auth = useAuthStore()
    // 刷新页面后 store 为空，需先拉取用户信息再判定
    if (!auth.user) {
      await auth.fetchProfile()
    }
    const role = auth.user?.role
    const allowed = requiredAny?.length
      ? requiredAny.some((perm) => roleCan(role, perm))
      : roleCan(role, required as Permission)
    if (!allowed) {
      // 回「入口分流页」而非硬编码 dashboard —— 后者对无权角色同样不可达，
      // 会造成连续重定向；home 再按真实角色选择落地页。
      return { name: 'home' }
    }
  }
  return true
})

router.afterEach((to) => {
  const title = (to.meta.title as string) || ''
  document.title = title ? `${title} · 工程监理质量智能评估系统` : '工程监理质量智能评估系统'
})

export default router
