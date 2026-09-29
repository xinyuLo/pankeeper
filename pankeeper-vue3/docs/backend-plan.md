# PanKeeper 后端方案与技术选型

> 基于 6 个参考项目的源码调研（报告见 `docs/research/`）得出的总体方案。结论先行的 TL;DR 在 §1；缓存体系（本次核心诉求：减少网盘请求）在 §6。

## 1. 结论速览（TL;DR）

1. **技术选型：Python + SQLite 完全正确**，具体栈推荐 **FastAPI + SQLAlchemy 2.0 + APScheduler + httpx + SQLite(WAL)**，不上 Redis、不上 Celery——家用单管理员，每盘一条串行队列，异步/分布式队列都是负资产。细节见 §3。
2. **架构一句话**：FastAPI 提供 REST/SSE（对齐 `api-contract.md`）→ 每网盘一个 adapter（百度自写 5 接口客户端 / 夸克照 quark-auto-save 链路 / 115 用 p115client）→ 统一转存队列（阶段机照前端契约）→ QMS/STRM 客户端照 bdsavepro 的 `trigger_after_transfer` 语义 → pansou 纯代理。
3. **缓存是本方案的灵魂**（§6）：目录树按目录粒度 LRU+TTL+水位降级+singleflight+写后失效+冷却栅栏+快照恢复（LitePan 已验证的同构方案）；分享清单缓存跟任务 cron 走 + 死链熔断；搜索历史存 SQLite 24–72h；配合批量提交与全局限速器，网盘请求量可以压到"每任务个位数请求"。
4. **License 红线**：bdsavepro / quark-auto-save 是 **AGPL-3.0**、LitePan 是 **PolyForm 非商用**——**机制照抄、代码重写**，API 端点/参数/策略不受版权保护；pansou / TgtoDrive / CloudSaver 是 MIT 可放心抄。
5. 各项目一句话评价：**quark-auto-save** 的夸克链路+风控三件套+MagicRename 直接当接口文档抄；**bdsavepro** 的价值是百度 5 个 API 的实测参数、错误码表和 QMS 轮询语义（外加一个"别把任务存 JSON"的反面教材）；**LitePan** 的价值是整套缓存/限频工程实践；**pansou** 是拿来即用的搜索源（记得配 `ENABLED_PLUGINS` 和 `PROXY`）；**TgtoDrive/CloudSaver** 补齐 115 的 API 组合和"无需重复接收"去重语义。

## 2. 六个项目各取什么

| 项目 | License | 拿什么 | 别拿什么 |
| --- | --- | --- | --- |
| fish2018/pansou | MIT ✅ | `POST /api/search` 契约（`res=merge` + `merged_by_type`）、`/api/health` 健康度、"首搜不全+后台补齐"特性 | 它自己的缓存我们不复用（60min 在它进程里） |
| xinyuLo/bdsavepro | AGPL-3.0 ⚠️ | 百度 5 个 API 的实测端点/参数/Referer、错误码表（-65 频率/-33 上限/31023/31061…）、去重顺序（**先 MD5 后文件名**）、QMS `trigger_after_transfer` 全套语义（延迟10s/轮询5s/超时300s/无新文件不触发STRM） | 代码本身（AGPL 传染）；config.json 存任务的模式（高频回写丢数据）；它没生效的全局限速（死代码） |
| Cp0204/quark-auto-save | AGPL-3.0 ⚠️ | 夸克 API 全套（stoken→detail→save→task 轮询→save_as_top_fids 顺序 rename）、风控三件套（`__dt`/`__t` 伪造、移动端分享 API、`fetch_risk_file_name=1`）、`shareurl_ban` 死链熔断、MagicRename 正则表、100/批上限 | 代码本身；它缺失的全局限速器/指数退避（PanKeeper 要补） |
| walkingddd/TgtoDrive | MIT ✅ | 115 webapi 组合（`/share/snap` 翻页 + `/share/receive` 整目录接收）、**"文件已接收，无需重复接收"判成功**、文件夹优先取 `fid`、p115client 的 `fs_files_app`/`fs_move_app(app="android")` | 它的裸 sleep 堆砌（要换成统一限速器） |
| jiangrui1994/CloudSaver | MIT ✅ | `ICloudStorageService` 多盘适配器接口形状、目录选择面包屑交互、115 小程序 UA | 它的 limit=20 不翻页、只转 fids[0] 等缺陷 |
| Ponphil/LitePan | PolyForm 非商用 ⚠️ | **整套缓存工程**（按目录 LRU/TTL/水位/单飞/写后失效+3s冷却栅栏/快照/命中率统计）、115 Open 的 800ms 账号级间隔门/406 退避/3 连败熔断 30s/token 提前 15min 刷新、阶梯分页 300/600/1000 | 代码本身（禁止商用许可） |

## 3. 技术选型意见（Python + SQLite：赞成，附具体栈）

你的方向（Python + SQLite）和所有参考项目的实践一致（bdsavepro=Flask+SQLite WAL、CloudSaver=Sequelize+SQLite、LitePan=modernc SQLite、pansou 连数据库都不要）。家用单管理员 + 每盘一条串行队列的场景，**复杂度预算应该全花在转存链路和缓存上，而不是基础设施上**。推荐组合：

| 层 | 选型 | 理由 |
| --- | --- | --- |
| Web 框架 | **FastAPI**（同步路径函数为主） | api-contract.md 已按 REST 设计；自带 OpenAPI 文档方便自己调试；SSE 原生支持（队列状态推送）。不必上异步全家桶——网盘操作是限速串行的，`def` 路径跑线程池足够 |
| ORM | **SQLAlchemy 2.0 + Alembic** | 不要裸 sqlite3（表会越长越多）；也别 SQLModel 太省（复杂查询难受）。单文件 `pankeeper.db` |
| SQLite 打开方式 | **WAL 模式 + `busy_timeout` + 单写者** | Web 读（看板 600ms 轮询）与任务写并发；bdsavepro 验证过 WAL 的必要性 |
| 调度 | **APScheduler**（BackgroundScheduler + CronTrigger） | bdsavepro/quark-auto-save 双验证：`max_instances=1`、`coalesce=True`、`misfire_grace_time=300`；cron 直接 `from_crontab`。**进程内跑即可，不用像 QAS 起子进程** |
| HTTP 客户端 | **httpx**（同步 Client，per-drive 实例） | 带连接池、超时好控；百度/夸克/115/QMS/Server酱各一个 Client 类 |
| 115 客户端 | **p115client** | TgtoDrive 同款；`user_info()` 探活 + `fs_files_app` 列目录 + `fs_move_app` 搬移 |
| 配置/凭据存储 | **SQLite，凭据列用 Fernet 加密**（密钥来自环境变量/密钥文件） | 修复 bdsavepro 明文 JSON + 可回显的问题；`api-contract.md` 已承诺"只回状态不回明文" |
| 队列状态 | **内存权威 + SQLite 快照** | 队列任务量小（个位数），进程内对象最快；每次状态变更落 `queue_task` 表，重启恢复（对齐前端 30 分钟保留语义） |
| 推送 | Server酱 + Webhook（对齐设置页） | QAS 的 25 渠道是过度设计，接口留扩展点即可 |
| 部署 | 单容器/单进程，pansou 单独一容器 | compose 两行；pansou 端口绑内网 |

**明确不引入**：Redis（pansou 新版都删了，我们没理由加）、Celery/RQ（APScheduler + 线程够了）、Docker 内多进程（单管理员没有并发压力）。

## 4. 模块划分与目录

```
pankeeper-backend/
  app/
    main.py            # FastAPI 装配：路由 + APScheduler + 队列线程启动
    api/               # 路由层（对齐 docs/api-contract.md 的 9 大领域）
    adapters/
      base.py          # CloudAdapter 抽象：parse_share/list_share/list_dir/save_files/
                       #   mkdir/rename/check_credential（照 CloudSaver 接口形状）
      baidu/           # 自写 5 接口客户端（端点参数照 research/bdsavepro.md §2）
      quark/           # stoken→detail→save→task 轮询→rename（照 research/quark-auto-save.md §2.3）
      pan115/          # p115client + webapi /share/snap + /share/receive（照 research/115-*.md）
    engine/
      queue.py         # 阶段机 transfer→waitqms→qms→waitstrm→strm→done；线程数/间隔/QMS延迟可配
      rate_limiter.py  # 每网盘一个限速门（间隔/令牌桶 + 退避 + 熔断）
      scheduler.py     # APScheduler：pa 任务 cron 执行 + 凭据每日探活 + 缓存自动刷新
    services/
      dedup.py         # 去重链：正则 → MD5 → 文件名 → rename_only（百度）；文件名/序号（夸克）；"已接收"（115）
      qms.py           # trigger_after_transfer：延迟10s→start→轮询5s→超时300s→新文件才STRM(+10s)
      notify.py        # Server酱/Webhook，批次汇总制
      search.py        # pansou 代理（res=merge，超时15s）+ 搜索历史落库
    cache/
      dir_tree.py      # §6.1 目录树缓存（核心）
      share_list.py    # §6.2 分享清单缓存（key=分享链接，跟任务 cron）
      search_history.py# §6.3 搜索历史
    db/                # models.py + migrations
```

## 5. 数据模型（SQLite 建表清单）

```sql
-- 凭据（加密列）
accounts(type PK, cookies_enc, extra_json, status, last_check)
-- 自动转存任务（PaTask + 扩展字段合并进一张表，前端契约见 types/model.ts）
pa_tasks(id PK, type, name, enabled, share_url, share_code, save_dir, compare_path,
         include_subdirs, cron, exclude_json, exclude_count, qms_id, strm_id,
         regex_json, drill_json, drill_on, post_qms, post_notify,
         last_run, last_status, last_result, ban_reason)  -- ban_reason=死链熔断（QAS shareurl_ban）
-- 转存配置目录（快速转存依据）
dd_items(id PK, type, account, sort, name, path, is_default, qms_on, qms_id, strm_id)
qms_paths(id PK, media_type, source_path)   strm_paths(id PK, remote_path)
-- 队列（内存权威 + 快照）
queue_tasks(id PK, name, type, path, files, size, status, phase, phase_start,
            progress, done_at, logs_json)
queue_config(k=单行: threads, gap, qms, strm)
-- 记录（快照，只追加）
records(id PK, n, t, p, st, cls, tm, qms_json, strm_json, share_url, share_code,
        cron, include_subdirs, exclude_count, post_qms, post_notify)
run_history(id PK, task_id, started, finished, status, add, skip, fail, excl, logs_json)
-- 缓存
dir_tree_cache(cid PK per account, items_json, expires_at)  -- 持久层（内存 LRU 为主）
dir_path_cache(account_id, dir_id, dir_path, parent_id, last_seen_at)  -- ID→路径稳定映射（LitePan 同款）
share_list_cache(share_url PK, files_json, fetched_at, banned)         -- banned=死链标记
search_history(id, keyword, cloud_types, result_json, fetched_at)
-- 设置与日志
settings(key, value_json)   push_log(id, channel, title, content, result, sent_at)
qms_trigger_log(id, link_id, trigger_at, source, success, message)
```

## 6. 缓存体系（核心：减少网盘频繁请求）

目标拆开看是三类请求：**浏览类**（目录树、分享清单——纯读，重缓存）、**对比类**（去重基线——短缓存+写后失效）、**动作类**（转存本身——不可缓存，但可批量化+限速化）。策略一张表：

| 缓存 | 内容与粒度 | 存哪 | TTL / 失效 | 来源依据 |
| --- | --- | --- | --- | --- |
| ① 目录树缓存 | **按目录**（不是整树）：`cid → entries`，内存 LRU（条目上限/内存水位 80%→批量逐出/100%→逐到70%）+ 空目录也缓存 + singleflight（同 key 并发只放一个去打网盘）+ 每 10min 快照重启恢复 | 内存（主）+ SQLite `dir_tree_cache`（辅） | 默认 30 分钟起步（前端已做成可配 30 小时+自动刷新）；**写后失效 + 3s 冷却栅栏**：转存/建目录成功 → 失效父目录 → 冷却期内直读不回填（防 115 写后延迟的脏数据染进 TTL） | LitePan §3 全套；前端缓存配置页就是照它设计的 |
| ② 目录 ID→路径映射 | `(account, cid) → path` 稳定映射 | SQLite `dir_path_cache` | 无 TTL；rename/move/delete 按路径前缀级联删 | LitePan §3.1 |
| ③ 分享清单缓存 | `share_url → 文件列表(含 md5有无)` | SQLite `share_list_cache` | **跟任务 cron 走**：任务执行时预取（排除弹窗秒开）；弹窗显示"下次自动刷新=cron 下次触发"；手动强刷透传 | 原型 mtExclCache 语义 + bdsavepro `_SHARE_FILES_CACHE` |
| ④ 死链熔断 | `share_url → ban_reason` | `share_list_cache.banned` | stoken/verify 返回失效即标记 + 推送，**之后不再对死链发请求**；换链接自动清除 | QAS `shareurl_ban`（最容易被忽视的风控细节） |
| ⑤ 搜索历史 | `(keyword, cloud_types) → merged_by_type` | SQLite `search_history` | 24–72h（影视链接时效远长于 pansou 的 60min）；翻历史零等待，点"重新搜索"才回源 | pansou 调研 §5.3 |
| ⑥ 去重基线 | 对比目录的 `{文件名集合, md5集合}` | 内存（任务执行期） | 任务执行时拉一次（走①的目录缓存可复用）；转存成功后失效该目录 | bdsavepro 去重链 |
| ⑦ 凭据探活 | `user_info`/`account_info` 结果 | 内存 + `accounts.last_check` | 每日一次定时（兼保活流量），失效标红推送 | 三项目都是空白，PanKeeper 补齐 |
| ⑧ QMS 触发去重 | `transferred_count` | 内存 | **=0 不触发 QMS**（QMS 对在库文件只会跳过）；QMS 无新记录也不触发 STRM | bdsavepro qms_client 语义 |

**三条贯穿性原则**（LitePan 用血泪换来的）：
1. **singleflight 必做**：前端 600ms 轮询 + 缓存过期瞬间的并发请求，不做单飞就是缓存击穿直打网盘。
2. **写后失效 + 冷却直读**：不要在写操作后立刻回填缓存——115/百度写后服务端有复制延迟，"转存完立刻看不到文件"比"看到脏数据 30 分钟"好得多。
3. **空结果也缓存**：空目录穿透是最常见的无意义请求来源。

## 7. 各网盘接入要点（风控对策汇总）

### 7.1 全局限速器（QAS/bdsavepro 都没有，必须补）
每网盘一个 `RateGate`：**串行锁 + 最小间隔 + 指数退避 + 连败熔断**。参考预算（ LitePan 实测 + QAS 批量上限）：

| 网盘 | 最小间隔 | 批量上限 | 限频信号 → 动作 | 熔断 |
| --- | --- | --- | --- | --- |
| 百度 | 1–2s（-65 → sleep 10s 整组重试） | fsidlist 一组 ≤999（-33） | -6/-115/145/200025 不重试 | -6 标记凭据失效 |
| 夸克 | 0.5–1s（task 轮询 0.5s） | 100 个/批（save_as_top_fids 硬上限） | stoken 非 200/500 → ban | `code:50051` → Cookie 失效告警 |
| 115 | **≥800ms/请求**（同账号串行） | file_id 逗号拼接整目录一次提交 | HTTP 406 → 退避 | **3 连败 → 30s 熔断**；验证码 = 已被风控，停下报警不硬闯 |
| pansou | 转发超时 ≥15s | — | tg 不可达看 `/api/health` | — |

请求伪装：百度 Referer=分享链接；夸克 PC Electron UA + `__dt`/`__t` + 移动端分享 API（kps/sign/vcode，删 Cookie 头）；115 小程序 UA 或 p115client 默认 UA；夸克目录列表带 `fetch_risk_file_name=1`。

### 7.2 去重链（每盘统一，顺序不可乱）
百度：正则过滤 → **MD5 优先**（分享 md5 ∈ 对比目录 md5 集合 → 跳过）→ 文件名（basename）→ rename_only。夸克：文件名 + ignore_extension + `{I}` 序号通配。115：`/share/receive` 返回"文件已接收，无需重复接收"判成功 + 已处理分享 URL 表。**输入侧**再叠一层：同一分享 URL 不重复入队。

### 7.3 与前端的衔接
队列阶段机/线程数/间隔/QMS·STRM 延迟全部由队列配置驱动（`api-contract.md` §4 已定契约）；执行进度/日志用 SSE 推（`GET /api/queue/state` 600ms 轮询为兜底）；目录树接口照 LitePan 形状 `GET /api/files/list?parent=&force_refresh=`，配 `/api/cache/hit-rate` 给缓存配置页显示命中率。

## 8. 搜索对接（pansou）

- 部署：`ghcr.io/fish2018/pansou` 单容器绑内网 8888，`ENABLED_PLUGINS` 必须显式配（不配=零插件），`CHANNELS` 抄 README 的 60 个影视频道清单，访问 t.me 配 `PROXY=socks5://192.168.2.77:7890`。
- 后端代理 `POST /api/search`：固定 `res=merge` + `cloud_types` 按 PanKeeper 支持的盘 + `filter.exclude:["预告","花絮"]`；**超时 15s**（无缓存首搜 4–10s，命中 <100ms）。
- 利用"首搜不全 + 后台补齐"特性：前端首搜出结果后 3–5s 静默重查一次合并增量（pansou 缓存命中近乎免费）；`/api/health` 的 liveness 给设置页做"搜索源健康度"。
- `merged_by_type[*].url+password` 直接喂转存 adapter；`note` 清洗后作任务名/刮削搜索词。

## 9. QMS / STRM / Emby 链路

照 bdsavepro `trigger_after_transfer` 语义（与前端原型 10s+10s 契约一致）：
转存完成（且新增文件数 >0）→ 延迟 10s → `POST /api/scrape/pathes/start {id}` → 每 5s 轮询 `GET /api/scrape/pathes/{id}`（完成判定 `is_running==false && is_scraping==false && updated_at>=触发时刻`，超时 300s）→ 统计 `/api/scrape/records` 本次新增 → 有新文件再延迟 10s → `POST /api/sync/path/start {strm_id}`。失败路径：超时记"未确认"不触发 STRM；success=0（无新文件）不触发 STRM。QMS 鉴权 X-API-Key 或 login+CSRF（缓存 session 1800s）。

## 10. 分期实施建议

1. **M1 骨架（先跑通一条盘）**：FastAPI 骨架 + SQLite + 登录 + 夸克 adapter + 队列引擎（阶段机照契约）+ 前端切真实接口（夸克单盘端到端：搜索→快速转存→队列看板→记录）。
2. **M2 缓存体系**：目录树缓存全套（LRU/水位/单飞/写后失效/快照）+ 分享清单缓存 + 死链熔断 + 命中率统计接进缓存配置页。
3. **M3 自动转存 + 剩余网盘**：APScheduler cron + 百度 adapter（自写客户端）+ 115 adapter（p115client）+ 排除清单跟 cron + 凭据每日探活。
4. **M4 媒体链路 + 打磨**：QMS/STRM 联动 + Server酱推送 + 搜索历史/健康度 + 记录清理/重试。

每期结束跑一遍前端全流程目检（页面已就绪，`VITE_USE_MOCK=false` 按模块切换即可）。
