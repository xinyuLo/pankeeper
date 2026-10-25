"""联动目标解析（T-012 / D-1 方案 B）：QMS/LitePan/STRM 目标与目录开关的解析收口。

从 auto.py（_hit_dd_dir / dir_only_video / resolve_media_link / resolve_litepan_link）
与 manual.py（_match_dd_link / _strm_for_qms）**原样搬来**——两套解析刻意不合并：
auto 版带总闸语义（qms_on 关 = 一律未配），manual 版带显式 qmsId/strmId 优先级与
最长前缀匹配；合并属行为变更，另开卡。auto / manual（以及经 auto 再导出的
api 层旧引用）都从本模块取。本模块不 import auto/manual（防循环，见结构测试）。

唯一的非逐字改动（T-011 日志预告、T-012 落地）：resolve_media_link 的 STRM 自动
配对兜底原来是 `from .qms import strm_id_for_qms`——app/transfer/qms 模块并不存在，
该分支真被执行必 ModuleNotFoundError（改前就是坏的）；改为模块级 services.qms 的
同名函数（与 manual._strm_for_qms 同款通道）。除这条必崩分支外零行为变化。
"""
from __future__ import annotations

from ..db import SessionLocal
from ..models import DdItem, PaTask
from ..services import qms
from ..services.settings_svc import get_group


def _hit_dd_dir(path: str) -> dict | None:
    """save_dir 命中的转存配置目录（最长前缀优先）。

    ⚠️ **刻意不过滤 `qms_on`**：调用方需要能区分「没登记这个目录」和「登记了但把 QMS 关了」。
    后者代表用户明确关掉了联动开关 —— 那时任务级配置也该一并作废（2026-10-04 用户定稿）。
    """
    if not path:
        return None
    hit = None
    with SessionLocal() as s:
        for d in s.query(DdItem).all():
            base = (d.path or "").rstrip("/")
            if not base:
                continue
            if path == base or path.startswith(base + "/"):
                if hit is None or len(base) > len(hit["path"]):
                    hit = {
                        "path": base, "qms_on": bool(d.qms_on), "qms_id": d.qms_id,
                        "strm_id": d.strm_id, "only_video": bool(d.only_video),
                    }
    return hit


def dir_only_video(path: str) -> bool:
    """目标目录是否开了「过滤其他文件」（save_dir 前缀最长匹配；没命中任何目录 = False）。

    开启后转到此目录只保存视频文件（adapters.base.VIDEO_EXTS），nfo/海报图片等
    杂件在转存环节直接剔除（2026-10-07 定稿）。"""
    hit = _hit_dd_dir(path or "")
    return bool(hit and hit["only_video"])


def resolve_media_link(path: str, task_id: int | None = None) -> dict | None:
    """解析一个任务要用的 QMS/STRM 联动目标。

    判定顺序（2026-10-04 用户定稿："那个目录要是关闭了，就等于没配"）：
    ① **总闸**：`save_dir` 命中的「转存配置」目录若 `qms_on=False` → **一律视为未配**，
       哪怕任务弹窗里显式选了 QMS/STRM 目录也不联动（否则会出现"目录明明关了、任务还在偷偷联动"
       这种没人能预期的事）；
    ② 总闸开着（或 `save_dir` 没命中任何目录）时：**任务弹窗里选的 `qms_id`/`strm_id` 优先**，
       没填的字段各自回退到命中目录的值（目录级 STRM 下拉 2026-10-09 恢复）；
    ③ 两级都没填 STRM：按「刮削目录整理目标根 = 同步路径来源路径」自动配对（strm_id_for_qms）。

    历史：执行侧原只读目录级 DdItem，任务弹窗配的 `strm_id` 被无视 —— 用户任务 1 配了 5，
    代码却拿到目录的 None，STRM 永远不触发（2026-10-04 用户实拍发现）。
    """
    qms_id: int | None = None
    strm_id: int | None = None
    if task_id:
        with SessionLocal() as s:
            row = s.get(PaTask, task_id)
            if row is not None:
                qms_id, strm_id = row.qms_id, row.strm_id
    hit = _hit_dd_dir(path or "")
    if hit is not None and not hit["qms_on"]:
        return None  # 目录关了联动 → 未配（任务里选过什么一律作废）
    dq = hit["qms_id"] if hit else None
    ds = hit["strm_id"] if hit else None
    rq = qms_id or dq
    if not rq:
        return None
    rs = strm_id if strm_id is not None else ds
    if rs is None:
        # STRM 自动配对兜底（2026-10-09 恢复目录级 STRM 下拉后为最后一级）：
        # 目录级和任务级都没填时，按「刮削目录整理目标根 = 同步路径来源路径」配对
        rs = qms.strm_id_for_qms(rq)
    return {"qms_id": rq, "strm_id": rs}


def resolve_litepan_link(path: str, task_id: int | None = None, require_lp_on: bool = True) -> dict | None:
    """LitePan 联动目标解析。

    闸门（2026-10-07 用户定稿更新）：
    ① 设置页 LitePan「启用联动」总闸关 → 全部不推；
    ② **自动转存（require_lp_on=False）不再要求转存配置目录开 lp_on**——系统设置选了
       litepan，自动转存就直接走 LitePan；搜索转存（require_lp_on=True）仍按目录
       lp_on 匹配（没配 → None，弹窗/日志如实说明）；
    ③ **事件名没填就不联动**（无全局兜底）：任务弹窗和目录配置都没填事件 → None。
    事件名优先级：任务弹窗 > 目录配置（按 save_dir 前缀最长匹配，不再要求 lp_on）。"""
    if not get_group("litepan").get("enabled"):
        return None
    hit: DdItem | None = None
    with SessionLocal() as s:
        query = s.query(DdItem)
        if require_lp_on:
            query = query.filter(DdItem.lp_on.is_(True))
        for d in query.all():
            if path == d.path or path.startswith(d.path.rstrip("/") + "/"):
                if hit is None or len(d.path) > len(hit.path):
                    hit = d  # 最长前缀优先
    task_event = ""
    if task_id:
        with SessionLocal() as s:
            row = s.get(PaTask, task_id)
            if row is not None:
                task_event = (row.lp_event or "").strip()
    dir_event = (hit.lp_event or "").strip() if hit else ""
    event = task_event or dir_event
    if not event:
        return None  # 事件名没填 = 不联动（无兜底）
    return {"event": event}


def _strm_for_qms(qms_id: int) -> int | None:
    """与 QMS 配对的 STRM 同步路径：**QMS 侧自动配对**（刮削整理目标根 ↔ 同步路径，
    2026-10-04 用户定稿：转存配置里不再单独选 STRM）；QMS 侧拿不到再回退转存配置
    里存的 strm_id（历史数据）。"""
    sid = qms.strm_id_for_qms(qms_id)
    if sid:
        return sid
    with SessionLocal() as s:
        d = s.query(DdItem).filter(DdItem.qms_id == int(qms_id), DdItem.qms_on.is_(True)).first()
        return d.strm_id if d and d.strm_id else None


def _match_dd_link(path: str) -> dict | None:
    """转存目录 → 配置的 QMS/STRM 关联：前缀命中里取**最长**（最具体的配置赢），
    与添加顺序无关（2026-10-13 清单七：嵌套目录时旧实现按库序首个命中，会命中宽的那条；
    _hit_dd_dir 一直是正确写法，这边看齐）。"""
    with SessionLocal() as s:
        rows = s.query(DdItem).filter(DdItem.qms_on.is_(True), DdItem.qms_id.isnot(None)).all()
        for d in sorted(rows, key=lambda x: len(x.path or ""), reverse=True):
            if path == d.path or path.startswith(d.path.rstrip("/") + "/"):
                return {"qms_id": d.qms_id, "strm_id": d.strm_id}
    return None
