# PanKeeper 工作交接（2026-10-06 更新）

> 交接范围：本地开发（Windows，`D:\zcodeWork\pankeeper\pankeeper`）+ NAS 部署（192.168.2.77 / 外网 100.66.1.1）。  
> 本文档上一版为 2026-10-04 晚版（b9dda21），本次 LitePan 对接批次增量更新，历史版本在 git 里。

## 0. 一句话状态

**10-06 批次三：识别候选选择 + 剥壳全平铺**——①识别歧义（搜"狂飙"想存 F1：狂飙飞车，旧算法按热度永远给 2023 剧集）：`recognize_candidates` movie+tv 双搜 + 粘连名变体重试 + 分享内文件名信号（只读预热缓存）打分，不自信就弹候选卡片（海报/年份/类型）让用户挑，`/recognize` 返回 `confident+candidates`，两个转存弹窗接 `RecognizePicker`；②剥壳转存**全平铺**（用户实锤多层文件夹 QMS 识别不出）：baidu/quark `_save_with_shell` 把所有文件夹层级剥光平铺进新壳一层，同名文件自动父目录前缀改名防覆盖。详见 `工作日志/2026-10-06-PanKeeper-识别候选选择与剥壳全平铺.md`。

**10-06 批次二：推送串台/漏集双 bug 根修 + 推送历史详情**——用户实测两单（自动转存 6 集只推 1 集；搜索转存狂飙推送变成「兰香如故 · 更新 1 集 S01E17」）根因=①推送/回填「全部终态」判定漏了"还没记录的文件"（匹配到 1 条旧记录就早退）+②裸名文件跨剧串台（狂飙 17.mp4 撞兰香如故旧批次同名 QMS 记录，QMS 记录的 path 字段为空没法按目录过滤）。修法：`run_watch._wait` 重写为唯一真相源（**全覆盖才收工** + **baseline 指纹全路径必传**只认本次触发的新记录 + 停滞 10 分钟保护），media_push 委托调用；推送信息条/兜底对缺文件如实点名。另：push_logs 加 content 列（Server酱正文快照）+ 推送历史「详情」抽屉。详见 `工作日志/2026-10-06-PanKeeper-推送串台漏集根修与推送详情.md`。

**10-06 批次一：LitePan 对接落地**——外层 LitePan Go 源码通读定稿协议（**HTTP Webhook**，非此前猜测的 WS：`POST /api/open/automation/events`，体 `{event,source,path}`，Bearer API Key，规则异步执行响应带 matched/triggered）→ `services/litepan.py` 真实现（notify_transfer_done + test_webhook，失败分支如实报）→ 设置组 litepan（webhook_url/apikey 加密/event）+ 设置页 tab3 恢复后端切换下拉（顺修 onMediaBackend 不回写本地值的潜伏 bug）+ LitePan 参数表单与测试按钮。**真连 LitePan 实测未做**（NAS 尚无实例），部署后点「测试」即可验。详见 `工作日志/2026-10-06-PanKeeper-LitePan对接落地.md`。

**10-05 深夜批次已收口并部署 NAS（6df209e）**：**TMDB 自识别器**（`media_recognize.py`，±1 年容错+存疑标记，推送不再依赖后端刮削记录）+ **LitePan 独立推送流程**（`media.backend` 分流，两套日志互不掺和）+ **转存弹窗识别按钮**（回填按 QMS 规则「标题 (年份)」，年份以 TMDB 为准）+ **STRM 日志口径如实**（定向=临时任务，整路径=触发同步目录）+ **Emby 刷新修复**（QMS emby-config 套层解析）——**转存→刮削→定向同步→Emby 刷库全链闭合**。详见 `工作日志/2026-10-05-PanKeeper-TMDB自识别与联动收尾.md`。

10-04 全天大批次已收口（工作日志见 `工作日志/2026-10-04-PanKeeper-建壳重构-115适配-联动后端切换.md`）：
**建壳转存重构**（按资源名/更名值新建文件夹+剥壳转入，删除事后改名链）；**115 适配器补全**
（建壳/整目录接收/batch_rename 三字段改名/目录管理/summary，Cookie 路线全实测）；**STRM 跟随
QMS 自动配对**（刮削 dest_path ↔ 同步 remote_path，转存配置不再选 STRM）+ **定向同步**（manual
临时任务只扫整理出的一个目录）；**推送死代码修复**（media_push._watch 尾巴永不执行，推送全丢）；**
联动后端切换预留**（media.backend qms/litepan，UI 已隐藏默认 qms）。⚠️ 115 风控 302 案：限速已放宽
到 2s+302 识别，账号封禁等自愈。⚠️ NAS 部署待更新（本批推送后按 §1 姿势重建容器）。

## 1. 环境与部署拓扑（本次实测更新）

| 项 | 值 |
| --- | --- |
| 本地仓库 | `D:\zcodeWork\pankeeper\pankeeper`（remote = `http://192.168.2.77:8029/xinyu/pankeeper.git`） |
| 本地服务 | 后端 `pankeeper-backend/.venv/Scripts/python.exe run.py --port 8000`；前端 `pankeeper-vue3` `npm run dev` :5173；日志 `*-dev.log`（已 gitignore） |
| 本地登录 | `xinyu / LxY252235!`（admin/admin#123 已失效）；NAS 部署站同此账号 |
| Gitea | 内网 `http://192.168.2.77:8029` / 外网 `http://100.66.1.1:8029`（外网 Gitea 只剩合并仓库 pankeeper，独立仓库已删） |
| NAS SSH | `192.168.2.77:10000`（外网 100.66.1.1:10000），xinyu，paramiko 密码认证 |
| **NAS 代码** | `/vol2/1001/disk2/workspace/pankeeper-deploy/`（pankeeper-backend/pankeeper-vue3；remote 走 localhost:8029） |
| **权威 compose** | **fnOS 的 `/vol1/1001/compose/pankeeper/docker-compose.yml`**（build 上下文指向 pankeeper-deploy，镜像 `pankeeper-backend-pankeeper`，容器 `pankeeper`，8031→8000） |
| **数据真身** | bind mount `/vol1/1001/tools/pankeeper/data -> /app/data`（SQLite+密钥）。09-30 文档说的 named volume `pankeeper-data` 是空壳——**别在 pankeeper-deploy/pankeeper-backend 下 compose up（会撞容器名+挂空卷）** |
| **更新部署姿势** | pankeeper-deploy 里 `git pull` → **在 `/vol1/1001/compose/pankeeper` 下** `docker compose up -d`（重建容器挂新镜像，数据 bind 不动） |
| 限速门 | 百度 1.0s / 夸克 0.8s / **115 2.0s**（每账号一闸，串行+退避+连败熔断；115 有 302 风控识别） |

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
   - ~~**MD5 去重跳过的文件回写任务排除清单**（名字+MD5，TransferResult.md5_skipped → auto.py 合并）~~
     **⚠️ 2026-10-04 已取消**（用户拍板）：该回写等于任务静默改自己的配置——删了本地文件想重转会被清单挡住、不看日志发现不了（实锤：`40.4k.mp4` 被自动勾进排除清单）。现在只在日志/RunHistory 如实记录（`skip_md5` 计数 + 适配器「MD5 命中跳过」日志行），要排除由用户手动勾（`POST /pa/tasks/{id}/exclude`）。**别再恢复这段回写**（`auto.py::_sync_pa_task` 留了同款警示注释）。残留的 1 项已通过接口清空。

### 2.5 界面小件
- 转存配置页表格对齐全局基础样式（此前漏改：字号/字重/颜色自成一派）；序号并入名称格（独立排序列被 fixed 布局撑到 101px，序号和名称隔 90px 空白）。
- 转存历史统计列改「新增：2 跳过：29 失败：0」（数字绿色等宽，失败染红）+ 表头简化「统计」；转存历史/推送历史刷新按钮统一 `primary ghost` + `#icon` 插槽（修 loading 时按钮宽度跳动）。
- 日志管理四项按用户命名定稿；首页定时任务卡说明文字删除；联动 QMS 弹窗去掉写死的「15 秒」（实际间隔在队列配置）。

## 2.9 本批增量四（2026-10-04 凌晨）

- **QMS/STRM 快照语义修正 + 真实结果回填**（用户实锤：QMS 侧 `41.4k.mp4`、`35~38.4k_1002164941.mp4` 都是 `scrape_failed`，PanKeeper 详情却亮绿"成功"）：
  - 根因：`qms.trigger_scrape` 只证明 QMS **受理了触发请求**（HTTP 200 + code 0），不是刮削成败；快照在触发瞬间写死成"成功"。
  - 改：触发时如实写 **「已触发」**（`auto.py::_media_chain`，QMS/STRM 都改）；新增 `services/run_watch.py` —— 转存落库拿到 run id 后 spawn 守护线程，按**本次转存的文件名**轮询 `GET /api/scrape/records` 到终态（renamed/scrape_failed/rename_failed），把汇总（成功 N / 失败 M / 未见记录 K）回填 `run_history.qms_json`。**不依赖推送开关**（结果展示是独立诉求）。30s/轮、总超时 30 分钟、一条记录都没有时 10 分钟收工。
  - **坑**：QMS 的 `name=` 过滤实测会漏记录（同一文件带 name 查不到、不带就查得到），所以 run_watch **不按任务名筛**，一次拉 500 条按 `file_name` 精确匹配（新→旧取第一条）。`media_push._wait_records` 仍在用 name 过滤，将来若发现推送"等不到终态"多半是这个原因。
  - STRM 不做轮询（QMS 无对应查询接口），如实显示「已触发」。
  - 验证：拿 run 8（文件 `41.4k.mp4`，QMS 里 `scrape_failed`）跑真实链路 → 回填 `{"st": "失败 1 项（详见 QMS）", "cls": "t-bad"}`，接口直出已确认；`_summary` 五个分支（全成功/有失败/全失败/无记录/部分未见）逐条自检通过。
- **「整单结果」口径**（同批，用户要求："最近结果应该是部分失败吧"）：新增 `run_watch.overall_of(status, qms_snap)` —— 转存失败 → 失败（红）；转存成功但 QMS 有失败 → **部分失败**（橙 `t-warn`）；其余 → 成功（绿）。落地三处：`/pa/runs` 列表与 `/runs/{id}` 详情直出 `overall` 字段；`run_watch._write` 在该 run 仍是任务最新一次时，把 `PaTask.last_status` 升级为 `partial`（任务行「最近结果」显示"部分失败 · 新增 N"）。前端：`PaTask.last_status` 加 `partial`、`pa-st-partial` 橙色 tag、转存历史页「结果」列与转存日志卡片 tag 均改用 `overall`。**别再只看 `status` 报"成功"——那只是转存那一半。**
- **「重新触发 QMS」按钮 + 重刷回填**（2026-10-04 用户要求："重新触发刮削，成功的话就帮我改成成功吧"）：`POST /pa/tasks/{id}/retrigger-qms`（`pa.py`）——联动目标走 `auto.resolve_media_link`（**任务级 `qms_id`/`strm_id` 优先，没配才回退 `dd_items`**，见下方"联动目标解析规则"）→ 触发刮削；**受理后顺带给该任务最新一次运行挂 `run_watch` 回填**：刮成功→那次运行改判「成功」、任务行从部分失败翻回成功；仍失败→维持原判。刻意不写"重刷中"中间态（判定会来回跳）。前端：自动转存行操作第 7 个紫色图标（`pa-op-qms`，SyncOutlined 转圈 + 防连点）+ 手机端次操作字链；操作列宽 196→230。实测：触发返回 `{"ok":true,"qms_id":10,...}`，日志 `[run-watch] run 8 QMS 结果回填：…`；成功/失败两条路径均验证并已还原真值。
- `run_watch` 的 print 加了 `flush=True`——重定向到文件时块缓冲会吞日志（排查时踩过）。
- **QMS 路由硬情报**（2026-10-04 从 Go 二进制符号 + GitHub `chen8945/QMediaSync` 源码双向确认；`backend/main.go` 注册处）：
  - `POST /api/scrape/pathes/start {id}` 触发刮削；`GET /api/scrape/pathes/{id}` 运行状态
  - `GET /api/scrape/records` 逐文件记录；**`DELETE /api/scrape/records?ids=1,2,3`** 按 ID 删记录（不限状态）
  - **`POST /api/scrape/clear-failed`** 清除**所有**目录的失败记录（= QMS UI 上那个按钮）；`POST /api/scrape/truncate-all` 清空全部
  - `POST /api/sync/path/start {id}` 触发 STRM；`GET /api/sync/path-list`
  - 语义：`models.ClearFailedScrapeRecords(ids)` —— ids 空 = 只删 `status='scrape_failed'` 的；给了 ids = 按 ID 删。
  - **QMS 按文件路径去重**：失败记录还在时再触发不会重刮 → 手动重刷必须先删记录。PanKeeper 用按 ID 精确删（不误伤别的目录），没用全局 clear-failed。
- **STRM 改为「等刮削真跑完」再触发**（2026-10-04 用户拍板，对齐 bdsavepro 语义）：
  - 原实现是**固定间隔**（`queue_cfg.strm`，默认 10s）就触发——刮削比它慢时 STRM 会在刮完前触发（元数据不全）。
  - 现实现 `run_watch.trigger_strm_after_scrape(run_id, qms_id, strm_id, delay, timeout)`：后台线程轮询 `GET /api/scrape/pathes/{id}`，**完成判据 = 曾亲眼见它忙（is_running≠0 或 is_scraping）→ 现在不忙了**；`updated_at >= 触发时刻 - 60s` 作为辅助信号（**必须在 60s 容差内**：QMS 在 NAS、PanKeeper 在另一台机，时钟差会让严格比较误判为假——桩测试实测踩到）。确认跑完 → 再等 delay 秒 → 触发 STRM。
  - 超时（默认 5 分钟）→ 记 `未确认（刮削超时，未触发 STRM）`，**不触发 STRM**；取不到状态 → 退化成"等 delay 秒直接触发"（不把能力弄丢）。
  - **自动流程同步改造**：`auto.py::_media_chain` 不再在队列里 `_sleep_phase` 死等（原实现会堵住 worker），改为 strm 快照先写「等待刮削完成…」，落库后由 `_sync_pa_task` 挂后台线程。
  - 桩验证四例：见过忙→闲=触发 / 一直闲且时间戳不前进=超时不触发 / 时间戳更新=触发（含容差）/ 取不到状态=退化触发。
  - ⚠️ 注意：百度电视剧目录（`dd_items` id=3）**strm_id 为空**，所以该任务根本不会触发 STRM；只有配了 strm_id 的目录（如电影 id=4→strm_id=7）才有这一步。
- **⚠️ 常驻「自愈复查」已按用户要求关闭（2026-10-04 01:23）**：曾实现过每 3 分钟扫「最近 24h 内未成功」的运行自动翻正（`start_reconciler`，一轮 1 个 QMS 请求）；用户明确不要后端全天候轮询 → 代码已删（`git log` 里有）。**现状：只有两条触发时轮询**——转存收尾（30s/次、上限 30 分钟、无记录 10 分钟收工）与手动重刷（多一个 120s 判定 QMS 是否真重刮）。QMS 晚成功**不再自动翻正**，要翻正就点任务行的「重新触发 QMS」。别自作主张加回来。
- **「重新触发 QMS」定稿链路**（用户 2026-10-04 要求）：① 门禁——只有最新运行的 QMS 快照 `cls=t-bad`（任务行"部分失败"）才允许点（前端 disabled + 后端 400 双保险）；② `DELETE /api/scrape/records?ids=` 清本次文件的失败记录；③ `POST /api/scrape/pathes/start` 重刮；④ 该目录配了 `strm_id` → **等刮削真跑完**（`run_watch.trigger_strm_after_scrape`）再等「队列配置」的 `strm` 秒数触发 `POST /api/sync/path/start`（结果只写「已触发 / 失败 / 未确认」，QMS 侧没有 STRM 结果接口）；⑤ 挂 `run_watch` 回填（baseline 取删除后指纹 → 任何新记录都算 fresh），刮好自动改判成功。
- **⚠️ 联动目标解析规则（2026-10-04 修，`auto.resolve_media_link(path, task_id)`）**：**任务弹窗里配的 `qms_id`/`strm_id` 优先，两个字段各自独立回退到按 `save_dir` 前缀匹配的 `dd_items` 目录**。历史 bug：执行侧（`auto._media_chain`、`pa.py` 重触发）**原只读目录级**——用户给任务 1 配了 `strm_id=5`，代码却只认目录（目录那层为空）→ **STRM 永不触发，且界面毫无提示**。实测 `resolve_media_link('/A罗/0.影视/待整理-电视剧/兰香如故(2026)', 1)` → `{'qms_id': 10, 'strm_id': 5}`（旧逻辑 `strm_id=None`）。**⚠️ 总闸（2026-10-04 用户定稿："那个目录要是关闭了，就等于没配"）**：`save_dir` 命中的「转存配置」目录若 `qms_on=false` → `resolve_media_link` **一律返回 None（未配）**，任务里选过什么都不算（前端打开任务弹窗也会把失效选择清空并提示）。**总闸开着、或 `save_dir` 未命中任何目录时**，才走上面的"任务级优先"。实测：目录开 → `{'qms_id':10,'strm_id':5}`；目录关 → `None`；恢复 → 又回来。
- **推送信息条补「STRM 已生成」+ STRM「已触发」改绿（2026-10-04）**：自动转存的 `strm_ok` 在 STRM 改后台等待后恒为 `None`，推送信息条从不显示 STRM 项。修法：`run_watch` 加 `_STRM_RESULTS` 登记 + `get_strm_result(run_id)`；`watch_and_spawn` 从 `_media_chain` **挪到 `_sync_pa_task`**（那里才有 run_id），ctx 带 `run_id` + `strm_plan={strm_id,delay}`；`media_push._wait_strm` 在 QMS 终态后轮询等结果（上限 delay+340s）。信息条：✅ STRM 已生成 / ❌ 失败原因 / ⏳ 未确认；`_overall_status` 把 STRM 失败计入 fail（`_header`/`_fallback` 均传 `strm_res`）。manual（同步触发）走 `strm_ok` 旧通道不变。同时 STRM 快照「已触发」的 cls 由 t-off 改 **t-ok（绿色）**——用户要求；QMS 的"已触发"保持灰（它随后会被真实结果回填替换）。
- **搜索转存同款回填（2026-10-04）**：`run_watch.watch_qms/_write` 加 `table` 参数（RunHistory=自动 / Record=搜索）；`manual._media_chain` QMS 受理写「已触发」、`_finish` 落库后挂回填（存量夸克 record 27 已按 QMS 真实状态订正「失败 4 项」）。
- **搜索转存「带壳」+ 文件夹更名（2026-10-04 02:15）**：`TaskSpec.with_shell/folder_rename`；`baidu/quark list_share` 单壳时存 `self._root_shell`，`save_files` 带壳分支整壳转（百度 fsidlist=[壳fsid]、夸克 fid_list=[壳fid]，目标侧自带文件夹名）+ `folder_rename` 非空转完 `rename_dir`；去重口径=目标已有同名文件夹→整壳跳过。链路：QuickTransferModal（`rename/with_shell`）→ EnqueueBody → engine t（`rename/withShell`，与 filePaths 同款**不持久化**）→ manual spec。**auto 不传，行为不变**。桩测四例全过（并抓出漏 `on_progress`、漏 `ctx` 两个真 bug）。端到端待用户实点一次快速转存。
  - **⚠️ 套娃真根因（03:00 修，eae6002）**：`quark.list_share` 原来拿 `_walk_share(include_subdirs=True)` 的 `len(files)` 判单壳——列表含壳内全部文件，壳里有文件就永远 >1 → **quark 单壳分享全部被误判非单壳** → 建壳+原壳整转 → 双层同名（用户实测）。修：先 `include_subdirs=False` 只列根层判单壳；单壳→剥壳遍历（base=/）；非单壳→根层+子树（原行为）。baidu 判定本就正确。
  - **百度"未找到 yunData"抢救强化（03:10，c88bd4e 诊断 + 本批二轮抢救）**：分享页抓取失败时最多两轮抢救——init 预热+重抓 →（仍失败）**重新提交提取码**+重抓；全失败时错误信息带页面特征（title/字节数），安全验证页单独提示"疑似风控，稍后重试或在浏览器打开一次该分享"。`list_share` 存 `self._last_pwd` 供二轮重验。
  - **⚠️ 02:40 补丁（用户两条）**：① **非单壳建壳兜底**——散文件/多文件夹混杂的分享在 save_dir 下建壳（壳名=更名值或分享名 `spec.share_name`），只转根层条目（文件夹整转带子树、散文件直转，子文件不重复）；**有壳的分享走整壳分支，建壳只是兜底**。② **QMS 刮失败不生成 STRM**——`trigger_strm_after_scrape` 真等刮完后再查本次文件 QMS 记录，有失败就不触发（快照「QMS 刮削失败 N 项，未生成 STRM」）；**manual 的 STRM 同步触发已删**，与 auto 统一挂后台（`table=Record`；`_write_strm/get_strm_result/_STRM_RESULTS` 带 table 维度，key=`TableName:id` 防撞）；manual 推送 ctx 带 `run_id/strm_plan/strm_table="Record"`。想补救走「重新触发 QMS」，重刷成功会照常续上 STRM。

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
- **LitePan 对接已落地（2026-10-06）**：协议=HTTP Webhook（`POST /api/open/automation/events`），
  设置页 tab3 配 webhook_url/apikey/event。剩余动作：NAS 部署 LitePan 实例后建 webhook 规则
  （事件名对齐设置页、路径前缀按转存目标目录配），点「测试」验通，再实测一次真转存链路。
- 115：观察 2s 限速下的风控表现；容量接口 get_storage_info 仅 web Cookie 版可用。
- 观察几天百度限速的风控表现。
