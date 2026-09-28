"""设置读写：settings 表单个 key 存分组 JSON，读取时合并默认值。

分组与前端系统设置页四 tab 对齐：search / notify / qms / security；
另有两个系统级 key：queue_cfg（队列节奏）、cache_cfg（目录树缓存策略）。
"""
from __future__ import annotations

import json
from typing import Any

from ..db import SessionLocal
from ..models import Setting

DEFAULTS: dict[str, dict[str, Any]] = {
    "settings": {
        "search": {
            "pansou_url": "http://192.168.2.77:8028",
            "timeout": 30,
            "cache_mode": "on",
            "def_dir_baidu": "/影视",
            "def_dir_quark": "/剧集",
            # 搜索频道白名单（空 = 使用 pansou 的全部频道）；pansou 容器环境变量的
            # CHANNELS 决定"有哪些可选"，这里决定"每次搜索带哪些"
            "channels": [],
        },
        "notify": {
            "enabled": False,
            "sendkey": "",
            "webhook": "",
            "on_done": True,
            "on_fail": True,
            "on_part": True,
            "on_cred": True,
        },
        "qms": {
            "enabled": False,
            "url": "http://192.168.2.77:8020",
            "apikey": "",
            "act_strm": True,
            "act_emby": True,
        },
        "security": {"username": "admin", "session_days": 7},
    },
    "queue_cfg": {"threads": 1, "gap": 5, "qms": 10, "strm": 10},
    "cache_cfg": {
        "master": True,
        "ttl": 30,
        "ttlUnit": "小时",
        "auto": True,
        "memHigh": 85,
        "act": "ladder",
        # 缓存大小上限（MB）：水位条 = 已用字节 / 该上限，超出按 LRU 淘汰
        "maxSizeMb": 800,
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
    return _merge(merged, stored) if isinstance(stored, dict) else merged


def save_group(key: str, value: dict[str, Any]) -> None:
    """保存分组：敏感字段值以 **** 开头视为「前端没改」，保留库里旧值。"""
    if key == "settings":
        old = get_group(key)
        for group in ("notify", "qms"):
            for field in ("sendkey", "apikey", "webhook"):
                new_val = (value.get(group, {}) or {}).get(field, "")
                if isinstance(new_val, str) and new_val.startswith("****"):
                    value.setdefault(group, {})[field] = old.get(group, {}).get(field, "")
    with SessionLocal() as s:
        row = s.get(Setting, key)
        if row:
            row.value_json = json.dumps(value, ensure_ascii=False)
        else:
            s.add(Setting(key=key, value_json=json.dumps(value, ensure_ascii=False)))
        s.commit()
