"""岗位匹配：B0–B3、L0 与解释。"""

from jobbot.matching.embed import embed_texts, use_injected_embedder
from jobbot.matching.explain import explain
from jobbot.matching.features import compute_features
from jobbot.matching.rank import l0, rank
from jobbot.matching.textutil import ranking_text
from jobbot.matching.tfidf import build_tfidf

__all__ = [
    "build_tfidf",
    "compute_features",
    "embed_texts",
    "explain",
    "l0",
    "rank",
    "ranking_text",
    "use_injected_embedder",
]
