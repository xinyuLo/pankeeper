# 2026-10-04 PanKeeper 工作日志：自动转存六连修 → QMS 真实结果回填 → 搜索转存带壳

> 时段：10-03 深夜 ~ 10-04 凌晨 02:30。横跨 31 文件 +953/-190（见当次 commit）。
> 主线：把"触发成功"冒充"刮削成功"的显示链全部换成真实结果回填；搜索转存补上根文件夹。

## 一、自动转存六连修（10-03 深夜批次）

1. **立即执行去假存真**：RunModal 原来是 `buildPaRunSeq` 纯前端演戏；重写为真实模式——`POST /pa/tasks/{id}/run` 入队（与 cron 同链路）→ 1.5s 轮询 `/queue/state` 按 `paTaskId` 对上队列项，进度/日志/统计全真数据；去重/熔断的 `queued=false + reason` 如实展示；catch 不再吞错误（`errText()` 优先吐后端 detail）。
2. **QMS/STRM 结果落 RunHistory**：新列 `qms_json/strm_json`（db.py 迁移自动补列）；区分「未配置」「未执行（无新增）」「已触发」。
3. **详情执行结果三段**：`转存tag · QMS tag · STRM tag`。
4. **转存日志弹窗**：说明按成败染色。
5. **转存历史页**：开始/结束时间拆两列；统计改彩色 tag（新增>0 绿 / 失败>0 红 / 其余中性）；列宽重配。
6. **自动转存表格**：`table-layout: fixed→auto`（修分享链接列独吞宽度）；操作列表头左对齐。

另：设置页/队列配置页的灰字注释按截图逐条删除。

## 二、基础环境两坑（真实 bug，非玄学）

- **vite 代理 ECONNRESET → 前端 500**：uvicorn 默认 5s 关空闲 keep-alive 连接，Node 19+ 的 globalAgent 默认 keepAlive 复用死连接。修：`vite.config.ts` 的 /api 代理指定 `keepAlive: false` agent（复现坐实：修复前闲置 7s 后必 000，修复后 0 次 ECONNRESET）。交接文档旧结论（win32 事件循环）是真修复但治的不是这个病。
- **服务反复掉线根因**：进程挂 WorkBuddy 会话下被空闲回收（四种姿势全试无效）。交付 `start-dev.bat` / `stop-dev.bat`（全 ASCII 路径），用户双击即脱离会话独立运行。

## 三、QMS 真实结果回填（核心链路改造）

- **新增 `services/run_watch.py`**：转存收尾挂后台线程，按本次转存文件名轮询 QMS 记录到终态（30s/次，上限 30 分钟；一条记录没有则 10 分钟收工），把汇总回填 `run_history.qms_json`。
- **「触发成功」全部改口**：`trigger_scrape` 受理只写「已触发」（灰），真实结果由回填替换——自动转存（auto.py）与搜索转存（manual.py）统一口径（用户实锤：夸克搜索转存 4 文件全 scrape_failed，这边还显示"成功"）。
- **整单结果 `overall_of`**：转存成功 + QMS 有失败 = **部分失败**（橙）。任务行/转存日志/转存历史三处统一，别再看 `status` 报"成功"。
- **MD5 去重明细落库**：`run_history.md5_skipped_json`（详情弹窗新增一段，紫色）——"哪集被 MD5 滤掉"可追溯。
- **⚠️ MD5 自动回写排除清单已取消**（用户发现"40.4k.mp4 被偷偷加进排除清单"）：任务不该偷偷改自己配置；手动重刷时的指纹基线（`record_fingerprint` + `_is_fresh`）解决"QMS 按文件去重没真重刮、旧记录被当新结果"的坑。
- **推送信息条补 STRM 结果**：`run_watch._STRM_RESULTS` 登记 + `media_push._wait_strm` 等待后显示「✅ STRM 已生成 / ❌ 失败 / ⏳ 未确认」（此前自动转存 `strm_ok` 恒 None 永不显示）。
- **Server酱排查**：SendKey 曾是占位符 `SCT12345`（用户已换真 Key，test 通过）；`notify` 错误信息带上 Server酱原始 message（`HTTP 400 · [AUTH]错误的Key`）；`media_push` 按 name 筛 QMS 记录会漏（41.4k.mp4 实锤）→ 改拉 500 条按文件名匹配。
- **自愈复查加了又删**：曾实现每 3 分钟扫 24h 未成功运行自动翻正，用户明确不要后端全天候轮询 → 整段删除（106 行）。QMS 晚成功不再自动翻正，要翻正点「重新触发 QMS」。

## 四、重新触发 QMS（手动补救链路，2026-10-04 定稿）

门禁（只有最新运行 QMS 快照 `t-bad` 才可点）→ `DELETE /api/scrape/records?ids=` 清本次文件失败记录 → `POST /api/scrape/pathes/start` 重刮 → **STRM 真等刮完**（轮询 `GET /api/scrape/pathes/{id}`，判据=曾亲眼见忙→不忙；`updated_at` 不随刮削前进、跨机时钟差需 60s 容差——两个坑都是实测/桩测抓的）→ 挂回填，重刷成功自动改判成功。

## 五、联动目标解析（两层配置统一）

- 病根：任务弹窗配的 `qms_id/strm_id` 存了 `pa_tasks` 却从没被读过（执行侧只按 save_dir 匹配 `dd_items`），用户任务 1 配的 `strm_id=5` 被无视。
- 新增 `auto.resolve_media_link(path, task_id)`：**转存配置目录开关是总闸**（`qms_on=false` → 一律未配，用户定稿"目录关了等于没配"）；总闸开着时任务级配置优先、字段各自回退目录级。前端 TaskModal 打开时自动清空失效选择（目录关了/目录已从 QMS 消失）。

## 六、UI 打磨

- 执行监控弹窗：百分比进度条（假进度）→ **不定量流动 loading 条**，完成变实心绿。
- STRM 快照「已触发」改绿色（含存量数据订正）；QMS「已触发」保持灰（会被回填替换）。
- 队列浮标/看板只显示手动任务（`isAutoQueued` 判据用 `paTaskId`，跨重启稳定）；自动转存走自己的页面。

## 七、搜索转存带壳 + 文件夹更名（02:15）

- 需求："搜索转存得带着根文件夹一块过来，填了文件夹更名就改根文件夹名"——记录页显示的就是根文件夹名。
- 病根：`list_share` 单壳自动下钻剥壳 + `save_files` 只转文件（目录目标侧重建）。
- 新链路：`TaskSpec.with_shell / folder_rename`；单壳时记 `_root_shell`，`save_files` 整壳直接转（百度 fsidlist=[壳fsid] / 夸克 fid_list=[壳fid]），更名走 `rename_dir`；去重口径 = 目标已有同名文件夹 → 整壳跳过。前端 QuickTransferModal 传 `rename/with_shell`；**自动任务行为完全不变**。
- 桩测四例全过，抓出两个真 bug（`_save_with_shell` 漏 `on_progress`；baidu `_transfer_group` 漏传 `ctx`）。
- 搜索转存 QMS 回填：`run_watch.watch_qms` 加 `table` 参数（RunHistory/Record 双表）；存量 record 27 已按 QMS 真实状态订正为「失败 4 项」。

## 验证与部署

- pytest 39 passed、vue-tsc 零错、后端实测（详情接口 qms/strm 直出、门禁、总闸、迁移列全验证）。
- 本批 commit 后需 NAS 重新部署：pankeeper-deploy pull → `/vol1/1001/compose/pankeeper` 下 `docker compose up -d`。
