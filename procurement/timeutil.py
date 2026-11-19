"""ISO 日期工具；全项目统一用 date 比较，避免字符串比较出错。"""

from __future__ import annotations

from datetime import date

DATE_FMT = "%Y-%m-%d"


def parse_date(value: str) -> date:
    """解析 YYYY-MM-DD。"""
    year, month, day = (int(part) for part in value.split("-"))
    return date(year, month, day)


def to_date(value) -> date:
    """字符串转 date，date 原样返回。"""
    if isinstance(value, date):
        return value
    return parse_date(value)


def days_between(later: str | date, earlier: str | date) -> int:
    """later - earlier 的天数。"""
    return (to_date(later) - to_date(earlier)).days
