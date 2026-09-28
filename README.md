# PanKeeper-vue3

网盘转存管理工具的前台（Vue3 版），基于 `PanKeeper-Prototype` 原型开发；交互契约见原型仓库 `docs/`（`01-overview.md` / `02-data-model.md`）。

## 技术栈

Vite 5 · Vue 3.5 · TypeScript · Ant Design Vue 4 · Pinia · Vue Router（hash 模式）

## 快速开始

```bash
npm install
npm run dev        # http://localhost:5173，登录任意输入即可
npm run typecheck  # vue-tsc
npm run build
```

## 数据层：mock 先行 + API 层预留

当前全部数据来自 `src/api/mock/`（内存/localStorage），后端（Python）就绪后按模块切换：

1. `.env.development` / `.env.production` 里 `VITE_USE_MOCK=false`
2. `vite.config.ts` 的 `/api` proxy target 改成后端地址
3. 按 `src/api/modules/*.ts` 里各函数的 `TODO 后端: GET/POST /api/...` 注释逐个实现真实接口（字段契约 = `src/types/model.ts`）

页面代码不用动。**后端开发按 [docs/api-contract.md](docs/api-contract.md)（接口需求文档）实现**：通用约定（认证/响应格式/错误码/时间格式）+ 全部端点的请求响应形状与行为要求。

## 目录速览

```
src/api/         http.ts(axios) + mock/(数据源) + modules/(领域API，含后端端点注释)
src/queue/       转存队列引擎（原型 queue-core.js 的 TS 移植，localStorage pkq_v2/pkq_cfg）
src/views/       页面视图（login/dashboard/search/records/defaultdir/auto/accounts/cachecfg/queuecfg/settings）
src/styles/pk.css  全局视觉令牌（浅/暗两套）+ 共享组件类
开发约定见 AGENTS.md
```
