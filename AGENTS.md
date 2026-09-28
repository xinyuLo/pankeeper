# AGENTS.md — PanKeeper-vue3 开发约定

网盘转存管理工具的 Vue3 前台。技术栈：**Vite + Vue3 + TypeScript + Ant Design Vue 4 + Pinia**。
数据层 **mock 先行 + API 层预留**：全部数据来自 `src/api/mock/`（内存/localStorage），后端（Python）就绪后按模块切换，页面代码不动。
原型参考：`../PanKeeper-Prototype/`（交互契约以 `docs/01-overview.md` 为准，字段形状以 `docs/02-data-model.md` 为准）。

## 目录与职责

```
src/
  api/
    http.ts            axios 实例 + mockDelay 助手
    mock/              mock 数据源（meta=网盘元信息/dd=转存配置/tree=目录树/各页面自己的）
    modules/           领域 API 模块（每个函数 mock 分支 + 注释标注未来 REST 端点）
  components/          跨页面共享组件（PkTree/LogBox/PkPager），页面私有组件放自己视图文件夹
  layouts/BasicLayout.vue   侧栏+顶栏+内容区+队列浮标（路由/标题/菜单都在 router 里）
  queue/               转存队列引擎（engine.ts=状态机，QueueBoard=记录页看板，QueueBadge=浮标）
  router/index.ts      全部路由 + 登录守卫（hash 模式）
  store/               pinia（auth 登录态 / theme 夜间模式）
  styles/pk.css        全局视觉令牌（浅/暗）+ 共享组件类（.card/.tag/.tabs/.stat/.logbox…）
  types/model.ts       领域类型（对齐 docs/02 接口契约，跨页面共用才放这里）
  views/               页面视图（每个页面一个文件夹）
```

## 铁律

1. **文件所有权**：多智能体并行开发时，只能改自己负责的视图文件夹 + 自己的 mock/api 文件。
   共享文件（router/store/styles/queue/components/api/http）**只读不改**——需要新共享能力就在自己视图文件夹内实现。
2. **视觉还原**：全局令牌已在 `styles/pk.css`（CSS 变量浅/暗两套）。页面样式用这些变量 +
   共享类（.card/.tabs/.filterbar/.stat/.tag/.logbox/.note-box/.rowbtns/.pager 等），页面私有样式用
   **scoped style + 自己的前缀**（dd-/qs-/pa-/mt-/cc-/cq-，防跨页污染）。暗色模式不能漏：新增颜色一律走 CSS 变量，
   硬编码浅色的必须补 `html[data-theme='dark']` 覆盖。
3. **组件优先用 antd**：Modal/Drawer/Select/Input/Switch/Table/message/Popconfirm 等用 ant-design-vue
   （已全局注册，无需 import 组件，直接 `<a-modal>` 等；图标从 @ant-design/icons-vue 引）。
   原型里自定义的强视觉元素（胶囊 tab、统计卡、日志盒、行内按钮、分页条）保留自定义类，别换成 antd 默认样式。
4. **数据一律走 api 模块**：页面不直接写死业务数据。mock 数据放 `api/mock/<域>.ts`（reactive 导出，内存可变），
   api 模块放 `api/modules/<域>.ts`，函数签名 `Promise<T>`，mock 用 `mockDelay()` 包一层：

   ```ts
   // src/api/modules/dd.ts —— 参考实现
   import { mockDelay } from '../http'
   import { ddStore } from '../mock/dd'
   export function getDdItems() {
     // TODO 后端: GET /api/dd/items
     return mockDelay(ddStore.items)
   }
   ```

5. **队列引擎不许绕过**：任何转存动作只调 `pkQueue.enqueue()`（src/queue/engine.ts），入队即走——
   toast 报位次、弹窗立即关闭，绝不弹进度条等人。队列状态渲染读 `queueView`（响应式镜像）。
6. **交互契约必须保留**（docs/01「关键交互契约」）：入队即走 / 队列看板 30 分钟 / 快速转存靠转存配置 /
   首页只看下一个任务 / 手动转存不接 Server 酱推送（只有自动转存有）。
7. 中文注释，注释写「为什么」而不是「做什么」。TypeScript strict 通过（`npm run typecheck`）。

## Mock 约定

- mock 数据 reactive 导出，页面内增删改直接变它（会话内有效，刷新重置——和原型一致）。
- **例外**：队列状态（`pkq_v2`）与队列配置（`pkq_cfg`）存 localStorage（跨页面/刷新持久，原型如此）。
- localStorage 键名沿用原型：`pkq_v2` / `pkq_cfg` / `pk-theme` / `pk-nav` / `pk-auth`。

## 验证

```
npm run typecheck   # vue-tsc，必须 0 错误
npm run dev         # http://localhost:5173 （登录任意输入即可）
npm run build       # 交付前跑
```
