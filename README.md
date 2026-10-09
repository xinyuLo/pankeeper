# PanKeeper

自托管的网盘转存管理工具：把「聚合搜索 → 转存到自己的网盘 → 媒体库刮削 → 生成 STRM → 刷新 Emby」串成一条可观测的流水线。

单容器、单端口、一个 SQLite 文件，不需要外部数据库。Web 界面适配桌面与手机。

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

**方式一：预构建镜像（推荐）**

```bash
mkdir pankeeper && cd pankeeper
curl -o docker-compose.yml https://raw.githubusercontent.com/xinyuLo/pankeeper/main/docker-compose.yml
docker compose pull && docker compose up -d
```

**方式二：源码构建**（仓库自带前后端同构 Dockerfile，前端构建产物由 FastAPI 托管，同源零反代）：

```bash
git clone https://your-git-host/your-name/pankeeper-git.git
cd pankeeper-git
docker compose up -d --build
```

`docker-compose.yml`（放在仓库根目录）：

```yaml
services:
  pankeeper:
    build:
      context: ./pankeeper-backend
      additional_contexts:
        frontend: ./pankeeper-vue3
    container_name: pankeeper
    restart: unless-stopped
    ports:
      - "8031:8000"
    volumes:
      - ./data:/app/data
```

启动后访问 `http://<主机IP>:8031`，默认账号 `admin / admin#123`（**登录后请立即修改**，系统设置 → 账号安全）。

数据（SQLite + 加密密钥）全部在 `data/` 目录，备份它即备份一切。

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
