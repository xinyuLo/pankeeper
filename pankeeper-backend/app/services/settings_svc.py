from __future__ import annotations

import json
from typing import Any

from ..db import SessionLocal
from ..models import Setting
from ..security import decrypt_credential, encrypt_credential

_SENSITIVE_FIELDS = ("sendkey", "apikey", "webhook", "tmdb_api_key")

DEFAULTS: dict[str, dict[str, Any]] = {
    "settings": {
        "search": {

            "pansou_url": "",
            "timeout": 30,
            "cache_mode": "on",

            "channels": [],
        },
        "notify": {
            "enabled": False,
            "sendkey": "",
            "webhook": "",

            "on_auto": True,
            "on_search": True,
            "on_cred": True,
        },
        "qms": {
            "enabled": False,
            "url": "",
            "apikey": "",

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

    "media": {"backend": "qms"},

    "litepan": {"enabled": False, "webhook_url": "", "apikey": "", "source": ""},
    "queue_cfg": {"threads": 1, "gap": 5, "qms": 10, "strm": 10},

    "health_cfg": {"enabled": True, "hour": 10, "minute": 0},

    "stats_cfg": {"retain_days": 180},

    "drive_notify": {"baidu": True, "quark": True, "115": True},

    "root_cfg": {},

    "avatar_cfg": {"data": "", "updated": ""},
    "cache_cfg": {
        "master": True,

        "persist": True,
        "ttl": 30,
        "ttlUnit": "小时",
        "auto": True,
        "memHigh": 85,
        "act": "ladder",

        "maxSizeMb": 100,
    },
}

def get_group(key: str) -> dict[str, Any]:
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

def save_group(key: str, value: dict[str, Any]) -> None:

    def _encrypt(group: dict) -> None:
        for f in _SENSITIVE_FIELDS:
            v = group.get(f)
            if isinstance(v, str) and v and not v.startswith("fernet:"):
                group[f] = "fernet:" + encrypt_credential(v)

    if key == "settings":
        old = get_group(key)
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
        value.pop("event", None)
        _encrypt(value)
    with SessionLocal() as s:
        row = s.get(Setting, key)
        if row:
            row.value_json = json.dumps(value, ensure_ascii=False)
        else:
            s.add(Setting(key=key, value_json=json.dumps(value, ensure_ascii=False)))
        s.commit()
