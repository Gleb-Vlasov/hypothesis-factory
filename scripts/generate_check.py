# -*- coding: utf-8 -*-
"""End-to-end проверка генерации гипотез (мок-режим без LLM)."""
import glob
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from hypofactory.ingestion.tailings import parse_tailings_report
from hypofactory.rag.retriever import build_default_retriever
from hypofactory.generation.generator import generate_hypotheses

DATA = os.environ.get(
    "DATA_DIR",
    os.path.join(os.path.dirname(__file__), "..", "..", "DATA"),
)
INDEX = os.path.join(os.path.dirname(__file__), "..", "data_index")
target = sys.argv[1] if len(sys.argv) > 1 else "КГМК"
f = glob.glob(os.path.join(DATA, "Пример *", f"Хвосты*{target}*.xlsx"))[0]

retr = build_default_retriever(INDEX)               # BM25-only (без ключа)
report = parse_tailings_report(f)
hset = generate_hypotheses(report, retr, llm=None)

print("ФАЙЛ:", os.path.basename(f))
print("РЕЖИМ:", hset.meta, "| семантика:", hset.meta.get("semantic_rag"))
print("СТРАТЕГИЯ:", hset.dominant_strategy)
print(f"ГИПОТЕЗ: {len(hset.hypotheses)}\n")
for h in hset.hypotheses:
    print("─" * 70)
    print(f"[{h.id}] {h.title}   (новизна: {h.novelty.label} {h.novelty.score}, {h.generated_by})")
    print(f"  Гипотеза: {h.statement}")
    print(f"  Ценность: {h.expected_value.addressable_recoverable_t} т | {h.expected_value.kpi_text}")
    if h.sources:
        s = h.sources[0]
        print(f"  Источник: {s.title}" + (f", с.{s.page}" if s.page else ""))
    print(f"  Риски(тех): {h.risks.technical[0] if h.risks.technical else '-'}")
