"""Private HTTP API for the standalone embedding service."""

from __future__ import annotations

import os
import secrets
from typing import Literal, Protocol

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .engine import EmbeddingError, OllamaEmbeddingEngine


class Embedder(Protocol):
    model: str
    dimensions: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class EmbeddingRequest(BaseModel):
    input: list[str] = Field(min_length=1, max_length=128)
    input_type: Literal["document", "query"]


class EmbeddingResponse(BaseModel):
    model: str
    dimensions: int
    embeddings: list[list[float]]


def create_app(
    *, embedder: Embedder | None = None, service_token: str | None = None
) -> FastAPI:
    engine = embedder or OllamaEmbeddingEngine()
    expected_token = (
        service_token
        if service_token is not None
        else os.getenv("EMBEDDING_SERVICE_TOKEN")
    )
    api = FastAPI(
        title="Headspace Embedding Service",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @api.get("/health", tags=["system"])
    async def health() -> dict[str, str | int]:
        return {
            "status": "ok",
            "model": engine.model,
            "dimensions": engine.dimensions,
        }

    @api.post("/v1/embeddings", response_model=EmbeddingResponse)
    async def embeddings(
        request: EmbeddingRequest,
        authorization: str | None = Header(default=None),
    ) -> EmbeddingResponse:
        if expected_token:
            supplied_token = ""
            if authorization and authorization.startswith("Bearer "):
                supplied_token = authorization.removeprefix("Bearer ")
            if not secrets.compare_digest(supplied_token, expected_token):
                raise HTTPException(status_code=401, detail="Invalid service token")

        if any(not value.strip() for value in request.input):
            raise HTTPException(
                status_code=422,
                detail="Embedding input must contain non-blank strings",
            )
        try:
            if request.input_type == "document":
                vectors = await engine.embed_documents(request.input)
            elif len(request.input) == 1:
                vectors = [await engine.embed_query(request.input[0])]
            else:
                raise HTTPException(
                    status_code=422,
                    detail="Query embedding requests accept exactly one input",
                )
        except EmbeddingError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        return EmbeddingResponse(
            model=engine.model,
            dimensions=engine.dimensions,
            embeddings=vectors,
        )

    return api


