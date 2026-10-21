"""FastAPI application entrypoint."""

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import engine

API_PREFIX = "/api/v1"

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    log.info("startup", app=settings.app_name, env=settings.app_env)
    yield
    await engine.dispose()
    log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=f"{settings.app_name} API",
        version="0.1.0",
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_logging(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("request_failed", method=request.method, path=request.url.path)
            # A JSON 500 the browser can read: re-raising would bypass CORS and show up as a CORS error
            response = JSONResponse(
                {"detail": "Something went wrong on the server. Please try again.", "request_id": request_id},
                status_code=500,
            )
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers["X-Request-ID"] = request_id
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
            request_id=request_id,
        )
        return response

    # Added last, so it is the outermost layer: every response, errors included, carries CORS headers
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.include_router(api_router, prefix=API_PREFIX)
    return app


app = create_app()
