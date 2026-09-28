import { defineStore } from 'pinia'

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
    /** mock 登录：后端就绪后换成 POST /api/auth/login 拿 JWT */
    login(username: string, _password: string) {
      this.username = username || 'admin'
      this.token = 'mock-token-' + Date.now()
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
