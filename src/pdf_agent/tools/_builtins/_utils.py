"""内建工具共用的辅助函数。"""
from __future__ import annotations


def to_bool(value: object) -> bool:
    """把宽松类型的输入值转换成布尔值。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)
