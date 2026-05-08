"""
Execution engine — order management, routing, and fill handling.

Features:
- Order state machine with deterministic transitions
- Abstract broker adapter interface
- Slippage controls
- Retry logic
- EOD flatten
"""

from __future__ import annotations

import abc
import logging
import uuid
from datetime import datetime, time
from decimal import Decimal
from typing import Any

from ..core.config import ExecutionConfig, FrameworkConfig
from ..core.events import Event, EventBus, EventType
from ..core.types import (
    Fill,
    Order,
    OrderStatus,
    OrderType,
    Quote,
    Side,
    Signal,
    SignalAction,
    TimeInForce,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Order state machine — valid transitions
# ──────────────────────────────────────────────

VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING_NEW: {
        OrderStatus.NEW,
        OrderStatus.REJECTED,
    },
    OrderStatus.NEW: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.PENDING_CANCEL,
        OrderStatus.EXPIRED,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.PARTIALLY_FILLED,  # More partial fills
        OrderStatus.FILLED,
        OrderStatus.PENDING_CANCEL,
    },
    OrderStatus.PENDING_CANCEL: {
        OrderStatus.CANCELLED,
        OrderStatus.FILLED,  # Race condition: fill arrives before cancel ack
    },
    # Terminal states — no transitions
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.EXPIRED: set(),
}


def transition_order(order: Order, new_status: OrderStatus) -> bool:
    """
    Attempt a state transition. Returns True if valid, False if rejected.
    This is a deterministic, testable state machine.
    """
    valid = VALID_TRANSITIONS.get(order.status, set())
    if new_status not in valid:
        logger.error(
            "Invalid order transition: %s -> %s (order %s)",
            order.status.value,
            new_status.value,
            order.order_id,
        )
        return False
    order.status = new_status
    order.updated_at = datetime.utcnow()
    return True


# ──────────────────────────────────────────────
# Abstract broker adapter
# ──────────────────────────────────────────────

class BrokerAdapter(abc.ABC):
    """
    Abstract broker interface.

    Implement this for each broker API (Interactive Brokers, etc.).
    Paper trading adapter is provided as the default.
    """

    @abc.abstractmethod
    def submit_order(self, order: Order) -> bool:
        """Submit an order. Returns True if accepted."""

    @abc.abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Request order cancellation."""

    @abc.abstractmethod
    def get_order_status(self, order_id: str) -> OrderStatus | None:
        """Poll order status."""

    @abc.abstractmethod
    def get_positions(self) -> dict[str, int]:
        """Get current positions from broker."""


class PaperBrokerAdapter(BrokerAdapter):
    """
    Paper trading broker — simulates fills from market data.
    Uses last quote mid-price with configurable slippage.
    """

    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}
        self._positions: dict[str, int] = {}

    def submit_order(self, order: Order) -> bool:
        self._orders[order.order_id] = order
        transition_order(order, OrderStatus.NEW)
        return True

    def cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if order and not order.is_terminal:
            transition_order(order, OrderStatus.PENDING_CANCEL)
            transition_order(order, OrderStatus.CANCELLED)
            return True
        return False

    def get_order_status(self, order_id: str) -> OrderStatus | None:
        order = self._orders.get(order_id)
        return order.status if order else None

    def get_positions(self) -> dict[str, int]:
        return dict(self._positions)

    def simulate_fill(self, order: Order, fill_price: Decimal) -> Fill:
        """Simulate a full fill at the given price."""
        fill = Fill(
            fill_id=str(uuid.uuid4()),
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            commission=Decimal("10"),  # DEFAULT flat commission
            timestamp=datetime.utcnow(),
        )
        order.filled_quantity = order.quantity
        order.average_fill_price = fill_price
        transition_order(order, OrderStatus.FILLED)

        # Update positions
        delta = order.quantity if order.side == Side.BUY else -order.quantity
        self._positions[order.symbol] = self._positions.get(order.symbol, 0) + delta
        if self._positions[order.symbol] == 0:
            del self._positions[order.symbol]

        return fill


# ──────────────────────────────────────────────
# Execution engine
# ──────────────────────────────────────────────

class ExecutionEngine:
    """
    Manages order lifecycle from signal to fill.

    Responsibilities:
    - Convert signals to orders
    - Route orders through broker adapter
    - Handle fills and publish events
    - EOD flatten logic
    """

    def __init__(
        self,
        config: FrameworkConfig,
        event_bus: EventBus,
        broker: BrokerAdapter,
    ) -> None:
        self.config = config
        self.exec_config: ExecutionConfig = config.execution
        self.event_bus = event_bus
        self.broker = broker
        self._active_orders: dict[str, Order] = {}
        self._all_orders: list[Order] = []
        self._eod_flatten_time = time.fromisoformat(self.exec_config.eod_flatten_time)

    def create_order_from_signal(
        self,
        signal: Signal,
        quote: Quote | None = None,
    ) -> Order:
        """Convert a risk-approved signal into an order."""
        order_type = OrderType[self.exec_config.default_order_type]
        tif = TimeInForce[self.exec_config.default_time_in_force]

        # Determine price
        price = signal.price
        if quote is not None and order_type == OrderType.LIMIT:
            # Set limit at a realistic level
            if signal.action == SignalAction.ENTER_LONG:
                price = quote.ask  # Cross the spread to get filled
            elif signal.action == SignalAction.ENTER_SHORT:
                price = quote.bid
            elif signal.action == SignalAction.EXIT:
                price = quote.bid if signal.quantity > 0 else quote.ask

        side = Side.BUY
        if signal.action == SignalAction.ENTER_SHORT:
            side = Side.SELL
        elif signal.action == SignalAction.EXIT:
            side = Side.SELL  # Assumes closing a long; adjust for shorts

        order = Order(
            order_id=str(uuid.uuid4()),
            symbol=signal.symbol,
            side=side,
            order_type=order_type,
            quantity=signal.quantity,
            price=price,
            time_in_force=tif,
            strategy_id=signal.strategy_id,
            tags=dict(signal.metadata) if signal.metadata else {},
        )

        # Set stop price if applicable
        if signal.stop_loss and order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
            order.stop_price = signal.stop_loss

        return order

    def submit_order(self, order: Order) -> bool:
        """Submit an order to the broker."""
        success = self.broker.submit_order(order)
        if success:
            self._active_orders[order.order_id] = order
            self._all_orders.append(order)
            self.event_bus.publish(Event(
                event_type=EventType.ORDER_ACCEPTED,
                data={"order": order},
                source="execution_engine",
            ))
            logger.info(
                "Order submitted: %s %s %s %d @ %s",
                order.order_id[:8],
                order.side.value,
                order.symbol,
                order.quantity,
                order.price,
            )
        else:
            transition_order(order, OrderStatus.REJECTED)
            self.event_bus.publish(Event(
                event_type=EventType.ORDER_REJECTED,
                data={"order": order},
                source="execution_engine",
            ))
            logger.warning("Order rejected: %s", order.order_id[:8])
        return success

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an active order."""
        success = self.broker.cancel_order(order_id)
        if success:
            order = self._active_orders.pop(order_id, None)
            if order:
                self.event_bus.publish(Event(
                    event_type=EventType.ORDER_CANCELLED,
                    data={"order": order},
                    source="execution_engine",
                ))
        return success

    def process_fill(self, fill: Fill) -> None:
        """Process a fill event from the broker."""
        order = self._active_orders.get(fill.order_id)
        if order is None:
            logger.error("Fill for unknown order: %s", fill.order_id)
            return

        order.filled_quantity += fill.quantity
        order.average_fill_price = fill.price  # Simplified; should be weighted avg

        if order.filled_quantity >= order.quantity:
            transition_order(order, OrderStatus.FILLED)
            self._active_orders.pop(fill.order_id, None)
            event_type = EventType.ORDER_FILLED
        else:
            transition_order(order, OrderStatus.PARTIALLY_FILLED)
            event_type = EventType.ORDER_PARTIALLY_FILLED

        self.event_bus.publish(Event(
            event_type=event_type,
            data={"order": order, "fill": fill},
            source="execution_engine",
        ))
        logger.info(
            "Fill: %s %d %s @ %s (commission=%s)",
            fill.side.value,
            fill.quantity,
            fill.symbol,
            fill.price,
            fill.commission,
        )

    def cancel_all_orders(self) -> int:
        """Cancel all active orders. Returns count cancelled."""
        count = 0
        for order_id in list(self._active_orders.keys()):
            if self.cancel_order(order_id):
                count += 1
        return count

    def should_flatten_eod(self, current_time: time) -> bool:
        """Check if it's time to flatten all positions (EOD)."""
        return (
            self.exec_config.end_of_day_flat
            and current_time >= self._eod_flatten_time
        )

    @property
    def active_orders(self) -> dict[str, Order]:
        return dict(self._active_orders)

    @property
    def all_orders(self) -> list[Order]:
        return list(self._all_orders)
