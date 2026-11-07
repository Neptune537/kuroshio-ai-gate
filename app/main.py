"""
Kuroshio Gyre AI Model Revolver Gateway (Render Cloud Service)
Complies with AGENTS.md § 1.4: Free Model Revolver Engine Standard.
Rotates through free model endpoints (Groq, OpenRouter, Gemini Free Tier)
with cooldowns, Price Guard sanitization, and fallback routing.
"""

import os
import time
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx

app = FastAPI(
    title="Kuroshio Gyre AI Revolver Gateway",
    version="1.0.0",
    description="Free Model Revolver Engine with Rate-Limit Tracking and Gemini Free Tier Fallback"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 1024
    caller_id: Optional[str] = "anonymous"

class ChamberStatus(BaseModel):
    provider: str
    model: str
    cooldown_until: float
    total_calls: int
    tripped_count: int

CHAMBERS = [
    {
        "provider": "groq",
        "model": "llama-3.3-70b-versatile",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "env_key": "GROQ_API_KEY",
        "cooldown_until": 0.0,
        "total_calls": 0,
        "tripped_count": 0,
    },
    {
        "provider": "openrouter",
        "model": "deepseek/deepseek-r1:free",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "env_key": "OPENROUTER_API_KEY",
        "cooldown_until": 0.0,
        "total_calls": 0,
        "tripped_count": 0,
    },
    {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "env_key": "GEMINI_API_KEY",
        "cooldown_until": 0.0,
        "total_calls": 0,
        "tripped_count": 0,
    }
]

current_chamber_index = 0

def check_price_guard(prompt: str) -> None:
    forbidden_terms = ["fake_checkout", "alter_price", "cart_bypass", "override_catalog_price"]
    lower = prompt.lower()
    for term in forbidden_terms:
        if term in lower:
            raise HTTPException(status_code=403, detail=f"Price Guard rejection: illegal token '{term}' detected.")

@app.get("/")
def read_root():
    return {
        "service": "Kuroshio AI Gate Revolver Gateway",
        "status": "online",
        "chambers_available": len(CHAMBERS)
    }

@app.get("/health")
def health_check():
    now = time.time()
    return {
        "status": "healthy",
        "timestamp": now,
        "chambers": [
            {
                "provider": c["provider"],
                "model": c["model"],
                "ready": now >= c["cooldown_until"],
                "tripped_count": c["tripped_count"]
            }
            for c in CHAMBERS
        ]
    }

@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    global current_chamber_index
    full_prompt = " ".join(m.content for m in req.messages)
    check_price_guard(full_prompt)

    now = time.time()
    num_chambers = len(CHAMBERS)

    for i in range(num_chambers):
        idx = (current_chamber_index + i) % num_chambers
        chamber = CHAMBERS[idx]

        if now < chamber["cooldown_until"]:
            continue

        api_key = os.getenv(chamber["env_key"])
        if not api_key:
            continue

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
                if chamber["provider"] == "openrouter":
                    headers["HTTP-Referer"] = "https://kuroshio-gyre.lemuria"
                    headers["X-Title"] = "Kuroshio AI Gate"

                payload = {
                    "model": chamber["model"],
                    "messages": [{"role": m.role, "content": m.content} for m in req.messages],
                    "temperature": req.temperature,
                    "max_tokens": req.max_tokens
                }

                res = await client.post(chamber["url"], headers=headers, json=payload)

                if res.status_code == 429:
                    chamber["cooldown_until"] = time.time() + 60.0
                    chamber["tripped_count"] += 1
                    continue

                res.raise_for_status()
                data = res.json()

                current_chamber_index = (idx + 1) % num_chambers
                chamber["total_calls"] += 1

                return {
                    "id": data.get("id", f"ai-gate-{int(now)}"),
                    "provider": chamber["provider"],
                    "model": chamber["model"],
                    "choices": data.get("choices", []),
                    "usage": data.get("usage", {})
                }

        except Exception as e:
            chamber["cooldown_until"] = time.time() + 15.0
            chamber["tripped_count"] += 1
            continue

    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {
                    "Authorization": f"Bearer {gemini_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": "gemini-2.5-flash",
                    "messages": [{"role": m.role, "content": m.content} for m in req.messages],
                    "temperature": req.temperature,
                }
                res = await client.post(
                    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                    headers=headers,
                    json=payload
                )
                res.raise_for_status()
                data = res.json()
                return {
                    "id": data.get("id", f"ai-gate-gemini-{int(now)}"),
                    "provider": "gemini-fallback",
                    "model": "gemini-2.5-flash",
                    "choices": data.get("choices", []),
                    "usage": data.get("usage", {})
                }
        except Exception as fallback_err:
            raise HTTPException(status_code=503, detail=f"All chambers and Gemini fallback failed: {str(fallback_err)}")

    raise HTTPException(status_code=503, detail="All AI revolver chambers are cooling down and no fallback API key is configured.")
