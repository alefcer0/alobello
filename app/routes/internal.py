import logging
from datetime import UTC
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from app.constants.routes import (
    AUTH_LOGIN_PATH,
    CLASSIFY_PATH,
    EXTRACT_PATH,
    HANDOFF_PATH,
    INTERNAL_TAG,
    LOG_GET_REPORT_LEADS,
    LOG_GET_REPORT_LEADS_EXPORT_CLAUDE,
    LOG_POST_AUTH_LOGIN_TEMPLATE,
    LOG_POST_CLASSIFY,
    LOG_POST_EXTRACT,
    LOG_POST_HANDOFF_TEMPLATE,
    LOG_POST_RESPOND_TEMPLATE,
    LOG_POST_SESSION_TEMPLATE,
    REPORT_LEADS_EXPORT_CLAUDE_PATH,
    REPORT_LEADS_PATH,
    RESPOND_PATH,
    SESSION_PATH,
)
from app.models.handoff import HandoffRequest, HandoffResponse
from app.models.auth import AuthLoginRequest, AuthTokenResponse, AuthenticatedUser
from app.models.lead import (
    ClassifyRequest,
    ClassifyResponse,
    ExtractRequest,
    ExtractResponse,
    RespondRequest,
    RespondResponse,
)
from app.models.report import LeadsReportResponse
from app.models.session import SessionCreate, SessionResponse
from app.services.classifier import classify_text
from app.services.extractor import extract_data
from app.services.auth import authenticate_user, create_access_token, get_current_user
from app.services.reports import build_leads_report
from app.services.responder import generate_and_send_response
from app.services.session_manager import get_or_create_session, set_handoff_state

logger = logging.getLogger(__name__)
router = APIRouter()


def _claude_export_payload(report: LeadsReportResponse) -> dict:
    conversations = []
    for item in report.results:
        conversations.append(
            {
                "conversation_id": str(item.conversation_id),
                "sender_id": item.sender_id,
                "plataforma": item.plataforma,
                "lead_outcome": item.lead_outcome,
                "is_nopal_sale": item.is_nopal_sale,
                "sale_data_status": item.sale_data_status,
                "missing_sale_fields": item.missing_sale_fields,
                "contacto": {
                    "nombre": item.nombre,
                    "telefono": item.telefono,
                },
                "topic": {
                    "last_intent_category": item.last_intent_category,
                    "last_detected_topic": item.last_detected_topic,
                },
                "flow": {
                    "clarification_attempts": item.clarification_attempts,
                    "topic_change_count": item.topic_change_count,
                    "interaction_closed": item.interaction_closed,
                },
                "messages": [
                    {
                        "created_at": interaction.created_at.isoformat(),
                        "direction": interaction.direction,
                        "categoria": interaction.categoria,
                        "intent_category": interaction.intent_category,
                        "detected_topic": interaction.detected_topic,
                        "classified_by_openai": interaction.classified_by_openai,
                        "texto": interaction.texto,
                    }
                    for interaction in item.interactions
                ],
            }
        )

    return {
        "purpose": "Analisis externo en Claude para mejorar comportamiento del bot",
        "generated_at": datetime.now(UTC).isoformat(),
        "filters": report.filters.model_dump(mode="json"),
        "summary": report.summary.model_dump(mode="json"),
        "conversations": conversations,
    }


@router.post(
    AUTH_LOGIN_PATH,
    response_model=AuthTokenResponse,
    tags=["Autenticacion"],
    summary="Iniciar sesion y obtener JWT",
    description=(
        "Funcionalidad:\n"
        "Autentica un usuario operativo y devuelve un token JWT para consumir endpoints protegidos.\n\n"
        "Funcionamiento:\n"
        "- Valida usuario y contrasena contra auth_users.\n"
        "- Si es valido, emite un access_token con expiracion.\n"
        "- El token debe enviarse como Authorization: Bearer <token>."
    ),
)
async def auth_login_endpoint(body: AuthLoginRequest):
    logger.info(LOG_POST_AUTH_LOGIN_TEMPLATE, body.username)
    user = await authenticate_user(body.username, body.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    access_token, expires_at, expires_in_seconds = create_access_token(user)
    return AuthTokenResponse(
        access_token=access_token,
        expires_at=expires_at,
        expires_in_seconds=expires_in_seconds,
        username=user.username,
        is_superuser=user.is_superuser,
    )


@router.post(
    CLASSIFY_PATH,
    response_model=ClassifyResponse,
    tags=["Operacion Interna"],
    summary="Clasificar intencion del mensaje",
    description=(
        "Funcionalidad:\n"
        "Clasifica texto entrante en categorias operativas como lead, proveedor, saludo o spam.\n\n"
        "Funcionamiento:\n"
        "- Requiere JWT Bearer valido.\n"
        "- Analiza el texto y determina la categoria para enrutar el flujo."
    ),
)
async def classify_endpoint(
    body: ClassifyRequest,
    _: AuthenticatedUser = Depends(get_current_user),
):
    logger.info(LOG_POST_CLASSIFY)
    return await classify_text(body.texto)


@router.post(
    EXTRACT_PATH,
    response_model=ExtractResponse,
    tags=["Operacion Interna"],
    summary="Extraer datos comerciales del texto",
    description=(
        "Funcionalidad:\n"
        "Extrae nombre, telefono, cantidad y ciudad desde un mensaje.\n\n"
        "Funcionamiento:\n"
        "- Requiere JWT Bearer valido.\n"
        "- Ejecuta extraccion sin modificar la base de datos.\n"
        "- Campos no detectados se devuelven como null."
    ),
)
async def extract_endpoint(
    body: ExtractRequest,
    _: AuthenticatedUser = Depends(get_current_user),
):
    logger.info(LOG_POST_EXTRACT)
    return extract_data(body.texto)


@router.post(
    RESPOND_PATH,
    response_model=RespondResponse,
    tags=["Operacion Interna"],
    summary="Generar y enviar respuesta al contacto",
    description=(
        "Funcionalidad:\n"
        "Construye respuesta segun contexto de conversacion y datos faltantes del lead.\n\n"
        "Funcionamiento:\n"
        "- Requiere JWT Bearer valido.\n"
        "- Usa session_id o conversation_id para recuperar contexto.\n"
        "- Si se envia texto manual, prioriza ese contenido."
    ),
)
async def respond_endpoint(
    body: RespondRequest,
    _: AuthenticatedUser = Depends(get_current_user),
):
    logger.info(LOG_POST_RESPOND_TEMPLATE, body.sender_id)
    return await generate_and_send_response(
        sender_id=body.sender_id,
        plataforma=body.plataforma,
        conversation_id=body.context_id,
        texto_override=body.texto,
    )


@router.post(
    SESSION_PATH,
    response_model=SessionResponse,
    tags=["Operacion Interna"],
    summary="Crear o recuperar sesion activa",
    description=(
        "Funcionalidad:\n"
        "Abre o recupera la sesion activa de un contacto por sender_id y plataforma.\n\n"
        "Funcionamiento:\n"
        "- Requiere JWT Bearer valido.\n"
        "- Si existe sesion vigente, retorna la existente.\n"
        "- Si no existe, crea una nueva sesion y marca created=true."
    ),
)
async def session_endpoint(
    body: SessionCreate,
    _: AuthenticatedUser = Depends(get_current_user),
):
    logger.info(LOG_POST_SESSION_TEMPLATE, body.sender_id)
    return await get_or_create_session(body.sender_id, body.plataforma)


@router.post(
    HANDOFF_PATH,
    response_model=HandoffResponse,
    tags=["Operacion Interna"],
    summary="Activar o liberar handoff humano",
    description=(
        "Funcionalidad:\n"
        "Controla cuando una conversacion pasa a atencion humana o vuelve a modo automatico.\n\n"
        "Funcionamiento:\n"
        "- Requiere JWT Bearer valido.\n"
        "- locked=true bloquea respuestas automaticas.\n"
        "- locked=false restablece operacion automatica."
    ),
)
async def handoff_endpoint(
    body: HandoffRequest,
    _: AuthenticatedUser = Depends(get_current_user),
):
    logger.info(LOG_POST_HANDOFF_TEMPLATE, body.sender_id, body.locked)
    conversation_id, handoff_state, handoff_until = await set_handoff_state(
        body.sender_id,
        body.plataforma,
        body.locked,
        reason=body.reason,
    )
    return HandoffResponse(
        sender_id=body.sender_id,
        plataforma=body.plataforma,
        conversation_id=conversation_id,
        handoff_state=handoff_state,
        handoff_until=handoff_until,
    )


@router.get(
    REPORT_LEADS_PATH,
    response_model=LeadsReportResponse,
    tags=["Reportes"],
    summary="Reporte de leads, ventas e interacciones",
    description=(
        "Funcionalidad:\n"
        "Entrega un consolidado comercial y operativo por conversacion: ventas, leads caidos, descartados y activos.\n\n"
        "Funcionamiento:\n"
        "- Requiere JWT Bearer valido.\n"
        "- Aplica filtros por fecha, plataforma, sender, estado comercial, categoria y handoff.\n"
        "- Puede incluir interacciones detalladas por conversacion."
    ),
)
async def leads_report_endpoint(
    from_date: Optional[datetime] = Query(default=None, description="Inicio del rango (ISO 8601)."),
    to_date: Optional[datetime] = Query(default=None, description="Fin del rango (ISO 8601)."),
    plataforma: Optional[str] = Query(default=None, description="Canal: facebook o whatsapp."),
    sender_id: Optional[str] = Query(default=None, description="Filtra por sender_id exacto."),
    sender: Optional[str] = Query(
        default=None,
        description="Filtra por coincidencia parcial de sender_id.",
    ),
    topic: Optional[str] = Query(
        default=None,
        description="Filtra por coincidencia parcial en tema detectado.",
    ),
    search: Optional[str] = Query(
        default=None,
        description="Busqueda libre en sender_id, nombre, telefono, ciudad o tema detectado.",
    ),
    order_status: Optional[str] = Query(
        default=None,
        description="Estado de orden: draft, collecting, ready_to_confirm, confirmed, cancelled, closed, none.",
    ),
    categoria: Optional[str] = Query(
        default=None,
        description="Categoria de mensajes: lead, proveedor, saludo, consulta, spam, auto_reply, human_reply.",
    ),
    handoff_state: Optional[str] = Query(
        default=None,
        description="Estado de handoff: auto o human_locked.",
    ),
    only_nopal_sales: bool = Query(
        default=False,
        description="Si true, retorna solo sesiones asociadas a venta de nopal.",
    ),
    sale_data_status: Optional[str] = Query(
        default=None,
        description="Estado de datos para venta: completos, faltantes o no_aplica.",
    ),
    lead_outcome: str = Query(
        default="all",
        description="Resultado comercial: all, sale, fallen, discarded, active, unknown.",
    ),
    include_interactions: bool = Query(
        default=True,
        description="Si true, incluye el detalle de mensajes por conversacion.",
    ),
    max_interactions: int = Query(
        default=30,
        ge=1,
        le=200,
        description="Maximo de interacciones por conversacion en results[].",
    ),
    limit: int = Query(default=50, ge=1, le=200, description="Tamano de pagina."),
    offset: int = Query(default=0, ge=0, description="Desplazamiento para paginacion."),
    _: AuthenticatedUser = Depends(get_current_user),
):
    logger.info(LOG_GET_REPORT_LEADS)
    try:
        return await build_leads_report(
            from_date=from_date,
            to_date=to_date,
            plataforma=plataforma,
            sender_id=sender_id,
            sender=sender,
            topic=topic,
            search=search,
            order_status=order_status,
            categoria=categoria,
            handoff_state=handoff_state,
            only_nopal_sales=only_nopal_sales,
            sale_data_status=sale_data_status,
            lead_outcome=lead_outcome,
            include_interactions=include_interactions,
            max_interactions=max_interactions,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get(
    REPORT_LEADS_EXPORT_CLAUDE_PATH,
    tags=["Reportes"],
    summary="Descargar conversaciones filtradas para analisis en Claude",
    description=(
        "Funcionalidad:\n"
        "Genera un archivo JSON descargable con conversaciones filtradas y mensajes para analisis en Claude.\n\n"
        "Funcionamiento:\n"
        "- Requiere JWT Bearer valido.\n"
        "- Reutiliza las mismas reglas de filtrado del reporte principal.\n"
        "- Incluye interacciones por conversacion para inspeccion de comportamiento."
    ),
)
async def leads_report_export_claude_endpoint(
    from_date: Optional[datetime] = Query(default=None, description="Inicio del rango (ISO 8601)."),
    to_date: Optional[datetime] = Query(default=None, description="Fin del rango (ISO 8601)."),
    plataforma: Optional[str] = Query(default=None, description="Canal: facebook o whatsapp."),
    sender_id: Optional[str] = Query(default=None, description="Filtra por sender_id exacto."),
    sender: Optional[str] = Query(
        default=None,
        description="Filtra por coincidencia parcial de sender_id.",
    ),
    topic: Optional[str] = Query(
        default=None,
        description="Filtra por coincidencia parcial en tema detectado.",
    ),
    search: Optional[str] = Query(
        default=None,
        description="Busqueda libre en sender_id, nombre, telefono, ciudad o tema detectado.",
    ),
    order_status: Optional[str] = Query(
        default=None,
        description="Estado de orden: draft, collecting, ready_to_confirm, confirmed, cancelled, closed, none.",
    ),
    categoria: Optional[str] = Query(
        default=None,
        description="Categoria de mensajes: lead, proveedor, saludo, consulta, spam, auto_reply, human_reply.",
    ),
    handoff_state: Optional[str] = Query(
        default=None,
        description="Estado de handoff: auto o human_locked.",
    ),
    only_nopal_sales: bool = Query(
        default=False,
        description="Si true, retorna solo sesiones asociadas a venta de nopal.",
    ),
    sale_data_status: Optional[str] = Query(
        default=None,
        description="Estado de datos para venta: completos, faltantes o no_aplica.",
    ),
    lead_outcome: str = Query(
        default="all",
        description="Resultado comercial: all, sale, fallen, discarded, active, unknown.",
    ),
    max_interactions: int = Query(
        default=80,
        ge=1,
        le=300,
        description="Maximo de interacciones por conversacion incluidas en el archivo.",
    ),
    max_conversations: int = Query(
        default=500,
        ge=1,
        le=2000,
        description="Maximo de conversaciones incluidas en el archivo.",
    ),
    _: AuthenticatedUser = Depends(get_current_user),
):
    logger.info(LOG_GET_REPORT_LEADS_EXPORT_CLAUDE)
    try:
        report = await build_leads_report(
            from_date=from_date,
            to_date=to_date,
            plataforma=plataforma,
            sender_id=sender_id,
            sender=sender,
            topic=topic,
            search=search,
            order_status=order_status,
            categoria=categoria,
            handoff_state=handoff_state,
            only_nopal_sales=only_nopal_sales,
            sale_data_status=sale_data_status,
            lead_outcome=lead_outcome,
            include_interactions=True,
            max_interactions=max_interactions,
            limit=max_conversations,
            offset=0,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    payload = _claude_export_payload(report)
    now_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"conversaciones_claude_{now_tag}.json"
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
