# PanKeeper 前端（Vue3）

网盘转存管理工具的 Web 前台。技术栈：Vite 5 · Vue 3.5 · TypeScript · Ant Design Vue 4 · Pinia · Vue Router（hash 模式）。

后端仓库见 `../pankeeper-backend`，整体部署与项目介绍见仓库根目录 [README](../README.md)。

## 本地开发

```bash
npm install
npm run dev        # http://localhost:5173，/api 由 vite 代理到 http://127.0.0.1:8000
npm run typecheck  # vue-tsc
npm run build
```

默认走真实后端（`VITE_USE_MOCK=false`）；纯离线联调可改为 `true`，全部数据来自 `src/api/mock/`。

## 目录速览

```
src/
├── api/           http.ts(axios 实例) + mock/(离线数据源) + modules/(领域 API)
├── components/    跨页面共享组件（PkTree / LogBox / PkPager 等）
├── composables/   组合式工具（移动端判定、侧滑返回护栏）
├── layouts/       BasicLayout（侧栏 + 顶栏 + 队列浮标）
├── queue/         转存队列前端引擎（SSE 状态同步 + 兜底轮询）
├── router/        路由与登录守卫
├── store/         Pinia（登录态 / 夜间模式）
├── styles/        pk.css 全局视觉令牌（浅 / 暗两套）+ 共享组件类
├── types/         领域类型（跨页面共用）
└── views/         页面视图（search / auto / records / logs / settings / accounts / cachecfg / queuecfg）
```

接口契约见 [docs/api-contract.md](docs/api-contract.md)。
