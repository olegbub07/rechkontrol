from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session, require_admin
from app.audit import write_audit
from app.dataset import evaluate_wer, import_to_queue, start_download_async, status
from app.models import User

router = APIRouter(tags=["dataset"])


class DatasetLimit(BaseModel):
    limit: int = Field(default=24, ge=8, le=60)


@router.get("/dataset")
def dataset_status(_: User = Depends(current_user)) -> dict:
    return status()


@router.post("/dataset/download")
def dataset_download(
    request: Request,
    body: DatasetLimit | None = None,
    session: Session = Depends(db_session),
    admin: User = Depends(require_admin),
) -> dict:
    start_download_async(limit=(body.limit if body else 24))
    write_audit(session, "dataset.download", user=admin, request=request)
    return status()


@router.post("/dataset/evaluate")
def dataset_evaluate(limit: int | None = Query(default=12, ge=4, le=40), _: User = Depends(current_user)) -> dict:
    try:
        return evaluate_wer(limit=limit)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)[:200]) from exc
    except Exception as exc:
        raise HTTPException(400, "Не удалось посчитать WER") from exc


@router.post("/dataset/import")
def dataset_import(
    request: Request,
    limit: int | None = Query(default=12, ge=1, le=40),
    session: Session = Depends(db_session),
    user: User = Depends(require_admin),
) -> dict:
    try:
        ids = import_to_queue(limit=limit)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)[:200]) from exc
    except Exception as exc:
        raise HTTPException(400, "Не удалось импортировать датасет") from exc
    write_audit(session, "dataset.import", user=user, detail=f"{len(ids)} clips", request=request)
    return {"imported": len(ids), "ids": ids}
