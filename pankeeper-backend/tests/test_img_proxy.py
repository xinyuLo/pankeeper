"""TMDB 图本地代理（/img）单测：白名单、磁盘缓存、拉取失败口径。

2026-10-31：推送详情里的海报直链墙内拉不到，图空白；这里由 NAS 代拉+缓存。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests
from fastapi import HTTPException
from fastapi.responses import FileResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api import img  # noqa: E402

URL = "https://image.tmdb.org/t/p/w500/abc.jpg"


def test_rejects_non_tmdb_host():
    with pytest.raises(HTTPException) as ei:
        img.proxy_image("http://127.0.0.1/secret.jpg")
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException):
        img.proxy_image("https://evil.example.com/abc.jpg")


def test_fetch_then_cache_hit(tmp_path, monkeypatch):
    monkeypatch.setattr(img, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(img, "_proxy_map", lambda: None)
    calls = {"n": 0}

    def fake_fetch(url, proxies):
        calls["n"] += 1
        return b"jpeg-bytes", "image/jpeg"

    monkeypatch.setattr(img, "_fetch", fake_fetch)

    first = img.proxy_image(URL)
    assert first.body == b"jpeg-bytes"
    assert first.media_type == "image/jpeg"

    second = img.proxy_image(URL)
    assert isinstance(second, FileResponse)
    assert calls["n"] == 1  # 第二次直接吃缓存，没有再拉


def test_fetch_failure_is_502(tmp_path, monkeypatch):
    monkeypatch.setattr(img, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(img, "_proxy_map", lambda: None)

    def boom(url, proxies):
        raise requests.RequestException("boom")

    monkeypatch.setattr(img, "_fetch", boom)
    with pytest.raises(HTTPException) as ei:
        img.proxy_image("https://image.tmdb.org/t/p/w500/die.jpg")
    assert ei.value.status_code == 502
