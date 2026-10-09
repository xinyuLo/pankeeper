import { defineStore } from 'pinia'
import { del, get, post, put, USE_MOCK } from '@/api/http'

const KEY = 'pk-auth'

function load(): string {
  try {
    return localStorage.getItem(KEY) || ''
  } catch {
    return ''
  }
}

/**
 * 从 JWT 里取用户名（后端 make_token 把用户名写进了 sub）。
 *
 * 这里曾经是 `username: 'admin'` 硬编码且不持久化 —— 登录那一下是对的，
 * 但一刷新页面就「变回 admin」，跟真实账号完全脱节。改为以 token 为唯一真源。
 */
function usernameFromToken(token: string): string {
  if (!token) return ''
  try {
    const part = (token.split('.')[1] || '').replace(/-/g, '+').replace(/_/g, '/')
    const bytes = Uint8Array.from(atob(part), (c) => c.charCodeAt(0))
    const payload = JSON.parse(new TextDecoder().decode(bytes))
    return typeof payload.sub === 'string' ? payload.sub : ''
  } catch {
    return ''
  }
}

export const useAuthStore = defineStore('auth', {
  state: () => {
    const token = load()
    return {
      token,
      username: usernameFromToken(token),
      /** 头像（data URL，后端持久化）；空 = 用用户名首字兜底 */
      avatar: '',
    }
  },
  getters: {
    logged: (s) => !!s.token,
    /** 没有头像图时用来兜底的字符 */
    initial: (s) => (s.username || '?').slice(0, 1).toUpperCase(),
  },
  actions: {
    /**
     * 登录：mock 模式任意输入放行；真实模式调 POST /api/auth/login 换 JWT，
     * 校验失败抛错（Login.vue 捕获后 toast），401 由 http 拦截器统一处理。
     */
    async login(username: string, password: string) {
      if (USE_MOCK) {
        this.token = 'mock-token-' + Date.now()
        this.username = username || 'admin'
        try {
          localStorage.setItem(KEY, this.token)
        } catch {
          /* ignore */
        }
        return
      }
      const res = await post<{ token: string; username: string }>('/auth/login', { username, password })
      this.token = res.token
      this.username = res.username || usernameFromToken(res.token)
      try {
        localStorage.setItem(KEY, this.token)
      } catch {
        /* ignore */
      }
      await this.loadAvatar()
    },
    logout() {
      this.token = ''
      this.username = ''
      this.avatar = ''
      try {
        localStorage.removeItem(KEY)
      } catch {
        /* ignore */
      }
    },
    /** 拉头像；失败静默（头像拉不到不该影响登录态） */
    async loadAvatar() {
      if (USE_MOCK || !this.token) return
      try {
        const res = await get<{ data: string }>('/settings/avatar')
        this.avatar = res?.data || ''
      } catch {
        this.avatar = ''
      }
    },
    /** 保存头像（传已压缩的 data URL） */
    async saveAvatar(dataUrl: string) {
      if (USE_MOCK) {
        this.avatar = dataUrl
        return
      }
      await put('/settings/avatar', { data: dataUrl })
      this.avatar = dataUrl
    },
    /** 移除头像，回落到用户名首字 */
    async removeAvatar() {
      if (USE_MOCK) {
        this.avatar = ''
        return
      }
      await del('/settings/avatar')
      this.avatar = ''
    },
  },
})
