"""设置读写：settings 表单个 key 存分组 JSON，读取时合并默认值。

分组与前端系统设置页四 tab 对齐：search / notify / qms / security；
另有两个系统级 key：queue_cfg（队列节奏）、cache_cfg（目录树缓存策略）。
"""
from __future__ import annotations

import json
from typing import Any

from ..db import SessionLocal
from ..models import Setting
from ..security import decrypt_credential, encrypt_credential

# 落库加密的敏感字段：密文加 fernet: 前缀，get_group 读取时自动解密
_SENSITIVE_FIELDS = ("sendkey", "apikey", "webhook", "tmdb_api_key")

DEFAULTS: dict[str, dict[str, Any]] = {
    "settings": {
        "search": {
            # 默认留空：不该替用户预设外部服务地址。写死成作者自己的 NAS IP，
            # 别人 clone 下来就是一串连不上的地址，而且会把「未配置」误显示成「已配置」。
            "pansou_url": "",
            "timeout": 30,
            "cache_mode": "on",
            # 搜索频道白名单（空 = 使用 pansou 的全部频道）；pansou 容器环境变量的
            # CHANNELS 决定"有哪些可选"，这里决定"每次搜索带哪些"
            "channels": [],
        },
        "notify": {
            "enabled": False,
            "sendkey": "",
            "webhook": "",
            # 推送时机三开关：自动转存 / 搜索转存 / 凭据过期告警
            "on_auto": True,
            "on_search": True,
            "on_cred": True,
        },
        "qms": {
            "enabled": False,
            "url": "",
            "apikey": "",
            # TMDB（设置页「代理配置」tab）：API Key 用于推送带图；连通模式
            # proxy=HTTP 代理（tmdb_proxy）/ host=按 tmdb_hosts 域名→IP 表直连
            # （消费方见 services/tmdb.py；tmdb_skip_tls=自建反代证书对不上时跳过校验）
            "tmdb_api_key": "",
            "tmdb_proxy": "",
            "tmdb_mode": "proxy",
            "tmdb_hosts": [],
            "tmdb_skip_tls": False,
            "act_strm": True,
            "act_emby": True,
        },
        "security": {"username": "admin", "session_days": 7},
    },
    # 联动后端选择：qms = 现行 QMS 全流程（刮削/STRM/PanKeeper 管理）；
    # litepan = 转存完成后 Webhook 推给 LitePan，后续整理由 LitePan 自动化规则自理
    "media": {"backend": "qms"},
    # LitePan 对接参数（HTTP Webhook，协议定稿见 services/litepan.py 模块注释）。
    # enabled 总闸（关=全部不推）；事件名不在这配（按目录/任务配，没填不联动）。
    # source 全局通知来源：填了才随事件传（LitePan 规则匹配大小写敏感），留空不传该字段。
    "litepan": {"enabled": False, "webhook_url": "", "apikey": "", "source": ""},
    # QMS/STRM 触发延迟（转存完成后 qms 秒触发 QMS、刮完 strm 秒触发 STRM）；
    # reverse = 反转触发顺序：先生成 STRM、再触发 QMS 刮削（不等刮削完成，2026-10-10）
    "queue_cfg": {"threads": 1, "gap": 5, "qms": 10, "strm": 10, "reverse": False},
    # 网盘凭据每日探活（M3）：默认每天 10:00 跑一次。
    # 时间特意放在上午而不是凌晨——半夜探出失效也没人看，通知等于白发；
    # 10 点人都起来了，失效提醒当场就能处理。
    # 强烈建议不要调得比每日更密：夸克有频率风控，过密调用反而可能加速 Cookie 失效。
    "health_cfg": {"enabled": True, "hour": 10, "minute": 0},
    # 请求统计保留期：默认半年（180 天）。每日定时清理更早的行，
    # 半年足够回溯趋势，又不至于让 SQLite 无限长胖。
    "stats_cfg": {"retain_days": 180},
    # 每个网盘的「失效通知」开关（网盘连接页卡片上控制）。
    # 只是粒度开关，总闸仍是 settings.notify.enabled + on_cred。
    "drive_notify": {"baidu": True, "quark": True, "115": True},
    # 各网盘「默认根目录」（网盘连接页配置）：所有目录树弹窗的固定浏览起点。
    # 与转存配置的 is_default（快速转存下拉第一项/排序）是两回事，别混。
    "root_cfg": {},
    # 头像：前端压缩后的 data URL（256×256 JPEG，通常 20–60KB）。
    # 单独一组、单独接口读写，**不并进 /settings 响应**——否则每次拉配置都要背着它。
    "avatar_cfg": {"data": "", "updated": ""},
    "cache_cfg": {
        "master": True,
        # 缓存持久化：条目写穿到 SQLite（dir_tree_cache 表），重启/重装不丢、恢复零网盘请求
        "persist": True,
        "ttl": 30,
        "ttlUnit": "小时",
        "auto": True,
        "memHigh": 85,
        "act": "ladder",
        # 缓存大小上限（MB）：水位条 = 已用字节 / 该上限，超出按 LRU 淘汰
        "maxSizeMb": 100,
    },
}


def get_group(key: str) -> dict[str, Any]:
    """读取分组并深合并默认值（新增配置项无需迁移）。"""
    merged = DEFAULTS.get(key, {})

    def _merge(base: dict, extra: dict) -> dict:
        out = dict(base)
        for k, v in extra.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = _merge(out[k], v)
            else:
                out[k] = v
        return out

    with SessionLocal() as s:
        row = s.get(Setting, key)
        stored = json.loads(row.value_json) if row else {}
    data = _merge(merged, stored) if isinstance(stored, dict) else merged

    def _decrypt(group: dict) -> None:
        for f in _SENSITIVE_FIELDS:
            v = group.get(f)
            if isinstance(v, str) and v.startswith("fernet:"):
                try:
                    group[f] = decrypt_credential(v[len("fernet:"):])
                except Exception:
                    group[f] = ""

    if key == "settings":
        _decrypt(data.get("notify", {}))
        _decrypt(data.get("qms", {}))
    elif key == "litepan":
        _decrypt(data)
    return data


def _normalize_base_url(u: Any) -> Any:
    """服务地址清洗：剥掉 host:port 之后的路径/参数/锚点（2026-10-09）。

    痛点：从浏览器复制地址时容易把整条链路带进来（如
    `http://ip:8888/api/search?kw=x&res=merge`），后端拼 `/api/search` 就变成
    `/api/search?kw=x/api/search` 直接 404/报错。这三个字段语义上都是「服务根地址」，
    统一裁成 scheme://host[:port]，没写协议的补 http://。解析不了就原样放行（别把
    能用的配置改坏）。"""
    if not isinstance(u, str):
        return u
    u = u.strip().rstrip("/")
    if not u:
        return u
    from urllib.parse import urlsplit

    if "://" not in u:
        u = "http://" + u
    try:
        p = urlsplit(u)
        host = p.hostname or ""
        if not host:
            return u
        if ":" in host:  # IPv6 字面量要保留方括号
            host = f"[{host}]"
        port = f":{p.port}" if p.port else ""
        return f"{p.scheme}://{host}{port}"
    except ValueError:
        return u


def save_group(key: str, value: dict[str, Any]) -> None:
    """保存分组：敏感字段值以 **** 开头视为「前端没改」，保留库里旧值。"""

    def _encrypt(group: dict) -> None:
        for f in _SENSITIVE_FIELDS:
            v = group.get(f)
            if isinstance(v, str) and v and not v.startswith("fernet:"):
                group[f] = "fernet:" + encrypt_credential(v)

    if key == "settings":
        old = get_group(key)
        # 服务根地址清洗：复制粘贴带进来的路径尾巴（/api/search?kw=... 这类）整段剥掉
        for g, f in (("search", "pansou_url"), ("qms", "url"), ("qms", "tmdb_proxy")):
            if g in value and f in (value.get(g) or {}):
                value[g][f] = _normalize_base_url(value[g].get(f))
        for group in ("notify", "qms"):
            for field in ("sendkey", "apikey", "webhook", "tmdb_api_key"):
                new_val = (value.get(group, {}) or {}).get(field, "")
                if isinstance(new_val, str) and new_val.startswith("****"):
                    value.setdefault(group, {})[field] = old.get(group, {}).get(field, "")

        _encrypt(value.get("notify", {}))
        _encrypt(value.get("qms", {}))
    elif key == "litepan":
        old = get_group(key)
        new_val = value.get("apikey", "")
        if isinstance(new_val, str) and new_val.startswith("****"):
            value["apikey"] = old.get("apikey", "")
        value.pop("event", None)  # 事件名配置已废弃（按目录/任务配），老库残留一并清掉
        _encrypt(value)
    with SessionLocal() as s:
        row = s.get(Setting, key)
        if row:
            row.value_json = json.dumps(value, ensure_ascii=False)
        else:
            s.add(Setting(key=key, value_json=json.dumps(value, ensure_ascii=False)))
        s.commit()
