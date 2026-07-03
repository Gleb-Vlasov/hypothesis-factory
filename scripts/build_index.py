# -*- coding: utf-8 -*-
"""Сборка корпуса знаний из материалов организаторов → data_index/corpus.jsonl."""
import io
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from hypofactory.rag.corpus import build_corpus
from hypofactory.rag.bm25_index import save_corpus

DATA = os.environ.get(
    "DATA_DIR",
    os.path.join(os.path.dirname(__file__), "..", "..", "DATA"),
)
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data_index")
os.makedirs(OUT_DIR, exist_ok=True)
OUT = os.path.join(OUT_DIR, "corpus.jsonl")

records, warnings = build_corpus(DATA)
save_corpus(records, OUT)

by_type = Counter(r["type"] for r in records)
by_src = Counter(r["title"] for r in records)
print(f"Записей всего: {len(records)}  →  {OUT}")
print("По типам:", dict(by_type))
print("По источникам:")
for title, n in by_src.most_common():
    print(f"   {n:5}  {title}")
if warnings:
    print("Предупреждения:")
    for w in warnings:
        print("   -", w)
