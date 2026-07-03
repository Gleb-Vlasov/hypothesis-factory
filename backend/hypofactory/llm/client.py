"""Провайдер-агностичный LLM-клиент (OpenAI-совместимый, провайдер — Yandex AI Studio).

Если ключ не задан, `available == False` — вызывающий код обязан деградировать
(шаблонные гипотезы), а не падать. Это ключ к надёжному деплою.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from hypofactory.config import Settings, get_settings


class LLMClient:
    def __init__(self, settings: Optional[Settings] = None):
        self.s = settings or get_settings()
        self._client = None

    @property
    def available(self) -> bool:
        return self.s.llm_ready

    @property
    def model_uri(self) -> str:
        # Формат Yandex AI Studio: gpt://<folder>/<model>/latest
        return f"gpt://{self.s.yandex_folder_id}/{self.s.llm_model_id}/latest"

    def _ensure(self):
        if self._client is None:
            from openai import OpenAI  # ленивый импорт
            self._client = OpenAI(api_key=self.s.yandex_api_key,
                                  base_url=self.s.llm_base_url,
                                  timeout=self.s.llm_timeout)
        return self._client

    def chat(self, system: str, user: str,
             temperature: Optional[float] = None,
             max_tokens: Optional[int] = None) -> str:
        if not self.available:
            raise RuntimeError("LLM недоступен: не задан YANDEX_API_KEY/FOLDER_ID.")
        client = self._ensure()
        resp = client.chat.completions.create(
            model=self.model_uri,
            temperature=self.s.llm_temperature if temperature is None else temperature,
            max_tokens=self.s.llm_max_tokens if max_tokens is None else max_tokens,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content or ""

    def chat_messages(self, system: str, messages: list[dict],
                      temperature: Optional[float] = None,
                      max_tokens: Optional[int] = None) -> str:
        """Многоходовый диалог: messages = [{"role": "user"|"assistant", "content": str}, ...]."""
        if not self.available:
            raise RuntimeError("LLM недоступен: не задан YANDEX_API_KEY/FOLDER_ID.")
        client = self._ensure()
        resp = client.chat.completions.create(
            model=self.model_uri,
            temperature=self.s.llm_temperature if temperature is None else temperature,
            max_tokens=self.s.llm_max_tokens if max_tokens is None else max_tokens,
            messages=[{"role": "system", "content": system}, *messages],
        )
        text = resp.choices[0].message.content or ""
        # некоторые reasoning-модели оборачивают рассуждения в <think>…</think>
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    def chat_json(self, system: str, user: str, **kw) -> object:
        """Запрашивает ответ и извлекает JSON (устойчиво к обёрткам ```json ...```)."""
        raw = self.chat(system, user + "\n\nОтвет верни СТРОГО одним JSON без пояснений.", **kw)
        return extract_json(raw)


def extract_json(text: str) -> object:
    text = text.strip()
    # снять markdown-ограждение
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # выцепить первый сбалансированный JSON-объект/массив
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError("Не удалось извлечь JSON из ответа модели.")
