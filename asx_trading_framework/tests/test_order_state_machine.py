"""
Unit tests for the order state machine.

Tests all valid and invalid state transitions to ensure
deterministic, correct order lifecycle management.
"""

import unittest
from datetime import datetime
from decimal import Decimal

from ..core.types import Order, OrderStatus, OrderType, Side, TimeInForce
from ..execution.engine import VALID_TRANSITIONS, transition_order


def make_order(status: OrderStatus = OrderStatus.PENDING_NEW) -> Order:
    """Create a test order in the specified state."""
    order = Order(
        order_id="test-001",
        symbol="BHP",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=100,
        price=Decimal("45.00"),
        time_in_force=TimeInForce.DAY,
        status=status,
    )
    return order


class TestOrderStateMachine(unittest.TestCase):
    """Test all order state transitions."""

    # ──────────────────────────────────────────
    # Valid transitions
    # ──────────────────────────────────────────

    def test_pending_new_to_new(self) -> None:
        order = make_order(OrderStatus.PENDING_NEW)
        self.assertTrue(transition_order(order, OrderStatus.NEW))
        self.assertEqual(order.status, OrderStatus.NEW)

    def test_pending_new_to_rejected(self) -> None:
        order = make_order(OrderStatus.PENDING_NEW)
        self.assertTrue(transition_order(order, OrderStatus.REJECTED))
        self.assertEqual(order.status, OrderStatus.REJECTED)

    def test_new_to_partially_filled(self) -> None:
        order = make_order(OrderStatus.NEW)
        self.assertTrue(transition_order(order, OrderStatus.PARTIALLY_FILLED))
        self.assertEqual(order.status, OrderStatus.PARTIALLY_FILLED)

    def test_new_to_filled(self) -> None:
        order = make_order(OrderStatus.NEW)
        self.assertTrue(transition_order(order, OrderStatus.FILLED))
        self.assertEqual(order.status, OrderStatus.FILLED)

    def test_new_to_pending_cancel(self) -> None:
        order = make_order(OrderStatus.NEW)
        self.assertTrue(transition_order(order, OrderStatus.PENDING_CANCEL))
        self.assertEqual(order.status, OrderStatus.PENDING_CANCEL)

    def test_new_to_expired(self) -> None:
        order = make_order(OrderStatus.NEW)
        self.assertTrue(transition_order(order, OrderStatus.EXPIRED))
        self.assertEqual(order.status, OrderStatus.EXPIRED)

    def test_partially_filled_to_filled(self) -> None:
        order = make_order(OrderStatus.PARTIALLY_FILLED)
        self.assertTrue(transition_order(order, OrderStatus.FILLED))
        self.assertEqual(order.status, OrderStatus.FILLED)

    def test_partially_filled_to_more_partial(self) -> None:
        order = make_order(OrderStatus.PARTIALLY_FILLED)
        self.assertTrue(transition_order(order, OrderStatus.PARTIALLY_FILLED))
        self.assertEqual(order.status, OrderStatus.PARTIALLY_FILLED)

    def test_partially_filled_to_pending_cancel(self) -> None:
        order = make_order(OrderStatus.PARTIALLY_FILLED)
        self.assertTrue(transition_order(order, OrderStatus.PENDING_CANCEL))
        self.assertEqual(order.status, OrderStatus.PENDING_CANCEL)

    def test_pending_cancel_to_cancelled(self) -> None:
        order = make_order(OrderStatus.PENDING_CANCEL)
        self.assertTrue(transition_order(order, OrderStatus.CANCELLED))
        self.assertEqual(order.status, OrderStatus.CANCELLED)

    def test_pending_cancel_to_filled_race(self) -> None:
        """Fill arrives before cancel acknowledgement — valid race condition."""
        order = make_order(OrderStatus.PENDING_CANCEL)
        self.assertTrue(transition_order(order, OrderStatus.FILLED))
        self.assertEqual(order.status, OrderStatus.FILLED)

    # ──────────────────────────────────────────
    # Invalid transitions
    # ──────────────────────────────────────────

    def test_filled_is_terminal(self) -> None:
        """Cannot transition from FILLED — it's a terminal state."""
        order = make_order(OrderStatus.FILLED)
        self.assertFalse(transition_order(order, OrderStatus.NEW))
        self.assertEqual(order.status, OrderStatus.FILLED)

    def test_cancelled_is_terminal(self) -> None:
        order = make_order(OrderStatus.CANCELLED)
        self.assertFalse(transition_order(order, OrderStatus.NEW))
        self.assertEqual(order.status, OrderStatus.CANCELLED)

    def test_rejected_is_terminal(self) -> None:
        order = make_order(OrderStatus.REJECTED)
        self.assertFalse(transition_order(order, OrderStatus.NEW))
        self.assertEqual(order.status, OrderStatus.REJECTED)

    def test_expired_is_terminal(self) -> None:
        order = make_order(OrderStatus.EXPIRED)
        self.assertFalse(transition_order(order, OrderStatus.NEW))
        self.assertEqual(order.status, OrderStatus.EXPIRED)

    def test_pending_new_cannot_skip_to_filled(self) -> None:
        """Must go through NEW before FILLED."""
        order = make_order(OrderStatus.PENDING_NEW)
        self.assertFalse(transition_order(order, OrderStatus.FILLED))
        self.assertEqual(order.status, OrderStatus.PENDING_NEW)

    def test_new_cannot_go_to_cancelled_directly(self) -> None:
        """Must go through PENDING_CANCEL before CANCELLED."""
        order = make_order(OrderStatus.NEW)
        self.assertFalse(transition_order(order, OrderStatus.CANCELLED))
        self.assertEqual(order.status, OrderStatus.NEW)

    def test_pending_new_cannot_go_to_cancelled(self) -> None:
        order = make_order(OrderStatus.PENDING_NEW)
        self.assertFalse(transition_order(order, OrderStatus.CANCELLED))
        self.assertEqual(order.status, OrderStatus.PENDING_NEW)

    # ──────────────────────────────────────────
    # Order properties
    # ──────────────────────────────────────────

    def test_remaining_quantity(self) -> None:
        order = make_order()
        order.quantity = 100
        order.filled_quantity = 30
        self.assertEqual(order.remaining_quantity, 70)

    def test_is_terminal_for_terminal_states(self) -> None:
        for status in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED]:
            order = make_order(status)
            self.assertTrue(order.is_terminal, f"{status} should be terminal")

    def test_is_not_terminal_for_active_states(self) -> None:
        for status in [OrderStatus.PENDING_NEW, OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING_CANCEL]:
            order = make_order(status)
            self.assertFalse(order.is_terminal, f"{status} should not be terminal")

    def test_transition_updates_timestamp(self) -> None:
        """State transitions should update updated_at."""
        order = make_order(OrderStatus.PENDING_NEW)
        old_updated = order.updated_at
        transition_order(order, OrderStatus.NEW)
        self.assertGreaterEqual(order.updated_at, old_updated)


class TestValidTransitionsCompleteness(unittest.TestCase):
    """Verify that every OrderStatus has a defined transition set."""

    def test_all_statuses_have_transition_rules(self) -> None:
        for status in OrderStatus:
            self.assertIn(
                status,
                VALID_TRANSITIONS,
                f"Missing transition rules for {status.value}",
            )

    def test_terminal_states_have_empty_transitions(self) -> None:
        terminal = {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
        for status in terminal:
            self.assertEqual(
                VALID_TRANSITIONS[status],
                set(),
                f"{status.value} should have no valid transitions",
            )


if __name__ == "__main__":
    unittest.main()
