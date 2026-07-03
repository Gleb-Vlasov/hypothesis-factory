# Лёгкий образ «Фабрики гипотез».
# LLM выполняется по API (Yandex AI Studio), поэтому GPU/torch не требуются.
# Индекс знаний (data_index/corpus.jsonl) вшит в образ — сборка не зависит от сети.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/backend \
    DATA_INDEX_DIR=/app/data_index

WORKDIR /app

# Зависимости отдельным слоем (кэшируется)
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Код, вшитый индекс знаний (текстовый корпус + векторный индекс ~2.4 МБ) и фронтенд.
# Рантайм-файлы (feedback, пользовательская литература) отсечены в .dockerignore.
COPY backend /app/backend
COPY data_index/ /app/data_index/
COPY frontend /app/frontend

EXPOSE 8000

# Health-check без внешних утилит (в slim нет curl)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').getcode()==200 else 1)"

CMD ["python", "-m", "uvicorn", "hypofactory.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
