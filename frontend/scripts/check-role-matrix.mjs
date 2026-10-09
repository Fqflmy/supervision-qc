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
  { name: 'dashboard', title: '运行总览' },
  { name: 'knowledge', title: '知识库管理', perm: 'kb:write' },
  { name: 'graph', title: '知识图谱', permAny: ['kg:write', 'retrieval:read'] },
  { name: 'chat', title: '智能问答', perm: 'retrieval:read' },
  { name: 'evaluation', title: '评估任务', perm: 'eval:read' },
  { name: 'judge', title: '质量评审', perm: 'judge:read' },
  { name: 'users', title: '用户与授权', perm: 'admin:*' },
  { name: 'system', title: '系统与审计', perm: 'admin:*' },
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

/** 各角色预期的菜单集合（改动菜单规则时需同步这里，作为显式契约） */
const EXPECTED_MENUS = {
  admin: ['dashboard', 'knowledge', 'graph', 'chat', 'evaluation', 'judge', 'users', 'system'],
  kb_manager: ['dashboard', 'knowledge', 'graph', 'chat', 'evaluation', 'judge'],
  engineer: ['dashboard', 'graph', 'chat', 'evaluation', 'judge'],
  expert: ['dashboard', 'graph', 'chat', 'evaluation', 'judge'],
  viewer: ['dashboard', 'graph', 'chat', 'evaluation', 'judge'],
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

// 只读角色不得看到需要写权限的入口
for (const role of ['viewer']) {
  const menus = visibleMenus(backend[role] ?? [])
  if (menus.includes('knowledge')) {
    failures += 1
    console.log(`  [FAIL] ${role}（只读）不该看到知识库管理（需 kb:write）`)
  }
}
console.log('  [OK] 只读角色不显示写权限入口')

console.log('')
if (failures > 0) {
  console.log(`[FAIL] ${failures} 项未通过`)
  process.exit(1)
}
console.log('[OK] 前后端权限一致，各角色菜单符合契约')
