"""Диалог по результатам анализа: уточнение, критика и доработка гипотез.

Сервер без состояния: историю диалога присылает клиент с каждым запросом.
Контекст каждого хода = дайджест анализа + профиль фабрики + свежие выдержки
из литературы под конкретный вопрос (RAG). Без LLM — деградация до выдержек.
"""
from __future__ import annotations

CHAT_SYSTEM = """Ты — эксперт-обогатитель Норникеля (флотация сульфидных медно-никелевых руд),
ведёшь диалог с технологом фабрики по результатам анализа потерь с хвостами.

Тебе даны: количественный диагноз хвостов, сгенерированные гипотезы, профиль
оборудования фабрики и выдержки из литературы, подобранные под текущий вопрос.

Правила:
1. Отвечай ТОЛЬКО на основе данных контекста: диагноза (числа), гипотез,
   профиля фабрики и выдержек. Не выдумывай реагенты, режимы и оборудование.
2. Если просят переформулировать/доработать гипотезу — сохраняй проверяемость:
   что изменить, где в схеме, ожидаемый эффект, критерий успеха.
3. Ссылайся на источники по названию и странице, когда опираешься на выдержку.
4. Сохраняй анонимизацию: «Элемент 28» и «Элемент 29», не «никель/медь».
5. Различай механизмы: сростки и крупные раскрытые зёрна → измельчение/классификация;
   тонкие раскрытые (шламы) → флотация/реагенты. Неизвлекаемый металл — физический
   предел, по нему решений не предлагай.
6. Если вопрос выходит за рамки данных (нет числа, нет источника) — честно скажи,
   каких данных не хватает и как их получить. Если выдержки противоречат друг
   другу — укажи противоречие явно и предложи, как его разрешить проверкой.
7. Отвечай по-русски, деловым инженерным языком, компактно (обычно до 250 слов).
   Списки — маркерами, ключевые величины — числами из диагноза."""


def _fmt(x) -> str:
    return f"{x:,.0f}".replace(",", " ") if isinstance(x, (int, float)) else "—"


def build_digest(analysis: dict, max_hyp: int = 14) -> str:
    """Компактный дайджест результата /api/analyze для системного контекста."""
    if not analysis:
        return "(анализ ещё не выполнялся — отвечай по литературе общими рекомендациями)"
    rep = analysis.get("report") or {}
    lines = [
        f"Файл: {rep.get('source_file', '—')}",
        f"Переработка: {_fmt(rep.get('feed_smt'))} СМТ; хвосты: {_fmt(rep.get('tails_smt'))} СМТ.",
        f"Потери с хвостами: Элемент 28 — {_fmt(rep.get('tails_loss_t_28'))} т; "
        f"Элемент 29 — {_fmt(rep.get('tails_loss_t_29'))} т.",
        f"Диагноз: {analysis.get('dominant_strategy', '—')}",
    ]
    goal = (analysis.get("meta") or {}).get("user_goal")
    if goal:
        lines.append(f"Целевая задача технолога: {goal}")
    totals = (analysis.get("diagnosis") or {}).get("totals") or {}
    for el, t in totals.items():
        lines.append(f"{el}: извлекаемые потери {_fmt(t.get('recoverable_t'))} т, "
                     f"неизвлекаемые {_fmt(t.get('nonrecoverable_t'))} т.")
    hyps = analysis.get("hypotheses") or []
    if hyps:
        lines.append(f"\nГипотезы ({len(hyps)}):")
        for h in hyps[:max_hyp]:
            tg = h.get("target") or {}
            ev = h.get("expected_value") or {}
            lines.append(
                f"- {h.get('id')}: {h.get('title')} | поток «{tg.get('stream', '')}», "
                f"классы {', '.join(tg.get('size_classes') or [])} | "
                f"адресуемые потери {_fmt(ev.get('addressable_recoverable_t'))} т. "
                f"Суть: {str(h.get('statement', ''))[:280]}")
    return "\n".join(lines)


def build_system(digest: str, facility: str, evidence_text: str) -> str:
    parts = [CHAT_SYSTEM, "\n=== КОНТЕКСТ АНАЛИЗА ===\n" + digest]
    if facility:
        parts.append("\n=== ПРОФИЛЬ ФАБРИКИ (оборудование и регламент) ===\n" + facility)
    parts.append("\n=== ВЫДЕРЖКИ ИЗ ЛИТЕРАТУРЫ ПОД ТЕКУЩИЙ ВОПРОС ===\n" + evidence_text)
    return "\n".join(parts)


def trim_history(history: list, max_turns: int = 10, max_chars: int = 2000) -> list[dict]:
    """Последние ходы диалога в формате OpenAI, с защитой от мусора на входе."""
    out = []
    for m in (history or [])[-max_turns:]:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = str(m.get("content", "")).strip()
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content[:max_chars]})
    return out
