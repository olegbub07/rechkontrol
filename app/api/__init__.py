from fastapi import APIRouter

from app.api.auth import router as auth_router
from app.api.calls import router as calls_router
from app.api.dashboard import router as dashboard_router
from app.api.admin import router as admin_router
from app.api.dataset import router as dataset_router
from app.api.export import router as export_router
from app.api.checklist import router as checklist_router
from app.api.operators import router as operators_router
from app.api.reports import router as reports_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(calls_router)
api_router.include_router(dashboard_router)
api_router.include_router(export_router)
api_router.include_router(dataset_router)
api_router.include_router(admin_router)
api_router.include_router(checklist_router)
api_router.include_router(operators_router)
api_router.include_router(reports_router)
