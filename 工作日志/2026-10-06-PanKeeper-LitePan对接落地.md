# 2026-10-06 批次：LitePan 对接落地（Webhook 协议定稿 + 全链实现）

> 时段：10-06 下午。前置：gitea 同步于 b9dda21（10-05 深夜批次收口点）。

## 一、协议定稿：不是 WebSocket，是 HTTP Webhook

上批交接里写的是「LitePan 用 WS 推消息、协议未定稿」——那是研究前的猜测。本批把用户放在
工作区外层的 LitePan Go 源码（`D:\zcodeWork\pankeeper\lp_*.go`，14 个文件）通读后定稿：

- **端点**：`POST <LitePan地址>/api/open/automation/events`（open 组，免登录但必须带 API Key）。
- **请求体**：`{event, source, path}`——LitePan 的 `decodeWebhookEvent` **只读这三个字段**，
  多给的键直接忽略（PanKeeper 照发 drive/task/files/share_url/share_code，留作扩展与排查）。
- **认证**：`Authorization: Bearer <API Key>`（LitePan 侧也认 `X-API-Key` 头，二选一）。
- **规则匹配**：LitePan 拿 event / source / path_prefix **精确匹配**启用中的 webhook 规则
  （`lp_webhook.go::matchWebhook`），命中即 `submitRun` 入队——**异步执行，响应立回**，
  PanKeeper 不会被 LitePan 的整理流程堵住。
- **响应**：`data` 里带 `matched` / `triggered:[{id,name}]`——能知道有没有规则接住。
  writeOK 信封定义不在手头文件里，解包按 `{code,data}` / 裸 `{...}` 两种形状兜底。
- **规则动作链**（`lp_run.go::executeAction`）：organize（整理任务）→ strm → strm_scrape →
  emby refresh → emby 补全 → fnos 扫库/刷元数据 → cache_clear → delay，全在 LitePan 侧自理。

## 二、后端实现

- **`services/litepan.py` 重写**（原为纯留痕 stub）：
  - `notify_transfer_done(payload)` → 真 POST Webhook，返回 `{ok, matched, triggered, message}`；
    未配 webhook_url 只留痕不报错。四种失败分支各有如实 message：连接失败 / HTTP 4xx/5xx /
    信封业务码非 0 / matched=0（提示检查事件名与路径前缀配对）。
  - 新增 `test_webhook(url, apikey)`：发 `pankeeper.test` 事件（正常配置不会命中规则），
    HTTP/信封通了就算 ok——设置页「测试」按钮用。
- **设置组 `litepan`**（`settings_svc.py`）：`ws_url` 废弃 → `{webhook_url, apikey, event}`，
  event 默认 `transfer.done`（须与 LitePan 规则里配的完全一致）。apikey 复用 `_SENSITIVE_FIELDS`
  加密存储 + `****` 掩码保留逻辑（get_group/save_group 各加 litepan 分支；_encrypt 提到函数层）。
- **设置 API**（`api/settings.py`）：GET /settings 回 litepan 组（apikey 掩码）；
  新增 `PUT /settings/litepan`（独立顶层组，同 media 的待遇）与 `POST /settings/litepan/test`
  （掩码 key 回落已保存值）。
- **调用点**（`manual.py` / `auto.py` litepan 分支）：拿返回值写转存日志——
  ok→INFO（含命中规则数与名字），失败→WARN。**不再无脑写「转存完成消息已推送」**。

## 三、前端（设置页 tab3）

- **恢复联动后端切换下拉**（此前按注释标记隐藏、恒 qms）：顶部选后端，
  下面按所选显示 QMS 连接参数或 LitePan 参数（Webhook 地址+测试 / API Key / 事件名+说明行）。
- `saveLitePan` / `testLitePan` API 模块 + mock 类型 `LitePanCfg`。
- **顺手抓出一个潜伏 bug**：`onMediaBackend` 只调 `saveMediaBackend(v)` 没更新本地
  `mediaBackend.value`（原代码在 UI 隐藏期间从没被点过）——弹窗提示切换成功、下拉和表单都不动。
  已修（select 是 `:value` 绑定不是 v-model），双向切换实测正常。

## 四、验证记录

- 后端：compile + 单元自检（未配置/空地址/掩码保留/加密回落）；本地 mock HTTP 服务全链往返
  （Bearer 头、matched/triggered 解包、命中 2 规则的日志文案）；monkeypatch 四分支
  （401 JSON / 500 无 JSON / 业务码非 0 / 裸响应无信封）全过。
- 接口：起真后端登录实测 GET/PUT/settings、掩码回显与保留、test 端点连通失败如实报错。
- 前端：`vue-tsc` 过；浏览器实测——QMS 模式渲染、切 LitePan 表单显示、测试按钮报错文案、
  切回 QMS（验证切换 bug 修复）。测试数据已清空还原（media.backend=qms，litepan 组空）。
- **真连 LitePan 实测未做**——NAS 上还没部署 LitePan 实例，等部署后在「测试」按钮上点一次即可。

## 五、遗留与注意

- LitePan 规则匹配 path 用的是**转存目标目录**（`t["path"]`），规则的「路径前缀」按这个配。
- `event` 名两头必须完全一致（PanKeeper 设置页 ↔ LitePan 规则），不一致就是 matched=0，
  转存日志会如实提示。
- LitePan 是 PolyForm NC（禁商用）协议：只搬思路不搬代码，对接走标准 HTTP 接口无碍。
- NAS 部署：本批推送后按 HANDOFF §1 姿势重建容器（`/vol1/1001/compose/pankeeper` 下 compose up）。
