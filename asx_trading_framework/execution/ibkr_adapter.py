"""
Interactive Brokers adapter — BrokerAdapter bridge.

This module bridges the async IBAdapter (broker.ib.adapter) into the
framework's synchronous BrokerAdapter interface used by ExecutionEngine.

Architecture:
    ExecutionEngine → IBKRBrokerAdapter (this file) → IBAdapter (ib_async)

The IBAdapter does the real work (connect, resolve, place orders).
This wrapper translates between the framework's Order/Fill types
and the ib_async Trade/Contract types.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from ..core.events import Event, EventBus, EventType
from ..core.types import Fill, Order, OrderStatus, OrderType, Side, TimeInForce
from ..execution.engine import BrokerAdapter, transition_order

logger = logging.getLogger(__name__)

# Try to import the new adapter
try:
    from ..broker.ib.adapter import IBAdapter, IB_LIB
    from ..broker.ib.config import IBConfig
    from ..broker.ib.errors import IBAdapterError
    IB_AVAILABLE = True
except ImportError:
    IB_AVAILABLE = False
    IB_LIB = ""


class IBKRBrokerAdapter(BrokerAdapter):
    """
    BrokerAdapter implementation backed by IBAdapter (ib_async).

    This is the bridge between the framework's execution engine
    and the ib_async-based IB adapter.

    Config:
        host:       TWS/Gateway host          (DEFAULT: "127.0.0.1")
        port:       API socket port            (DEFAULT: 7497 paper)
        client_id:  API client ID              (DEFAULT: 1)
        account_id: IB account ID
    """

    def __init__(
        self,
        event_bus: EventBus,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        account_id: str = "",
    ) -> None:
        if not IB_AVAILABLE:
            raise ImportError(
                "IB adapter not available. Install: pip install ib_async"
            )

        self.event_bus = event_bus

        config = IBConfig(
            host=host,
            port=port,
            client_id=client_id,
            account=account_id,
            mode="paper" if port in (7497, 4002) else "live",
        )
        self._adapter = IBAdapter(config)
        self._trade_map: dict[str, Any] = {}  # our order_id → ib_async Trade

    def connect(self) -> bool:
        """Connect to TWS/Gateway."""
        try:
            self._adapter.connect_sync()
            return True
        except Exception:
            logger.exception("IBKR connection failed")
            return False

    def disconnect(self) -> None:
        """Disconnect from TWS/Gateway."""
        self._adapter.disconnect_sync()

    def submit_order(self, order: Order) -> bool:
        """Submit an order to IB via ib_async."""
        try:
            contract = self._adapter.resolve_contract_sync(order.symbol)
        except IBAdapterError as exc:
            logger.error("Contract resolution failed for %s: %s", order.symbol, exc)
            return False

        try:
            if order.order_type == OrderType.MARKET:
                trade = self._adapter.place_market_order_sync(
                    order.symbol,
                    "BUY" if order.side == Side.BUY else "SELL",
                    order.quantity,
                )
            elif order.order_type == OrderType.LIMIT:
                if order.price is None:
                    logger.error("Limit order requires a price")
                    return False
                trade = self._adapter.place_limit_order_sync(
                    order.symbol,
                    "BUY" if order.side == Side.BUY else "SELL",
                    order.quantity,
                    float(order.price),
                )
            else:
                logger.error("Unsupported order type for IB bridge: %s", order.order_type)
                return False

            self._trade_map[order.order_id] = trade
            transition_order(order, OrderStatus.NEW)

            logger.info(
                "IBKR order submitted: %s %s %d @ %s (IB orderId=%d)",
                order.side.value, order.symbol, order.quantity,
                order.price, trade.order.orderId,
            )
            return True

        except IBAdapterError as exc:
            logger.error("Order submission failed: %s", exc)
            return False

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order via IB."""
        trade = self._trade_map.get(order_id)
        if trade is None:
            logger.warning("Cannot cancel: unknown order %s", order_id)
            return False

        try:
            from ..broker.ib.adapter import _run
            _run(self._adapter.cancel_order(trade))
            return True
        except IBAdapterError as exc:
            logger.error("Cancel failed: %s", exc)
            return False

    def get_order_status(self, order_id: str) -> OrderStatus | None:
        """Get order status from IB."""
        trade = self._trade_map.get(order_id)
        if trade is None:
            return None

        ib_status = trade.orderStatus.status
        status_map = {
            "Submitted": OrderStatus.NEW,
            "PreSubmitted": OrderStatus.PENDING_NEW,
            "Filled": OrderStatus.FILLED,
            "Cancelled": OrderStatus.CANCELLED,
            "Inactive": OrderStatus.REJECTED,
        }
        return status_map.get(ib_status, OrderStatus.NEW)

    def get_positions(self) -> dict[str, int]:
        """Query current positions from IB."""
        try:
            positions = self._adapter.get_positions_sync()
            return {p["symbol"]: p["quantity"] for p in positions}
        except IBAdapterError as exc:
            logger.error("Position query failed: %s", exc)
            return {}
