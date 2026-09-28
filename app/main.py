"""FastAPI 装配：路由挂载 + 启动初始化。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import accounts, auth, cache_api, dd, pa, queue_api, records, search, settings as settings_api
from .api.auth import ensure_admin
from .db import init_db


def create_app() -> FastAPI:
    app = FastAPI(title="PanKeeper API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # 开发期 vite
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    app.include_router(settings_api.router)
    app.include_router(accounts.router)
    app.include_router(dd.router)
    app.include_router(search.router)
    app.include_router(queue_api.router)
    app.include_router(records.router)
    app.include_router(pa.router)
    app.include_router(cache_api.router)

    @app.on_event("startup")
    def _startup():
        init_db()
        ensure_admin()
        from .queue.engine import get_engine

        get_engine()  # 恢复快照 + 启动 worker
        print("[init] PanKeeper 后端就绪")

    return app


app = create_app()
