# 2026-10-10 PanKeeper STRM 可选拉通、TMDB 重试、地址清洗与 QMS/STRM 反转

本批次五项调整，全部源于朋友侧部署暴露的真实问题（pansou 401 / QMS 联动开关 / STRM 不触发）与本机复测。

## 1. 转存配置 & 转存弹窗恢复 STRM 选择（不填自动配对）

**背景**：2026-10-04 定稿"STRM 跟随 QMS 自动配对（刮削整理目标根 = 同步路径来源）"后，朋友侧
配对不上（来源路径选了待整理目录而非整理目标）时 STRM **静默跳过**——执行链 `if link.get("strm_id")`
直接不挂线程，日志/UI 双无感，排查只能读代码。

**改动**：
- 后端 `dd.py`：`DdBody` 恢复 `strm_id` 字段写入（create/update 放行，列一直在）；
- 转存配置弹窗（DefaultDir）加「STRM 同步路径」下拉（可清空，编辑回显）；
- 普通转存弹窗（TransferModal）同款下拉，入队透传 `strm_id`（EnqueueBody → engine.strmId → manual）；
- 解析优先级统一为：**任务/弹窗显式选的 > 目录配置的 > 自动配对兜底**（`resolve_media_link` /
  `manual._media_chain`，执行侧原优先级链本就如此，只是写入端曾被掐死）。

## 2. TMDB 识别偶发失败 → `_get` 一次退避重试

**现象**：识别"偶尔慢、偶尔失败"。实测证据：本机走 clash 代理首查 2460ms（缓存 93ms）成功；
TMDB **直连**被墙重置（"远程主机强迫关闭连接"）——整条链路吊在单一代理上。

**根因**：4 路并行（10-06 提速）只治"串行叠加慢"，不治"代理链路抖动"。proxy 模式 `_build_clients`
只产**一个候选**，`_get` 里 TransportError 换下一个候选=无处可换，且 429/5xx 非 200 一律直接 None，
零重试。代理抖一下 → 4 路全空 → 候选池空 → "未识别到 TMDB 条目"。第一波全空还会触发粘连名
变体第二波，最坏 2×15s 等完仍失败。

**修法**：`tmdb._get` 对 TransportError / 429 / 500/502/503/504 做 **0.5s 退避后原地重试一次**，
仍失败再走候选轮换/放弃。401/404 等换候选也没用的错误不重试。方案二（候选池整体重搜）评估后
不做：重试已覆盖瞬态抖动，池级重搜只会把最坏耗时再翻倍。

## 3. 设置页三地址清洗（复制整链路报错的根治）

**现象**：从浏览器复制地址容易把整条链路带进来（实锤：`http://ip:8888/api/search?kw=功夫女足&res=merge&cloud_types=...`），
后端拼 `/api/search` 就成了 `...?kw=x/api/search`，直接报错。

**修法**：`settings_svc.save_group` 保存时对 `search.pansou_url` / `qms.url` / `qms.tmdb_proxy`
统一清洗成 `scheme://host[:port]`：剥路径/参数/锚点，没写协议补 `http://`，IPv6 保留方括号，
socks5 代理不误伤，解析失败原样放行（不把能用的配置改坏）。清洗发生在保存时——库里已存坏的
地址重新保存一次即修复。实测 8 种输入（含上述完整链接）全部正确。

## 4. QMS/STRM 反转（队列配置全局开关，本批主体）

**需求**：转存完成后**先生成 STRM、再触发 QMS 刮削**（默认是 QMS 刮完等 `trigger_strm_after_scrape`
再生成 STRM）。开关放**队列配置页**一处全局，不散落各弹窗。

**改动**：
- 存储：`queue_cfg.reverse`（settings JSON，默认 false），`PUT /queue/config` 收 bool；前端 QueueCfg
  类型 + CFG_DEF 同步；
- 执行侧 `manual._media_chain` / `auto._media_chain`：反转时 转存→等 `qms` 秒→**触发 STRM**（快照
  "已触发（反转·先行）"）→等 `strm` 秒→触发 QMS 刮削；`_finish/_sync_pa_task` 落库后的
  `trigger_strm_after_scrape` 用 `t["_media_reverse"]` 门控跳过（反转时 STRM 已触发过）；
- 日志对齐：反转分支全程 STEP/INFO 带"（反转）"前缀，快照如实；watch_qms 结果回填不受影响；
- 搜索历史/转存记录页「手动触发」弹窗：QMS+STRM 都选时顺序对偶翻转（STRM 先行，15s 后 QMS），
  弹窗提示文案随开关切换（打开弹窗时 `pkQueueCfgFetch` 现拉最新配置）；
- **必选校验**：反转开启时三处拦——转存配置保存、转存弹窗提交、自动任务保存，STRM 未选报错
  （文案指路队列配置可关闭）；执行侧另有兜底：真遇到没选的（老任务）记 WARN 退回默认顺序，
  不把联动弄丢。

**语义提醒（已知边界）**：反转模式 STRM 扫的是**转存原目录**（文件尚未被 QMS 整理改名），刮削
搬走文件后这些 strm 不会自动更新——这正是"反转必须显式选 STRM"的产品含义，使用者须知。

## 验证

- 逻辑测试（桩替换 qms.trigger_*，零真实调用）5/5：manual/auto × 反转=STRM→QMS、默认=QMS→后台
  等刮削日志在、反转无 STRM 兜底 WARN 退默认（manual/auto 各一）；
- `PUT /queue/config reverse=true→GET true→false` round-trip OK；
- 后端 py_compile 全过、单测 16 过 1 挂（pansou magnet 旧断言，10-06 放行 magnet 时遗留，与本批无关）；
- vue-tsc 零错误；生产构建产物 grep "反转"/"QMS/STRM" 确认新 chunk 生效；
- 本地 8000 单端口（后端托管 web/）联调通过；注：本机 vite 代理层偶发 ECONNRESET（约 1/3，
  后端日志全 200），与 10-04 noKeepAliveAgent 修复结论不符，未深究——本地联调走单端口模式绕开。

## 遗留

- vite dev 代理抖动根因（uvicorn timeout-keep-alive / http-proxy 参数）待查，只影响本地开发模式；
- pansou magnet 旧断言待更新（test_unit.py test_map_merged_types_and_drop）；
- 快速转存弹窗无 STRM 选择（按目录配置走），反转时若目录没配 STRM 走执行侧 WARN 兜底。
