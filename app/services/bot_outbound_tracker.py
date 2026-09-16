import asyncio
import logging
import time

from app.config import settings
from app.constants.bot_outbound import MIN_BOT_OUTBOUND_TTL_SECONDS
from app.constants.bot_outbound_runtime import (
    LOG_BOT_OUTBOUND_MATCHED_TEMPLATE,
    LOG_BOT_OUTBOUND_TRACKED_TEMPLATE,
    LOG_BOT_OUTBOUND_TRACKER_ENABLED_TEMPLATE,
)

logger = logging.getLogger(__name__)


class BotOutboundTracker:
    def __init__(self, ttl_seconds: float):
        self._ttl_seconds = max(ttl_seconds, MIN_BOT_OUTBOUND_TTL_SECONDS)
        self._events: dict[str, float] = {}
        self._lock = asyncio.Lock()
        logger.info(LOG_BOT_OUTBOUND_TRACKER_ENABLED_TEMPLATE, self._ttl_seconds)

    async def track(self, event_id: str) -> None:
        if not event_id:
            return
        async with self._lock:
            self._purge_locked()
            self._events[event_id] = time.monotonic() + self._ttl_seconds
        logger.info(LOG_BOT_OUTBOUND_TRACKED_TEMPLATE, event_id)

    async def consume_if_tracked(self, event_id: str) -> bool:
        if not event_id:
            return False
        async with self._lock:
            self._purge_locked()
            expires_at = self._events.pop(event_id, None)
        matched = expires_at is not None
        if matched:
            logger.info(LOG_BOT_OUTBOUND_MATCHED_TEMPLATE, event_id)
        return matched

    def _purge_locked(self) -> None:
        now = time.monotonic()
        expired = [event_id for event_id, expires_at in self._events.items() if expires_at <= now]
        for event_id in expired:
            self._events.pop(event_id, None)


bot_outbound_tracker = BotOutboundTracker(settings.bot_outbound_event_ttl_seconds)
