/** 与后端 OpenAPI 契约对应的 TypeScript 类型定义。 */
import { api, download, saveBlob, type PageData } from './http'

export { api }
export type { PageData }

// --------------------------------------------------------------------------- //
// 认证
// --------------------------------------------------------------------------- //
export interface User {
  id: number
  username: string
  full_name?: string | null
  /** 人员身份（账号与身份分离：username 是登录凭据，这些字段标识「这个人是谁」） */
  employee_no?: string | null
  org_name?: string | null
  department?: string | null
  position?: string | null
  cert_no?: string | null
  /** 报告中的签认署名；留空时展示回退到 full_name */
  signature?: string | null
  email?: string | null
  phone?: string | null
  role: 'admin' | 'kb_manager' | 'engineer' | 'expert' | 'viewer' | string
  specialties: string[]
  project_ids: number[]
  is_active: boolean
  /**
   * 管理员重置过密码，需先到个人中心自行修改。
   * 为 true 时路由守卫会把用户**留在个人中心**直到改密完成。
   */
  must_change_password?: boolean
}

export interface LoginResult {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: User
}

/** 登录页可选的身份（演示环境） */
export interface DemoIdentity {
  role: string
  label: string
  description: string
  /** 登录后默认跳转的路由名 */
  home: string
  username: string
  password: string
}

export interface DemoIdentityResult {
  enabled: boolean
  identities: DemoIdentity[]
}

export const authApi = {
  login: (username: string, password: string) =>
    api.post<LoginResult>('/auth/login', { username, password }),
  me: () => api.get<User>('/auth/me'),
  logout: () => api.post<{ message: string }>('/auth/logout'),
  /** 获取演示身份。生产环境返回 enabled=false，登录页不显示选择器。 */
  demoIdentities: () => api.get<DemoIdentityResult>('/auth/demo-identities'),

  /**
   * 个人中心：自己的身份信息 + 被授权的项目。
   *
   * 与 `usersApi.getUser()` 的区别：后者是**管理员视角**（可看他人、含管理字段）；
   * 本接口只能看自己，不含角色 / 项目授权等由管理员维护的字段。
   */
  profile: () => api.get<ProfileResult>('/auth/profile'),

  /**
   * 个人中心：自助修改密码（需验证当前密码）。
   *
   * ⚠️ `old_password` 必须提供：否则 token 泄漏即等于账号被永久接管
   * （攻击者可静默改密，把真实用户锁在外面）。后端会校验。
   *
   * 成功后后端**重新签发 token**（清除 must_change_password 标记），
   * 因此调用方必须用返回的 token 覆盖本地存储，否则会陷入「改完还被拦」。
   */
  changePassword: (payload: { old_password: string; new_password: string }) =>
    api.post<LoginResult>('/auth/password', payload),
}

/** 个人中心返回：身份信息 + 授权项目 */
export interface ProfileResult {
  user: User
  projects: { id: number; code: string; name: string }[]
  /** 管理员不受项目隔离限制 */
  is_admin: boolean
}

// --------------------------------------------------------------------------- //
// 知识库
// --------------------------------------------------------------------------- //
export interface KnowledgeBase {
  id: number
  code: string
  name: string
  specialty?: string | null
  description?: string | null
  is_active: boolean
}

export interface SpecDoc {
  id: number
  kb_id?: number | null
  spec_code: string
  spec_name: string
  issuer?: string | null
  specialty?: string | null
  region_level?: string | null
  status: string
  kg_built: boolean
  created_at?: string | null
}

export interface DocVersion {
  id: number
  doc_id: number
  version_label: string
  file_name: string
  file_size?: number | null
  file_type?: string | null
  page_count?: number | null
  parse_status: string
  parse_error?: string | null
  is_current: boolean
  chunk_count: number
}

export interface SpecDocDetail extends SpecDoc {
  versions: DocVersion[]
}

export interface Chunk {
  id: number
  doc_id: number
  clause_no?: string | null
  chapter_path?: string | null
  chunk_index: number
  page_no?: number | null
  content: string
  token_count?: number | null
  status: string
}

export interface IngestResult {
  ingest: {
    doc_id: number
    version_id: number
    chunk_count: number
    indexed: number
    page_count: number
    char_count: number
    namespace: string
    degraded_embedding: boolean
  }
  knowledge_graph: Record<string, unknown> | null
}

export const kbApi = {
  listKbs: () => api.get<KnowledgeBase[]>('/kb'),
  createKb: (payload: Partial<KnowledgeBase>) => api.post<KnowledgeBase>('/kb', payload),
  listDocs: (params: {
    page?: number
    page_size?: number
    specialty?: string
    status?: string
    keyword?: string
  }) => api.get<PageData<SpecDoc>>('/kb/documents', params),
  getDoc: (id: number) => api.get<SpecDocDetail>(`/kb/documents/${id}`),
  upload: (form: FormData) =>
    api.upload<{ created: Record<string, unknown>[]; errors: { file: string; error: string }[] }>(
      '/kb/documents',
      form,
    ),
  parse: (id: number, buildKg = false) =>
    api.post<IngestResult>(`/kb/documents/${id}/parse`, { force: false, build_kg: buildKg }),
  listChunks: (id: number, page = 1, pageSize = 50) =>
    api.get<PageData<Chunk>>(`/kb/documents/${id}/chunks`, { page, page_size: pageSize }),
  updateChunk: (chunkId: number, payload: Partial<Chunk>) =>
    api.patch<Chunk>(`/kb/chunks/${chunkId}`, payload),
  publish: (id: number, action: 'publish' | 'unpublish' | 'abolish', reason?: string) =>
    api.post<{ doc_id: number; status: string }>(`/kb/documents/${id}/publish`, { action, reason }),
  extractKg: (docId: number, concurrency = 4) =>
    api.post<Record<string, unknown>>('/kb/kg/extract', { doc_id: docId, concurrency }),
  kgStats: () => api.get<Record<string, unknown>>('/kb/kg/stats'),
}

// --------------------------------------------------------------------------- //
// 检索
// --------------------------------------------------------------------------- //
export interface RetrievedClause {
  chunk_id: number
  clause_no?: string | null
  spec_code?: string | null
  spec_name?: string | null
  chapter_path?: string | null
  page_no?: number | null
  content?: string
  rrf_score: number
  relevance_score: number
  source: string[]
  kg_related: Record<string, unknown>[]
  citation: Record<string, unknown>
}

export interface RetrievalResult {
  query: string
  understanding: Record<string, unknown>
  sub_queries: string[]
  results: RetrievedClause[]
  no_evidence: boolean
  latency_ms: number
  degraded: boolean
  debug: Record<string, unknown>
}

export interface ChatResult {
  answer: string
  no_evidence: boolean
  citations: {
    index: number
    clause_no?: string
    spec_code?: string
    spec_name?: string
    location: string
    chunk_id: number
    relevance_score: number
    content: string
  }[]
  sub_queries: string[]
  latency_ms: number
}

export const retrievalApi = {
  search: (payload: {
    query: string
    kb_ids?: number[]
    specialty?: string
    top_k?: number
    enable_multi_query?: boolean
    enable_kg_expand?: boolean
  }) => api.post<RetrievalResult>('/retrieval/search', payload),
  chat: (payload: { query: string; kb_ids?: number[]; specialty?: string; top_k?: number }) =>
    api.post<ChatResult>('/retrieval/chat', payload),
}

// --------------------------------------------------------------------------- //
// 评估任务
// --------------------------------------------------------------------------- //
export interface EvalSubtask {
  id: number
  seq: number
  name: string
  criterion?: string | null
  required_evidence?: string | null
  specialty?: string | null
  query?: string | null
  status: string
}

export interface MatchResult {
  id: number
  clause_no?: string | null
  spec_code?: string | null
  spec_name?: string | null
  verdict: string
  confidence?: number | null
  relevance_score?: number | null
  evidence?: string | null
  reasoning?: string | null
  risk_level?: string | null
  remediation?: string | null
  citation_json?: Record<string, unknown> | null
}

export interface AgentStep {
  id: number
  step: string
  seq: number
  status: string
  duration_ms?: number | null
  token_used: number
  iteration: number
  retry_count: number
  output_digest?: Record<string, unknown> | null
  error?: string | null
}

export interface EvalTask {
  id: string
  project_id?: number | null
  eval_type: string
  specialty?: string | null
  title?: string | null
  current_state: string
  current_step?: string | null
  iteration_count: number
  no_progress_rounds: number
  progress: number
  total_tokens: number
  guard_reason?: string | null
  started_at?: string | null
  finished_at?: string | null
  created_at?: string | null
  /** 报告复核状态：待复核 / 已签发 / 已复核不合格 */
  review_status?: ReviewStatus
  /** 乐观锁版本号（人工裁定时回传，防并发改判） */
  version?: number
  /** Judge 评审摘要（仅 with_judge=true 时返回） */
  judge?: {
    review_id: number
    total_score?: number | null
    grade?: string | null
    needs_human?: boolean | null
    threshold?: number | null
  } | null
}

export interface EvalTaskDetail extends EvalTask {
  input_payload: Record<string, unknown>
  options: Record<string, unknown>
  state_snapshot?: Record<string, unknown> | null
  error_state?: Record<string, unknown> | null
  subtasks: EvalSubtask[]
  matches: MatchResult[]
  steps: AgentStep[]
  report_id?: string | null
  judge?: {
    review_id: number
    total_score: number | null
    grade: string | null
    needs_human: boolean
    has_conflict: boolean
    comment: string | null
  } | null
}

export interface EvalRunResult {
  task_id: string
  thread_id: string
  current_state: string
  iteration_count: number
  no_progress_rounds: number
  subtasks: EvalSubtask[]
  matches: MatchResult[]
  analysis: Record<string, unknown>
  report: Record<string, unknown>
  report_id?: string | null
  guard: Record<string, unknown>
  errors: Record<string, unknown>[]
  token_used: number
  elapsed_ms: number
  checkpoint_backend: string
  degraded: boolean
}

export interface EvalReport {
  id: string
  task_id: string
  conclusion?: string | null
  overall_verdict?: string | null
  risk_level?: string | null
  summary?: string | null
  markdown?: string | null
  content: Record<string, unknown>
  basis_count: number
  non_compliance_count: number
  generator_model?: string | null
  is_final: boolean
  created_at?: string | null
  judge?: JudgeReviewDetail | null
}

export interface JudgeScoreItem {
  dimension: string
  dimension_label?: string
  score: number | null
  weight: number | null
  comment: string | null
  is_conflict: boolean
}

export interface JudgeReviewDetail {
  review_id?: number
  id?: number
  judge_model?: string | null
  total_score: number | null
  grade: string | null
  grade_label?: string
  needs_human: boolean
  has_conflict: boolean
  threshold?: number | null
  comment: string | null
  citation_check?: CitationCheck | null
  scores: JudgeScoreItem[]
}

export interface CitationCheck {
  total: number
  valid: number
  hallucinations: number
  abolished: number
  inconsistent: number
  accuracy: number
  items: {
    clause_no: string | null
    spec_code: string | null
    exists: boolean
    source: string
    abolished: boolean
    hallucination: boolean
    semantic_consistent: boolean | null
  }[]
}

export const evalApi = {
  create: (payload: {
    project_id?: number
    eval_type?: string
    specialty?: string
    title?: string
    object: { part?: string; project_name?: string; description?: string; records: { name: string; content: string }[] }
    kb_ids?: number[]
    options?: Record<string, unknown>
  }) => api.post<{ task_id: string; current_state: string }>('/eval/tasks', payload),
  list: (params: { page?: number; page_size?: number; state?: string; mine?: boolean; with_judge?: boolean; with_report?: boolean }) =>
    api.get<PageData<EvalTask>>('/eval/tasks', params),
  detail: (id: string) => api.get<EvalTaskDetail>(`/eval/tasks/${id}`),
  run: (id: string, resume = false, force = false) =>
    api.post<EvalRunResult>(`/eval/tasks/${id}/run`, undefined, { resume, force }),
  submit: (id: string) => api.post<{ task_id: string; current_state: string }>(`/eval/tasks/${id}/submit`),
  report: (id: string) => api.get<EvalReport>(`/eval/tasks/${id}/report`),
  /**
   * 下载评估报告 PDF（正式可交付形态）。
   *
   * 走 api.download 而不是 api.get —— 二进制响应不是 `{code,message,data}` 信封，
   * 用 request() 会解析失败。
   *
   * ⚠️ 返回的 `handled === true` **不是失败**：浏览器装了下载管理器扩展
   * （IDM/迅雷等）时，扩展在**网络层**拦截并自行完成下载，页面 fetch 只拿到
   * 被取消的空响应（204 / 0 字节）。此时调用方**不要再保存 blob**，
   * 否则会留下 0 字节的损坏文件。详见 `api/http.ts` 的 `download()`。
   *
   * 导出行为会在后端留下审计记录（AuditAction.REPORT_EXPORT）。
   */
  downloadReportPdf: (id: string) => download(`/eval/tasks/${id}/report/pdf`),
  resume: (id: string, payload: { action: string; corrected_matches?: Record<string, unknown>[]; comment?: string }) =>
    api.post<{ task_id: string; current_state: string; feedback: number }>(`/eval/tasks/${id}/resume`, payload),
}

// --------------------------------------------------------------------------- //
// Judge
// --------------------------------------------------------------------------- //
export interface JudgeDashboard {
  review_count: number
  avg_total_score: number
  needs_human_count: number
  conflict_count: number
  grade_distribution: Record<string, number>
  dimension_averages: Record<string, number>
  hallucination_total: number
  threshold: number
  weights: Record<string, number>
}

export const judgeApi = {
  score: (reportId: string, payload: { threshold?: number; cross_model?: boolean } = {}) =>
    api.post<Record<string, unknown> & { review_id: number }>(`/judge/reports/${reportId}/score`, payload),
  reviews: (reportId: string) => api.get<JudgeReviewDetail[]>(`/judge/reports/${reportId}/reviews`),
  dashboard: () => api.get<JudgeDashboard>('/judge/dashboard'),
}

// --------------------------------------------------------------------------- //
// 系统
// --------------------------------------------------------------------------- //
export interface HealthInfo {
  status: string
  app: string
  version: string
  environment: string
  components: Record<string, Record<string, unknown>>
}

export interface MetricsInfo {
  documents_total: number
  chunks_total: number
  tasks_by_state: Record<string, number>
  tokens_total: number
  vector_index: Record<string, unknown>
  /** global = 管理员看到的全局口径；visible = 仅统计当前用户可见范围 */
  scope?: 'global' | 'visible'
}

/** 人工复核裁定结论 */
export type HumanVerdict = 'qualified' | 'unqualified'

/** 报告复核状态（由 human_verdict + is_final 派生） */
export type ReviewStatus = 'pending' | 'signed' | 'rejected'

/** 人工裁定与签发状态 */
/**
 * 报告复核信息（复核决定的公共字段）。
 *
 * ⚠️ 术语对象（本项目最易混淆处）
 * ------------------------------
 * 这里描述的是**人工对「AI 报告」的决定**，对象是报告本身；
 * 工程质量结论在 `machine_verdict*`（AI 判定），两者并存、互不覆盖。
 *
 * 因此复核决定一律显示为**动作词**（接受报告 / 退回报告），
 * 而不是「合格 / 不合格」—— 后者会被误读成工程质量结论。
 */
export interface ReviewInfo {
  /** 人工复核决定（动作词）：accept=接受报告 / return=退回报告 */
  review_decision?: string | null
  review_decision_label?: string | null
  /** 决定对象的说明，界面必须与决定值同时展示 */
  review_decision_object_note?: string | null
  /** 历史字段（qualified / unqualified），保留兼容；显示请用 review_decision_label */
  human_verdict?: HumanVerdict | null
  human_verdict_label?: string | null
  review_comment?: string | null
  reviewed_by?: number | null
  reviewed_by_name?: string | null
  /** 复核人岗位（报告签认需体现职务） */
  reviewed_by_position?: string | null
  reviewed_by_org?: string | null
  reviewed_at?: string | null
  is_final: boolean
  review_status: ReviewStatus
  review_status_label: string
  /** 复核状态的后果说明（如「不得作为正式依据」） */
  review_status_note?: string | null
}

export interface ReviewStatusInfo extends ReviewInfo {
  task_id: string
  report_id?: string | null
  current_state: string
  version: number
  /**
   * 机器结论（AI 生成，不被人工覆盖）。
   * ⚠️ 对象是**工程质量**，与下面的复核决定是两件事。
   */
  machine_verdict?: string | null
  machine_verdict_label?: string | null
  /** 机器结论的对象说明（如「AI 对工程质量的判定」） */
  machine_verdict_object_note?: string | null
  risk_level?: string | null
}

export interface ReviewDecisionResult {
  task_id: string
  report_id: string
  /** 人工复核决定（动作词），如「接受报告」 */
  review_decision?: string | null
  review_decision_label?: string | null
  /** 历史字段（qualified / unqualified） */
  human_verdict: HumanVerdict
  human_verdict_label: string
  review_comment: string
  reviewed_by?: number | null
  reviewed_at?: string | null
  is_final: boolean
  current_state: string
  /** 是否已驳回重跑（后台执行） */
  rerun: boolean
  revision_count: number
  machine_verdict?: string | null
  version: number
}

export interface PendingReviewItem {
  task_id: string
  title?: string | null
  specialty?: string | null
  current_state: string
  guard_reason?: string | null
  version: number
  finished_at?: string | null
  machine_verdict?: string | null
  risk_level?: string | null
  review_status: ReviewStatus
}

export const reviewApi = {
  /**
   * 提交人工复核决定（FR-AGT-11 报告签发）。
   *
   * ⚠️ 本操作的对象是 **AI 报告**，不是工程质量。
   * 工程质量结论由 AI 给出并存于 overall_verdict，人工复核**不覆盖**它。
   * erdict 的内部值仍是 qualified / unqualified（历史字段），
   * 界面一律显示为动作词「接受报告 / 退回报告」——
   * 动作词天然绑定对象，不会像「复核合格」那样被误读成「工程合格」。
   *
   * 退回报告（unqualified）时必须给 comment；
   * rerun 决定「退回并重新评估」还是「直接结束（不予签发）」。
   */
  decide: (
    taskId: string,
    payload: {
      verdict: HumanVerdict
      comment: string
      rerun?: boolean
      corrected_matches?: { match_id?: number; verdict?: string }[]
      expected_version?: number
    },
  ) => api.post<ReviewDecisionResult>(`/eval/tasks/${taskId}/review`, payload),
  status: (taskId: string) => api.get<ReviewStatusInfo>(`/eval/tasks/${taskId}/review`),
  pending: (params: { page?: number; page_size?: number }) =>
    api.get<PageData<PendingReviewItem>>('/eval/reviews/pending', params),
}

export interface AuditLog {  id: number
  trace_id?: string | null
  username?: string | null
  action: string
  object_type?: string | null
  object_id?: string | null
  result: string
  ip?: string | null
  created_at?: string | null
}

export const systemApi = {
  health: () => api.get<HealthInfo>('/health'),
  metrics: () => api.get<MetricsInfo>('/metrics'),
  auditLogs: (params: { page?: number; page_size?: number; action?: string }) =>
    api.get<PageData<AuditLog>>('/admin/audit-logs', params),
  config: () => api.get<Record<string, Record<string, unknown>>>('/admin/config'),
  rebuildIndex: () => api.post<Record<string, unknown>>('/admin/rebuild-index'),
}

// --------------------------------------------------------------------------- //
// 用户与项目授权（管理员）
// --------------------------------------------------------------------------- //
/** 可分配的角色（与后端 ROLE_PERMISSIONS 的键一致） */
export type AssignableRole = 'admin' | 'kb_manager' | 'engineer' | 'expert' | 'viewer'

export interface ManagedUser {
  id: number
  username: string
  full_name?: string | null
  /** 人员身份（账号与身份分离：username 是登录凭据，这些字段标识「这个人是谁」） */
  employee_no?: string | null
  org_name?: string | null
  department?: string | null
  position?: string | null
  cert_no?: string | null
  /** 报告中的签认署名；留空时展示回退到 full_name */
  signature?: string | null
  email?: string | null
  phone?: string | null
  role: AssignableRole | string
  /** 授权访问的项目 ID。非管理员必须至少有一个，否则看不到任何项目数据。 */
  project_ids: number[]
  specialties: string[]
  is_active: boolean
  /** 是否被要求下次登录后修改密码（管理员重置密码后置为 true） */
  must_change_password?: boolean
  last_login_at?: string | null
  created_at?: string | null
}

export interface ProjectItem {
  id: number
  code: string
  name: string
  specialty?: string | null
  status: string
}

export interface UserCreatePayload {
  username: string
  password: string
  full_name?: string | null
  /** 人员身份（账号与身份分离：username 是登录凭据，这些字段标识「这个人是谁」） */
  employee_no?: string | null
  org_name?: string | null
  department?: string | null
  position?: string | null
  cert_no?: string | null
  /** 报告中的签认署名；留空时展示回退到 full_name */
  signature?: string | null
  email?: string | null
  phone?: string | null
  role: AssignableRole
  project_ids: number[]
  specialties?: string[]
}

/** 当前用户的访问范围（用于「缺授权」提示） */
export interface MyScope {
  user_id?: number | null
  username: string
  role: string
  is_admin: boolean
  project_ids: number[]
  accessible_kb_count?: number | null
  /** 非空表示存在配置问题，需提示用户 */
  warning?: string | null
}

export interface MyProjectsResult {
  items: ProjectItem[]
  total: number
  /** 只被授权一个项目时为 true —— 前端无需让用户选择 */
  can_auto_select: boolean
}

export const usersApi = {
  listProjects: () => api.get<ProjectItem[]>('/admin/projects'),
  /** 当前用户被授权的项目（所有登录用户可调，供创建任务时选择） */
  myProjects: () => api.get<MyProjectsResult>('/me/projects'),
  listUsers: (params: { page?: number; page_size?: number; keyword?: string }) =>
    api.get<PageData<ManagedUser>>('/admin/users', params),
  createUser: (payload: UserCreatePayload) => api.post<ManagedUser>('/admin/users', payload),
  /** 单个用户详情（编辑表单回填） */
  getUser: (userId: number) => api.get<ManagedUser>(`/admin/users/${userId}`),
  /** 更新用户资料与角色（只传需要改的字段；角色变更立即生效） */
  updateUser: (
    userId: number,
    payload: {
      // 身份字段：null 表示清空（后端只更新非 None 的字段）
      full_name?: string | null
      employee_no?: string | null
      org_name?: string | null
      department?: string | null
      position?: string | null
      cert_no?: string | null
      signature?: string | null
      email?: string | null
      phone?: string | null
      role?: string
      specialties?: string[]
      project_ids?: number[]
    },
  ) => api.patch<ManagedUser>(`/admin/users/${userId}`, payload),
  /**
   * 管理员重置某用户密码。
   * ⚠️ 本系统**无自助找回密码**，这是用户忘记密码后唯一的恢复途径。
   */
  resetPassword: (userId: number, payload: { new_password: string; must_change?: boolean }) =>
    api.post<{ user_id: number; username: string; must_change_password: boolean }>(
      `/admin/users/${userId}/password`,
      payload,
    ),
  /**
   * 删除用户。
   * 默认**软删除（停用）**：保留历史评估记录与审计链。
   * `hard=true` 仅在无关联业务数据时允许 —— 有数据会返回 409 并说明原因。
   */
  deleteUser: (userId: number, hard = false) =>
    api.delete<{ user_id: number; username: string; mode: string; message: string }>(
      `/admin/users/${userId}`,
      { hard },
    ),
  updateProjects: (userId: number, projectIds: number[]) =>
    api.post<ManagedUser>(`/admin/users/${userId}/projects`, { project_ids: projectIds }),
  updateStatus: (userId: number, isActive: boolean) =>
    api.post<ManagedUser>(`/admin/users/${userId}/status`, { is_active: isActive }),
  myScope: () => api.get<MyScope>('/me/scope'),
}
