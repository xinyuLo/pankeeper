"""资源名清洗：pansou 频道给的 note 常带 emoji/装饰符号，直接拿去当文件夹名
会在百度/夸克撞上非法字符（errno=2 实锤案），展示侧也是噪音。统一在这里洗。

规则（宁松勿紧——只删确定是垃圾的，别伤到正经字符）：
- emoji/符号区段：SMP 全段（U+1F000+，含旗帜/表情/杂物）、杂项符号与 Dingbats
  （U+2600-27BF）、星号箭头（U+2B00-2BFF）、其他符号 pin（U+2E80 前的零散段不碰）；
- 变体选择符 / ZWJ / 零宽字符：emoji 组合的胶水，本体删了它们就是孤儿；
- 网盘文件名非法字符 \\ / : * ? " < > | ：替换成空格；
- 连续空白折叠、掐头去尾。全洗空返回空串，由调用方决定兜底名。
"""
from __future__ import annotations

import re

_JUNK_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # SMP：表情/旗帜/符号/装饰（emoji 主体全在这）
    "\U00002600-\U000027BF"  # 杂项符号 + Dingbats（☀✂✔…）
    "\U00002B00-\U00002BFF"  # ⭐⭕ 等星形箭头
    "\U0000FE00-\U0000FE0F"  # 变体选择符（emoji 文本/彩色的胶水）
    "\U0000200D"             # ZWJ（组合 emoji 的连接符）
    "\U0000200B-\U0000200F"  # 零宽空格/连接符/方向标记
    "\U000020E3"             # keycap 组合符（1️⃣ 的方框）
    "]+"
)
_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|]')
_WS_RE = re.compile(r"\s+")


def sanitize_name(name: str) -> str:
    """洗掉 emoji/装饰符号与文件名非法字符；返回可能为空的字符串。"""
    if not name:
        return ""
    cleaned = _JUNK_RE.sub("", name)
    cleaned = _ILLEGAL_RE.sub(" ", cleaned)
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    # 洗完可能剩下孤立的「（）【】」或悬挂标点——只处理明显的空壳：全是括号/标点
    if cleaned and not re.search(r"[\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", cleaned):
        return ""
    return cleaned
