from fastapi import APIRouter, Depends

from app.shared.security import require_viewer

router = APIRouter(prefix="/v1", tags=["core"], dependencies=[Depends(require_viewer)])


@router.get("/status")
def core_status() -> dict[str, str]:
    return {"service": "system-automation", "status": "ok"}
