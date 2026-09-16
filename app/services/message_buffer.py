import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app.config import settings
from app.constants.message_buffer import (
    AGGREGATED_EVENT_ID_TEMPLATE,
    BUFFER_KEY_TEMPLATE,
    FLUSH_REASON_INACTIVITY,
    FLUSH_REASON_MAX_MESSAGES,
    FLUSH_REASON_MAX_WAIT,
    FLUSH_REASON_SHUTDOWN,
    MESSAGE_BURST_JOIN_SEPARATOR,
    MIN_INACTIVITY_SECONDS,
    MIN_MAX_MESSAGES,
    MIN_MAX_WAIT_SECONDS,
)
from app.constants.message_buffer_runtime import (
    LOG_BUFFER_BACKGROUND_FLUSH_FAILED_TEMPLATE,
    LOG_BUFFER_DISABLED,
    LOG_BUFFER_ENABLED_TEMPLATE,
    LOG_BUFFER_EVENT_ALREADY_PENDING_TEMPLATE,
    LOG_BUFFER_EVENT_BUFFERED_TEMPLATE,
    LOG_BUFFER_FLUSH_TEMPLATE,
    LOG_BUFFER_FLUSHED_TEMPLATE,
    LOG_BUFFER_SHUTDOWN_DRAINED,
    LOG_BUFFER_SHUTDOWN_FLUSH_TEMPLATE,
    LOG_BUFFER_TASK_CANCELLED_TEMPLATE,
)
from app.services.meta_webhook import IncomingMessage, mark_event_processed
from app.services.pipeline import process_incoming_message

logger = logging.getLogger(__name__)

ProcessMessageFn = Callable[[IncomingMessage], Awaitable[dict]]


@dataclass
class _BufferedEvent:
    event_id: str
    text: str
    nombre: str | None


@dataclass
class _BufferState:
    sender_id: str
    plataforma: str
    created_at: float
    last_received_at: float
    events: list[_BufferedEvent] = field(default_factory=list)
    flush_task: asyncio.Task | None = None


class MessageBurstBuffer:
    def __init__(
        self,
        inactivity_seconds: float,
        max_wait_seconds: float,
        max_messages: int,
        process_message: ProcessMessageFn,
    ):
        self._enabled = inactivity_seconds > 0
        self._inactivity_seconds = max(inactivity_seconds, MIN_INACTIVITY_SECONDS)
        self._max_wait_seconds = max(max_wait_seconds, MIN_MAX_WAIT_SECONDS)
        self._max_messages = max(max_messages, MIN_MAX_MESSAGES)
        self._process_message = process_message
        self._lock = asyncio.Lock()
        self._buffers: dict[str, _BufferState] = {}
        self._pending_event_ids: set[str] = set()

        if self._enabled:
            logger.info(
                LOG_BUFFER_ENABLED_TEMPLATE,
                self._inactivity_seconds,
                self._max_wait_seconds,
                self._max_messages,
            )
        else:
            logger.info(LOG_BUFFER_DISABLED)

    async def has_pending_event(self, event_id: str) -> bool:
        if not self._enabled:
            return False
        async with self._lock:
            return event_id in self._pending_event_ids

    async def enqueue_message(self, msg: IncomingMessage) -> list[dict]:
        if not self._enabled:
            result = await self._process_message(msg)
            await mark_event_processed(msg.event_id)
            return [result]

        key = self._build_key(msg.sender_id, msg.plataforma)
        now = time.monotonic()
        state_to_flush: _BufferState | None = None
        flush_reason: str | None = None

        async with self._lock:
            if msg.event_id in self._pending_event_ids:
                logger.info(LOG_BUFFER_EVENT_ALREADY_PENDING_TEMPLATE, msg.event_id)
                return []

            self._pending_event_ids.add(msg.event_id)
            state = self._buffers.get(key)
            if state is None:
                state = _BufferState(
                    sender_id=msg.sender_id,
                    plataforma=msg.plataforma,
                    created_at=now,
                    last_received_at=now,
                )
                self._buffers[key] = state

            state.events.append(_BufferedEvent(event_id=msg.event_id, text=msg.text, nombre=msg.nombre))
            state.last_received_at = now

            logger.info(
                LOG_BUFFER_EVENT_BUFFERED_TEMPLATE,
                msg.event_id,
                key,
                len(state.events),
                self._max_messages,
            )

            if len(state.events) >= self._max_messages:
                flush_reason = FLUSH_REASON_MAX_MESSAGES
                state_to_flush = self._detach_state_locked(key, state, cancel_task=True)
            elif (now - state.created_at) >= self._max_wait_seconds:
                flush_reason = FLUSH_REASON_MAX_WAIT
                state_to_flush = self._detach_state_locked(key, state, cancel_task=True)
            else:
                self._ensure_flush_task_locked(key, state)

        if state_to_flush and flush_reason:
            result = await self._flush_state(state_to_flush, flush_reason)
            return [result]

        return []

    async def shutdown(self, flush_pending: bool = True) -> None:
        if not self._enabled:
            return

        pending_states: list[tuple[str, _BufferState]] = []
        async with self._lock:
            for key, state in list(self._buffers.items()):
                pending_states.append((key, self._detach_state_locked(key, state, cancel_task=True)))

        if flush_pending and pending_states:
            logger.info(LOG_BUFFER_SHUTDOWN_FLUSH_TEMPLATE, len(pending_states))
            for key, state in pending_states:
                try:
                    await self._flush_state(state, FLUSH_REASON_SHUTDOWN, buffer_key=key)
                except Exception:
                    logger.exception(LOG_BUFFER_BACKGROUND_FLUSH_FAILED_TEMPLATE, key)
        else:
            async with self._lock:
                for _, state in pending_states:
                    for event in state.events:
                        self._pending_event_ids.discard(event.event_id)

        logger.info(LOG_BUFFER_SHUTDOWN_DRAINED)

    def _build_key(self, sender_id: str, plataforma: str) -> str:
        return BUFFER_KEY_TEMPLATE.format(plataforma=plataforma, sender_id=sender_id)

    def _ensure_flush_task_locked(self, key: str, state: _BufferState) -> None:
        if state.flush_task is None or state.flush_task.done():
            state.flush_task = asyncio.create_task(self._flush_when_idle(key))

    def _detach_state_locked(
        self,
        key: str,
        state: _BufferState,
        *,
        cancel_task: bool,
    ) -> _BufferState:
        current_task = asyncio.current_task()
        task = state.flush_task
        if cancel_task and task and not task.done() and task is not current_task:
            task.cancel()

        self._buffers.pop(key, None)
        state.flush_task = None
        return state

    async def _flush_when_idle(self, key: str) -> None:
        try:
            while True:
                state_to_flush: _BufferState | None = None
                flush_reason: str | None = None
                sleep_seconds = self._inactivity_seconds

                async with self._lock:
                    state = self._buffers.get(key)
                    if state is None:
                        return

                    now = time.monotonic()
                    idle_for = now - state.last_received_at
                    total_for = now - state.created_at

                    if len(state.events) >= self._max_messages:
                        flush_reason = FLUSH_REASON_MAX_MESSAGES
                    elif total_for >= self._max_wait_seconds:
                        flush_reason = FLUSH_REASON_MAX_WAIT
                    elif idle_for >= self._inactivity_seconds:
                        flush_reason = FLUSH_REASON_INACTIVITY

                    if flush_reason is None:
                        remaining_idle = self._inactivity_seconds - idle_for
                        remaining_total = self._max_wait_seconds - total_for
                        sleep_seconds = max(
                            min(remaining_idle, remaining_total),
                            MIN_INACTIVITY_SECONDS,
                        )
                    else:
                        state_to_flush = self._detach_state_locked(
                            key,
                            state,
                            cancel_task=False,
                        )

                if state_to_flush and flush_reason:
                    try:
                        await self._flush_state(state_to_flush, flush_reason, buffer_key=key)
                    except Exception:
                        logger.exception(LOG_BUFFER_BACKGROUND_FLUSH_FAILED_TEMPLATE, key)
                    return

                await asyncio.sleep(sleep_seconds)
        except asyncio.CancelledError:
            logger.debug(LOG_BUFFER_TASK_CANCELLED_TEMPLATE, key)
            raise

    async def _flush_state(
        self,
        state: _BufferState,
        reason: str,
        *,
        buffer_key: str | None = None,
    ) -> dict:
        key = buffer_key or self._build_key(state.sender_id, state.plataforma)
        event_ids = [event.event_id for event in state.events]

        logger.info(LOG_BUFFER_FLUSH_TEMPLATE, key, reason, len(event_ids))

        aggregated_text = MESSAGE_BURST_JOIN_SEPARATOR.join(
            part for part in (event.text.strip() for event in state.events) if part
        )
        aggregated_event_id = self._build_aggregated_event_id(event_ids)
        nombre = next((event.nombre for event in reversed(state.events) if event.nombre), None)

        merged_message = IncomingMessage(
            event_id=aggregated_event_id,
            sender_id=state.sender_id,
            text=aggregated_text,
            plataforma=state.plataforma,
            nombre=nombre,
        )

        try:
            result = await self._process_message(merged_message)
            for event_id in event_ids:
                await mark_event_processed(event_id)
        except Exception:
            async with self._lock:
                for event_id in event_ids:
                    self._pending_event_ids.discard(event_id)
            raise

        async with self._lock:
            for event_id in event_ids:
                self._pending_event_ids.discard(event_id)

        logger.info(LOG_BUFFER_FLUSHED_TEMPLATE, key, len(event_ids))
        return result

    def _build_aggregated_event_id(self, event_ids: list[str]) -> str:
        if len(event_ids) == 1:
            return event_ids[0]
        return AGGREGATED_EVENT_ID_TEMPLATE.format(
            first=event_ids[0],
            last=event_ids[-1],
            count=len(event_ids),
        )


message_burst_buffer = MessageBurstBuffer(
    inactivity_seconds=settings.message_burst_inactivity_seconds,
    max_wait_seconds=settings.message_burst_max_wait_seconds,
    max_messages=settings.message_burst_max_messages,
    process_message=process_incoming_message,
)
