import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import http from 'node:http'
import { fileURLToPath, URL } from 'node:url'

/* ⚠️ 别删（2026-10-04 实测复现 + 修复验证）：
 * 后端 uvicorn 默认 timeout-keep-alive=5s，空闲超过 5 秒就把连接关掉；而 Node 19+ 的
 * http.globalAgent 默认 keepAlive=true，vite 代理会把这条「已被对端关闭」的空闲连接
 * 当活的复用 → 下一次请求 read ECONNRESET，代理层回 500，表现为「隔一会儿就连不上、
 * 一连炸一片接口」，而后端日志干干净净只有 200（请求根本没发出去）。
 * 复现：走代理请求 → 闲置 7s → 再请求 = 000 重置。
 * 修法：给代理一个不复用连接的 agent。 */
const noKeepAliveAgent = new http.Agent({ keepAlive: false })

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
        agent: noKeepAliveAgent, // 见文件头注释：禁用连接复用，根治 ECONNRESET 假 500
      },
    },
  },
})
