"""
Cloud Agent Platform - Main Application Entry Point

A platform for running autonomous AI agents in isolated cloud environments.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from app.config import settings
from app.core.database import init_db
from app.routers import tasks, ws

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logging.info("Cloud Agent Platform started")
    yield
    logging.info("Cloud Agent Platform shutting down")


app = FastAPI(
    title="Cloud Agent Platform",
    description="Submit natural language tasks, executed by autonomous agents in isolated sandboxes.",
    version="1.0.0",
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks.router)
app.include_router(ws.router)


@app.get("/health")
async def health():
    return {"status": "ok", "environment": settings.environment}
