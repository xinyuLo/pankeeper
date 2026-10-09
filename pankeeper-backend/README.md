# PanKeeper 后端

网盘转存管理工具的 Python 后端：聚合搜索代理（pansou）→ 三网盘转存队列 → 触发 QMS 刮削 → 记录快照。
配套前端：`PanKeeper-vue3`（接口契约见其仓库 `docs/api-contract.md`）。


## 技术栈

FastAPI · SQLAlchemy 2.0 · SQLite(WAL) · httpx + requests · PyJWT · APScheduler（M3 接入定时任务）

## 快速开始

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Linux/Mac: .venv/bin/pip
.venv/Scripts/python run.py --port 8000
# 文档：http://127.0.0.1:8000/docs
# 默认管理员 admin / admin#123（首启生成，请立即在「系统设置 → 账号安全」修改）
```

数据落在 `data/pankeeper.db`（WAL），密钥自动生成在 `data/*.key`（**记得备份/加入 .gitignore，已忽略**）。

## 已实现（M1–M3）

| 领域 | 说明 |
| --- | --- |
| 认证 | 单管理员 JWT（会话有效期 1/7/30 天可配），401 全局拦截 |
| 搜索代理 | pansou `GET /api/search`（res=merge），类型映射 aliyun→ali，不支持转存的类型（magnet 等）直接丢弃 |
| 转存队列 | 阶段机 transfer→waitqms→qms→waitstrm→strm→done；线程数(1-4)/间隔/延迟可配即时生效；完成保留 30 分钟出队；内存权威 + SQLite 快照，重启恢复（中断任务回队，去重保证幂等）；SSE `/api/queue/events` + 轮询兜底 |
| 夸克 adapter | 分享解析（pwd_id/提取码/子目录锚点）→ stoken（兼死活探测，失效熔断）→ 清单分页（含子目录/根目录单文件夹自动下钻）→ 逐级建目录 → 100/批转存 + task 轮询 → 按规则重命名；文件名去重（目标名参与比对）；每请求 0.8s 限速门 + 连败熔断 |
| QMS 触发 | 转存成功且有新增文件 → 按目标目录前缀匹配转存配置的 qms_id/strm_id → 延迟可配 → `POST /api/scrape/pathes/start` / `sync/path/start`（X-API-Key 鉴权）；**详细刮削日志/队列在 QMS 侧**，本服务只记触发快照 |
| 转存记录 | 快照制（结果/QMS·STRM 状态/执行日志），手动转存不接推送（交互契约） |
| 目录树缓存 | 按目录 LRU + TTL + 水位降级 + singleflight + 命中率统计（LitePan 同构模型，原创实现） |
| 网盘连接 | Cookie 加密存库（Fernet），接口只回状态绝不回明文；保存即验证 |
| 每日探活 | 每日定时验证 Cookie（时间可配/可关），失效标记 + 推送（仅由好变坏）；顺路刷新容量/会员缓存 + PanSou 健康度 |
| 自动转存 | PaTask cron 调度（APScheduler，5 段 cron）：触发→校验/输入侧去重→入队（source=auto）；完成回写 last_status + RunHistory；分享失效自动熔断（ban_reason），更新链接自动恢复；`POST /api/pa/tasks/:id/run` 立即运行、`GET /api/pa/next-runs` 下次执行时间 |
| 百度 adapter | 完整转存链路（原创实现）：提取码验证（BDCLND）→ 分享页 yunData 解析 → /share/list 子目录递归 → MD5 优先 + 文件名去重（对比目录 compare_path 可配）→ 逐级建目录（errno 12=已存在）→ share/transfer（-65 等 10s 整组重试，fsidlist ≤500/组）→ filemanager 重命名；死链熔断/Cookie 失效分流 |

| 115 adapter | 完整转存链路（webapi 路线，不依赖 p115client）：分享解析（115/115cdn/anxia + receive_code）→ share/snap 翻页到 count（文件夹优先 fid 可整目录接收）→ 文件名去重（对比目录 compare_path 可配）→ files/add 逐级建目录 → share/receive 整组接收（「文件已接收」判幂等成功）；**验证码=已被风控，停下报警不硬闯**；每请求 ≥1s 串行门 + 406 退避 |

**M3 完成**。M4 剩余：Server 酱推送时机打磨、搜索历史/健康度页、记录清理/重试。

## M3 实现说明

- **调度与转存解耦**：pa_scheduler 只做「到点入队」，不发任何网盘请求——转存全走队列引擎
  （限速/熔断/去重由 adapter 与 RateGate 负责），调度器自身故障不产生风控暴露。
- **输入侧去重**：同一分享链接还有 wait/run 任务在队时，cron 到点自动跳过本次。
- **排除清单**：PaTask.exclude_json 存文件名列表 → TaskSpec.exclude_names 按 basename 过滤
  （目录条目同样适用，可整目录排除）。
- **MD5 去重基线**：百度分享文件自带 md5，优先与 compare_path（空则 save_dir）现有文件 md5
  集合比对——解决「改名后重复转存」；对比目录读取失败时降级为不去重（任务不炸）。

## 环境踩坑记录（重要）

1. **pansou-web 镜像（willowgod/pansou-web）对接**：`POST /api/search` 恒返回空（必须 GET）；httpx 发出的
   请求稳定 502 且根本没到容器（容器 nginx 无日志），**requests/urllib/curl 同 URL 正常**——本仓库 pansou
   客户端因此用 requests。
2. Git Bash 里 curl 发中文 JSON body 会按本地编码发出导致服务端解析失败：测试时用 `python` 写 UTF-8
   临时文件再 `--data-binary @file`。
3. SQLite 必开 WAL + busy_timeout（Web 读轮询与任务写并发）。

## 前端联调交接（待前端侧完成）

- `.env` 改 `VITE_USE_MOCK=false`、`vite.config.ts` 的 `/api` proxy target 指向 `http://127.0.0.1:8000`；
- **搜索入队必须带真实链接**：`pkQueue.enqueue` 入参需扩展 `share_url` / `share_code` / `include_subdirs`
  （真实转存没有分享链接无从谈起，mock 时代不需要）；
- 前端队列引擎（本地 600ms tick 模拟）切到消费后端：轮询 `GET /api/queue/state` 或订阅 SSE
  `GET /api/queue/events`，`QueueBoard/QueueBadge` 渲染层不用动。

## Docker 部署（NAS，单容器全家桶）

**前后端打成一个镜像**：多阶段构建（node 编译前端 → python 运行后端 + 托管前端静态包），单端口 8000，无 nginx 无反代。

```bash
# NAS 上两个仓库同级克隆：
#   /vol2/1001/disk2/workspace/pankeeper-backend
#   /vol2/1001/disk2/workspace/pankeeper-vue3
cd pankeeper-backend
docker compose up -d --build
# 前台：http://<NAS_IP>:8000/   API 文档：http://<NAS_IP>:8000/docs
```

**持久化说明（重要）**：
- 所有配置与数据都在容器内 `/app/data`（SQLite `pankeeper.db` + 加密密钥 `jwt.key`/`cred.key`），已挂 volume `pankeeper-data`——升级镜像、重建容器**不丢任何配置**。
- 网盘连接（加密凭据）、系统设置、转存配置、队列配置、自动任务、转存记录：全部落 SQLite。
- 目录树缓存/分享清单缓存是**内存态**（重启即清，重新浏览自动重建——设计如此）。
- **备份 = 备份 volume**（或直接拷 `data/` 目录）。⚠️ 别只拷 db 不拷两个 `.key` 文件——密钥丢了加密凭据解不开。
- 时区已在镜像里固定 Asia/Shanghai（记录时间不会差 8 小时）。

首次启动：默认管理员 `admin / admin#123`（日志里也会打印），登录后先改密码。

## 测试

```bash
.venv/Scripts/python -m pytest tests/ -q
```
