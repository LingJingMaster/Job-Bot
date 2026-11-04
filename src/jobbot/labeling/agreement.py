"""二次加权 κ。只对调用方给的两组评分做计算，不写报告、不读库。"""

from __future__ import annotations

from collections.abc import Sequence

from jobbot.contracts import JobBotError

_CATEGORIES = (0, 1, 2, 3)
_SCALE = len(_CATEGORIES) - 1


def quadratic_weighted_kappa(left: Sequence[int], right: Sequence[int]) -> float:
    """同一批岗位上的两组评分。下标一一对应。权重是 (i-j)^2 / 3^2。

    返回值只供合成样本或以后的评测脚本使用。本函数不写一致性报告。
    """
    if len(left) != len(right):
        raise JobBotError(
            "两组标注条数不同，无法计算二次加权 κ。没有写成一致性报告。",
            saved=False,
            next_step="按同一批岗位对齐后再计算。",
        )
    if not left:
        raise JobBotError(
            "没有成对标注，无法计算二次加权 κ。没有写成一致性报告。",
            saved=False,
            next_step="先准备同一批岗位上的两组评分。",
        )
    pairs = [(_rating(item), _rating(other)) for item, other in zip(left, right, strict=True)]
    observed = [[0.0 for _ in _CATEGORIES] for _ in _CATEGORIES]
    left_counts = [0.0 for _ in _CATEGORIES]
    right_counts = [0.0 for _ in _CATEGORIES]
    for left_rating, right_rating in pairs:
        observed[left_rating][right_rating] += 1.0
        left_counts[left_rating] += 1.0
        right_counts[right_rating] += 1.0
    total = float(len(pairs))
    weighted_observed = 0.0
    weighted_expected = 0.0
    for left_rating in _CATEGORIES:
        for right_rating in _CATEGORIES:
            weight = ((left_rating - right_rating) ** 2) / (_SCALE**2)
            expected = left_counts[left_rating] * right_counts[right_rating] / total
            weighted_observed += weight * observed[left_rating][right_rating]
            weighted_expected += weight * expected
    if weighted_expected == 0:
        return 1.0 if weighted_observed == 0 else 0.0
    return 1.0 - weighted_observed / weighted_expected


def _rating(value: object) -> int:
    if value is None:
        raise JobBotError(
            "有标注是空的。跳过没有分数，不能当成 0 分计算二次加权 κ。没有写成一致性报告。",
            saved=False,
            next_step="去掉未评分的岗位，或只对已保存的评分计算。",
        )
    if isinstance(value, bool) or not isinstance(value, int) or value not in _CATEGORIES:
        raise JobBotError(
            "二次加权 κ 只接受 0 到 3 的整数评分。没有写成一致性报告。",
            saved=False,
            next_step="检查两组评分是否都在 0 到 3。",
        )
    return value
