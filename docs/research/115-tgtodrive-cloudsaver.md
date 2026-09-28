# 115 转存调研报告：TgtoDrive 与 CloudSaver

> 调研方式：GitHub API + raw 源码逐个读取（走代理）。两仓库开源代码均落后于实际发行版（新版闭源走 docker），但 115 核心转存链路在开源部分完整可读。**两者均 MIT License**。

## 一、walkingddd/TgtoDrive（MIT）

### 1. 定位 / 技术栈
- 网盘文件管理增强：TG 频道/分享链接转存、盘内影视音乐整理（TMDB/MusicBrainz）、STRM 生成、Emby/飞牛反代 302 播放。与 PanKeeper 目标场景高度重合。
- 纯 Python（Flask + requests + **p115client** + p123client + BeautifulSoup + SQLite + schedule + dotenv）。
- Docker（python:3.13-slim）host 网络，Web 管理台 12366；配置 `db/user.env`（Web 页按模板编辑）。main 分支只开源 v6.6.4 快照，发行版 8.6.4 闭源。

### 2. 115 转存链路（`tgto115.py`）

**登录态**：环境变量 `ENV_115_COOKIES` 存整串浏览器 Cookie，无扫码无 UIA。p115client 初始化：
```python
client_115 = P115Client(cookies=COOKIES)
client_115.user_info()   # 验证 cookie 有效性，失败 TG 通知
```

**分享解析**（正则，三个域名）：`r'https?:\/\/(115|115cdn|anxia)\.com\/s\/(\w+)\?password\=(\w+)'` → share_code / receive_code。

**转存 API（不走 p115client，裸调 webapi，类 `Fake115Client`）**：

| 步骤 | 端点 | 说明 |
| --- | --- | --- |
| 取 user_id | `GET https://my.115.com/?ct=ajax&ac=get_user_aq` | 取 `data.uid` |
| 列分享内容 | `GET https://webapi.115.com/share/snap?share_code=X&receive_code=Y&offset=0&limit=20&cid=` | 按 `count` offset 循环翻页到取完 |
| 转存 | `POST https://webapi.115.com/share/receive` | form: `user_id, share_code, receive_code, file_id`（逗号拼接）、`cid`（目标 pid） |

关键细节：
- **file_id 取法**：遍历 share/snap，`cid = item.get('fid', item['cid'])`——**文件夹优先取 `fid`（分享侧目录 id，可整目录接收），文件取 `cid`**，逗号拼接一次提交。
- **去重/幂等**：返回 `state=false` 且 error 含 **"文件已接收，无需重复接收"** 按成功处理；另用 SQLite（`db/TG_monitor-115.db`）记录已处理消息 URL 做输入侧去重。
- 每次转存前固定 `time.sleep(2)`。

**转存后整理**（p115client）：`fs_files_app({"cid","limit":1000,"offset"})` 分页列目录、`fs_move_app({"ids":id,"to_cid":pid}, app="android")` 移动（**带 `app="android"`**）、`fs_delete_app` 删空目录、`recyclebin_clean(password=回收站密码)`。

### 3. 风控 / 限频
无验证码处理，全靠**粗粒度 sleep**：转存前 2s、移动后 0.2s、删除后 0.5s、删目录后 1s、秒传每文件 10s、每轮 60s、TG 轮询默认 5 分钟。无自动失效处理。

### 4. 目录浏览与凭据
`fs_files_app`（115 App 接口）+ `cid/limit/offset` 分页，limit=1000，一页不满即停。Cookie 存 `db/user.env`，**无保活无过期检测**，失效看日志手工换。

## 二、jiangrui1994/CloudSaver（MIT）

### 1. 定位 / 技术栈
聚合搜索（TG 频道）→ 一键转存（115/夸克/天翼/123），多用户 Web。前端 Vue3+TS+Pinia+Element Plus/Vant；后端 Node.js + Express + Sequelize + **SQLite** + inversify.js。Docker 8008，`/app/data`（SQLite）+ `/app/config`。开源仓库停 V0.2.5，新版闭源。

### 2. 115 转存链路（`backend/src/services/Cloud115Service.ts`，147 行）

**登录态**：设置页粘贴 Cookie，明文存 SQLite `user_settings.cloud115Cookie` 列，每次调接口前 `setCookie` 挂到 axios 拦截器。

**小程序 UA 伪装（最有价值的细节）**：
```
User-Agent: ...MicroMessenger/6.8.0 ... MiniProgramEnv/Mac MacWechat/WMPF ...
Referer: https://servicewechat.com/wx2c744c010a61b0fa/94/page-frame.html
xweb_xhr: 1
```

**分享解析**：`/(?:115|anxia|115cdn)\.com\/s\/([^?]+)(?:\?password=([^&#]+))/`（接收码可缺省；没处理"无提取码需先提交"场景）。

**三个 API（`https://webapi.115.com`，均一次性请求无翻页）**：

| 路由 | 上游端点 | 参数 |
| --- | --- | --- |
| `/share/snap` | `GET /share/snap` | `share_code, receive_code, offset=0, limit=20, cid=""`（**limit 20 不翻页，>20 文件只看到前 20——反面教材**） |
| `/files`（目录浏览） | `GET /files` | `aid=1, cid, o=user_ptime, asc=1, offset=0, show_dir=1, limit=50, type=0, format=json, ...`，`item.ns` 过滤文件夹 |
| `POST /share/receive` | 同名 | form: `cid, share_code, receive_code, file_id`（**只传 fids[0]，多选实际只转第一个，已知缺陷**）；不带 user_id |

有趣发现：`/share/snap` 列分享内容**不强制需要登录 Cookie**（公开分享可匿名列），只有浏览自己目录和转存才需要。

### 3. 风控 / 限频 / 凭据
无任何 115 侧限频/验证码处理；30s axios 超时是唯一韧性措施，风控对抗全押小程序 UA。Cookie 无校验无保活，失效表现为上游 `state=false`。多用户共享单例 Service 时 cookie 互相覆盖（单管理员可忽略此坑）。

### 4. 目录浏览交互（FolderSelect.vue）
`cid=0` 根目录开始逐层下钻，面包屑可回跳，选中 emit folderId。每次只列一层只显示文件夹 limit=50 不翻页——**正是 PanKeeper"转存弹窗选目录"的交互参照**。

## 三、对 PanKeeper 的可借鉴点

1. **API 组合（用 p115client 落地）**：
   - 登录态校验：`P115Client(cookies)` + `user_info()`（TgtoDrive 同款，最简可靠）；`user_id` 可用 `client.user_id`，不必调 `my.115.com` 的 get_user_aq。
   - 分享解析：正则 `(?:115|115cdn|anxia).com/s/(\w+)(?:\?password=(\w+))`；提取码缺省时传空 receive_code（遇到需提取码的分享会失败，PanKeeper 补一个"提交提取码"流程）。
   - 列分享内容：`/share/snap` 按 offset 翻页到 `count`（**学 TgtoDrive，别学 CloudSaver 的 limit=20 不翻页**）；**文件夹项优先取 `fid`**。
   - 转存：`POST /share/receive`，payload `user_id + share_code + receive_code + file_id(逗号拼接) + cid(目标pid)`——**一次提交可整目录接收**。
   - 目录浏览（选目录弹窗）：`fs_files_app({cid, limit:1000, offset})` 分页（比 `/files` 网页接口稳）；UI 交互抄 CloudSaver 面包屑 FolderSelect。
   - 转存后搬移：`fs_move_app(..., app="android")`。
2. **去重语义**：error 含"文件已接收，无需重复接收"判成功；SQLite 记已处理分享 URL/消息 ID 做输入侧去重（双层去重）。
3. **限频**：两项目都无智能风控，业界实测就是**低频 + sleep**：转存前 1–2s、批量盘内操作每条 0.2–0.5s。**无需实现 gif 验证码**（两项目都没有——遇到验证码即已被风控，应停下报警而不是硬闯）。
4. **UA**：小程序 UA（MicroMessenger + servicewechat.com Referer）可作 webapi 伪装 profile；用 p115client 时其默认 UA 已针对各接口调好，不必自造。
5. **Cookie 保活（两项目的空白，PanKeeper 自建）**：每日定时 `user_info()` 探活（兼保活流量），失败即 Web 告警 + 标记失效；Cookie 存 SQLite（单机家用可明文，注意文件权限）；115 Cookie 持续使用下有效期较长，轻量探活足够，一期不做扫码登录。
6. **架构借鉴**：CloudSaver 的 `ICloudStorageService` 接口（getShareInfo/getFolderList/saveSharedFile + per-drive 正则路由）适合 PanKeeper 多网盘扩展。
