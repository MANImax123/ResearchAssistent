from fastapi import APIRouter

from app.api.v1 import auth, debug, files, health, jobs, projects, stages
from app.core.config import get_settings

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(files.router)
api_router.include_router(jobs.router)
api_router.include_router(stages.router)

if get_settings().is_dev:
    api_router.include_router(debug.router)
