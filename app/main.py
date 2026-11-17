from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import api_router
from app.auth import ensure_staff_accounts, restore_persisted_guards
from app.demo_voice import start_demo_voices
from app.config import settings
from app.crypto import get_fernet, jwt_secret
from app.db import SessionLocal, init_db, protect_store
from app.security import SecurityHeadersMiddleware, clean_tmp_dir, trusted_hosts
from app.seed import seed_if_empty
from app.transcribe import local_model_ready, model_loaded
from app.worker import start_workers, stop_workers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rechkontrol")

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _warm_model() -> None:
    try:
        from app.transcribe import load_model

        load_model()
        log.info("Модель Whisper прогрета")
    except Exception:
        log.exception("Не удалось прогреть модель Whisper")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    get_fernet()
    jwt_secret()
    protect_store()
    clean_tmp_dir()
    with SessionLocal() as session:
        seed_if_empty(session)
        ensure_staff_accounts(session)
        session.commit()
    restore_persisted_guards()
    start_workers()
    start_demo_voices()
    log.info("Стартовые пароли, если они выданы, записаны в data/initial_credentials.txt и в эту консоль.")
    if local_model_ready():
        threading.Thread(target=_warm_model, name="whisper-warm", daemon=True).start()
    log.info("РечьКонтроль listening on http://%s:%s", settings.app_host, settings.app_port)
    try:
        yield
    finally:
        stop_workers()


docs = "/docs" if settings.app_debug else None
app = FastAPI(
    title=settings.app_name,
    description="Локальная платформа расшифровки и контроля качества звонков. Данные не покидают контур.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=docs,
    redoc_url=docs.replace("/docs", "/redoc") if docs else None,
    openapi_url="/openapi.json" if settings.app_debug else None,
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts())
app.include_router(api_router, prefix="/api")


@app.exception_handler(RequestValidationError)
async def hide_validation_input(_request: Request, _exc: RequestValidationError):
    """Не возвращаем присланные поля: в них может быть пароль."""
    return JSONResponse(status_code=422, content={"detail": "Проверьте поля запроса"})


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    if isinstance(exc, StarletteHTTPException):
        return await http_exception_handler(request, exc)
    log.exception("unhandled error: %s", exc.__class__.__name__)
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка. Подробности в журнале сервера."})


@app.get("/api/health")
def health() -> dict:
    from app.db import SessionLocal as SL
    from app.models import Call

    with SL() as session:
        queued = session.query(Call).filter(Call.status == "queued").count()
        processing = session.query(Call).filter(Call.status == "processing").count()
    return {
        "status": "ok",
        "local": True,
        "offline": local_model_ready(),
        "whisper_loaded": model_loaded(),
        "whisper_model": settings.whisper_model,
        "queue": {"queued": queued, "processing": processing},
    }


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})
