"""Public HTTP API for source ingestion and canonical graph queries."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.edges import router as edges_router
from app.api.ingestions import router as ingestions_router
from app.api.landing import router as landing_router
from app.api.nodes import router as nodes_router
from app.auth.api import router as auth_router
from app.persistence.postgres.connection import (
    close_async_pool,
    open_async_pool,
)
from app.workspaces.api import router as workspace_router


def _cors_origins() -> list[str]:
    configured = os.getenv("API_CORS_ORIGINS", "http://localhost:5173")
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


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
    allow_origins=_cors_origins(),
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
