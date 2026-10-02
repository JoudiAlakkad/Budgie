"""`GET /api/health`."""

from dataclasses import asdict

from fastapi import APIRouter, Depends

from app.api.schemas import Health
from app.services.health import HealthReport, current_health

router = APIRouter(tags=["health"])


@router.get("/health", response_model=Health, summary="Service, database and model status")
def health(report: HealthReport = Depends(current_health)) -> Health:
    return Health(**asdict(report))
