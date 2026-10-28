"""Run the embeddings API directly for local development."""

from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "services.embeddings.api:create_app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8001")),
        factory=True,
    )
