# PanKeeper

自托管的网盘转存管理工具。把「聚合搜索 → 转存到自己的网盘 → 触发媒体库刮削 → 生成 STRM」串成一条可观测的流水线。

单容器，单端口，一个 SQLite 文件，不需要外部数据库。

> 开发中。目前完成 M1：认证、聚合搜索、转存队列、夸克适配、QMS 联动、记录快照。百度与 115 适配、定时自动转存是下一步。

## 它解决什么问题

手动转存网盘资源是个很烦的过程：搜到链接、打开网盘、建目录、等转存、再去手动刮削、再确认 STRM 有没有生成。中间任何一步失败，就得回来重做，而且做过什么全靠记忆。

PanKeeper 把这条链路收进一个队列：转存、刮削、生成 STRM 自动串联，每一步的状态都有记录，失败了能看到原因，服务重启也不会丢任务。

它面向自己搭服务、自己管媒体库的人，不是一键小白工具——搜索和刮削依赖你已有的 PanSou 与 QMS。

## 功能

| 领域 | 说明 |
| --- | --- |
| 认证 | 单管理员 JWT，会话有效期 1/7/30 天可配，401 全局拦截 |
| 聚合搜索 | 对接 PanSou，一次搜索多个网盘分享源，按可转存类型过滤 |
| 转存队列 | 阶段机 `transfer → waitqms → qms → waitstrm → strm → done`，线程数与间隔可调，重启后恢复中断任务并去重 |
| 网盘适配 | 夸克：分享解析（含提取码与子目录锚点）、子目录自动下钻、逐级建目录、分批转存、按规则重命名、每请求限速门与连败熔断 |
| 刮削联动 | 转存成功且有新增文件时触发 QMS 刮削，成功后再触发生成 STRM，延迟可配 |
| 转存记录 | 快照制：结果、QMS/STRM 状态、执行日志 |
| 目录缓存 | 按目录 LRU + TTL + 水位降级 + singleflight，带命中率统计 |
| 网盘连接 | Cookie 经 Fernet 加密存库，接口只返回状态，绝不回明文；保存时即验证 |

计划中：百度网盘适配、115 网盘适配、定时自动转存（cron）。

## 架构

```
浏览器
  │  HTTP :8000
  ▼
PanKeeper 单容器
  ├── FastAPI  ── 同时托管前端静态包（Vue3 构建产物，同源，无 nginx）
  ├── /api/*      业务接口
  └── /app/data   SQLite (WAL) + 加密密钥
  │
  ├──► PanSou   聚合搜索 · GET /api/search        （自备）
  └──► QMS      刮削 / STRM · POST /api/scrape/... （自备）
```

前后端打进同一个镜像：多阶段构建，Node 编译前端，Python 运行后端并托管静态产物。

## 技术栈

后端 FastAPI · SQLAlchemy 2.0 · SQLite(WAL) · PyJWT · httpx / requests · APScheduler

前端 Vite 5 · Vue 3.5 · TypeScript · Ant Design Vue 4 · Pinia · Vue Router（hash 模式）

## 快速开始

### 方式一：直接用镜像

```yaml
services:
  pankeeper:
    image: git.xinyunas.cn:8443/xinyu/pankeeper:latest
    container_name: pankeeper
    restart: unless-stopped
    ports:
      - "8031:8000"
    volumes:
      - pankeeper-data:/app/data
    environment:
      TZ: Asia/Shanghai

volumes:
  pankeeper-data:
```

```bash
docker compose up -d
```

镜像放在自建的 Gitea 容器仓库上，走 HTTPS，不需要登录也不需要配 `insecure-registries`。不过那是个人自建服务，可用性没有保证；长期使用建议按方式二自己构建。

### 方式二：从源码构建

仓库是 monorepo，前后端同级目录。Dockerfile 通过 `--build-context frontend=../pankeeper-vue3` 取前端源码，所以在后端目录里直接 compose 即可，不用改任何路径：

```bash
git clone http://192.168.2.77:8029/xinyu/pankeeper.git
cd pankeeper/pankeeper-backend
docker compose up -d --build
```

启动后访问 `http://localhost:8031`（或把 8031 换成你要的端口）。

## 默认账号

首次启动自动生成管理员账号：

- 用户名 `admin`
- 密码 `admin#123`

登录后请立刻到「系统设置 → 账号安全」修改。

## 配置外部依赖

PanKeeper 本身不产生内容，需要两个外部服务配合，地址都在应用内的「系统设置」里填写：

| 服务 | 用途 | 对接接口 |
| --- | --- | --- |
| PanSou | 聚合搜索 | `GET /api/search` |
| QMS | 刮削 / STRM 生成 | `POST /api/scrape/pathes/start`、`POST /api/sync/path/start`（X-API-Key 鉴权） |

没配置之前界面能正常登录和改设置，但搜索与转存是空的。

## 数据与备份

所有持久化状态都在容器的 `/app/data` 下：

| 文件 | 内容 |
| --- | --- |
| `pankeeper.db` | SQLite 主库（WAL 模式）：账号、系统设置、转存配置、队列快照、转存记录 |
| `jwt.key` | 登录令牌签名密钥 |
| `cred.key` | 网盘凭据的加密密钥（Fernet） |

备份整个 volume 就等于备份整个系统，升级镜像或重建容器都不丢配置。

只备份 `pankeeper.db` 而不备份两个 `.key` 文件，已保存的网盘凭据将无法解密——这一点务必注意。

目录树缓存和分享清单缓存是内存态，重启即清空，重新浏览会自动重建，属于设计如此。

## 目录结构

```
pankeeper-backend/          Python 后端（FastAPI）+ Dockerfile + 单容器 compose
pankeeper-vue3/             前端（Vue3 + Vite）
  ├── docs/api-contract.md    前后端接口契约（后端按此实现）
  └── docs/research/          同类项目的接口调研记录
```

## 开发

后端：

```bash
cd pankeeper-backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt    # Linux / macOS 用 .venv/bin/pip
.venv/Scripts/python run.py --port 8000
# 接口文档 http://127.0.0.1:8000/docs
```

前端：

```bash
cd pankeeper-vue3
npm install
npm run dev        # http://localhost:5173
npm run build
```

前端开发态默认使用内置 mock 数据。要接真实后端：把 `.env` 里的 `VITE_USE_MOCK` 改成 `false`，并把 `vite.config.ts` 里 `/api` 的 proxy target 指向后端地址。具体端点的实现要求见 `pankeeper-vue3/docs/api-contract.md`。

后端测试：

```bash
cd pankeeper-backend
.venv/Scripts/python -m pytest tests/ -q
```

## 实现说明

本仓库为原创实现。参考项目（bdSavePro、quark-auto-save 为 AGPL-3.0，LitePan 为 PolyForm 非商用许可）的代码与注释一律未复制，仅依据其公开的 API 端点、参数与状态码等接口事实自行实现。调研记录存于 `pankeeper-vue3/docs/research/`。

## 许可

AGPL-3.0，全文见 [LICENSE](LICENSE)。
