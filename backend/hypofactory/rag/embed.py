"""Семантический слой ретривера: эмбеддинги и векторный индекс.

Два бэкенда (выбираются конфигом), оба опциональны — при их недоступности
система работает на одном BM25:
  - YandexEmbedder — эмбеддинги Yandex AI Studio (лёгкий образ, интернет в рантайме);
  - LocalEmbedder  — sentence-transformers (полностью офлайн, тяжелее образ).

Документные эмбеддинги считаются офлайн и кладутся в data_index/ (embeddings.npy +
ids.json). В рантайме кодируется только запрос.
"""
from __future__ import annotations

import json
import os
from typing import Optional, Protocol

import numpy as np


class Embedder(Protocol):
    dim: int
    def encode(self, texts: list[str], is_query: bool = False) -> np.ndarray: ...


class YandexEmbedder:
    """Эмбеддинги через OpenAI-совместимый/спец. эндпоинт Yandex AI Studio.

    Для документов и запросов используются разные модели (doc/query) —
    это рекомендация Yandex. Требует YANDEX_API_KEY и YANDEX_FOLDER_ID.
    """
    def __init__(self, api_key: str, folder_id: str,
                 base_url: str = "https://llm.api.cloud.yandex.net/v1",
                 doc_model: str = "text-search-doc",
                 query_model: str = "text-search-query"):
        from openai import OpenAI  # ленивый импорт
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.folder_id = folder_id
        self.doc_model = f"emb://{folder_id}/{doc_model}/latest"
        self.query_model = f"emb://{folder_id}/{query_model}/latest"
        self.dim = 256  # уточняется по факту ответа

    def encode(self, texts: list[str], is_query: bool = False) -> np.ndarray:
        model = self.query_model if is_query else self.doc_model
        vecs = []
        for t in texts:
            # Yandex не поддерживает base64-формат, который OpenAI SDK просит по умолчанию
            resp = self.client.embeddings.create(model=model, input=t, encoding_format="float")
            vecs.append(resp.data[0].embedding)
        arr = np.array(vecs, dtype=np.float32)
        self.dim = arr.shape[1]
        return _l2norm(arr)


class LocalEmbedder:
    """Полностью офлайн эмбеддинги (sentence-transformers, напр. bge-m3)."""
    def __init__(self, model_name: str = "BAAI/bge-m3", device: Optional[str] = None):
        from sentence_transformers import SentenceTransformer  # ленивый импорт
        self.model = SentenceTransformer(model_name, device=device)
        self.dim = self.model.get_sentence_embedding_dimension()

    def encode(self, texts: list[str], is_query: bool = False) -> np.ndarray:
        arr = self.model.encode(texts, normalize_embeddings=True,
                                batch_size=32, show_progress_bar=False)
        return np.asarray(arr, dtype=np.float32)


def _l2norm(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return m / n


class VectorIndex:
    """Косинусный поиск по предпосчитанной матрице документных эмбеддингов."""
    def __init__(self, ids: list[str], matrix: np.ndarray):
        self.ids = ids
        self.matrix = _l2norm(matrix.astype(np.float32))

    def search(self, query_vec: np.ndarray, k: int = 20) -> list[tuple[str, float]]:
        q = _l2norm(query_vec.reshape(1, -1))[0]
        sims = self.matrix @ q
        order = np.argsort(-sims)[:k]
        return [(self.ids[i], float(sims[i])) for i in order]


def build_vector_index(records: list[dict], embedder: Embedder, out_dir: str,
                       batch_log: int = 200) -> None:
    ids = [r["id"] for r in records]
    texts = [r["text"] for r in records]
    vecs = []
    for i in range(0, len(texts), batch_log):
        chunk = texts[i:i + batch_log]
        vecs.append(embedder.encode(chunk, is_query=False))
        print(f"  эмбеддинги {i + len(chunk)}/{len(texts)}")
    matrix = np.vstack(vecs)
    os.makedirs(out_dir, exist_ok=True)
    np.save(os.path.join(out_dir, "embeddings.npy"), matrix)
    with open(os.path.join(out_dir, "ids.json"), "w", encoding="utf-8") as f:
        json.dump(ids, f, ensure_ascii=False)


def load_vector_index(out_dir: str) -> Optional[VectorIndex]:
    emb_path = os.path.join(out_dir, "embeddings.npy")
    ids_path = os.path.join(out_dir, "ids.json")
    if not (os.path.exists(emb_path) and os.path.exists(ids_path)):
        return None
    matrix = np.load(emb_path)
    with open(ids_path, encoding="utf-8") as f:
        ids = json.load(f)
    return VectorIndex(ids, matrix)
