# src/bayleaf_agents/app.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .config import settings
from .logging import setup_logging
from .routers import health
from .routers.agents import router as agents_router  # ⬅️ add this
from .routers.documents import router as documents_router


def create_app() -> FastAPI:
    log = setup_logging()
    app = FastAPI(title="Bayleaf Agents", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.APP_ENV == "dev" else ["https://labcopilot.nonnenmacher.tech"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(agents_router)
    app.include_router(documents_router)

    log.info("app_started", env=settings.APP_ENV, provider=settings.LLM_PROVIDER)
    return app
