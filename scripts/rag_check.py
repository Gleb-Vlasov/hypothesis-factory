# -*- coding: utf-8 -*-
"""Проверка качества BM25-ретривера на доменных запросах."""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from hypofactory.rag.bm25_index import BM25Index, load_corpus

CORPUS = os.path.join(os.path.dirname(__file__), "..", "data_index", "corpus.jsonl")
idx = BM25Index(load_corpus(CORPUS))

queries = [
    "доизмельчение закрытых сростков пентландита раскрытие минералов",
    "потери тонких шламов при флотации, флотация шламовых классов",
    "замена спиральных классификаторов на гидроциклоны",
    "гуммирование футеровки шаровой мельницы геометрия",
    "реагенты собиратели для флотации сульфидов меди и никеля",
]
for q in queries:
    print("=" * 74)
    print("Q:", q)
    for h in idx.search(q, k=3, types=("textbook", "guide")):
        r = h.record
        loc = f"с.{r['page']}" if r.get("page") else r["type"]
        print(f"  [{h.score:5.1f}] {r['title']} ({loc})")
        print(f"        {r['text'][:200]}...")
    print()
