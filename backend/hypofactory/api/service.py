"""Сервис-слой: единожды загружает RAG-ретривер и LLM, выполняет анализ.

Индекс и модели грузятся один раз (на старте приложения). Всё спроектировано так,
чтобы работать без ключа Yandex (шаблонный режим) — надёжность деплоя приоритетна.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Optional

from hypofactory.config import Settings, get_settings
from hypofactory.ingestion.tailings import parse_tailings_report
from hypofactory.rag.retriever import build_default_retriever
from hypofactory.generation.generator import generate_hypotheses
from hypofactory.llm.client import LLMClient


class Pipeline:
    def __init__(self, settings: Optional[Settings] = None):
        self.s = settings or get_settings()
        self.llm = LLMClient(self.s)
        self.retriever = build_default_retriever(self.s.data_index_dir, embedder=self._embedder())
        self._load_user_corpus()

    # --- Пользовательская база знаний ---

    @property
    def _user_corpus_path(self) -> str:
        return os.path.join(self.s.data_index_dir, "user_corpus.jsonl")

    def _load_user_corpus(self) -> None:
        """Подхватывает ранее загруженную литературу (переживает перезапуск)."""
        try:
            with open(self._user_corpus_path, encoding="utf-8") as f:
                records = [json.loads(line) for line in f if line.strip()]
            if records:
                self.retriever.add_records(records)
        except OSError:
            pass  # файла ещё нет — нормально

    def add_knowledge(self, path: str, original_name: str, meta: Optional[dict] = None) -> dict:
        """Парсит документ (или расшифровывает изображение), добавляет в индекс."""
        from hypofactory.ingestion.images import IMAGE_EXT, image_records, transcribe_image
        from hypofactory.ingestion.literature import parse_document
        preview = ""
        if original_name.lower().endswith(IMAGE_EXT):
            text = transcribe_image(self.llm, path, original_name)
            records = image_records(text, original_name, meta)
            preview = text[:500]
        else:
            records = parse_document(path, original_name, meta)
        if not records:
            raise ValueError("Не удалось извлечь текст (пустой документ или скан без текстового слоя).")
        added = self.retriever.add_records(records)
        if added:
            with open(self._user_corpus_path, "a", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        out = {"source": original_name, "fragments": len(records), "added": added,
               "corpus_size": len(self.retriever.records)}
        if preview:
            out["transcript_preview"] = preview
        return out

    def knowledge_status(self) -> dict:
        """Состав базы знаний: вшитая часть + пользовательские документы."""
        builtin = 0
        user_docs: dict[str, dict] = {}
        for r in self.retriever.records:
            if r.get("type") == "user":
                d = user_docs.setdefault(r["source"], {
                    "source": r["source"], "title": r.get("title", r["source"]),
                    "author": r.get("author", ""), "year": r.get("year", ""),
                    "note": r.get("note", ""), "added_at": r.get("added_at", ""),
                    "fragments": 0})
                d["fragments"] += 1
            else:
                builtin += 1
        return {"builtin_fragments": builtin,
                "user_documents": sorted(user_docs.values(), key=lambda d: d["source"]),
                "corpus_size": len(self.retriever.records)}

    def _embedder(self):
        if not self.s.embeddings_enabled:
            return None
        try:
            if self.s.embeddings_backend == "local":
                from hypofactory.rag.embed import LocalEmbedder
                return LocalEmbedder(self.s.local_embedding_model)
            from hypofactory.rag.embed import YandexEmbedder
            if not self.s.llm_ready:
                return None
            return YandexEmbedder(self.s.yandex_api_key, self.s.yandex_folder_id, self.s.llm_base_url)
        except Exception:
            return None

    def status(self) -> dict:
        return {
            "corpus_size": len(self.retriever.records),
            "semantic_rag": self.retriever.semantic_enabled,
            "llm_ready": self.llm.available,
            "llm_model": self.s.llm_model_id if self.llm.available else None,
            "mode": "llm" if self.llm.available else "template",
        }

    def _load_feedback(self) -> dict:
        """Вердикты эксперта из прошлых сессий: {'confirmed': [...], 'rejected': [...]}."""
        out = {"confirmed": [], "rejected": []}
        try:
            with open(os.path.join(self.s.data_index_dir, "feedback.jsonl"), encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    title = str(rec.get("title", "")).strip()
                    v = rec.get("verdict")
                    if title and v in ("confirmed", "rejected"):
                        out[v].append(title)
        except OSError:
            pass
        return out

    def _transcribe_images(self, image_paths: list[tuple[str, str]]) -> tuple[str, list[dict]]:
        """Параллельная расшифровка приложенных схем: (общий контекст, метаданные).

        Изображения независимы — расшифровываем одновременно, чтобы анализ
        со схемами укладывался в тот же порядок времени, что и без них.
        """
        from concurrent.futures import ThreadPoolExecutor
        from hypofactory.ingestion.images import transcribe_image

        def _one(item: tuple[str, str]) -> dict:
            path, name = item
            try:
                return {"name": name, "text": transcribe_image(self.llm, path, name)}
            except Exception as e:
                return {"name": name, "error": str(e)[:300]}

        with ThreadPoolExecutor(max_workers=min(4, len(image_paths))) as pool:
            results = list(pool.map(_one, image_paths))
        blocks, meta = [], []
        for r in results:
            if r.get("text"):
                blocks.append(f"=== {r['name']} ===\n{r['text']}")
                meta.append({"name": r["name"], "transcript": r["text"]})
            else:
                meta.append({"name": r["name"], "error": r.get("error", "не расшифровано")})
        return "\n\n".join(blocks), meta

    def analyze(self, path: str, goal: str = "",
                image_paths: Optional[list[tuple[str, str]]] = None) -> dict:
        from hypofactory.diagnosis.engine import MetalPrices
        report = parse_tailings_report(path)
        prices = MetalPrices(usd_per_t_28=self.s.metal_price_28_usd_t,
                             usd_per_t_29=self.s.metal_price_29_usd_t)
        extra_context, images_meta = "", []
        if image_paths:
            if not self.llm.available:
                report.warnings.append(
                    "Приложенные изображения не учтены: расшифровка схем требует LLM-режима.")
            else:
                extra_context, images_meta = self._transcribe_images(image_paths)
                for m in images_meta:
                    if m.get("error"):
                        report.warnings.append(f"Схема «{m['name']}» не расшифрована: {m['error']}")
        hset = generate_hypotheses(report, self.retriever, llm=self.llm, goal=goal,
                                   feedback=self._load_feedback(), prices=prices,
                                   extra_context=extra_context)
        if images_meta:
            hset.meta["images"] = images_meta
        if self.s.metal_price_28_usd_t or self.s.metal_price_29_usd_t:
            hset.meta["prices"] = {"Элемент 28": self.s.metal_price_28_usd_t,
                                   "Элемент 29": self.s.metal_price_29_usd_t,
                                   "currency": "USD/т",
                                   "note": "референсные цены, настраиваются переменными METAL_PRICE_28/29_USD_T"}
        result = hset.to_dict()
        result["report"] = {
            "source_file": os.path.basename(report.source_file),
            "feed_smt": report.feed_smt,
            "tails_smt": report.tails_smt,
            "tails_grade_28": report.tails_grade_28,
            "tails_grade_29": report.tails_grade_29,
            "tails_loss_t_28": report.tails_loss_t_28,
            "tails_loss_t_29": report.tails_loss_t_29,
            "streams": [{"kind": s.kind, "smt": s.smt,
                         "loss_t_28": s.loss_t_28, "loss_t_29": s.loss_t_29,
                         "classes": len(s.classes)} for s in report.streams],
            "warnings": report.warnings,
        }
        return result

    def chat(self, message: str, history: list, analysis: Optional[dict]) -> dict:
        """Диалог по результатам анализа. Без LLM — деградация до выдержек из литературы."""
        from hypofactory.generation import chat as chat_mod
        from hypofactory.generation.generator import _load_facility_profile

        evidence = self.retriever.search(message, k=4, types=("textbook", "guide", "user"))
        sources = [{"title": p.title, "page": p.page} for p in evidence]
        ev_text = "\n".join(
            f"[{i}] {p.title}{f', с.{p.page}' if p.page else ''}: {p.text[:400]}"
            for i, p in enumerate(evidence, 1)) or "(выдержек не найдено)"

        if not self.llm.available:
            reply = ("Диалоговый режим требует LLM (сейчас работает базовый режим). "
                     "Вот наиболее релевантные вашему вопросу выдержки из литературы:\n\n" + ev_text)
            return {"reply": reply, "sources": sources, "mode": "template"}

        facility = _load_facility_profile()
        # схемы/регламенты, приложенные к текущему анализу, — тоже контекст диалога
        img_ctx = "\n\n".join(
            f"=== {m.get('name')} (расшифровка схемы) ===\n{m.get('transcript', '')}"
            for m in ((analysis or {}).get("meta") or {}).get("images", [])
            if m.get("transcript"))
        if img_ctx:
            facility = (facility + "\n\n" + img_ctx[:6000]).strip()
        system = chat_mod.build_system(chat_mod.build_digest(analysis or {}),
                                       facility, ev_text)
        messages = chat_mod.trim_history(history)
        messages.append({"role": "user", "content": message[:4000]})
        try:
            reply = self.llm.chat_messages(system, messages)
        except Exception as e:
            reply = ("Не удалось получить ответ модели (" + str(e)[:200] + "). "
                     "Релевантные выдержки из литературы:\n\n" + ev_text)
            return {"reply": reply, "sources": sources, "mode": "template"}
        return {"reply": reply, "sources": sources, "mode": "llm"}

    def refine(self, hypothesis: dict, instruction: str, analysis: Optional[dict]) -> dict:
        """Переработка гипотезы по указанию эксперта (требует LLM)."""
        if not self.llm.available:
            raise RuntimeError("Переработка гипотез требует LLM-режима (задайте ключ Yandex).")
        from hypofactory.generation.generator import refine_hypothesis
        from hypofactory.generation.chat import build_digest
        goal = ((analysis or {}).get("meta") or {}).get("user_goal", "")
        h = refine_hypothesis(self.llm, self.retriever, hypothesis, instruction,
                              context=build_digest(analysis or {}, max_hyp=6), goal=goal)
        return h.to_dict()

    def save_feedback(self, record: dict) -> None:
        path = os.path.join(self.s.data_index_dir, "feedback.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


@lru_cache
def get_pipeline() -> Pipeline:
    return Pipeline(get_settings())
