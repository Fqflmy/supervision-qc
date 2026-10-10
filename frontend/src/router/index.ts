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
        // 平台运营视角：全库规模、组件自检、检索链路参数，仅管理员
        meta: { title: '运行总览', icon: 'DataBoard', perm: 'admin:*' },
      },
      {
        path: 'knowledge',
        name: 'knowledge',
        component: () => import('@/views/KnowledgeView.vue'),
        // 页面同时服务两种角色：有 kb:write 的可管理（上传/解析/发布），
        // 仅 kb:read 的为只读查询 —— 写操作按钮由页面内的 canWriteKb 控制。
        // 因此这里只要 kb:read 即可进入（委托：只读用户也应能查规范原文）。
        meta: { title: '知识库', icon: 'Files', perm: 'kb:read' },
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
        // 构建图谱需 kg:write；工程师用来看条款引用链（支撑质量判定）
        meta: { title: '知识图谱', icon: 'Share', permAny: ['kg:write', 'eval:write'] },
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
        // 发起评估是监理工程师的职责；审核人员改在质量评审页处理待复核
        meta: { title: '评估任务', icon: 'Checked', perm: 'eval:write' },
      },
      {
        path: 'evaluation/:id',
        name: 'eval-detail',
        component: () => import('@/views/EvalDetailView.vue'),
        meta: { title: '任务详情', hidden: true, perm: 'eval:read' },
      },
      {
        // 个人中心：**所有已登录角色**都可访问（无 meta.perm），
        // 因为它是用户管理自己账号（改密）与确认身份登记的地方。
        // 也是强制改密时的唯一放行目标，因此绝不能加角色权限限制。
        path: 'profile',
        name: 'profile',
        component: () => import('@/views/ProfileView.vue'),
        meta: { title: '个人中心', hidden: true },
      },
      {
        // 评估报告（只读视角）：任何有 eval:read 的角色都能看**授权项目内**的报告。
        // 与 evaluation（发起方工作台，需 eval:write）分开，让只读用户也有入口。
        path: 'reports',
        name: 'reports',
        component: () => import('@/views/ReportListView.vue'),
        meta: { title: '评估报告', icon: 'Document', perm: 'eval:read' },
      },
      {
        // 报告详情（只读）：只呈现报告与签发状态，不含 Token/迭代/执行轨迹等运维字段。
        // 与 eval-detail（发起方/复核方工作台）区分 —— 只读用户不该看到写操作入口。
        path: 'reports/:id',
        name: 'report-detail',
        component: () => import('@/views/ReportDetailView.vue'),
        meta: { title: '报告详情', hidden: true, perm: 'eval:read' },
      },
      {
        path: 'judge',
        name: 'judge',
        component: () => import('@/views/JudgeView.vue'),
        // 审核人员的工作面（含待复核队列）；只读用户在报告详情内看评分即可
        meta: { title: '质量评审', icon: 'Medal', permAny: ['eval:review', 'judge:write'] },
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

  // ---- 强制修改密码 ----
  // 管理员重置过密码（must_change_password=True）时，把用户**留在个人中心**直到改密完成。
  // 登录后的提示横幅做不到这一点：用户可以无视横幅继续操作。
  //
  // ⚠️ 两条必须放行的路径，否则用户会被彻底卡住：
  // 1. `profile` 本身 —— 否则到不了改密界面，形成死锁；
  // 2. `login` 已在上面的 isPublic 分支处理 —— 用户必须还能退出登录换账号。
  if (!isPublic) {
    const auth = useAuthStore()
    if (!auth.user) {
      await auth.fetchProfile()
    }
    if (auth.user?.must_change_password && to.name !== 'profile') {
      return { name: 'profile' }
    }
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
      //
      // 但要防死循环：若落地页本身就无权（ROLE_HOME 配错），
      // 踢回 home 后 home 又跳回该落地页会无限循环、页面卡死。
      // 因此当目标已是 home 时不再重定向，直接放行由 RoleHomeView 处理。
      if (to.name === 'home') {
        return true
      }
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
