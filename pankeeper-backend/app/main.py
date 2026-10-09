from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import accounts, auth, cache_api, dd, drive_logs, pa, qms_api, queue_api, recognize, records, search, settings as settings_api
from .api.auth import ensure_admin
from .db import init_db
from .deps import is_local_request
from .security import parse_token

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

PUBLIC_PATHS = {"/api/auth/login"}

DOC_PREFIXES = ("/docs", "/redoc", "/openapi.json")

def _needs_auth(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return False
    return path.startswith("/api/") or path.startswith(DOC_PREFIXES)

def _token_of(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return request.query_params.get("token", "")

def create_app() -> FastAPI:
    app = FastAPI(title="PanKeeper API", version="0.1.0")

    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        if _needs_auth(request.url.path):

            if not is_local_request(request) and not parse_token(_token_of(request)):
                return JSONResponse(status_code=401, content={"detail": "登录凭证不存在或已过期"})
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    app.include_router(auth.router)
    app.include_router(settings_api.router)
    app.include_router(accounts.router)
    app.include_router(dd.router)
    app.include_router(search.router)
    app.include_router(qms_api.router)
    app.include_router(queue_api.router)
    app.include_router(records.router)
    app.include_router(pa.router)
    app.include_router(cache_api.router)
    app.include_router(drive_logs.router)
    app.include_router(recognize.router)

    if WEB_DIR.is_dir():

        app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def _index():
            # ⚠️ index.html 绝不能进浏览器缓存：每次构建产物文件名 hash 都会变，
            # 客户端缓存旧 HTML 会引用已不存在的旧 CSS/JS → 整页裸样式。
            # no-cache = 每次协商校验（etag 变了就拿新的）；/assets/* 文件名自带 hash，可放心缓存
            return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

        _STATIC_FILES = (
            ("favicon.svg", None),
            ("favicon.ico", "image/x-icon"),
            ("manifest.webmanifest", None),
        )

        for name, ctype in _STATIC_FILES:
            p = WEB_DIR / name
            if p.is_file():

                def _make(fp: Path, ct: str | None):
                    def _serve():
                        return FileResponse(fp, media_type=ct) if ct else FileResponse(fp)
                    return _serve

                app.get("/" + name, include_in_schema=False)(_make(p, ctype))

        icons = WEB_DIR / "icons"
        if icons.is_dir():
            app.mount("/icons", StaticFiles(directory=icons), name="icons")

        @app.get("/{full_path:path}", include_in_schema=False)
        def _spa_fallback(full_path: str):

            candidate = (WEB_DIR / full_path).resolve() if full_path else None
            if candidate and candidate.is_file() and str(candidate).startswith(str(WEB_DIR.resolve())):
                return FileResponse(candidate)
            # ⚠️ index.html 绝不能进浏览器缓存：每次构建产物文件名 hash 都会变，
            # 客户端缓存旧 HTML 会引用已不存在的旧 CSS/JS → 整页裸样式。
            # no-cache = 每次协商校验（etag 变了就拿新的）；/assets/* 文件名自带 hash，可放心缓存
            return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.on_event("startup")
    def _startup():
        init_db()
        ensure_admin()
        from .queue.engine import get_engine

        get_engine()

        from .services.healthcheck import start_scheduler

        start_scheduler()

        from .services.pa_scheduler import start_pa_scheduler

        start_pa_scheduler()
        print("[init] PanKeeper 后端就绪" + ("（含前端静态托管）" if WEB_DIR.is_dir() else ""))

    return app

app = create_app()
