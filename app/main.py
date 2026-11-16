from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import api_router
from app.auth import ensure_admin
from app.config import settings
from app.crypto import get_fernet, jwt_secret
from app.db import SessionLocal, init_db
from app.security import SecurityHeadersMiddleware, clean_tmp_dir
from app.seed import seed_if_empty
from app.transcribe import local_model_ready, model_loaded
from app.worker import start_workers, stop_workers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rechkontrol")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    get_fernet()
    jwt_secret()
    clean_tmp_dir()
    with SessionLocal() as session:
        ensure_admin(session)
        seed_if_empty(session)
        session.commit()
    start_workers()
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
app.include_router(api_router, prefix="/api")


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
    return FileResponse(STATIC_DIR / "index.html")
