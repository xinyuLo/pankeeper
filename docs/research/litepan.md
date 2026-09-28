# LitePan 调研报告 —— 115 目录浏览/挂载/缓存策略

> 调研对象：https://github.com/Ponphil/LitePan（main，2026-09-20，v0.5.6-Beta，1288 star）。当前仓库是 **Go 重写版**（旧 Python 版归档在 LitePan-old）。**License：PolyForm Noncommercial 1.0.0——个人非商用，禁止商用，不接受 PR。代码不能复制进 PanKeeper，只能作行为参考（端点、参数、TTL 数值、策略）；所列 115 Open 端点为官方公开 API，不受其许可约束。**

## 1. 定位 / 技术栈 / 分层

- 多网盘聚合挂载工具（115 Open/123/天翼/移动/夸克/百度/OneDrive/WebDAV/OpenList/本地盘），与 PanKeeper 高度同源：跨盘秒传转存、STRM 生成、STRM 刮削（nfo/海报）、目录整理（TMDB）、302 直链。
- Go 1.x + 自研 router；存储 `modernc.org/sqlite`（纯 Go 无 CGO）；WebDAV server `golang.org/x/net/webdav`；FUSE `hanwen/go-fuse/v2`；前端 Vue3+Vite。
- 单 Docker 容器（5211），数据全落 `/app/data`（SQLite + 缓存快照 + FUSE 读缓存）。单机单管理员形态与 PanKeeper 一致。
- **最有参考价值的分层**：
  `前端(FileBrowser/FolderSelector)` → `HTTP API(/api/files/list)` → `file.Service(读缓存/写失效)` → `cache.Service(LRU+TTL)` → `driverexec.Executor(认证闸门/熔断/被动刷新)` → `drivers/115_Open(限频门/OAuth)` → `proapi.115.com`

## 2. 115 目录获取链路（drivers/115_Open/）

用 **115 Open 平台 API（OAuth Bearer token）**，不是 web 端 cookie API。base `https://proapi.115.com`，UA 伪装 Chrome 120。

### 端点清单（transport.go）
| 用途 | 端点 | 方式 |
|---|---|---|
| **目录/文件列表** | `GET /open/ufile/files` | query |
| 单文件/目录详情 | `GET /open/folder/get_info?file_id=` | 返回 `paths[]` 祖先链 |
| 下载直链 | `POST /open/ufile/downurl`（form: pick_code） | 直链 TTL 按 5 分钟算 |
| 建目录 | `POST /open/folder/add`（pid, file_name） | |
| 复制（同盘转存） | `POST /open/ufile/copy`（file_id 逗号串, pid, nodupli=0） | |
| 移动 | `POST /open/ufile/move`（file_ids, to_cid） | |
| 删除 | `POST /open/ufile/delete`（file_ids）→ 回收站；永久删再 `/open/rb/list`+`/open/rb/del` | |
| token 刷新 | `POST https://passportapi.115.com/open/refreshToken` | |

### 列表分页（driver.go `ListFiles`，核心！）
- 参数：`cid=<父目录ID>&limit=<N>&offset=<N>&show_dir=1`（文件夹文件混合返回，靠字段区分）。
- **阶梯式页大小：第 1 页 300 条，第 2 页 600 条，之后每页 1000 条**——首页只拿 300 为了快速响应；`count` 在第一页返回总数，后续按 `remaining = count - fetched` 收窄 limit。
- 终止条件：`fetched >= count`，或返回条数 < limit，或空页。**不信任 count 单独终止**（防 count 不准漏文件）。

### 全量递归清单（full_list.go，STRM 扫描用）
- 同端点加 **`cur=0&show_dir=0`**：服务端**递归展开**所有文件（不返回文件夹），每页 **1150 条**，条目自带 `pid`，上层用 `pid→路径` 映射还原目录树（目录路径由 `/open/folder/get_info` 的 `paths[]` 补齐，存 SQLite）。
- 空页不直接结束：**等 250ms 重试一次**，连续两页空才算完；结束后校验 `去重后条数 < count` 则报"清单不完整"**整体作废**；分页 ID 重复立即作废。"宁可失败不可半截"的一致性策略。

### 条目字段（models.go，多命名兼容）
`fid/file_id`、`fn/file_name`、`pid/parent_id/cid`、`fc/file_category`（=="0" 是目录）、`s/size/fs`、`sha1`、`pc/pick_code`（下载凭证）、`upt/t/uet` 时间戳、`thumb`。
- **回收站过滤**：`aid ∈ {"7","120"}` 的条目是回收站残留直接跳过。
- **pick_code 内存缓存**：`map[fileID]pickCode` 上限 10 万条满则整体清空；列表时顺手记住，下载省一次 get_info。

## 3. 缓存策略（重点，internal/cache/）——PanKeeper 目录树缓存的直接参照

### 3.1 双层结构
1. **进程内元数据缓存 `cache.Service`**（主缓存）：
   - **粒度：按目录**。键 `<type>:<accountID>:<parentID>`，type ∈ `dir`（目录列表）/ `file`（单文件详情）/ `dl`（下载直链，含 UA 维度）/ WebDAV 路径映射 / PROPFIND 报文。**没有"整树缓存"**，树是逐目录 entry 的集合。
   - **存储：纯内存**（map + container/list 的 LRU），可选定期快照到磁盘 JSON。
   - **TTL**：默认 **30 分钟**（可配 0–1440 分钟）；优先级：全局开关 > 账号级 `cache_ttl`（0=该账号禁用缓存）> 全局默认；TTL<=0 完全禁用直连网盘。
   - **容量双上限 + 水位分级淘汰**（与 PanKeeper 前端已设计的"内存水位降级/LRU"完全同构）：
     - 条数上限默认 **10000 条**（超了逐出最旧）；
     - 内存软上限默认 **128MB**（可配 64MB–16GB）：**≥80% 水位 → 批量逐出 len/10 条（1–100 条）；≥100% → 逐到 70%**；
     - 每条 entry 入队时估算字节（payload JSON 长度 + 48B 壳 × 1.1 堆浮系数）。
   - **GC**：后台每 **1 分钟**清扫过期项。
   - **singleflight**：同 key 并发未命中只放一个 loader 去打网盘，其余等结果；**空结果也缓存**（防空目录穿透）。
   - **统计**：hits/misses/evictions/expirations 原子计数 + 命中率 HitTracker，前端 UI 实时显示"缓存命中率 %"（信任感强）。
2. **SQLite 持久目录映射 `strm_remote_dir_cache`**——只有 `(account_id, dir_id) → dir_path, last_seen_at` 一张表。用途：全量清单按 pid 还原路径 + 按路径前缀反查子孙做级联失效。**不是列表缓存**，是"ID→路径"稳定映射，无 TTL，靠 rename/move/delete 事件级联删除（`ListByPathPrefix` 找子孙 → `DeleteByIDs`）。

### 3.2 写后一致性（最值得抄的三个机制）
1. **先失效再发事件**：所有写操作（建目录/删/移/改名/上传）成功后先 `InvalidateDirKeys(父目录)`，再发 `FileMutated` 事件。
2. **事件驱动的原地更新**：create 事件时若父目录列表还在缓存里，**直接把新条目插进缓存列表并保留剩余 TTL**——避免"建完目录立刻列表又打一次网盘"。
3. **写后冷却栅栏（mutation fence）**：写操作把父目录标记进 **3 秒冷却期**；冷却期内 List **直连网盘且不回写缓存**——因为 115 写后服务端有复制延迟，立刻读会拿到 stale 列表并把脏数据染进 30 分钟 TTL（源码注释："避免 stale 列表污染 TTL"）。这就是"转存完成 → 弹窗立即刷目标目录"的正确姿势：**失效 + 冷却直读，而不是立即回填缓存**。

### 3.3 持久化快照（persist.go / snapshot.go）
可开关（默认开），**每 10 分钟**把未过期 entry 原子写 `<dataDir>/cache/cache_data.json`（tmp+rename）；**重启时恢复**（过期项丢弃），关机时也存一次。效果：重启后缓存依然热着，冷启动不打 API。

## 4. 限频对策（115 Open 版）

1. **账号级请求间隔门**（delay.go + transport.go `beforeCall`）：每个**账号**一把串行锁 + 上次请求时间戳，**任何平台 API 调用前强制等待，同账号相邻请求间隔 ≥ 800ms**。跨账号互不影响。
2. **限频识别**：响应 `code == 406` → 归类 `CodeRateLimited`；删除回收站轮询 300ms→800ms 递增 sleep。
3. **网络熔断**：同账号**连续 3 次网络失败 → 熔断 30 秒**，期间所有调用直接报"约 N 秒后自动重试"；成功即清零。传输层失败重置 HTTP transport（断连自愈）。
4. **认证失败自愈**：`code 99 / 401 / 401xxxx` → token 过期 → **自动刷新 token 并原请求重试一次**；主动调度器另有 stepped cooldown（60s/120s/300s/1800s，最终 24h）。
5. **token 保活**：access_token 生命周期按 **2 小时**登记，**提前 15 分钟**主动续期；refresh_token 翻新后**立即持久化到 SQLite**；刷新错误分级：40140115/116/119/120 致命（需重新授权），40140117/121 可重试。账号健康探测 `Ping` = 列表端点发一次 `limit=1&offset=0` 最小请求。
6. **并发控制**：单账号内天然串行（800ms 间隔门互斥）；下载并发=2；**无并发目录扫描——宁可慢不并发**。

> **115 日常请求预算结论**：LitePan 实践 = "同账号 ≥800ms/请求 + 无并发 + 失败熔断 30s"，即单账号稳态 ~1.2 req/s 封顶，常态靠 30 分钟缓存远低于此。PanKeeper 安全线：**1 请求/秒封顶、批量任务 2–3 请求/秒以下、406 即退避**。

## 5. 挂载形态

- **WebDAV server**：路径 `/dav/<账号名>/<相对路径>`，basic auth；PROFIND 响应整体缓存；路径→ID 映射缓存（逐级解析每级都写，失败整账号清空重试一次）。GET 走 302 直链（或代理 range 透传）。
- **FUSE 本地挂载**：内存 nodes 树 + 可选磁盘读缓存（默认 10GB / 7 天 / LRU）。
- **给 Emby/QMS 的配合**：STRM 文件里写 LitePan 自己的播放 URL（302 重定向到 115 CDN），不是 WebDAV 路径。**PanKeeper 的 STRM 方案可照此：strm 内容指向自家 API，后端换 115 下载直链并 302。**

## 6. 对 PanKeeper 的具体建议

### 6.1 目录树缓存（Python+SQLite 落地）
照抄双层模型：
- **内存层（主）**：`dict[cid] -> {items, expires_at}` + OrderedDict LRU；参数直接用它的默认值：**TTL 30 分钟起步（PanKeeper 可配更长）、条目上限 10000、内存软上限 128MB、80% 水位批量逐出 / 100% 逐到 70%、空结果也缓存、singleflight（Python 用 per-key lock + future）**。
- **SQLite 层（辅）**：只存稳定映射：

```sql
CREATE TABLE dir_path_cache (
    account_id   INTEGER NOT NULL,
    dir_id       TEXT    NOT NULL,
    dir_path     TEXT    NOT NULL,
    parent_id    TEXT,
    last_seen_at INTEGER NOT NULL,
    PRIMARY KEY (account_id, dir_id)
);
CREATE INDEX idx_dpc_path ON dir_path_cache(account_id, dir_path);
```
浏览目录时顺手 reconcile 写入；rename/move/delete 后按 `dir_path LIKE '旧路径/%'` 级联删。
- **可选快照**：每 10 分钟 dump JSON、启动恢复——NAS 重启后转存弹窗秒开。

### 6.2 与转存弹窗懒加载的配合
- 弹窗只拉根一层；展开/进入再拉该目录，全命中后端缓存；
- 面包屑 + 收藏定位；前端请求带**序号防串台**（翻目录快于响应时丢弃旧响应）、`force_refresh=true` 只失效当前目录；
- **转存写成功后：后端失效目标父目录缓存 + 打 3 秒冷却标记**，弹窗刷新时直连拿真数据（不回写缓存），3 秒后恢复——立刻看到新文件，又不把 115 写后延迟的脏数据染进缓存。
- "整树预加载"（自动刷新）用 `cur=0&show_dir=0` 全量清单模式（1150/页、250ms 空页重试、count 校验防半截），比逐目录 BFS 省一个数量级请求。

### 6.3 后端 API 形状（照抄即可）
`GET /api/files/list?account_id=&parent=<cid>&force_refresh=true&path=<展示路径>`：返回 items（is_dir/size/mtime/sha1/thumb），响应同时 reconcile dir_path_cache；另出 `GET /api/cache/hit-rate` 给前端显示命中率。

### 6.4 115 客户端封装要点清单
Bearer OAuth（refresh_token 持久化）· 每账号串行 ≥800ms · 300/600/1000 阶梯分页 · `aid∈{7,120}` 过滤回收站 · 406→限频退避、401→刷 token 重试一次、3 次网络失败熔断 30s · pick_code 随列表缓存 · 目录名含 `/` 时段内替换成 `_`（防路径伪造，STRM 场景的坑）。
