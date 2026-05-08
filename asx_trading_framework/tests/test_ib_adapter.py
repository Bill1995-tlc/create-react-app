"""
Tests for the IB adapter — runs WITHOUT a live IB connection.

All IB interactions are mocked. These tests verify:
- Config loading from env vars
- Contract creation and caching
- Order submission logic
- Error classification
- CLI argument parsing
"""

from __future__ import annotations

import asyncio
import os
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from ..broker.ib.config import IBConfig, PORT_MAP
from ..broker.ib.errors import (
    IBAdapterError,
    IBConnectionError,
    IBContractError,
    IBMarketDataError,
    IBOrderError,
)
from ..broker.ib.cli import build_parser, COMMAND_MAP


# ──────────────────────────────────────────────
# Config tests
# ──────────────────────────────────────────────

class TestIBConfig(unittest.TestCase):
    """Test IBConfig loading and defaults."""

    def test_defaults(self) -> None:
        """Default config is paper trading on localhost."""
        cfg = IBConfig()
        self.assertEqual(cfg.host, "127.0.0.1")
        self.assertEqual(cfg.port, 7497)
        self.assertEqual(cfg.client_id, 1)
        self.assertEqual(cfg.mode, "paper")
        self.assertTrue(cfg.is_paper)
        self.assertFalse(cfg.is_live)
        self.assertFalse(cfg.readonly)

    def test_from_env(self) -> None:
        """Config reads from environment variables."""
        env = {
            "IB_HOST": "192.168.1.100",
            "IB_PORT": "4001",
            "IB_CLIENT_ID": "5",
            "IB_ACCOUNT": "DU123456",
            "IB_MODE": "gateway_live",
            "IB_TIMEOUT": "20",
            "IB_READONLY": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = IBConfig.from_env()
            self.assertEqual(cfg.host, "192.168.1.100")
            self.assertEqual(cfg.port, 4001)
            self.assertEqual(cfg.client_id, 5)
            self.assertEqual(cfg.account, "DU123456")
            self.assertEqual(cfg.mode, "gateway_live")
            self.assertEqual(cfg.timeout, 20)
            self.assertTrue(cfg.readonly)
            self.assertTrue(cfg.is_live)
            self.assertFalse(cfg.is_paper)

    def test_mode_sets_default_port(self) -> None:
        """When IB_PORT is not set, port is derived from IB_MODE."""
        for mode, expected_port in PORT_MAP.items():
            with patch.dict(os.environ, {"IB_MODE": mode}, clear=False):
                # Remove IB_PORT to test mode-based default
                env = os.environ.copy()
                env.pop("IB_PORT", None)
                with patch.dict(os.environ, env, clear=True):
                    os.environ["IB_MODE"] = mode
                    cfg = IBConfig.from_env()
                    self.assertEqual(cfg.port, expected_port, f"mode={mode}")

    def test_explicit_port_overrides_mode(self) -> None:
        """Explicit IB_PORT takes precedence over mode default."""
        with patch.dict(os.environ, {"IB_MODE": "paper", "IB_PORT": "9999"}, clear=False):
            cfg = IBConfig.from_env()
            self.assertEqual(cfg.port, 9999)

    def test_describe(self) -> None:
        """describe() returns a readable string."""
        cfg = IBConfig()
        desc = cfg.describe()
        self.assertIn("127.0.0.1", desc)
        self.assertIn("7497", desc)
        self.assertIn("paper", desc)


# ──────────────────────────────────────────────
# Error tests
# ──────────────────────────────────────────────

class TestIBErrors(unittest.TestCase):
    """Test custom exception types."""

    def test_connection_error(self) -> None:
        err = IBConnectionError("refused", host="localhost", port=7497)
        self.assertIn("refused", str(err))
        self.assertIn("localhost", str(err))
        self.assertEqual(err.port, 7497)

    def test_contract_error(self) -> None:
        err = IBContractError("XYZ", "not found")
        self.assertIn("XYZ", str(err))
        self.assertIn("not found", str(err))
        self.assertEqual(err.symbol, "XYZ")

    def test_order_error(self) -> None:
        err = IBOrderError("insufficient funds", order_id=42)
        self.assertIn("insufficient funds", str(err))
        self.assertEqual(err.order_id, 42)

    def test_market_data_error(self) -> None:
        err = IBMarketDataError("BHP", "no subscription")
        self.assertIn("BHP", str(err))
        self.assertIn("no subscription", str(err))

    def test_base_hierarchy(self) -> None:
        """All errors inherit from IBAdapterError."""
        self.assertTrue(issubclass(IBConnectionError, IBAdapterError))
        self.assertTrue(issubclass(IBContractError, IBAdapterError))
        self.assertTrue(issubclass(IBOrderError, IBAdapterError))
        self.assertTrue(issubclass(IBMarketDataError, IBAdapterError))


# ──────────────────────────────────────────────
# Adapter tests (mocked IB)
# ──────────────────────────────────────────────

class TestIBAdapterMocked(unittest.TestCase):
    """Test IBAdapter with mocked ib_async."""

    @classmethod
    def setUpClass(cls) -> None:
        from ..broker.ib.adapter import IB_LIB
        if not IB_LIB:
            raise unittest.SkipTest("ib_async not installed")

    def _make_adapter(self) -> tuple:
        """Create an adapter with fully mocked IB internals."""
        from ..broker.ib.adapter import IBAdapter

        config = IBConfig(host="127.0.0.1", port=7497, client_id=1, mode="paper")
        adapter = IBAdapter(config)

        # Mock the internal IB object
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True
        mock_ib.managedAccounts.return_value = ["DU123456"]
        mock_ib.client.serverVersion.return_value = 163
        adapter._ib = mock_ib
        adapter._connected = True

        return adapter, mock_ib

    def test_is_connected(self) -> None:
        adapter, mock_ib = self._make_adapter()
        self.assertTrue(adapter.is_connected)
        mock_ib.isConnected.return_value = False
        self.assertFalse(adapter.is_connected)

    def test_require_connected_raises(self) -> None:
        adapter, mock_ib = self._make_adapter()
        adapter._connected = False
        with self.assertRaises(IBConnectionError):
            adapter._require_connected()

    def test_contract_cache(self) -> None:
        """Resolved contracts are cached."""
        adapter, mock_ib = self._make_adapter()

        # Simulate a qualified contract
        mock_contract = MagicMock()
        mock_contract.conId = 12345
        mock_contract.exchange = "ASX"
        mock_ib.qualifyContractsAsync = AsyncMock(return_value=[mock_contract])

        loop = asyncio.new_event_loop()
        try:
            # First call should hit IB
            result = loop.run_until_complete(adapter.resolve_contract("BHP"))
            self.assertEqual(result.conId, 12345)
            self.assertEqual(mock_ib.qualifyContractsAsync.call_count, 1)

            # Second call should use cache
            result2 = loop.run_until_complete(adapter.resolve_contract("BHP"))
            self.assertEqual(result2.conId, 12345)
            self.assertEqual(mock_ib.qualifyContractsAsync.call_count, 1)  # No additional call
        finally:
            loop.close()

    def test_resolve_contract_empty_result(self) -> None:
        """Empty qualification result raises IBContractError."""
        adapter, mock_ib = self._make_adapter()
        mock_ib.qualifyContractsAsync = AsyncMock(return_value=[])

        loop = asyncio.new_event_loop()
        try:
            with self.assertRaises(IBContractError) as ctx:
                loop.run_until_complete(adapter.resolve_contract("INVALID"))
            self.assertIn("INVALID", str(ctx.exception))
        finally:
            loop.close()

    def test_get_positions(self) -> None:
        """Positions are returned as dicts."""
        adapter, mock_ib = self._make_adapter()

        mock_pos = MagicMock()
        mock_pos.position = 100
        mock_pos.avgCost = 45.50
        mock_pos.contract.symbol = "BHP"
        mock_pos.contract.exchange = "ASX"
        mock_pos.contract.primaryExchange = "ASX"
        mock_pos.contract.currency = "AUD"

        mock_ib.positions.return_value = [mock_pos]

        loop = asyncio.new_event_loop()
        try:
            positions = loop.run_until_complete(adapter.get_positions())
            self.assertEqual(len(positions), 1)
            self.assertEqual(positions[0]["symbol"], "BHP")
            self.assertEqual(positions[0]["quantity"], 100)
            self.assertEqual(positions[0]["avg_cost"], 45.50)
        finally:
            loop.close()

    def test_zero_positions_excluded(self) -> None:
        """Positions with qty=0 are excluded."""
        adapter, mock_ib = self._make_adapter()

        mock_pos = MagicMock()
        mock_pos.position = 0
        mock_ib.positions.return_value = [mock_pos]

        loop = asyncio.new_event_loop()
        try:
            positions = loop.run_until_complete(adapter.get_positions())
            self.assertEqual(len(positions), 0)
        finally:
            loop.close()

    def test_readonly_blocks_orders(self) -> None:
        """Orders are blocked in readonly mode."""
        from ..broker.ib.adapter import IBAdapter

        config = IBConfig(readonly=True)
        adapter = IBAdapter(config)
        adapter._connected = True
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True
        adapter._ib = mock_ib

        loop = asyncio.new_event_loop()
        try:
            with self.assertRaises(IBOrderError) as ctx:
                loop.run_until_complete(adapter.place_market_order("BHP", "BUY", 10))
            self.assertIn("read-only", str(ctx.exception))
        finally:
            loop.close()

    def test_error_handler_classification(self) -> None:
        """Error handler classifies codes correctly."""
        adapter, _ = self._make_adapter()

        # Info code should not raise, just log
        adapter._on_error(0, 2104, "Market data farm connected")

        # Connection error
        adapter._on_error(0, 502, "Connection refused")

        # Market data error
        adapter._on_error(0, 354, "No subscription")

        # Order error
        adapter._on_error(0, 201, "Order rejected")

    def test_get_open_orders(self) -> None:
        """Open orders are returned as dicts."""
        adapter, mock_ib = self._make_adapter()

        mock_trade = MagicMock()
        mock_trade.order.orderId = 1
        mock_trade.contract.symbol = "CBA"
        mock_trade.order.action = "BUY"
        mock_trade.order.orderType = "LMT"
        mock_trade.order.totalQuantity = 50
        mock_trade.order.lmtPrice = 110.0
        mock_trade.order.auxPrice = 0.0
        mock_trade.orderStatus.status = "Submitted"
        mock_trade.orderStatus.filled = 0
        mock_trade.orderStatus.remaining = 50

        mock_ib.openTrades.return_value = [mock_trade]

        orders = adapter.get_open_orders()
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["symbol"], "CBA")
        self.assertEqual(orders[0]["action"], "BUY")
        self.assertEqual(orders[0]["qty"], 50)


# ──────────────────────────────────────────────
# Order type tests (STOP / STOP_LIMIT / bracket / cancel)
# ──────────────────────────────────────────────

class TestIBAdapterOrders(unittest.TestCase):
    """Verify the order-placement surface added for live trading."""

    @classmethod
    def setUpClass(cls) -> None:
        from ..broker.ib.adapter import IB_LIB
        if not IB_LIB:
            raise unittest.SkipTest("ib_async not installed")

    def _make_adapter(self):
        """Adapter with a connected mock IB and BHP contract pre-cached."""
        from ..broker.ib.adapter import IBAdapter

        adapter = IBAdapter(IBConfig(host="127.0.0.1", port=7497, mode="paper"))
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True
        adapter._ib = mock_ib
        adapter._connected = True

        # Pre-cache BHP so resolve_contract() doesn't hit the mock IB.
        bhp = MagicMock()
        bhp.conId = 12345
        bhp.symbol = "BHP"
        adapter._contract_cache["BHP:ASX:AUD"] = bhp

        return adapter, mock_ib, bhp

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    # ──────────────────────────
    # STOP order
    # ──────────────────────────

    def test_place_stop_order_uses_StopOrder(self) -> None:
        """place_stop_order builds a StopOrder with correct side/qty/stop and submits it."""
        from ..broker.ib.adapter import StopOrder

        adapter, mock_ib, bhp = self._make_adapter()
        mock_ib.placeOrder.return_value = MagicMock(order=MagicMock(orderId=1))

        self._run(adapter.place_stop_order("BHP", "sell", 100, stop_price=44.50))

        mock_ib.placeOrder.assert_called_once()
        contract_arg, order_arg = mock_ib.placeOrder.call_args[0]
        self.assertIs(contract_arg, bhp)
        self.assertIsInstance(order_arg, StopOrder)
        self.assertEqual(order_arg.action, "SELL")
        self.assertEqual(order_arg.totalQuantity, 100)
        self.assertEqual(order_arg.auxPrice, 44.50)

    # ──────────────────────────
    # STOP_LIMIT order
    # ──────────────────────────

    def test_place_stop_limit_order_uses_STP_LMT(self) -> None:
        """place_stop_limit_order builds an IBOrder with type=STP LMT, lmtPrice, and auxPrice (stop)."""
        adapter, mock_ib, bhp = self._make_adapter()
        mock_ib.placeOrder.return_value = MagicMock(order=MagicMock(orderId=1))

        self._run(adapter.place_stop_limit_order(
            "BHP", "sell", 100, stop_price=44.50, limit_price=44.40,
        ))

        mock_ib.placeOrder.assert_called_once()
        _, order_arg = mock_ib.placeOrder.call_args[0]
        self.assertEqual(order_arg.orderType, "STP LMT")
        self.assertEqual(order_arg.action, "SELL")
        self.assertEqual(order_arg.totalQuantity, 100)
        self.assertEqual(order_arg.auxPrice, 44.50)   # stop trigger
        self.assertEqual(order_arg.lmtPrice, 44.40)   # limit price after trigger

    # ──────────────────────────
    # Bracket order
    # ──────────────────────────

    def test_place_bracket_order_passes_three_orders(self) -> None:
        """Bracket order calls bracketOrder() with right args and submits all 3 children."""
        adapter, mock_ib, bhp = self._make_adapter()

        # bracketOrder returns three IB Order objects (parent, takeProfit, stopLoss)
        parent, tp, sl = MagicMock(), MagicMock(), MagicMock()
        mock_ib.bracketOrder.return_value = [parent, tp, sl]

        # placeOrder returns a Trade per call
        trade1 = MagicMock(order=MagicMock(orderId=1))
        trade2 = MagicMock(order=MagicMock(orderId=2))
        trade3 = MagicMock(order=MagicMock(orderId=3))
        mock_ib.placeOrder.side_effect = [trade1, trade2, trade3]

        trades = self._run(adapter.place_bracket_order(
            "BHP", "buy", 100,
            entry_limit_price=45.00,
            stop_loss_price=44.00,
            take_profit_price=46.50,
        ))

        # bracketOrder was asked to build the three legs with correct prices
        mock_ib.bracketOrder.assert_called_once_with(
            action="BUY", quantity=100,
            limitPrice=45.00, takeProfitPrice=46.50, stopLossPrice=44.00,
        )
        # All three legs were placed with the BHP contract
        self.assertEqual(mock_ib.placeOrder.call_count, 3)
        for call in mock_ib.placeOrder.call_args_list:
            self.assertIs(call.args[0], bhp)
        self.assertEqual(trades, [trade1, trade2, trade3])

    # ──────────────────────────
    # Readonly mode blocks new order types
    # ──────────────────────────

    def test_readonly_blocks_stop_limit_and_bracket(self) -> None:
        """Read-only adapter rejects STOP, STOP_LIMIT, and bracket orders before any IB call."""
        from ..broker.ib.adapter import IBAdapter

        adapter = IBAdapter(IBConfig(readonly=True))
        adapter._connected = True
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True
        adapter._ib = mock_ib
        adapter._contract_cache["BHP:ASX:AUD"] = MagicMock()

        for coro_factory in (
            lambda: adapter.place_stop_order("BHP", "SELL", 1, 44.0),
            lambda: adapter.place_stop_limit_order("BHP", "SELL", 1, 44.0, 43.9),
            lambda: adapter.place_bracket_order("BHP", "BUY", 1, 45.0, 44.0, 46.0),
        ):
            with self.assertRaises(IBOrderError):
                self._run(coro_factory())
        mock_ib.placeOrder.assert_not_called()
        mock_ib.bracketOrder.assert_not_called()

    # ──────────────────────────
    # cancel_all_open_orders
    # ──────────────────────────

    def test_cancel_all_skips_done_trades(self) -> None:
        """cancel_all_open_orders cancels only non-done trades and returns the count."""
        adapter, mock_ib, _ = self._make_adapter()

        live_a = MagicMock()
        live_a.isDone.return_value = False
        live_b = MagicMock()
        live_b.isDone.return_value = False
        done = MagicMock()
        done.isDone.return_value = True

        mock_ib.openTrades.return_value = [live_a, done, live_b]

        count = self._run(adapter.cancel_all_open_orders())

        self.assertEqual(count, 2)
        cancelled = [c.args[0] for c in mock_ib.cancelOrder.call_args_list]
        self.assertIn(live_a.order, cancelled)
        self.assertIn(live_b.order, cancelled)
        self.assertNotIn(done.order, cancelled)

    def test_cancel_all_zero_when_nothing_open(self) -> None:
        adapter, mock_ib, _ = self._make_adapter()
        mock_ib.openTrades.return_value = []

        count = self._run(adapter.cancel_all_open_orders())

        self.assertEqual(count, 0)
        mock_ib.cancelOrder.assert_not_called()

    # ──────────────────────────
    # get_all_trades
    # ──────────────────────────

    def test_get_all_trades_returns_session_trades(self) -> None:
        adapter, mock_ib, _ = self._make_adapter()
        t1, t2 = MagicMock(), MagicMock()
        mock_ib.trades.return_value = [t1, t2]
        self.assertEqual(adapter.get_all_trades(), [t1, t2])


# ──────────────────────────────────────────────
# Fill / order-status callback tests
# ──────────────────────────────────────────────

class TestIBAdapterCallbacks(unittest.TestCase):
    """Verify on_fill / on_order_status registration and dispatch."""

    @classmethod
    def setUpClass(cls) -> None:
        from ..broker.ib.adapter import IB_LIB
        if not IB_LIB:
            raise unittest.SkipTest("ib_async not installed")

    def _make_adapter(self):
        from ..broker.ib.adapter import IBAdapter
        adapter = IBAdapter(IBConfig(mode="paper"))
        adapter._ib = MagicMock()
        adapter._connected = True
        return adapter

    def _trade(self, status: str = "Submitted"):
        """Build a minimal mock Trade with all attributes _on_order_status touches."""
        trade = MagicMock()
        trade.orderStatus.status = status
        trade.orderStatus.filled = 0 if status != "Filled" else 100
        trade.orderStatus.remaining = 100 if status != "Filled" else 0
        trade.orderStatus.avgFillPrice = 0.0 if status != "Filled" else 45.10
        trade.order.action = "BUY"
        trade.order.orderId = 42
        trade.contract.symbol = "BHP"
        return trade

    def test_on_fill_registers_callback(self) -> None:
        adapter = self._make_adapter()
        cb = MagicMock()
        adapter.on_fill(cb)
        self.assertIn(cb, adapter._fill_callbacks)

    def test_on_order_status_registers_callback(self) -> None:
        adapter = self._make_adapter()
        cb = MagicMock()
        adapter.on_order_status(cb)
        self.assertIn(cb, adapter._order_status_callbacks)

    def test_status_dispatch_calls_status_callbacks_always(self) -> None:
        """Every status change fires order_status callbacks regardless of status."""
        adapter = self._make_adapter()
        cb = MagicMock()
        adapter.on_order_status(cb)

        for status in ("Submitted", "PreSubmitted", "Cancelled", "Filled"):
            cb.reset_mock()
            adapter._on_order_status(self._trade(status))
            cb.assert_called_once()

    def test_fill_callbacks_only_fire_on_filled(self) -> None:
        """Fill callbacks fire only when status == 'Filled'."""
        adapter = self._make_adapter()
        fill_cb = MagicMock()
        adapter.on_fill(fill_cb)

        for status in ("Submitted", "PreSubmitted", "Cancelled"):
            adapter._on_order_status(self._trade(status))
        fill_cb.assert_not_called()

        adapter._on_order_status(self._trade("Filled"))
        fill_cb.assert_called_once()

    def test_callback_exception_is_isolated(self) -> None:
        """A raising callback must not prevent other callbacks from firing."""
        adapter = self._make_adapter()

        bad = MagicMock(side_effect=RuntimeError("boom"))
        good = MagicMock()
        adapter.on_order_status(bad)
        adapter.on_order_status(good)

        # Should not raise — failure is logged and dispatch continues.
        adapter._on_order_status(self._trade("Submitted"))

        bad.assert_called_once()
        good.assert_called_once()

    def test_fill_callback_exception_isolated_from_status_callback(self) -> None:
        """A failing fill callback does not break a sibling fill callback."""
        adapter = self._make_adapter()

        bad_fill = MagicMock(side_effect=RuntimeError("boom"))
        good_fill = MagicMock()
        adapter.on_fill(bad_fill)
        adapter.on_fill(good_fill)

        adapter._on_order_status(self._trade("Filled"))

        bad_fill.assert_called_once()
        good_fill.assert_called_once()


# ──────────────────────────────────────────────
# CLI parser tests
# ──────────────────────────────────────────────

class TestIBCLI(unittest.TestCase):
    """Test CLI argument parsing (no IB connection needed)."""

    def test_parser_test_connection(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["test-connection"])
        self.assertEqual(args.command, "test-connection")

    def test_parser_account(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["account"])
        self.assertEqual(args.command, "account")

    def test_parser_positions(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["positions"])
        self.assertEqual(args.command, "positions")

    def test_parser_quote(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["quote", "BHP"])
        self.assertEqual(args.command, "quote")
        self.assertEqual(args.symbol, "BHP")

    def test_parser_buy_market(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["buy", "BHP", "--qty", "10", "--type", "market"])
        self.assertEqual(args.command, "buy")
        self.assertEqual(args.symbol, "BHP")
        self.assertEqual(args.qty, 10)
        self.assertEqual(args.type, "market")

    def test_parser_sell_limit(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "sell", "CBA", "--qty", "20", "--type", "limit", "--limit", "110.50",
        ])
        self.assertEqual(args.command, "sell")
        self.assertEqual(args.symbol, "CBA")
        self.assertEqual(args.qty, 20)
        self.assertEqual(args.type, "limit")
        self.assertAlmostEqual(args.limit, 110.50)

    def test_parser_cancel(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["cancel", "42"])
        self.assertEqual(args.command, "cancel")
        self.assertEqual(args.order_id, 42)

    def test_parser_verbose(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["-v", "account"])
        self.assertTrue(args.verbose)

    def test_all_commands_have_handlers(self) -> None:
        """Every CLI command has a handler function."""
        parser = build_parser()
        for cmd_name in COMMAND_MAP:
            args = parser.parse_args([cmd_name] if cmd_name not in ("quote", "buy", "sell", "cancel") else
                                     [cmd_name, "BHP"] if cmd_name in ("quote",) else
                                     [cmd_name, "BHP", "--qty", "1"] if cmd_name in ("buy", "sell") else
                                     [cmd_name, "1"])
            self.assertIn(args.command, COMMAND_MAP)


if __name__ == "__main__":
    unittest.main()
