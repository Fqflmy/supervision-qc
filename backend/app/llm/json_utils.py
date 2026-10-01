# -*- coding: utf-8 -*-
"""JSON 解析工具：容忍 Markdown 代码块、前后缀文本、轻微语法错误。"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def strip_code_fence(text: str) -> str:
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _find_balanced(text: str, open_ch: str, close_ch: str) -> Optional[str]:
    start = text.find(open_ch)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    return None


def extract_json(text: str) -> Any:
    """从模型输出中提取 JSON 对象或数组。失败抛 ValueError。"""
    if text is None:
        raise ValueError("内容为空")
    candidate = strip_code_fence(text)

    for loader in (_strict_load, _loose_load):
        try:
            return loader(candidate)
        except Exception:  # noqa: BLE001
            pass

    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        snippet = _find_balanced(candidate, open_ch, close_ch)
        if snippet:
            for loader in (_strict_load, _loose_load):
                try:
                    return loader(snippet)
                except Exception:  # noqa: BLE001
                    pass
    raise ValueError("未找到合法 JSON")


def _strict_load(text: str) -> Any:
    return json.loads(text)


def _loose_load(text: str) -> Any:
    """常用修补：尾逗号、中文引号、单引号键、未转义换行。"""
    fixed = text
    fixed = fixed.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    # 任意位置的尾逗号（对象/数组末尾，含字符串值后的尾逗号）
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
    fixed = re.sub(r"'([^'\\]*)'\s*:", r'"\1":', fixed)
    try:
        import json_repair  # type: ignore

        repaired = json_repair.loads(fixed)
        if repaired not in (None, "", {}):
            return repaired
    except Exception:  # noqa: BLE001
        pass
    return json.loads(fixed)


def dumps_cn(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


__all__ = ["extract_json", "strip_code_fence", "dumps_cn"]
