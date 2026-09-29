import axios from 'axios'

/**
 * 数据层模式开关：
 * - VITE_USE_MOCK=true（默认）：各 api 模块走 src/api/mock/ 的本地实现（离线可开发）；
 * - VITE_USE_MOCK=false：走本文件封装的真实后端请求（/api 由 vite 代理转发）。
 * 切换开关在 .env.development / .env.production，页面代码零改动。
 */
export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'

/**
 * 统一请求实例。响应拦截器直接返回 body（契约：后端不包 {code,data} 壳）；
 * 401 清登录态并回登录页。
 */
export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api',
  timeout: 35000, // pansou 无缓存首搜 4-10s，留足余量
})

http.interceptors.request.use((cfg) => {
  const token = localStorage.getItem('pk-auth')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

http.interceptors.response.use(
  (res) => res.data,
  (err) => {
    if (err?.response?.status === 401) {
      localStorage.removeItem('pk-auth')
      if (location.pathname !== '/login') window.location.assign('/login')
    }
    return Promise.reject(err)
  },
)

/** mock 专用：模拟网络延迟，保持和真实接口一致的异步签名 */
export function mockDelay<T>(data: T, ms = 120): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(data), ms))
}

/* ---- 类型友好的真实请求助手（响应拦截器已剥壳，这里只是把 TS 类型对齐） ---- */

export async function get<T>(url: string, config?: Record<string, unknown>): Promise<T> {
  return (await http.get(url, config)) as unknown as T
}

export async function post<T>(url: string, body?: unknown, config?: Record<string, unknown>): Promise<T> {
  return (await http.post(url, body, config)) as unknown as T
}

export async function put<T>(url: string, body?: unknown, config?: Record<string, unknown>): Promise<T> {
  return (await http.put(url, body, config)) as unknown as T
}

export async function del<T>(url: string, config?: Record<string, unknown>): Promise<T> {
  return (await http.delete(url, config)) as unknown as T
}
