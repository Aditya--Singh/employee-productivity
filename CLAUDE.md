# PNB Employee Productivity Tool

Internal web app for Punjab National Bank. Three tabs, built one at a time.
Specs per tab live in docs/specs/. Read only the spec for the tab being worked on.

## Stack
- Backend: Python 3.12, FastAPI, managed with uv. Run: `cd backend && uv run uvicorn app.main:app --reload --port 8100`
- Frontend: React + Vite + TypeScript + Tailwind. Run: `cd frontend && npm run dev` (port 5173)
- LLM: local Ollama at http://localhost:11434, model gpt-oss:20b (default; override with the OLLAMA_MODEL env var)
- Grammar: LanguageTool at http://localhost:8120 (Docker)
- Tests: pytest (backend), run `cd backend && uv run pytest`

## Rules
- No data may leave this machine. Never call external APIs or CDNs at runtime.
- Never read anything in private/. Use samples/ for testing.
- Each tab is self-contained: backend/app/tabs/<tab>/ and frontend/src/tabs/<tab>/.
  Do not modify another tab's folder unless asked. Shared code goes in core/.
- Branding: PNB maroon (#A6192E) and gold (#F2A900), clean, lots of white space.
- Write tests for every backend feature and run them before saying a task is done.
- Keep changes small. Ask before adding a new library.