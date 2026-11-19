"""培训采购与岗位匹配分析包。

只使用 Python 标准库。所有分析都基于某个决策时点的不可变快照，
往届证据与本期采购判断分开建模，结论可按用工组追溯。
"""

from .timeutil import days_between, parse_date, to_date  # noqa: F401
from .snapshot import Snapshot, load_snapshot  # noqa: F401

__all__ = ["Snapshot", "load_snapshot", "parse_date", "to_date", "days_between"]
