# bdsavepro 调研报告（面向 PanKeeper 后端开发）

**仓库**：https://github.com/xinyuLo/bdsavepro（main，v2.0.1，非 GitHub fork，是 kokojacket/baidu-autosave 的二次开发衍生版）。**License：AGPL-3.0**（上游 README 写 MIT 但 LICENSE 文件是 AGPL，bdsavepro NOTICE 已澄清）。

## 1. 项目定位 / 技术栈 / 入口

- 单管理员、Docker 部署的 Web 服务（端口 5000），定位与 PanKeeper 高度重叠（定时转存+通知+QMS 联动），但没有聚合搜索、没有 STRM/Emby 之外的媒体链路。
- **Python 3.10 + Flask 2.3.3 + gevent WSGIServer + APScheduler 3.10.4 + loguru**；前端 Vue3+Vite+Element Plus 构建产物拷进 `static/`。核心依赖 **`baidupcs-py==0.7.6`**（PeterDing/BaiduPCS-Py，2021 后基本停更，拖 eventlet/Cython）。
- 后端仅 7 个文件（约 8700 行）：`storage.py`(2756 转存核心)、`web_app.py`(2859 路由+SSE)、`scheduler.py`(1122 定时)、`qms_client.py`(659)、`notify.py`(1041 通知)、`history_db.py`(247 SQLite)、`utils.py`。
- **存储划分（重要教训）**：任务/账号/配置在 `config/config.json`（JSON 全量读写）；执行历史、QMS 配置/日志在 `config/history.db`（SQLite WAL）。作者明确注释：config.json 每次转存被进度回写上百次、多写入者互相覆盖，历史和 QMS 配置放里面会丢数据——**PanKeeper 必须全部 DB 化**。

## 2. 百度转存完整链路（文件级）

**关键结论：storage.py 不直接发百度 HTTP 请求，全部委托 `baidupcs_py.baidupcs.BaiduPCSApi`（v0.7.6）**。主流程 `BaiduStorage.transfer_share()`（storage.py:1198）：

1. **每次执行新建临时客户端** `BaiduPCSApi(cookies=…)`（不用全局 client）。
2. **URL 规整**（:680-698）：去掉 `#` 后缀；`/share/init?surl=xxx` → `https://pan.baidu.com/s/xxx`；正则白名单 `^https?://pan\.baidu\.com/s/[a-zA-Z0-9_-]+(\?pwd=[a-zA-Z0-9]+)?$`。
3. **访问分享**：`access_shared(url, pwd)` → `POST https://pan.baidu.com/share/verify`，params `surl, t=毫秒, channel=chunlei, web=1, bdstoken=null, clienttype=0`，data `pwd`；返回 cookie（BDCLND 等）合入 session。
4. **分享清单**：`shared_paths(url)` → GET 分享页 HTML 正则抓 `yunData.setData(...)` JSON，取 `shareid`、`uk`、`bdstoken`、`file_list`（**md5 字段百度直接返回，无额外请求**）。子目录递归 `GET pan.baidu.com/share/list`，params `page/num=100/dir/t/uk/shareid/order=other/desc=1`（storage.py:2195）；文件路径剥掉 `/sharelink\d*-\d+` 前缀。
5. **本地对比**：`list_local_files()`（:2088）递归扫描**对比目录**（compare_path 留空用 save_dir）：优先 `GET pan.baidu.com/api/list?dir&page&num=100&order=name`（为绕过 PCS 对部分路径报 31023），失败回退 PCS `list`；收集 `{文件名集合, md5集合}`。
6. **去重与决策**（:1417-1500）实际顺序：
   - ① 正则过滤（regex_pattern 不命中=过滤；regex_replace 产生重命名目标）
   - ② **先 MD5**：分享文件 md5 ∈ 对比目录 md5 集合 → 跳过（解决改名后重复转存）
   - ③ **后文件名**（只比 basename）：原名或重命名后名已存在 → 跳过；"原文名存在、新名不存在" → `rename_only_list`（只重命名不转存）
   - 注意 README 写"先文件名再 MD5"，**代码实际 MD5 在前**，以代码为准。
7. **建目录**：`_ensure_dir_tree_exists` 逐级 `makedir`，处理 31061/12（已存在=成功）、31062（文件名非法）、-9/31066（不存在→创建）、31023（不确定 → sleep 0.2s 后父目录枚举兜底确认，:816-938）。
8. **执行转存**：按目标目录分组，`POST pan.baidu.com/share/transfer`（不是新版 share/create，无 sign 计算），params `shareid, from=uk, bdstoken, channel=chunlei, clienttype=0, web=1, app_id=250528`，data `fsidlist=[fs_id...]（JSON数组）, path=目标目录`，**Referer 必须是分享链接**；`info[0].errno` 非零转异常。目录组之间 `sleep 1`。
9. **重命名**：转存成功后逐个 rename，失败收集末尾批量重试一轮。
10. 返回 `{success, message|error, skipped, transferred_files[]}`。

**错误码与重试**：
- 装饰器 `api_retry`（:25）：max_retries=1、随机延迟 2-3s；**排除不重试码 `[-6, 115, 145, 200025, -9]`**。
- **-65（频率限制）→ sleep 10 秒整组重试一次**（:1673-1704）。
- `_parse_share_error`（:1070）：115→"分享已失效（文件禁止分享）"、145→"分享链接已失效"、200025→"提取码输入错误"。
- error_code 4 → 显示"转存错误"。
- 完整错误码表（baidupcs-py errors.py）：-6 重新登录、-7 分享已删除、-8 分享过期、-9 文件不存在或提取码错误、-12 访问密码错误、-30/-12/12/31061 文件已存在、-31 保存失败、-32 空间不足、-33 单次 999 个上限、-62/-19 需验证码、-70 病毒文件、113 签名错误、115 禁止分享、130 转存数超限、132 账号安全风险、31041/31042 cookie 非法/未登录。
- 任务状态机：`pending/running/normal/error`（+skipped）。

## 3. 凭据管理

- **BDUSS + STOKEN**：用户从浏览器 F12 复制整串 cookies，`add_user_from_cookies` 解析 → `_validate_cookies` 只校验 BDUSS/STOKEN 存在 → 临时 `BaiduPCSApi.user_info()`（手机端 tieba 接口，`sign=md5(排序参数串+'tiebaclient!!!')`）验证可用。
- 多账号：`config.json → baidu.users = {用户名: {cookies明文, name, user_id}}` + `current_user`；**同一时刻只有当前账号生效**。
- 失效发现：被动——抛 -6/-4/-11/31041/31042 映射"身份验证失败"，任务标 error；**没有定时探活**。
- 日志 `filter_sensitive_info` 遮蔽 BDUSS；`GET /api/user/<name>/cookies` 登录后可回显明文。

## 4. QMS 联动（`qms_client.py`，与 PanKeeper 原型流程一致）

**触发点**：定时执行（scheduler.py:584）和手动执行（web_app.py:889），都在"执行历史落库后"调 `trigger_after_transfer(storage, task, transferred_count)`。**transferred_count=0 不触发**。

**常量**：`TRIGGER_DELAY_SECONDS=10`、`STRM_DELAY_SECONDS=10`、`SCRAPE_WAIT_TIMEOUT=300`、`SCRAPE_POLL_INTERVAL=5`、QMS 默认端口 12333、HTTP 超时 15s。

**`trigger_after_transfer` 实现**（qms_client.py:612）：
1. 校验 `enabled` + `auto_trigger`；`match_links_for_task` 按 `task_uid → order → 任务名` 三级匹配连接列表（连接模型 `{id(8位hex), task_uid, task_order, task_name, qms_id, qms_path, qms_media_type, strm_id, strm_path, enabled}`，存 SQLite `app_kv` 表）。
2. 起**守护线程** sleep 10s → 逐连接 `trigger_link`：`POST /api/scrape/pathes/start {"id": qms_id}` → 写 `qms_trigger_log`（每连接保留 30 条）→ 触发成功且开了 `watch_result` 再起后台线程 `_follow_up`。
3. **轮询**（`wait_scrape_result`，:409）：每 5s `GET /api/scrape/pathes/{id}`，**完成判定 = `is_running==false && is_scraping==false && updated_at >= 触发时刻`**；全部完成或 300s 超时退出。完成后 `GET /api/scrape/records?page=1&page_size=100` 按 `source_full_path` 前缀 + 时间戳统计本次逐文件成功（`status='renamed'`）/失败（`failed_reason` 非空）。
4. **失败路径**：触发失败只写日志不重试；轮询超时记"未确认"，**不触发 STRM**；success=0（QMS 对在库文件直接跳过无新记录）→ **不触发 STRM**；刮削成功且有新文件 → sleep 10s → `POST /api/sync/path/start {"id": strm_id}`。

**QMS API 清单**（鉴权：`X-API-Key` 头；或 `POST /api/login {username,password,rememberMe}` 取 `csrf_token` 加 `X-CSRF-Token`，session 缓存 1800s，401 自动重登一次）：
- `GET /api/user/info` 连接测试
- `GET /api/scrape/pathes` 刮削任务列表；`POST /api/scrape/pathes/start {"id":N}`；`GET /api/scrape/pathes/{id}` 运行状态
- `GET /api/scrape/records` 逐文件刮削记录
- `GET /api/sync/path-list`（id/remote_path/base_cid/local_path/is_running/last_sync_at）；`POST /api/sync/path/start {"id":N}`

## 5. 限流 / 风控对策

- 有效措施：api_retry 随机 2-3s；-65 → sleep 10s；目录组转存间 sleep 1s；重命名间隔默认 0.5s（重试翻倍）；APScheduler `max_workers=1` + 全局 `_execution_lock`。
- **bug**：`storage.py:1930 _wait_for_rate_limit` 引用不存在的 `self.min_request_time`（真名 `min_request_interval`）且无人调用——**全局 2 秒节流实际没生效**，只靠零散 sleep。
- 无预防性分批：fsidlist 一次整组提交（-33 单次 999 上限只靠报错暴露）。
- **并发隐患**：手动执行不拿 `_execution_lock`，手动+定时可能同时转存。

## 6. 缓存

仅两处：① 用户信息 30s TTL 内存缓存；② 分享文件清单 `_SHARE_FILES_CACHE`（web_app.py:2101，url→files，最多 20 条按时间淘汰，重启清空，只服务排除文件弹窗，`refresh=1` 强刷）。**目录树、本地网盘清单一律不缓存**，每次执行全量递归拉分享清单 + 全量递归扫描对比目录——大目录慢且增加风控暴露。

## 7. 对 PanKeeper 后端的可复用建议

- **可近乎原样搬语义**（注意 AGPL 传染）：`qms_client.py` 的 `trigger_after_transfer` 骨架（延迟/轮询/完成判定/"无新文件不触发 STRM"）；`storage.py` 的去重判定（正则→MD5→文件名→rename_only）、`_ensure_dir_tree_exists`/31023 兜底、`_parse_share_error` 文案、`api_retry` 排除码表；`history_db.py` 的 SQLite WAL + 单写锁 + 按 task 保留 N 条。
- **依赖决策**：a) 沿用 `baidupcs-py==0.7.6`（停更、拖 eventlet/Cython、上层代码质量一般）；b) **自写百度客户端**——转存场景只需 5 个接口：`POST /share/verify`、GET 分享页抓 yunData、`GET /share/list`、`POST /share/transfer`、`GET /api/list`。本报告端点/参数/头均为 v0.7.6 实测内容。
- **表结构建议**（SQLite）：`accounts(id, name, cookies_json, status, checked_at)`；`tasks(uid, share_url, pwd, save_dir, compare_dir, transfer_folders_json, exclude_files_json, regex_pattern, regex_replace, cron, enabled, status, message, last_run_at)`；`run_history(id, task_uid, start/end, success, message, total, excluded, filtered, md5_skipped, to_transfer, transferred_json, logs_json)`（保留 10 条/任务）；`qms_links(link_id, task_uid, qms_id, qms_path, strm_id, strm_path, enabled)`；`qms_logs(...)`（30 条/连接）；`kv(key,value)`。
- **要重写/修复的**：任务/进度高频回写 JSON；手动/定时并发互斥；补真正的全局请求节流；失效任务别直接删（标记+通知）。

## 8. License

**AGPL-3.0**——直接复制 storage.py/qms_client.py 代码会使 PanKeeper 成为衍生作品需整体开源；**按本文档的 API 端点/参数/错误码自行实现**（接口事实不受版权保护），不要抄它的中文注释与代码结构。家用不分发实际风险低，但按干净方式做没有成本。
