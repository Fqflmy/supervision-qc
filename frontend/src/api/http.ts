/**
 * 统一 HTTP 客户端（前后端分离的唯一边界）。
 *
 * 约定：
 * - 所有接口返回 { code, message, data, trace_id }，code === 0 视为成功；
 * - 401/40102 自动清理凭证并跳登录；
 * - 业务错误抛出 ApiError，由调用方决定提示方式。
 */
import axios, { AxiosError, type AxiosInstance, type AxiosRequestConfig } from 'axios'

export interface Envelope<T> {
  code: number
  message: string
  data: T
  trace_id: string
}

export interface PageMeta {
  page: number
  page_size: number
  total: number
  pages: number
}

export interface PageData<T> {
  items: T[]
  meta: PageMeta
}

export class ApiError extends Error {
  code: number
  traceId?: string
  details?: unknown

  constructor(code: number, message: string, traceId?: string, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.traceId = traceId
    this.details = details
  }
}

const TOKEN_KEY = 'supervision.access_token'
const REFRESH_KEY = 'supervision.refresh_token'

export const tokenStore = {
  get access(): string | null {
    return localStorage.getItem(TOKEN_KEY)
  },
  get refresh(): string | null {
    return localStorage.getItem(REFRESH_KEY)
  },
  set(access: string, refresh?: string) {
    localStorage.setItem(TOKEN_KEY, access)
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh)
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(REFRESH_KEY)
  },
}

const http: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 300000,
  headers: { 'Content-Type': 'application/json' },
})

http.interceptors.request.use((config) => {
  const token = tokenStore.access
  if (token) {
    config.headers = config.headers ?? {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

let redirecting = false

http.interceptors.response.use(
  (response) => response,
  (error: AxiosError<Envelope<unknown>>) => {
    const status = error.response?.status
    const body = error.response?.data

    if (status === 401 || (body && (body.code === 40101 || body.code === 40102))) {
      tokenStore.clear()
      if (!redirecting && !location.hash.includes('/login')) {
        redirecting = true
        const back = encodeURIComponent(location.hash.replace(/^#/, '') || '/')
        location.hash = `#/login?redirect=${back}`
        setTimeout(() => (redirecting = false), 800)
      }
      return Promise.reject(new ApiError(40101, body?.message || '登录已过期，请重新登录'))
    }

    if (body && typeof body.code === 'number') {
      return Promise.reject(new ApiError(body.code, body.message, body.trace_id, body.data))
    }
    return Promise.reject(
      new ApiError(-1, error.message || '网络异常，请检查后端服务是否已启动', undefined, status),
    )
  },
)

/** 解包统一响应体，失败时抛 ApiError。 */
export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  const response = await http.request<Envelope<T>>(config)
  const body = response.data
  if (body.code !== 0) {
    throw new ApiError(body.code, body.message, body.trace_id, body.data)
  }
  return body.data
}

/**
 * 下载二进制文件（PDF / Excel 等）。
 *
 * ⚠️ 必须理解「**204 / 0 字节并不等于失败**」
 * ------------------------------------------
 * 这是本项目最容易被误判为 bug 的地方，改动前务必读完：
 *
 * 浏览器安装下载管理器扩展（IDM / 迅雷 / FDM 等）时，扩展在**网络层**拦截
 * 带 ``Content-Disposition: attachment`` 的响应并**自己完成下载**。此时页面里的
 * ``fetch`` / ``XHR`` 会拿到一个**被取消的空响应**：
 *
 *   - ``status === 204``
 *   - ``blob.size === 0``
 *   - 读不到 ``Content-Disposition``（因此文件名解析不出来）
 *   - 浏览器网络事件里**看不到这个请求**（扩展在网络层就截走了）
 *
 * 但**文件其实已经下好了**（实测：IDM 下载完成对话框显示 19.54 KB、
 * 中文文件名正确）。若把 204 当失败处理，用户会看到「下载失败」的提示；
 * 若继续 ``saveBlob(空 blob)``，则会在磁盘上留下一个 **0 字节的空文件**，
 * 用户打开发现损坏 —— 这才是真正的问题。
 *
 * 因此返回值里带上 ``handled`` 标记，由调用方决定要不要再保存：
 * - ``handled === true``：下载已被扩展/浏览器接管并完成，**不要再保存**；
 * - ``handled === false``：拿到真实字节，由调用方保存。
 *
 * 另一个已知限制：``<a href>`` 直链导航无法携带 ``Authorization`` 头，
 * 因此本站用 Token 鉴权时不能简单用直链；直链方案需要一次性下载票据
 * （见 ``/eval/tasks/{id}/report/download-ticket``）。
 */
export async function download(
  url: string,
  params?: Record<string, unknown>,
): Promise<{ blob: Blob; filename: string; handled: boolean }> {
  const response = await http.request<Blob>({
    method: 'GET',
    url,
    params,
    responseType: 'blob',
  })

  const disposition =
    (response.headers['content-disposition'] as string | undefined) ??
    (response.headers['Content-Disposition'] as string | undefined) ??
    ''

  let filename = ''
  // 优先 filename*=UTF-8''xxx（RFC 5987）
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  if (star?.[1]) {
    try {
      filename = decodeURIComponent(star[1])
    } catch {
      filename = star[1]
    }
  } else {
    const plain = /filename="?([^";]+)"?/i.exec(disposition)
    if (plain?.[1]) filename = plain[1]
  }

  if (!filename.endsWith('.pdf') && url.includes('/report/pdf')) {
    // 被扩展接管时读不到 Content-Disposition，这里给一个可用的回退名，
    // 避免调用方拿到 'download' 这种无意义的名字。
    if (!filename || filename === 'download') filename = '质量评估报告.pdf'
  }

  const size = response.data?.size ?? 0
  const handled = response.status === 204 || size === 0

  return { blob: response.data, filename: filename || 'download', handled }
}

/** 触发浏览器保存一个 Blob（下载的最后一步，各页面共用）。 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  // 立即 revoke 在部分浏览器会让下载中断，延后释放
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

export const api = {
  get: <T>(url: string, params?: Record<string, unknown>) => request<T>({ method: 'GET', url, params }),
  post: <T>(url: string, data?: unknown, params?: Record<string, unknown>) =>
    request<T>({ method: 'POST', url, data, params }),
  patch: <T>(url: string, data?: unknown) => request<T>({ method: 'PATCH', url, data }),
  // params 用于少数带查询参数的 DELETE（如删除用户时的 hard=true 物理删除开关）
  delete: <T>(url: string, params?: Record<string, unknown>) =>
    request<T>({ method: 'DELETE', url, params }),
  upload: <T>(url: string, form: FormData) =>
    request<T>({ method: 'POST', url, data: form, headers: { 'Content-Type': 'multipart/form-data' } }),
  /**
   * 下载二进制文件（PDF 等）。
   * 必须走 download 而不是 get —— 二进制响应不是统一信封，request 会解析失败。
   */
  download: (url: string, params?: Record<string, unknown>) => download(url, params),
}

export default http
