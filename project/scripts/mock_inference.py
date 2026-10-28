"""Local stand-in for an OpenAI-compatible inference endpoint, for demos without an API key.

    uvicorn --app-dir scripts mock_inference:app --port 9000
    INFERENCE_BASE_URL=http://127.0.0.1:9000/v1 uvicorn --app-dir src --factory batch_engine.api:create_app

Failure rates and latency are configurable:
    MOCK_429_RATE (default 0.10), MOCK_500_RATE (0.02), MOCK_RETRY_AFTER_S (1),
    MOCK_LATENCY_MS_MIN (50), MOCK_LATENCY_MS_MAX (400)
"""

import asyncio
import os
import random

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

RATE_429 = float(os.environ.get("MOCK_429_RATE", "0.10"))
RATE_500 = float(os.environ.get("MOCK_500_RATE", "0.02"))
RETRY_AFTER = os.environ.get("MOCK_RETRY_AFTER_S", "1")
LATENCY_MS = (int(os.environ.get("MOCK_LATENCY_MS_MIN", "50")), int(os.environ.get("MOCK_LATENCY_MS_MAX", "400")))

app = FastAPI(title="Mock inference endpoint")


@app.get("/v1/models")
async def models() -> dict:
    return {"object": "list", "data": [{"id": "llama3-8b-instruct", "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    await asyncio.sleep(random.uniform(*LATENCY_MS) / 1000)
    roll = random.random()
    if roll < RATE_429:
        return JSONResponse({"error": {"message": "rate limited"}}, status_code=429, headers={"Retry-After": RETRY_AFTER})
    if roll < RATE_429 + RATE_500:
        return JSONResponse({"error": {"message": "internal error"}}, status_code=500)
    prompt = body["messages"][-1]["content"]
    return {
        "id": "mock-completion",
        "object": "chat.completion",
        "model": body.get("model"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": f"[mock] reply to: {prompt[:80]}"},
                     "finish_reason": "stop"}],
    }
