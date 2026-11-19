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
from app.db import SessionLocal, encrypt_plain_texts, init_db, protect_store
from app.retention import start_retention, stop_retention
from app.security import SecurityHeadersMiddleware, clean_tmp_dir, trusted_hosts
from app.seed import seed_if_empty
from app.transcribe import local_model_ready
from app.worker import start_workers, stop_workers
from app.inbox import start_inbox, stop_inbox
from app.webhooks import start_webhooks, stop_webhooks

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
    encrypt_plain_texts()
    clean_tmp_dir()
    with SessionLocal() as session:
        seed_if_empty(session)
        ensure_staff_accounts(session)
        session.commit()
    restore_persisted_guards()
    start_workers()
    start_retention()
    start_demo_voices()
    start_webhooks()
    start_inbox()
    log.info("Стартовые пароли, если они выданы, записаны в data/initial_credentials.txt и в эту консоль.")
    if local_model_ready():
        threading.Thread(target=_warm_model, name="whisper-warm", daemon=True).start()
    log.info("РечьКонтроль listening on http://%s:%s", settings.app_host, settings.app_port)
    try:
        yield
    finally:
        stop_inbox()
        stop_retention()
        stop_workers()
        stop_webhooks()


_show_docs = settings.app_debug or settings.api_docs
docs = "/docs" if _show_docs else None
app = FastAPI(
    title=settings.app_name,
    description=(
        "Локальная платформа расшифровки и контроля качества звонков. Данные не покидают контур.\n\n"
        "**Внешним системам** нужен раздел `external-api` (`/api/v1/...`): ключ в заголовке `X-API-Key`, "
        "загрузка записи, статус, результат и список звонков. О готовности сообщает вебхук `call.processed`. "
        "Подробности и примеры curl: docs/INTEGRATION.md."
    ),
    openapi_tags=[
        {"name": "external-api", "description": "REST API для CRM, АТС и BI по API-ключу (X-API-Key)."},
        {"name": "admin", "description": "Администрирование, API-ключи, журнал вебхуков. Нужна сессия администратора."},
    ],
    version="1.0.0",
    lifespan=lifespan,
    # Swagger UI тянет скрипты с CDN, а контур без интернета: своя страница /docs ниже.
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json" if _show_docs else None,
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
    """Без входа отдаём только живость. Очередь и модель видны в кабинете после авторизации."""
    return {"status": "ok"}


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if docs:

    @app.get("/docs", include_in_schema=False)
    def api_docs() -> FileResponse:
        return FileResponse(STATIC_DIR / "apidocs.html", headers={"Cache-Control": "no-store"})


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})
