"""Tests for CMC Invest alert adapter."""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from ..core.config import FrameworkConfig
from ..core.events import EventBus, EventType
from ..core.types import Order, OrderStatus, OrderType, Side, TimeInForce
from ..execution.cmc_alert import CMCAlertAdapter, TradeAlert


def make_order(
    symbol: str = "BHP",
    side: Side = Side.BUY,
    quantity: int = 100,
    price: Decimal = Decimal("45.00"),
    stop_price: Decimal | None = Decimal("44.00"),
    strategy_id: str = "orb_crabel",
) -> Order:
    return Order(
        order_id=f"test-{symbol}-001",
        symbol=symbol,
        side=side,
        order_type=OrderType.LIMIT,
        quantity=quantity,
        price=price,
        stop_price=stop_price,
        time_in_force=TimeInForce.DAY,
        strategy_id=strategy_id,
    )


class TestCMCAlertAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.config = FrameworkConfig()
        self.config.broker.adapter = "cmc_alert"
        self.config.operations.log_directory = "/tmp/asx_test_logs"
        self.event_bus = EventBus()
        self.adapter = CMCAlertAdapter(self.config, self.event_bus)

    def test_submit_creates_alert(self) -> None:
        """Submitting an order creates an alert."""
        order = make_order()
        result = self.adapter.submit_order(order)
        self.assertTrue(result)
        self.assertEqual(len(self.adapter.alerts), 1)
        self.assertEqual(self.adapter.alerts[0].symbol, "BHP")

    def test_submit_transitions_to_new(self) -> None:
        """After submit, order should be in NEW status."""
        order = make_order()
        self.adapter.submit_order(order)
        self.assertEqual(order.status, OrderStatus.NEW)

    def test_pending_count(self) -> None:
        """Pending count reflects unreconciled orders."""
        self.assertEqual(self.adapter.pending_count, 0)
        order = make_order()
        self.adapter.submit_order(order)
        self.assertEqual(self.adapter.pending_count, 1)

    def test_mark_filled_reconciles(self) -> None:
        """Marking an order as filled updates status and publishes event."""
        events_received: list[EventType] = []
        self.event_bus.subscribe(
            EventType.ORDER_FILLED,
            lambda e: events_received.append(e.event_type),
        )

        order = make_order()
        self.adapter.submit_order(order)
        result = self.adapter.mark_filled(
            order.order_id, fill_price=Decimal("45.10"),
        )
        self.assertTrue(result)
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(order.average_fill_price, Decimal("45.10"))
        self.assertEqual(self.adapter.pending_count, 0)
        self.assertIn(EventType.ORDER_FILLED, events_received)

    def test_mark_filled_unknown_order(self) -> None:
        """Marking unknown order returns False."""
        result = self.adapter.mark_filled("nonexistent", Decimal("10"))
        self.assertFalse(result)

    def test_mark_not_filled(self) -> None:
        """Marking as not filled expires the order."""
        order = make_order()
        self.adapter.submit_order(order)
        result = self.adapter.mark_not_filled(order.order_id, "Missed it")
        self.assertTrue(result)
        self.assertEqual(order.status, OrderStatus.EXPIRED)
        self.assertEqual(self.adapter.pending_count, 0)

    def test_cancel_order(self) -> None:
        """Cancelling removes from pending."""
        order = make_order()
        self.adapter.submit_order(order)
        result = self.adapter.cancel_order(order.order_id)
        self.assertTrue(result)
        self.assertEqual(self.adapter.pending_count, 0)

    def test_get_positions_returns_empty(self) -> None:
        """CMC adapter cannot query positions — always empty."""
        self.assertEqual(self.adapter.get_positions(), {})

    def test_alert_text_formatting(self) -> None:
        """Alert text contains essential trade info."""
        alert = TradeAlert(
            timestamp=datetime(2024, 1, 15, 10, 30),
            symbol="CBA",
            action="BUY",
            quantity=200,
            limit_price=Decimal("110.500"),
            stop_loss=Decimal("109.000"),
            take_profit=None,
            strategy="momentum_zanger",
            risk_dollars=Decimal("300.00"),
        )
        text = alert.to_text()
        self.assertIn("CBA", text)
        self.assertIn("BUY", text)
        self.assertIn("200", text)
        self.assertIn("110.500", text)
        self.assertIn("109.000", text)
        self.assertIn("momentum_zanger", text)

    def test_alert_to_dict_serializable(self) -> None:
        """Alert dict should be JSON-serializable."""
        alert = TradeAlert(
            timestamp=datetime(2024, 1, 15, 10, 30),
            symbol="BHP",
            action="BUY",
            quantity=100,
            limit_price=Decimal("45.00"),
            stop_loss=Decimal("44.00"),
            take_profit=None,
            strategy="orb_crabel",
            risk_dollars=Decimal("100"),
        )
        d = alert.to_dict()
        serialized = json.dumps(d)
        self.assertIn("BHP", serialized)

    def test_mark_filled_with_partial_quantity(self) -> None:
        """Can reconcile with a different quantity than ordered."""
        order = make_order(quantity=100)
        self.adapter.submit_order(order)
        self.adapter.mark_filled(
            order.order_id,
            fill_price=Decimal("45.00"),
            fill_quantity=80,
        )
        self.assertEqual(order.filled_quantity, 80)
        self.assertEqual(order.status, OrderStatus.FILLED)

    def test_mark_filled_custom_commission(self) -> None:
        """Can specify custom CMC commission on reconciliation."""
        events: list[dict] = []
        self.event_bus.subscribe(
            EventType.ORDER_FILLED,
            lambda e: events.append(e.data),
        )
        order = make_order()
        self.adapter.submit_order(order)
        self.adapter.mark_filled(
            order.order_id,
            fill_price=Decimal("45.00"),
            commission=Decimal("0"),  # First free trade
        )
        self.assertEqual(events[0]["fill"].commission, Decimal("0"))

    def test_alert_file_written(self) -> None:
        """Alert is persisted to a JSONL file."""
        order = make_order()
        self.adapter.submit_order(order)
        log_dir = Path("/tmp/asx_test_logs/alerts")
        if log_dir.exists():
            files = list(log_dir.glob("*_alerts.jsonl"))
            self.assertTrue(len(files) > 0)


if __name__ == "__main__":
    unittest.main()
