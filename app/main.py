"""FastAPI 装配：路由挂载 + 前端静态托管 + 启动初始化。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import accounts, auth, cache_api, dd, pa, queue_api, records, search, settings as settings_api
from .api.auth import ensure_admin
from .db import init_db

# Docker 镜像里的前端静态包（Dockerfile 阶段 1 构建产物 COPY 到 ./web）；
# 本地开发无此目录，自动跳过——开发期前端仍走 vite :5173。
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app() -> FastAPI:
    app = FastAPI(title="PanKeeper API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # 开发期 vite
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)  # 前端包 1.5MB，gzip 必要

    app.include_router(auth.router)
    app.include_router(settings_api.router)
    app.include_router(accounts.router)
    app.include_router(dd.router)
    app.include_router(search.router)
    app.include_router(queue_api.router)
    app.include_router(records.router)
    app.include_router(pa.router)
    app.include_router(cache_api.router)

    if WEB_DIR.is_dir():
        # ---- 前端静态托管（单容器模式）：hash 路由只需 / 与静态资产 ----
        app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def _index():
            return FileResponse(WEB_DIR / "index.html")

        _STATIC_NAMES = ("favicon.svg", "manifest.webmanifest")

        for name in _STATIC_NAMES:
            p = WEB_DIR / name
            if p.is_file():

                def _make(fp: Path):
                    def _serve():
                        return FileResponse(fp)
                    return _serve

                app.get("/" + name, include_in_schema=False)(_make(p))

        icons = WEB_DIR / "icons"
        if icons.is_dir():
            app.mount("/icons", StaticFiles(directory=icons), name="icons")

    @app.on_event("startup")
    def _startup():
        init_db()
        ensure_admin()
        from .queue.engine import get_engine

        get_engine()  # 恢复快照 + 启动 worker
        print("[init] PanKeeper 后端就绪" + ("（含前端静态托管）" if WEB_DIR.is_dir() else ""))

    return app


app = create_app()
