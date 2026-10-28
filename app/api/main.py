"""Public HTTP API for source ingestion and canonical graph queries."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.edges import router as edges_router
from app.api.routes.ingestions import router as ingestions_router
from app.api.routes.landing import router as landing_router
from app.api.routes.nodes import router as nodes_router
from app.api.routes.workspaces import router as workspace_router
from app.api.routes.auth import router as auth_router
from app.db.postgres import (
    close_async_pool,
    open_async_pool,
)


def _cors_origins() -> list[str]:
    """Parse API_CORS_ORIGINS, tolerating quotes and trailing slashes.

    Browsers send origins as scheme://host[:port] with no path, so a pasted
    "https://example.com/" would otherwise never match.
    """

    configured = os.getenv("API_CORS_ORIGINS", "http://localhost:5173")
    origins: list[str] = []
    for value in configured.strip().strip("'\"").split(","):
        origin = value.strip().strip("'\"").rstrip("/")
        if origin and origin not in origins:
            origins.append(origin)
    return origins


CORS_ORIGINS = _cors_origins()
print(f"CORS allowed origins: {CORS_ORIGINS}", flush=True)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await open_async_pool()
    try:
        yield
    finally:
        await close_async_pool()


app = FastAPI(
    title="Headspace API",
    version="0.1.0",
    description=(
        "Persists source content, extracts a chunk-aggregated proposed graph, "
        "atomically applies canonical graph changes, and queries canonical nodes."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(auth_router)
app.include_router(workspace_router)
app.include_router(nodes_router)
app.include_router(edges_router)
app.include_router(landing_router)
app.include_router(ingestions_router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
