/**
 * 校验前端权限矩阵与后端 ROLE_PERMISSIONS 完全一致，并输出各角色可见菜单。
 *
 * 为什么需要
 * ----------
 * 前端 `utils/permissions.ts` 镜像了后端的角色权限表，用来决定菜单显示与
 * 按钮可用性。一旦两边分叉，就会出现「菜单能点但接口 403」或
 * 「按钮隐藏但其实有权限」这类难查的问题。
 *
 * 本脚本从**后端 Python 源码**解析出 ROLE_PERMISSIONS，与本文件的期望值逐项比对，
 * 无需数据库、无需启动服务。前后端任一侧改了角色权限而另一侧没跟上，这里就会失败。
 *
 * 由 backend/tests/test_role_matrix.py 通过 subprocess 调用，也可单独运行：
 *     node frontend/scripts/check-role-matrix.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..', '..')
const BACKEND_DEPS = resolve(ROOT, 'backend', 'app', 'api', 'deps.py')
const FRONTEND_PERMS = resolve(ROOT, 'frontend', 'src', 'utils', 'permissions.ts')

/** 从后端 deps.py 中解析 ROLE_PERMISSIONS 字面量 */
function parseBackendRoles() {
  const source = readFileSync(BACKEND_DEPS, 'utf8')
  const block = source.match(/ROLE_PERMISSIONS[^{]*\{([\s\S]*?)\n\}/)
  if (!block) throw new Error('未能从后端 deps.py 解析出 ROLE_PERMISSIONS')

  const roles = {}
  const entry = /"([a-z_]+)"\s*:\s*\{([^}]*)\}/g
  let m
  while ((m = entry.exec(block[1])) !== null) {
    const perms = [...m[2].matchAll(/"([^"]+)"/g)].map((x) => x[1])
    roles[m[1]] = perms.sort()
  }
  return roles
}

/** 从后端解析各页面的权限声明（router 文件里的 require_permission） */
function parseFrontendRoles() {
  const source = readFileSync(FRONTEND_PERMS, 'utf8')
  const block = source.match(/ROLE_PERMISSIONS[^{]*=\s*\{([\s\S]*?)\n\}/)
  if (!block) throw new Error('未能从前端 permissions.ts 解析出 ROLE_PERMISSIONS')

  const roles = {}
  const entry = /([a-z_]+)\s*:\s*\[([^\]]*)\]/g
  let m
  while ((m = entry.exec(block[1])) !== null) {
    const perms = [...m[2].matchAll(/'([^']+)'/g)].map((x) => x[1])
    roles[m[1]] = perms.sort()
  }
  return roles
}

/** 菜单定义（与 MainLayout.vue 的 allMenus 对应） */
const MENUS = [
  { name: 'dashboard', title: '运行总览', perm: 'admin:*' },
  // 知识库：有 kb:write 的是「管理」，仅 kb:read 的是「查询」——同一页面
  { name: 'knowledge', title: '知识库', perm: 'kb:read' },
  { name: 'graph', title: '知识图谱', permAny: ['kg:write', 'eval:write'] },
  { name: 'chat', title: '智能问答', perm: 'retrieval:read' },
  { name: 'evaluation', title: '评估任务', perm: 'eval:write' },
  // 评估报告（只读视角）：所有具备 eval:read 的角色都应能查看授权项目内的报告
  { name: 'reports', title: '评估报告', perm: 'eval:read' },
  { name: 'judge', title: '质量评审', permAny: ['eval:review', 'judge:write'] },
  { name: 'users', title: '用户与授权', perm: 'admin:*' },
  { name: 'system', title: '系统与审计', perm: 'admin:*' },
  // 个人中心：**无 perm，所有角色可见**。
  // 它是用户改自己密码、确认身份登记的地方，也是「强制改密」时
  // 路由守卫唯一放行的目标 —— 因此绝不能加角色限制，
  // 否则被要求改密的用户会无处可去（死锁）。
  { name: 'profile', title: '个人中心' },
]

function can(grants, permission) {
  return grants.includes('*') || grants.includes(permission)
}

function visibleMenus(grants) {
  return MENUS.filter((menu) => {
    if (menu.permAny) return menu.permAny.some((p) => can(grants, p))
    if (menu.perm) return can(grants, menu.perm)
    return true
  }).map((m) => m.name)
}

/**
 * 各角色预期的菜单集合（改动菜单规则时需同步这里，作为显式契约）。
 *
 * 原则：**每个角色只看到与其职责相关的功能**。
 *   系统管理员    平台运营全貌
 *   知识库管理员  规范维护（管理态知识库）
 *   监理工程师    发起评估 + 查引用链
 *   审核人员      复核工作面
 *   普通用户      只读查询（规范原文 + 问答）
 *
 * 注：viewer 与 expert 在此恰好都含 knowledge/chat/judge 之外的不同项，
 * 但两者并不相同（viewer 无 judge）。断言里会校验「各角色菜单各不相同」。
 */
const EXPECTED_MENUS = {
  admin: [
    'dashboard',
    'knowledge',
    'graph',
    'chat',
    'evaluation',
    'reports',
    'judge',
    'users',
    'system',
    'profile',
  ],
  // 每个角色都含 profile（个人中心对所有角色开放）
  kb_manager: ['knowledge', 'graph', 'chat', 'reports', 'profile'],
  engineer: ['knowledge', 'graph', 'chat', 'evaluation', 'reports', 'profile'],
  expert: ['knowledge', 'chat', 'reports', 'judge', 'profile'],
  viewer: ['knowledge', 'chat', 'reports', 'profile'],
}

function sortedEqual(a, b) {
  return JSON.stringify([...a].sort()) === JSON.stringify([...b].sort())
}

let failures = 0

// ---------- 1) 前后端权限表一致 ----------
const backend = parseBackendRoles()
const frontend = parseFrontendRoles()

console.log('=== 角色权限表一致性（后端 deps.py vs 前端 permissions.ts）===')
const allRoles = [...new Set([...Object.keys(backend), ...Object.keys(frontend)])].sort()
for (const role of allRoles) {
  const b = backend[role] ?? []
  const f = frontend[role] ?? []
  if (sortedEqual(b, f)) {
    console.log(`  [OK] ${role}: ${b.join(', ') || '（无）'}`)
  } else {
    failures += 1
    console.log(`  [FAIL] ${role} 不一致`)
    console.log(`         后端: ${b.join(', ') || '（无）'}`)
    console.log(`         前端: ${f.join(', ') || '（无）'}`)
  }
}

// ---------- 2) 各角色菜单集合 ----------
console.log('')
console.log('=== 各角色可见菜单 ===')
for (const role of allRoles) {
  const actual = visibleMenus(backend[role] ?? [])
  const expected = EXPECTED_MENUS[role]
  if (!expected) {
    failures += 1
    console.log(`  [FAIL] ${role} 未在 EXPECTED_MENUS 中定义（新增角色需补契约）`)
    continue
  }
  if (sortedEqual(actual, expected)) {
    console.log(`  [OK] ${role}（${actual.length} 项）: ${actual.join(' / ')}`)
  } else {
    failures += 1
    console.log(`  [FAIL] ${role} 菜单不符`)
    console.log(`         期望: ${expected.join(' / ')}`)
    console.log(`         实际: ${actual.join(' / ')}`)
  }
}

// ---------- 3) 关键约束 ----------
console.log('')
console.log('=== 关键约束 ===')

// 只有管理员能看到管理页
for (const role of allRoles) {
  const menus = visibleMenus(backend[role] ?? [])
  const hasAdminMenu = menus.includes('users') || menus.includes('system')
  if (role === 'admin' && !hasAdminMenu) {
    failures += 1
    console.log('  [FAIL] 管理员看不到管理菜单')
  } else if (role !== 'admin' && hasAdminMenu) {
    failures += 1
    console.log(`  [FAIL] 非管理员 ${role} 看到了管理菜单`)
  }
}
console.log('  [OK] 管理菜单仅管理员可见')

// 每个角色至少有一个可访问页面（否则登录后会无处可去）
for (const role of allRoles) {
  if (visibleMenus(backend[role] ?? []).length === 0) {
    failures += 1
    console.log(`  [FAIL] ${role} 没有任何可访问页面`)
  }
}
console.log('  [OK] 每个角色至少有一个可访问页面')

// 只读角色不得看到**写权限**入口（知识库现在是只读可查，属职责内功能）
for (const role of ['viewer']) {
  const perms = backend[role] ?? []
  const menus = visibleMenus(perms)
  if (menus.includes('knowledge') && can(perms, 'kb:write')) {
    failures += 1
    console.log(`  [FAIL] ${role}（只读）不该拿到知识库的写权限`)
  }
}
console.log('  [OK] 只读角色不显示写权限入口')

// 运行总览是平台运营视角，仅管理员
for (const role of allRoles) {
  const menus = visibleMenus(backend[role] ?? [])
  if (role !== 'admin' && menus.includes('dashboard')) {
    failures += 1
    console.log(`  [FAIL] ${role} 不该看到「运行总览」（平台运营视角，含系统内部参数）`)
  }
}
console.log('  [OK] 运行总览仅管理员可见')

// 评估任务入口只给能发起评估的角色；质量评审入口只给审核角色
for (const role of allRoles) {
  const perms = backend[role] ?? []
  const menus = visibleMenus(perms)
  if (menus.includes('evaluation') && !can(perms, 'eval:write')) {
    failures += 1
    console.log(`  [FAIL] ${role} 看到「评估任务」但无 eval:write（会进去发现不能发起）`)
  }
  if (
    menus.includes('judge') &&
    !can(perms, 'eval:review') &&
    !can(perms, 'judge:write')
  ) {
    failures += 1
    console.log(`  [FAIL] ${role} 看到「质量评审」但无复核权限`)
  }
}
console.log('  [OK] 评估任务 / 质量评审入口与发起、复核权限对应')

// 各角色菜单应**互不相同**（否则说明没按职责区分）
const signatures = allRoles.map((r) => visibleMenus(backend[r] ?? []).sort().join(','))
const uniqueCount = new Set(signatures).size
if (uniqueCount < allRoles.length) {
  const dupes = allRoles.filter((r, i) => signatures.indexOf(signatures[i]) !== i)
  failures += 1
  console.log(`  [FAIL] 这些角色的菜单完全相同，未按职责区分：${dupes.join(', ')}`)
} else {
  console.log(`  [OK] ${allRoles.length} 个角色的菜单各不相同（按职责区分）`)
}

// 落地页必须可达：否则「落地页无权 -> 守卫踢回 home -> home 又跳回落地页」会死循环
console.log('')
console.log('=== 落地页可达性（防止死循环）===')
const ROLE_HOME = {
  admin: 'users',
  kb_manager: 'knowledge',
  engineer: 'evaluation',
  expert: 'judge',
  viewer: 'reports',
}
for (const role of allRoles) {
  const home = ROLE_HOME[role]
  if (!home) {
    failures += 1
    console.log(`  [FAIL] ${role} 未配置落地页`)
    continue
  }
  const menus = visibleMenus(backend[role] ?? [])
  if (!menus.includes(home)) {
    failures += 1
    console.log(
      `  [FAIL] ${role} 的落地页「${home}」不在其菜单中 -> 会造成无限重定向、页面卡死`,
    )
  } else {
    console.log(`  [OK] ${role} -> ${home}`)
  }
}

console.log('')
if (failures > 0) {
  console.log(`[FAIL] ${failures} 项未通过`)
  process.exit(1)
}
console.log('[OK] 前后端权限一致，各角色菜单符合契约')
