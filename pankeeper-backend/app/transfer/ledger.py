"""本地已转存台账（T-002）：转存前查账去重 + 转存成功落账 + 手工清理。

QMS 整理时把文件从待整理目录**移动**进媒体库（源文件消失），网盘基线（T-001 的
compare_path 递归扫描）看得到库内文件，但文件再被移走/基线缓存陈旧时仍可能判成
新增再转一遍。台账是最后一道保险：转存过的文件记在本地库里，不依赖网盘现状。

分层约束：适配器层不感知台账（不 import 模型）——查账/落账都在本模块，
auto.py / manual.py 在调用 adapter.save_files 前后接线；去重顺序为先查台账
（命中直接跳过，不再走网盘基线），未命中才进适配器。
"""
from __future__ import annotations

import time

from ..db import SessionLocal
from ..models import TransferLedger


def ledger_lookup(drive: str, task_id: int, save_dir: str) -> tuple[set[str], set[str]]:
    """查台账：该 (drive, task_id, save_dir) 下已转存的名字与 MD5 集合。

    查不了（本地库故障）按无台账处理——保险不能反过来挡转存。"""
    names: set[str] = set()
    md5s: set[str] = set()
    try:
        with SessionLocal() as s:
            rows = (
                s.query(TransferLedger)
                .filter(
                    TransferLedger.drive == drive,
                    TransferLedger.task_id == int(task_id or 0),
                    TransferLedger.save_dir == save_dir,
                )
                .all()
            )
            for r in rows:
                if r.name:
                    names.add(r.name)
                if r.md5:
                    md5s.add(r.md5)
    except Exception as e:  # noqa: BLE001
        print(f"[ledger] 台账读取失败（本次按无台账处理）：{e}")
    return names, md5s


def ledger_prefilter(drive: str, task_id: int, save_dir: str, files: list, on_log=None) -> tuple[list, list[dict]]:
    """转存前查台账过滤（去重第一道，先于适配器内的网盘基线）。

    文件名（原名或目标名）或 MD5 任一命中即视为已转存。目录条目永远保留
    （结构/勾选匹配要用）。返回 (剩余清单, 命中名单)；名单元素 {name, md5}
    （md5=文件自身 MD5，按名字命中的可能为空，T-017）。判定条件与顺序一字未动。
    全部命中时调用方应直接短路 save_files——连网盘基线都不查（零网盘请求）。"""
    names, md5s = ledger_lookup(drive, task_id, save_dir)
    if not names and not md5s:
        return files, []
    kept: list = []
    hits: list[dict] = []
    for f in files:
        if (
            not f.is_dir
            and (f.name in names or (f.target_name or "") in names or (bool(f.md5) and f.md5 in md5s))
        ):
            hits.append({"name": f.name, "md5": f.md5 or ""})
            continue
        kept.append(f)
    if hits and on_log:
        on_log(f"本地台账命中 {len(hits)} 个已转存文件，直接跳过（不查网盘基线）")
    return kept, hits


def ledger_write(drive: str, acc_id: int | None, task_id: int, share_url: str, save_dir: str, files: list, result) -> int:
    """转存成功后落账：TransferResult.transferred 逐条写入（唯一键已存在 = 记过，跳过）。

    md5 从 files 按名字映射（transferred 条目只有 name/fid）；target_name 与原名
    都建索引以便回查。写失败不抛——台账是保险，不能把成功的转存拖成失败。"""
    if not result or not result.transferred:
        return 0
    md5_by_name: dict[str, str] = {}
    for f in files or []:
        if f.is_dir or not f.md5:
            continue
        md5 = f.md5.lower()
        md5_by_name[f.target_name or f.name] = md5
        md5_by_name.setdefault(f.name, md5)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    written = 0
    try:
        with SessionLocal() as s:
            for e in result.transferred:
                name = str(e.get("name") or "").strip()
                if not name:
                    continue
                exists = (
                    s.query(TransferLedger.id)
                    .filter(
                        TransferLedger.drive == drive,
                        TransferLedger.task_id == int(task_id or 0),
                        TransferLedger.save_dir == save_dir,
                        TransferLedger.name == name,
                    )
                    .first()
                )
                if exists:
                    continue
                s.add(
                    TransferLedger(
                        drive=drive,
                        acc_id=int(acc_id or 0),
                        task_id=int(task_id or 0),
                        share_url=share_url or "",
                        name=name,
                        md5=md5_by_name.get(name, ""),
                        save_dir=save_dir,
                        transferred_at=now,
                    )
                )
                written += 1
            s.commit()
    except Exception as e:  # noqa: BLE001
        print(f"[ledger] 台账写入失败（不影响转存结果）：{e}")
    return written


def ledger_clear(task_id: int | None = None, drive: str | None = None, name: str | None = None, save_dir: str | None = None) -> int:
    """清理台账（DELETE /api/pa/ledger）：按 task_id / drive / name / save_dir 任意
    组合过滤删除；全部缺省 = 清空整表。清掉的记录不再拦截转存（手工删了网盘文件
    想重转时用）。返回删除条数。"""
    with SessionLocal() as s:
        q = s.query(TransferLedger)
        if task_id is not None:
            q = q.filter(TransferLedger.task_id == int(task_id))
        if drive:
            q = q.filter(TransferLedger.drive == drive)
        if name:
            q = q.filter(TransferLedger.name == name)
        if save_dir:
            q = q.filter(TransferLedger.save_dir == save_dir)
        n = q.delete(synchronize_session=False)
        s.commit()
        return n
