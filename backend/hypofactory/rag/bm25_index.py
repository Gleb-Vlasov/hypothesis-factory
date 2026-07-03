"""Лексический BM25-индекс по корпусу знаний.

Не требует моделей и внешних сервисов — работает всегда, что важно для надёжного
деплоя. Служит основой ретривера; семантические эмбеддинги подключаются сверху
(см. rag/embed.py) и при их отсутствии система деградирует до BM25.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

_WORD = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
_CJK = re.compile(r"[一-鿿]+")  # китайская литература индексируется биграммами


def tokenize(text: str) -> list[str]:
    low = text.lower()
    out = [t for t in _WORD.findall(low) if len(t) > 2]
    for run in _CJK.findall(low):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return out


@dataclass
class Hit:
    record: dict
    score: float


class BM25Index:
    def __init__(self, records: list[dict]):
        self.records = records
        self._tokens = [tokenize(r["text"]) for r in records]
        self.bm25 = BM25Okapi(self._tokens)

    def search(self, query: str, k: int = 8, types: tuple[str, ...] | None = None) -> list[Hit]:
        scores = self.bm25.get_scores(tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        hits: list[Hit] = []
        for i in order:
            if types and self.records[i]["type"] not in types:
                continue
            if scores[i] <= 0:
                break
            hits.append(Hit(record=self.records[i], score=float(scores[i])))
            if len(hits) >= k:
                break
        return hits


def load_corpus(jsonl_path: str) -> list[dict]:
    with open(jsonl_path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def save_corpus(records: list[dict], jsonl_path: str) -> None:
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
