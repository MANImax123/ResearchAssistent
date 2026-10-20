from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.services import health

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    app: str
    env: str
    dependencies: dict[str, health.DependencyStatus]


@router.get("/health", response_model=HealthResponse)
async def get_health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Report the status of every backing service.

    Always returns 200 while the API process is up; `status` is `degraded`
    if any dependency is failing (details per dependency).
    """
    deps = await health.check_all(settings)
    overall: Literal["ok", "degraded"] = "ok" if all(d.status == "ok" for d in deps.values()) else "degraded"
    return HealthResponse(status=overall, app=settings.app_name, env=settings.app_env, dependencies=deps)
