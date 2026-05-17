import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.db.connection import close_pool, init_pool
from app.limiter import limiter
from app.routes import internal, webhook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Nopal Leads API")
    await init_pool()
    yield
    await close_pool()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Nopal Leads API",
    description="Captura y clasificación de leads desde redes sociales (Meta)",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.get("/health")
async def health():
    return {"status": "ok"}


app.include_router(webhook.router)
app.include_router(internal.router)
