from fastapi import APIRouter

from app.api.auth import router as auth_router
from app.api.calls import router as calls_router
from app.api.dashboard import router as dashboard_router
from app.api.export import router as export_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(calls_router)
api_router.include_router(dashboard_router)
api_router.include_router(export_router)
