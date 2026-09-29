import { defineStore } from 'pinia'
import { post, USE_MOCK } from '@/api/http'

const KEY = 'pk-auth'

function load(): string {
  try {
    return localStorage.getItem(KEY) || ''
  } catch {
    return ''
  }
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: load(),
    username: 'admin',
  }),
  getters: {
    logged: (s) => !!s.token,
  },
  actions: {
    /**
     * 登录：mock 模式任意输入放行；真实模式调 POST /api/auth/login 换 JWT，
     * 校验失败抛错（Login.vue 捕获后 toast），401 由 http 拦截器统一处理。
     */
    async login(username: string, password: string) {
      if (USE_MOCK) {
        this.username = username || 'admin'
        this.token = 'mock-token-' + Date.now()
        try {
          localStorage.setItem(KEY, this.token)
        } catch {
          /* ignore */
        }
        return
      }
      const res = await post<{ token: string; username: string }>('/auth/login', { username, password })
      this.username = res.username || username || 'admin'
      this.token = res.token
      try {
        localStorage.setItem(KEY, this.token)
      } catch {
        /* ignore */
      }
    },
    logout() {
      this.token = ''
      try {
        localStorage.removeItem(KEY)
      } catch {
        /* ignore */
      }
    },
  },
})
