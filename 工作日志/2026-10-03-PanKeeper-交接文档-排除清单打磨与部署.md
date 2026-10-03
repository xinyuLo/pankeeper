# 2026-10-03 PanKeeper 交接文档（外网环境 · 排除清单弹窗打磨 · 部署）

> 本日志是给「新对话」的交接底账：环境信息、今天做了什么、当前状态、接下来可做的事。
> 上一份交接：`2026-09-30-PanKeeper-部署收尾与环境交接.md`（部署架构/更新一条龙在那份，本文不重复）。

## 环境速记（2026-10-03 实测有效）

| 项 | 值 |
| --- | --- |
| 当前网络 | **外网**（节点小宝组网），NAS=`100.66.1.1`；回家切局域网 `192.168.2.77` |
| Gitea | **外网 Gitea 上只剩合并仓库 `xinyu/pankeeper`**（独立仓库 pankeeper-backend/pankeeper-vue3 已删除，ls-remote 报 Cannot find repository）；本地 `pankeeper-merge` 的 remote 已切 `git@100.66.1.1:xinyu/pankeeper.git` |
| 本次推送 | `f018fc9` 排除清单弹窗改动（今天唯一代码提交） |
| 本地服务起法 | 后端：`pankeeper-merge/pankeeper-backend` 下 `.venv/Scripts/python.exe run.py --port 8000`（.venv 今天新建，依赖与旧仓库一致）；前端：`pankeeper-vue3` 下 `npm run dev`（5173）。日志 `pankeeper-merge/{backend,frontend}-dev.log`（已进 .gitignore） |
| 本地库账号 | admin / admin#123；百度 id=3「强盘」connected；夸克 id=2「七盘」connected |
| pansou/QMS 配置 | 本地 settings 里还指内网 `192.168.2.77:8028/8020`——**外网联调搜索/刮削前要先改**，不影响纯转存 |
| NAS SSH | paramiko（`192.168.2.77:10000` 或 `100.66.1.1:10000`，xinyu，密码认证） |
| **NAS 部署位置（重要，09-30 文档已过时）** | 代码在 `/vol2/1001/disk2/workspace/pankeeper-deploy/`（pankeeper-backend/pankeeper-vue3 子目录，remote 走 `localhost:8029`）；**权威 compose 是 fnOS 的 `/vol1/1001/compose/pankeeper/docker-compose.yml`**（build 上下文直接指 pankeeper-deploy，镜像名 `pankeeper-backend-pankeeper`） |
| **数据真身** | bind mount `/vol1/1001/tools/pankeeper/data -> /app/data`（SQLite+密钥）。09-30 文档说的 "volume pankeeper-data" 不对——那个 named volume 是空壳，别删容器后从 pankeeper-deploy 直接 compose up（会挂空卷） |
| **更新部署正确姿势** | pankeeper-deploy 里 `git pull` → 在 `/vol1/1001/compose/pankeeper` 下 `docker compose up -d`（重建容器挂新镜像，数据 bind 不动）。**不要**在 pankeeper-deploy/pankeeper-backend 下 compose up（项目名不同会撞容器名/挂空卷） |
| NAS PanKeeper 登录 | `xinyu / LxY252235!`（不是 admin） |
| 部署核验 | ✅ `f018fc9` 已上线 8031（容器重建、站点 200、`[pa-sched] 自动任务排期：1 个生效`） |

## 今天做了什么

1. **环境迁移**：外网 Gitea 拉代码、本地前后台起服务（见上表）。
2. **百度自动转存任务导入**：NAS `baidu-autosave` 容器（`/vol1/1001/tools/baiduAutoSave/config.json`）
   的「兰香如故」任务（cron `0 20 * * *`）已写入本地 pa_tasks id=1：分享 15FgWoA5NT…码 6666，
   目标 `/A罗/0.影视/待整理-电视剧/兰香如故(2026)`，compare_path 指灿如繁星 Season 1，
   排除 30~34.4k.mp4 共 5 项，正则 `^[4-9]\d\..*4k.*\.mp4$`，post_notify 开。
   适配点：排除清单去掉「兰丨香如故/」目录前缀（PanKeeper 按裸文件名匹配）；
   NAS 的 share_info（转存后自动 7 天分享）PanKeeper 无此概念未迁移。
   NAS 配置原件备份在 `D:\workspace\fnWork\baidu-autosave-ref\`（**含 Cookie/密码，勿入仓库**）。
3. **全链路意外验证**：测试时误触发了一次真实执行（详见同名踩坑日志）——新增 2 / 跳过 29 / 失败 0 /
   已排除 5，Server 酱推送送达。转存→排除→推送全链路 OK。
4. **排除清单弹窗（ExclModal.vue）打磨**（f018fc9）：
   - 展示口径（用户拍板）：**只显示正则命中的 + 已勾选（已排除）的**。后端 share-files 本来就有
     `filtered=false` 拉全量，弹窗侧按 `task.regex_pattern` 现筛；已排除文件即使正则外也保留展示
     （紫色「正则外」tag），否则被正则筛掉的排除项永远没法取消。
   - 「一键勾选无 MD5」+「刷新」并排上移候选标题行；缓存状态条改胶囊（命中绿/拉取橙）；
     勾选行选中高亮；一键勾选三态提示；已排除文件不在清单时黄字警示「确定后将移出」。
   - PaTask 类型补齐 `regex_pattern/exclude_names/exclude_md5s`。
5. **NAS PanKeeper 已配置同一任务**（2026-10-03 傍晚，用户要求）：API 创建 pa_tasks id=1「兰香如故」，
   acc_id=2（强盘），参数与本地一致，排除清单 5 项，`[pa-sched] 自动任务排期：1 个生效`，
   next-runs=当天 20:00。部署站登录用 `xinyu / LxY252235!`（admin/admin#123 已失效）。
   **注意**：NAS 的 baidu-autosave 容器也是每天 20:00 跑同一任务，两边会撞车（有去重兜底）——
   确认 PanKeeper 稳定后停掉 baidu-autosave 的任务。

## 踩坑（详见同名踩坑日志）

1. **vite 监听不到 `<style scoped src="./xxx.css">` 的 css 变更**（Windows）：改 mt-modal.css 后 HMR
   日志只有 ExclModal.vue 记录、重载也拿旧缓存；`touch` 一下 css 立即恢复。改共享 css 没生效先 touch。
2. 浏览器坐标点击前必须重新取坐标（布局变了会点错按钮——今天就因此误触发执行）。
3. mt- 自定义按钮在 Playwright 下偶发 actionability 卡死，换 CUA 坐标点击可解。

## 接下来可做（候选）

- 百度 adapter 收尾项：115 adapter 转存链路、自动转存 cron 调度真实执行今天已实测 OK（本次误触发即 cron 任务的立即执行路径）
- 本地 pansou/QMS 地址改外网可达（或回家自动切内网），搜索转存联调
- 分享清单预热与转存配置的联动细化、Server 酱推送时机完善
- NAS 侧 `baidu-autosave` 任务还在跑（cron 每天 20:00），与 PanKeeper 侧并存会撞车（有去重兜底）——
  确认 PanKeeper 稳定后停掉 NAS 侧或任务迁移收尾
