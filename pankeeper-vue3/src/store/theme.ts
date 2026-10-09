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
      // 同步浏览器/系统 UI 颜色（Safari 地址栏、PWA 状态栏底色）跟手主题
      const meta = document.getElementById('meta-theme-color')
      if (meta) meta.setAttribute('content', this.isDark ? '#0f1115' : '#f5f7fa')
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
