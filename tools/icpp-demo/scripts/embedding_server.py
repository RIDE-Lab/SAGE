"""OpenAI-compatible CPU embedding service for the ICPP demo."""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from sentence_transformers import SentenceTransformer

MODEL_ID = os.getenv("SAGE_LOCAL_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
MODEL = SentenceTransformer(MODEL_ID, device="cpu")
app = FastAPI(title="SAGE ICPP CPU Embedding")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "model": MODEL_ID}


@app.get("/v1/models")
def models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]}


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> dict[str, Any]:
    payload = await request.json()
    inputs = payload.get("input")
    if isinstance(inputs, str):
        texts = [inputs]
    elif isinstance(inputs, list) and inputs and all(isinstance(item, str) for item in inputs):
        texts = inputs
    else:
        raise HTTPException(400, "input must be a non-empty string or list of strings")
    if len(texts) > 128 or sum(len(text) for text in texts) > 1_000_000:
        raise HTTPException(413, "embedding request is too large")
    vectors = MODEL.encode(
        texts,
        batch_size=min(16, len(texts)),
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return {
        "object": "list",
        "model": MODEL_ID,
        "data": [
            {"object": "embedding", "index": index, "embedding": vector.tolist()}
            for index, vector in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }
