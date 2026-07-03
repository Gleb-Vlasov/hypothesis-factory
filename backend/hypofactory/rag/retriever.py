"""Гибридный ретривер: BM25 (лексика) + опциональный семантический слой.

Объединение рангов методом Reciprocal Rank Fusion (RRF): устойчиво к разным
шкалам скорингов. Если векторный индекс/эмбеддер недоступны — работает BM25-only
(graceful degradation), что гарантирует работоспособность при любом деплое.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from hypofactory.rag.bm25_index import BM25Index, Hit, load_corpus
from hypofactory.rag.embed import Embedder, VectorIndex, load_vector_index


@dataclass
class RetrievedPassage:
    id: str
    source: str
    title: str
    type: str
    page: Optional[int]
    text: str
    score: float


class HybridRetriever:
    def __init__(self, records: list[dict],
                 vector_index: Optional[VectorIndex] = None,
                 embedder: Optional[Embedder] = None,
                 rrf_k: int = 60):
        self.records = records
        self.by_id = {r["id"]: r for r in records}
        self.bm25 = BM25Index(records)
        self.vector_index = vector_index
        self.embedder = embedder
        self.rrf_k = rrf_k

    @property
    def semantic_enabled(self) -> bool:
        return self.vector_index is not None and self.embedder is not None

    def add_records(self, new_records: list[dict]) -> int:
        """Добавляет записи (например, пользовательскую литературу) и пересобирает BM25.

        Дубликаты по id пропускаются. Векторный индекс не трогаем — новые записи
        участвуют в лексическом слое (RRF корректно смешает ранги).
        Возвращает число реально добавленных записей.
        """
        fresh = [r for r in new_records if r["id"] not in self.by_id]
        if not fresh:
            return 0
        self.records.extend(fresh)
        self.by_id.update({r["id"]: r for r in fresh})
        self.bm25 = BM25Index(self.records)
        return len(fresh)

    def search(self, query: str, k: int = 8,
               types: tuple[str, ...] | None = None,
               candidates: int = 40) -> list[RetrievedPassage]:
        # BM25-ранги
        bm_hits = self.bm25.search(query, k=candidates, types=None)
        ranks: dict[str, float] = {}
        for rank, h in enumerate(bm_hits):
            ranks[h.record["id"]] = ranks.get(h.record["id"], 0.0) + 1.0 / (self.rrf_k + rank)

        # Семантические ранги (если доступны)
        if self.semantic_enabled:
            try:
                qv = self.embedder.encode([query], is_query=True)[0]
                for rank, (rid, _s) in enumerate(self.vector_index.search(qv, k=candidates)):
                    ranks[rid] = ranks.get(rid, 0.0) + 1.0 / (self.rrf_k + rank)
            except Exception:
                pass  # деградация до BM25

        fused = sorted(ranks.items(), key=lambda kv: kv[1], reverse=True)
        out: list[RetrievedPassage] = []
        for rid, score in fused:
            r = self.by_id.get(rid)
            if r is None:
                continue
            if types and r["type"] not in types:
                continue
            out.append(RetrievedPassage(
                id=r["id"], source=r["source"], title=r["title"], type=r["type"],
                page=r.get("page"), text=r["text"], score=round(score, 5)))
            if len(out) >= k:
                break
        return out


def build_default_retriever(data_index_dir: str,
                            embedder: Optional[Embedder] = None) -> HybridRetriever:
    """Собирает ретривер из data_index/corpus.jsonl (+ векторный индекс при наличии)."""
    import os
    records = load_corpus(os.path.join(data_index_dir, "corpus.jsonl"))
    vindex = load_vector_index(data_index_dir) if embedder is not None else None
    return HybridRetriever(records, vector_index=vindex, embedder=embedder)
