import { defineStore } from 'pinia'

const KEY = 'pk-theme'

export const useThemeStore = defineStore('theme', {
  state: () => ({
    isDark: ((): boolean => {
      try {
        return localStorage.getItem(KEY) === 'dark'
      } catch {
        return false
      }
    })(),
  }),
  actions: {
    apply() {
      document.documentElement.dataset.theme = this.isDark ? 'dark' : ''
      if (!this.isDark) delete document.documentElement.dataset.theme
    },
    toggle() {
      this.isDark = !this.isDark
      try {
        localStorage.setItem(KEY, this.isDark ? 'dark' : 'light')
      } catch {
        /* ignore */
      }
      this.apply()
    },
  },
})
