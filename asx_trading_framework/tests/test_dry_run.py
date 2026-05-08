"""Tests for dry-run mode: DryRunBrokerAdapter and live safety gates."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import MagicMock

from ..core.types import Order, OrderStatus, OrderType, Side
from ..execution.dry_run import DryRunBlocked, DryRunBrokerAdapter
from ..execution.engine import PaperBrokerAdapter


class TestDryRunBlocked(unittest.TestCase):

    def test_exception_message(self) -> None:
        exc = DryRunBlocked("submit_order(BUY BHP)")
        self.assertIn("DRY-RUN BLOCKED", str(exc))
        self.assertIn("submit_order", str(exc))
        self.assertEqual(exc.operation, "submit_order(BUY BHP)")


class TestDryRunBrokerAdapter(unittest.TestCase):

    def setUp(self) -> None:
        self.inner = PaperBrokerAdapter()
        self.dry_run = DryRunBrokerAdapter(self.inner)

    def _make_order(self, symbol: str = "BHP") -> Order:
        return Order(
            order_id="test-001",
            symbol=symbol,
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=100,
            price=Decimal("45.00"),
        )

    def test_submit_order_raises_dry_run_blocked(self) -> None:
        order = self._make_order()
        with self.assertRaises(DryRunBlocked) as ctx:
            self.dry_run.submit_order(order)
        self.assertIn("BHP", str(ctx.exception))
        self.assertIn("submit_order", ctx.exception.operation)

    def test_cancel_order_raises_dry_run_blocked(self) -> None:
        with self.assertRaises(DryRunBlocked) as ctx:
            self.dry_run.cancel_order("test-001")
        self.assertIn("cancel_order", ctx.exception.operation)

    def test_get_positions_delegates_to_inner(self) -> None:
        self.inner._positions["BHP"] = 100
        positions = self.dry_run.get_positions()
        self.assertEqual(positions, {"BHP": 100})

    def test_get_order_status_delegates_to_inner(self) -> None:
        order = self._make_order()
        self.inner._orders["test-001"] = order
        status = self.dry_run.get_order_status("test-001")
        self.assertIsNotNone(status)

    def test_blocked_count_increments(self) -> None:
        self.assertEqual(self.dry_run.blocked_count, 0)

        order = self._make_order()
        with self.assertRaises(DryRunBlocked):
            self.dry_run.submit_order(order)
        self.assertEqual(self.dry_run.blocked_count, 1)

        with self.assertRaises(DryRunBlocked):
            self.dry_run.cancel_order("test-001")
        self.assertEqual(self.dry_run.blocked_count, 2)

    def test_inner_property(self) -> None:
        self.assertIs(self.dry_run.inner, self.inner)

    def test_multiple_orders_all_blocked(self) -> None:
        """All order attempts are blocked regardless of symbol or side."""
        for symbol in ("BHP", "CBA", "CSL"):
            order = Order(
                order_id=f"test-{symbol}",
                symbol=symbol,
                side=Side.BUY,
                order_type=OrderType.LIMIT,
                quantity=50,
                price=Decimal("100.00"),
            )
            with self.assertRaises(DryRunBlocked):
                self.dry_run.submit_order(order)

        self.assertEqual(self.dry_run.blocked_count, 3)


class TestDryRunWithMockedBroker(unittest.TestCase):
    """Test DryRunBrokerAdapter wrapping a mocked real broker."""

    def test_connect_delegates(self) -> None:
        mock_broker = MagicMock()
        mock_broker.connect.return_value = True
        dry_run = DryRunBrokerAdapter(mock_broker)
        result = dry_run.connect()
        self.assertTrue(result)
        mock_broker.connect.assert_called_once()

    def test_disconnect_delegates(self) -> None:
        mock_broker = MagicMock()
        dry_run = DryRunBrokerAdapter(mock_broker)
        dry_run.disconnect()
        mock_broker.disconnect.assert_called_once()

    def test_submit_blocked_even_with_real_broker(self) -> None:
        mock_broker = MagicMock()
        dry_run = DryRunBrokerAdapter(mock_broker)

        order = Order(
            order_id="test-001",
            symbol="BHP",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=1,
            price=Decimal("45.00"),
        )
        with self.assertRaises(DryRunBlocked):
            dry_run.submit_order(order)

        # Inner broker's submit_order should NOT have been called
        mock_broker.submit_order.assert_not_called()


class TestLiveSafetyGates(unittest.TestCase):
    """Test the _check_live_gates function."""

    def test_missing_both_gates_exits(self) -> None:
        """Without CLI flag and env var, live mode should fail."""
        import argparse
        import os

        args = argparse.Namespace(confirm_live=None)

        # Ensure env var is not set
        old_val = os.environ.pop("LIVE_TRADING_ENABLED", None)
        try:
            from ..main import _check_live_gates
            with self.assertRaises(SystemExit) as ctx:
                _check_live_gates(args)
            self.assertEqual(ctx.exception.code, 1)
        finally:
            if old_val is not None:
                os.environ["LIVE_TRADING_ENABLED"] = old_val

    def test_missing_cli_flag_exits(self) -> None:
        import argparse
        import os

        args = argparse.Namespace(confirm_live="WRONG")
        os.environ["LIVE_TRADING_ENABLED"] = "1"
        try:
            from ..main import _check_live_gates
            with self.assertRaises(SystemExit):
                _check_live_gates(args)
        finally:
            del os.environ["LIVE_TRADING_ENABLED"]

    def test_missing_env_var_exits(self) -> None:
        import argparse
        import os

        args = argparse.Namespace(confirm_live="YES_I_UNDERSTAND")
        old_val = os.environ.pop("LIVE_TRADING_ENABLED", None)
        try:
            from ..main import _check_live_gates
            with self.assertRaises(SystemExit):
                _check_live_gates(args)
        finally:
            if old_val is not None:
                os.environ["LIVE_TRADING_ENABLED"] = old_val

    def test_both_gates_pass(self) -> None:
        import argparse
        import os

        args = argparse.Namespace(confirm_live="YES_I_UNDERSTAND")
        os.environ["LIVE_TRADING_ENABLED"] = "1"
        try:
            from ..main import _check_live_gates
            # Should NOT raise
            _check_live_gates(args)
        finally:
            del os.environ["LIVE_TRADING_ENABLED"]


class TestMaxNotionalGuard(unittest.TestCase):
    """Test max-notional blocking in TradingFramework._on_signal_for_execution."""

    def test_max_notional_blocks_large_order(self) -> None:
        from ..core.config import FrameworkConfig
        from ..core.events import Event, EventBus, EventType
        from ..core.types import Signal, SignalAction
        from ..main import TradingFramework
        from datetime import datetime

        config = FrameworkConfig()
        # Low max notional to trigger blocking
        fw = TradingFramework(config, mode="paper", max_notional=Decimal("100"))

        # Create a signal that would produce notional > $100
        signal = Signal(
            strategy_id="test",
            symbol="BHP",
            action=SignalAction.ENTER_LONG,
            timestamp=datetime.utcnow(),
            price=Decimal("45.00"),
            quantity=10,  # 10 * 45 = $450 > $100
            stop_loss=Decimal("44.00"),
        )

        # The signal should be blocked by max-notional
        # We test indirectly: after publishing, no orders should exist
        fw.event_bus.publish(Event(
            event_type=EventType.SIGNAL,
            data={"signal": signal},
            source="test",
        ))

        # No orders should have been created
        self.assertEqual(len(fw.execution_engine.all_orders), 0)


if __name__ == "__main__":
    unittest.main()
