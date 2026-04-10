from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import ModelConfig
from app.responses import success
from app.schemas import ModelConfigPayload


router = APIRouter(prefix="/api/config", tags=["config"])


@router.post("/models")
def list_models(
    payload: dict | None = Body(default=None),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    rows = db.query(ModelConfig).order_by(ModelConfig.updated_at.desc()).all()
    return success(
        [
            {
                "id": row.id,
                "model_type": row.model_type,
                "model_name": row.model_name,
                "config": json.loads(row.config) if row.config else {},
                "is_active": bool(row.is_active),
                "updated_at": row.updated_at,
            }
            for row in rows
        ]
    )


@router.post("/models/create")
def create_model(payload: ModelConfigPayload, db: Session = Depends(get_db), user=Depends(require_admin)):
    item = ModelConfig(
        model_type=payload.model_type,
        model_name=payload.model_name,
        config=json.dumps(payload.config, ensure_ascii=False),
        is_active=1 if payload.is_active else 0,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return success(
        {
            "id": item.id,
            "model_type": item.model_type,
            "model_name": item.model_name,
            "config": json.loads(item.config) if item.config else {},
            "is_active": bool(item.is_active),
            "updated_at": item.updated_at,
        },
        message="created",
    )


@router.post("/models/{model_id}")
def update_model(model_id: int, payload: ModelConfigPayload, db: Session = Depends(get_db), user=Depends(require_admin)):
    row = db.query(ModelConfig).filter(ModelConfig.id == model_id).first()
    if not row:
        return {"code": 1000, "message": "model config not found", "data": None}
    row.model_type = payload.model_type
    row.model_name = payload.model_name
    row.config = json.dumps(payload.config, ensure_ascii=False)
    row.is_active = 1 if payload.is_active else 0
    db.add(row)
    db.commit()
    return success(message="updated")
