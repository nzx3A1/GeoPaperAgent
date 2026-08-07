from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.modules.tasks.router import router as tasks_router

router = APIRouter()
router.include_router(health_router)
router.include_router(tasks_router, prefix="/tasks", tags=["tasks"])
