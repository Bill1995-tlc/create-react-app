"""
Event bus — the backbone of the event-driven architecture.

All modules communicate via typed events through this bus.
Synchronous by default; async dispatch available for I/O-bound handlers.
"""

from __future__ import annotations

import enum
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

logger = logging.getLogger(__name__)


class EventType(enum.Enum):
    # Data events
    BAR = "BAR"
    QUOTE = "QUOTE"
    TRADE_TICK = "TRADE_TICK"

    # Signal events
    SIGNAL = "SIGNAL"

    # Order lifecycle
    ORDER_NEW = "ORDER_NEW"
    ORDER_ACCEPTED = "ORDER_ACCEPTED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_PARTIALLY_FILLED = "ORDER_PARTIALLY_FILLED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_EXPIRED = "ORDER_EXPIRED"

    # Risk events
    RISK_VETO = "RISK_VETO"
    RISK_ALERT = "RISK_ALERT"
    DAILY_LOSS_LIMIT_HIT = "DAILY_LOSS_LIMIT_HIT"
    WEEKLY_LOSS_LIMIT_HIT = "WEEKLY_LOSS_LIMIT_HIT"

    # Kill switch
    KILL_SWITCH_ACTIVATED = "KILL_SWITCH_ACTIVATED"
    CIRCUIT_BREAKER_TRIPPED = "CIRCUIT_BREAKER_TRIPPED"

    # Position events
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_CLOSED = "POSITION_CLOSED"
    POSITION_UPDATED = "POSITION_UPDATED"

    # Operations
    DAILY_PREP_COMPLETE = "DAILY_PREP_COMPLETE"
    EOD_REVIEW_COMPLETE = "EOD_REVIEW_COMPLETE"
    ALERT = "ALERT"

    # System
    SYSTEM_START = "SYSTEM_START"
    SYSTEM_SHUTDOWN = "SYSTEM_SHUTDOWN"
    HEARTBEAT = "HEARTBEAT"
    ERROR = "ERROR"


@dataclass
class Event:
    """Base event that flows through the event bus."""
    event_type: EventType
    timestamp: datetime = field(default_factory=datetime.utcnow)
    data: dict[str, Any] = field(default_factory=dict)
    source: str = ""


# Handler type: takes an Event and returns nothing
EventHandler = Callable[[Event], None]


class EventBus:
    """
    Central event bus for inter-module communication.

    Synchronous, deterministic dispatch.
    Handlers are called in registration order.
    Exceptions in one handler do not prevent others from running.
    """

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._global_handlers: list[EventHandler] = []
        self._event_log: list[Event] = []
        self._max_log_size: int = 10_000

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Subscribe a handler to a specific event type."""
        self._handlers[event_type].append(handler)
        logger.debug(
            "Handler %s subscribed to %s", handler.__name__, event_type.value
        )

    def subscribe_all(self, handler: EventHandler) -> None:
        """Subscribe a handler to ALL events (useful for logging/metrics)."""
        self._global_handlers.append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Remove a handler from an event type."""
        handlers = self._handlers[event_type]
        if handler in handlers:
            handlers.remove(handler)

    def publish(self, event: Event) -> None:
        """Publish an event to all registered handlers."""
        # Log the event
        self._event_log.append(event)
        if len(self._event_log) > self._max_log_size:
            self._event_log = self._event_log[-self._max_log_size:]

        # Dispatch to type-specific handlers
        for handler in self._handlers.get(event.event_type, []):
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Handler %s failed on event %s",
                    handler.__name__,
                    event.event_type.value,
                )

        # Dispatch to global handlers
        for handler in self._global_handlers:
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Global handler %s failed on event %s",
                    handler.__name__,
                    event.event_type.value,
                )

    def get_event_log(
        self,
        event_type: EventType | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Retrieve recent events, optionally filtered by type."""
        if event_type is None:
            return self._event_log[-limit:]
        return [e for e in self._event_log if e.event_type == event_type][-limit:]

    def clear_handlers(self) -> None:
        """Remove all handlers. Used in testing."""
        self._handlers.clear()
        self._global_handlers.clear()
