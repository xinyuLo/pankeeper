# 2026-10-08 PanKeeper 代理配置 tab 与 TMDB host 模式

## 补充批次：host 多行策略（用户拍板「两个都要」）

- **用户实测 host 模式已连通**（配了两个真实优选 IP），tab 位置按用户要求挪到「搜索源」后
  （内部编号 tb1~tb6 与模板块顺序一并对齐，别留名不符实的编号）。
- **运行时 failover**：`_build_clients` 把同域名每个 IP 产出一个候选（按行序、去重、跳空行），
  `_get` 里连接层失败（`httpx.TransportError`：连不上/超时）**自动换下一行**；
  链路通但 401/5xx 换 IP 救不了，直接失败。
- **测试比速**：`ping` 并发（ThreadPoolExecutor）测全部候选（8s 超时），`results` 逐 IP 报耗时，
  `winner`=最快者；前端把 winner 行**置顶为生效行**并自动保存（置顶只动 DOM 顺序+落库，
  后端不写库，避免拿草稿值覆盖用户配置）。单候选/全失败的 message 都带人话明细。
- 提示文案更新：「同域名多行按顺序生效、连不上自动换下一行，点测试会把最快的 IP 置顶」。
- **手机端 tab 适配**（用户截图 4+2 参差换行 → 要求 3 个一排、按钮加宽）：设置页 scoped 媒体查询里
  `.st-card .tabs > div { flex: 1 1 calc((100% - 8px)/3); text-align: center }`——只影响设置页，
  覆盖 pk.css 移动端给横向滚动场景的 `flex: 0 0 auto`；实测 390 宽 3+3 两排整齐拉宽居中。
- **手机端 Hosts 行输入框等宽**（用户截图：IP 180 / 域名 240 换行后宽度参差）：媒体查询里改
  `.host-row` 输入框 `flex: 1 1 100%` 各自独占一整行、删除键 `margin-left: auto` 靠右。
- **Hosts 行卡片化**（用户要求"做成卡片加点背景色有框住感，删除按钮要边框加图标"）：
  `.host-row` 加 `background: var(--surface-2) + border: 1px solid var(--border) + radius/padding`
  （CSS 变量随暗色主题自动翻转）；删除按钮从 `type="text"`（无边框）改默认 danger 按钮
  （红边框）+ `DeleteOutlined` 垃圾桶图标。桌面横排/手机竖排两形态浏览器实测 OK。
- 实测：多假 IP 并发全失败逐 IP 报错、空表降级直连、浏览器点测试红提示全链路 OK；
  winner 置顶路径桩测覆盖（真 TMDB 连不通没法实测 winner，逻辑同 `_ping_one` 汇总分支）。

## 需求（用户口述定稿）

- 系统设置新增「**代理配置**」tab：TMDB API Key、TMDB 代理，外加**连通模式**二选一：
  - **代理模式**（原行为）：走 HTTP 代理，留空直连；
  - **Host 模式**：跟改 hosts 文件一样，一左一右输入框填 **IP / 域名**（可多行、可删），
    后端连 TMDB 时直连指定 IP。
- 搜索源 tab 的 TMDB API Key / 代理两行**删掉**（迁去代理配置 tab）。

## 实现要点

- **后端 `services/tmdb.py`（单口改造）**：所有 TMDB 调用都走 `_client()`，天然一处生效：
  - host 模式 = `base_url=https://{ip}/3` + 显式 `Host: api.themoviedb.org` 头 +
    httpx **`extensions={"sni_hostname": "api.themoviedb.org"}`**（httpcore 1.0.9 支持）——
    **连接走 IP、TLS SNI 与证书校验仍按真域名**，Cloudflare 优选 IP 场景证书是匹配的；
  - `tmdb_skip_tls` 开关（问过用户：公共优选 IP 和自建反代两种都可能用）：
    自建反代证书对不上域名时换 `_insecure_ctx()`（check_hostname=False + CERT_NONE）；
  - hosts 表里没配 api.themoviedb.org 的条目 → **退回普通直连**（空表不能把推送弄瞎）；
  - 图片 URL（image.tmdb.org）由手机端 Server酱拉取，host 表对它不生效（界面有提示）。
- **配置存储**：都放 `settings.qms` 组（`tmdb_mode/tmdb_hosts/tmdb_skip_tls` 新增，
  `get_group` 深合并默认值，老库零迁移）。hosts 存 `[{ip, host}]`。
- **测试按钮**：`POST /settings/tmdb/test`，打最轻的鉴权端点 `GET /configuration`；
  mode/proxy/hosts/skip_tls 传**草稿值**（不等 2s 防抖自动保存），掩码 key 后端回落已保存值；
  失败也是 200 + `{ok:false,message}`，message 带 mode_desc（如「host 模式直连 x.x.x.x」）。
- **前端**：tab 插在「联动后端」后（tb4，账号安全/头像管理顺延 tb5/tb6）；
  host 模式下才显示 Hosts 映射 + 跳过证书校验；代理模式下才显示代理输入框。
- **保存过滤**：qms 的 watch 保存时**剔除 ip/host 全空的行**（占位行不入库）。

## 验证（全过）

- vue-tsc 0 错；桩测试 6 项（client 构造/sni/skip_tls 生效/空表降级/proxy 回归/无 key 文案）。
- 真实接口四场景：host 直连 127.0.0.1 秒拒（IP 覆盖实锤）、skip_tls 同路径、
  proxy 死代理报「经代理 http://127.0.0.1:9」、掩码 key 回落后真连 TMDB 超时
  （本外网机直连不通——正是 host 模式的用武之地）。
- 浏览器实测：新 tab 渲染、模式切换互斥表单、自动保存落库、代理地址回显、空行过滤，均 OK。

## 踩坑

1. **antd select 的 combobox 被 `ant-select-selection-item` span 盖住**，Playwright
   actionability 超时——老坑复现，直接 CUA 坐标点击下拉和选项。
2. **httpx 断言代理别抠内部属性**：0.28 的 `Client._transport` 上没有 `_proxy`/
   `_proxy_url`（httpcore 层也没挂），桩测试断言代理挂载别走这条路，功能性验证即可。
3. IAB `playwright.evaluate` 传**箭头函数字符串**返回 `{}`，要传**真函数对象**。
4. 本机前端依赖落后仓库：新拉的 `markdown-it` 要 `npm install`（vite 报
   "could not be resolved" 就是它）。

## 现场状态

- 本地前后端已起（8000/5173）；库里终态：`tmdb_mode=host`、`tmdb_hosts=[]`、
  原代理地址原样保留、API Key 完好（每次保存重新 fernet 加密，密文会变、可解密不变）。
- **NAS 已部署 `fa15cb7`**（2026-10-08 晚，用户发话后执行）：pankeeper-deploy `git pull` →
  `/vol1/1001/compose/pankeeper` 下 `docker compose up -d --build`，容器重建、站点 200、
  `[pa-sched] 自动任务排期：1 个生效`；镜像内 `tmdb.py` 含 `sni_hostname` 实锤新代码。
  用户已在部署站配好两个优选 IP 实测连通（108.138.246.55 / 18.161.156.50）。
- 坑：外网 SSH kick 命令读输出会 PipeTimeout（nohup 后台任务占着通道）——**部署其实已经
  跑起来了**，别慌，直接另起连接轮询 `/tmp/pk_deploy.log` 即可；NAS 拉 docker.io 元数据
  慢（node/python 镜像 metadata 30~60s），构建全程约 4 分钟属正常。
- **占位示例去真实 IP**（用户指出示例不该写真 IP）：Hosts 的 IP 占位改 127.0.0.1；
  LitePan Webhook 占位 LitePan地址:端口 → 127.0.0.1:端口；TMDB 代理占位 192.168.2.77:7890 →
  http://127.0.0.1:7890（支持 http 协议）。全局 grep 确认无其他真实 IP 占位。
- **TMDB 代理支持 socks5**（用户问支不支持 → 原来不支持）： 的 httpx 改
  （=装 socksio，纯 py 小包）； 代理构造加 try/except——代理串写坏
  按空候选处理（ping 报「代理配置无效」，_get 走 None 兜底），不再 500。socks5:// 与
  socks5h:// 都支持（后者域名解析交给代理，防 DNS 污染）。前端提示文案同步注明两协议。
