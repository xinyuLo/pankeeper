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
        # 前端表现就是 vite 代理层偶发 500；Selector 循环没有这个坑（家用并发足够）
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    uvicorn.run("app.main:app", host=args.host, port=args.port)
