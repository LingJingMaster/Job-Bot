"""字符 n-gram TF-IDF，用标准库实现。"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from jobbot.config import load_config
from jobbot.contracts import JobBotError, TfidfConfig, TfidfIndex

IMPLEMENTATION = "stdlib_char_ngram_tfidf"


@dataclass(frozen=True)
class FittedTfidf:
    idf: dict[str, float]
    config: dict[str, object]
    n_docs: int
    vocab_sha256: str
    vocab_path: str

    def transform(self, text: str) -> dict[str, float]:
        counts = char_ngrams(
            text,
            nmin=int(self.config["ngram_min"]),
            nmax=int(self.config["ngram_max"]),
            lowercase=bool(self.config["lowercase"]),
        )
        raw: dict[str, float] = {}
        sublinear = bool(self.config["sublinear_tf"])
        for gram, count in counts.items():
            weight = self.idf.get(gram)
            if weight is None or count <= 0:
                continue
            tf = 1.0 + math.log(count) if sublinear else float(count)
            raw[gram] = tf * weight
        return _l2_normalize(raw)


def resolved_tfidf_config(cfg: TfidfConfig | None = None) -> dict[str, object]:
    source = cfg or TfidfConfig()
    nmin, nmax = source.ngram_range
    min_df: int | float = 1 if source.min_df is None else source.min_df
    return {
        "implementation": IMPLEMENTATION,
        "sklearn": None,
        "analyzer": source.analyzer,
        "ngram_range": [nmin, nmax],
        "ngram_min": nmin,
        "ngram_max": nmax,
        "norm": source.norm,
        "sublinear_tf": False if source.sublinear_tf is None else bool(source.sublinear_tf),
        "min_df": min_df,
        "min_df_kind": "proportion" if isinstance(min_df, float) else "count",
        "use_idf": True,
        "smooth_idf": True,
        "lowercase": True,
        "token_pattern": None,
    }


def char_ngrams(text: str, *, nmin: int, nmax: int, lowercase: bool) -> dict[str, int]:
    prepared = text.lower() if lowercase else text
    counts: dict[str, int] = {}
    length = len(prepared)
    for size in range(nmin, nmax + 1):
        if length < size:
            continue
        for start in range(0, length - size + 1):
            gram = prepared[start : start + size]
            counts[gram] = counts.get(gram, 0) + 1
    return counts


def fit_texts(texts: list[str], params: dict[str, object], *, corpus_ids: list[str]) -> FittedTfidf:
    if not texts:
        raise JobBotError(
            "没有传入训练语料，字符 n-gram 词表没有拟合。",
            saved=False,
            next_step="传入训练分区的岗位快照后再拟合词表。",
        )
    n_docs = len(texts)
    document_frequency: dict[str, int] = {}
    nmin = int(params["ngram_min"])
    nmax = int(params["ngram_max"])
    lowercase = bool(params["lowercase"])
    for text in texts:
        seen = char_ngrams(text, nmin=nmin, nmax=nmax, lowercase=lowercase)
        for gram in seen:
            document_frequency[gram] = document_frequency.get(gram, 0) + 1
    min_df = params["min_df"]
    idf: dict[str, float] = {}
    for gram, frequency in document_frequency.items():
        if not _passes_min_df(frequency, n_docs, min_df):
            continue
        idf[gram] = math.log((1.0 + n_docs) / (1.0 + frequency)) + 1.0
    payload = {
        "implementation": IMPLEMENTATION,
        "sklearn": None,
        "config": params,
        "corpus_ids": list(corpus_ids),
        "n_docs": n_docs,
        "idf": {gram: idf[gram] for gram in sorted(idf)},
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    directory = load_config().data_dir / "models" / "tfidf"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}.json"
    path.write_text(encoded, encoding="utf-8")
    return FittedTfidf(
        idf=dict(sorted(idf.items())),
        config=dict(params),
        n_docs=n_docs,
        vocab_sha256=digest,
        vocab_path=str(path),
    )


def cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    dot = 0.0
    for gram, weight in left.items():
        other = right.get(gram)
        if other is not None:
            dot += weight * other
    if dot < 0.0:
        return 0.0
    if dot > 1.0:
        return 1.0
    return dot


def build_tfidf(corpus_ids: list[str], cfg: TfidfConfig | None = None) -> TfidfIndex:
    from jobbot.matching.store import get_job_snapshot

    if not corpus_ids:
        raise JobBotError(
            "没有传入训练语料，字符 n-gram 词表没有拟合。",
            saved=False,
            next_step="传入训练分区的岗位快照 id。",
        )
    ordered = list(dict.fromkeys(corpus_ids))
    texts = [(get_job_snapshot(snapshot_id).description or "") for snapshot_id in ordered]
    fitted = fit_texts(texts, resolved_tfidf_config(cfg), corpus_ids=ordered)
    remember(fitted)
    return to_index(fitted, ordered)


def remember(fitted: FittedTfidf) -> None:
    global _current
    _current = fitted


def current() -> FittedTfidf | None:
    return _current


_current: FittedTfidf | None = None


def to_index(fitted: FittedTfidf, corpus_ids: list[str]) -> TfidfIndex:
    params = fitted.config
    return TfidfIndex(
        corpus_ids=list(corpus_ids),
        config=TfidfConfig(
            analyzer="char",
            ngram_range=(int(params["ngram_min"]), int(params["ngram_max"])),
            norm="l2",
            sublinear_tf=bool(params["sublinear_tf"]),
            min_df=params["min_df"] if isinstance(params["min_df"], (int, float)) else 1,
        ),
        vocab_path=fitted.vocab_path,
    )


def _passes_min_df(frequency: int, n_docs: int, min_df: object) -> bool:
    if isinstance(min_df, float):
        return frequency / n_docs >= min_df
    threshold = 1 if min_df is None else int(min_df)
    return frequency >= threshold


def _l2_normalize(weights: dict[str, float]) -> dict[str, float]:
    square = sum(value * value for value in weights.values())
    if square <= 0.0:
        return {}
    scale = math.sqrt(square)
    return {gram: value / scale for gram, value in weights.items()}
