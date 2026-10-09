"""启动入口：python run.py [--port 8000]"""
import argparse
import asyncio
import sys

import uvicorn

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    if sys.platform == "win32":
        # Proactor 循环在 Windows 上偶发 WinError 10022（连接建立即被掐断），
        # 前端表现就是 vite 代理层偶发 500；Selector 循环没有这个坑（家用并发足够）。
        # ⚠️ 必须 loop="none"：uvicorn 0.36+ 在 win32 会用 loop_factory 直接造
        # ProactorEventLoop，set_event_loop_policy 对它无效（2026-10-03 实锤，
        # 之前那条"切 Selector 修复"实际从未生效——手机端浏览弹窗"失败重试"的真凶）。
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        uvicorn.run("app.main:app", host=args.host, port=args.port, loop="none")
    else:
        uvicorn.run("app.main:app", host=args.host, port=args.port)
