/**
 * 角色与权限（与后端 `app/api/deps.py` 的 ROLE_PERMISSIONS 保持一致）。
 *
 * 为什么要镜像一份到前端
 * ----------------------
 * 菜单显示、按钮可用性、落地页选择都需要知道「这个角色能做什么」。
 * 如果各处分别写 `role === 'admin' || role === 'expert'`，规则会很快分叉 ——
 * 后端加了新角色或调整了权限，前端就悄悄显示错。
 * 因此这里以**权限点**为准，菜单与按钮都通过 `can(perm)` 判断。
 *
 * ⚠️ 这仍然只是**体验层**：真正的边界在后端（接口返回 403）。
 *    前端隐藏按钮是为了不让用户点到必然失败的入口，不是安全措施。
 */

export type Permission =
  | 'kb:read'
  | 'kb:write'
  | 'kg:write'
  | 'retrieval:read'
  | 'eval:read'
  | 'eval:write'
  | 'eval:review'
  | 'judge:read'
  | 'judge:write'
  | 'admin:*'
  | '*'

/** 与后端 ROLE_PERMISSIONS 逐项对应，改动时两边必须同步 */
export const ROLE_PERMISSIONS: Record<string, Permission[]> = {
  admin: ['*'],
  kb_manager: ['kb:read', 'kb:write', 'kg:write', 'retrieval:read', 'eval:read', 'judge:read'],
  engineer: ['kb:read', 'retrieval:read', 'eval:read', 'eval:write', 'judge:read'],
  expert: ['kb:read', 'retrieval:read', 'eval:read', 'eval:review', 'judge:read', 'judge:write'],
  viewer: ['kb:read', 'retrieval:read', 'eval:read', 'judge:read'],
}

/** 角色在界面上的显示名（与后端语义一致） */
export const ROLE_LABELS: Record<string, string> = {
  admin: '系统管理员',
  kb_manager: '知识库管理员',
  engineer: '监理工程师',
  expert: '审核人员',
  viewer: '普通用户',
}

/** 按角色的职责描述，用于登录页与个人信息处提示 */
export const ROLE_DUTIES: Record<string, string> = {
  admin: '用户与项目授权、系统配置与审计',
  kb_manager: '规范库维护、图谱构建',
  engineer: '上传规范、发起质量评估',
  expert: '人工复核、确认评估结论',
  viewer: '只读查询授权范围内的报告',
}

/** 该角色是否拥有某权限点（支持 * 与 admin:* 通配） */
export function roleCan(role: string | null | undefined, permission: Permission): boolean {
  const grants = ROLE_PERMISSIONS[(role ?? '').toLowerCase()] ?? []
  return grants.includes('*') || grants.includes(permission)
}

/** 该角色是否管理员（不受项目隔离限制） */
export function roleIsAdmin(role: string | null | undefined): boolean {
  return (role ?? '').toLowerCase() === 'admin'
}

/** 角色显示名 */
export function roleLabel(role: string | null | undefined): string {
  const key = (role ?? '').toLowerCase()
  return ROLE_LABELS[key] ?? role ?? '-'
}
