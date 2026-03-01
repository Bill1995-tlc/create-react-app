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

    def _make_adapter(self) -> tuple:
        """Create an adapter with fully mocked IB internals."""
        # We need to mock at the ib_async import level
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
