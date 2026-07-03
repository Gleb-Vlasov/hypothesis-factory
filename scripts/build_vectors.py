# -*- coding: utf-8 -*-
"""Предпосчёт векторного индекса корпуса через эмбеддинги Yandex AI Studio.

Устойчив к прерываниям: прогресс пишется в embeddings_partial.jsonl, повторный
запуск докачивает только недостающие записи. Запросы идут параллельно
(NUM_WORKERS потоков) с ретраями на 429/сетевые ошибки.
"""
import io
import json
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

import numpy as np
from hypofactory.rag.bm25_index import load_corpus
from hypofactory.config import get_settings

NUM_WORKERS = 8
INDEX = os.path.join(os.path.dirname(__file__), "..", "data_index")
PARTIAL = os.path.join(INDEX, "embeddings_partial.jsonl")

records = load_corpus(os.path.join(INDEX, "corpus.jsonl"))
s = get_settings()
assert s.llm_ready, "нет ключей Yandex"

from openai import OpenAI
client = OpenAI(api_key=s.yandex_api_key, base_url=s.llm_base_url, timeout=60)
model = f"emb://{s.yandex_folder_id}/text-search-doc/latest"

# --- Возобновление: уже посчитанные id ---
done: dict[str, list] = {}
if os.path.isfile(PARTIAL):
    with open(PARTIAL, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                done[row["id"]] = row["vec"]
todo = [r for r in records if r["id"] not in done]
print(f"Корпус: {len(records)}; готово: {len(done)}; осталось: {len(todo)}")

lock = threading.Lock()
partial_f = open(PARTIAL, "a", encoding="utf-8")
counter = {"n": 0, "skip": 0}
t0 = time.time()


def embed_one(r: dict):
    for attempt in range(4):
        try:
            resp = client.embeddings.create(model=model, input=r["text"][:1900],
                                            encoding_format="float")  # Yandex: только float
            vec = resp.data[0].embedding
            with lock:
                partial_f.write(json.dumps({"id": r["id"], "vec": vec}) + "\n")
                partial_f.flush()
                counter["n"] += 1
                n = counter["n"]
            if n % 100 == 0:
                rate = n / (time.time() - t0)
                print(f"{n}/{len(todo)}  ({rate:.1f} rps, ETA {(len(todo)-n)/max(rate,0.1):.0f}s)")
            return
        except Exception as e:
            if attempt == 3:
                with lock:
                    counter["skip"] += 1
                print(f"SKIP {r['id']}: {e}")
            else:
                time.sleep(1.5 * (attempt + 1))


with ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
    list(as_completed([pool.submit(embed_one, r) for r in todo]))
partial_f.close()

# --- Финальная сборка npy + ids.json из partial (в порядке корпуса) ---
done = {}
with open(PARTIAL, encoding="utf-8") as f:
    for line in f:
        if line.strip():
            row = json.loads(line)
            done[row["id"]] = row["vec"]
ids = [r["id"] for r in records if r["id"] in done]
if len(ids) < len(records):
    print(f"ВНИМАНИЕ: посчитано {len(ids)}/{len(records)} — перезапустите скрипт для докачки.")
m = np.array([done[i] for i in ids], dtype=np.float32)
np.save(os.path.join(INDEX, "embeddings.npy"), m)
json.dump(ids, open(os.path.join(INDEX, "ids.json"), "w", encoding="utf-8"), ensure_ascii=False)
print(f"DONE shape={m.shape}, skip={counter['skip']}, за {time.time()-t0:.0f}с")
