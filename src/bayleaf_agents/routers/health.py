from fastapi import APIRouter
from ..config import settings

router = APIRouter(tags=["health"])

@router.get("/health")
def health():
    return {"status": "ok", "env": settings.APP_ENV, "provider": settings.LLM_PROVIDER}
