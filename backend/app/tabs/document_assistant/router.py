"""HTTP routes for the Document Assistant tab (phase 2: upload and Summarize)."""

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.ollama import OllamaClient, OllamaContextError, OllamaError
from app.core.uploads import SUPPORTED_TYPES, ValidatedFile, validate_upload

from .extraction import ExtractionError, Extracted, extract, sheet_preview
from .file_store import FileExpired, FileStore, get_store
from .summarize import (
    Event,
    SummaryTooLong,
    check_length,
    summarize_data,
    summarize_text,
)

router = APIRouter(prefix="/api/document-assistant", tags=["document-assistant"])
log = logging.getLogger(__name__)

LLM_UNAVAILABLE = (
    "The language model is not available right now. Please check that Ollama is running "
    "and try again."
)
TOO_LONG = "This document is too long to process in one go. Please shorten it."


def get_ollama() -> OllamaClient:
    return OllamaClient()


def _extract_or_raise(file: ValidatedFile) -> Extracted:
    try:
        return extract(file)
    except ExtractionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


def _load(store: FileStore, file_id: str) -> Extracted:
    try:
        stored = store.get(file_id)
    except FileExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from None
    return _extract_or_raise(
        ValidatedFile(stored.filename, stored.ext, stored.read_bytes())
    )


@router.post("/extract")
async def extract_upload(
    file: UploadFile = File(...), store: FileStore = Depends(get_store)
) -> dict:
    validated = await validate_upload(file, allowed=SUPPORTED_TYPES)
    result = _extract_or_raise(validated)
    stored = store.save(validated.filename, validated.content)
    body: dict = {
        "file_id": stored.id,
        "filename": stored.filename,
        "kind": result.kind,
        "notices": result.notices,
        "expires_at": stored.expires_at,
    }
    if result.kind == "text":
        body.update(text=result.text, word_count=result.word_count)
    else:
        body.update(
            row_count=result.row_count,
            sheets=[sheet_preview(s) for s in result.sheets],
        )
    return body


class SummarizeRequest(BaseModel):
    text: str | None = None
    file_id: str | None = None


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _event_stream(events: AsyncIterator[Event]) -> AsyncIterator[str]:
    """Turn summary events into SSE frames. Errors become a final "error" event."""
    try:
        async for event in events:
            yield _sse({"type": event.type, "text": event.text})
    except OllamaContextError:
        yield _sse({"type": "error", "code": "too_long", "message": TOO_LONG})
        return
    except OllamaError as exc:
        log.warning("Summarize failed: %s", exc)
        yield _sse(
            {"type": "error", "code": "llm_unavailable", "message": LLM_UNAVAILABLE}
        )
        return
    yield _sse({"type": "done"})


@router.post("/summarize")
async def summarize(
    req: SummarizeRequest,
    store: FileStore = Depends(get_store),
    client: OllamaClient = Depends(get_ollama),
) -> StreamingResponse:
    if (req.text is None) == (req.file_id is None):
        raise HTTPException(status_code=400, detail="Send either text or a file id.")

    if req.file_id is not None:
        extracted = _load(store, req.file_id)
    else:
        if not req.text.strip():
            raise HTTPException(status_code=400, detail="Please enter some text.")
        extracted = Extracted("text", text=req.text)

    if extracted.kind == "data":
        events = summarize_data(extracted.sheets, client)
    else:
        try:
            check_length(extracted.text)
        except SummaryTooLong as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from None
        events = summarize_text(extracted.text, client)

    # Closing the response (the user pressed Stop) closes these generators, which closes
    # the HTTP stream to Ollama and stops generation.
    return StreamingResponse(
        _event_stream(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/files/{file_id}", status_code=204)
async def delete_file(file_id: str, store: FileStore = Depends(get_store)) -> Response:
    store.delete(file_id)
    return Response(status_code=204)
