"""Схема гипотезы со всеми полями из ТЗ (обоснование, источники, механизм,
новизна, риски, ценность/KPI, дорожная карта проверки)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Source:
    title: str
    page: Optional[int] = None
    quote: str = ""


@dataclass
class Novelty:
    score: float                # 0..1, выше = новее относительно известных решений
    label: str                  # «известное» | «инкрементальное» | «новое»
    explanation: str = ""
    closest_known: str = ""     # ближайшая известная гипотеза


@dataclass
class Risks:
    technical: list[str] = field(default_factory=list)
    economic: list[str] = field(default_factory=list)


@dataclass
class VerificationStep:
    step: str
    resources: str = ""
    success_criteria: str = ""


@dataclass
class ExpectedValue:
    addressable_recoverable_t: float = 0.0   # адресуемые извлекаемые потери, т
    share_of_stream_pct: Optional[float] = None
    kpi_text: str = ""                        # влияние на KPI словами
    value_usd: Optional[float] = None         # стоимость адресуемых потерь по референсным ценам


@dataclass
class Target:
    element: str = ""            # «Элемент 28/29»
    stream: str = ""             # породные | пирротиновые | ...
    size_classes: list[str] = field(default_factory=list)
    mechanism: str = ""          # liberation | coarse_liberation | flotation_fines | flotation_mid
    category: str = ""           # человекочитаемая категория гипотез


@dataclass
class Hypothesis:
    id: str
    title: str
    statement: str
    target: Target
    rationale: str
    mechanism_of_influence: str
    sources: list[Source] = field(default_factory=list)
    novelty: Optional[Novelty] = None
    risks: Risks = field(default_factory=Risks)
    expected_value: ExpectedValue = field(default_factory=ExpectedValue)
    verification_roadmap: list[VerificationStep] = field(default_factory=list)
    confidence: float = 0.5
    generated_by: str = "template"   # llm | template | llm-refined
    feedback_note: Optional[str] = None  # пометка цикла обратной связи (похожа на подтверждённую/отклонённую)
    priority_score: Optional[float] = None  # композитный ранг: эффект + новизна + реализуемость

    def to_dict(self) -> dict:
        return asdict(self)
