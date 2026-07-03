"""Генератор гипотез: диагноз → RAG-контекст → LLM (или шаблон) → структура.

Работает в двух режимах:
  - LLM доступен (Yandex) — формулировки генерирует модель на основе диагноза и
    процитированных выдержек из литературы;
  - LLM недоступен — детерминированный шаблонный фолбэк (диагноз + топ-выдержка).
    Это гарантирует осмысленный результат при любом деплое.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

from hypofactory.diagnosis.engine import (
    diagnose, Diagnosis, GRINDING_MECHS,
    MECH_LIBERATION, MECH_COARSE, MECH_FLOTATION_FINES, MECH_FLOTATION_MID,
)
from hypofactory.ingestion.tailings import TailingsReport, CLASS_ORDER
from hypofactory.rag.bm25_index import tokenize
from hypofactory.rag.retriever import HybridRetriever
from hypofactory.generation.schema import (
    Hypothesis, Target, Source, Novelty, Risks, ExpectedValue, VerificationStep,
)
from hypofactory.generation import prompts

# Поисковый запрос к литературе по механизму
_QUERY = {
    MECH_LIBERATION: "раскрытие закрытых сростков пентландита доизмельчение степень раскрытия минералов",
    MECH_COARSE: "крупные зёрна вне флотируемого окна крупность флотируемых частиц классификация гидроциклоны",
    MECH_FLOTATION_FINES: "потери тонких шламов флотация шламовых классов раздельный контакт реагенты",
    MECH_FLOTATION_MID: "условия флотации время агитации плотность пульпы собиратели сульфидов",
}


@dataclass
class SignalGroup:
    stream_kind: str
    mechanism: str
    category: str
    size_classes: list[str]
    total_t: float
    by_element: dict            # {"Элемент 28": tons, ...}
    max_share_pct: Optional[float]
    value_usd: Optional[float] = None  # денежная оценка адресуемых потерь (если заданы цены)

    @property
    def dominant_element(self) -> str:
        return max(self.by_element, key=self.by_element.get) if self.by_element else "Элемент 28"


@dataclass
class HypothesisSet:
    source_file: str
    dominant_strategy: str
    hypotheses: list[Hypothesis] = field(default_factory=list)
    diagnosis: Optional[dict] = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["hypotheses"] = [h.to_dict() for h in self.hypotheses]
        return d


def _group_signals(diag: Diagnosis) -> list[SignalGroup]:
    groups: dict[tuple, SignalGroup] = {}
    for s in diag.signals:
        key = (s.stream_kind, s.mechanism)
        g = groups.get(key)
        if g is None:
            g = SignalGroup(s.stream_kind, s.mechanism, s.hypothesis_category, [], 0.0, {}, s.share_of_stream_pct)
            groups[key] = g
        g.total_t += s.recoverable_t
        if s.size_class not in g.size_classes:
            g.size_classes.append(s.size_class)
        g.by_element[s.element] = g.by_element.get(s.element, 0.0) + s.recoverable_t
        if s.value_usd is not None:
            g.value_usd = (g.value_usd or 0.0) + s.value_usd
        if s.share_of_stream_pct and (g.max_share_pct is None or s.share_of_stream_pct > g.max_share_pct):
            g.max_share_pct = s.share_of_stream_pct
    order = {c: i for i, c in enumerate(CLASS_ORDER)}
    for g in groups.values():
        g.size_classes.sort(key=lambda c: order.get(c, 99))
    return sorted(groups.values(), key=lambda g: g.total_t, reverse=True)


def _lexical_novelty(text: str, known_texts: list[str]) -> Novelty:
    tt = set(tokenize(text))
    best, closest = 0.0, ""
    for k in known_texts:
        kt = set(tokenize(k))
        if not tt or not kt:
            continue
        j = len(tt & kt) / len(tt | kt)
        if j > best:
            best, closest = j, k
    score = round(1 - best, 2)
    label = "новое" if score > 0.66 else "инкрементальное" if score > 0.33 else "известное"
    return Novelty(score=score, label=label,
                   explanation=f"Максимальное лексическое сходство с известными решениями: {best:.2f}.",
                   closest_known=closest)


def _context_text(diag: Diagnosis, g: SignalGroup) -> str:
    classes = ", ".join(g.size_classes)
    els = "; ".join(f"{el}: {t:.0f} т" for el, t in sorted(g.by_element.items(), key=lambda x: -x[1]))
    return (f"Общая стратегия: {diag.dominant_strategy}\n"
            f"Сигнал потерь: поток «{g.stream_kind}», механизм «{g.category}».\n"
            f"Затронутые классы крупности: {classes}.\n"
            f"Адресуемые извлекаемые потери: {els} (всего {g.total_t:.0f} т).\n"
            f"Тип механизма: {'измельчение/классификация' if g.mechanism in GRINDING_MECHS else 'флотация'}.")


def _evidence_text(passages) -> str:
    out = []
    for i, p in enumerate(passages, 1):
        loc = f", с.{p.page}" if p.page else ""
        out.append(f"[{i}] {p.title}{loc}: {p.text[:320]}")
    return "\n".join(out) if out else "(нет выдержек)"


# ---------------- Шаблонный фолбэк (без LLM) ----------------

_TEMPLATE = {
    MECH_LIBERATION: dict(
        title="Доизмельчение для раскрытия закрытых сростков ({stream})",
        statement=("Дополнительное измельчение материала классов {classes} потока «{stream}» снизит "
                   "долю закрытых сростков Pnt/Cp и повысит извлечение {el} за счёт раскрытия зёрен."),
        mech="Раскрытие сростков увеличивает долю свободной поверхности ценного минерала, доступной собирателю при флотации."),
    MECH_COARSE: dict(
        title="Классификация крупных зёрен в флотируемую крупность ({stream})",
        statement=("Усиление классификации/возврат крупных классов {classes} потока «{stream}» в измельчение "
                   "переведёт раскрытые, но слишком крупные зёрна {el} в флотируемое окно крупности."),
        mech="Слишком крупные частицы плохо закрепляются на пузырьках; снижение их крупности повышает вероятность флотации."),
    MECH_FLOTATION_FINES: dict(
        title="Флотация шламов тонких классов ({stream})",
        statement=("Раздельная обработка шламовой фракции (классы {classes}) потока «{stream}» — реагентный режим и "
                   "увеличенное время контакта — снизит потери раскрытого {el} со шламами."),
        mech="Тонкие раскрытые зёрна теряются из-за плохой минерализации пузырьков; отдельный режим для шламов повышает их извлечение."),
    MECH_FLOTATION_MID: dict(
        title="Оптимизация условий флотации среднего класса ({stream})",
        statement=("Настройка времени агитации, плотности пульпы и реагентов для классов {classes} потока «{stream}» "
                   "повысит извлечение раскрытого {el}."),
        mech="Достаточное время контакта и корректная плотность пульпы улучшают закрепление раскрытых зёрен на пузырьках."),
}

_RISKS = {
    "grinding": Risks(
        technical=["Переизмельчение и рост доли шламов, ухудшающих флотацию",
                   "Рост энергозатрат и износа мелющих тел/футеровки"],
        economic=["CAPEX на дополнительное оборудование классификации/измельчения",
                  "Рост удельных затрат на измельчение"]),
    "flotation": Risks(
        technical=["Снижение селективности, попадание пустой породы в концентрат",
                   "Чувствительность режима к колебаниям питания"],
        economic=["Рост расхода реагентов", "Затраты на дополнительные аппараты/чаны"]),
}


def _template_hypothesis(g: SignalGroup, passages, novelty: Novelty, idx: int) -> Hypothesis:
    t = _TEMPLATE[g.mechanism]
    classes = ", ".join(g.size_classes)
    fmt = dict(stream=g.stream_kind, classes=classes, el=g.dominant_element)
    sources = []
    for p in passages[:2]:
        sources.append(Source(title=p.title, page=p.page, quote=p.text[:160]))
    risks = _RISKS["grinding"] if g.mechanism in GRINDING_MECHS else _RISKS["flotation"]
    return Hypothesis(
        id=f"H{idx}",
        title=t["title"].format(**fmt),
        statement=t["statement"].format(**fmt),
        target=Target(element=g.dominant_element, stream=g.stream_kind,
                      size_classes=g.size_classes, mechanism=g.mechanism, category=g.category),
        rationale=(f"В потоке «{g.stream_kind}» на классы {classes} приходится {g.total_t:.0f} т извлекаемого "
                   f"металла (до {g.max_share_pct}% извлекаемых потерь потока по элементу). Механизм — {g.category}."),
        mechanism_of_influence=t["mech"],
        sources=sources,
        novelty=novelty,
        risks=risks,
        expected_value=ExpectedValue(addressable_recoverable_t=round(g.total_t, 1),
                                     share_of_stream_pct=g.max_share_pct,
                                     kpi_text=f"Потенциальное снижение потерь до {g.total_t:.0f} т извлекаемого металла.",
                                     value_usd=round(g.value_usd) if g.value_usd else None),
        verification_roadmap=[
            VerificationStep(step="Лабораторное/пилотное опробование режима на пробе хвостов",
                             resources="Проба хвостов, лабораторная флотомашина/мельница, аналитика",
                             success_criteria="Снижение содержания извлекаемого металла в хвостах при сохранении качества концентрата"),
            VerificationStep(step="Оценка тех-эконом. эффекта и рисков переизмельчения/селективности",
                             resources="Данные по энергозатратам и реагентам",
                             success_criteria="Положительный NPV пилота"),
        ],
        confidence=0.5,
        generated_by="template",
    )


# ---------------- LLM-путь ----------------

def _feedback_similarity(text: str, items: list[str]) -> tuple[float, str]:
    """Максимальное лексическое сходство (Жаккар по токенам) с формулировками из фидбэка."""
    tt = set(tokenize(text))
    best, closest = 0.0, ""
    for k in items:
        kt = set(tokenize(k))
        if not tt or not kt:
            continue
        j = len(tt & kt) / len(tt | kt)
        if j > best:
            best, closest = j, k
    return best, closest


def apply_feedback(hyps: list[Hypothesis], confirmed: list[str], rejected: list[str],
                   threshold: float = 0.35) -> list[Hypothesis]:
    """Обучение на фидбэке эксперта: понижаем похожее на отклонённое,
    повышаем похожее на подтверждённое; помечаем и пересортировываем."""
    if not confirmed and not rejected:
        return hyps
    for h in hyps:
        # сравниваем заголовок и формулировку по отдельности: короткий вердикт
        # эксперта не должен размываться длинным текстом гипотезы
        def _best(items: list[str]) -> tuple[float, str]:
            cands = [_feedback_similarity(h.title, items), _feedback_similarity(h.statement, items)]
            return max(cands, key=lambda x: x[0])
        sim_r, close_r = _best(rejected)
        sim_c, close_c = _best(confirmed)
        if sim_r >= threshold and sim_r >= sim_c:
            h.confidence = round(max(0.05, h.confidence - 0.2), 2)
            h.feedback_note = f"Похожа на отклонённую экспертом: «{close_r[:120]}» — понижена."
        elif sim_c >= threshold:
            h.confidence = round(min(0.95, h.confidence + 0.1), 2)
            h.feedback_note = f"Похожа на подтверждённую экспертом: «{close_c[:120]}»."
    # отклонённые уходят вниз (стабильная сортировка сохраняет порядок по тоннам)
    hyps.sort(key=lambda h: 1 if (h.feedback_note or "").startswith("Похожа на отклонённую") else 0)
    return hyps


def _load_facility_profile() -> str:
    """Профиль оборудования/регламента фабрики (data_index/facility_profile.md)."""
    import os
    from hypofactory.config import get_settings
    path = os.path.join(get_settings().data_index_dir, "facility_profile.md")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _llm_hypotheses(llm, context: str, evidence, known, g: SignalGroup, start_idx: int,
                    known_texts: list[str], per_signal: int, facility: str = "",
                    goal: str = "", rejected_text: str = "") -> list[Hypothesis]:
    ev_text = _evidence_text(evidence)
    known_text = "\n".join(f"- {k.text}" for k in known) or "(нет)"
    user = prompts.build_user_prompt(context, ev_text, known_text, per_signal, facility, goal,
                                     rejected_text)
    data = llm.chat_json(prompts.SYSTEM, user)
    items = data.get("hypotheses") if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = [items]
    result = []
    for k, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        stmt = str(item.get("statement", "")).strip()
        nov = _lexical_novelty(stmt or str(item.get("title", "")), known_texts)
        srcs = [Source(title=str(s.get("title", "")), page=s.get("page"), quote=str(s.get("quote", "")))
                for s in item.get("sources", []) if isinstance(s, dict)]
        rk = item.get("risks", {}) or {}
        ev = item.get("expected_value", {}) or {}
        vr = [VerificationStep(step=str(v.get("step", "")), resources=str(v.get("resources", "")),
                               success_criteria=str(v.get("success_criteria", "")))
              for v in item.get("verification_roadmap", []) if isinstance(v, dict)]
        result.append(Hypothesis(
            id=f"H{start_idx + k}",
            title=str(item.get("title", "")).strip() or "(без названия)",
            statement=stmt,
            target=Target(element=g.dominant_element, stream=g.stream_kind,
                          size_classes=g.size_classes, mechanism=g.mechanism, category=g.category),
            rationale=str(item.get("rationale", "")).strip(),
            mechanism_of_influence=str(item.get("mechanism_of_influence", "")).strip(),
            sources=srcs,
            novelty=nov,
            risks=Risks(technical=list(rk.get("technical", [])), economic=list(rk.get("economic", []))),
            expected_value=ExpectedValue(addressable_recoverable_t=round(g.total_t, 1),
                                         share_of_stream_pct=g.max_share_pct,
                                         kpi_text=str(ev.get("kpi_text", "")),
                                         value_usd=round(g.value_usd) if g.value_usd else None),
            verification_roadmap=vr,
            confidence=float(item.get("confidence", 0.5) or 0.5),
            generated_by="llm",
        ))
    return result


def refine_hypothesis(llm, retriever: HybridRetriever, hypothesis: dict,
                      instruction: str, context: str = "", goal: str = "") -> Hypothesis:
    """Перерабатывает гипотезу по указанию эксперта, сохраняя адресуемый сигнал.

    Числовая часть (поток, классы, адресуемые тонны) остаётся от исходной гипотезы —
    LLM меняет решение и согласованно обновляет текстовые поля.
    """
    import json as _json

    old_t = hypothesis.get("target") or {}
    old_ev = hypothesis.get("expected_value") or {}
    query = f"{instruction} {hypothesis.get('title', '')}"
    evidence = retriever.search(query, k=4, types=("textbook", "guide", "user"))
    known_texts = [r["text"] for r in retriever.records if r["type"] == "hypothesis"]

    slim = {k: hypothesis.get(k) for k in
            ("title", "statement", "rationale", "mechanism_of_influence", "risks",
             "verification_roadmap")}
    user = prompts.build_refine_prompt(
        _json.dumps(slim, ensure_ascii=False), instruction, context,
        _evidence_text(evidence), _load_facility_profile(), goal)
    data = llm.chat_json(prompts.SYSTEM, user)
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        raise ValueError("Модель вернула некорректную структуру.")

    stmt = str(data.get("statement", "")).strip() or hypothesis.get("statement", "")
    rk = data.get("risks", {}) or {}
    ev = data.get("expected_value", {}) or {}
    return Hypothesis(
        id=hypothesis.get("id", "H?"),
        title=str(data.get("title", "")).strip() or hypothesis.get("title", ""),
        statement=stmt,
        target=Target(element=old_t.get("element", ""), stream=old_t.get("stream", ""),
                      size_classes=list(old_t.get("size_classes") or []),
                      mechanism=old_t.get("mechanism", ""), category=old_t.get("category", "")),
        rationale=str(data.get("rationale", "")).strip(),
        mechanism_of_influence=str(data.get("mechanism_of_influence", "")).strip(),
        sources=[Source(title=str(s.get("title", "")), page=s.get("page"),
                        quote=str(s.get("quote", "")))
                 for s in data.get("sources", []) if isinstance(s, dict)],
        novelty=_lexical_novelty(stmt, known_texts),
        risks=Risks(technical=list(rk.get("technical", [])), economic=list(rk.get("economic", []))),
        expected_value=ExpectedValue(
            addressable_recoverable_t=old_ev.get("addressable_recoverable_t", 0.0),
            share_of_stream_pct=old_ev.get("share_of_stream_pct"),
            kpi_text=str(ev.get("kpi_text", "")) or old_ev.get("kpi_text", ""),
            value_usd=old_ev.get("value_usd")),
        verification_roadmap=[VerificationStep(step=str(v.get("step", "")),
                                               resources=str(v.get("resources", "")),
                                               success_criteria=str(v.get("success_criteria", "")))
                              for v in data.get("verification_roadmap", []) if isinstance(v, dict)],
        confidence=float(data.get("confidence", 0.6) or 0.6),
        generated_by="llm-refined",
    )


def generate_hypotheses(report: TailingsReport, retriever: HybridRetriever,
                        llm=None, max_groups: int = 6, per_signal: int = 2,
                        goal: str = "", feedback: Optional[dict] = None,
                        prices=None) -> HypothesisSet:
    diag = diagnose(report, prices=prices)
    groups = _group_signals(diag)[:max_groups]
    known_records = [r for r in retriever.records if r["type"] == "hypothesis"]
    known_texts = [r["text"] for r in known_records]

    hset = HypothesisSet(source_file=report.source_file,
                         dominant_strategy=diag.dominant_strategy,
                         diagnosis=diag.to_dict())
    use_llm = bool(llm and getattr(llm, "available", False))
    facility = _load_facility_profile() if use_llm else ""
    rejected_text = "\n".join(f"- {r}" for r in (feedback or {}).get("rejected", [])[:10])

    def _one_group(g: SignalGroup) -> list[Hypothesis]:
        query = _QUERY.get(g.mechanism, "флотация обогащение потери металла")
        evidence = retriever.search(query, k=4, types=("textbook", "guide", "user"))
        known = retriever.search(query, k=3, types=("hypothesis",))
        if use_llm:
            try:
                return _llm_hypotheses(llm, _context_text(diag, g), evidence, known, g, 1,
                                       known_texts, per_signal, facility, goal, rejected_text)
            except Exception as e:
                hset.meta.setdefault("llm_errors", []).append(str(e))
                return [_template_hypothesis(g, evidence,
                        _lexical_novelty(g.category + " " + g.stream_kind, known_texts), 1)]
        nov = _lexical_novelty(_TEMPLATE[g.mechanism]["title"].format(
            stream=g.stream_kind, classes=", ".join(g.size_classes), el=g.dominant_element), known_texts)
        return [_template_hypothesis(g, evidence, nov, 1)]

    # LLM-вызовы по сигналам независимы — выполняем параллельно (минуты → ~1 мин);
    # порядок групп сохраняется, id присваиваются после ранжирования
    if use_llm and len(groups) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(groups))) as pool:
            results = list(pool.map(_one_group, groups))
    else:
        results = [_one_group(g) for g in groups]
    for hyps in results:
        hset.hypotheses.extend(hyps)

    # Обучение на фидбэке: вердикты эксперта из прошлых сессий влияют на ранжирование
    fb = feedback or {}
    if fb.get("confirmed") or fb.get("rejected"):
        apply_feedback(hset.hypotheses, fb.get("confirmed", []), fb.get("rejected", []))
        hset.meta["feedback_used"] = {"confirmed": len(fb.get("confirmed", [])),
                                      "rejected": len(fb.get("rejected", []))}

    # Композитное ранжирование по критериям ТЗ: потенциальный эффект (тонны),
    # новизна, реализуемость (confidence уже учитывает риски и вердикты эксперта)
    max_t = max((h.expected_value.addressable_recoverable_t for h in hset.hypotheses), default=0) or 1
    for h in hset.hypotheses:
        effect = h.expected_value.addressable_recoverable_t / max_t
        nov = h.novelty.score if h.novelty else 0.5
        h.priority_score = round(0.55 * effect + 0.2 * nov + 0.25 * h.confidence, 2)
    hset.hypotheses.sort(key=lambda h: h.priority_score, reverse=True)
    # похожие на отклонённые экспертом — всегда в конце, независимо от эффекта
    hset.hypotheses.sort(key=lambda h: 1 if (h.feedback_note or "").startswith("Похожа на отклонённую") else 0)
    for i, h in enumerate(hset.hypotheses, 1):
        h.id = f"H{i}"
    if goal:
        hset.meta["user_goal"] = goal
    hset.meta["mode"] = "llm" if use_llm else "template"
    hset.meta["groups"] = len(groups)
    hset.meta["semantic_rag"] = retriever.semantic_enabled
    return hset
