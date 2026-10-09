"""转存业务流程层：手动（manual.py，搜索页入口）与自动（auto.py，定时任务）两条路径。

⚠️ 两边刻意不共享流程代码——业务规则不同（熔断/回写/推送策略各异），
宁可重复也别互相牵连。网盘 API 层见 adapters/（每盘独立文件）。
"""
from .auto import run_auto
from .manual import run_manual

__all__ = ["run_manual", "run_auto"]
