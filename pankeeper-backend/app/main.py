"""FastAPI 装配：路由挂载 + 前端静态托管 + 启动初始化。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import accounts, auth, cache_api, dd, drive_logs, pa, qms_api, queue_api, records, search, settings as settings_api
from .api.auth import ensure_admin
from .db import init_db
from .deps import is_local_request
from .security import parse_token

# Docker 镜像里的前端静态包（Dockerfile 阶段 1 构建产物 COPY 到 ./web）；
# 本地开发无此目录，自动跳过——开发期前端仍走 vite :5173。
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# ---- 统一鉴权守卫的边界 --------------------------------------------------
# 只放行登录接口本身（否则谁也登不进来）；/api/* 其余全部要登录态。
PUBLIC_PATHS = {"/api/auth/login"}
# 接口文档一并收口：/docs 不泄露数据，但会把「有哪些接口、要什么参数」印成一张地图。
DOC_PREFIXES = ("/docs", "/redoc", "/openapi.json")


def _needs_auth(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return False
    return path.startswith("/api/") or path.startswith(DOC_PREFIXES)


def _token_of(request: Request) -> str:
    """header 优先；EventSource（SSE）不能带自定义 header，退回 query 参数。"""
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return request.query_params.get("token", "")


def create_app() -> FastAPI:
    app = FastAPI(title="PanKeeper API", version="0.1.0")

    # 鉴权中间件放在其它中间件「内层」：它在最前拦截，而 401 响应仍会穿过
    # CORS/GZip 外层，浏览器才不会把 401 误报成跨域错误。
    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        if _needs_auth(request.url.path):
            # 本机回环豁免（本地开发零负担）；局域网/外网一律要 token
            if not is_local_request(request) and not parse_token(_token_of(request)):
                return JSONResponse(status_code=401, content={"detail": "登录凭证不存在或已过期"})
        return await call_next(request)

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
    app.include_router(qms_api.router)
    app.include_router(queue_api.router)
    app.include_router(records.router)
    app.include_router(pa.router)
    app.include_router(cache_api.router)
    app.include_router(drive_logs.router)

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

        # history 路由 SPA 回退：/login、/dashboard 等前端路径刷新时返回 index.html
        @app.get("/{full_path:path}", include_in_schema=False)
        def _spa_fallback(full_path: str):
            # 只服务 web 目录内真实存在的文件；其余全部回 index.html（前端路由接管）
            candidate = (WEB_DIR / full_path).resolve() if full_path else None
            if candidate and candidate.is_file() and str(candidate).startswith(str(WEB_DIR.resolve())):
                return FileResponse(candidate)
            return FileResponse(WEB_DIR / "index.html")

    @app.on_event("startup")
    def _startup():
        init_db()
        ensure_admin()
        from .queue.engine import get_engine

        get_engine()  # 恢复快照 + 启动 worker

        # 网盘凭据每日探活（M3）：定时验证 Cookie 是否还有效，失效即标记 + 推送
        from .services.healthcheck import start_scheduler

        start_scheduler()

        # 自动转存任务 cron 调度（M3）
        from .services.pa_scheduler import start_pa_scheduler

        start_pa_scheduler()
        print("[init] PanKeeper 后端就绪" + ("（含前端静态托管）" if WEB_DIR.is_dir() else ""))

    return app


app = create_app()
