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
_SENSITIVE_FIELDS = ("sendkey", "apikey", "webhook")

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
            "on_done": True,
            "on_fail": True,
            "on_part": True,
            "on_cred": True,
        },
        "qms": {
            "enabled": False,
            "url": "",
            "apikey": "",
            "act_strm": True,
            "act_emby": True,
        },
        "security": {"username": "admin", "session_days": 7},
    },
    "queue_cfg": {"threads": 1, "gap": 5, "qms": 10, "strm": 10},
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
    # 头像：前端压缩后的 data URL（256×256 JPEG，通常 20–60KB）。
    # 单独一组、单独接口读写，**不并进 /settings 响应**——否则每次拉配置都要背着它。
    "avatar_cfg": {"data": "", "updated": ""},
    "cache_cfg": {
        "master": True,
        "ttl": 30,
        "ttlUnit": "小时",
        "auto": True,
        "memHigh": 85,
        "act": "ladder",
        # 缓存大小上限（MB）：水位条 = 已用字节 / 该上限，超出按 LRU 淘汰
        "maxSizeMb": 200,
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
    return data


def save_group(key: str, value: dict[str, Any]) -> None:
    """保存分组：敏感字段值以 **** 开头视为「前端没改」，保留库里旧值。"""
    if key == "settings":
        old = get_group(key)
        for group in ("notify", "qms"):
            for field in ("sendkey", "apikey", "webhook"):
                new_val = (value.get(group, {}) or {}).get(field, "")
                if isinstance(new_val, str) and new_val.startswith("****"):
                    value.setdefault(group, {})[field] = old.get(group, {}).get(field, "")

        def _encrypt(group: dict) -> None:
            for f in _SENSITIVE_FIELDS:
                v = group.get(f)
                if isinstance(v, str) and v and not v.startswith("fernet:"):
                    group[f] = "fernet:" + encrypt_credential(v)

        _encrypt(value.get("notify", {}))
        _encrypt(value.get("qms", {}))
    with SessionLocal() as s:
        row = s.get(Setting, key)
        if row:
            row.value_json = json.dumps(value, ensure_ascii=False)
        else:
            s.add(Setting(key=key, value_json=json.dumps(value, ensure_ascii=False)))
        s.commit()
