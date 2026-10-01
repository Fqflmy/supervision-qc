/** 展示层共用工具：状态映射、标签、格式化。 */
import dayjs from 'dayjs'

export const STATE_LABELS: Record<string, string> = {
  PENDING: '待执行',
  PLANNING: '任务拆解',
  RETRIEVING: '规范检索',
  MATCHING: '条款匹配',
  ANALYZING: '结果分析',
  REPORTING: '报告生成',
  JUDGING: '质量评审',
  NEED_HUMAN: '待人工复核',
  DEGRADED: '降级完成',
  COMPLETED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
}

export const STATE_TONES: Record<string, 'info' | 'warn' | 'danger' | 'ok' | 'muted'> = {
  PENDING: 'muted',
  PLANNING: 'info',
  RETRIEVING: 'info',
  MATCHING: 'info',
  ANALYZING: 'info',
  REPORTING: 'info',
  JUDGING: 'info',
  NEED_HUMAN: 'warn',
  DEGRADED: 'warn',
  COMPLETED: 'ok',
  FAILED: 'danger',
  CANCELLED: 'muted',
}

export const VERDICT_LABELS: Record<string, string> = {
  compliant: '符合',
  partial: '部分符合',
  non_compliant: '不符合',
  not_applicable: '不适用',
  insufficient_evidence: '证据不足',
}

export const VERDICT_TONES: Record<string, 'info' | 'warn' | 'danger' | 'ok' | 'muted'> = {
  compliant: 'ok',
  partial: 'warn',
  non_compliant: 'danger',
  not_applicable: 'muted',
  insufficient_evidence: 'warn',
}

export const DOC_STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  parsing: '解析中',
  pending_review: '待审核',
  published: '已发布',
  abolished: '已废止',
  failed: '解析失败',
}

export const DOC_STATUS_TONES: Record<string, 'info' | 'warn' | 'danger' | 'ok' | 'muted'> = {
  draft: 'muted',
  parsing: 'info',
  pending_review: 'warn',
  published: 'ok',
  abolished: 'danger',
  failed: 'danger',
}

export const PARSE_STATUS_LABELS: Record<string, string> = {
  pending: '待解析',
  parsing: '解析中',
  parsed: '已解析',
  failed: '解析失败',
}

export const GRADE_LABELS: Record<string, string> = {
  excellent: '优秀',
  good: '良好',
  qualified: '合格',
  unqualified: '不合格',
}

export const STEP_LABELS: Record<string, string> = {
  planning: '任务拆解',
  retrieval: '规范检索',
  clause_matching: '条款匹配',
  analysis: '结果分析',
  report_generation: '报告生成',
}

export const DIMENSION_LABELS: Record<string, string> = {
  clause_citation_accuracy: '条款引用准确性',
  conclusion_reasonableness: '结论合理性',
  evidence_sufficiency: '证据充分性',
  format_compliance: '格式规范性',
  remediation_actionability: '整改建议可执行性',
}

export const AUDIT_ACTION_LABELS: Record<string, string> = {
  login: '登录',
  logout: '登出',
  doc_upload: '上传文档',
  doc_parse: '解析文档',
  doc_publish: '发布文档',
  kg_extract: '图谱抽取',
  retrieval: '检索',
  chat: '问答',
  eval_create: '创建评估',
  eval_resume: '续跑评估',
  report_export: '导出报告',
  judge_score: '质量评审',
  config_change: '配置变更',
}

export const RISK_LABELS: Record<string, string> = { high: '高', medium: '中', low: '低' }
export const RISK_TONES: Record<string, 'danger' | 'warn' | 'muted'> = {
  high: 'danger',
  medium: 'warn',
  low: 'muted',
}

export function stateLabel(state?: string | null): string {
  if (!state) return '-'
  return STATE_LABELS[state] ?? state
}

export function stateTone(state?: string | null) {
  return state ? STATE_TONES[state] ?? 'muted' : 'muted'
}

export function verdictLabel(verdict?: string | null): string {
  if (!verdict) return '-'
  return VERDICT_LABELS[verdict] ?? verdict
}

export function verdictTone(verdict?: string | null) {
  return verdict ? VERDICT_TONES[verdict] ?? 'muted' : 'muted'
}

export function fmtTime(value?: string | null, pattern = 'YYYY-MM-DD HH:mm:ss'): string {
  if (!value) return '-'
  const parsed = dayjs(value)
  return parsed.isValid() ? parsed.format(pattern) : String(value)
}

export function fmtSize(bytes?: number | null): string {
  if (!bytes && bytes !== 0) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

export function fmtScore(score?: number | null): string {
  return score === null || score === undefined ? '-' : Number(score).toFixed(2)
}

/** debug.recall / debug.debug 等嵌套结构的安全取值。 */
export function pick<T = unknown>(source: unknown, key: string, fallback?: T): T {
  if (source && typeof source === 'object' && key in (source as Record<string, unknown>)) {
    return (source as Record<string, unknown>)[key] as T
  }
  return fallback as T
}
