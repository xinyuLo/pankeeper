#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pk.py — pankeeper API 客户端（watch-movie skill 专用）

铁律（代码层强制，不靠调用方自觉）：
  1. 所有会触达网盘/外部服务的调用（search/check/files/enqueue）统一走 RateLimiter：
     严格串行（进程内全局锁）、两次调用间隔 >= min_interval_sec（默认 3s）。
  2. 撞上 405/429：立即随机退避 30-60s 重试一次，之后间隔翻倍（3→6→12…上限 60s），
     本进程内不恢复原速。
  3. JWT 缓存到 state.json，401 自动重登一次再试。

子命令（输出一律 JSON 到 stdout，供 AI 在 bash 里消费）：
  preflight                     前置检查：网盘账号 + 转存配置（不打网盘）
  history [--limit N]           最近搜索词 + 转存记录（语境判断用，不打网盘）
  search KW [--refresh]         搜索资源（走 pansou）
  recognize NAME [--hint H]     识别片名 → TMDB 候选（= 前端「识别」按钮）
  check TYPE URL [CODE]         死活预检（触网盘，限流）
  files TYPE URL [CODE]         分享内文件树（触网盘，限流）[--refresh 强制直连]
  enqueue                       提交转存（读 stdin 或 --file 的 JSON payload，触网盘，限流）
  state                         队列当前状态快照（本地读，不限流）
  tmdb-season TMDB_ID SEASON    某季集数清单（需配置 tmdb_api_key，可选）
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SKILL_DIR / "pk_config.json"
STATE_PATH = SKILL_DIR / "state.json"

TOKEN_SKEW = 3600  # token 缓存提前 1 小时视为过期（pankeeper 默认 7 天）


# ---------------------------------------------------------------- 配置与状态
def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg.setdefault("base_url", "")
    cfg.setdefault("api_key", "")
    cfg.setdefault("username", "admin")
    cfg.setdefault("password", "")
    cfg.setdefault("min_interval_sec", 3)
    cfg.setdefault("tmdb_api_key", "")
    cfg.setdefault("tmdb_proxy", "")   # 可选：http://IP:端口，访问 TMDB 用（二选一）
    cfg.setdefault("tmdb_ip", "")      # 可选：TMDB 映射 IP（可带端口），二选一
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(st: dict) -> None:
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        STATE_PATH.chmod(0o600)
    except OSError:
        pass


# ---------------------------------------------------------------- 限流器
class RateLimiter:
    """进程级统一限流出口：串行 + 最小间隔 + 405/429 惩罚退避。"""

    def __init__(self, min_interval: float):
        self.min_interval = max(1.0, float(min_interval))
        self.interval = self.min_interval  # 惩罚后翻倍，本进程内不回落
        self._last = 0.0
        self._lock = threading.Lock()

    def gate(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                wait = (self._last + self.interval) - now
                if wait <= 0:
                    self._last = now
                    return
            time.sleep(min(wait, 5.0))

    def penalize(self) -> None:
        """405/429 惩罚：间隔翻倍（上限 60s）+ 本次随机退避 30-60s。"""
        with self._lock:
            self.interval = min(self.interval * 2, 60.0)
        penalty = random.uniform(30.0, 60.0)
        err(f"限流退避：休眠 {penalty:.0f}s（后续间隔升至 {self.interval:.0f}s）", stream=sys.stderr)
        time.sleep(penalty)


_limiter: RateLimiter | None = None


def limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(load_config()["min_interval_sec"])
    return _limiter


# ---------------------------------------------------------------- HTTP
def err(msg: str, stream=sys.stderr) -> None:
    print(msg, file=stream)


def _request(cfg: dict, method: str, path: str, body: dict | None = None,
             token: str | None = None, api_key: str | None = None) -> tuple[int, dict | list | dict]:
    url = cfg["base_url"].rstrip("/") + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json", "User-Agent": "watch-movie-skill/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = {"detail": raw[:200]}
        return e.code, payload
    except urllib.error.URLError as e:
        return 0, {"detail": f"连接失败：{e.reason}"}


def _login(cfg: dict) -> str:
    st_code, st = _request(cfg, "POST", "/api/auth/login",
                           {"username": cfg["username"], "password": cfg["password"]})
    if st_code != 200 or not isinstance(st, dict) or not st.get("token"):
        detail = st.get("detail") if isinstance(st, dict) else st
        err(json.dumps({"error": "登录失败", "detail": detail}, ensure_ascii=False))
        raise SystemExit(1)
    state = load_state()
    state["token"] = st["token"]
    state["token_time"] = time.time()
    save_state(state)
    return st["token"]


def _token(cfg: dict) -> str:
    state = load_state()
    tok = state.get("token") or ""
    ts = state.get("token_time") or 0
    if tok and (time.time() - ts) < (7 * 86400 - TOKEN_SKEW):
        return tok
    return _login(cfg)


def call(path: str, method: str = "GET", body: dict | None = None,
         limited: bool = False, retried: bool = False) -> dict | list:
    """统一调用出口。有 api_key 走 X-API-Key 头（等效登录态，不问密码）；
    否则 JWT 登录。limited=True 过限流器；401 重登一次；405/429 退避重试一次。"""
    cfg = load_config()
    if not cfg["base_url"].strip():
        err(json.dumps({"error": "pankeeper 地址未配置",
                        "hint": "问用户要 pankeeper 的 IP+端口，然后执行: pk.py config base_url http://IP:端口"},
                       ensure_ascii=False))
        raise SystemExit(1)
    if limited:
        limiter().gate()
    if cfg.get("api_key"):
        status, payload = _request(cfg, method, path, body, api_key=cfg["api_key"])
    else:
        token = _token(cfg)
        status, payload = _request(cfg, method, path, body, token=token)
        if status == 401 and not retried:
            _login(cfg)
            return call(path, method, body, limited=limited, retried=True)
    if status in (405, 429) and not retried:
        limiter().penalize()
        return call(path, method, body, limited=limited, retried=True)
    if status == 0:
        err(json.dumps({"error": "pankeeper 不可达", "detail": payload}, ensure_ascii=False))
        raise SystemExit(1)
    if status >= 400:
        err(json.dumps({"error": f"HTTP {status}", "detail": payload}, ensure_ascii=False))
        raise SystemExit(1)
    return payload


# ---------------------------------------------------------------- 子命令
def cmd_preflight(_args) -> None:
    accounts = call("/api/accounts")
    dd_items = call("/api/dd/items")
    items = dd_items if isinstance(dd_items, list) else (dd_items.get("items") or [])
    drives = []
    for acc in accounts:
        t = acc.get("type")
        has = call(f"/api/dd/has-config?type={urllib.parse.quote(t)}")
        paths = sorted({it.get("path", "") for it in items if it.get("type") == t and it.get("path")})
        drives.append({
            "type": t, "acc_id": acc.get("id"), "alias": acc.get("alias"),
            "status": acc.get("status"), "is_default": acc.get("is_default"),
            "dd_configured": bool(has), "dd_paths": paths,
        })
    print(json.dumps({"drives": drives}, ensure_ascii=False, indent=2))


def cmd_history(args) -> None:
    kws = call(f"/api/search/recent-keywords?limit=10")
    recs = call("/api/records?source=search")
    rows = []
    for r in (recs if isinstance(recs, list) else [])[: args.limit]:
        rows.append({"id": r.get("id"), "name": r.get("n"), "type": r.get("t"),
                     "path": r.get("p"), "status": r.get("st"), "time": r.get("tm")})
    print(json.dumps({"recent_keywords": kws, "recent_transfers": rows}, ensure_ascii=False, indent=2))


def cmd_search(args) -> None:
    qs = urllib.parse.urlencode({"kw": args.kw, "refresh": "true" if args.refresh else "false"})
    rows = call(f"/api/search/results?{qs}")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def cmd_recognize(args) -> None:
    body = {"name": args.name}
    if args.hint:
        body["hint"] = args.hint
    print(json.dumps(call("/api/recognize", method="POST", body=body), ensure_ascii=False, indent=2))


def cmd_check(args) -> None:
    body = {"type": args.type, "url": args.url, "code": args.code or ""}
    print(json.dumps(call("/api/search/check-link", method="POST", body=body, limited=True),
                     ensure_ascii=False, indent=2))


def cmd_files(args) -> None:
    qs = urllib.parse.urlencode({"type": args.type, "url": args.url, "code": args.code or "",
                                 "refresh": "true" if args.refresh else "false"})
    payload = call(f"/api/search/share-files?{qs}", limited=True)
    # 只保留扁平清单（path/name/is_dir/size/md5），树太占输出
    if isinstance(payload, dict):
        payload.pop("tree", None)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_enqueue(args) -> None:
    raw = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    body = json.loads(raw)
    print(json.dumps(call("/api/queue/tasks", method="POST", body=body, limited=True),
                     ensure_ascii=False, indent=2))


def cmd_state(_args) -> None:
    print(json.dumps(call("/api/queue/state"), ensure_ascii=False, indent=2))


def _tmdb_get(path_qs: str, cfg: dict) -> dict:
    """访问 TMDB：tmdb_proxy（代理）> tmdb_ip（映射 IP，Host/SNI 仍为官方域名）> 直连。"""
    import http.client
    import ssl

    host = "api.themoviedb.org"
    headers = {"User-Agent": "watch-movie-skill/1.0", "Host": host}
    ip = (cfg.get("tmdb_ip") or "").strip()
    proxy = (cfg.get("tmdb_proxy") or "").strip()
    if ip:
        h, _, p = ip.partition(":")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # 按 IP 建连时证书校验不了域名，映射 IP 场景关闭校验
        conn = http.client.HTTPSConnection(h, int(p) if p else 443, context=ctx, timeout=30)
        conn.request("GET", path_qs, headers=headers)
        resp = conn.getresponse()
        return json.loads(resp.read().decode("utf-8"))
    req = urllib.request.Request(f"https://{host}{path_qs}", headers=headers)
    if proxy:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        with opener.open(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def cmd_tmdb_season(args) -> None:
    cfg = load_config()
    key = cfg.get("tmdb_api_key")
    if not key:
        raise SystemExit(err(json.dumps({"error": "未配置 tmdb_api_key（可选）"}, ensure_ascii=False)))
    data = _tmdb_get(f"/tv/{args.tmdb_id}/season/{args.season}?api_key={key}&language=zh-CN", cfg)
    eps = [{"episode_number": e.get("episode_number"), "name": e.get("name")} for e in data.get("episodes") or []]
    print(json.dumps({"season": args.season, "episode_count": len(eps), "episodes": eps},
                     ensure_ascii=False, indent=2))


MASK_KEYS = {"api_key", "password", "tmdb_api_key"}


def cmd_config(args) -> None:
    cfg = load_config()
    if args.key is None:
        shown = {k: (v[:6] + "…" if v and k in MASK_KEYS else v) for k, v in cfg.items()}
        print(json.dumps(shown, ensure_ascii=False, indent=2))
        return
    if args.value is None:
        raise SystemExit(err(json.dumps({"error": "缺少 value", "usage": "pk.py config <key> <value>"}, ensure_ascii=False)))
    if args.key not in cfg:
        raise SystemExit(err(json.dumps({"error": f"未知配置项 {args.key}",
                                         "allowed": sorted(cfg.keys())}, ensure_ascii=False)))
    cfg[args.key] = args.value
    save_config(cfg)
    shown = args.value[:6] + "…" if args.key in MASK_KEYS else args.value
    print(json.dumps({"ok": True, args.key: shown}, ensure_ascii=False))


def main() -> None:
    p = argparse.ArgumentParser(description="pankeeper API client (watch-movie skill)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("preflight").set_defaults(fn=cmd_preflight)

    h = sub.add_parser("history")
    h.add_argument("--limit", type=int, default=10)
    h.set_defaults(fn=cmd_history)

    s = sub.add_parser("search")
    s.add_argument("kw")
    s.add_argument("--refresh", action="store_true")
    s.set_defaults(fn=cmd_search)

    r = sub.add_parser("recognize")
    r.add_argument("name")
    r.add_argument("--hint", default="")
    r.set_defaults(fn=cmd_recognize)

    c = sub.add_parser("check")
    c.add_argument("type")
    c.add_argument("url")
    c.add_argument("code", nargs="?", default="")
    c.set_defaults(fn=cmd_check)

    f = sub.add_parser("files")
    f.add_argument("type")
    f.add_argument("url")
    f.add_argument("code", nargs="?", default="")
    f.add_argument("--refresh", action="store_true")
    f.set_defaults(fn=cmd_files)

    e = sub.add_parser("enqueue")
    e.add_argument("--file", default="")
    e.set_defaults(fn=cmd_enqueue)

    sub.add_parser("state").set_defaults(fn=cmd_state)

    c = sub.add_parser("config", help="查看/设置配置（问用户要一次就存起来）")
    c.add_argument("key", nargs="?", default=None)
    c.add_argument("value", nargs="?", default=None)
    c.set_defaults(fn=cmd_config)

    t = sub.add_parser("tmdb-season")
    t.add_argument("tmdb_id", type=int)
    t.add_argument("season", type=int)
    t.set_defaults(fn=cmd_tmdb_season)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
