"""夸克网盘适配器（原创实现）。

接口事实依据：docs/research/quark-auto-save.md §2（端点/参数/批量上限/状态码语义）。
设计要点：
- httpx 同步 Client，每账号一个实例；所有请求过 RateGate（0.8s 间隔）；
- stoken 接口兼分享死活探测：HTTP 200 正常 / 500 网络异常重试 / 其他状态 = 分享失效（ShareBanned 熔断）；
- 转存 100 个/批（save_as_top_fids 硬上限），task 轮询 0.5s；
- 去重按目标名（正则改名后的名字也参与比对）。
"""
from __future__ import annotations

import random
import re
import time

import httpx

from ..security import decrypt_credential
from .base import (
    AdapterError,
    CloudAdapter,
    CredentialExpired,
    ShareBanned,
    ShareFile,
    TaskSpec,
    TransferResult,
)
from .rate_gate import CircuitOpen, RateGate, RateLimited

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "quark-cloud-drive/3.14.2 Chrome/112.0.5615.165 Electron/24.1.3.8 Safari/537.36 Channel/pckk_other_ch"
)

_RE_SHARE = re.compile(r"(?:pan\.)?quark\.cn/s/(\w+)")
_RE_PWD = re.compile(r"[?&]pwd=([a-zA-Z0-9]+)")
_RE_PDIR = re.compile(r"#/list/share/(\w{32})")

BATCH = 100  # 服务端 save_as_top_fids 上限 100，超过会被截断


def extract_share(url: str) -> dict:
    """从分享链接解析 pwd_id / 提取码 / 子目录 fid（无需请求网页）。"""
    m = _RE_SHARE.search(url)
    if not m:
        raise AdapterError(f"无法识别的夸克分享链接：{url}")
    out = {"pwd_id": m.group(1), "passcode": "", "pdir_fid": "0"}
    mp = _RE_PWD.search(url)
    if mp:
        out["passcode"] = mp.group(1)
    md = _RE_PDIR.search(url)
    if md:
        out["pdir_fid"] = md.group(1)
    return out


class QuarkAdapter(CloudAdapter):
    type = "quark"

    def __init__(self, cookies_enc: str, gate: RateGate | None = None):
        self.cookies = decrypt_credential(cookies_enc)
        self.gate = gate or RateGate("quark", min_interval=0.8, cooldown=30.0)
        self._stoken = ""  # list_share 取一次，save 批次复用
        self._http = httpx.Client(
            headers={"cookie": self.cookies, "content-type": "application/json", "user-agent": UA},
            timeout=20.0,
        )

    # ---------- 基础请求 ----------

    def _req(self, method: str, url: str, *, params: dict | None = None, json_body: dict | None = None) -> dict:
        self.gate.wait()
        params = dict(params or {})
        if "drive" in url:  # drive-pc.quark.cn 的公共参数
            params.setdefault("pr", "ucpro")
            params.setdefault("fr", "pc")
        try:
            resp = self._http.request(method, url, params=params, json=json_body)
        except httpx.HTTPError as e:
            self.gate.on_failure()
            raise AdapterError(f"网络异常：{e}") from e
        if resp.status_code == 401:
            self.gate.on_failure()
            raise CredentialExpired("夸克 Cookie 已失效（401）")
        try:
            data = resp.json()
        except ValueError as e:
            self.gate.on_failure()
            raise AdapterError(f"响应非 JSON（HTTP {resp.status_code}）") from e
        # 夸克业务码：code==0 成功；status 为 HTTP 层语义
        if data.get("code") not in (0, None) and resp.status_code != 200:
            self.gate.on_failure()
            raise AdapterError(f"夸克接口错误：{data.get('message') or data.get('status')}")
        self.gate.on_success()
        return data

    @staticmethod
    def _fake_browse_params() -> dict:
        """伪装页面停留时长的行为参数（1~5 分钟的毫秒数 + 当前时间戳）。"""
        return {"__dt": int(random.uniform(1, 5) * 60 * 1000), "__t": int(time.time() * 1000)}

    # ---------- 凭据 ----------

    def verify(self) -> str:
        self.gate.wait()
        try:
            resp = self._http.get("https://pan.quark.cn/account/info", params={"fr": "pc", "platform": "pc"})
        except httpx.HTTPError as e:
            raise AdapterError(f"网络异常：{e}") from e
        if resp.status_code in (401, 403):
            raise CredentialExpired("夸克 Cookie 已失效")
        data = resp.json()
        if not data.get("data", {}).get("nickname"):
            raise CredentialExpired("夸克 Cookie 已失效（无昵称）")
        self.gate.on_success()
        return data["data"]["nickname"]

    # ---------- 分享 ----------

    def get_stoken(self, pwd_id: str, passcode: str) -> str:
        data = self._req(
            "POST",
            "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token",
            json_body={"pwd_id": pwd_id, "passcode": passcode},
        )
        # 业务语义：status!=200 且非 500 → 分享失效（500 视为网络抖动走 AdapterError 重试链）
        if data.get("status") == 500:
            raise AdapterError("夸克接口网络异常（500）")
        if data.get("status") != 200:
            raise ShareBanned(data.get("message") or "分享已失效")
        stoken = (data.get("data") or {}).get("stoken")
        if not stoken:
            raise AdapterError("未取到 stoken")
        return stoken

    def list_share(self, spec: TaskSpec) -> list[ShareFile]:
        parsed = self.prepare(spec)
        stoken = self._stoken
        files = self._walk_share(stoken, parsed["pwd_id"], parsed["pdir_fid"], spec.include_subdirs, depth=0)
        # 根目录只有 1 个文件夹时自动下钻（对齐前端「转存文件夹下钻」的直觉）
        if parsed["pdir_fid"] == "0" and len(files) == 1 and files[0].is_dir:
            files = self._walk_share(stoken, parsed["pwd_id"], files[0].fid, spec.include_subdirs, depth=1)
        # 排除清单 + 改名映射
        for f in files:
            f.target_name = spec.rename_map.get(f.name)
        if spec.exclude_names:
            files = [f for f in files if f.name not in spec.exclude_names]
        return files

    def _walk_share(self, stoken: str, pwd_id: str, pdir_fid: str, include_subdirs: bool, depth: int) -> list[ShareFile]:
        out: list[ShareFile] = []
        page = 1
        while True:
            data = self._req(
                "GET",
                "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail",
                params={
                    "pwd_id": pwd_id,
                    "stoken": stoken,
                    "pdir_fid": pdir_fid,
                    "_page": page,
                    "_size": 50,
                    "_fetch_total": 1,
                    "ver": 2,
                },
            )
            d = data.get("data") or {}
            items = d.get("list") or []
            for it in items:
                out.append(
                    ShareFile(
                        fid=str(it.get("fid", "")),
                        fid_token=str(it.get("share_fid_token", "")),
                        name=str(it.get("file_name", "")),
                        is_dir=bool(it.get("dir")),
                        size=int(it.get("size") or 0),
                    )
                )
            total = (d.get("metadata") or {}).get("_total")
            if not items or (total is not None and len(out) >= int(total)) or len(items) < 50:
                break
            page += 1
        if include_subdirs and depth < 8:
            for f in [x for x in out if x.is_dir]:
                out.extend(self._walk_share(stoken, pwd_id, f.fid, True, depth + 1))
        return out

    # ---------- 目标盘操作 ----------

    def ensure_dir(self, dir_path: str) -> str:
        """逐级建目录，返回最末级 fid；已存在视为成功。"""
        fid = "0"
        walked = ""
        for part in [p for p in dir_path.strip("/").split("/") if p]:
            walked += "/" + part
            children = self._list_dir(walked)
            hit = next((c for c in children if c["file_name"] == part and c.get("dir")), None)
            if hit:
                fid = str(hit["fid"])
                continue
            data = self._req(
                "POST",
                "https://drive-pc.quark.cn/1/clouddrive/file",
                json_body={"pdir_fid": fid, "file_name": "", "dir_path": walked, "dir_init_lock": False},
            )
            new_fid = (data.get("data") or {}).get("fid")
            if not new_fid:
                raise AdapterError(f"建目录失败：{walked}")
            fid = str(new_fid)
        return fid

    def _list_dir(self, pdir_fid: str) -> list[dict]:
        """列自己的目录（pdir_fid 支持 fid 或路径；这里传 fid）。"""
        data = self._req(
            "GET",
            "https://drive-pc.quark.cn/1/clouddrive/file/sort",
            params={
                "pdir_fid": pdir_fid,
                "_size": 50,
                "_fetch_total": 1,
                "fetch_all_file": 1,
                "fetch_risk_file_name": 1,  # 不带此参数违规文件名会返回 ***，影响去重
            },
        )
        return (data.get("data") or {}).get("list") or []

    def list_dir_names(self, dir_path: str) -> set[str]:
        fid = self.ensure_dir(dir_path)  # 不存在会顺带建好，语义对转存更顺
        return {str(it.get("file_name")) for it in self._list_dir(fid)}

    def _path_to_fid(self, dir_path: str) -> str:
        """路径 → fid（建好目录树后取末级）。"""
        return self.ensure_dir(dir_path)

    # ---------- 转存 ----------

    def save_files(self, files: list[ShareFile], spec: TaskSpec, on_progress, on_log) -> TransferResult:
        result = TransferResult()
        save_list = [f for f in files if not f.is_dir and f.fid]
        # 去重：目标目录已有同名（按目标名比对，改名后的名字也算已存在）
        existing = self.list_dir_names(spec.save_dir)
        to_fid = self._path_to_fid(spec.save_dir)
        need = []
        for f in save_list:
            target = f.target_name or f.name
            if target in existing or f.name in existing:
                result.skip += 1
            else:
                need.append(f)
        on_log(f"清单 {len(save_list)} 项：跳过 {result.skip} / 待转存 {len(need)}")
        if not need:
            on_progress(100)
            return result

        done = 0
        total = len(need)
        for i in range(0, total, BATCH):
            batch = need[i : i + BATCH]
            self._save_batch(batch, to_fid, spec, result, on_log)
            done += len(batch)
            on_progress(min(99, int(done / total * 100)))
        on_progress(100)
        return result

    def _save_batch(self, batch: list[ShareFile], to_fid: str, spec: TaskSpec, result: TransferResult, on_log) -> None:
        data = self._req(
            "POST",
            "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/save",
            params=self._fake_browse_params(),
            json_body={
                "fid_list": [f.fid for f in batch],
                "fid_token_list": [f.fid_token for f in batch],
                "to_pdir_fid": to_fid,
                "pwd_id": extract_share(spec.share_url)["pwd_id"],
                "stoken": self._stoken,
                "pdir_fid": "0",
                "scene": "link",
            },
        )
        task_id = (data.get("data") or {}).get("task_id")
        if not task_id:
            raise AdapterError(f"转存提交失败：{data.get('message')}")
        top_fids = self._poll_task(task_id)
        # top_fids 与提交 fid_list 顺序对齐；对不齐就不做改名（宁可原名也不改错名）
        for idx, f in enumerate(batch):
            result.add += 1
            entry = {"name": f.target_name or f.name, "fid": ""}
            if idx < len(top_fids):
                entry["fid"] = str(top_fids[idx])
            result.transferred.append(entry)
        if spec.rename_map:
            self._apply_renames(batch, top_fids, result, on_log)

    def _poll_task(self, task_id: str, timeout: float = 120.0) -> list:
        deadline = time.time() + timeout
        retry = 0
        while time.time() < deadline:
            data = self._req(
                "GET",
                "https://drive-pc.quark.cn/1/clouddrive/task",
                params={"task_id": task_id, "retry_index": retry, **self._fake_browse_params()},
            )
            d = data.get("data") or {}
            status = d.get("status")
            if status == 2:
                return ((d.get("save_as") or {}).get("save_as_top_fids")) or []
            retry += 1
            time.sleep(0.5)
        raise AdapterError(f"转存任务轮询超时：{task_id}")

    def _apply_renames(self, batch: list[ShareFile], top_fids: list, result: TransferResult, on_log) -> None:
        for idx, f in enumerate(batch):
            target = f.target_name
            if not target or target == f.name or idx >= len(top_fids):
                continue
            try:
                self._req(
                    "POST",
                    "https://drive-pc.quark.cn/1/clouddrive/file/rename",
                    json_body={"fid": str(top_fids[idx]), "file_name": target},
                )
                result.renamed += 1
            except (AdapterError, CircuitOpen, RateLimited) as e:
                on_log(f"改名失败 {f.name} → {target}：{e}")

    def summary(self) -> dict:
        """会员 + 容量摘要（防御性解析，拿不到的字段为 None）。

        接口为夸克网页端公开接口（社区工具广泛使用）：
        - GET /1/clouddrive/member   会员类型与到期时间
        - GET /1/clouddrive/capacity 容量（含各分类，取合计字段）
        """
        member, cap = None, None
        try:
            data = self._req(
                "GET",
                "https://drive-pc.quark.cn/1/clouddrive/member",
                params=self._fake_browse_params(),
            )
            d = data.get("data") or {}
            mtype = str(d.get("member_type") or "NORMAL")
            expires = d.get("member_expires") or None
            name = {"EXP_SVIP": "体验 SVIP", "SVIP": "SVIP", "VIP": "VIP"}.get(mtype, "普通用户" if mtype == "NORMAL" else mtype)
            member = {"name": name, "expires": str(expires)[:10] if expires else None}
        except (AdapterError, CredentialExpired, CircuitOpen):
            member = None
        try:
            data = self._req(
                "GET",
                "https://drive-pc.quark.cn/1/clouddrive/capacity",
                params=self._fake_browse_params(),
            )
            d = data.get("data") or {}
            total = d.get("total_capacity")
            if not total:
                # 有些版本把合计放在 file_* 分类里，做一次兜底求和
                total = sum(v for k, v in d.items() if isinstance(v, (int, float)) and k.startswith(("total", "use")))
            used = d.get("use_capacity") or d.get("used_capacity")
            if total:
                cap = {"total": int(total), "used": int(used or 0)}
        except (AdapterError, CredentialExpired, CircuitOpen):
            cap = None
        return {"capacity": cap, "vip": member}

    # stoken 在 list_share 与 save 之间复用，避免重复请求

    def prepare(self, spec: TaskSpec) -> dict:
        """取 stoken 并缓存（save 批次复用），返回解析结果。"""
        parsed = extract_share(spec.share_url)
        self._stoken = self.get_stoken(parsed["pwd_id"], parsed["passcode"])
        return parsed
