"""
Dry-run broker adapter — wraps any real adapter but HARD-BLOCKS order operations.

Used in dry-run mode to validate connectivity, market data, and signal generation
without any risk of accidental order placement.
"""

from __future__ import annotations

import logging

from ..core.types import Order, OrderStatus
from .engine import BrokerAdapter

logger = logging.getLogger(__name__)


class DryRunBlocked(Exception):
    """Raised when an order operation is attempted in dry-run mode."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(
            f"DRY-RUN BLOCKED: {operation}. "
            "No orders can be placed in dry-run mode."
        )


class DryRunBrokerAdapter(BrokerAdapter):
    """
    Wraps a real BrokerAdapter and blocks all order operations.

    Read-only operations (get_positions, connect, market data) are
    delegated to the inner adapter. Any attempt to submit or cancel
    orders raises DryRunBlocked.

    This ensures dry-run mode can never accidentally place real orders,
    regardless of what signals the strategy engine generates.
    """

    def __init__(self, inner: BrokerAdapter) -> None:
        self._inner = inner
        self._blocked_count: int = 0
        logger.warning(
            "DRY-RUN MODE: Order operations are BLOCKED. "
            "Market data and account queries will work normally."
        )

    def submit_order(self, order: Order) -> bool:
        self._blocked_count += 1
        logger.warning(
            "DRY-RUN BLOCKED order: %s %s %d @ %s (blocked count: %d)",
            order.side.value,
            order.symbol,
            order.quantity,
            order.price,
            self._blocked_count,
        )
        raise DryRunBlocked(
            f"submit_order({order.side.value} {order.symbol} "
            f"qty={order.quantity} @ {order.price})"
        )

    def cancel_order(self, order_id: str) -> bool:
        self._blocked_count += 1
        logger.warning("DRY-RUN BLOCKED cancel: order_id=%s", order_id)
        raise DryRunBlocked(f"cancel_order({order_id})")

    def get_order_status(self, order_id: str) -> OrderStatus | None:
        # Allow status queries — they're read-only
        if hasattr(self._inner, "get_order_status"):
            return self._inner.get_order_status(order_id)
        return None

    def get_positions(self) -> dict[str, int]:
        return self._inner.get_positions()

    # Delegate connection methods if the inner adapter has them
    def connect(self) -> bool:
        if hasattr(self._inner, "connect"):
            return self._inner.connect()
        return True

    def disconnect(self) -> None:
        if hasattr(self._inner, "disconnect"):
            self._inner.disconnect()

    @property
    def blocked_count(self) -> int:
        return self._blocked_count

    @property
    def inner(self) -> BrokerAdapter:
        return self._inner
