# PanKeeper 工作交接（2026-10-01）

> 交接范围：本地开发环境（Windows，`D:\zcodeWork\pankeeper\pankeeper`）当前全部改动。
> 上一笔已推送提交：`3fd4506`（全树预热参数修复）。本文档之后的提交包含本文件所述全部内容。

## 1. 本地环境（重要）

| 项 | 值 |
| --- | --- |
| 仓库根 | `D:\zcodeWork\pankeeper\pankeeper`（git 已初始化，remote = Gitea `xinyu/pankeeper`） |
| 后端 | `pankeeper-backend/`，venv 在 `.venv/`，`run.py --port 8000`，数据在 `data/pankeeper.db`（WAL） |
| 前端 | `pankeeper-vue3/`，`npm run dev` → :5173，`/api` 代理到 127.0.0.1:8000；`VITE_USE_MOCK=false` |
| Gitea | `http://192.168.2.77:8029`，账号 xinyu；NAS SSH 同机 10000 端口 |
| Node/Git | 本机原无 Node/Git，已装 Node 24（`C:\Program Files\nodejs`）和 Git 2.55（`C:\Program Files\Git\bin`）；Git Bash 下 `/PID` 会被转义，杀进程用 PowerShell `Stop-Process` |
| 数据来源 | 本地库是从 **NAS 容器（pankeeper，端口 8031）拷回的快照**（db + jwt.key/cred.key）。本地登录密码 = NAS 那套；**本地与 NAS 是两份独立数据，本地改动不会同步回 NAS** |

⚠️ 坑：PowerShell 批量改文件会把 UTF-8 中文文件写花（本次事故已从 Gitea 恢复），批量改文件一律用 Edit 工具或 Python。

## 2. 本次完成的功能（后端）

### 2.1 自动转存 cron 调度（M3 收尾）
- `app/services/pa_scheduler.py`：每个启用 cron 的 PaTask 一个 APScheduler job；增删改/启停自动重排。
- 到点只做「校验 + 输入侧去重（同分享链接在队即跳过）+ 入队（source=auto）」，转存全走队列引擎。
- 完成/失败由引擎回写 `last_status` + `RunHistory`；分享失效写 `ban_reason` 熔断，更新链接自动恢复。
- 新接口：`POST /api/pa/tasks/{id}/run`（立即运行）、`GET /api/pa/next-runs`。

### 2.2 百度 adapter 完整转存链路（踩坑最多，已实测通过）
坑与修法（详见 `app/adapters/baidu.py` 注释）：
1. **提取码**：pwd 在链接 `?pwd=` 里也必须先 `POST /share/verify` 拿 BDCLND，否则抓到验证页；
2. **BDCLND Cookie 重复**：百度对 BDCLND 下发两份（domain 差异），httpx 照收 → 请求头重复 → 百度仍按未验证处理。修法：拿到 `randsk` 后**清空 jar 重建**（基础 cookie + 单份 BDCLND）；
3. **两套短码**：页面地址 `/s/<slug>` 用**全长**（含前导 1，23 位）；verify 的 surl 参数用**剥前导 1 的 22 位码**。`parse_share_url` 返回全长，`_verify_surl()` 负责剥；
4. 错误分流：死链(-7/-8/-9/115/145)→ShareBanned 熔断；-6→CredentialExpired；-65 转存频率限制→等 10s 整组重试；fsidlist ≤500/组。

### 2.3 115 adapter（webapi 路线，不引 p115client）
- `app/adapters/pan115.py`：share/snap 翻页到 count（文件夹优先取 fid 整目录接收）→ 文件名去重（compare_path 可配）→ files/add 建目录 → share/receive 整组接收（「文件已接收，无需重复接收」判幂等成功）。
- 风控：微信小程序 UA；**验证码=已被风控，停下报警不硬闯**；≥1s 串行门；406 退避。
- ⚠️ 未拿真实 115 账号端到端验证过（本机没绑 115 账号）。

### 2.4 缓存持久化（+开关）
- 目录树缓存**写穿** SQLite 新表 `dir_tree_cache`（type+acc+cid 主键），重启**懒恢复**（只恢复未过期条目，零网盘请求）；清除/逐出同步删库。
- 开关在缓存配置页「目录树缓存」旁「持久化」小开关（`cache_cfg.persist`，默认开）。
- 缓存条目新增 `cachedAt`（写入时间），配合缓存配置页新增「缓存时间」「距下次缓存」两列（倒计时药丸，<1h 转橙）。

## 3. 本次完成的功能（前端）

### 3.1 目录树语义拆分（最重要的设计变更，别再改回去）
两个概念彻底分开：
1. **默认根目录**（网盘连接页配置，存 `settings.root_cfg`，按网盘类型）= **所有目录树弹窗的固定浏览起点**：
   - 搜索转存→转存弹窗的「我的网盘」树；
   - 转存配置→新增/编辑的目录树；
   - 自动转存→任务弹窗的转存/对比树。
   实现链：`GET/PUT /api/accounts/root-dirs` → 前端 `LazyDirTree` 的 `root-path` 属性（锁根）。
2. **转存配置 is_default** = 快速转存下拉的第一项/排序，与树根**无关**。

配套行为：
- 编辑条目时弹窗自动展开定位到该条目路径（`initial-path`）；
- **新增时不预选**任何目录（必须自己点）；
- 网盘连接页的默认根目录配置弹窗本身从真根浏览（配置入口不能被锁）；
- 锁定目录在网盘侧不存在→自动回退真根，不白屏。

### 3.2 LazyDirTree 组件（`src/components/LazyDirTree.vue`）
- 选中态收在根实例统一维护（递归子层沿 props 透传）——修「点选后整条链高亮」bug；子层选中先回写根再转发；
- 树容器限高 `min(55vh, 480px)` 内部滚动（防底部按钮被顶出屏幕），所有用它的弹窗一起生效；
- `reload()` 在锁根模式下整树重走。

### 3.3 其他 UI 修复/打磨
- 转存记录/任务弹窗 QMS 下拉：真数据 media_type 已是中文，不再显示 undefined；原生 select 换 a-select；
- 任务弹窗「解析」按钮接真接口 `POST /api/pa/parse-share`（空链接提示、失效报真实原因），原来假 toast「共 36 个文件」已删；
- 搜索结果表 `table-layout: fixed`：长名称省略号 + title 悬停全文，操作按钮不再被挤没；
- 缓存配置页倒计时药丸美化；PWA 图标改全出血渐变 + iOS 风圆角（重装 PWA 才会刷新桌面图标）；
- 网盘连接卡片：别名行删除，卡片头显示「网盘用户名/别名」，右侧「别名」小胶囊按钮开修改框；
- 搜索检索中动画：能量环 + 卫星点 + 放大镜呼吸（纯 CSS/SVG），替换骨架屏。

## 4. 已修复的后端 bug（顺手清单）
- 全树预热 `_load_dir_payload` 少传 `path`（添加网盘报「缓存失败」）；
- `/api/files/list` 路径解析模式（parent=0+path）缓存键没带路径 → 命中真根缓存返回错数据；
- 测试：33 个全过（`test_m3.py`、`test_dircache.py` 已扩充）。

## 5. 待办 / 下一步

1. **端到端验证**：百度真实转存（搜索→快速转存→队列→记录）只差临门一脚——解析已通，转存动作请实测一次；115 同理（需先绑账号）；
2. **NAS 部署更新**：NAS 容器还在跑旧代码，`docker compose up -d --build` 前先看本文档 §3.1 的语义变更（NAS 数据里 root_cfg 为空，部署后需在网盘连接页重新配置一次默认根目录）；
3. M4 剩余：Server 酱推送时机打磨、记录清理/重试细化、搜索历史页；
4. 排除清单（exclude_json）当前按文件名匹配，前端排除弹窗仍是 mock 数据（`fetchExclFiles`），接真数据时要转成 `/api/pa/tasks/{id}/share-files` 已有接口。

## 6. 快速自检命令

```bash
# 后端测试
cd pankeeper-backend && .venv/Scripts/python -m pytest tests/ -q
# 前端类型检查
cd pankeeper-vue3 && npm run typecheck
# 冒烟：解析分享（真实链接）
curl -X POST http://127.0.0.1:8000/api/pa/parse-share -H "Content-Type: application/json" \
  -d '{"type":"baidu","share_url":"<分享链接>?pwd=<提取码>"}'
```
