"""Расшифровка изображений (схемы флотации, регламенты, списки оборудования).

Изображение прогоняется через vision-модель и превращается в структурированный
текст: тип документа, оборудование, цепочка операций, режимы, выводы для анализа
потерь. Дальше этот текст живёт как обычный документ базы знаний (BM25-поиск,
цитирование) либо как контекст конкретного анализа.

Точные цифры балансов даёт Excel — от схемы нужна КАЧЕСТВЕННАЯ картина
(стадиальность, аппараты, куда уходят пески/шламы), поэтому промпт запрещает
переписывать все числа из таблиц: на мелких таблицах vision-модели галлюцинируют.
"""
from __future__ import annotations

import base64
import io
import os

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
MAX_IMAGE_MB = 15
MAX_IMAGES_PER_ANALYZE = 5

VISION_PROMPT = """Ты — инженер-технолог обогатительной фабрики. На изображении технический документ: схема цепочки аппаратов / схема флотации / технологический регламент / список оборудования.

Составь его текстовое описание на русском для инженерной базы знаний. Строго по разделам:

ТИП: одна строка — что это за документ.
ОБОРУДОВАНИЕ: перечисли аппараты с марками и количеством (только то, что читается уверенно).
ЦЕПОЧКА: последовательность операций и потоки между ними (откуда → куда), включая циркулирующие нагрузки; укажи крупности переделов, если читаются.
РЕЖИМЫ: реагенты и расходы, времена флотации, температуры, плотности — только уверенно читаемые.
ВЫВОДЫ ДЛЯ АНАЛИЗА ПОТЕРЬ: 3-5 пунктов — стадиальность дробления/измельчения, есть ли гравитация, доизмельчение, перечистки, классификация, куда уходят пески и шламы, где образуются отвальные хвосты.

Правила: НЕ переписывай все числа из таблиц — только ключевые параметры. Если элемент неразборчив, пропусти его. Ничего не выдумывай. Пиши компактно."""


def image_to_jpeg_b64(path: str, max_side: int = 1568, quality: int = 88) -> str:
    """PNG/JPG/WebP/BMP → компактный JPEG base64 для vision-модели."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def transcribe_image(llm, path: str, original_name: str) -> str:
    """Изображение → структурированный текст. Требует LLM-режима."""
    if not llm.available:
        raise RuntimeError("Расшифровка изображений требует LLM-режима "
                           "(задайте ключ Yandex — сейчас работает базовый режим).")
    text = llm.describe_image(image_to_jpeg_b64(path), VISION_PROMPT)
    if len(text) < 60:
        raise ValueError(f"Не удалось расшифровать изображение «{original_name}» "
                         "(модель не распознала содержимое).")
    return text


def image_records(text: str, original_name: str, meta: dict | None = None) -> list[dict]:
    """Расшифровка → записи корпуса type='user' (как у литературы).

    Разделы расшифровки короткие и структурные — режем крупными кусками без
    фильтра is_quality (он рассчитан на книжную прозу и отсеял бы списки).
    """
    from datetime import date
    from hypofactory.ingestion.pdf import chunk_page

    title = os.path.splitext(os.path.basename(original_name))[0]
    meta = {k: str(v).strip() for k, v in (meta or {}).items() if str(v or "").strip()}
    byline = ", ".join(filter(None, [meta.get("author"), meta.get("year")]))
    if byline:
        title = f"{title} ({byline})"
    extra = {**meta, "added_at": date.today().isoformat(), "kind": "image"}

    chunks = chunk_page(" ".join(text.split()), size=900, overlap=120) or [text]
    return [{
        "id": f"user::{original_name}#img#c{i}",
        "source": original_name, "title": f"{title} (схема, расшифровано)",
        "type": "user", "page": None, "text": c, **extra,
    } for i, c in enumerate(chunks)]
