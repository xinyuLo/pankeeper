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

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        uvicorn.run("app.main:app", host=args.host, port=args.port, loop="none")
    else:
        uvicorn.run("app.main:app", host=args.host, port=args.port)
