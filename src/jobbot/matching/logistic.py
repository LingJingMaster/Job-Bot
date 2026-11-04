"""L2 逻辑回归。只用标准库，预测 rating ≥ 2 的模型分数。"""

from __future__ import annotations

import math
import random

FEATURE_ORDER = ["f_sem", "f_skill", "f_resp", "f_pref", "f_tfidf", "f_exp"]
DEFAULT_C = 1.0
DEFAULT_SEED = 0
DEFAULT_EPOCHS = 300
DEFAULT_LEARNING_RATE = 0.2
OBJECTIVE = "mean_logistic_loss + (1/(2C)) * ||w||^2 ; bias 不惩罚"


def fit_predict(
    rows: list[dict[str, float | None]],
    labels: list[int],
    *,
    c: float = DEFAULT_C,
    seed: int = DEFAULT_SEED,
    epochs: int = DEFAULT_EPOCHS,
    learning_rate: float = DEFAULT_LEARNING_RATE,
) -> tuple[list[float], dict[str, object]]:
    if c <= 0:
        raise ValueError("C 必须大于 0")
    if len(rows) != len(labels) or not rows:
        raise ValueError("标注行和特征行数量不一致")
    matrix, used_names, standardize = _design(rows, rows)
    targets = [1.0 if label >= 2 else 0.0 for label in labels]
    weights, bias = _train(matrix, targets, c=c, seed=seed, epochs=epochs, learning_rate=learning_rate)
    scores = [_sigmoid(_dot(weights, row) + bias) for row in matrix]
    report = {
        "implementation": "stdlib_l2_logistic_regression",
        "sklearn": None,
        "objective": OBJECTIVE,
        "C": c,
        "seed": seed,
        "epochs": epochs,
        "learning_rate": learning_rate,
        "feature_order": used_names,
        "weights": weights,
        "bias": bias,
        "standardize": standardize,
        "positive_is": "rating>=2",
        "score_meaning": "模型给出的 rating≥2 分数，不是录用概率",
    }
    return scores, report


def predict(
    rows: list[dict[str, float | None]],
    report: dict[str, object],
) -> tuple[list[float], list[list[str]]]:
    feature_order = [str(name) for name in report["feature_order"]]  # type: ignore[index]
    standardize = report["standardize"]
    if not isinstance(standardize, dict):
        raise ValueError("缺少标准化参数")
    matrix, imputed = _apply(rows, feature_order, standardize)
    weights = [float(item) for item in report["weights"]]  # type: ignore[index]
    bias = float(report["bias"])  # type: ignore[arg-type]
    scores = [_sigmoid(_dot(weights, row) + bias) for row in matrix]
    return scores, imputed


def _design(
    rows: list[dict[str, float | None]],
    fit_rows: list[dict[str, float | None]],
) -> tuple[list[list[float]], list[str], dict[str, object]]:
    names = [name for name in FEATURE_ORDER if any(row.get(name) is not None for row in fit_rows)]
    if not names:
        raise ValueError("训练特征全部缺失")
    means: list[float] = []
    for name in names:
        observed = [float(row[name]) for row in fit_rows if row.get(name) is not None]
        means.append(sum(observed) / len(observed))
    filled, _imputed = _fill(fit_rows, names, means)
    stds: list[float] = []
    for index, _name in enumerate(names):
        column = [row[index] for row in filled]
        center = sum(column) / len(column)
        variance = sum((item - center) ** 2 for item in column) / len(column)
        stds.append(math.sqrt(variance) if variance > 1e-12 else 1.0)
    standardized = _scale(filled, means, stds)
    return standardized, names, {"mean": means, "std": stds, "impute_mean": means, "feature_order": names}


def _apply(
    rows: list[dict[str, float | None]],
    names: list[str],
    standardize: dict[str, object],
) -> tuple[list[list[float]], list[list[str]]]:
    means = [float(item) for item in standardize["impute_mean"]]  # type: ignore[index]
    stds = [float(item) for item in standardize["std"]]  # type: ignore[index]
    filled, imputed = _fill(rows, names, means)
    return _scale(filled, means, stds), imputed


def _fill(
    rows: list[dict[str, float | None]],
    names: list[str],
    means: list[float],
) -> tuple[list[list[float]], list[list[str]]]:
    filled: list[list[float]] = []
    imputed: list[list[str]] = []
    for row in rows:
        values: list[float] = []
        missing: list[str] = []
        for index, name in enumerate(names):
            value = row.get(name)
            if value is None:
                values.append(means[index])
                missing.append(name)
            else:
                values.append(float(value))
        filled.append(values)
        imputed.append(missing)
    return filled, imputed


def _scale(rows: list[list[float]], means: list[float], stds: list[float]) -> list[list[float]]:
    scaled: list[list[float]] = []
    for row in rows:
        scaled.append([(value - means[index]) / stds[index] for index, value in enumerate(row)])
    return scaled


def _train(
    matrix: list[list[float]],
    targets: list[float],
    *,
    c: float,
    seed: int,
    epochs: int,
    learning_rate: float,
) -> tuple[list[float], float]:
    width = len(matrix[0])
    count = len(matrix)
    weights = [0.0] * width
    bias = 0.0
    order = list(range(count))
    random.Random(seed).shuffle(order)
    regularizer = 1.0 / c
    for _epoch in range(epochs):
        for index in order:
            row = matrix[index]
            error = _sigmoid(_dot(weights, row) + bias) - targets[index]
            for feature_index, value in enumerate(row):
                gradient = error * value + regularizer * weights[feature_index] / count
                weights[feature_index] -= learning_rate * gradient
            bias -= learning_rate * error
    return weights, bias


def _dot(weights: list[float], row: list[float]) -> float:
    return sum(weight * value for weight, value in zip(weights, row, strict=True))


def _sigmoid(value: float) -> float:
    if value >= 30.0:
        return 1.0
    if value <= -30.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))
