"""推送通知：Server 酱 / Bark / 自定义 Webhook，通道下拉单选（2026-10-12 定案）。

交互契约：推送只服务自动转存（任务 post_notify）；手动转存结果看日志不推送。
批次汇总制（参考 quark-auto-save 的做法但 M1 简化为即时单条）。
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid

import httpx
import requests

from .settings_svc import get_group

# Server 酱推送的标签：SC3 的客户端按 tag 分组，把 PanKeeper 的消息归到一处好找
SC_TAG = "PanKeeper"

# Bark 无海报时的兜底小图标：公开仓里的 PWA 图标走 jsdelivr CDN（国内手机可达；
# raw.githubusercontent 国内不稳，PanKeeper 自身地址手机在外网时又够不着）
PK_ICON_URL = "https://cdn.jsdelivr.net/gh/xinyuLo/pankeeper@main/pankeeper-vue3/public/icons/icon-192.png"

# 通知通道（settings.notify.channel 的合法值）：单选，只发选中的一路
CHANNELS = ("serverchan", "bark", "webhook")


def push(title: str, content: str, kind: str = "info", short: str | None = None, subtitle: str | None = None, plain: str | None = None, markdown: str | None = None, title_bark: str | None = None, cover: str | None = None) -> None:
    """按推送时机开关过滤。kind 对应设置页「推送通知 → 推送时机」三个开关：

    - search_done / search_fail → on_search（搜索转存）
    - auto_done   / auto_fail   → on_auto（自动转存）
    - cred                      → on_cred（凭据过期告警）
    - info                      → 不受时机开关限制（仅受总开关）
    short：Server酱³ 列表简介（Turbo 不支持该参数，会忽略）。
    subtitle：Bark 副标题（可选；当前版式不传）。Server酱 忽略，Webhook 随 JSON 原样带出。
    plain / markdown：Bark 专用正文（与 Server酱 文本完全拆开）——markdown 传了服务端忽略 body，
    App 历史里渲染基础 markdown（加粗/标题/列表；图片降级成链接文本，图走 image 参数），
    横幅按纯文本降级展示；plain 为纯文本兜底，两者都没传则把 content 剥 markdown。
    title_bark：Bark 专用标题（2026-10-13 用户定案：「☁️ 夸克 · 影片名 ✅ 成功」信息条首行），
    不传 Bark 沿用 title（片名）。
    cover：Bark 单图用**横版封面**（2026-10-13 用户定案：backdrop 优先、poster 兜底），
    不传退回从 content 正文提取首图。

    每次真正发出（未被开关拦掉）都落一行 push_logs：标题/时间/成败/失败原因，推送历史页的数据源。"""
    cfg = get_group("settings")["notify"]
    if not cfg.get("enabled"):
        return
    gate = {
        "search_done": "on_search", "search_fail": "on_search",
        "auto_done": "on_auto", "auto_fail": "on_auto",
        "cred": "on_cred", "info": None,
    }
    flag = gate.get(kind)
    if flag and not cfg.get(flag, True):
        return

    # 后台线程发送：Server酱抖时单次等 30s + 重试，最坏可占 1 分多钟——
    # 不能堵在转存完成回调/队列线程上（2026-10-10 用户定案：宁可慢，不能一条电影推 3 遍）
    threading.Thread(target=_deliver, args=(cfg, title, content, kind, short, subtitle, plain, markdown, title_bark, cover), daemon=True).start()


def _deliver(cfg: dict, title: str, content: str, kind: str, short: str | None, subtitle: str | None = None, plain: str | None = None, markdown: str | None = None, title_bark: str | None = None, cover: str | None = None) -> None:
    """实际投递 + 落库（后台线程里跑）。通道单选：只发 channel 指向的那一路。

    bark_id：本次推送的唯一标识，随 Bark payload 的 id 字段发出并存进 push_logs——
    每日清理任务（cleanup_expired）按它给手机端发 delete 指令删掉过期历史（2026-10-12 用户定案）。"""
    bark_id = uuid.uuid4().hex
    ok, err = _deliver_channel(cfg.get("channel"), cfg, title, content, kind, short, subtitle, bark_id, plain, markdown, title_bark, cover)
    _log_push(title, kind, "success" if ok else "fail", err, content, bark_id)


def _deliver_channel(channel, cfg: dict, title: str, content: str, kind: str, short: str | None, subtitle: str | None = None, bark_id: str = "", plain: str | None = None, markdown: str | None = None, title_bark: str | None = None, cover: str | None = None) -> tuple[bool, str]:
    """按通道分发，返回 (是否成功, 失败原因)；成功时原因为空串。失败统一带「通道名：」前缀（推送历史/前端展示用）。"""
    if channel not in CHANNELS:
        channel = "serverchan"
    if channel == "bark":
        return _bark(cfg.get("bark") or {}, title, content, kind, short, subtitle, bark_id, plain, markdown, title_bark, cover)
    if channel == "webhook":
        url = (cfg.get("webhook") or {}).get("url") or ""
        if not url:
            return False, "Webhook：地址未配置"
        payload = {"title": title, "content": content}
        if subtitle:
            payload["subtitle"] = subtitle
        ok, err = _webhook(url, payload)
        return ok, ("" if ok else f"Webhook：{err}")
    sendkey = (cfg.get("serverchan") or {}).get("sendkey") or ""
    if not sendkey:
        return False, "Server酱：SendKey 未配置"
    ok, err = _serverchan(sendkey, title, content, short)
    return ok, ("" if ok else f"Server酱：{err}")


def channel_ready(cfg: dict | None = None) -> bool:
    """当前选中通道是否已配置必填项（media_push 富文本推送的入口闸门用）。"""
    cfg = cfg if cfg is not None else get_group("settings")["notify"]
    ch = cfg.get("channel")
    if ch == "bark":
        bark = cfg.get("bark") or {}
        return bool((bark.get("server") or "").strip() and (bark.get("device_key") or "").strip())
    if ch == "webhook":
        return bool((cfg.get("webhook") or {}).get("url"))
    return bool((cfg.get("serverchan") or {}).get("sendkey"))


def _log_push(title: str, kind: str, status: str, error: str, content: str = "", bark_id: str = "") -> None:
    """推送结果落库（含正文快照，推送历史「详情」用）。日志失败绝不影响主流程（推送本身就是旁路）。"""
    try:
        from ..db import SessionLocal
        from ..models import PushLog

        # 正文快照截断：富文本推送带图片链接时可能几十 KB，SQLite 存得下但没必要
        body = content or ""
        if len(body) > 50000:
            body = body[:50000] + "\n…（超长截断）"
        with SessionLocal() as db:
            db.add(PushLog(ts=time.strftime("%Y-%m-%d %H:%M:%S"), title=title, kind=kind, status=status, error=error, content=body, bark_id=bark_id))
            db.commit()
    except Exception:
        pass


def drive_enabled(drive: str) -> bool:
    """该账号是否允许推送「失效通知」（网盘连接页卡片上的开关，默认开）。

    参数为账号 id 字符串（多账号粒度）。只是粒度开关：真正的总闸仍是
    settings.notify.enabled（以及 on_cred 时机开关）。
    """
    return bool(get_group("drive_notify").get(drive, True))


def _sc_request(sendkey: str, data: dict) -> httpx.Response:
    """按 sendkey 形态自动适配 Server 酱版本。

    - **Server酱³**：sendkey 形如 `sctp<uid>t...`，接口在 `<uid>.push.ft07.com`，
      且支持 `tags`（客户端按标签分组）。
    - **Server酱 Turbo**：sendkey 形如 `SCT...`，接口在 `sctapi.ftqq.com`，**不支持 tags**。

    两个产品的 key 互不通用、域名也不同，写死任何一个都会让另一半用户发不出去。
    """
    m = re.match(r"^sctp(\d+)t", sendkey)
    if m:
        url = f"https://{m.group(1)}.push.ft07.com/send/{sendkey}.send"
        data = {**data, "tags": SC_TAG}
    else:
        url = f"https://sctapi.ftqq.com/{sendkey}.send"
    # 30s（2026-10-10 用户定案）：ft07 偶发「送达了但响应慢」，等太短就重发会变成
    # 同一条电影推 2-3 遍；宁可多等也不重发——真没响应 30s 后才进下一次
    return httpx.post(url, data=data, timeout=30)


def _serverchan(sendkey: str, title: str, content: str, short: str | None = None) -> tuple[bool, str]:
    """返回 (是否成功, 失败原因)。判活口径与 test_sendkey 一致：HTTP 200 且 body code∈(0, None)。

    ft07 端点抖（2026-10-10 实测：最近 30 推 19 成 1 超时，根路径 12s 无响应）——
    网络级错误重试至多 2 次（隔 3s）；Server酱 body 明确拒绝（code≠0 / 4xx）不重试，重试也没用。"""
    last_err = ""
    for attempt in range(3):
        try:
            data = {"title": title, "desp": content}
            if short:
                data["short"] = short
            resp = _sc_request(sendkey, data)
        except httpx.HTTPError as e:
            last_err = _human_net_error(e)
            if attempt < 2:
                time.sleep(3)
                continue
            return False, last_err
        if resp.status_code >= 500:
            last_err = f"HTTP {resp.status_code}（服务端错误）"
            if attempt < 2:
                time.sleep(3)
                continue
            return False, last_err
        if resp.status_code != 200:
            # 带上 Server酱 原始原因——只写「HTTP 400」等于没说，实测这里能直接看到
            # 「[AUTH]错误的Key」（占位符 sendkey）之类，秒定位（2026-10-04 踩坑）
            return False, f"HTTP {resp.status_code}{_sc_reason(resp)}"
        try:
            body = resp.json()
        except ValueError:
            return True, ""
        code = body.get("code")
        if code in (0, None):
            return True, ""
        return False, str(body.get("message") or f"code={code}")


def _human_net_error(e: Exception) -> str:
    """httpx 网络异常 → 人话（原始 _ssl.c:983 这类堆栈文案用户看不懂，2026-10-08 用户实锤）。

    只翻译常见网络故障；认不出的保留原文前 80 字（信息不丢）。"""
    s = str(e)
    low = s.lower()
    if "handshake" in low and ("timed out" in low or "timeout" in low):
        return "连接超时（Server酱服务响应慢或网络不稳定），稍后重试"
    if "timed out" in low or "timeout" in low:
        return "请求超时（网络不稳定或服务无响应），稍后重试"
    if "getaddrinfo" in low or "name or service not known" in low or "nodename nor servname" in low:
        return "域名解析失败（检查设备的网络/DNS）"
    if "connection refused" in low:
        return "连接被拒绝（服务地址不可达）"
    if "reset" in low:
        return "连接被重置（网络中断或服务端断开），稍后重试"
    if "ssl" in low or "certificate" in low:
        return "SSL/证书异常（网络被劫持或设备时间不对）"
    if "unreachable" in low or "network is down" in low:
        return "网络不可达（检查设备联网状态）"
    if "max retries" in low or "failed to establish" in low:
        return "无法连接（服务地址不可达或被网络策略拦截）"
    return s[:80] or e.__class__.__name__


def _sc_reason(resp: httpx.Response) -> str:
    """从失败响应里抠一句人话（message/info 优先，其次裸文本前 80 字）。"""
    try:
        body = resp.json()
        msg = body.get("message") or body.get("info")
        if msg:
            return f" · {msg}"
    except ValueError:
        pass
    txt = (resp.text or "").strip()[:80]
    return f" · {txt}" if txt else ""


def _reason(text: str, _status: int) -> str:
    """requests 版响应原因提炼（_sc_reason 的 text 版，Bark 用）：JSON message 优先，其次裸文本前 80 字。"""
    try:
        body = json.loads(text or "")
        msg = body.get("message") or body.get("info")
        if msg:
            return f" · {msg}"
    except ValueError:
        pass
    txt = (text or "").strip()[:80]
    return f" · {txt}" if txt else ""


def _webhook(url: str, payload: dict) -> tuple[bool, str]:
    """自定义 Webhook：POST JSON（title/content，带副标题时多一个 subtitle 字段）。"""
    try:
        resp = requests.post(url, json=payload, timeout=10)
    except requests.RequestException as e:
        return False, _human_net_error(e)
    if resp.status_code < 200 or resp.status_code >= 300:
        return False, f"HTTP {resp.status_code}"
    return True, ""


# ===== Bark 通道（iOS）=====
# 展示参数对照 bark params 文档（https://bark.day.app/#/params）：横幅只显纯文本，
# 富信息量靠 image（海报大图）/ url（点击跳转）承载，markdown 参数不用。

_MD_IMAGE = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)[^)]*\)")
_MD_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\((https?://[^)\s]+)[^)]*\)")

# level 只开放这三档；critical 需要 App 内持续定位授权且体验吓人，不提供（计划文档 §六）
_BARK_LEVELS = ("active", "timeSensitive", "passive")

# 业务 kind → Bark 分组后缀：App 内可按组查看/单独静音
_BARK_KIND_GROUP = {
    "search_done": "搜索转存", "search_fail": "搜索转存",
    "auto_done": "自动转存", "auto_fail": "自动转存",
    "cred": "凭据告警",
}


def _extract_md_assets(content: str) -> tuple[str, str]:
    """从富文本正文里抠 (首图 URL, 首个非图片链接)，给 Bark 的 image / url 参数；没有就返回空串。"""
    content = content or ""
    m = _MD_IMAGE.search(content)
    image = m.group(1) if m else ""
    m = _MD_LINK.search(content)
    link = m.group(2) if m and m.group(2) != image else ""
    return image, link


def md_to_text(md: str, limit: int = 500) -> str:
    """Markdown → 纯文本摘要（横幅不渲染 markdown）：图片整段去掉（海报走 image），链接保留文字。"""
    if not md:
        return ""
    s = _MD_IMAGE.sub("", md)
    s = _MD_LINK.sub(lambda m: m.group(1) or m.group(2), s)
    s = re.sub(r"\*\*|__|`+|^#{1,6}\s*", "", s, flags=re.M)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()[:limit]


def _bark_group(cfg: dict, kind: str) -> str:
    """Bark group：测试固定 test；业务推送 =「配置前缀·时机分组」，配置留空只留时机分组。"""
    if kind == "test":
        return "test"
    base = (cfg.get("group") or "").strip()
    label = _BARK_KIND_GROUP.get(kind)
    if not label:
        return base or "PanKeeper"
    return f"{base}·{label}" if base else label


def _one_line(text: str, limit: int = 100) -> str:
    """压成单行纯文本并截断（Bark subtitle 用：横幅副标题放不下换行和长文）。"""
    s = re.sub(r"\s+", " ", (text or "").strip())
    return s[:limit] + "…" if len(s) > limit else s


def _bark_server(cfg: dict) -> str:
    """bark 服务器地址清洗：剥尾巴斜杠、没写协议补 http://。没配返回空串。"""
    server = (cfg.get("server") or "").strip()
    if server and not server.startswith(("http://", "https://")):
        server = "http://" + server
    return server


BARK_PAYLOAD_BUDGET = 3600  # APNs 单条推送硬上限 4KB（4096B）：title/markdown 之外，URL/参数/键名约占数百字节，留余量


def _fit_payload_markdown(md: str, title: str) -> str:
    """把 Bark markdown 压进单条载荷预算（2026-10-13 清单六 B 案：发送前算总长，超限自动降级）。

    APNs 单条 4KB 硬上限，超了 Bark 整条丢失（不是截断是丢）。超预算时从尾部按行丢
    （尾部是简介/集行，头部统计行/标题保住），整段单行巨文本硬截断兜底。
    只动 Bark 的 markdown——Server酱 desp 不走 APNs、照发全量（2026-10-13 全量定案不变）。
    电影长简介/电视剧逐集简介一个逻辑，调用方无需自己掐尺寸。"""
    budget = BARK_PAYLOAD_BUDGET - len((title or "").encode("utf-8")) - 40  # 40B 留给截断标记
    if len(md.encode("utf-8")) <= budget:
        return md
    lines = md.splitlines(keepends=True)
    while lines and len("".join(lines).encode("utf-8")) > budget:
        lines.pop()
    out = "".join(lines).rstrip()
    if not out:
        # 整段就是一行巨文本：丢行无法降级，硬截断
        out = md.encode("utf-8")[:budget].decode("utf-8", "ignore").rstrip()
    elif len(out.encode("utf-8")) > budget:  # 兜底（正常到不了）
        out = out.encode("utf-8")[:budget].decode("utf-8", "ignore").rstrip()
    return out + "\n…（超长已截断）"


def _bark(cfg: dict, title: str, content: str, kind: str, short: str | None = None, subtitle: str | None = None, bark_id: str = "", plain: str | None = None, markdown: str | None = None, title_bark: str | None = None, cover: str | None = None) -> tuple[bool, str]:
    """Bark 通道：POST {server}/push，判活=HTTP 200 且 body code==200（`{"code":200}` 风格）。

    版式（2026-10-13 用户定案）：标题=信息条首行（title_bark：「☁️ 夸克 · 影片名 ✅ 成功」，
    不传沿用 title 片名），副标题不传，正文优先 markdown（App 历史渲染加粗/标题/列表，
    横幅降级纯文本；服务端忽略 body）；没传 markdown 退回 plain（纯文本正文，与 Server酱
    文本拆开）；海报/链接自动从富文本正文提取。
    推送带唯一 id（bark_id，JSON 必须字符串，需 Bark v1.5.2+ / bark-server v2.2.5+）：
    每日清理任务按它发 delete 指令删手机端过期历史，不传 ttl（2026-10-12 用户定案）。
    ⚠️ 与 pansou.py 同款坑用 requests 不用 httpx：httpx 会吃 Windows 系统代理（注册表）且
    不认「本地地址绕过」，发内网 bark-server 稳定 502 且请求根本没到容器（2026-10-12 实测）；
    requests 尊重系统绕过规则，内网/外网 bark-server 两相宜。
    重试口径与 _serverchan 一致（2026-10-10 定案）：30s 超时、网络错重试 ≤2、明确拒绝不重试——
    bark-server 本机部署基本不抖，实际大概率一次过。"""
    server = _bark_server(cfg)
    device_key = (cfg.get("device_key") or "").strip()
    if not server or not device_key:
        return False, "Bark：服务器地址或 DeviceKey 未配置"
    image, link = _extract_md_assets(content)
    image = (cover or "").strip() or image  # 显式横版封面优先；没传才用正文首图
    data: dict = {
        "device_key": device_key,
        "title": title_bark or title,
        "group": _bark_group(cfg, kind),
        "level": cfg.get("level") if cfg.get("level") in _BARK_LEVELS else "active",
        "isArchive": 1 if cfg.get("is_archive", True) else 0,
    }
    if markdown:
        # 传了 markdown 服务端忽略 body；横幅按纯文本降级展示。
        # 超 APNs 载荷预算自动降级（2026-10-13 清单六 B 案：电影长简介/逐集简介一个逻辑，
        # 超了整条推送会丢，这里保证一定装得下）
        data["markdown"] = _fit_payload_markdown(markdown, title_bark or title)
    else:
        data["body"] = short or plain or md_to_text(content)
    if subtitle:
        data["subtitle"] = _one_line(subtitle)
    if bark_id:
        data["id"] = bark_id  # Bark 要求 JSON 传参用字符串类型，传数字不生效
    if image:
        data["image"] = image
        data["icon"] = image  # 封面同时当通知小图标（ios 15+）
    else:
        # 无封面：设置页配了自定义图标用它，否则兜底 PanKeeper 图标（2026-10-12 用户定案）
        data["icon"] = (cfg.get("icon") or "").strip() or PK_ICON_URL
    if link:
        data["url"] = link
    sound = (cfg.get("sound") or "").strip()
    if sound:
        data["sound"] = sound
    url = server.rstrip("/") + "/push"
    last_err = ""
    for attempt in range(3):
        try:
            resp = requests.post(url, json=data, timeout=30)
        except requests.RequestException as e:
            last_err = f"Bark：{_human_net_error(e)}"
            if attempt < 2:
                time.sleep(3)
                continue
            return False, last_err
        if resp.status_code >= 500:
            last_err = f"Bark：HTTP {resp.status_code}（服务端错误）"
            if attempt < 2:
                time.sleep(3)
                continue
            return False, last_err
        if resp.status_code != 200:
            return False, f"Bark：HTTP {resp.status_code}{_reason(resp.text, resp.status_code)}"
        try:
            body = resp.json()
        except ValueError:
            return True, ""
        if body.get("code") == 200:
            return True, ""
        reason = body.get("message") or f"code={body.get('code')}"
        return False, f"Bark：{reason}"


def test_serverchan(sendkey: str) -> tuple[bool, str]:
    """设置页「测试」按钮（Server酱通道）：与真实推送同走 _serverchan（含重试）——
    测试口径和实际投递不一致会误导排障（2026-10-10：按钮单发超时、真实推送却成功）。"""
    return _serverchan(sendkey, "PanKeeper 测试", "推送链路已打通，PanKeeper 的消息会带这个标签。")


def test_bark(cfg: dict) -> tuple[bool, str]:
    """Bark 通道测试：固定 id（连点只刷新同一条通知，不堆历史）+ isArchive=0 不存 App 历史，
    收到即通（2026-10-12 用户定案）。"""
    return _bark({**cfg, "is_archive": False}, "PanKeeper 测试", "推送链路已打通。", kind="test", bark_id="pankeeper-test")


def test_webhook(url: str) -> tuple[bool, str]:
    """Webhook 通道测试：与真实投递同走 _webhook。"""
    return _webhook(url, {"title": "PanKeeper 测试", "content": "推送链路已打通。"})


def delete_bark(bark_id: str) -> tuple[bool, str]:
    """按 id 删手机端单条 Bark 推送（通知中心 + App 历史一并删），推送历史「删除」按钮用。

    delete 指令走静默推送：HTTP 200 只代表 bark-server 受理，手机端实际删除取决于
    Bark 的「后台 App 刷新」开关。"""
    cfg = get_group("settings")["notify"].get("bark") or {}
    server = _bark_server(cfg)
    device_key = (cfg.get("device_key") or "").strip()
    if not server or not device_key:
        return False, "Bark 未配置"
    try:
        resp = requests.post(f"{server.rstrip('/')}/push", json={"device_key": device_key, "id": bark_id, "delete": "1"}, timeout=15)
    except requests.RequestException as e:
        return False, _human_net_error(e)
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"
    return True, ""


def cleanup_expired() -> dict:
    """推送历史每日清理（定时任务调用，挂 10:35，见 healthcheck._apply_schedule）。

    两端一把清（2026-10-12 用户定案）：电脑端删 push_logs 过期行；手机端 Bark 历史
    不用 ttl，而是对过期行里存着的 bark_id 逐条发 delete 指令——同时清掉 App 历史
    和通知中心里的对应推送（需 Bark v1.5.2+，且手机给 Bark 开「后台 App 刷新」，
    否则 delete 静默推送下不去、手机端只能手动清）。
    删除指令用当前 Bark 配置发送：DeviceKey 不变（同 iCloud 重装也不变）时才删得掉。"""
    from ..db import SessionLocal
    from ..models import PushLog

    cfg = get_group("settings")["notify"]
    retain = max(1, min(3650, int(cfg.get("retain_days") or 30)))
    cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - retain * 86400))
    with SessionLocal() as db:
        bark_ids = [r[0] for r in db.query(PushLog.bark_id).filter(PushLog.ts < cutoff, PushLog.bark_id != "").all()]
        n = db.query(PushLog).filter(PushLog.ts < cutoff).delete(synchronize_session=False)
        db.commit()

    bark = cfg.get("bark") or {}
    server = _bark_server(bark)
    device_key = (bark.get("device_key") or "").strip()
    deleted_remote = 0
    if bark_ids and server and device_key:
        for bid in bark_ids:
            try:
                resp = requests.post(f"{server.rstrip('/')}/push", json={"device_key": device_key, "id": bid, "delete": "1"}, timeout=15)
                if resp.status_code == 200:
                    deleted_remote += 1
            except requests.RequestException:
                pass
            time.sleep(0.2)  # delete 走 APNs 静默推送，几十条也就几秒，别打太密
    print(f"[notify] 推送历史清理：电脑端删 {n} 条；Bark 删除指令 {deleted_remote}/{len(bark_ids)} 条（保留 {retain} 天，截至 {cutoff}）", flush=True)
    return {"deleted_local": n, "bark_ids": len(bark_ids), "deleted_remote": deleted_remote, "retain_days": retain}
