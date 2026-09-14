from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.shared.metrics.security import require_metrics_key

router = APIRouter(tags=["metrics"], dependencies=[Depends(require_metrics_key)])


@router.get("/metrics")
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
