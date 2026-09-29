import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// 后端就绪后：把 target 改成 Python 服务地址（如 http://127.0.0.1:8000），
// 前端所有请求走 /api 前缀由 vite 代理转发，页面代码不用动。
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    host: true, // 监听 0.0.0.0：手机连同一 WiFi 用 http://<电脑IP>:5173 真机联调
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
