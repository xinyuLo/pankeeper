# PanKeeper 后端接口需求文档

> 给 Python 后端开发用。前端（PanKeeper-vue3）已按本文档的契约实现并全部走 mock；
> 后端按本文档实现接口后，前端把 `.env` 里 `VITE_USE_MOCK` 改为 `false` 即完成对接，页面代码不动。
> 字段定义以 `src/types/model.ts` 为准（对齐原型 docs/02-data-model.md），本文档给的是接口形状与行为要求。

## 0. 通用约定

| 项 | 约定 |
| --- | --- |
| Base URL | `/api`（开发期由 vite 代理到后端，`vite.config.ts` 的 `server.proxy`，后端起来改 target 即可，无 CORS 问题） |
| 认证 | 登录换 JWT，之后所有请求带 `Authorization: Bearer <token>`（前端拦截器已加）；单管理员，不开放注册 |
| 响应格式 | **直接返回业务 JSON，不包 `{code,message,data}` 壳**——前端 axios 拦截器把响应体原样交给页面 |
| 错误语义 | 用 HTTP 状态码：400 参数错误 / 401 未登录或 token 过期 / 404 不存在 / 500 服务端错误。错误信息放 body `{"message": "..."}`（前端目前只特殊处理 401 → 跳登录页） |
| 时间 | 两种并存：**快照展示字符串** `MM-DD HH:mm`（记录列表/上次执行，照 mock 现状）；**毫秒时间戳**（队列/缓存等计算用）。字段名带 `ts` 的是时间戳，`tm`/`last_run`/`last_check` 是字符串 |
| 数据持久 | mock 里只有队列状态（`pkq_v2`）和队列配置（`pkq_cfg`）存 localStorage，其余是内存态；后端全部落库。建议表见 §1 |

建议数据表：`pa_task`（自动任务，含扩展字段，见 §4.4）、`dd_item`（转存配置目录）、`record`（转存记录快照）、`account`（网盘凭据，加密）、`qms_path` / `strm_path`（刮削/STRM 目录字典）、`queue_task`（队列任务）、`queue_config`（单行）、`settings`（KV 或分组 JSON）、`cache_tree`（目录树缓存元数据）。

---

## 1. 认证

### POST /api/auth/login
```jsonc
// 请求
{ "username": "admin", "password": "..." }
// 200
{ "token": "<jwt>", "username": "admin" }
```
- 会话有效期支持配置（见 §6 security：1/7/30 天）。
- 前端 mock 任意输入可登录；真实版校验失败返回 401 + `{"message":"用户名或密码错误"}`。

### PUT /api/settings/security
改密码（见 §6）；改密成功后应让旧 token 失效。

---

## 2. 转存配置（默认目录，快速转存的依据）

每个网盘每账号维护一组「别名 → 路径」配置；`is_default` 约束 **每账号唯一**（后端必须强制）。

### GET /api/dd/items
```jsonc
// 200: DdItem[]
{ "id": 1, "type": "baidu", "account": "bd_main", "sort": 1, "name": "电视剧",
  "path": "/影视/国产剧", "is_default": true, "qms_on": true, "qms_id": 1, "strm_id": 1 }
```

### GET /api/dd/default?type={type}
返回该网盘**排序最前的默认项**（多账号各有一个默认，取 sort 最小的）；没配过返回 `null`。

### GET /api/dd/has-config?type={type}
`true/false`——搜索页「快速转存」按钮的可用依据。

### GET /api/dd/items/:id · POST /api/dd/items · PUT /api/dd/items/:id · DELETE /api/dd/items/:id
保存时校验：同账号下重名拒绝（400 + message）；同账号唯一默认。

### PUT /api/dd/items/:id/default
设为该（网盘,账号）下的唯一默认，其余自动取消。

### GET /api/qms/paths · GET /api/strm/paths
QMS 刮削目录 / STRM 同步目录字典（转存配置联动 + 记录页手动触发的下拉候选）：
```jsonc
// QmsPath[]
{ "id": 1, "media_type": "tv", "source_path": "/vol1/1001/media/电视剧" }
// StrmPath[]
{ "id": 1, "remote_path": "/媒体/剧集" }
```

---

## 3. 搜索（PanSou 代理）

**前端不直连 PanSou**，后端转发（避免 TG 频道配置与地址暴露在浏览器）。

### GET /api/search/results?kw={keyword}
聚合检索（后端代理 pansou，实测本例的 pansou-web 镜像必须走 GET），返回 `SearchResultItem[]`：
```jsonc
{ "n": "庆余年.第二季.4K.HDR.国粤双语", "t": "baidu", "s": "—", "d": "2026-09-01", "ok": true, "hot": false,
  "url": "https://pan.baidu.com/s/1xxxx", "share_code": "8888", "source": "tg:xxx" }
// t: baidu|quark|115|123|ali|xunlei|uc（pansou 的 aliyun→ali 映射；magnet/ed2k/天翼等不支持转存的类型后端直接丢弃）
// s: pansou 不提供大小，展示 —；ok=是否有该网盘凭据；url/share_code 供入队真实转存
```
- `d`（分享时间）保留 PanSou 原文即可。
- 支持结果缓存（见 §6 搜索源设置：开启·30 分钟）。

### GET /api/search/results?kw=xxx&cached=1
返回**上次检索的缓存结果**（首屏直出，不走检索动效）；无缓存返回 `[]`。

### GET /api/search/channels
搜索源频道清单 `[{ "name": "影视资源共享", "on": true }, ...]`（设置里的频道勾选）。

### GET /api/search/pansou-addr
当前 PanSou 地址（页面展示用）。

---

## 4. 转存队列（⚠️ 重点：现状是前端模拟，后端要接管）

### 现状与接管方案
目前队列引擎在前端（`src/queue/engine.ts`），600ms tick 模拟，状态存 localStorage `pkq_v2`、配置 `pkq_cfg`。
后端就绪后**队列执行全部搬后端**（网盘转存/QMS/STRM 真实执行必须后端做），前端只保留看板渲染。两种对接形态：

- **方案 A（推荐）：后端真实队列 + 前端轮询/SSE。** 前端每 600ms 拉 `GET /api/queue/state`（或 SSE 推送），渲染逻辑不变。
- 方案 B：短期内后端只做「收单 + 转存执行」，QMS/STRM 延迟阶段也后端做，前端引擎下线。

**阶段机契约（照抄，勿改）**：`transfer → waitqms → qms → waitstrm → strm → done`，全链占一个线程位；
节奏参数来自队列配置（改完即时生效，**只影响之后的任务**，执行中的不受影响）；
已完成任务在队列里保留 30 分钟后出队（历史落转存记录表）。

### POST /api/queue/tasks（入队，所有转存动作的唯一入口）
```jsonc
// 请求（⚠️ 真实转存必须带 share_url/share_code——mock 时代不需要，前端搜索入队要补上）
{ "name": "庆余年.第二季.4K", "type": "quark", "path": "/影视/国产剧",
  "files": 36, "size": "82.4 GB",
  "share_url": "https://pan.quark.cn/s/xxxx", "share_code": "ab12",
  "include_subdirs": true }
// 200
{ "id": 43, "pos": 1 }   // pos = 当前第几位（wait+run 计数），前端 toast 用
```
搜索结果行相应扩展：`GET /api/search/results` 返回的每行含 `url` / `share_code` / `source`（真实转存的数据源）。

### GET /api/queue/state
全量状态（前端 600ms 轮询或 SSE），形状 = 前端 `queueView`：
```jsonc
{ "seq": 43, "lastTick": 1695880000000, "lastDone": 1695879990000,
  "tasks": [{ "id": 42, "name": "…", "type": "baidu", "path": "/影视/国产剧",
    "files": 36, "size": "82.4 GB", "status": "run", "phase": "transfer",
    "phaseStart": 1695880000000, "progress": 68, "doneAt": 0,
    "logs": [{ "lv": "STEP", "txt": "正在转存 24/36 …" }] }] }
// status: wait|run|done|fail；logs 保留最近 40 行；progress 0-100
```

### GET /api/queue/config · PUT /api/queue/config
```jsonc
{ "threads": 1, "gap": 5, "qms": 10, "strm": 10 }
// threads 并行上限(1-4)；gap 任务间隔秒（上个任务完全结束后计时）；qms/strm 阶段延迟秒
```
提新任务的入场条件：空闲线程 > 0 **且** 距 lastDone ≥ gap 秒。

---

## 5. 自动转存任务

### GET /api/pa/tasks?type={baidu|quark|115}
```jsonc
// PaTask[]
{ "id": 1, "type": "baidu", "name": "兰香如故", "enabled": true,
  "share_url": "https://pan.baidu.com/s/1aBcDeFg", "share_code": "abcd",
  "save_dir": "/影视/国产剧/兰香如故", "compare_path": "/影视/国产剧/兰香如故",
  "include_subdirs": true, "cron": "0 3 * * *", "exclude_count": 3,
  "last_run": "09-26 03:00", "last_status": "success",
  "last_result": "新增 2 / 跳过 34 / 失败 0", "post_qms": true, "post_notify": true }
// last_status: success|fail|running|never；cron 空 = 仅手动
```

### POST /api/pa/tasks · PUT /api/pa/tasks/:id
保存，返回落库后的任务（**新增时 id 由后端分配**）。请求体除 PaTask 外带扩展字段（前端现在单独存，后端建表时并入任务表）：
```jsonc
{ "…PaTask": "…",
  "qms_id": 1, "strm_id": null,
  "regex": [{ "pat": "\\.mkv$", "rep": "[1080P]" }],
  "drill_on": false, "drill": ["第 01-12 集", "幕后花絮"] }
```

### PUT /api/pa/tasks/:id/enabled · DELETE /api/pa/tasks/:id
开关翻转返回新状态布尔；删除。

### PUT /api/pa/tasks/:id/exclude
```jsonc
// 请求：排除文件回写（idx = 分享清单里被排除的下标数组）
{ "idx": [0, 2, 4] }
```
后端存排除清单（建议存文件名/md5 而非下标），回写 `exclude_count`。

### POST /api/pa/tasks/parse-share
解析分享链接：请求 `{ "url": "https://pan.baidu.com/s/…?pwd=abcd" }`；返回
`{ "code": "abcd", "dirs": [{ "name": "第 01-12 集", "size": "36 GB" }] }`
（`?pwd=` 提取码识别前端已做，后端兜底再解析一次；dirs 供「转存文件夹下钻」勾选）。

### GET /api/pa/excl-files?url={share_url}&refresh=1
排除清单：按分享链接拉文件列表 `{ "fresh": true, "ts": 1695880000000, "files": [{ "name": "sample.mp4", "md5": false }] }`
（`md5`=是否带校验值；无 MD5 的转存时按文件名去重。**缓存语义：后端缓存，同链接命中秒回 fresh=false；refresh=1 强制重拉**）。

### POST /api/pa/tasks/:id/run
手动执行一次任务（原型「执行」按钮）。执行进度前端弹监控弹窗：建议后端起异步任务，前端轮询
`GET /api/pa/tasks/:id/run-state` 返回 `{ "progress": 68, "add": 2, "skip": 34, "fail": 0, "excl": 3, "logs": [...QueueLogLine] }`，
结束回写任务的 `last_run/last_status/last_result`（等价 mock 的 run-finish，前端留了这个占位）。

### GET /api/pa/tasks/:id/history-log
详情抽屉的执行日志 `QueueLogLine[]`（`lv`: STEP/INFO/WARN/ERROR，染色渲染）。

### GET /api/pan/dirs?type={type}
网盘目录树（转存弹窗选目标、任务弹窗浏览用）。**懒加载契约**：只返回根层，带 `children` 占位，
前端点一层拉一层（路径参数扩展为 `?type=&path=`）；配合 §7 目录树缓存秒开。

---

## 6. 转存记录（快照，只追加不改写）

### GET /api/records
```jsonc
// RecordRow[]（前端分页只做展示切片，数据量大了可加 ?page=&size=，前端已兼容全量）
{ "id": 1, "n": "庆余年.第二季.4K.HDR.国粤双语", "t": "baidu", "p": "/影视/国产剧/庆余年2",
  "st": "完成 36/36", "cls": "t-ok", "tm": "09-26 22:41",
  "qms": { "st": "成功", "cls": "t-ok" }, "strm": { "st": "成功", "cls": "t-ok" },
  "share_url": "https://…", "share_code": "abcd", "cron": "0 3 * * *",
  "include_subdirs": true, "exclude_count": 0, "post_qms": true, "post_notify": true }
// cls/qms.cls/strm.cls: t-ok|t-bad|t-off；qms/strm.st 直接写「失败 · 目标目录不存在」这类带原因的文案
```
注意交互契约：**偶数序自动任务记录接 Server 酱推送展示（"Server 酱推送已送达"），手动转存记录不接推送**。

### GET /api/records/:id/logs · DELETE /api/records/:id
执行日志；删除单条。

### DELETE /api/records?before={ISO 时间}
清空三月前记录，返回 `{ "count": 0 }`。

### POST /api/records/:id/retry-failed
重试失败项，返回 `{ "count": 3 }`（重试入队数）。

### POST /api/records/:id/retrigger-qms
对该记录的目标目录再次触发 QMS。

### POST /api/qms/trigger · POST /api/strm/trigger
手动触发（记录页弹窗）：`{ "id": 1 }`（刮削/同步目录 id）。前端对「都选」的场景自己做了 QMS 立即 + STRM 延迟 10 秒，
后端只需各管各的触发。

---

## 7. 网盘连接（红线：只回状态，绝不回填明文）

### GET /api/accounts
```jsonc
// AccountRow[]，固定 baidu/quark/115 三行
{ "type": "baidu", "short": "百度", "color": "#1677ff", "cred_kind": "BDUSS / STOKEN",
  "status": "connected", "last_check": "09-27 01:12", "base": "/影视",
  "note": "复用 bdsavepro 的 storage.py 逻辑" }
// status: connected|expired|unset
```

### POST /api/accounts/:type/check
凭据有效性自检，返回 `{ "ok": true, "kind": "success", "message": "连通正常", "status": "connected", "last_check": "09-28 21:00" }`
（`kind`: success|error|warning——未配置时 warning「尚未配置凭据」；检测后更新 last_check 与 status）。

### POST /api/accounts/:type/credential · DELETE /api/accounts/:type/credential
绑定凭据（body 形状按网盘：百度 BDUSS/STOKEN、夸克/115 Cookie 或扫码会话）；删除 = 置 unset。
**凭据加密存储，任何接口不回明文**；建议每日定时自检，过期置 expired（前端已按三态渲染）。

---

## 8. 系统设置

### GET /api/settings
```jsonc
{
  "search": { "pansou_url": "http://192.168.2.77:8028", "timeout": 30,
              "cache_mode": "on", "def_dir_baidu": "/影视", "def_dir_quark": "/剧集" },
  "notify": { "enabled": true, "sendkey": "SCT***", "webhook": "",
              "on_done": true, "on_fail": true, "on_part": true, "on_cred": true },
  "qms":    { "enabled": true, "url": "http://192.168.2.77:8020", "apikey": "***",
              "act_strm": true, "act_emby": true },
  "security": { "username": "admin", "session_days": 7 }
}
```
- **sendkey/apikey 接口可回掩码但保存要能区分「没改过掩码」**（或前端只写不改时跳过该字段——后端约定：值为 `****` 开头视为未修改）。
- 推送只服务**自动转存**（手动转存不推送），推送时机四项：转存完成/失败/部分失败/凭据过期告警。

### PUT /api/settings/search · PUT /api/settings/notify · PUT /api/settings/qms · PUT /api/settings/security
分 tab 保存；security 请求体 `{ "username", "old_password", "new_password" }`（前端已校验 ≥8 位、两次一致）。

### POST /api/settings/search/test · POST /api/settings/notify/test · POST /api/settings/qms/test
连通性测试，body `{ "url": "…" }` / `{ "sendkey": "…" }`；返回 `{ "ok": true, "ms": 138 }` / 200 / 200。

### GET /api/notify/history?limit=50
推送历史统计 `{ "delivered": 128, "failed": 2 }`（后续可扩展明细列表）。

---

## 9. 缓存配置（网盘目录树缓存）

### GET /api/cache/config · PUT /api/cache/config
```jsonc
// GET 返回
{ "cfg": { "master": true, "ttl": 30, "ttlUnit": "小时", "auto": true,
           "memHigh": 85, "act": "ladder", "maxEntries": 500 },
  "mem": { "pct": 62, "usedGb": 4.9, "totalGb": 8 } }
// act: ladder(逐级降级)|off(直接关闭)|compress(只压缩条目)
// 降级链路（后端实现）：压缩条目 → 减半缓存上限 → 关闭缓存；任何降级只影响速度不影响转存可用性
// memHigh 取 75|85|95；PUT 保存即生效
```

### GET /api/cache/trees
```jsonc
// CacheTree[]；ttlMin < 0 表示已过期（前端染红「已过期·待刷新」）
{ "id": 1, "type": "baidu", "acc": "主账号 138****6688", "path": "/影视",
  "entries": 156, "size": "42 KB", "ttlMin": 96 }
```

### POST /api/cache/trees/:id/refresh · DELETE /api/cache/trees/:id
刷新单棵（重置过期时间）/ 清除单棵。

### POST /api/cache/trees/refresh-all · DELETE /api/cache/trees
全部刷新（返回 `{ "count": 6 }`）/ 全部清除。

---

## 10. 前端切换清单（后端就绪后我来做，共 7 个文件）

`src/api/` 下 7 个模块（dd/search/records/tasks/accounts/settings/cache + queue 待建），
把每个函数里的 `mockDelay(...)` 换成对应 `http.get/post/put/delete` 调用（`TODO 后端:` 注释即端点），
`.env` 里 `VITE_USE_MOCK=false`。前端页面、组件、队列看板渲染逻辑零改动；
唯一结构性工作：队列引擎从本地 tick 切到轮询/SSE 消费后端状态（QueueBoard/QueueBadge 不用动）。
