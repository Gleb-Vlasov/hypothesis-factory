"""Извлечение и чанкинг текста научной литературы (PDF).

Тексты русскоязычные, из PDF приходят построчно и с переносами по слогам
(«ме-\\nжду»). Чистим переносы и лишние переводы строк, затем режем на
перекрывающиеся пассажи фиксированного размера с сохранением номера страницы.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import fitz  # pymupdf


@dataclass
class Chunk:
    source: str        # имя файла-источника
    title: str         # человекочитаемое название
    page: int          # номер страницы (1-based)
    text: str


# Слоговый перенос: строчная буква + дефис + перевод строки + строчная буква.
_HYPHEN = re.compile(r"([а-яёa-z])-\n([а-яёa-z])", re.IGNORECASE)
_MULTISPACE = re.compile(r"[ \t]+")
_MULTINL = re.compile(r"\n{2,}")
_LEADERS = re.compile(r"(?:[.…_]\s*){4,}")   # точки-лидеры оглавлений (в т.ч. «. . . .»)
_LETTERS = re.compile(r"[а-яёa-z]", re.IGNORECASE)


def clean_text(t: str) -> str:
    t = t.replace("\xad", "")            # мягкий перенос
    t = _HYPHEN.sub(r"\1\2", t)          # склеиваем перенесённые слова
    t = t.replace("\n", " ")             # переводы строк → пробел
    t = _LEADERS.sub(" ", t)             # убираем «................»
    t = _MULTISPACE.sub(" ", t)
    return t.strip()


def is_quality(text: str, min_letter_ratio: float = 0.6, min_words: int = 12) -> bool:
    """Отсеивает оглавления, таблицы номеров и прочий малосодержательный текст."""
    if len(text) < 80:
        return False
    letters = len(_LETTERS.findall(text))
    if letters / max(len(text), 1) < min_letter_ratio:
        return False
    if len(text.split()) < min_words:
        return False
    return True


def extract_pages(path: str) -> list[tuple[int, str]]:
    doc = fitz.open(path)
    out = []
    for i in range(len(doc)):
        raw = doc[i].get_text("text")
        cleaned = clean_text(raw)
        if len(cleaned) >= 40:           # пропускаем пустые/обложки
            out.append((i + 1, cleaned))
    doc.close()
    return out


def _split_sentences(text: str) -> list[str]:
    # грубое деление по концам предложений, достаточно для чанкинга
    parts = re.split(r"(?<=[.!?;])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def chunk_page(text: str, size: int = 750, overlap: int = 150) -> list[str]:
    """Режет текст страницы на пассажи ~size символов по границам предложений."""
    sents = _split_sentences(text)
    chunks: list[str] = []
    buf = ""
    for s in sents:
        if len(buf) + len(s) + 1 <= size:
            buf = f"{buf} {s}".strip()
        else:
            if buf:
                chunks.append(buf)
            if len(s) > size:
                # очень длинное предложение — рубим по символам
                for k in range(0, len(s), size - overlap):
                    chunks.append(s[k:k + size])
                buf = ""
            else:
                # хвост-перекрытие для контекста
                tail = buf[-overlap:] if buf else ""
                buf = f"{tail} {s}".strip()
    if buf:
        chunks.append(buf)
    return chunks


def chunks_from_pdf(path: str, source: str, title: str,
                    size: int = 750, overlap: int = 150) -> list[Chunk]:
    result: list[Chunk] = []
    for page_no, text in extract_pages(path):
        for c in chunk_page(text, size, overlap):
            if is_quality(c):
                result.append(Chunk(source=source, title=title, page=page_no, text=c))
    return result
