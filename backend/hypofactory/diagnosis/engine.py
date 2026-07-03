"""Детерминированный диагностический движок.

Превращает разобранный баланс хвостов (`TailingsReport`) в ранжированный список
«сигналов проблем». Логика полностью прозрачна и без LLM — это опора для
интерпретируемости и доверия эксперта.

Механизмы потерь (по извлекаемому металлу — только он представляет возможность,
неизвлекаемый = физический предел текущей технологии):
  - LIBERATION  — металл в «Закрытом Pnt/Cp» (сростки), преимущественно в крупных
                  классах → недостаточное раскрытие → измельчение/классификация;
  - FLOTATION_FINES — раскрытый металл в тонких классах (-20+10, -10) → потери со
                  шламами → реагентный режим, флотация шламов, время агитации;
  - FLOTATION_COARSE — раскрытый металл в крупных/средних классах → недостаток
                  времени/условий флотации → фронт флотации, реагенты, плотность.

Размер возможности выражается в тоннах извлекаемого металла (и опц. в деньгах).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

from hypofactory.ingestion.tailings import TailingsReport, TailingsStream, SizeClass

COARSE = {"+125", "-125+71", "-71+45"}
MID = {"-45+20"}
FINE = {"-20+10", "-10"}

MECH_LIBERATION = "liberation"           # закрытые сростки → раскрытие
MECH_COARSE = "coarse_liberation"        # раскрытый металл в крупном классе → вне окна флотации
MECH_FLOTATION_FINES = "flotation_fines" # раскрытый металл в тонком классе → шламы
MECH_FLOTATION_MID = "flotation_mid"     # раскрытый металл в среднем классе → условия флотации

HYPO_CATEGORY = {
    MECH_LIBERATION: "Измельчение и классификация: раскрытие сростков",
    MECH_COARSE: "Измельчение и классификация: крупные частицы вне окна флотации",
    MECH_FLOTATION_FINES: "Флотация шламов и реагентный режим",
    MECH_FLOTATION_MID: "Условия флотации: время агитации, реагенты, плотность",
}

# Механизмы, лечащиеся измельчением/классификацией, и — флотацией.
GRINDING_MECHS = {MECH_LIBERATION, MECH_COARSE}
FLOTATION_MECHS = {MECH_FLOTATION_FINES, MECH_FLOTATION_MID}

ELEMENTS = {28: "Элемент 28", 29: "Элемент 29"}


@dataclass
class MetalPrices:
    """Опциональные цены для денежной оценки. По умолчанию отключено (анонимизация)."""
    usd_per_t_28: Optional[float] = None
    usd_per_t_29: Optional[float] = None

    def price(self, el: int) -> Optional[float]:
        return self.usd_per_t_28 if el == 28 else self.usd_per_t_29


@dataclass
class ProblemSignal:
    stream_kind: str
    size_class: str
    element: str            # анонимизированная метка «Элемент 28/29»
    element_num: int
    mechanism: str
    hypothesis_category: str
    recoverable_t: float    # тонны извлекаемого металла в этом сигнале
    share_of_stream_pct: Optional[float]  # доля от извлекаемых потерь потока по элементу
    value_usd: Optional[float]
    rationale: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Diagnosis:
    source_file: str
    signals: list[ProblemSignal] = field(default_factory=list)
    totals: dict = field(default_factory=dict)     # per-element recoverable/nonrecoverable
    dominant_strategy: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["signals"] = [s.to_dict() for s in self.signals]
        return d


def _loss_t(mineral, el: int) -> float:
    v = mineral.loss_t_28 if el == 28 else mineral.loss_t_29
    return v or 0.0

def _is_recoverable(mineral, el: int) -> bool:
    return mineral.recoverable_ni if el == 28 else mineral.recoverable_cu


def _split_locked_open(sc: SizeClass, el: int) -> tuple[float, float]:
    """Возвращает (закрытый_извлекаемый_т, раскрытый_извлекаемый_т) для элемента."""
    locked = sum(_loss_t(m, el) for m in sc.minerals
                 if m.category == "locked" and _is_recoverable(m, el))
    opened = sum(_loss_t(m, el) for m in sc.minerals
                 if m.category == "open" and _is_recoverable(m, el))
    return locked, opened


def _class_bucket(name: str) -> str:
    if name in COARSE:
        return "coarse"
    if name in FINE:
        return "fine"
    return "mid"


def _choose_streams(report: TailingsReport) -> list[TailingsStream]:
    """Предпочитаем пораздельные потоки (породные/пирротиновые) как более действенные;
    суммарный «отвальные (общие)» используем только если он единственный."""
    specific = [s for s in report.streams if s.kind in ("породные", "пирротиновые")]
    return specific if specific else report.streams


def diagnose(report: TailingsReport, prices: Optional[MetalPrices] = None,
             min_signal_t: float = 1.0) -> Diagnosis:
    prices = prices or MetalPrices()
    diag = Diagnosis(source_file=report.source_file)
    streams = _choose_streams(report)

    totals = {28: {"recoverable_t": 0.0, "nonrecoverable_t": 0.0},
              29: {"recoverable_t": 0.0, "nonrecoverable_t": 0.0}}
    mech_totals = {MECH_LIBERATION: 0.0, MECH_COARSE: 0.0,
                   MECH_FLOTATION_FINES: 0.0, MECH_FLOTATION_MID: 0.0}

    for st in streams:
        # Извлекаемые потери потока по элементу (для долей)
        stream_rec = {28: 0.0, 29: 0.0}
        for sc in st.classes:
            for el in (28, 29):
                locked, opened = _split_locked_open(sc, el)
                stream_rec[el] += locked + opened

        for sc in st.classes:
            bucket = _class_bucket(sc.name)
            for el in (28, 29):
                locked, opened = _split_locked_open(sc, el)
                # аккумулируем тотал извлекаемого/неизвлекаемого
                rec_class = sc.recoverable_t_28 if el == 28 else sc.recoverable_t_29
                nonrec_class = sc.nonrecoverable_t_28 if el == 28 else sc.nonrecoverable_t_29
                totals[el]["recoverable_t"] += (rec_class if rec_class is not None else locked + opened)
                totals[el]["nonrecoverable_t"] += (nonrec_class or 0.0)

                # --- сигнал по закрытому (раскрытие/измельчение) ---
                if locked >= min_signal_t:
                    diag.signals.append(_make_signal(
                        st, sc, el, MECH_LIBERATION, locked, stream_rec[el], prices,
                        rationale=(f"В классе {sc.name} потока «{st.kind}» {locked:.0f} т извлекаемого "
                                   f"{ELEMENTS[el]} заперто в закрытых сростках Pnt/Cp — металл раскрыт "
                                   f"недостаточно. Класс {'крупный' if bucket=='coarse' else 'средний' if bucket=='mid' else 'тонкий'}, "
                                   f"типовое решение — доизмельчение/улучшение классификации.")))
                    mech_totals[MECH_LIBERATION] += locked

                # --- сигнал по раскрытому металлу; механизм зависит от крупности ---
                if opened >= min_signal_t:
                    if bucket == "coarse":
                        mech = MECH_COARSE
                        where = ("крупный класс — зёрна раскрыты, но слишком крупны для эффективной "
                                 "флотации (вне флотируемого окна крупности)")
                        solution = "доизмельчение/классификация для перевода в флотируемую крупность"
                    elif bucket == "fine":
                        mech = MECH_FLOTATION_FINES
                        where = "тонкий класс — металл вскрыт, но теряется со шламами"
                        solution = "флотация шламов, реагентный режим, время агитации"
                    else:
                        mech = MECH_FLOTATION_MID
                        where = "средний класс — металл вскрыт, но не сфлотирован"
                        solution = "условия флотации: время агитации, реагенты, плотность пульпы"
                    diag.signals.append(_make_signal(
                        st, sc, el, mech, opened, stream_rec[el], prices,
                        rationale=(f"В классе {sc.name} потока «{st.kind}» {opened:.0f} т извлекаемого "
                                   f"{ELEMENTS[el]} в раскрытом Pnt/Cp: {where}. Решение — {solution}.")))
                    mech_totals[mech] += opened

    diag.totals = {ELEMENTS[el]: totals[el] for el in (28, 29)}
    diag.signals.sort(key=lambda s: s.recoverable_t, reverse=True)
    diag.dominant_strategy = _dominant(mech_totals)
    diag.notes = list(report.warnings)
    return diag


def _make_signal(st, sc, el, mech, tons, stream_rec_el, prices, rationale) -> ProblemSignal:
    share = (100.0 * tons / stream_rec_el) if stream_rec_el else None
    price = prices.price(el)
    value = tons * price if price else None
    return ProblemSignal(
        stream_kind=st.kind, size_class=sc.name, element=ELEMENTS[el], element_num=el,
        mechanism=mech, hypothesis_category=HYPO_CATEGORY[mech],
        recoverable_t=round(tons, 1), share_of_stream_pct=(round(share, 1) if share is not None else None),
        value_usd=(round(value, 0) if value is not None else None), rationale=rationale,
    )


def _dominant(mech_totals: dict) -> str:
    grind = sum(mech_totals[m] for m in GRINDING_MECHS)
    flo = sum(mech_totals[m] for m in FLOTATION_MECHS)
    total = grind + flo
    if total <= 0:
        return "Недостаточно данных для определения доминирующей стратегии."
    if grind >= flo:
        return (f"Доминирует проблема крупности/раскрытия: {grind:.0f} т извлекаемого металла теряется "
                f"из-за сростков и крупных зёрен вне окна флотации ({100*grind/total:.0f}% адресуемых "
                f"потерь) → приоритет на измельчение и классификацию.")
    return (f"Доминирует проблема флотации: {flo:.0f} т раскрытого извлекаемого металла (преим. шламы) "
            f"теряется ({100*flo/total:.0f}% адресуемых потерь) → приоритет на реагентику и флотацию шламов.")


if __name__ == "__main__":
    import sys
    from hypofactory.ingestion.tailings import parse_tailings_report
    rep = parse_tailings_report(sys.argv[1])
    d = diagnose(rep)
    print(d.dominant_strategy)
    for s in d.signals[:10]:
        print(f"  [{s.recoverable_t:>7.0f} т] {s.element} {s.stream_kind:<12} {s.size_class:>8} "
              f"{s.mechanism:<18} доля={s.share_of_stream_pct}%")
