import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.constants.app_meta import (
    APP_DESCRIPTION,
    APP_TITLE,
    APP_VERSION,
    HEALTH_STATUS_KEY,
    HEALTH_STATUS,
    LOG_FORMAT,
    LOG_SHUTDOWN,
    LOG_STARTUP,
)
from app.constants.routes import HEALTH_PATH
from app.db.connection import close_pool, init_pool
from app.limiter import limiter
from app.routes import internal, ui, webhook
from app.services.auth import ensure_auth_schema_and_superuser
from app.services.message_buffer import message_burst_buffer

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(LOG_STARTUP)
    await init_pool()
    await ensure_auth_schema_and_superuser()
    yield
    await message_burst_buffer.shutdown(flush_pending=True)
    await close_pool()
    logger.info(LOG_SHUTDOWN)


app = FastAPI(
    title=APP_TITLE,
    description=(
        APP_DESCRIPTION
        + "\n\n"
        + "Documentacion por categorias:\n"
        + "- Webhook Publico: verificacion y recepcion de eventos de Meta.\n"
        + "- Autenticacion: login para obtener JWT.\n"
        + "- Operacion Interna: endpoints protegidos para sesiones, clasificacion, extraccion, handoff y respuestas.\n"
        + "- Reportes: consolidado de leads, ventas e interacciones."
    ),
    version=APP_VERSION,
    lifespan=lifespan,
    openapi_tags=[
        {
            "name": "Webhook Publico",
            "description": "Endpoints publicos para verificacion de suscripcion y recepcion de eventos entrantes de Meta.",
        },
        {
            "name": "Autenticacion",
            "description": "Endpoint para autenticar usuario operativo y obtener token JWT.",
        },
        {
            "name": "Operacion Interna",
            "description": "Endpoints protegidos con Bearer token para flujo operativo de leads.",
        },
        {
            "name": "Reportes",
            "description": "Consulta consolidada de conversion comercial e interacciones por conversacion.",
        },
    ],
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
_STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get(
    HEALTH_PATH,
    tags=["Webhook Publico"],
    summary="Health check del servicio",
    description=(
        "Funcionalidad:\n"
        "Verifica que la API este disponible para recibir trafico.\n\n"
        "Funcionamiento:\n"
        "- Endpoint publico sin autenticacion.\n"
        "- Devuelve status=ok cuando el proceso esta saludable."
    ),
)
async def health():
    return {HEALTH_STATUS_KEY: HEALTH_STATUS}


app.include_router(webhook.router)
app.include_router(internal.router)
app.include_router(ui.router)
