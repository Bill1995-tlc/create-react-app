"""
Interactive Brokers adapter using ib_async.

This is the primary broker adapter for automated ASX equity trading.
It wraps the ib_async library (the modern, maintained fork of ib_insync)
to provide a clean, async Python interface.

Key design decisions:
- Async-first but provides sync wrappers for CLI use
- All contract resolution goes through qualifyContracts() to avoid ambiguity
- Comprehensive error mapping to custom exceptions
- Logging at every boundary for debuggability
- Fill callbacks via orderStatusEvent for real-time trade tracking
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any, Callable

from .config import IBConfig
from .errors import (
    IBAdapterError,
    IBConnectionError,
    IBContractError,
    IBMarketDataError,
    IBOrderError,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Import ib_async with fallback to ib_insync
# ──────────────────────────────────────────────

IB_LIB: str = ""

try:
    import ib_async
    from ib_async import (
        IB,
        Contract,
        Stock,
        LimitOrder,
        MarketOrder,
        StopOrder,
        Order as IBOrder,
        Trade,
        AccountValue,
        Position,
        Ticker,
        util,
    )
    IB_LIB = "ib_async"
except ImportError:
    try:
        import ib_insync as ib_async  # type: ignore[no-redef]
        from ib_insync import (  # type: ignore[no-redef, assignment]
            IB,
            Contract,
            Stock,
            LimitOrder,
            MarketOrder,
            StopOrder,
            Order as IBOrder,
            Trade,
            AccountValue,
            Position,
            Ticker,
            util,
        )
        IB_LIB = "ib_insync"
        logger.info("Using ib_insync (fallback). Consider: pip install ib_async")
    except ImportError:
        IB_LIB = ""
        # Stubs so module-level type aliases below remain importable.
        # IBAdapter.__init__ guards real use via require_ib_lib().
        Trade = Any  # type: ignore[misc, assignment]


def require_ib_lib() -> None:
    """Raise ImportError if neither ib_async nor ib_insync is available."""
    if not IB_LIB:
        raise ImportError(
            "Neither ib_async nor ib_insync is installed.\n"
            "Install with: pip install ib_async\n"
            "Or fallback:  pip install ib_insync"
        )


# ──────────────────────────────────────────────
# IB Error Code Classification
# ──────────────────────────────────────────────

INFO_CODES = {2103, 2104, 2106, 2107, 2108, 2119, 2158}

CONN_ERROR_CODES = {
    502: "Could not connect — TWS/Gateway not running or wrong port",
    504: "Not connected — call connect() first",
    509: "Exception caught — possible network issue",
    1100: "Connectivity lost",
    1300: "Socket dropped — TWS/Gateway may have shut down",
}

MKTDATA_ERROR_CODES = {
    354: "No market data subscription for ASX. Subscribe in Account Management.",
    10167: "Delayed market data — no real-time subscription",
    10168: "Delayed market data requested",
    10197: "No market data during competing live session",
}

ORDER_ERROR_CODES = {
    103: "Duplicate order ID",
    104: "Cannot modify filled order",
    105: "Order being modified — please wait",
    110: "Price does not conform to minimum tick",
    135: "Cannot cancel order — already cancelled or filled",
    161: "Cancel attempted when order is not in cancellable state",
    201: "Order rejected",
    202: "Order cancelled",
    203: "Insufficient margin / buying power",
    399: "Order message: check warning",
    10147: "OrderId already in use",
}

PERMISSION_CODES = {
    326: "Client not authenticated — check trusted IPs in TWS config",
    1101: "Connectivity restored — data lost",
    1102: "Connectivity restored — data maintained",
    2100: "API client is unsubscribed from account data",
    2101: "Paper trading account requires separate API client subscription",
    2105: "HMDS data farm is disconnected",
}

# Callback type for fill notifications
FillCallback = Callable[[Trade], None]
OrderStatusCallback = Callable[[Trade], None]


# ──────────────────────────────────────────────
# Adapter
# ──────────────────────────────────────────────

class IBAdapter:
    """
    Async Interactive Brokers adapter for ASX equities.

    Uses ib_async (preferred) or ib_insync as the underlying library.

    Supports all order types needed for live trading:
    - Market, Limit, Stop, Stop-Limit
    - Bracket orders (entry + stop-loss + take-profit as one atomic group)

    Fill notifications are delivered via callbacks registered with
    on_fill() and on_order_status().
    """

    RECONNECT_DELAYS = [2, 4, 8, 16]

    def __init__(self, config: IBConfig | None = None) -> None:
        require_ib_lib()
        self.config = config or IBConfig.from_env()
        self._ib: IB = IB()
        self._connected: bool = False
        self._contract_cache: dict[str, Contract] = {}
        self._reconnect_count: int = 0

        # Callback registries
        self._fill_callbacks: list[FillCallback] = []
        self._order_status_callbacks: list[OrderStatusCallback] = []

        # Register event handlers
        self._ib.errorEvent += self._on_error
        self._ib.disconnectedEvent += self._on_disconnect
        self._ib.orderStatusEvent += self._on_order_status
        self._ib.newOrderEvent += self._on_new_order

        logger.info("IBAdapter initialised. Library: %s. %s", IB_LIB, self.config.describe())

    # ──────────────────────────────────────────
    # Callback registration
    # ──────────────────────────────────────────

    def on_fill(self, callback: FillCallback) -> None:
        """Register a callback for fill events. Called when a trade reaches terminal fill."""
        self._fill_callbacks.append(callback)

    def on_order_status(self, callback: OrderStatusCallback) -> None:
        """Register a callback for any order status change."""
        self._order_status_callbacks.append(callback)

    def _on_order_status(self, trade: Trade) -> None:
        """Internal handler — dispatches to registered callbacks."""
        status = trade.orderStatus.status
        symbol = trade.contract.symbol if trade.contract else "?"

        logger.info(
            "Order status: %s %s orderId=%d status=%s filled=%d remaining=%d avgPrice=%s",
            trade.order.action, symbol, trade.order.orderId,
            status, int(trade.orderStatus.filled),
            int(trade.orderStatus.remaining),
            trade.orderStatus.avgFillPrice,
        )

        for cb in self._order_status_callbacks:
            try:
                cb(trade)
            except Exception:
                logger.exception("Order status callback failed")

        if status == "Filled":
            for cb in self._fill_callbacks:
                try:
                    cb(trade)
                except Exception:
                    logger.exception("Fill callback failed")

    def _on_new_order(self, trade: Trade) -> None:
        """Log new order placement confirmation from IB."""
        logger.debug(
            "New order confirmed: orderId=%d %s %s",
            trade.order.orderId,
            trade.order.action,
            trade.contract.symbol if trade.contract else "?",
        )

    # ──────────────────────────────────────────
    # Connection management
    # ──────────────────────────────────────────

    async def connect(
        self,
        host: str | None = None,
        port: int | None = None,
        client_id: int | None = None,
    ) -> None:
        """Connect to TWS or IB Gateway. Raises IBConnectionError on failure."""
        h = host or self.config.host
        p = port or self.config.port
        cid = client_id or self.config.client_id

        logger.info("Connecting to IB at %s:%d (clientId=%d) ...", h, p, cid)

        try:
            await self._ib.connectAsync(
                host=h, port=p, clientId=cid,
                timeout=self.config.timeout,
                readonly=self.config.readonly,
                account=self.config.account or "",
            )
        except ConnectionRefusedError:
            raise IBConnectionError(
                "Connection refused. Is TWS/IB Gateway running? "
                "Check that API connections are enabled in "
                "TWS → Edit → Global Configuration → API → Settings.",
                host=h, port=p,
            )
        except asyncio.TimeoutError:
            raise IBConnectionError(
                "Connection timed out. TWS/Gateway may be starting up, "
                "or the port is wrong. Paper=7497/4002, Live=7496/4001.",
                host=h, port=p,
            )
        except OSError as exc:
            raise IBConnectionError(
                f"Network error: {exc}. Check host/port and firewall.",
                host=h, port=p,
            )
        except Exception as exc:
            raise IBConnectionError(
                f"Unexpected connection error: {exc}",
                host=h, port=p,
            )

        if not self._ib.isConnected():
            raise IBConnectionError(
                "Connection call returned but IB reports not connected.",
                host=h, port=p,
            )

        self._connected = True
        managed = self._ib.managedAccounts()
        logger.info(
            "Connected to IB. Server version: %s. Managed accounts: %s",
            self._ib.client.serverVersion() if self._ib.client else "?",
            managed,
        )

    async def disconnect(self) -> None:
        """Disconnect from TWS/Gateway."""
        if self._connected:
            self._ib.disconnectedEvent -= self._on_disconnect
            self._ib.disconnect()
            self._connected = False
            logger.info("Disconnected from IB.")

    def _on_disconnect(self) -> None:
        """Handle unexpected disconnection — attempt auto-reconnect with backoff."""
        if not self._connected:
            return

        self._connected = False
        logger.warning("IB connection lost. Attempting auto-reconnect...")

        for attempt, delay in enumerate(self.RECONNECT_DELAYS, 1):
            logger.info("Reconnect attempt %d/%d in %ds...", attempt, len(self.RECONNECT_DELAYS), delay)
            import time as _time
            _time.sleep(delay)
            try:
                self._ib.connect(
                    host=self.config.host, port=self.config.port,
                    clientId=self.config.client_id, timeout=self.config.timeout,
                    readonly=self.config.readonly, account=self.config.account or "",
                )
                if self._ib.isConnected():
                    self._connected = True
                    self._reconnect_count += 1
                    logger.info("Reconnected (attempt %d, total: %d)", attempt, self._reconnect_count)
                    return
            except Exception as exc:
                logger.warning("Reconnect attempt %d failed: %s", attempt, exc)

        logger.error("Failed to reconnect after %d attempts.", len(self.RECONNECT_DELAYS))

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ib.isConnected()

    def _require_connected(self) -> None:
        if not self.is_connected:
            raise IBConnectionError("Not connected. Call connect() first.")

    # ──────────────────────────────────────────
    # Contract resolution
    # ──────────────────────────────────────────

    async def resolve_contract(
        self, symbol: str, exchange: str = "ASX", currency: str = "AUD",
    ) -> Contract:
        """Resolve and qualify an ASX equity contract. Results are cached."""
        self._require_connected()

        cache_key = f"{symbol}:{exchange}:{currency}"
        if cache_key in self._contract_cache:
            return self._contract_cache[cache_key]

        contract = Stock(symbol, exchange, currency)
        try:
            qualified = await self._ib.qualifyContractsAsync(contract)
        except Exception as exc:
            raise IBContractError(symbol, str(exc))

        if not qualified:
            raise IBContractError(
                symbol, f"No contract found on {exchange} in {currency}."
            )

        resolved = qualified[0]
        if resolved.conId == 0:
            raise IBContractError(symbol, "conId=0. Symbol may be ambiguous.")

        self._contract_cache[cache_key] = resolved
        logger.info("Contract resolved: %s → conId=%d", symbol, resolved.conId)
        return resolved

    # ──────────────────────────────────────────
    # Account data
    # ──────────────────────────────────────────

    async def get_account_summary(self) -> dict[str, Any]:
        """Get account summary: equity, buying power, unrealised PnL, etc."""
        self._require_connected()
        account = self.config.account or ""
        summary: dict[str, Any] = {"account": account, "mode": self.config.mode}

        try:
            values: list[AccountValue] = self._ib.accountValues(account=account)
            if not values:
                self._ib.reqAccountUpdates(subscribe=True, account=account)
                await asyncio.sleep(1)
                values = self._ib.accountValues(account=account)
                self._ib.reqAccountUpdates(subscribe=False, account=account)
        except Exception as exc:
            raise IBAdapterError(f"Account summary request failed: {exc}")

        key_fields = {
            "NetLiquidation", "TotalCashValue", "BuyingPower",
            "GrossPositionValue", "UnrealizedPnL", "RealizedPnL",
            "AvailableFunds", "MaintMarginReq", "ExcessLiquidity", "Cushion",
        }
        for av in values:
            if av.tag in key_fields and av.currency in ("AUD", "BASE", ""):
                try:
                    summary[av.tag] = float(av.value)
                except (ValueError, TypeError):
                    summary[av.tag] = av.value

        logger.info("Account summary: %d fields retrieved", len(summary))
        return summary

    async def get_positions(self) -> list[dict[str, Any]]:
        """Get all current positions."""
        self._require_connected()
        positions: list[Position] = self._ib.positions(account=self.config.account or "")

        result: list[dict[str, Any]] = []
        for pos in positions:
            if pos.position == 0:
                continue
            result.append({
                "symbol": pos.contract.symbol,
                "exchange": pos.contract.exchange or pos.contract.primaryExchange,
                "currency": pos.contract.currency,
                "quantity": int(pos.position),
                "avg_cost": float(pos.avgCost),
                "market_value": float(pos.position * pos.avgCost),
            })

        logger.info("Positions: %d open", len(result))
        return result

    # ──────────────────────────────────────────
    # Market data
    # ──────────────────────────────────────────

    async def get_market_data(self, symbol: str) -> dict[str, Any]:
        """Get a market data snapshot for an ASX equity."""
        self._require_connected()
        contract = await self.resolve_contract(symbol)

        try:
            ticker: Ticker = self._ib.reqMktData(contract, snapshot=True)
            for _ in range(50):
                await asyncio.sleep(0.1)
                if ticker.last is not None or ticker.bid is not None:
                    break
        except Exception as exc:
            raise IBMarketDataError(symbol, str(exc))

        self._ib.cancelMktData(contract)

        bid = _nan_to_none(ticker.bid)
        ask = _nan_to_none(ticker.ask)
        last = _nan_to_none(ticker.last)
        close = _nan_to_none(ticker.close)
        volume = ticker.volume if ticker.volume and ticker.volume > 0 else None

        if bid is None and ask is None and last is None:
            raise IBMarketDataError(
                symbol,
                "No data received. Check ASX market data subscription."
            )

        result = {
            "symbol": symbol, "bid": bid, "ask": ask, "last": last,
            "close": close, "volume": int(volume) if volume else None,
            "high": _nan_to_none(ticker.high), "low": _nan_to_none(ticker.low),
            "halted": _nan_to_none(ticker.halted),
        }

        if bid is not None and ask is not None and bid > 0:
            result["spread"] = round(ask - bid, 4)
            result["spread_bps"] = round((ask - bid) / bid * 10000, 1)

        logger.info("Quote %s: bid=%s ask=%s last=%s vol=%s", symbol, bid, ask, last, volume)
        return result

    # ──────────────────────────────────────────
    # Order placement — all types
    # ──────────────────────────────────────────

    async def place_market_order(self, symbol: str, side: str, qty: int) -> Trade:
        """Place a market order."""
        self._require_connected()
        self._require_writable()
        contract = await self.resolve_contract(symbol)
        order = MarketOrder(side.upper(), qty)
        return await self._submit_order(contract, order, symbol)

    async def place_limit_order(
        self, symbol: str, side: str, qty: int, limit_price: float,
    ) -> Trade:
        """Place a limit order."""
        self._require_connected()
        self._require_writable()
        contract = await self.resolve_contract(symbol)
        order = LimitOrder(side.upper(), qty, limit_price)
        return await self._submit_order(contract, order, symbol)

    async def place_stop_order(
        self, symbol: str, side: str, qty: int, stop_price: float,
    ) -> Trade:
        """Place a stop (market) order. Triggers a market order when stop_price is hit."""
        self._require_connected()
        self._require_writable()
        contract = await self.resolve_contract(symbol)
        order = StopOrder(side.upper(), qty, stop_price)
        return await self._submit_order(contract, order, symbol)

    async def place_stop_limit_order(
        self, symbol: str, side: str, qty: int,
        stop_price: float, limit_price: float,
    ) -> Trade:
        """Place a stop-limit order. When stop_price is hit, a limit order at limit_price is created."""
        self._require_connected()
        self._require_writable()
        contract = await self.resolve_contract(symbol)
        order = IBOrder(
            action=side.upper(),
            totalQuantity=qty,
            orderType="STP LMT",
            lmtPrice=limit_price,
            auxPrice=stop_price,
        )
        return await self._submit_order(contract, order, symbol)

    async def place_bracket_order(
        self, symbol: str, side: str, qty: int,
        entry_limit_price: float,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> list[Trade]:
        """
        Place a bracket order: entry + stop-loss + take-profit as one atomic group.

        IB links the three orders via OCA (One-Cancels-All) so when the entry fills,
        the stop and target are activated. When either exit fills, the other is cancelled.

        Returns [entry_trade, stop_trade, profit_trade].
        """
        self._require_connected()
        self._require_writable()
        contract = await self.resolve_contract(symbol)

        bracket = self._ib.bracketOrder(
            action=side.upper(),
            quantity=qty,
            limitPrice=entry_limit_price,
            takeProfitPrice=take_profit_price,
            stopLossPrice=stop_loss_price,
        )

        trades: list[Trade] = []
        for order in bracket:
            trade = self._ib.placeOrder(contract, order)
            trades.append(trade)

        logger.info(
            "Bracket order placed: %s %s %d — entry=%.4f stop=%.4f target=%.4f "
            "(orderIds=%s)",
            side.upper(), symbol, qty,
            entry_limit_price, stop_loss_price, take_profit_price,
            [t.order.orderId for t in trades],
        )
        return trades

    # ──────────────────────────────────────────
    # Order management
    # ──────────────────────────────────────────

    async def cancel_order(self, trade: Trade) -> None:
        """Cancel an open order."""
        self._require_connected()
        if trade.isDone():
            logger.warning(
                "Cannot cancel order %d — already %s",
                trade.order.orderId, trade.orderStatus.status,
            )
            return
        logger.info("Cancelling order %d for %s", trade.order.orderId, trade.contract.symbol)
        self._ib.cancelOrder(trade.order)

    async def cancel_order_by_id(self, order_id: int) -> bool:
        """Cancel by IB order ID. Returns True if found."""
        self._require_connected()
        for trade in self._ib.openTrades():
            if trade.order.orderId == order_id:
                self._ib.cancelOrder(trade.order)
                logger.info("Cancelled order %d", order_id)
                return True
        logger.warning("No open order with ID %d found", order_id)
        return False

    async def cancel_all_open_orders(self) -> int:
        """Cancel all open orders. Returns count cancelled."""
        self._require_connected()
        open_trades = self._ib.openTrades()
        count = 0
        for trade in open_trades:
            if not trade.isDone():
                self._ib.cancelOrder(trade.order)
                count += 1
        if count:
            logger.info("Cancelled %d open orders", count)
        return count

    def get_open_orders(self) -> list[dict[str, Any]]:
        """Get all open (non-terminal) orders."""
        self._require_connected()
        result: list[dict[str, Any]] = []
        for trade in self._ib.openTrades():
            result.append({
                "order_id": trade.order.orderId,
                "symbol": trade.contract.symbol,
                "action": trade.order.action,
                "order_type": trade.order.orderType,
                "qty": int(trade.order.totalQuantity),
                "limit_price": trade.order.lmtPrice,
                "stop_price": trade.order.auxPrice,
                "status": trade.orderStatus.status,
                "filled": int(trade.orderStatus.filled),
                "remaining": int(trade.orderStatus.remaining),
            })
        return result

    def get_all_trades(self) -> list[Trade]:
        """Get all trades (open + completed) for this session."""
        self._require_connected()
        return list(self._ib.trades())

    # ──────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────

    def _require_writable(self) -> None:
        if self.config.readonly:
            raise IBOrderError("Adapter is in read-only mode. Cannot place orders.")

    async def _submit_order(self, contract: Contract, order: Any, symbol: str) -> Trade:
        """Submit an order and return the Trade object."""
        try:
            trade: Trade = self._ib.placeOrder(contract, order)
        except Exception as exc:
            raise IBOrderError(f"Failed to place order for {symbol}: {exc}")

        logger.info(
            "Order placed: %s %s %d %s @ %s (orderId=%d)",
            order.action, symbol, int(order.totalQuantity),
            order.orderType,
            getattr(order, "lmtPrice", None) or getattr(order, "auxPrice", "MKT"),
            trade.order.orderId,
        )
        return trade

    def _on_error(
        self, reqId: int, errorCode: int, errorString: str, contract: Any = None,
    ) -> None:
        """Global error handler — logs all IB messages with proper severity."""
        if errorCode in INFO_CODES:
            logger.debug("IB info [%d]: %s", errorCode, errorString)
            return

        if errorCode in CONN_ERROR_CODES:
            logger.error("IB connection [%d]: %s — %s", errorCode, errorString, CONN_ERROR_CODES[errorCode])
        elif errorCode in MKTDATA_ERROR_CODES:
            logger.warning("IB market data [%d]: %s — %s", errorCode, errorString, MKTDATA_ERROR_CODES[errorCode])
        elif errorCode in ORDER_ERROR_CODES:
            logger.warning("IB order [%d]: %s — %s", errorCode, errorString, ORDER_ERROR_CODES[errorCode])
        elif errorCode in PERMISSION_CODES:
            logger.error("IB permissions [%d]: %s — %s", errorCode, errorString, PERMISSION_CODES[errorCode])
        else:
            logger.warning("IB error [reqId=%d, code=%d]: %s", reqId, errorCode, errorString)

    # ──────────────────────────────────────────
    # Sync wrappers (for CLI and simple scripts)
    # ──────────────────────────────────────────

    def connect_sync(self, **kwargs: Any) -> None:
        _run(self.connect(**kwargs))

    def disconnect_sync(self) -> None:
        _run(self.disconnect())

    def resolve_contract_sync(self, symbol: str, **kwargs: Any) -> Contract:
        return _run(self.resolve_contract(symbol, **kwargs))

    def get_account_summary_sync(self) -> dict[str, Any]:
        return _run(self.get_account_summary())

    def get_positions_sync(self) -> list[dict[str, Any]]:
        return _run(self.get_positions())

    def get_market_data_sync(self, symbol: str) -> dict[str, Any]:
        return _run(self.get_market_data(symbol))

    def place_market_order_sync(self, symbol: str, side: str, qty: int) -> Trade:
        return _run(self.place_market_order(symbol, side, qty))

    def place_limit_order_sync(
        self, symbol: str, side: str, qty: int, limit_price: float,
    ) -> Trade:
        return _run(self.place_limit_order(symbol, side, qty, limit_price))

    def place_stop_order_sync(
        self, symbol: str, side: str, qty: int, stop_price: float,
    ) -> Trade:
        return _run(self.place_stop_order(symbol, side, qty, stop_price))

    def place_stop_limit_order_sync(
        self, symbol: str, side: str, qty: int,
        stop_price: float, limit_price: float,
    ) -> Trade:
        return _run(self.place_stop_limit_order(symbol, side, qty, stop_price, limit_price))

    def place_bracket_order_sync(
        self, symbol: str, side: str, qty: int,
        entry_limit_price: float, stop_loss_price: float, take_profit_price: float,
    ) -> list[Trade]:
        return _run(self.place_bracket_order(
            symbol, side, qty, entry_limit_price, stop_loss_price, take_profit_price,
        ))


# ──────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────

def _run(coro: Any) -> Any:
    """Run a coroutine using ib_async's event loop integration."""
    if IB_LIB:
        return util.run(coro)
    raise IBAdapterError("No IB library available")


def _nan_to_none(val: Any) -> float | None:
    """Convert NaN/None to None for clean JSON output."""
    if val is None:
        return None
    try:
        import math
        if math.isnan(val):
            return None
    except (TypeError, ValueError):
        return None
    return float(val)
