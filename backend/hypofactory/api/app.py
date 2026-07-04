"""FastAPI-приложение «Фабрика гипотез».

Эндпоинты:
  GET  /api/health           — статус (режим, размер корпуса, готовность LLM)
  POST /api/analyze          — загрузка Excel-баланса хвостов → диагноз + гипотезы
  POST /api/feedback         — фидбэк эксперта по гипотезе (обучение на фидбэке)
Статика фронтенда (если собрана) отдаётся с корня.
"""
from __future__ import annotations

import os
import tempfile

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from hypofactory.api.service import get_pipeline

app = FastAPI(title="Фабрика гипотез", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

ALLOWED_EXT = (".xlsx", ".xlsm", ".xls")


@app.middleware("http")
async def access_control(request, call_next):
    """Опциональное разграничение доступа: APP_ACCESS_KEY → X-API-Key на /api/*."""
    from hypofactory.config import get_settings
    key = get_settings().app_access_key
    path = request.url.path
    if key and path.startswith("/api/") and path != "/api/health":
        if request.headers.get("x-api-key") != key:
            return JSONResponse({"detail": "Требуется ключ доступа (заголовок X-API-Key)."},
                                status_code=401)
    return await call_next(request)


@app.get("/api/health")
def health():
    return get_pipeline().status()


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...), goal: str = Form(""),
                  images: list[UploadFile] = File(default=[])):
    """Анализ: Excel-баланс хвостов (обязателен) + до 5 изображений схем/регламентов
    (опционально — расшифровываются vision-моделью и учитываются в гипотезах)."""
    from hypofactory.ingestion.images import IMAGE_EXT, MAX_IMAGE_MB, MAX_IMAGES_PER_ANALYZE
    name = file.filename or "upload.xlsx"
    if not name.lower().endswith(ALLOWED_EXT):
        raise HTTPException(status_code=400, detail="Ожидается файл Excel (.xlsx) с балансом хвостов.")
    if name.lower().endswith(".xls"):
        raise HTTPException(status_code=400, detail="Старый формат .xls не поддерживается — "
                            "пересохраните файл как .xlsx (Excel: «Сохранить как» → «Книга Excel»).")
    if len(images) > MAX_IMAGES_PER_ANALYZE:
        raise HTTPException(status_code=400,
                            detail=f"Не больше {MAX_IMAGES_PER_ANALYZE} изображений за один анализ.")
    data = await file.read()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(name)[1])
    tmp_images: list[tuple[str, str]] = []  # (путь, исходное имя)
    try:
        tmp.write(data)
        tmp.close()
        for img in images:
            iname = img.filename or "image.png"
            if not iname.lower().endswith(IMAGE_EXT):
                raise HTTPException(status_code=400,
                                    detail=f"«{iname}»: изображения принимаются в PNG/JPG/WebP/BMP.")
            idata = await img.read()
            if len(idata) > MAX_IMAGE_MB * 1024 * 1024:
                raise HTTPException(status_code=400,
                                    detail=f"«{iname}»: изображение больше {MAX_IMAGE_MB} МБ.")
            it = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(iname)[1])
            it.write(idata)
            it.close()
            tmp_images.append((it.name, iname))
        result = get_pipeline().analyze(tmp.name, goal=goal.strip()[:2000],
                                        image_paths=tmp_images or None)
        result["report"]["source_file"] = name
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Не удалось обработать файл: {e}")
    finally:
        os.unlink(tmp.name)
        for p, _ in tmp_images:
            try:
                os.unlink(p)
            except OSError:
                pass


@app.post("/api/export/{fmt}")
def export(fmt: str, payload: dict = Body(...)):
    """Экспорт результата анализа (тело = ответ /api/analyze) в docx|pdf|csv."""
    from fastapi.responses import Response
    from hypofactory.report import export as ex
    try:
        _DOCX_MT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if fmt == "docx":
            data, mt, fname = ex.to_docx(payload), _DOCX_MT, "hypotheses.docx"
        elif fmt == "pdf":
            data, mt, fname = ex.to_pdf(payload), "application/pdf", "hypotheses.pdf"
        elif fmt == "csv":
            data, mt, fname = ex.to_csv(payload), "text/csv; charset=utf-8", "hypotheses.csv"
        elif fmt == "tasks":
            # задачи для импорта в Jira / YouTrack
            data, mt, fname = ex.to_tasks_csv(payload), "text/csv; charset=utf-8", "tasks_jira_youtrack.csv"
        elif fmt == "protocol":
            # протоколы лабораторной проверки гипотез
            data, mt, fname = ex.to_protocol_docx(payload), _DOCX_MT, "protocols.docx"
        else:
            raise HTTPException(status_code=400, detail="Формат: docx | pdf | csv | tasks | protocol")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Ошибка экспорта: {e}")
    return Response(content=data, media_type=mt,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/api/knowledge")
def knowledge_list():
    """Состав базы знаний: вшитый корпус + загруженная пользователем литература."""
    return get_pipeline().knowledge_status()


@app.post("/api/knowledge")
async def knowledge_add(file: UploadFile = File(...), author: str = Form(""),
                        year: str = Form(""), note: str = Form("")):
    """Добавление литературы (PDF/DOCX/TXT/MD) или изображения схемы/регламента
    (PNG/JPG/WebP/BMP — расшифровывается vision-моделью) в базу знаний на лету.

    Необязательные метаданные: author (авторы), year (год), note (условия
    экспериментов/примечание) — попадают в цитирование.
    """
    from hypofactory.ingestion.images import IMAGE_EXT
    from hypofactory.ingestion.literature import ALLOWED_EXT as KB_EXT, MAX_FILE_MB
    name = file.filename or "document"
    if not name.lower().endswith(KB_EXT + IMAGE_EXT):
        raise HTTPException(status_code=400,
                            detail="Ожидается PDF, DOCX, TXT, MD или изображение (PNG/JPG/WebP/BMP).")
    data = await file.read()
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Файл больше {MAX_FILE_MB} МБ.")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(name)[1])
    try:
        tmp.write(data)
        tmp.close()
        meta = {"author": author[:200], "year": year[:20], "note": note[:500]}
        return get_pipeline().add_knowledge(tmp.name, name, meta)
    except RuntimeError as e:
        # изображение без LLM-режима — временная недоступность, не ошибка данных
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Не удалось обработать документ: {e}")
    finally:
        os.unlink(tmp.name)


@app.post("/api/chat")
def chat(payload: dict = Body(...)):
    """Диалог по результатам анализа.

    Тело: {"message": str, "history": [{"role","content"}...], "analysis": <ответ /api/analyze>}.
    Сервер без состояния: историю присылает клиент. Без LLM отвечает выдержками из литературы.
    """
    message = str(payload.get("message", "")).strip()
    if not message:
        raise HTTPException(status_code=400, detail="Пустое сообщение.")
    history = payload.get("history") or []
    analysis = payload.get("analysis")
    if analysis is not None and not isinstance(analysis, dict):
        analysis = None
    try:
        return get_pipeline().chat(message, history, analysis)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Ошибка диалога: {e}")


@app.post("/api/refine")
def refine(payload: dict = Body(...)):
    """Переработка гипотезы моделью по указанию эксперта.

    Тело: {"hypothesis": <объект гипотезы>, "instruction": str, "analysis": <ответ analyze>}.
    Возвращает обновлённую гипотезу той же структуры.
    """
    hyp = payload.get("hypothesis")
    instruction = str(payload.get("instruction", "")).strip()
    if not isinstance(hyp, dict) or not instruction:
        raise HTTPException(status_code=400, detail="Нужны hypothesis (объект) и instruction (текст).")
    analysis = payload.get("analysis")
    if analysis is not None and not isinstance(analysis, dict):
        analysis = None
    try:
        return {"hypothesis": get_pipeline().refine(hyp, instruction[:2000], analysis)}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Не удалось переработать гипотезу: {e}")


@app.post("/api/feedback")
def feedback(payload: dict = Body(...)):
    hid = payload.get("hypothesis_id")
    verdict = payload.get("verdict")  # confirmed | rejected | edited
    if not hid or verdict not in ("confirmed", "rejected", "edited"):
        raise HTTPException(status_code=400, detail="Нужны hypothesis_id и verdict (confirmed|rejected|edited).")
    get_pipeline().save_feedback(payload)
    return {"ok": True}


# --- Статика фронтенда (без сборки: frontend/, либо собранный frontend/dist) ---
_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")
for _cand in ("frontend/dist", "frontend"):
    _dir = os.path.join(_ROOT, *_cand.split("/"))
    if os.path.isfile(os.path.join(_dir, "index.html")):
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=_dir, html=True), name="frontend")
        break
