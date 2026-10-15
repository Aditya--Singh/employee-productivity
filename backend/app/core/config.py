"""Application settings, read from environment variables with local defaults."""

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gpt-oss:20b"
    ollama_num_ctx: int = 32768
    languagetool_url: str = "http://localhost:8120"
    languagetool_language: str = "en-GB"
    max_upload_mb: int = 20
    frontend_origin: str = "http://localhost:5173"
    data_dir: str = str(BACKEND_DIR / "data")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def get_settings() -> Settings:
    return Settings(
        ollama_url=os.getenv("OLLAMA_URL", Settings.ollama_url),
        ollama_model=os.getenv("OLLAMA_MODEL", Settings.ollama_model),
        ollama_num_ctx=int(os.getenv("OLLAMA_NUM_CTX", Settings.ollama_num_ctx)),
        languagetool_url=os.getenv("LANGUAGETOOL_URL", Settings.languagetool_url),
        languagetool_language=os.getenv(
            "LANGUAGETOOL_LANGUAGE", Settings.languagetool_language
        ),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", Settings.max_upload_mb)),
        frontend_origin=os.getenv("FRONTEND_ORIGIN", Settings.frontend_origin),
        data_dir=os.getenv("DATA_DIR", Settings.data_dir),
    )
