/**
 * 站点品牌与合规信息配置。
 *
 * 为什么用环境变量而不是硬编码
 * --------------------------
 * 企业化界面需要展示**机构名称、版权、备案号**等信息，但这些是**部署方独有**的，
 * 不应写死在代码里：
 *
 * - 硬编码虚假备案号属于伪造合规信息，且不同部署方备案号不同；
 * - 硬编码机构名会导致同一份代码无法服务多个客户。
 *
 * 因此通过 Vite 环境变量注入（构建时替换），未配置时**不渲染**而不是显示占位符 ——
 * 宁可少一行，也不要出现「示例公司」「京ICP备00000000号」这种一眼假的文本。
 *
 * 配置方式（`frontend/.env.local` 或构建环境变量）：
 * ```
 * VITE_ORG_NAME=某某建设集团有限公司
 * VITE_SYSTEM_NAME=工程监理质量智能评估系统
 * VITE_ICP_NUMBER=京ICP备2024000000号
 * VITE_COPYRIGHT=© 2026 某某建设集团有限公司
 * ```
 */
const env = import.meta.env

/** 部署机构名称（为空则不展示） */
export const ORG_NAME: string = (env.VITE_ORG_NAME ?? '').trim()

/** 系统名称（有默认值：未配置时用产品名） */
export const SYSTEM_NAME: string = (env.VITE_SYSTEM_NAME ?? '').trim() || '工程监理质量智能评估系统'

/** 系统英文名/副标题（企业系统常用，用于页脚与品牌区） */
export const SYSTEM_NAME_EN = 'SUPERVISION QUALITY ASSESSMENT PLATFORM'

/** ICP 备案号（生产环境必须配置；为空则不展示） */
export const ICP_NUMBER: string = (env.VITE_ICP_NUMBER ?? '').trim()

/** 版权声明（为空时按机构名与当前年份生成，仍为空则不展示） */
export const COPYRIGHT: string = (() => {
  const configured = (env.VITE_COPYRIGHT ?? '').trim()
  if (configured) return configured
  if (!ORG_NAME) return ''
  return `© ${new Date().getFullYear()} ${ORG_NAME}`
})()

/** 是否展示备案与版权行（任一有值即可展示页脚） */
export const HAS_LEGAL_FOOTER = Boolean(ICP_NUMBER || COPYRIGHT)
