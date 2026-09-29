# quark-auto-save 调研报告（面向 PanKeeper 后端）

> 调研基线：main 分支 commit `1c35e96`，Python，3050 stars，**AGPL-3.0**。本地核对全文的文件：`quark_auto_save.py`（主逻辑+夸克API）、`app/run.py`（WebUI+调度）、`notify.py`、`quark_config.json`、`plugins/*`、`app/sdk/{cloudsaver,pansou}.py`、Wiki。

## 1. 项目定位 / 技术栈 / 部署形态

- **定位**：夸克网盘签到、自动转存、命名整理、推送提醒、刷新媒体库一条龙。核心场景是**追更订阅**：定时检查分享链接 → 增量转存新文件 → 按规则重命名 → 通知/触发媒体库。
- **技术栈**：纯 Python，依赖仅 5 个：flask、apscheduler、requests、treelib、natsort。
- **部署形态**：Docker（python:3.13-alpine）Flask 监听 5005；兼容青龙独立 cron 跑。**调度器与执行体分离**：`run.py`（壳）到点 subprocess 拉起 `quark_auto_save.py`（核，无状态批处理），靠 JSON 配置传递全部状态。

## 2. 夸克转存完整链路（文件级，`Quark` 类 350-1080 行）

### 2.1 登录态
- **唯一凭据是 Cookie 字符串**（F12 抓取），多账号数组存配置，**仅第 1 个账号做转存**。
- Cookie 有效性判据：必须含 `__uid`（`verify_account()`），再用 `GET https://pan.quark.cn/account/info?fr=pc&platform=pc` 拿昵称验证。
- **移动端三参数**：Cookie 末尾可附 `;kps=xxx&sign=xxx&vcode=xxx`（手机端抓包任意 `drive-m.quark.cn` 请求可得），`_match_mparam_form_cookie()` 正则抠出存 `self.mparam`。
- 请求头：`cookie` + JSON Content-Type + **PC 客户端 UA**（伪装 Electron）：
  `Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... quark-cloud-drive/3.14.2 Chrome/112.0.5615.165 Electron/24.1.3.8 Safari/537.36 Channel/pckk_other_ch`
- 业务码判据：`response["code"] == 0` 成功；`response["status"] == 200` HTTP 层成功。

### 2.2 分享链接解析 `extract_url()`（纯正则，不走网页）
- `pwd_id`：`/s/(\w+)`；提取码：URL 拼 `?pwd=xxxx`（分享 id 后、`#/list/share` 前）；
- **子目录定位**：URL 锚点 `#/list/share/{32位fid}-目录名/...`，`re.findall(r"/(\w{32})-?([^/]+)?")` 逐级解析 `pdir_fid`，目录名 `*101`→`-` 转义还原。

### 2.3 关键 API 端点清单（base `https://drive-pc.quark.cn`，公共参数 `pr=ucpro&fr=pc`）

| 用途 | 端点 | 关键参数/载荷 |
|---|---|---|
| 取 stoken（兼验证死活） | `POST /1/clouddrive/share/sharepage/token` | `{pwd_id, passcode}` → `data.stoken` |
| 分享清单（分页） | `GET /1/clouddrive/share/sharepage/detail` | `pwd_id, stoken, pdir_fid, _page, _size=50, _sort=file_type:asc,updated_at:desc, _fetch_total=1, ver=2, fetch_share_full_path`；翻页到 `len(list) >= metadata._total` |
| 路径→fid 批量 | `POST /1/clouddrive/file/info/path_list` | `{file_path:[≤50/次], namespace:"0"}` |
| 网盘目录列表 | `GET /1/clouddrive/file/sort` | `pdir_fid, _size=50, _fetch_total=1, fetch_all_file=1,` **`fetch_risk_file_name=1`**（无此参数违规文件名返回 `***`） |
| **转存** | `POST /1/clouddrive/share/sharepage/save` | `{fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken, pdir_fid:"0", scene:"link"}`；query 附 `__dt`(随机1-5分钟毫秒)、`__t` → 返回 `task_id` |
| 转存结果轮询 | `GET /1/clouddrive/task` | `task_id, retry_index, __dt, __t` → `status==2` 完成；`save_as.save_as_top_fids`（**最多 100 个新 fid**） |
| 建目录 | `POST /1/clouddrive/file` | `{pdir_fid:"0", file_name:"", dir_path:"/a/b", dir_init_lock:false}` |
| 重命名 | `POST /1/clouddrive/file/rename` | `{fid, file_name}` |
| 删除（进回收站） | `POST /1/clouddrive/file/delete` | `{action_type:2, filelist:[fid], exclude_fids:[]}`（返回 task_id 需轮询） |
| 回收站 | `GET/POST /1/clouddrive/file/recycle/list|remove` | `{select_mode:2, record_list:[record_id]}` |
| 移动 | `POST /1/clouddrive/file/move` | `{filelist, to_pdir_fid, action_type:1}` |
| 解压 | `POST /1/clouddrive/archive/unarchive` | `{fid, to_pdir_fid, conflict_mode:3, ...}` |
| 下载直链 | `POST /1/clouddrive/file/download` | `{fids}`，**响应 Set-Cookie 也要带** |
| 签到 | `drive-m.quark.cn/1/clouddrive/capacity/growth/info|sign` | 移动端 kps/sign/vcode 走 query |

清单条目字段：`fid`、`share_fid_token`（转存时与 fid 一一对应）、`file_name`、`dir`(bool)、`obj_category`（video/image/...）、`updated_at`。

### 2.4 主流程 `do_save_task` → `dir_check_and_save`
1. 读任务缓存 `shareurl_ban`，已标记失效直接跳过；
2. `extract_url` → `get_stoken`（**兼失效探测**：status 200 正常；500 网络异常跳过本次；**其他 = 分享失效**，message 写 `task["shareurl_ban"]` 持久化并推送，之后不再请求）；
3. 取分享清单；**根目录只有 1 个文件夹自动下钻一层**；
4. `get_fids`/`mkdir` 解析目标目录 fid（进程内缓存 `savepath_fid`，一次运行只解析一次）；
5. `ls_dir` 拉目标目录现有文件名 → 去重（见 2.5）；
6. 正则筛 `need_save_list`，算重命名 `file_name_re`，对重命名后名字二次去重；
7. `save_file` 分批转存（100/批）→ `query_task` 轮询 `status==2` → 收集 `save_as_top_fids`；
8. `treelib` 建文件树，**转存成功后统一 `do_rename`**（save_as_top_fids 与 need_save_list 顺序对齐按索引 rename）；
9. 树非空 → 推送 → 依次调插件 `run(task, account, tree)`。

### 2.5 去重逻辑（纯文件名比对，无本地 DB）
- `is_exists()`：目标目录现有文件名 vs（重命名后）文件名字符串比对；`ignore_extension=true` 忽略扩展名（同剧 mp4/mkv 双源）；
- `{I+}` 序号模式：`{II}` 转成 `\d\d` 正则匹配现有文件名实现"序号通配"判重，`sort_file_list` 保证新文件序号接在已有文件之后（合并 natsort，撞号递增跳过）；
- **没有 fid/哈希级去重**。辅助：`startfid`（从某集开始订阅）、`update_subdir`（递归模式 + 重存模式：删目录→清回收站→整体重转）。

## 3. 风控对策（重点）

思路 =「**伪装真实客户端 + 控制频率 + 失效快速熔断**」，无验证码自动处理（遇到验证码类响应只当失败报错）。

1. **分享链路切换移动端 API（最核心）**：Cookie 带 kps/sign/vcode 时，所有含 `share` 的请求自动 `drive-pc.quark.cn` → `drive-m.quark.cn`，追加约 20 个移动端设备参数（`fr=android, pr=ucpro, device_model=M2011K2C, ve=7.4.5.680, dmn=Mi%2B11, pf=3300, bi=35937, ss=411x875, nt=5, nw=0, kt=4, sv=release, dt=phone, data_from=ucapi, app=clouddrive, kkkk=1`），**同时删掉 Cookie 头**——模拟 App 端免密访问分享，绕开 PC web 对分享接口的严格风控。
2. **伪造行为参数**：转存和轮询接口带 `__dt = int(random.uniform(1,5)*60*1000)`（伪装页面停留 1~5 分钟）+ `__t`（当前毫秒）。
3. **UA 伪装**：夸克 PC 客户端（Electron）UA，非浏览器 UA。
4. **频率控制**：调度频率靠用户自觉（README 红色警告「严禁设定过高的定时运行频率！以免账号风控」），默认 crontab `0 8,18,20 * * *`（一天 3 次）；APScheduler `max_instances=1, coalesce=True` 防堆积。任务内串行，请求间无显式 sleep（仅轮询 0.5s）；**没有全局限速器、没有指数退避**（要补）。批量上限：转存 100/批、路径转 fid 50/批、列表 50/页。
5. **失败处理**：网络异常 → 伪造 500 响应上层按网络异常跳过（不重试，下周期自然重试）；stoken 非 200/500 → `shareurl_ban` **熔断标记 + 推送**，后续周期直接跳过，避免对死链反复请求触发风控；`query_task` 轮询 0.5s/次无上限（TASK_TIMEOUT=1800s 兜底）；Cookie 失效（`token[st invalid,code:50051]`）→ 推送人工换 CK。
6. **违规文件名**：`ls_dir` 带 `fetch_risk_file_name=1` 拿原始名（否则和谐成 `***` 影响判重）。

## 4. 任务调度

`BackgroundScheduler` + `CronTrigger.from_crontab`；job = shell 执行脚本，`max_instances=1`（防重入）、`coalesce=True`（错过合并）、`misfire_grace_time=300`、超时 1800s 强杀；改配置 `remove_all_jobs()` 重建热更新。**账号级并发=无**：所有任务同账号串行 for。任务级周期控制：`enddate`（到期永久跳过）、`runweek`（星期几）。手动执行：`POST /run_script_now` SSE 流式回传 stdout；支持临时 tasklist 不落盘；`quark_test:true` 转存测试分享再删除自检。**无自动重试**——单任务失败只推送，下个 cron 周期全量重跑（按文件名去重天然幂等）。

## 5. 匹配与过滤（MagicRename，最值得抄的机制）

- **魔法匹配 `$XXX`**（pattern 以 `$` 开头且 replace 留空 → 查 `magic_regex` 字典，内置+用户扩展）：
  - `$TV`：`.*?([Ss]\d{1,2})?(?:[第EePpXx\.\-\_\( ]{1,2}|^)(\d{1,3})(?!\d).*?\.(mp4|mkv)` → `\1E\2.\3`（适配 90% 剧集命名）
  - `$BLACK_WORD`：负向前瞻负面词排除（`(?!.*纯享)(?!.*加更)...`）
  - `$TV_MAGIC`（视频后缀宽匹配 + `{TASKNAME}.{SXX}E{E}.{EXT}`）、`$SHOW_MAGIC`（综艺"第x期"）
- **魔法变量 `{XXX}`**（replace 侧）：每组候选正则依次 re.search 取第一命中：`{TASKNAME}`、`{I}/{II}/{III}` 自增序号（位数=I 个数补零）、`{EXT}`、`{CHINESE}`、`{DATE}`（多格式自动补年份→YYYYMMDD）、`{YEAR}`、`{S}`、`{SXX}`（无命中回退 S01）、`{E}`（8 条候选覆盖 S01E02/E02/第02集/02.mp4/孤立数字）、`{PART}`（上中下一二三…带优先级表）、`{VER}`。
- 过滤：`re.search(pattern, file_name)` 命中才转存（空=全部）；排除靠负向前瞻；无效视频过滤（`.mp4/.mkv` 后缀但 `obj_category != video` → 报"无效视频格式"，env `FILTER_INVALID_VIDEO`）。
- 文件夹不重命名；`{I}` 撞号：目录已有文件+候选合并 natsort + 中文优先级表（上一中一下/一~十 → `_00_` 数字）统一排序，尾拼 `updated_at` 稳定 tiebreak。

## 6. 推送（notify.py，青龙同款）

25+ 渠道：Server酱（`sctapi.ftqq.com/{KEY}.send`，兼容 sctp 新版）、Telegram Bot（API 反代+代理）、Bark、钉钉/飞书/企微机器人/应用、pushplus、ntfy、gotify、PushDeer、wxpusher、SMTP、**自定义 Webhook**（URL/Body 里 `$title`/`$content` 占位符）。每函数自判配置非空，`add_notify_function()` 汇总，`send()` 多线程并发全渠道。**推送时机：批次汇总制**——运行中 `add_notify()` 攒 `NOTIFYS` 数组，整轮跑完一次发送。消息类型：✅追更成功（带文件树）、❌分享失效/转存失败/CK 失效、📅签到。

## 7. 持久化与聚合搜索

- 唯一库是 JSON 文件 `quark_config.json`，每轮整体回写。任务级持久化仅两字段：`shareurl_ban`（熔断）、`addition`（任务级插件配置）。**没有事件历史/运行记录**——只有 stdout。
- 聚合搜索 SDK：CloudSaver（`/api/user/login` + `GET /api/search?keyword&lastMessageId` Bearer token 失效自动重登）、PanSou（`GET /api/search?kw=&cloud_types=quark&res=merge&refresh=`）。`/task_suggestions` 3 线程并发查两源 → datetime 倒序 → shareurl 去重。
- STRM 插件：遍历 alist `/api/fs/list` → 视频后缀生成 `{alist}/d{path}?sign=` 写 `.strm`（已存在跳过）；SmartStrm 发 webhook；Emby 插件按任务名 `GET /emby/Items?SearchTerm=` 匹配 Series Id → `POST /emby/Items/{id}/Refresh`。

## 8. 对 PanKeeper 后端的建议

### 模块划分（照 QAS 蒸馏 + 补短板）
```
app/
  quark/        # 夸克客户端 SDK（无业务）：auth(cookie+mparam)、extract_url、get_stoken/
                #   get_detail/save_file/query_task/ls_dir/mkdir/rename/delete...
  matching/     # MagicRename 引擎：魔法匹配表 + 魔法变量表（正则表可按语义重写）
  task_engine/  # 订阅任务执行器：check_and_save 主流程 + 去重 + rename
  scheduler/    # APScheduler cron（常驻进程内跑即可，不必像 QAS 起子进程）
  notifier/     # 先做 4 渠道：Server酱/TG/Bark/ntfy + 通用 webhook
  search/       # CloudSaver/PanSou 聚合（并发+去重逻辑照 run.py）
  strm/         # alist/本地目录 → .strm；emby/飞牛刷新
storage: SQLite # QAS 用 JSON，PanKeeper 必须 DB 化
```

### 机制照搬清单（按价值排序）
1. **转存链路全套参数**（§2.3 表可直接当接口文档）。
2. **风控三件套**：`__dt`/`__t` 伪造参数、移动端分享 API（kps/sign/vcode + 设备参数）、`fetch_risk_file_name=1`。
3. **shareurl_ban 熔断**：stoken 失败即标记+推送+跳过，防对死链反复请求。
4. **100/批转存分批 + 0.5s 任务轮询**（`save_as_top_fids` 上限 100 是硬约束）。
5. **文件名去重 + ignore_extension**：无 DB 依赖、幂等，作为转存前最后防线。
6. MagicRename 的 `$TV`/魔法变量正则表 + `{I}` 撞号处理。
7. 插件钩子设计：`task_before / run(task, tree) / task_after` 三钩子 + 全局/任务级两级配置——PanKeeper 的 QMS/STRM/Emby 正好挂 run 钩子（`tree` 里 fid/path/重命名后文件名全齐）。
8. 单账号串行 + 一天 2-3 次保守默认调度 + `max_instances=1`。

### QAS 没做、PanKeeper 应补的
- **全局限速器**（令牌桶，对 drive-pc 1-2 req/s）+ **429/5xx 指数退避**——QAS 完全没有。
- **SQLite 表结构建议**：`accounts(cookie, kps, sign, vcode, nickname, is_active, last_check_at)`；`subscriptions(id, name, pwd_id, passcode, pdir_fid, raw_url, savepath_fid, pattern, replace, update_subdir, ignore_extension, runweek, enddate, ban_reason, enabled, created_at)`（shareurl 存解析结果免每次正则）；`run_history(id, subscription_id, started/finished, status, new_files, message)`（QAS 缺失的事件历史）；`saved_files(id, subscription_id, fid, file_name, file_name_re, size, saved_at)`（转存产物，供刮削/STRM 增量消费与审计）；`push_log(channel, title, content, result, sent_at)`。
- Cookie 指纹监控：定期 `account/info` 校验 + 失效推送，加 `code:50051` 识别自动标记 CK 过期。

## 9. License（重要结论）

**AGPL-3.0**（README 明确强调传染性 + 网络服务也需开源）。家用不分发不对外提供服务 → AGPL 义务不触发；但只要分发给任何人或作为服务开放，就必须整包开源。建议：
1. **API 端点/参数/机制不受版权保护，放心照抄**（§2.3 表格即干净版本）；
2. 正则常量表属事实性短文本，抄了无实际风险，稳妥做法是照语义重写；
3. 避免整文件复制 `quark_auto_save.py`/`run.py`——按模块划分重写，逻辑一致但代码表达自己的。
**结论：机制照抄、代码重写。**
