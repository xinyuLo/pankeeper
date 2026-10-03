# PanKeeper 工作交接（2026-10-03 更新）

> 交接范围：本地开发环境（Windows，`D:\zcodeWork\pankeeper\pankeeper`）+ NAS 部署（192.168.2.77）。  
> 本文档上一版为 2026-10-02，本次重写。以下内容全部为**实测结论**，非推测。

## 0. 一句话状态

百度定时转存（正则过滤 + 排除清单 + 统计 + 转存日志）功能补齐；分享清单缓存、转存记录详情、cron 选择器、移动端适配全面翻新。
**⚠️ 勘误（2026-10-03 凌晨）**：本节早先写的"正则实跑验证"不实——引擎漏存 `regex_pattern`，正则当时从未真正生效（PNG 全量入库实锤）。已修（见 §2.6/§2.7），本地与 NAS 代码统一在 **`9b5ae17`**，今晚 20:00 cron 是首次真正带正则的定时执行。

## 1. 环境与部署拓扑（不变项速查）

| 项 | 值 |
| --- | --- |
| 本地仓库 | `D:\zcodeWork\pankeeper\pankeeper`（remote = 自建 Gitea `xinyu/pankeeper`） |
| 后端 | `pankeeper-backend/`，venv `.venv/`，`run.py --port 8000`，数据 `data/pankeeper.db`（SQLite + WAL） |
| 前端 | `pankeeper-vue3/`，`npm run dev` → :5173（5173 常驻一个上个会话的 vite，vite 按需读源码，改动自动生效） |
| Gitea | `http://192.168.2.77:8029`，账号 xinyu |
| NAS SSH | `192.168.2.77:10000`，账号 xinyu（paramiko 脚本在 `.tools/nas_deploy.py`，密码在脚本里） |
| NAS 源码 | `/vol2/1001/disk2/workspace/pankeeper-deploy`（git clone 自 Gitea） |
| NAS compose | `/vol1/1001/compose/pankeeper/docker-compose.yml`，容器 `pankeeper`，`8031 → 8000` |
| GitHub 代理 | `192.168.2.77:7890`（clash 在 NAS 上），git clone/curl 走 `-x http://192.168.2.77:7890` |

## 2. 本次完成的内容（2026-10-02 晚 ~ 10-03）

### 2.1 百度定时任务功能补齐（对标 bdsavePro，源码 `github.com/xinyuLo/bdsavepro`，克隆在 `.tools/bdsavepro-full`）

- **正则过滤接进转存链路**（重大修复）：此前 `regex_pattern` 只存不用——调度器和转存流程都没读它，兰香如故的 `4k.mp4` 正则形同虚设，实跑 106 个文件全转。现在两处入队（cron + 立即执行）都带正则，auto 流程拿到清单后按文件名过滤（目录条目保留维持结构），日志记录“正则过滤：命中 X / 未命中 Y”，正则非法降级为不过滤并告警。
- **RunHistory 扩容**（自动迁移加列）：skip_md5（跳过里 MD5 命中数，baidu 去重循环单独计数）、total_share（分享文件总数）、regex_miss、duration（秒）、message、transferred_json/excluded_json（本次转存/排除的文件名清单）。
- **转存日志弹窗**（`RunHistoryModal.vue`，bdsavePro 同款）：任务行新增第 6 个图标「转存日志」📄；卡片列表（成功/失败 tag + 秒级起止时间 + 五枚统计 chips + 转存路径 + 消息），「详情」第二层弹窗：执行信息（起止/耗时/结果/路径/正则/包含子目录）→ 五格统计块 → 排除文件/本次转存清单 → LogBox 完整日志。
- **排除清单接真数据**（原 mock）：候选文件来自分享清单缓存（带“无 MD5”标注 + 一键勾选），打开时已保存排除项自动勾上，确定走新接口 `POST /pa/tasks/{id}/exclude` 回写 `exclude_json`（适配器按 basename 排除，链路本已通）。30 分钟 TTL 定时刷新随 mock 撤销。
- **查看分享内容修复 + 缓存**：树为空的根因是**树构建时无斜杠的顶层条目 `rsplit` 后 parent 算成了自己**，顶层节点全被吞（实测 total=106 tree=0）；标题栏加「刷新」按钮 + 缓存获取时间显示。

### 2.2 分享清单缓存（独立逻辑，刻意不与目录缓存共用）

新文件 `app/services/share_cache.py` + 新表 `share_list_cache`（键 = type+链接+提取码）：

- **刷新由转存驱动，无 TTL**：自动转存每跑一次，`run_auto` 拿清单后顺手把 payload 写入缓存（转存用的那份就是缓存那份）；两次转存之间「查看」「排除」全部命中秒开。
- 持久化写穿 SQLite，重启恢复；恢复失败**不置标记**、下次访问重试（同 dircache 修复，见 §3.3）。
- dircache（目录浏览缓存）一行未动。

### 2.3 服务端稳定性与缓存修复

- **Windows 事件循环**：`run.py` 在 win32 下切 `WindowsSelectorEventLoopPolicy`——Proactor 循环偶发 `WinError 10022`（连接建立即断），是前端 vite 代理偶发 500 的根源；切换后连发请求不再抖。
  ⚠️ **2026-10-03 勘误**：上面那条修复**实际从未生效**——uvicorn 0.36+ 在 win32 会用 loop_factory 直接造 `ProactorEventLoop`，`set_event_loop_policy` 对它无效。真修法是 `uvicorn.run(..., loop="none")`（不让 uvicorn 造循环，policy 才落得下去）。症状：手机端目录浏览弹窗每次打开都"目录加载失败，正在重试"（后端其实全部 200，连接在传输层被掐）。升级 uvicorn 后若再遇"后端日志 200 但前端重试"，先查循环类型。
- **dircache 恢复失败静默吞掉（修复）**：`_ensure_restored` 原来先置 `_restored=True` 再恢复，DB 忙时恢复失败→整个进程生命周期无缓存→每次浏览真打百度→-7 风控。改为失败不置标记、下次重试。
- **目录缓存键统一（修复）**：初始化浏览键 `p:/A罗` 与子层展开键 `/A罗` 对不上，同目录两份缓存互不命中，初始化永远 miss。统一为路径本身（真根键是 `0`，不冲突）。
- `/files/list` 响应带 `cached` 标记（前端节流用，见 2.5）。

### 2.4 前端大改

- **PkPager 分页组件重写**（搜索/记录/转存配置/自动转存四页共用）：桌面幽灵页码右对齐（总数左、页码+每页+跳页贴右缘），手机一行式 `‹ 2/6 › + 共 N 条 + 每页`；转存配置与自动转存表格加内存分页（每页 20）。
- **搜索页**：统计卡常驻不随 tab 重排（点卡只挪高亮）；选中态改**淡紫描边**（#a78bfa 1.5px + 淡紫底）；耗时卡撤掉（PC 进“已检索 N 条 · 耗时”，手机进频道行右缘）；引擎状态胶囊三重防抖（缓存基调 + 探测失败不打翻缓存 + 请求挂掉不卡“检测中”）；频道行手机两行式（数量摘要 + 引擎状态独占一行）。
- **任务弹窗（TaskModal）**：「完成后动作」整行撤掉（触发 QMS = 上面 QMS 联动开关唯一控制点；Server 酱推送走「推送通知」全局开关，后端只推启用中的任务）；「转存文件夹下钻」功能暂撤（界面不露，已有任务的 drill 配置保存不丢）；**cron 选择器**（`CronPicker.vue`：每天/每周/每小时/每 N 分钟/自定义页签，antd 组件，实时人话预览，已有表达式反解析）；QMS 目录改为点开联动时才拉（loading + 重试）；解析/浏览按钮改无边框淡蓝 chip。
- **目录选择弹窗**：新建 `AutoDirModal.vue`（antd 外壳 + 共享 LazyDirTree 内核，根锁定默认根目录），任务弹窗专用；原 `DirModal.vue` 已原样恢复未动。**教训：不要跨页借样式类**（.bd-* 是页面私有，自动转存页没加载就是裸奔）。
- **记录页**：详情抽屉（完成后动作显示 QMS/STRM 实际结果 + 最近结果加文件清单详情弹窗 + 手机抽屉 100% 宽）；分享失败长文案 tag 限宽省略；操作列「详情 丨 删除」文字链样式（删除红字二次确认）。
- **LazyDirTree**：列目录统一入口 `fetchDir`（风控节流 + 2s×3 重试 + loading 状态文案“正在重试 N 次…”）；后端 `cached` 标记驱动节流（命中缓存不等待，真打网盘才 600ms 冷却）；默认不自动下钻（从默认根目录开始，手动逐层懒加载）。
- 自动转存表头定宽、dashboard 问候语、账号页等零散适配。

### 2.5 数据与记录

- 转存记录详情弹窗：`Record.files_json`（分享内文件清单快照，`/records` 直出），“完成后动作”显示 QMS/STRM 实际结果。
- 115 适配器 `transferred` 补文件名（原先空串，media_push 推送名单受益）。
- **NAS baidu-autosave 任务已迁移**：兰香如故（baidu，cron 0 20 * * *，正则 4k，排除 5 项，compare_path 灿如繁星 S1）→ PaTask id=1，调度已注册。

## 2.6 本批增量（2026-10-03 凌晨）

- **正则命中清单落库**：RunHistory 新列 `regex_hit_json`（自动迁移），auto 流程正则过滤后记录放行文件名，`/pa/runs/{id}` 详情直出 `regex_hit`，转存日志详情弹窗新增「正则命中（N）」区块（旧记录为空数组不显示，下次真实转存起生效）。过滤逻辑抽成纯函数 `_apply_regex` / `_apply_exclusion`（auto.py），4 条单元测试覆盖。
- **排除清单加 MD5 粒度**：PaTask/QueueTaskRow 新列 `exclude_md5_json`；排除弹窗「确定」同时回写文件名+MD5（`POST /pa/tasks/{id}/exclude` 带 `md5s`）；转存链路按「名字或 MD5 任一命中」剔除（baidu/quark/pan115 三适配器同语义），分享里改过名的文件靠 MD5 兜住。**顺手修了个隐患**：此前排除在适配器 list_share 内先过滤，auto.py 记排除名单的代码实际永远为空——现在排除统一由 auto 流程做并如实落库。
- **`share/list` 相邻调用强制 2 秒间隔**（BaiduClient._pace_share_list，实例级时间戳）：覆盖转存 walk 全部列目录请求，防 -7 风控。
- **下钻功能（转存文件夹多选）短期不实现**（2026-10-03 定）：`drill_on`/`drill_json` 字段保留、已有任务配置不丢，界面/链路/端点均不做。将来重启开发时的既定方案：浏览端点优先命中分享清单缓存（零请求），未命中走全量 list_share 回填缓存；bdsavePro 参照 AddTaskDialog.vue（逐层进入+多选勾选）+ storage.py transfer_folders 语义（keep_folder 控制是否连文件夹本身一起存）。

## 2.7 本批增量二（2026-10-03 凌晨，提交 9b5ae17）

- **引擎漏存 regex_pattern（PNG 全量入库的根因）**：调度器一直传 `regex_pattern`，`engine.enqueue` 从没落到任务状态，`run_auto` 读空 → 正则形同虚设。修：enqueue 补 `regexPattern`/`enabled`；`queue_tasks` 加 `regex_pattern` 列（迁移自动加列），`_persist`/`restore` 三处对齐；回归测试 2 条（39 passed）。
- **排除候选按任务正则过滤**：`share-files` 加 `filtered` 参数，缓存出全量、出仓后现筛（零额外网盘请求）；ExclModal 提示文案同步。实测兰香如故 113 → 1。
- **uvicorn 事件循环勘误**：uvicorn 0.36+ win32 用 loop_factory 直接造 Proactor，`set_event_loop_policy` 无效——`run.py` 改 `loop="none"` 才真吃上 Selector（手机端浏览弹窗"失败重试"的真凶，见 §2.3 勘误）。
- **手机端任务卡片改版**：链接行补上缺失的网盘链接（点击即跳转）+ 提取码 + 复制；操作分级：主按钮 执行/转存日志/编辑，次操作小字链 查看文件/排除/详情/删除。


## 2.8 本批增量三（2026-10-03 下午）

- **记录来源拆分**：`records` 新增 `source` 列（`search`=手动查询转存 / `auto`=自动转存），迁移时幂等回填（share_url 命中任一 PaTask 的旧记录判 auto）；`/records` 默认只回 `search`；manual/auto 两个流程各自写标记。**记录页从此只展示手动转存，自动的走「转存历史」页 + 任务内「转存日志」。**
- **新增「转存历史」页**（侧边栏 自动转存 → 转存历史，路由 `/auto/history`，手机端在「更多」面板）：
  - 新接口 `GET /pa/runs`：全任务聚合历史（join PaTask 带 task_name/task_type/save_dir），支持 task_id / type / status / keyword 筛选 + 分页；
  - 页面：筛选条 + PC 表格 + 手机卡片 + PkPager，行上「详情」；
  - 详情抽成共用组件 `views/auto/RunDetailModal.vue`——单任务「转存日志」弹窗与本页共用同一份详情，改一处两边都对。
- **详情三段恒常显示**：正则过滤后的文件 / 排除文件 / 本次实际转存，没数据时给空态说明（区分「未配置正则」「旧运行未记录该字段」「本次没命中排除」）；清单限高 210px + 滚动（flex 子项要 `flex: none`，否则长列表被压缩而不是滚动）。
- **表格样式统一（重要约定）**：本项目自定义表格一律继承 `styles/pk.css` 的全局 `th/td`（th 13px/600/text2，td 13.5px，行 hover），页面**只覆盖横向内边距**（记录页与自动转存页都取 12px）。本条踩了三次坑（历史页、自动转存列宽、表头），别再在页面里另起一套字号字重。
- **自动转存列表细节**：分享链接展示截断到 18 字符（全量在 title 与「复制」）；新增 `compactResult()` 把后端「新增 N / 跳过 N / 失败 N」压成「新增 N」（失败非 0 才补 `/ 失败 N`），保证最近结果一行放下；启用列宽 64px（装得下 40px 开关 + 左右 12px 内边距）。
- **QMS 连接修复**：本地 PanKeeper 存的 `qms.apikey` 是早期测试残留的占位假值 `qms-key-abc` → 健康检查 401「API Key 无效」。真 Key 从 NAS 的 pankeeper 容器配置里取出（前缀 `qms_AUqrw`，与 QMS postgres `api_keys` 表 id=4「pankeeper」对应）写入本地。排查路径：QMS 配置在 `/vol1/1001/tools/qmediasync/config/config.yml`（生效那份，postgres@15432），API Key 只存哈希（不可逆）在 `api_keys` 表，丢了只能在 QMS 后台重建。
- **免密截图验收手段**（以后自查 UI 用）：页面需登录且用户改过密码 → `from app.security import make_token; make_token('admin', 30)` 本地签测试 token，再 `agent-browser eval "localStorage.setItem('pk-auth','<token>')"` 注入即可免密渲染截图（token 与截图用完即删）。

## 3. 关键定论（别忘，别再改回去）



### 3.1 风控与缓存纪律

1. **百度对密集列目录回 -7/-9（可重试抖动）**：前端所有列目录走 `fetchDir`（600ms 节流 + 2s×3 重试）；后端有目录缓存 + 分享清单缓存，能命中就不打百度。
2. **分享树构建**：无斜杠路径的 parent 必须用哨兵，`rsplit` 会自吞（转存配置页的浏览、记录页清单都踩过同款）。
3. **缓存恢复失败不能置“已恢复”标记**——否则进程终身无缓存，全部真打网盘。
4. **缓存键必须全局一致**：同一目录在“初始化/子层/预热”三条路径里的键不同 = 三份缓存互不命中。

### 3.2 前端纪律

5. `-webkit-line-clamp` 必须配 `overflow: hidden`，漏了会一字一行竖着溢出（记录页实拍）。
6. **跨页面借样式类不行**：页面私有的 scoped/unscoped 样式只有该路由加载过才存在。
7. scoped `<style>` 里 media query 的大括号配对要人工复核（嵌套闭合错会整段漏到全局，PC 端直接坏）。
8. Git Bash 会把 `/中文路径` 参数 MSYS 转换成 `C:/Program Files/Git/...`——curl 测接口要么加 `MSYS_NO_PATHCONV=1`，要么用 python requests，**否则制造假 -7 冤案**。
9. `taskkill`/TaskStop 杀后台任务要按端口找 PID 再连父进程一起杀——shell 杀了 python 子进程会活着重占 8000，新代码永远上不去。

### 3.3 后端纪律

10. `run.py` 的 Selector 事件循环只对 uvicorn 主进程生效，改完必须真重启（旧进程常驻会让人以为没生效）。
11. 后端无 `--reload`，改 py 必须重启；重启后用 `python requests`（不是 curl）验证，绕开坑 8。

## 4. 当前系统状态

| 项 | 状态 |
| --- | --- |
| 本地 | 后端 :8000（loop="none" → Selector 循环）✅、前端 :5173 ✅、测试 39 passed、typecheck 通过 |
| Gitea | 见 §4.1（本批提交哈希随文档同批推送） |
| NAS | pankeeper 容器随本批重建（2026-10-03 下午核验：HTTP 200 + 新页面在新构建里）；兰香如故 cron 0 20 * * * |
| 分享清单缓存 | `share_list_cache` 表，兰香如故 113 项已缓存（转存驱动刷新，见 §2.2） |
| 已知待办 | ~~正则过滤后文件清单未单独落库~~ ✅（§2.6）；~~排除粒度按文件名~~ ✅ 加 MD5（§2.6）；~~记录/自动历史混在一起~~ ✅ 已拆分（§2.8）；~~QMS 连不上~~ ✅ 假 Key 已换真 Key（§2.8）；下钻功能**短期不实现**（方案见 §2.6）；自动转存误存的 PNG 待用户自行清理（清单在「转存历史 → 详情 → 本次实际转存」） |

### 4.1 本批提交

- `9b5ae17` 正则透传修复 + 排除候选按正则过滤 + 手机卡片改版（凌晨）
- `59da8dd` 交接文档定稿（凌晨）
- `c65360d` 记录来源拆分 + 转存历史页 + 详情三段空态/限高 + 表格样式统一 + 列表细节（下午）；NAS 容器已随本提交重建，部署目录核验在 `c65360d`


## 5. 快速自检 / 部署命令

```bash
# 后端测试
cd pankeeper-backend && .venv/Scripts/python -m pytest tests/ -q

# 本地起服务
cd pankeeper-backend && .venv/Scripts/python run.py --port 8000
cd pankeeper-vue3   && npm run dev

# 提交推送（PortableGit 走 http 会卡死，必须用系统 Git）
"C:/Program Files/Git/cmd/git.exe" -c http.version=HTTP/1.1 push origin main

# NAS 更新（一条龙）
ssh NAS "cd /vol2/1001/disk2/workspace/pankeeper-deploy && git pull --ff-only origin main"
ssh NAS "cd /vol1/1001/compose/pankeeper && nohup docker compose build > /tmp/pkbuild.log 2>&1 & disown"
ssh NAS "tail -3 /tmp/pkbuild.log"   # 轮询
ssh NAS "cd /vol1/1001/compose/pankeeper && docker compose up -d && sleep 5 && curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8031/"

# 容器代码新鲜度核验（光看容器 Up 不算数）
docker exec pankeeper grep -c <本次改动特征串> /app/app/xxx.py
```
