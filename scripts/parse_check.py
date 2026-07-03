# -*- coding: utf-8 -*-
"""Прогон парсера хвостов на всех примерах организаторов + сводка."""
import glob
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from hypofactory.ingestion.tailings import parse_tailings_report


def _f(x):
    return "None" if x is None else f"{x:.1f}"


DATA = os.environ.get(
    "DATA_DIR",
    os.path.join(os.path.dirname(__file__), "..", "..", "DATA"),
)

files = sorted(glob.glob(os.path.join(DATA, "Пример *", "Хвосты*.xlsx")))
print(f"Найдено файлов: {len(files)}\n")

for f in files:
    print("=" * 70)
    print(os.path.basename(f))
    rep = parse_tailings_report(f)
    print(f"  Питание: {rep.feed_smt} СМТ | Ni {rep.feed_grade_28}% | Cu {rep.feed_grade_29}%")
    print(f"  Хвосты:  {rep.tails_smt} СМТ | Ni {rep.tails_grade_28}% ({rep.tails_loss_t_28} т) "
          f"| Cu {rep.tails_grade_29}% ({rep.tails_loss_t_29} т)")
    for st in rep.streams:
        print(f"  Поток [{st.kind}] СМТ={st.smt} потери Ni={st.loss_t_28} Cu={st.loss_t_29} "
              f"классов={len(st.classes)}")
        for c in st.classes:
            nmin = len(c.minerals)
            locked = sum((m.loss_t_28 or 0) for m in c.minerals if m.category == "locked")
            opened = sum((m.loss_t_28 or 0) for m in c.minerals if m.category == "open")
            print(f"     {c.name:>8} доля={_f(c.share_pct)}% Ni_т={_f(c.loss_t_28)} "
                  f"(извлек={_f(c.recoverable_t_28)}/неизвл={_f(c.nonrecoverable_t_28)}) "
                  f"| закрыт_Ni={_f(locked)} раскрыт_Ni={_f(opened)} мин={nmin}")
    if rep.warnings:
        print("  WARN:", "; ".join(rep.warnings))
    print()
