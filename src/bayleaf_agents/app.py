# src/bayleaf_agents/app.py
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from .config import settings
from .logging import setup_logging
from .routers import health
from .routers.agents import router as agents_router
from .routers.documents import router as documents_router


def create_app() -> FastAPI:
    log = setup_logging()
    app = FastAPI(title="Bayleaf Agents", version="0.1.0")
    app.router.redirect_slashes = False

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.APP_ENV == "dev" else ["https://labcopilot.nonnenmacher.tech"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def canonicalize_trailing_slash(request: Request, call_next):
        path = request.url.path
        if path != "/" and path.endswith("/"):
            canonical_path = path.rstrip("/")
            if request.url.query:
                canonical_path = f"{canonical_path}?{request.url.query}"
            return RedirectResponse(url=canonical_path, status_code=308)
        return await call_next(request)

    app.include_router(health.router)
    app.include_router(agents_router)
    app.include_router(documents_router)

    log.info("app_started", env=settings.APP_ENV, provider=settings.LLM_PROVIDER)
    return app
