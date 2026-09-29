# pansou（fish2018/pansou）调研报告 — 供 PanKeeper 后端对接

> 调研基线：main 分支，最后提交 2026-09-27；14.7k stars / 3.6k forks；Go 1.25 + Gin。MIT License。

## 1. 项目定位与部署形态

**定位**：纯后端的网盘资源搜索 API 服务，聚合两类数据源：
- **Telegram 频道实时抓取**：搜索时直接请求 `https://t.me/s/<频道>?q=<关键词>`（t.me 网页预览版），逐频道并发抓 HTML 并解析出网盘链接；
- **插件爬虫**：每个插件是一个第三方资源站的爬虫（仓库 111 个插件包，main.go 空导入注册 76 个，docker-compose 默认启用 69 个）。

**它不是定时爬虫**——没有"多久爬一次"的概念，全部是**请求触发式搜索 + 结果缓存**。频道列表只是"搜索时并发查询哪些频道"的配置。

**部署形态**：单 Go 静态二进制，**零外部依赖**——不依赖 Redis、数据库、消息队列。Docker 镜像两种：`ghcr.io/fish2018/pansou`（纯后端 API，PanKeeper 用这个，端口 8888）/ `pansou-web`（前后端集成版）。数据落盘只有搜索缓存目录（`CACHE_PATH`，默认 `./cache`）。

**重要更正**：老版本曾有 Redis/内存两种缓存模式（`CACHE_TYPE=redis/memory`）。**当前版本已彻底移除 Redis**，改为"分片内存 + 分片磁盘"二级缓存，全代码无任何 Redis 依赖（go.mod 可证）。家用 NAS 单管理员场景 `docker run` 一条命令即起。

**代理**：访问 t.me 被墙时需配 `PROXY`（支持 socks5/http，兼容标准 `HTTPS_PROXY`/`HTTP_PROXY`）。内置 t.me 可达性后台探测，不可达时直接跳过 TG 阶段，探测结论暴露在 `/api/health` 的 `tg` 字段。

## 2. 搜索 API 完整契约

### 2.1 端点总览（api/router.go）

| 方法+路径 | 用途 | 认证 |
|---|---|---|
| `POST/GET /api/search` | 搜索（核心） | 默认关 |
| `POST /api/check/links` | 批量检测网盘链接有效性 | 默认关 |
| `GET /api/health` | 健康检查 + 插件/频道清单 + 存活观测 | 永远公开 |
| `POST /api/auth/login` `/verify` `/logout` | JWT 认证（`AUTH_ENABLED=true` 时才有意义） | 公开 |

认证默认关闭；开启后除 login/logout/health 外都要 `Authorization: Bearer <token>`。CORS 全开放。**无内置限流**。

### 2.2 `POST /api/search` 请求参数（Content-Type: application/json；POST 体上限 1MiB，channels 上限 512）

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `kw` | string | 必填 | 搜索关键词 |
| `channels` | string[] | 服务端 `CHANNELS` 配置 | 指定本次搜索的 TG 频道 |
| `conc` | number | 频道数+插件数+10 | 并发数（只影响 TG 工作池） |
| `refresh` | bool | false | 强制刷新，跳过所有缓存 |
| `res` | string | `merge` | `all`=完整、`results`=仅 results、`merge`=仅 merged_by_type |
| `src` | string | `all` | `tg`/`plugin`/`all` |
| `plugins` | string[] | 全部已启用插件 | 指定插件名 |
| `cloud_types` | string[] | 全部 | 过滤网盘类型：`baidu/quark/aliyun/guangya/tianyi/uc/mobile/115/pikpak/xunlei/123/magnet/ed2k` |
| `ext` | object | `{}` | 透传给插件的自定义参数（影响插件缓存键） |
| `filter` | object | 无 | `{"include":["合集"],"exclude":["预告"]}`；include 是 OR，exclude 任一命中剔除；对 merged_by_type 匹配 `note` 字段 |

GET 方式参数同名，列表用英文逗号分隔，`ext`/`filter` 传 JSON 字符串（需 URL 编码）。

### 2.3 响应（统一包装 `{code, message, data}`，成功 code=0）

```json
{
  "code": 0, "message": "success",
  "data": {
    "total": 15,
    "merged_by_type": {
      "quark": [
        { "url": "https://pan.quark.cn/s/xxxx", "password": "3a5f",
          "note": "凡人修仙传 4K全集", "datetime": "2023-06-10T15:30:22Z",
          "source": "tg:Quark_Movies", "images": ["...jpg"] }
      ],
      "aliyun": [ ... ]
    }
  }
}
```

- **`merged_by_type`**：`map[网盘类型][]MergedLink`。`MergedLink`：`url`、`password`（提取码可空）、`note`（资源标题）、`datetime`（发布时间）、`source`（`tg:频道名`/`plugin:插件名`）、`images`（TG 带图时，可当封面候选）。链接按 URL 全局去重，同 URL 保留时间最新的。
- **`results`**（`res=results/all`）：TG 消息粒度 `SearchResult`（message_id/unique_id/channel/title/links[]/tags/images）。
- `res=merge`（默认）时 `results` 因 omitempty 不出现——**PanKeeper 前端表格直接吃 `data.merged_by_type` 即可**。
- 错误：`{"code":400,...}` HTTP 400；`{"code":500,"message":"搜索失败: ..."}`；认证失败 HTTP 401。

### 2.4 `/api/check/links`（可选对接）
`{"items":[{"disk_type":"quark","url":"...","password":"..."}]}` → `{results:[{...,state,cache_hit,...}]}`。
`state`: `ok/bad/locked/unsupported/uncertain`；单次上限 256 条。服务端缓存检测结果：**ok 24h、bad 6h、locked 12h、unsupported 24h、uncertain 30 分钟**。可用于"转存前验链"。

### 2.5 `/api/health`
`status/auth_enabled/plugins_enabled/plugins[]/channels[]/liveness/tg`。`liveness` 是每插件/频道近 20 轮产出与报错统计——**PanKeeper 可轮询它展示"搜索源健康度"**。

## 3. 数据来源

### 3.1 TG 频道
- 请求即爬：`GET https://t.me/s/{channel}?q={keyword}`，单频道超时默认 **4 秒**（`TG_CHANNEL_REQUEST_TIMEOUT_SECONDS`），响应体上限 2MB。
- 频道列表**只能通过环境变量 `CHANNELS` 配置**（逗号分隔），默认仅 `tgsearchers7`；改了要重启。README supervisor 段有官方维护的 ~60 个影视向频道清单，docker-compose.yml 有 ~111 个全量清单，可直接抄。
- 全部频道并发抓取；超时频道由**后台补齐**（`TG_BACKFILL_ENABLED=true` 默认开），补齐结果合并写回缓存——**首次搜索可能不全，稍后再搜同一词结果更多**。
- 解析规则版本号 `v2` 编入缓存键，t.me 改版旧缓存自动失效。

### 3.2 插件源
- 插件 = 内嵌站点爬虫，实现 `AsyncSearchPlugin` 接口；**只有 `ENABLED_PLUGINS` 环境变量点名的才加载**（不设置 = 零插件，坑，必须显式配）。
- 优先级 1~5：1/2 级高质量（排序得分 +1000/+500）、3 级普通、4/5 低（-200）。
- **异步模式**：每插件只有 **4 秒同步窗口**（`ASYNC_RESPONSE_TIMEOUT`）；窗口内没回来先返回部分结果，后台继续抓完**合并进主缓存**。插件 HTTP 超时 `PLUGIN_TIMEOUT` **代码默认 10 秒**（2026-09-25 从 30s 下调；README 表格还写 30，以代码为准）。
- 插件层进程内缓存 TTL `ASYNC_CACHE_TTL_HOURS` 默认 1 小时，用到 80% TTL 时后台预刷新。

## 4. 缓存机制

**无 Redis。两级缓存 = 分片内存 + 分片磁盘**（util/cache/enhanced_two_level_cache.go）：

- **缓存内容**：关键词 → `[]SearchResult`（合并去重前的消息级结果），GOB 序列化。TG 与插件两套键空间，键为 MD5：
  - TG：`md5("tg:v2:{小写关键词}:{频道列表哈希}")`
  - 插件：`md5("plugin:{小写关键词}:{插件列表哈希}:{ext摘要}")`
  - 即**缓存粒度 = "关键词+来源组合"**，同一关键词不同 channels/plugins 参数各存一份。
- **内存层**：分片 LRU，容量 = `CACHE_MAX_SIZE` 的 60%（默认 100MB→60MB），最多 5000 条。
- **磁盘层**：分片文件缓存，总上限 `CACHE_MAX_SIZE`（默认 100MB），批量合并落盘，优雅退出强制 flush。
- **读取**：内存未命中读磁盘，磁盘命中按**剩余 TTL** 回填内存（不重新计时）。
- **TTL**：主缓存 `CACHE_TTL` 默认 **60 分钟**。结果按"完整度"决定写不写：全失败不写、有超时也照写。写入前与已有条目**合并（只增不减）**，缓存结果集随后台补齐单调变厚。
- `refresh=true` 跳过主缓存与插件缓存直接重搜。

**实际行为注意**：4 秒异步窗口 + 后台补齐 → **同一关键词的首次搜索结果是"部分"的，60 分钟内再搜会命中缓存且更全**。

## 5. 对 PanKeeper 后端的对接建议

### 5.1 部署拓扑
```
浏览器 → PanKeeper 后端(FastAPI, :8000) → pansou 容器(:8888, 仅监听 NAS 内部)
                                        └→ t.me（经 PROXY）/ 各插件站点
```
- pansou 单容器同机：`docker run -d -p 127.0.0.1:8888:8888 -v pansou-cache:/app/cache -e CHANNELS=<清单> -e ENABLED_PLUGINS=<清单> [-e PROXY=socks5://192.168.2.77:7890] ghcr.io/fish2018/pansou:latest`。认证不开，端口绑内网。
- 后端代理转发 `POST /api/search`（推荐 POST）+ `GET /api/health`；`/api/check/links` 可选透传。

### 5.2 请求与超时
- 后端转发超时建议 **≥15 秒**：无缓存首搜典型 4~10 秒，命中缓存 <100ms。
- 固定 `res=merge`；按 PanKeeper 支持的转存目标传 `cloud_types`；`filter.exclude:["预告","花絮","预告片"]` 做影视向初筛（匹配 `note`）。
- "重新搜"按钮透传 `refresh=true`。

### 5.3 后端要不要再加一层缓存？
**建议加，但定位是"历史记录/离线复用"，不是性能缓存**：
- pansou 的 60 分钟缓存只在其进程里，且首搜结果偏少、随后台补齐变多。PanKeeper（SQLite）把每次搜索的 `merged_by_type` 按 `(关键词, cloud_types)` 存"搜索历史"，TTL 24~72 小时（影视链接时效远长于 60 分钟），翻历史零等待；点"重新搜索"才回源更新。
- 不要做短 TTL 重复缓存——叠两层只会让"后台补齐变多"的新结果看不到。
- 前端交互：首搜展示后，隔 3~5 秒静默再查一次同一关键词（走 pansou 缓存近乎免费），把后台补齐的增量合并进列表。

### 5.4 频道/插件配置放哪
只能放 **pansou 容器环境变量**（`CHANNELS`/`ENABLED_PLUGINS`），改需重启。PanKeeper 后端不解析不下发频道列表，固化在 compose 文件即可。

### 5.5 转存链路衔接
`merged_by_type[*]` 的 `url+password` 直接喂转存模块；`note` 作初始标题（清洗清晰度/集数后缀后可当刮削搜索词）；`source` 展示来源；`datetime` 排序（pansou 已按时间+插件等级排好序，直接沿用）。

## 6. License
**MIT**——可自由复制修改闭源使用，保留版权声明即可。README 另有"勿用于盈利"声明，家用非商用无碍。

## 附：关键文件索引
| 关注点 | 文件 |
|---|---|
| 路由/搜索入口/参数 | `api/router.go`、`api/handler.go`、`api/limits.go`、`api/filter.go` |
| 请求/响应模型 | `model/request.go`、`model/response.go`、`model/check.go` |
| 搜索编排/TG 抓取/合并去重/排序 | `service/search_service.go` |
| 二级缓存/缓存键/落盘 | `util/cache/enhanced_two_level_cache.go`、`cache_key.go`、`delayed_batch_write_manager.go` |
| 插件框架与异步 4 秒窗口 | `plugin/plugin.go` |
| 全部环境变量与默认值 | `config/config.go`（PLUGIN_TIMEOUT 以代码为准 10s） |
