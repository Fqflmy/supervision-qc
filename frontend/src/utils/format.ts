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

/* ------------------------------------------------------------------ *
 * 复核决定与复核状态
 *
 * ⚠️ 术语对象（本项目最易混淆处，改动前请先读）
 * --------------------------------------------
 * 系统里有两个**不同对象**的判定，早期版本都用「合格/不合格」显示，
 * 导致用户无法分辨「是 AI 报告的结果合格，还是评估项目本身合格」：
 *
 *   工程质量  →  VERDICT_LABELS（符合 / 不符合），由 **AI** 判定
 *   AI 报告   →  REVIEW_DECISION_LABELS（接受报告 / 退回报告），由 **人工** 决定
 *
 * 因此复核决定一律用**动作词**：动作天然绑定对象，
 * 「接受报告」不可能被误读成「工程合格」，而「复核合格」一定会。
 * 展示时请同时给出 `*_OBJECT_NOTE`，避免脱离语境。
 * ------------------------------------------------------------------ */

/** 复核决定（人工对 AI 报告的取舍） */
export const REVIEW_DECISION_LABELS: Record<string, string> = {
  accept: '接受报告',
  return: '退回报告',
}

export const REVIEW_DECISION_TONES: Record<string, 'ok' | 'danger'> = {
  accept: 'ok',
  return: 'danger',
}

/** 由历史字段 human_verdict 派生动作词（后端未返回标签时的回退） */
export function reviewDecisionLabel(humanVerdict?: string | null): string {
  if (humanVerdict === 'qualified') return REVIEW_DECISION_LABELS.accept
  if (humanVerdict === 'unqualified') return REVIEW_DECISION_LABELS.return
  return '尚未复核'
}

export function reviewDecisionTone(humanVerdict?: string | null): 'ok' | 'danger' | 'muted' {
  if (humanVerdict === 'qualified') return 'ok'
  if (humanVerdict === 'unqualified') return 'danger'
  return 'muted'
}

/** 复核状态标签（用「已退回」而非「已复核不合格」—— 后者会与工程质量混淆） */
export const REVIEW_STATUS_TEXT: Record<string, string> = {
  pending: '待复核',
  signed: '已签发',
  rejected: '已退回',
}

/** 复核状态的后果说明：只看标签无法判断这份报告能否作为依据 */
export const REVIEW_STATUS_NOTES: Record<string, string> = {
  pending: '尚无人工复核决定，不得作为正式依据',
  signed: '已经人工复核签发，可作为正式依据',
  rejected: '报告未通过复核、未予签发，不得作为正式依据',
}

/** 两个判定的对象说明（必须与值同时展示，否则又会被误读） */
export const VERDICT_OBJECT_NOTE = 'AI 对工程质量的判定'
export const REVIEW_DECISION_OBJECT_NOTE = '人工对 AI 报告的取舍（不是判定工程质量）'
export const DUAL_VERDICT_NOTE = '两者判定对象不同，结论可并存、不矛盾'

export function reviewStatusText(status?: string | null): string {
  return status ? REVIEW_STATUS_TEXT[status] ?? status : '待复核'
}

export function reviewStatusNote(status?: string | null): string {
  return status ? REVIEW_STATUS_NOTES[status] ?? '' : REVIEW_STATUS_NOTES.pending
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
