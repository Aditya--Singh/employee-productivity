"""FastAPI entry point for the PNB Employee Productivity Tool."""

import asyncio
import contextlib
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.ollama import OllamaClient, OllamaError
from app.tabs.document_assistant.file_store import run_cleanup_loop
from app.tabs.document_assistant.router import router as document_assistant_router


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    cleanup = asyncio.create_task(run_cleanup_loop())
    try:
        yield
    finally:
        cleanup.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup


app = FastAPI(title="PNB Employee Productivity Tool", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _languagetool_reachable(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{url.rstrip('/')}/v2/languages")
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


async def health() -> dict:
    settings = get_settings()
    ollama = OllamaClient()
    try:
        models = await ollama.list_models(timeout=3.0)
        reachable = True
    except OllamaError:
        models, reachable = [], False
    return {
        "status": "ok",
        "ollama": {
            "reachable": reachable,
            "model": ollama.model,
            "model_available": ollama.model in models,
        },
        "languagetool": {
            "reachable": await _languagetool_reachable(settings.languagetool_url)
        },
    }


app.add_api_route("/health", health, methods=["GET"])

api = APIRouter(prefix="/api")
api.add_api_route("/health", health, methods=["GET"])
app.include_router(api)
app.include_router(document_assistant_router)
