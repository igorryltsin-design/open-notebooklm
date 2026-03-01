"""FastAPI application entry point."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import CONFIG_YAML_PATH, get_vision_ingest_settings
from app.routers.api import router as api_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Open NotebookLM",
    version="0.1.0",
    description="Local-first research assistant: summaries, podcast scripts and audio generation.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.on_event("startup")
def _log_config():
    vision = get_vision_ingest_settings()
    logger.info(
        "Config: %s | Vision ingest: %s (base_url=%s)",
        CONFIG_YAML_PATH,
        "on" if vision.get("enabled") else "off",
        vision.get("base_url", ""),
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
