"""向量接口。没有锁定模型或没有注入向量时，不返回假分数。"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from typing import Literal

from jobbot.config import Settings, load_config
from jobbot.contracts import JobBotError

Embedder = Callable[[list[str], str], list[list[float]]]

MAX_SEGMENT_CHARS = 512
SEGMENT_AGGREGATION = "max_cosine_over_segments"
QUERY_INSTRUCTION = ""

_injected: tuple[Embedder, str] | None = None


def use_injected_embedder(embedder: Embedder | None, revision: str = "") -> None:
    """测试注入假向量。revision 用来把向量存到单独目录。"""
    global _injected
    if embedder is None:
        _injected = None
        return
    cleaned = revision.strip()
    if not cleaned:
        raise JobBotError(
            "注入向量时没有给出 revision。向量没有写入，也没有排序。",
            saved=False,
            next_step="为这组假向量指定一个非空 revision。",
        )
    _injected = (embedder, cleaned)


def embedding_status(settings: Settings | None = None) -> tuple[bool, str]:
    current = settings or load_config()
    if _injected is not None:
        return True, ""
    revision = current.embedding_revision.strip()
    if not revision:
        return (
            False,
            "EMBEDDING_REVISION 为空，本地 embedding 模型未锁定。B1/B2/B3 没有排序。",
        )
    if not current.embedding_model_exists():
        return (
            False,
            "EMBEDDING_REVISION 已填写，但模型文件不在 data/models/<revision>。B1/B2/B3 没有排序。",
        )
    return (
        False,
        "模型文件在，但当前环境没有向量加载库，也不会下载 BAAI 模型。B1/B2/B3 没有排序。",
    )


def active_revision(settings: Settings | None = None) -> str:
    if _injected is not None:
        return _injected[1]
    current = settings or load_config()
    return current.embedding_revision.strip()


def embed_texts(texts: list[str], kind: Literal["query", "doc"]) -> list[list[float]]:
    ready, message = embedding_status()
    if not ready:
        raise JobBotError(
            message,
            saved=False,
            next_step="锁定本地模型，或在测试里注入假向量。不要把这次当成已排序。",
        )
    revision = active_revision()
    vectors: list[list[float]] = []
    for text in texts:
        segment_vectors = _embed_segments(text, kind, revision)
        vectors.append(_representative(segment_vectors))
    return vectors


def segment_vectors(text: str, kind: Literal["query", "doc"]) -> list[list[float]]:
    ready, message = embedding_status()
    if not ready:
        raise JobBotError(message, saved=False, next_step="锁定本地模型，或注入假向量。")
    return _embed_segments(text, kind, active_revision())


def vector_path(revision: str, kind: str, text: str):
    digest = hashlib.sha256(f"{kind}\n{text}".encode("utf-8")).hexdigest()
    return load_config().data_dir / "embeddings" / revision / f"{digest}.json"


def embedding_config(settings: Settings | None = None) -> dict[str, object]:
    current = settings or load_config()
    revision = active_revision(current)
    return {
        "model": current.embedding_model,
        "revision": revision,
        "provider": "injected" if _injected is not None else "not_run",
        "downloaded": False,
        "sklearn": None,
        "dimension": None,
        "max_segment_chars": MAX_SEGMENT_CHARS,
        "tokenizer_measured": False,
        "query_instruction": False,
        "query_instruction_text": QUERY_INSTRUCTION,
        "normalize": "l2",
        "segment_aggregation": SEGMENT_AGGREGATION,
    }


def max_mapped_cosine(left_text: str, right_text: str) -> float | None:
    if not left_text.strip() or not right_text.strip():
        return None
    left_segments = segment_vectors(left_text, "query")
    right_segments = segment_vectors(right_text, "doc")
    if not left_segments or not right_segments:
        return None
    best = -1.0
    for left in left_segments:
        for right in right_segments:
            best = max(best, _cosine(left, right))
    return _map_unit(best)


def _embed_segments(text: str, kind: str, revision: str) -> list[list[float]]:
    pieces = _segments(_prepare(text, kind))
    if not pieces:
        return []
    stored: list[list[float]] = []
    missing: list[str] = []
    for piece in pieces:
        path = vector_path(revision, kind, piece)
        if path.is_file():
            stored.append(_read_vector(path))
        else:
            missing.append(piece)
            stored.append([])
    if missing:
        fresh = _normalize_many(_produce(missing, kind))
        if len(fresh) != len(missing):
            raise JobBotError(
                "向量条数和文本条数不一致。这次没有写入新的排序。",
                saved=False,
                next_step="检查注入的 embedder 是否按原文顺序返回向量。",
            )
        cursor = 0
        for index, piece in enumerate(pieces):
            if stored[index]:
                continue
            vector = fresh[cursor]
            cursor += 1
            _write_new(vector_path(revision, kind, piece), revision, kind, piece, vector)
            stored[index] = vector
    return stored


def _produce(texts: list[str], kind: str) -> list[list[float]]:
    if _injected is None:
        _ready, message = embedding_status()
        raise JobBotError(message or "没有可用向量。", saved=False, next_step="注入假向量或锁定模型。")
    produced = _injected[0](texts, kind)
    if not isinstance(produced, list) or len(produced) != len(texts):
        raise JobBotError(
            "注入向量的数量和文本数量不一致。没有排序。",
            saved=False,
            next_step="让 embedder 对每段文本返回一条向量。",
        )
    vectors: list[list[float]] = []
    for vector in produced:
        if not isinstance(vector, list) or not vector:
            raise JobBotError("注入向量为空。没有排序。", saved=False, next_step="返回非空的浮点向量。")
        vectors.append([float(item) for item in vector])
    return vectors


def _prepare(text: str, kind: str) -> str:
    if kind == "query" and QUERY_INSTRUCTION:
        return QUERY_INSTRUCTION + text
    return text


def _segments(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    return [
        stripped[start : start + MAX_SEGMENT_CHARS]
        for start in range(0, len(stripped), MAX_SEGMENT_CHARS)
    ]


def _representative(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    return max(vectors, key=lambda vector: sum(item * item for item in vector))


def _normalize_many(vectors: list[list[float]]) -> list[list[float]]:
    return [_l2(vector) for vector in vectors]


def _l2(vector: list[float]) -> list[float]:
    square = sum(item * item for item in vector)
    if square <= 0.0:
        return vector
    scale = math.sqrt(square)
    return [item / scale for item in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        if not left or not right:
            return 0.0
        width = min(len(left), len(right))
        left = left[:width]
        right = right[:width]
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    if dot < -1.0:
        return -1.0
    if dot > 1.0:
        return 1.0
    return dot


def _map_unit(cosine: float) -> float:
    mapped = (cosine + 1.0) / 2.0
    if mapped < 0.0:
        return 0.0
    if mapped > 1.0:
        return 1.0
    return mapped


def _write_new(path, revision: str, kind: str, text: str, vector: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    payload = {
        "revision": revision,
        "kind": kind,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "normalized": True,
        "vector": vector,
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    if path.exists():
        temporary.unlink(missing_ok=True)
        return
    temporary.replace(path)


def _read_vector(path) -> list[float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    vector = payload.get("vector")
    if not isinstance(vector, list) or not vector:
        raise JobBotError(
            f"向量文件 {path.name} 无法读取。没有改用其他 revision 的向量。",
            saved=False,
            next_step="删除损坏的向量文件后按当前 revision 重新计算。",
        )
    return [float(item) for item in vector]
