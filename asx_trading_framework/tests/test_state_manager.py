"""Tests for the StateManager — position tracking, PnL, slippage, persistence."""

from __future__ import annotations

import json
import shutil
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ..core.events import EventBus, EventType, Event
from ..core.types import Fill, Order, OrderType, OrderStatus, Position, Side, TimeInForce
from ..state.manager import StateManager


TEMP_STATE_DIR = "/tmp/asx_test_state"


def make_fill(
    symbol: str = "BHP",
    side: Side = Side.BUY,
    quantity: int = 100,
    price: float = 45.0,
    commission: float = 10.0,
) -> Fill:
    return Fill(
        fill_id=f"fill-{symbol}-{side.value}",
        order_id=f"order-{symbol}",
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=Decimal(str(price)),
        commission=Decimal(str(commission)),
        timestamp=datetime.utcnow(),
    )


def make_order(
    symbol: str = "BHP",
    side: Side = Side.BUY,
    price: float = 45.0,
) -> Order:
    return Order(
        order_id=f"order-{symbol}",
        symbol=symbol,
        side=side,
        order_type=OrderType.LIMIT,
        quantity=100,
        price=Decimal(str(price)),
        time_in_force=TimeInForce.DAY,
    )


class TestStateManager(unittest.TestCase):

    def setUp(self) -> None:
        self.event_bus = EventBus()
        # Clean temp dir
        state_dir = Path(TEMP_STATE_DIR)
        if state_dir.exists():
            shutil.rmtree(state_dir)
        self.manager = StateManager(self.event_bus, persist_dir=TEMP_STATE_DIR)

    def tearDown(self) -> None:
        state_dir = Path(TEMP_STATE_DIR)
        if state_dir.exists():
            shutil.rmtree(state_dir)

    def _publish_fill(self, fill: Fill, order: Order | None = None) -> None:
        """Simulate a fill event through the event bus."""
        data: dict = {"fill": fill}
        if order is not None:
            data["order"] = order
        self.event_bus.publish(Event(
            event_type=EventType.ORDER_FILLED,
            data=data,
            source="test",
        ))

    # ──────────────────────────────────────────
    # Position tracking
    # ──────────────────────────────────────────

    def test_new_position_on_buy(self) -> None:
        """A fill opens a new position."""
        fill = make_fill(side=Side.BUY, quantity=100, price=45.0)
        self._publish_fill(fill)

        positions = self.manager.positions
        self.assertIn("BHP", positions)
        self.assertEqual(positions["BHP"].quantity, 100)
        self.assertEqual(positions["BHP"].average_entry_price, Decimal("45.0"))

    def test_new_short_position(self) -> None:
        """A sell fill opens a short position."""
        fill = make_fill(side=Side.SELL, quantity=50, price=45.0)
        self._publish_fill(fill)

        positions = self.manager.positions
        self.assertIn("BHP", positions)
        self.assertEqual(positions["BHP"].quantity, -50)

    def test_close_long_position(self) -> None:
        """Selling closes a long position and records a trade."""
        buy = make_fill(side=Side.BUY, quantity=100, price=45.0)
        self._publish_fill(buy)

        sell = make_fill(side=Side.SELL, quantity=100, price=46.0)
        self._publish_fill(sell)

        self.assertEqual(len(self.manager.positions), 0)
        self.assertEqual(len(self.manager.completed_trades), 1)

        trade = self.manager.completed_trades[0]
        self.assertEqual(trade.entry_price, Decimal("45.0"))
        self.assertEqual(trade.exit_price, Decimal("46.0"))
        # PnL = (46-45)*100 - 10 commission = 90
        self.assertEqual(trade.pnl, Decimal("90.0"))

    def test_close_short_position(self) -> None:
        """Buying closes a short position."""
        sell = make_fill(side=Side.SELL, quantity=100, price=46.0)
        self._publish_fill(sell)

        buy = make_fill(side=Side.BUY, quantity=100, price=45.0)
        self._publish_fill(buy)

        self.assertEqual(len(self.manager.positions), 0)
        trade = self.manager.completed_trades[0]
        # Short PnL = (entry - exit) * qty - commission = (46-45)*100 - 10 = 90
        self.assertEqual(trade.pnl, Decimal("90.0"))
        self.assertEqual(trade.side, Side.SELL)

    def test_add_to_position_averages(self) -> None:
        """Adding to an existing position recalculates average entry price."""
        fill1 = make_fill(side=Side.BUY, quantity=100, price=44.0, commission=10)
        self._publish_fill(fill1)

        fill2 = make_fill(side=Side.BUY, quantity=100, price=46.0, commission=10)
        self._publish_fill(fill2)

        pos = self.manager.positions["BHP"]
        self.assertEqual(pos.quantity, 200)
        # Average: (44*100 + 46*100) / 200 = 45
        self.assertEqual(pos.average_entry_price, Decimal("45.0"))

    # ──────────────────────────────────────────
    # PnL and equity
    # ──────────────────────────────────────────

    def test_equity_updates_on_close(self) -> None:
        """Equity increases on profitable trade."""
        initial = self.manager.equity

        buy = make_fill(side=Side.BUY, quantity=100, price=45.0, commission=0)
        self._publish_fill(buy)

        sell = make_fill(side=Side.SELL, quantity=100, price=46.0, commission=0)
        self._publish_fill(sell)

        # PnL = (46-45)*100 = 100
        self.assertEqual(self.manager.equity, initial + Decimal("100"))

    def test_daily_pnl_accumulates(self) -> None:
        """Daily PnL accumulates across multiple trades."""
        buy1 = make_fill(side=Side.BUY, quantity=100, price=45.0, commission=0)
        self._publish_fill(buy1)
        sell1 = make_fill(side=Side.SELL, quantity=100, price=46.0, commission=0)
        self._publish_fill(sell1)

        buy2 = make_fill(symbol="CBA", side=Side.BUY, quantity=50, price=100.0, commission=0)
        self._publish_fill(buy2)
        sell2 = make_fill(symbol="CBA", side=Side.SELL, quantity=50, price=101.0, commission=0)
        self._publish_fill(sell2)

        # BHP: +100, CBA: +50 = 150
        self.assertEqual(self.manager.daily_pnl, Decimal("150"))

    def test_commission_tracking(self) -> None:
        """Total commission is tracked."""
        fill = make_fill(commission=11.0)
        self._publish_fill(fill)
        self.assertEqual(self.manager.total_commission, Decimal("11.0"))

    def test_reset_daily(self) -> None:
        """reset_daily() zeroes daily PnL."""
        buy = make_fill(side=Side.BUY, quantity=100, price=45.0, commission=0)
        self._publish_fill(buy)
        sell = make_fill(side=Side.SELL, quantity=100, price=46.0, commission=0)
        self._publish_fill(sell)

        self.assertNotEqual(self.manager.daily_pnl, Decimal("0"))
        self.manager.reset_daily()
        self.assertEqual(self.manager.daily_pnl, Decimal("0"))

    # ──────────────────────────────────────────
    # Slippage tracking
    # ──────────────────────────────────────────

    def test_slippage_tracked_on_close(self) -> None:
        """Slippage is computed from intended vs actual fill price."""
        order = make_order(side=Side.BUY, price=44.50)
        buy = make_fill(side=Side.BUY, quantity=100, price=44.55, commission=0)
        self._publish_fill(buy, order=order)

        sell = make_fill(side=Side.SELL, quantity=100, price=46.0, commission=0)
        self._publish_fill(sell)

        trade = self.manager.completed_trades[0]
        # Slippage: bought at 44.55 instead of 44.50 → 0.05 * 100 = 5.00
        self.assertEqual(trade.slippage, Decimal("5.00"))

    def test_no_slippage_when_no_order_price(self) -> None:
        """Slippage is 0 when no intended price was recorded."""
        buy = make_fill(side=Side.BUY, quantity=100, price=45.0, commission=0)
        self._publish_fill(buy)

        sell = make_fill(side=Side.SELL, quantity=100, price=46.0, commission=0)
        self._publish_fill(sell)

        trade = self.manager.completed_trades[0]
        self.assertEqual(trade.slippage, Decimal("0"))

    # ──────────────────────────────────────────
    # Persistence
    # ──────────────────────────────────────────

    def test_persist_and_load(self) -> None:
        """State persists to disk and can be recovered."""
        fill = make_fill(side=Side.BUY, quantity=100, price=45.0)
        self._publish_fill(fill)

        # Create a new manager and load state
        manager2 = StateManager(EventBus(), persist_dir=TEMP_STATE_DIR)
        loaded = manager2.load_state()
        self.assertTrue(loaded)
        self.assertIn("BHP", manager2.positions)
        self.assertEqual(manager2.positions["BHP"].quantity, 100)

    def test_load_nonexistent_returns_false(self) -> None:
        """load_state() returns False when no file exists."""
        manager = StateManager(EventBus(), persist_dir="/tmp/asx_test_empty")
        self.assertFalse(manager.load_state())
        shutil.rmtree("/tmp/asx_test_empty", ignore_errors=True)

    def test_set_initial_equity(self) -> None:
        self.manager.set_initial_equity(Decimal("50000"))
        self.assertEqual(self.manager.equity, Decimal("50000"))

    # ──────────────────────────────────────────
    # Events
    # ──────────────────────────────────────────

    def test_position_opened_event(self) -> None:
        """POSITION_OPENED event is published."""
        events: list[EventType] = []
        self.event_bus.subscribe(EventType.POSITION_OPENED, lambda e: events.append(e.event_type))

        fill = make_fill(side=Side.BUY, quantity=100, price=45.0)
        self._publish_fill(fill)
        self.assertIn(EventType.POSITION_OPENED, events)

    def test_position_closed_event(self) -> None:
        """POSITION_CLOSED event is published."""
        events: list[EventType] = []
        self.event_bus.subscribe(EventType.POSITION_CLOSED, lambda e: events.append(e.event_type))

        buy = make_fill(side=Side.BUY, quantity=100, price=45.0)
        self._publish_fill(buy)

        sell = make_fill(side=Side.SELL, quantity=100, price=46.0)
        self._publish_fill(sell)
        self.assertIn(EventType.POSITION_CLOSED, events)


if __name__ == "__main__":
    unittest.main()
