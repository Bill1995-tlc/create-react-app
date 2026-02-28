"""
Interactive Brokers TWS API adapter.

Full automation path for ASX equities. Requires:
- IB Gateway or TWS running locally
- ibapi Python package (pip install ibapi)
- An IBKR account with ASX market data subscription

This adapter implements the BrokerAdapter interface using IB's
EClient/EWrapper pattern, providing:
- Order submission (LIMIT, MARKET, STOP, STOP_LIMIT)
- Order cancellation
- Position queries
- Fill callbacks via the event bus

IMPORTANT: This is a skeleton. The ibapi package must be installed
and IB Gateway must be running for this to work. The framework
gracefully falls back to PaperBrokerAdapter if ibapi is unavailable.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ..core.events import Event, EventBus, EventType
from ..core.types import Fill, Order, OrderStatus, OrderType, Side, TimeInForce
from ..execution.engine import BrokerAdapter, transition_order

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Try to import ibapi — gracefully handle if not installed
try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
    from ibapi.order import Order as IBOrder
    from ibapi.execution import Execution
    IBAPI_AVAILABLE = True
except ImportError:
    IBAPI_AVAILABLE = False
    logger.info("ibapi not installed. IBKR adapter unavailable. pip install ibapi")


# ──────────────────────────────────────────────
# IB TWS/Gateway Connection Wrapper
# ──────────────────────────────────────────────

if IBAPI_AVAILABLE:
    class IBWrapper(EWrapper):
        """Handles callbacks from IB TWS/Gateway."""

        def __init__(self, adapter: IBKRBrokerAdapter) -> None:
            super().__init__()
            self.adapter = adapter

        def nextValidId(self, orderId: int) -> None:
            """Called when connection is established."""
            self.adapter._next_order_id = orderId
            self.adapter._connected.set()
            logger.info("IBKR connected. Next valid order ID: %d", orderId)

        def orderStatus(
            self,
            orderId: int,
            status: str,
            filled: float,
            remaining: float,
            avgFillPrice: float,
            permId: int,
            parentId: int,
            lastFillPrice: float,
            clientId: int,
            whyHeld: str,
            mktCapPrice: float = 0.0,
        ) -> None:
            """Order status update from IB."""
            self.adapter._handle_order_status(
                orderId, status, int(filled), int(remaining),
                Decimal(str(avgFillPrice)),
            )

        def execDetails(
            self,
            reqId: int,
            contract: Contract,
            execution: Execution,
        ) -> None:
            """Execution (fill) details from IB."""
            self.adapter._handle_execution(contract, execution)

        def position(
            self,
            account: str,
            contract: Contract,
            pos: float,
            avgCost: float,
        ) -> None:
            """Position update from IB."""
            symbol = contract.symbol
            qty = int(pos)
            self.adapter._positions_cache[symbol] = qty
            if qty == 0:
                self.adapter._positions_cache.pop(symbol, None)

        def error(
            self,
            reqId: int,
            errorCode: int,
            errorString: str,
            advancedOrderRejectJson: str = "",
        ) -> None:
            """Error callback from IB."""
            # Codes 2103, 2104, 2106 are connection info, not errors
            if errorCode in (2103, 2104, 2106, 2158):
                logger.info("IBKR info [%d]: %s", errorCode, errorString)
            elif errorCode == 202:
                # Order cancelled
                logger.info("IBKR order cancelled [reqId=%d]", reqId)
            else:
                logger.error(
                    "IBKR error [reqId=%d, code=%d]: %s",
                    reqId, errorCode, errorString,
                )
                if errorCode in (201, 203):
                    # Order rejected
                    self.adapter._handle_rejection(reqId, errorString)

    class IBClient(EClient):
        """IB API client."""

        def __init__(self, wrapper: IBWrapper) -> None:
            super().__init__(wrapper)


# ──────────────────────────────────────────────
# IBKR Broker Adapter
# ──────────────────────────────────────────────

class IBKRBrokerAdapter(BrokerAdapter):
    """
    Interactive Brokers adapter for ASX equities.

    Config keys:
    - host: TWS/Gateway host (DEFAULT: "127.0.0.1")
    - port: TWS/Gateway port (DEFAULT: 7497 for paper, 7496 for live)
    - client_id: API client ID (DEFAULT: 1)
    - account_id: IB account ID
    """

    def __init__(
        self,
        event_bus: EventBus,
        host: str = "127.0.0.1",       # DEFAULT
        port: int = 7497,               # DEFAULT: paper trading port
        client_id: int = 1,             # DEFAULT
        account_id: str = "",
    ) -> None:
        if not IBAPI_AVAILABLE:
            raise ImportError(
                "ibapi package not installed. Run: pip install ibapi"
            )

        self.event_bus = event_bus
        self.host = host
        self.port = port
        self.client_id = client_id
        self.account_id = account_id

        # IB API objects
        self._wrapper = IBWrapper(self)
        self._client = IBClient(self._wrapper)

        # State
        self._next_order_id: int = 0
        self._connected = threading.Event()
        self._order_map: dict[int, Order] = {}      # IB order ID → our Order
        self._our_to_ib: dict[str, int] = {}         # our order_id → IB order ID
        self._positions_cache: dict[str, int] = {}
        self._thread: threading.Thread | None = None

    def connect(self) -> bool:
        """Connect to TWS/Gateway."""
        try:
            self._client.connect(self.host, self.port, self.client_id)
            self._thread = threading.Thread(
                target=self._client.run, daemon=True
            )
            self._thread.start()

            # Wait for connection confirmation
            if not self._connected.wait(timeout=10):
                logger.error("IBKR connection timeout")
                return False

            logger.info("IBKR connected to %s:%d", self.host, self.port)
            return True
        except Exception:
            logger.exception("IBKR connection failed")
            return False

    def disconnect(self) -> None:
        """Disconnect from TWS/Gateway."""
        self._client.disconnect()
        logger.info("IBKR disconnected")

    def submit_order(self, order: Order) -> bool:
        """Submit an order to IB."""
        if not self._connected.is_set():
            logger.error("Cannot submit order: not connected to IBKR")
            return False

        contract = self._make_contract(order.symbol)
        ib_order = self._make_ib_order(order)

        ib_id = self._next_order_id
        self._next_order_id += 1

        self._order_map[ib_id] = order
        self._our_to_ib[order.order_id] = ib_id

        self._client.placeOrder(ib_id, contract, ib_order)
        transition_order(order, OrderStatus.NEW)

        logger.info(
            "IBKR order submitted: IB#%d %s %s %d @ %s",
            ib_id, order.side.value, order.symbol,
            order.quantity, order.price,
        )
        return True

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order via IB."""
        ib_id = self._our_to_ib.get(order_id)
        if ib_id is None:
            logger.warning("Cannot cancel: unknown order %s", order_id)
            return False

        self._client.cancelOrder(ib_id, "")
        order = self._order_map.get(ib_id)
        if order:
            transition_order(order, OrderStatus.PENDING_CANCEL)
        return True

    def get_order_status(self, order_id: str) -> OrderStatus | None:
        """Get order status."""
        ib_id = self._our_to_ib.get(order_id)
        if ib_id is None:
            return None
        order = self._order_map.get(ib_id)
        return order.status if order else None

    def get_positions(self) -> dict[str, int]:
        """Query current positions from IB."""
        self._client.reqPositions()
        time.sleep(1)  # Brief wait for async response
        return dict(self._positions_cache)

    # ──────────────────────────────────────────
    # IB callback handlers
    # ──────────────────────────────────────────

    def _handle_order_status(
        self,
        ib_id: int,
        status: str,
        filled: int,
        remaining: int,
        avg_price: Decimal,
    ) -> None:
        """Process IB order status update."""
        order = self._order_map.get(ib_id)
        if order is None:
            return

        ib_status_map = {
            "Submitted": OrderStatus.NEW,
            "PreSubmitted": OrderStatus.PENDING_NEW,
            "Filled": OrderStatus.FILLED,
            "Cancelled": OrderStatus.CANCELLED,
            "Inactive": OrderStatus.REJECTED,
        }

        new_status = ib_status_map.get(status)
        if new_status and new_status != order.status:
            transition_order(order, new_status)

        order.filled_quantity = filled
        if avg_price > 0:
            order.average_fill_price = avg_price

    def _handle_execution(self, contract: Any, execution: Any) -> None:
        """Process IB execution (fill) report."""
        ib_id = execution.orderId
        order = self._order_map.get(ib_id)
        if order is None:
            return

        fill = Fill(
            fill_id=execution.execId,
            order_id=order.order_id,
            symbol=contract.symbol,
            side=Side.BUY if execution.side == "BOT" else Side.SELL,
            quantity=int(execution.shares),
            price=Decimal(str(execution.price)),
            commission=Decimal("0"),  # Commission comes in a separate callback
            timestamp=order.updated_at,
            exchange_trade_id=execution.execId,
        )

        event_type = EventType.ORDER_FILLED
        if order.filled_quantity < order.quantity:
            event_type = EventType.ORDER_PARTIALLY_FILLED

        self.event_bus.publish(Event(
            event_type=event_type,
            data={"order": order, "fill": fill},
            source="ibkr_adapter",
        ))

    def _handle_rejection(self, ib_id: int, reason: str) -> None:
        """Handle order rejection from IB."""
        order = self._order_map.get(ib_id)
        if order:
            transition_order(order, OrderStatus.REJECTED)
            self.event_bus.publish(Event(
                event_type=EventType.ORDER_REJECTED,
                data={"order": order, "reason": reason},
                source="ibkr_adapter",
            ))

    # ──────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────

    @staticmethod
    def _make_contract(symbol: str) -> Contract:
        """Create an IB Contract for an ASX equity."""
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = "ASX"         # Australian Securities Exchange
        contract.currency = "AUD"
        contract.primaryExchange = "ASX"
        return contract

    @staticmethod
    def _make_ib_order(order: Order) -> IBOrder:
        """Convert our Order to an IB Order object."""
        ib_order = IBOrder()
        ib_order.action = "BUY" if order.side == Side.BUY else "SELL"
        ib_order.totalQuantity = order.quantity

        # Order type mapping
        type_map = {
            OrderType.MARKET: "MKT",
            OrderType.LIMIT: "LMT",
            OrderType.STOP: "STP",
            OrderType.STOP_LIMIT: "STP LMT",
        }
        ib_order.orderType = type_map.get(order.order_type, "LMT")

        if order.price is not None:
            ib_order.lmtPrice = float(order.price)
        if order.stop_price is not None:
            ib_order.auxPrice = float(order.stop_price)

        # Time in force
        tif_map = {
            TimeInForce.DAY: "DAY",
            TimeInForce.IOC: "IOC",
            TimeInForce.FOK: "FOK",
            TimeInForce.GTC: "GTC",
        }
        ib_order.tif = tif_map.get(order.time_in_force, "DAY")

        return ib_order
