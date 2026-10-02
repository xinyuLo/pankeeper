# PanKeeper 全家桶镜像：前端构建 + 后端运行，单容器单端口。
# 构建上下文（docker-compose.yml 已配 additional_contexts）：
#   .            = 后端仓库（本目录）
#   frontend     = 前端仓库 PanKeeper-vue3（同级目录）
# 用法：docker compose up -d --build

# ---- 阶段 1：前端构建（node） ----
FROM node:20-alpine AS web
WORKDIR /src
# 国内 NAS 拉依赖走镜像源
ENV NPM_CONFIG_REGISTRY=https://registry.npmmirror.com
COPY --from=frontend package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY --from=frontend . .
RUN npm run build

# ---- 阶段 2：后端运行（python） ----
FROM python:3.12-slim

# 时区：记录时间用本地时间（slim 默认 UTC，不装 tzdata 记录会差 8 小时）
ENV TZ=Asia/Shanghai
# 国内 NAS 直连 deb.debian.org 常卡死（2026-10-02 build 实测挂 3 分钟+），换清华源；
# pip 同理走清华源（npm 已用 npmmirror，同一策略）
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends tzdata \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.py .
# 前端静态包（FastAPI 直接托管，同源零反代）
COPY --from=web /src/dist ./web

# 数据目录：SQLite（pankeeper.db）+ 密钥（jwt.key / cred.key）
ENV PK_DATA=/app/data
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
