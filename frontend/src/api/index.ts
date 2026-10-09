/** 与后端 OpenAPI 契约对应的 TypeScript 类型定义。 */
import { api, type PageData } from './http'

export { api }
export type { PageData }

// --------------------------------------------------------------------------- //
// 认证
// --------------------------------------------------------------------------- //
export interface User {
  id: number
  username: string
  full_name?: string | null
  email?: string | null
  role: 'admin' | 'kb_manager' | 'engineer' | 'expert' | 'viewer' | string
  specialties: string[]
  project_ids: number[]
  is_active: boolean
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
  list: (params: { page?: number; page_size?: number; state?: string; mine?: boolean }) =>
    api.get<PageData<EvalTask>>('/eval/tasks', params),
  detail: (id: string) => api.get<EvalTaskDetail>(`/eval/tasks/${id}`),
  run: (id: string, resume = false) =>
    api.post<EvalRunResult>(`/eval/tasks/${id}/run`, undefined, { resume }),
  submit: (id: string) => api.post<{ task_id: string; current_state: string }>(`/eval/tasks/${id}/submit`),
  report: (id: string) => api.get<EvalReport>(`/eval/tasks/${id}/report`),
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
}

export interface AuditLog {
  id: number
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
  email?: string | null
  phone?: string | null
  role: AssignableRole | string
  /** 授权访问的项目 ID。非管理员必须至少有一个，否则看不到任何项目数据。 */
  project_ids: number[]
  specialties: string[]
  is_active: boolean
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

export const usersApi = {
  listProjects: () => api.get<ProjectItem[]>('/admin/projects'),
  listUsers: (params: { page?: number; page_size?: number; keyword?: string }) =>
    api.get<PageData<ManagedUser>>('/admin/users', params),
  createUser: (payload: UserCreatePayload) => api.post<ManagedUser>('/admin/users', payload),
  updateProjects: (userId: number, projectIds: number[]) =>
    api.post<ManagedUser>(`/admin/users/${userId}/projects`, { project_ids: projectIds }),
  updateStatus: (userId: number, isActive: boolean) =>
    api.post<ManagedUser>(`/admin/users/${userId}/status`, { is_active: isActive }),
  myScope: () => api.get<MyScope>('/me/scope'),
}
