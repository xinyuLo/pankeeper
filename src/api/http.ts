import axios from 'axios'

/**
 * 统一请求实例。VITE_USE_MOCK=true 时各 api 模块走 mock 实现（见 api/mock/）；
 * 切真实后端时把模块里的 mockDelay(...) 换成本文件导出的 http 调用即可，页面不用动。
 */
export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api',
  timeout: 15000,
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
      location.hash = '#/login'
    }
    return Promise.reject(err)
  },
)

/** mock 专用：模拟网络延迟，保持和真实接口一致的异步签名 */
export function mockDelay<T>(data: T, ms = 120): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(data), ms))
}
