# -*- coding: utf-8 -*-
"""Прогон диагностического движка на примерах организаторов."""
import glob
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from hypofactory.ingestion.tailings import parse_tailings_report
from hypofactory.diagnosis.engine import diagnose

DATA = os.environ.get(
    "DATA_DIR",
    os.path.join(os.path.dirname(__file__), "..", "..", "DATA"),
)
files = sorted(glob.glob(os.path.join(DATA, "Пример *", "Хвосты*.xlsx")))

for f in files:
    print("=" * 74)
    print(os.path.basename(f))
    rep = parse_tailings_report(f)
    diag = diagnose(rep)
    print("СТРАТЕГИЯ:", diag.dominant_strategy)
    for el, t in diag.totals.items():
        rec, non = t["recoverable_t"], t["nonrecoverable_t"]
        tot = rec + non
        pct = (100 * rec / tot) if tot else 0
        print(f"  {el}: извлекаемо {rec:.0f} т / неизвлекаемо {non:.0f} т  "
              f"(адресуемо {pct:.0f}% потерь)")
    print("  ТОП-8 сигналов (по тоннам извлекаемого металла):")
    for s in diag.signals[:8]:
        print(f"    [{s.recoverable_t:>7.0f} т] {s.element} | {s.stream_kind:<12} | {s.size_class:>8} "
              f"| {s.mechanism:<17} | доля потока {s.share_of_stream_pct}%")
    print()
