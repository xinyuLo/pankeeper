# PanKeeper 工作交接（2026-10-03 晚重写）

> 交接范围：本地开发（Windows，`D:\workspace\fnWork\pankeeper-merge`）+ NAS 部署（192.168.2.77 / 外网 100.66.1.1）。  
> 本文档上一版为 2026-10-03 凌晨版（f4818f4），本次全天内容重写，历史版本在 git 里。

## 0. 一句话状态

日志管理菜单组（搜索历史/转存历史/推送历史/请求日志）+ 推送历史真落库上线；浏览弹窗支持目录新建/重命名/删除（含缓存就地同步）；修复夸克转存套娃目录、去重真空窗、浏览缓存陈旧三个 bug；百度限速定稿 1s。**NAS 部署待更新**（本次推送后按 §1 姿势重建容器）。

## 1. 环境与部署拓扑（本次实测更新）

| 项 | 值 |
| --- | --- |
| 本地仓库 | `D:\workspace\fnWork\pankeeper-merge`（remote = `git@100.66.1.1:xinyu/pankeeper.git`，SSH 5566 已配 ~/.ssh/config） |
| 本地服务 | 后端 `pankeeper-backend/.venv/Scripts/python.exe run.py --port 8000`；前端 `pankeeper-vue3` `npm run dev` :5173；日志 `*-dev.log`（已 gitignore） |
| 本地登录 | `xinyu / LxY252235!`（admin/admin#123 已失效）；NAS 部署站同此账号 |
| Gitea | 内网 `http://192.168.2.77:8029` / 外网 `http://100.66.1.1:8029`（外网 Gitea 只剩合并仓库 pankeeper，独立仓库已删） |
| NAS SSH | `192.168.2.77:10000`（外网 100.66.1.1:10000），xinyu，paramiko 密码认证 |
| **NAS 代码** | `/vol2/1001/disk2/workspace/pankeeper-deploy/`（pankeeper-backend/pankeeper-vue3；remote 走 localhost:8029） |
| **权威 compose** | **fnOS 的 `/vol1/1001/compose/pankeeper/docker-compose.yml`**（build 上下文指向 pankeeper-deploy，镜像 `pankeeper-backend-pankeeper`，容器 `pankeeper`，8031→8000） |
| **数据真身** | bind mount `/vol1/1001/tools/pankeeper/data -> /app/data`（SQLite+密钥）。09-30 文档说的 named volume `pankeeper-data` 是空壳——**别在 pankeeper-deploy/pankeeper-backend 下 compose up（会撞容器名+挂空卷）** |
| **更新部署姿势** | pankeeper-deploy 里 `git pull` → **在 `/vol1/1001/compose/pankeeper` 下** `docker compose up -d`（重建容器挂新镜像，数据 bind 不动） |
| 限速门 | 百度 1.0s / 夸克 0.8s / 115 1.0s（每账号一闸，串行+退避+连败熔断） |

## 2. 本次完成（2026-10-03 全天）

### 2.1 排除文件清单弹窗（ExclModal）打磨
- 展示口径（用户拍板）：**只显示正则命中的 + 已勾选（已排除）的**。后端 share-files 用 `filtered=false` 拉全量，弹窗按任务正则现筛；已排除文件即使正则外也保留展示（紫色「正则外」tag）——否则被正则筛掉的排除项永远无法取消。
- 「一键勾选无 MD5」+「刷新」并排上移标题行（soft chip + 图标）；缓存状态条改状态胶囊（命中绿/拉取橙）；勾选行选中高亮；一键勾选三态提示；已排除文件不在清单时黄字警示。
- PaTask 类型补齐 regex_pattern/exclude_names/exclude_md5s。

### 2.2 日志管理菜单组 + 推送历史（新页面 + 后端真落库）
- 侧栏重排：转存中心（搜索转存/转存配置）、自动转存（三网盘）、**日志管理**（搜索历史←原转存记录、转存历史、推送历史、请求日志←原网盘日志）、系统管理。PC 侧栏 + 移动端「更多」面板同步。
- **push_logs 表**：`notify.push()` 每次真实发出的推送落一行（时间/标题/类型/成败/失败原因）；被时机开关拦掉的不记。Server酱发送原先**不检查响应**，现 HTTP 码+body code 都判（对齐 test_sendkey）。
- `/notify/history` 从 stub 改真明细；新页面 `PushLogs.vue`（状态筛选+统计+表格，口径同转存历史页）。系统设置里的旧「推送历史」按钮删除。

### 2.3 目录浏览弹窗支持新建/重命名/删除（百度+夸克）
- 工具条三键：新建（蓝实心）/重命名（蓝 ghost）/删除（红 ghost，二次确认，**递归删除**）；操作对象=选中目录，没选中=根。
- 后端 `POST /files/dir`、`/files/dir/rename`、`/files/dir/delete`；操作完 `dir_cache.update` **就地更新对应层缓存** + 前端同步改本地树节点——全程零列目录请求。夸克维护 fid→路径映射（改名/删除清旧路径子树行）。
- 115 按钮不隐藏，后端暂回 400「尚未实现」（用户要求留口）。
- 顺修：`LazyDirTree.reload()` 锁定根模式改 `init(force=true)`——刷新绕缓存直连；`dircache.get_or_load` force 分支**回写缓存**（对齐 share_cache 语义），刷新后普通浏览命中的也是新数据。

### 2.4 三个 bug 修复（都由用户实际踩到）
1. **夸克转存套娃**（`ensure_dir`）：把绝对路径当 fid 传 `_list_dir`（永远查空→每层误创建）+ 创建接口 `dir_path` 是相对 pdir_fid 却塞绝对路径 → `/1.影视/待整理-电影` 建成 `/1.影视/1.影视/待整理-电影`。修：逐层用当前 fid 列目录按名匹配，创建只传 file_name。一级路径不触发所以此前未暴露。
2. **浏览映射表污染**（470 行）：全树预热/子层展开时 `_remember_paths` 拿不到父目录真实路径，把深层子目录全记成根路径 → `/1.影视` 解析指到套娃目录、浏览树只剩 1 个目录、路径显示 `1.影视/1.影视/…`。修：子层先反查父 fid 的已记路径，查不到整批放弃（宁缺勿错）；污染行已清，映射按正确路径重建。
3. **百度去重真空窗**：去重基线 `compare_path or save_dir` 二选一，QMS 搬运窗口期两边都查不到 → cron 重跑同一集存两遍（40/41 重复案）。修：基线 = **compare_path ∪ save_dir 双扫**（每轮多 1 次列表请求，值得）。
   - **MD5 去重跳过的文件回写任务排除清单**（名字+MD5，TransferResult.md5_skipped → auto.py 合并）：库里已有的显式排除，QMS 转码改 MD5/分享改名都挡得住；排除弹窗里呈已勾选态可见可取消。

### 2.5 界面小件
- 转存配置页表格对齐全局基础样式（此前漏改：字号/字重/颜色自成一派）；序号并入名称格（独立排序列被 fixed 布局撑到 101px，序号和名称隔 90px 空白）。
- 转存历史统计列改「新增：2 跳过：29 失败：0」（数字绿色等宽，失败染红）+ 表头简化「统计」；转存历史/推送历史刷新按钮统一 `primary ghost` + `#icon` 插槽（修 loading 时按钮宽度跳动）。
- 日志管理四项按用户命名定稿；首页定时任务卡说明文字删除；联动 QMS 弹窗去掉写死的「15 秒」（实际间隔在队列配置）。

## 3. 踩坑（新）

1. **vite 监听不到 `<style scoped src="./x.css">` 的 css 变更**（Windows）：HMR 日志只有 .vue 的记录，重载也拿旧模块缓存；`touch` 一下 css 立即恢复。改共享 css 没生效先 touch。
2. **浏览器坐标点击前必须重新取坐标**：布局变了会点错按钮（今天误触发一次真实转存，因祸得福全链路验证：新增 2/跳过 29/失败 0 + Server酱推送全通）。用 evaluate 现算 getBoundingClientRect 再 cua.click。
3. **夸克创建接口 dir_path 是相对 pdir_fid 的**——塞绝对路径就是套娃制造机；逐层创建只传 file_name。
4. **_remember_paths 类映射表的 Correctness 命门是父目录真实路径**——拿不到就整批跳过，宁缺勿错。
5. mt- 自定义按钮在 Playwright 下偶发 actionability 卡死（无遮挡也超时），换 CUA 坐标点击可解。
6. admin/admin#123 登录已失效（本地与部署站都是 xinyu/LxY252235!）。

## 4. 当前状态

- 本地与 Gitea 同步于本次提交（见 commit）；后端接口/前端 typecheck 全过。
- 夸克盘遗留垃圾：`/1.影视/1.影视/`（今晚套娃目录，用户已手动删除 ✔）。
- NAS 侧 baidu-autosave 容器已停（任务曾停用）；PanKeeper 侧兰香如故任务启用中，每天 20:00 cron。
- pansou/QMS 的本地 settings 还指内网 192.168.2.77，外网联调搜索/刮削要改（回家自动恢复）。

## 5. 接下来可做

- 115 adapter 目录浏览/管理补齐（接口口子已留，后端统一 400 提示）。
- 下钻勾选（drill_json）功能实现（字段保留、短期不实现的约定 2026-10-03 定）。
- 分享清单预热与转存配置联动细化、Server酱推送时机完善。
- 观察几天 1s 百度限速的风控表现，必要时回 2s。
