import logging
import re

from app.constants.common import (
    CATEGORY_AUTO_REPLY,
    CATEGORY_CONSULTA,
    CATEGORY_LEAD,
    CATEGORY_PROVEEDOR,
    CATEGORY_SALUDO,
    CATEGORY_SPAM,
    DATOS_EXTRAIDOS_PRIMER_MENSAJE,
    DATOS_EXTRAIDOS_RESPUESTA,
    DIRECTION_INBOUND,
    DIRECTION_OUTBOUND,
    ORDER_STATUS_READY_TO_CONFIRM,
)
from app.constants.pipeline import (
    LEAD_CHANGE_LABELS,
    LOG_PIPELINE_COMPLETE_INTENT_TEMPLATE,
    LOG_PIPELINE_COMPLETE_TEMPLATE,
    LOG_PROCESSING_MESSAGE_TEMPLATE,
    RESULT_KEY_CONVERSATION_ID,
    RESULT_KEY_EXTRAIDO,
    RESULT_KEY_LEAD_ID,
    RESULT_KEY_MESSAGE_ID,
    RESULT_KEY_ORDER_ID,
    RESULT_KEY_RESPUESTA,
    RESULT_KEY_RESPUESTA_ENVIADA,
    RESULT_KEY_SESSION_ID,
    RESULT_KEY_TIPO,
)
from app.constants.handoff_runtime import LOG_HANDOFF_SKIP_RESPONSE_TEMPLATE
from app.constants.session_runtime import ORDER_INTENT_CONFIRMED
from app.config import settings
from app.services.classifier import (
    classify_by_rules,
    classify_order_intent_with_openai,
    classify_with_openai_result,
    infer_topic_with_openai,
)
from app.services.extractor import extract_data, extract_data_with_openai
from app.services.meta_webhook import IncomingMessage
from app.services.responder import (
    build_clarification_message,
    build_cancellation_message,
    build_confirmation_message,
    build_general_topic_response,
    build_greeting_message,
    build_human_contact_bridge_message,
    build_interaction_close_message,
    build_order_change_message,
    build_order_confirmation_prompt,
    build_repeated_greeting_message,
    generate_and_send_response,
)
from app.services.session_manager import (
    apply_customer_order_intent,
    close_interaction,
    get_conversation_flow_state,
    get_active_order_status,
    get_missing_contact_fields,
    get_missing_fields,
    get_session_data,
    get_or_create_session,
    increment_clarification_attempt,
    is_repeat_greeting,
    is_bot_handoff_locked,
    merge_contact_data,
    merge_extracted_data,
    reset_clarification_attempts,
    save_message,
    track_topic_change,
    upsert_order_for_lead,
)

logger = logging.getLogger(__name__)
_AMBIGUOUS_TOPICS = {"consulta general", "tu consulta"}
_PHONE_ONLY_REGEX = re.compile(r"^\+?\d[\d\s\-]{6,}$")
_NAME_FRAGMENT_REGEX = re.compile(r"^[A-Za-zÁÉÍÓÚáéíóúñÑ'\-]{2,}(?:\s+[A-Za-zÁÉÍÓÚáéíóúñÑ'\-]{2,}){0,2}$")
_HUMAN_LOOKUP_REGEX = re.compile(
    r"\b(?:est[aá]s?|anda|sigue)\s+ah[ií]\b|"
    r"\b(?:busco|hablar|contactar(?:me)?|comunicar(?:me)?|pasame|pásame)\b"
    r".*\b(?:dueñ[oa]|persona|humano|encargad[oa]|admin|administrador)\b",
    re.IGNORECASE,
)
_NON_NAME_TOKENS = {
    "ok",
    "hola",
    "gracias",
    "si",
    "sí",
    "no",
    "vale",
    "va",
    "listo",
    "perfecto",
    "bien",
}
_LEAD_TOPIC_HINTS = ("nopal", "nopales", "kilo", "kilos", "kg")


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _lead_change_labels(before, after, extracted) -> list[str]:
    changed: list[str] = []

    for field, label in LEAD_CHANGE_LABELS.items():
        incoming = _norm(getattr(extracted, field))
        prev = _norm(getattr(before, field))
        curr = _norm(getattr(after, field))
        if incoming and prev and curr and prev != curr:
            changed.append(label)

    return changed


def _fallback_topic(category: str) -> str:
    if category == CATEGORY_LEAD:
        return "compra de nopal"
    if category == CATEGORY_PROVEEDOR:
        return "oferta de proveedor"
    if category == CATEGORY_SALUDO:
        return "consulta general"
    return "tu consulta"


def _is_ambiguous_topic(topic: str | None) -> bool:
    return _norm(topic) in _AMBIGUOUS_TOPICS


def _needs_context_clarification(
    *,
    category: str,
    no_rule_match: bool,
    topic: str | None,
    text: str,
) -> bool:
    if category in {CATEGORY_LEAD, CATEGORY_SPAM}:
        return False

    if category == CATEGORY_SALUDO:
        return True

    if _is_ambiguous_topic(topic):
        return True

    short_message = len((_norm(text)).split()) <= 3
    if no_rule_match and short_message and category != CATEGORY_PROVEEDOR:
        return True

    return False


def _data_fields_from_extracted(extracted) -> list[str]:
    fields: list[str] = []
    if extracted.nombre:
        fields.append("nombre")
    if extracted.telefono:
        fields.append("telefono")
    if extracted.cantidad:
        fields.append("cantidad")
    if extracted.ciudad:
        fields.append("ciudad")
    return fields


def _topic_hints_lead(topic: str | None) -> bool:
    normalized = _norm(topic)
    return any(token in normalized for token in _LEAD_TOPIC_HINTS)


def _looks_like_name_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized or not _NAME_FRAGMENT_REGEX.fullmatch(normalized):
        return False

    words = normalized.split()
    if any(word in _NON_NAME_TOKENS for word in words):
        return False

    return True


def _title_case_fragment(text: str) -> str:
    compact = re.sub(r"\s+", " ", text).strip(" .,!?")
    return compact.title()


def _inferred_fragment_fields(text: str) -> set[str]:
    normalized = _norm(text)
    inferred: set[str] = set()

    if _PHONE_ONLY_REGEX.match(normalized):
        inferred.add("telefono")
    if _looks_like_name_fragment(text):
        inferred.add("nombre")

    return inferred


def _is_human_lookup_text(text: str) -> bool:
    return bool(_HUMAN_LOOKUP_REGEX.search(text or ""))


def _is_context_data_fragment(
    *,
    text: str,
    extracted,
    missing_fields: list[str],
    current_topic: str | None,
    is_first: bool,
    clarification_attempts: int,
) -> bool:
    if not missing_fields:
        return False

    provided_fields = set(_data_fields_from_extracted(extracted))
    provided_fields.update(_inferred_fragment_fields(text))
    if not provided_fields:
        return False

    contributes_missing = any(field in missing_fields for field in provided_fields)
    if not contributes_missing:
        return False

    normalized = _norm(text)
    words = normalized.split()
    looks_like_phone_only = bool(_PHONE_ONLY_REGEX.match(normalized))
    is_short_fragment = len(words) <= 6
    has_specific_topic = bool(current_topic) and not _is_ambiguous_topic(current_topic)
    has_active_context = has_specific_topic or clarification_attempts > 0 or not is_first

    if not has_active_context:
        return False

    return looks_like_phone_only or is_short_fragment


async def process_incoming_message(msg: IncomingMessage) -> dict:
    logger.info(LOG_PROCESSING_MESSAGE_TEMPLATE, msg.sender_id, msg.plataforma, msg.text[:100])

    session = await get_or_create_session(msg.sender_id, msg.plataforma)
    is_first = session.created
    bot_handoff_locked = await is_bot_handoff_locked(session.id)

    current_data = await get_session_data(session.id)
    flow_state = await get_conversation_flow_state(session.id)
    active_order_status = await get_active_order_status(session.id)

    intent_order_id, order_intent = await apply_customer_order_intent(session.id, msg.text)
    intent_classified_by_openai = False
    if not order_intent and active_order_status:
        ai_intent = await classify_order_intent_with_openai(msg.text, active_order_status)
        if ai_intent:
            intent_order_id, order_intent = await apply_customer_order_intent(
                session.id,
                msg.text,
                forced_intent=ai_intent,
                confirmed_by_openai=True,
            )
            intent_classified_by_openai = order_intent is not None
    if order_intent:
        intent_topic = "confirmacion de pedido" if order_intent == ORDER_INTENT_CONFIRMED else "cancelacion de pedido"
        _, topic_limit_exceeded = await track_topic_change(session.id, intent_topic)
        message_id = await save_message(
            session_id=session.id,
            plataforma=msg.plataforma,
            sender_id=msg.sender_id,
            direction=DIRECTION_INBOUND,
            texto=msg.text,
            categoria=CATEGORY_LEAD,
            intent_category=CATEGORY_LEAD,
            detected_topic=intent_topic,
            external_message_id=msg.event_id,
            order_id=intent_order_id,
            classified_by_openai=intent_classified_by_openai,
        )

        response_sent = False
        response_message = None
        response_classified_by_openai = False

        if bot_handoff_locked:
            logger.info(LOG_HANDOFF_SKIP_RESPONSE_TEMPLATE, session.id)
        elif topic_limit_exceeded:
            response_message = build_interaction_close_message()
            await close_interaction(session.id)
            resp = await generate_and_send_response(
                sender_id=msg.sender_id,
                plataforma=msg.plataforma,
                texto_override=response_message,
            )
            response_sent = resp.enviado
        elif order_intent == ORDER_INTENT_CONFIRMED:
            await reset_clarification_attempts(session.id)
            resp = await generate_and_send_response(
                sender_id=msg.sender_id,
                plataforma=msg.plataforma,
                texto_override=build_confirmation_message(),
            )
            response_sent = resp.enviado
            response_message = resp.mensaje
        else:
            await reset_clarification_attempts(session.id)
            resp = await generate_and_send_response(
                sender_id=msg.sender_id,
                plataforma=msg.plataforma,
                texto_override=build_cancellation_message(),
            )
            response_sent = resp.enviado
            response_message = resp.mensaje

        if response_message:
            await save_message(
                session_id=session.id,
                plataforma=msg.plataforma,
                sender_id=msg.sender_id,
                direction=DIRECTION_OUTBOUND,
                texto=response_message,
                categoria=CATEGORY_AUTO_REPLY,
                order_id=intent_order_id,
                classified_by_openai=response_classified_by_openai,
            )

        result = {
            RESULT_KEY_LEAD_ID: str(message_id),  # backward compatibility
            RESULT_KEY_MESSAGE_ID: str(message_id),
            RESULT_KEY_SESSION_ID: str(session.id),
            RESULT_KEY_CONVERSATION_ID: str(session.id),
            RESULT_KEY_ORDER_ID: str(intent_order_id) if intent_order_id else None,
            RESULT_KEY_TIPO: CATEGORY_LEAD,
            RESULT_KEY_EXTRAIDO: current_data.model_dump(),
            RESULT_KEY_RESPUESTA_ENVIADA: response_sent,
            RESULT_KEY_RESPUESTA: response_message,
        }
        logger.info(LOG_PIPELINE_COMPLETE_INTENT_TEMPLATE, result)
        return result

    rule_category = classify_by_rules(msg.text)
    no_rule_match = rule_category is None

    if no_rule_match:
        category, category_classified_by_openai = await classify_with_openai_result(msg.text)
    else:
        category = rule_category
        category_classified_by_openai = False

    missing_before = get_missing_fields(current_data)
    extracted = extract_data(msg.text)
    is_context_fragment = _is_context_data_fragment(
        text=msg.text,
        extracted=extracted,
        missing_fields=missing_before,
        current_topic=flow_state.current_topic,
        is_first=is_first,
        clarification_attempts=flow_state.clarification_attempts,
    )
    if is_context_fragment:
        if not extracted.nombre and "nombre" in missing_before and _looks_like_name_fragment(msg.text):
            extracted.nombre = _title_case_fragment(msg.text)

        provided_fields = set(_data_fields_from_extracted(extracted))
        provided_fields.update(_inferred_fragment_fields(msg.text))
        if "cantidad" in provided_fields or "ciudad" in provided_fields or _topic_hints_lead(flow_state.current_topic):
            category = CATEGORY_LEAD
        else:
            category = CATEGORY_CONSULTA
        category_classified_by_openai = False

    should_analyze_with_openai = category != CATEGORY_SPAM and not is_context_fragment
    should_merge_into_order = category == CATEGORY_LEAD
    should_merge_contact_only = category not in {CATEGORY_SPAM, CATEGORY_LEAD}
    previous_data = current_data if category == CATEGORY_LEAD else None
    topic = flow_state.current_topic if is_context_fragment else None

    if should_analyze_with_openai:
        extracted = await extract_data_with_openai(msg.text, extracted)
        topic = await infer_topic_with_openai(msg.text, category)
    topic = topic or _fallback_topic(category)
    _, topic_limit_exceeded = await track_topic_change(session.id, topic)

    if should_merge_into_order:
        merged = await merge_extracted_data(session.id, extracted, is_first)
    elif should_merge_contact_only:
        merged = await merge_contact_data(session.id, extracted)
    else:
        merged = current_data
    datos_en = DATOS_EXTRAIDOS_PRIMER_MENSAJE if is_first else DATOS_EXTRAIDOS_RESPUESTA

    order_id = await upsert_order_for_lead(
        session_id=session.id,
        plataforma=msg.plataforma,
        tipo=category,
        nombre=merged.nombre or msg.nombre,
        telefono=merged.telefono,
        cantidad=merged.cantidad,
        ciudad=merged.ciudad,
        datos_extraidos_en=datos_en,
    )

    lead_changed_fields = []
    if category == CATEGORY_LEAD and previous_data:
        lead_changed_fields = _lead_change_labels(previous_data, merged, extracted)

    message_id = await save_message(
        session_id=session.id,
        plataforma=msg.plataforma,
        sender_id=msg.sender_id,
        direction=DIRECTION_INBOUND,
        texto=msg.text,
        categoria=category,
        intent_category=category,
        detected_topic=topic,
        external_message_id=msg.event_id,
        order_id=order_id,
        classified_by_openai=category_classified_by_openai,
    )

    response_sent = False
    response_message = None
    response_classified_by_openai = False
    missing_contact_fields = [] if category == CATEGORY_SPAM else get_missing_contact_fields(merged)
    is_plain_greeting = (not no_rule_match) and rule_category == CATEGORY_SALUDO
    is_typical_first_greeting = is_first and is_plain_greeting and len((_norm(msg.text)).split()) <= 3
    is_human_lookup_greeting = category == CATEGORY_SALUDO and _is_human_lookup_text(msg.text)
    needs_clarification = _needs_context_clarification(
        category=category,
        no_rule_match=no_rule_match,
        topic=topic,
        text=msg.text,
    ) and not is_typical_first_greeting and not is_context_fragment and not is_human_lookup_greeting

    should_send_followup = category != CATEGORY_SPAM
    if category == CATEGORY_SPAM:
        should_send_followup = False

    if bot_handoff_locked:
        logger.info(LOG_HANDOFF_SKIP_RESPONSE_TEMPLATE, session.id)
    elif topic_limit_exceeded:
        response_message = build_interaction_close_message()
        await close_interaction(session.id)
        resp = await generate_and_send_response(
            sender_id=msg.sender_id,
            plataforma=msg.plataforma,
            texto_override=response_message,
        )
        response_sent = resp.enviado
        response_message = resp.mensaje
    elif needs_clarification:
        flow_state = await get_conversation_flow_state(session.id)
        if flow_state.clarification_attempts >= max(settings.max_clarification_attempts - 1, 0):
            response_message = build_interaction_close_message()
            await close_interaction(session.id)
            resp = await generate_and_send_response(
                sender_id=msg.sender_id,
                plataforma=msg.plataforma,
                texto_override=response_message,
            )
            response_sent = resp.enviado
            response_message = resp.mensaje
        else:
            attempt = await increment_clarification_attempt(session.id)
            clarification_text, clarification_generated_by_openai = await build_clarification_message(
                user_text=msg.text,
                topic_hint=topic,
                missing_fields=missing_contact_fields,
                attempt=attempt,
            )
            resp = await generate_and_send_response(
                sender_id=msg.sender_id,
                plataforma=msg.plataforma,
                texto_override=clarification_text,
            )
            response_sent = resp.enviado
            response_message = resp.mensaje
            response_classified_by_openai = clarification_generated_by_openai
    elif category == CATEGORY_SALUDO:
        await reset_clarification_attempts(session.id)
        if is_human_lookup_greeting:
            reply_text = build_human_contact_bridge_message()
        elif is_plain_greeting and await is_repeat_greeting(session.id):
            reply_text = build_repeated_greeting_message()
        elif is_plain_greeting:
            reply_text = await build_greeting_message(session.id)
        else:
            reply_text, _ = build_general_topic_response(topic, merged)
        resp = await generate_and_send_response(
            sender_id=msg.sender_id,
            plataforma=msg.plataforma,
            texto_override=reply_text,
        )
        response_sent = resp.enviado
        response_message = resp.mensaje
    elif category == CATEGORY_LEAD:
        await reset_clarification_attempts(session.id)
        if lead_changed_fields:
            lead_message = build_order_change_message(lead_changed_fields, merged)
            resp = await generate_and_send_response(
                sender_id=msg.sender_id,
                plataforma=msg.plataforma,
                texto_override=lead_message,
            )
        elif active_order_status == ORDER_STATUS_READY_TO_CONFIRM:
            confirmation_prompt = build_order_confirmation_prompt(merged)
            resp = await generate_and_send_response(
                sender_id=msg.sender_id,
                plataforma=msg.plataforma,
                texto_override=confirmation_prompt,
            )
        else:
            resp = await generate_and_send_response(
                sender_id=msg.sender_id,
                plataforma=msg.plataforma,
                conversation_id=session.id,
            )
        response_sent = resp.enviado
        response_message = resp.mensaje
    elif should_send_followup:
        await reset_clarification_attempts(session.id)
        topic_followup, _ = build_general_topic_response(topic, merged)
        resp = await generate_and_send_response(
            sender_id=msg.sender_id,
            plataforma=msg.plataforma,
            texto_override=topic_followup,
        )
        response_sent = resp.enviado
        response_message = resp.mensaje

    if response_message:
        await save_message(
            session_id=session.id,
            plataforma=msg.plataforma,
            sender_id=msg.sender_id,
            direction=DIRECTION_OUTBOUND,
            texto=response_message,
            categoria=CATEGORY_AUTO_REPLY,
            order_id=order_id,
            classified_by_openai=response_classified_by_openai,
        )

    result = {
        RESULT_KEY_LEAD_ID: str(message_id),  # backward compatibility
        RESULT_KEY_MESSAGE_ID: str(message_id),
        RESULT_KEY_SESSION_ID: str(session.id),
        RESULT_KEY_CONVERSATION_ID: str(session.id),
        RESULT_KEY_ORDER_ID: str(order_id) if order_id else None,
        RESULT_KEY_TIPO: category,
        RESULT_KEY_EXTRAIDO: merged.model_dump(),
        RESULT_KEY_RESPUESTA_ENVIADA: response_sent,
        RESULT_KEY_RESPUESTA: response_message,
    }
    logger.info(LOG_PIPELINE_COMPLETE_TEMPLATE, result)
    return result
