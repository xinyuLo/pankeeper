# PanKeeper 后端镜像
# 构建后所有配置/数据都在 /app/data（SQLite + 密钥），挂 volume 即可持久化。
FROM python:3.12-slim

# 时区：记录时间用本地时间（不装 tzdata 的话容器内是 UTC，记录会差 8 小时）
ENV TZ=Asia/Shanghai
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.py .

# 数据目录：SQLite（pankeeper.db）+ 密钥（jwt.key / cred.key）
ENV PK_DATA=/app/data
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
