from __future__ import annotations

import re

_JUNK_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U00002B00-\U00002BFF"
    "\U0000FE00-\U0000FE0F"
    "\U0000200D"
    "\U0000200B-\U0000200F"
    "\U000020E3"
    "]+"
)
_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|]')
_WS_RE = re.compile(r"\s+")

def sanitize_name(name: str) -> str:
    if not name:
        return ""
    cleaned = _JUNK_RE.sub("", name)
    cleaned = _ILLEGAL_RE.sub(" ", cleaned)
    cleaned = _WS_RE.sub(" ", cleaned).strip()

    if cleaned and not re.search(r"[\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", cleaned):
        return ""
    return cleaned
