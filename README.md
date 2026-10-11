# PanKeeper

自托管的网盘转存管理工具：把「聚合搜索 → 转存到自己的网盘 → 媒体库刮削 → 生成 STRM → 刷新 Emby」串成一条可观测的流水线。

单容器、单端口、一个 SQLite 文件，不需要外部数据库。Web 界面适配桌面与手机。

> 📘 **新用户请从[《使用说明》](docs/USAGE.md)开始**：全部页面的功能与操作详解——搜索转存 / 自动转存 / 转存配置 / 网盘连接 / 系统设置 / 日志管理，以及「一次转存背后发生了什么」与常见问题。

## 功能特性

- **多网盘适配**：百度网盘 / 夸克 / 115，统一抽象（列目录、分享解析、转存、改名、目录管理），内置每账号限速闸与连败熔断
- **搜索转存**：对接 [pansou](https://github.com/fish2018/pansou) 聚合搜索，搜到资源直接勾选文件转存；支持建壳转存、剥壳平铺、更名、文件检测
- **自动转存**：cron 定时任务监控分享链接，正则排除 + MD5 去重 + 双目录去重基线，转存历史/运行详情完整落库
- **媒体库联动**：QMS（qMediaSync）与 LitePan 双后端——刮削触发、结果回填、STRM 等刮削完成后生成、Emby 刷库；转存 → 刮削 → 同步全链路可跟踪
- **TMDB 识别**：文件名自识别（年份容错、剧集解析、歧义候选卡片人工挑选），推送带海报/简介；连通支持 HTTP 代理与 SOCKS5，host 模式可按域名直连指定 IP（多 IP 自动择优）
- **推送通知**：Server 酱富文本推送，推送历史与正文详情
- **队列引擎**：单工串行 + 间隔节奏配置，前后端实时状态同步（SSE）
- **缓存预热**：目录树缓存策略可配，支持一键全量预热与实时进度

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python 3.12 · FastAPI · SQLAlchemy · SQLite · APScheduler |
| 前端 | Vue 3 · TypeScript · Vite · Ant Design Vue |
| 部署 | Docker Compose（单容器，前端由后端静态托管） |

## 部署

镜像发布在 GHCR 与 Docker Hub（amd64 / arm64 双架构，push 自动构建）：

- GHCR：`ghcr.io/xinyulo/pankeeper:latest`
- Docker Hub：`7yueyue/pankeeper:latest`

### 方式一：docker run 直接运行

```bash
docker run -d \
  --name pankeeper \
  --restart unless-stopped \
  -p 8031:8000 \
  -e TZ=Asia/Shanghai \
  -v /your/path/pankeeper/data:/app/data \
  ghcr.io/xinyulo/pankeeper:latest
```

**数据映射说明**：容器的 `/app/data` 是唯一的持久化目录，必须挂载到宿主机，否则容器重建后数据全部丢失。目录里包含：

| 文件 | 内容 |
| --- | --- |
| `pankeeper.db` | SQLite 主库：账号、系统设置、转存配置、转存记录 |
| `jwt.key` | 登录令牌签名密钥 |
| `cred.key` | 网盘凭据加密密钥（Fernet） |

> ⚠️ 备份 `data` 目录时请**三个文件一起备份**——只备份数据库不备份两个密钥文件，已保存的网盘凭据将无法解密。

用 Docker Hub 镜像的话把最后一行换成 `7yueyue/pankeeper:latest` 即可。

### 方式二：docker compose

新建 `docker-compose.yml`（仓库根目录已自带一份，可直接用）：

```yaml
services:
  pankeeper:
    image: ghcr.io/xinyulo/pankeeper:latest   # Docker Hub 用户可改为 7yueyue/pankeeper:latest
    container_name: pankeeper
    restart: unless-stopped
    ports:
      - "8031:8000"
    volumes:
      - ./data:/app/data
    environment:
      - TZ=Asia/Shanghai
```

```bash
mkdir data && docker compose up -d
```

启动后访问 `http://<主机IP>:8031`，默认账号 `admin / admin#123`（**登录后请立即修改**，系统设置 → 账号安全）。

数据（SQLite + 加密密钥）全部在 `data/` 目录，备份它即备份一切。

### 源码构建（可选）

monorepo 自带前后端同构 Dockerfile（前端构建产物由 FastAPI 托管，同源零反代），前端源码通过额外构建上下文注入：

```bash
docker buildx build \
  --build-context frontend=./pankeeper-vue3 \
  -t pankeeper:local ./pankeeper-backend
```

## 本地开发

```bash
# 后端（默认 127.0.0.1:8000）
cd pankeeper-backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Linux: .venv/bin/pip
.venv/Scripts/python run.py --port 8000

# 前端（5173，/api 由 vite 代理到 8000）
cd pankeeper-vue3
npm install
npm run dev
```

前端默认走真实后端；纯离线联调可把 `.env.development` 里 `VITE_USE_MOCK` 置为 `true`。

## 首次配置

1. **网盘连接**：登录后在各网盘页扫码/粘贴 Cookie 建立账号
2. **搜索源**：系统设置 → 搜索源，填 pansou 地址
3. **媒体库联动**：系统设置 → 联动后端，选 QMS 或 LitePan 并填连接参数
4. **推送**：系统设置 → 推送通知，填 Server 酱 SendKey
5. **TMDB**：系统设置 → 代理配置，填 API Key（[themoviedb.org](https://www.themoviedb.org/) 免费申请），按网络环境选代理或 Host 直连

## 目录结构

```
pankeeper-git
├── pankeeper-backend     # FastAPI 后端（adapters 网盘适配 / api 接口 / services 业务 / transfer 转存链路）
├── pankeeper-vue3        # Vue3 前端（views 页面 / api 双模式接口层 / queue 队列前端引擎）
└── docker-compose.yml
```

## 支持作者

如果这个项目对你有帮助，请作者喝杯咖啡吧 ~

<p align="center">
  <img src="assets/wechat.jpg" alt="微信赞赏" width="260" />
  &nbsp;&nbsp;&nbsp;&nbsp;
  <img src="assets/alipay.jpg" alt="支付宝收款" width="260" />
</p>

## 免责声明

本项目仅用于个人学习与自托管场景，请遵守各网盘服务商的用户协议，自行承担使用风险。

## License

[AGPL-3.0](LICENSE)
