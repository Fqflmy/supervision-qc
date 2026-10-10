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
 * 为什么不能用 ``request()``：该包装器假定响应体是
 * ``{ code, message, data }`` 信封并会去读 ``body.code`` —— 对二进制
 * 响应会得到一堆乱码且 ``code`` 为 undefined，判定失败。
 * 因此下载必须走**原始 axios 实例**（仍复用其 token 注入与 401 拦截）。
 *
 * 返回 blob 与后端在 ``Content-Disposition`` 中声明的文件名。
 * 解析文件名时优先取 RFC 5987 的 ``filename*``（UTF-8 编码），
 * 因为中文文件名在 ``filename=`` 里会被浏览器截断或乱码。
 */
export async function download(
  url: string,
  params?: Record<string, unknown>,
): Promise<{ blob: Blob; filename: string }> {
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

  return { blob: response.data, filename: filename || 'download' }
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
