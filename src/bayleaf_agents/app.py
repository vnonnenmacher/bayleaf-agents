from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .config import settings
from .logging import setup_logging
from .routers import health, chat


def create_app() -> FastAPI:
    log = setup_logging()
    app = FastAPI(title="Bayleaf Agents", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.APP_ENV == "dev" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(chat.router)

    log.info("app_started", env=settings.APP_ENV, provider=settings.LLM_PROVIDER)
    return app
