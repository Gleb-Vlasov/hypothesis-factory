"""Централизованная конфигурация через переменные окружения (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # dotenv опционален
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))


def _price(v: str | None, default: float | None) -> float | None:
    """Цена из env: не задано → дефолт; 0/отрицательная/мусор → выключено (None)."""
    if v is None or not v.strip():
        return default
    try:
        p = float(v)
    except ValueError:
        return None
    return p if p > 0 else None


def _b(v: str | None, default: bool = False) -> bool:
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # LLM (Yandex AI Studio, OpenAI-совместимый)
    yandex_api_key: str | None = None
    yandex_folder_id: str | None = None
    llm_base_url: str = "https://llm.api.cloud.yandex.net/v1"
    llm_model_id: str = "qwen3-235b-a22b-fp8"       # осн. модель (Apache-2.0, 235B MoE)
    llm_fallback_model_id: str = "gpt-oss-120b"
    llm_temperature: float = 0.2                    # доменная корректность → низкая t
    llm_max_tokens: int = 4000
    llm_timeout: float = 120.0

    # Vision-модель для расшифровки схем/регламентов (тот же эндпоинт и ключ).
    # Reasoning-модель: бюджет токенов должен вмещать и рассуждения, и ответ.
    vision_model_id: str = "qwen3.6-35b-a3b"
    vision_max_tokens: int = 12000
    vision_timeout: float = 180.0

    # Эмбеддинги (семантический слой RAG) — по умолчанию включены; при отсутствии
    # ключа Yandex или файлов индекса система сама деградирует до BM25
    embeddings_enabled: bool = True
    embeddings_backend: str = "yandex"              # yandex | local
    local_embedding_model: str = "BAAI/bge-m3"

    # Пути
    data_dir: str = os.path.join(_PROJECT_ROOT, "..", "DATA")
    data_index_dir: str = os.path.join(_PROJECT_ROOT, "data_index")

    # Разграничение доступа: если задан, все /api/* (кроме health) требуют
    # заголовок X-API-Key с этим значением. По умолчанию выключено.
    app_access_key: str | None = None

    # Референсные цены металлов для экономической оценки, USD/т.
    # 0 или пусто — отключить денежную оценку (строгая анонимизация).
    metal_price_28_usd_t: float | None = 16500.0
    metal_price_29_usd_t: float | None = 9200.0

    @property
    def llm_ready(self) -> bool:
        return bool(self.yandex_api_key and self.yandex_folder_id)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            yandex_api_key=os.environ.get("YANDEX_API_KEY"),
            yandex_folder_id=os.environ.get("YANDEX_FOLDER_ID"),
            llm_base_url=os.environ.get("LLM_BASE_URL", cls.llm_base_url),
            llm_model_id=os.environ.get("LLM_MODEL_ID", cls.llm_model_id),
            llm_fallback_model_id=os.environ.get("LLM_FALLBACK_MODEL_ID", cls.llm_fallback_model_id),
            llm_temperature=float(os.environ.get("LLM_TEMPERATURE", cls.llm_temperature)),
            llm_max_tokens=int(os.environ.get("LLM_MAX_TOKENS", cls.llm_max_tokens)),
            vision_model_id=os.environ.get("VISION_MODEL_ID", cls.vision_model_id),
            vision_max_tokens=int(os.environ.get("VISION_MAX_TOKENS", cls.vision_max_tokens)),
            embeddings_enabled=_b(os.environ.get("EMBEDDINGS_ENABLED"), True),
            embeddings_backend=os.environ.get("EMBEDDINGS_BACKEND", cls.embeddings_backend),
            local_embedding_model=os.environ.get("LOCAL_EMBEDDING_MODEL", cls.local_embedding_model),
            data_dir=os.environ.get("DATA_DIR", cls.data_dir),
            data_index_dir=os.environ.get("DATA_INDEX_DIR", cls.data_index_dir),
            app_access_key=os.environ.get("APP_ACCESS_KEY") or None,
            metal_price_28_usd_t=_price(os.environ.get("METAL_PRICE_28_USD_T"), cls.metal_price_28_usd_t),
            metal_price_29_usd_t=_price(os.environ.get("METAL_PRICE_29_USD_T"), cls.metal_price_29_usd_t),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
