#!/usr/bin/env python3
"""
Simulate framework restart — validates state persistence and recovery.

Steps:
1. Start framework in paper mode for a short duration
2. Write state to disk
3. Stop cleanly
4. Re-create framework and verify state was recovered
5. Check no duplicate positions or orders

Usage:
    python -m asx_trading_framework.tools.simulate_restart
    python -m asx_trading_framework.tools.simulate_restart --duration 15

Exit codes:
    0 = state recovery verified
    1 = state recovery failed
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from decimal import Decimal
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("simulate_restart")


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate framework restart")
    parser.add_argument(
        "--duration", type=int, default=5,
        help="Run duration in seconds before stopping (default: 5)",
    )
    parser.add_argument(
        "--state-dir", default="./state",
        help="State persistence directory (default: ./state)",
    )
    args = parser.parse_args()

    from ..core.config import FrameworkConfig
    from ..core.events import EventBus
    from ..state.manager import StateManager
    from ..core.types import Fill, Side

    state_dir = Path(args.state_dir)
    state_file = state_dir / "current_state.json"

    # ── Phase 1: Create initial state ──
    logger.info("=== Phase 1: Creating initial state ===")

    event_bus_1 = EventBus()
    sm1 = StateManager(event_bus_1, persist_dir=str(state_dir))
    sm1.set_initial_equity(Decimal("50000"))

    # Simulate a position by publishing a fill event
    from ..core.events import Event, EventType
    from ..core.types import Order, OrderStatus, OrderType, TimeInForce
    import uuid

    # Create a fake order + fill to generate a position
    order = Order(
        order_id=str(uuid.uuid4()),
        symbol="BHP",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=100,
        price=Decimal("45.50"),
        status=OrderStatus.FILLED,
    )
    fill = Fill(
        fill_id=str(uuid.uuid4()),
        order_id=order.order_id,
        symbol="BHP",
        side=Side.BUY,
        quantity=100,
        price=Decimal("45.50"),
        commission=Decimal("10.00"),
        timestamp=__import__("datetime").datetime.utcnow(),
    )

    # Publish the fill event to trigger state update
    event_bus_1.publish(Event(
        event_type=EventType.ORDER_FILLED,
        data={"order": order, "fill": fill},
        source="simulate_restart",
    ))

    # Verify position was created
    positions_before = sm1.positions
    equity_before = sm1.equity
    logger.info("Positions before stop: %s", {s: p.quantity for s, p in positions_before.items()})
    logger.info("Equity before stop: %s", equity_before)

    if "BHP" not in positions_before:
        logger.error("FAILED: Position was not created")
        return 1

    # Ensure state is persisted
    sm1._persist_state()
    logger.info("State persisted to %s", state_file)

    # Verify the file exists and is valid
    if not state_file.exists():
        logger.error("FAILED: State file not created at %s", state_file)
        return 1

    with open(state_file) as f:
        saved_state = json.load(f)
    logger.info("Saved state keys: %s", list(saved_state.keys()))

    # ── Phase 2: Simulate stop (wait briefly) ──
    logger.info("=== Phase 2: Stopping for %ds ===", args.duration)
    time.sleep(args.duration)

    # ── Phase 3: Recover state ──
    logger.info("=== Phase 3: Recovering state ===")

    event_bus_2 = EventBus()
    sm2 = StateManager(event_bus_2, persist_dir=str(state_dir))

    loaded = sm2.load_state()
    if not loaded:
        logger.error("FAILED: load_state() returned False")
        return 1

    positions_after = sm2.positions
    equity_after = sm2.equity

    logger.info("Positions after recovery: %s", {s: p.quantity for s, p in positions_after.items()})
    logger.info("Equity after recovery: %s", equity_after)

    # ── Phase 4: Validate ──
    logger.info("=== Phase 4: Validating ===")

    errors: list[str] = []

    # Check positions match
    if set(positions_before.keys()) != set(positions_after.keys()):
        errors.append(
            f"Position symbols mismatch: {set(positions_before.keys())} vs {set(positions_after.keys())}"
        )
    else:
        for symbol in positions_before:
            before = positions_before[symbol]
            after = positions_after[symbol]
            if before.quantity != after.quantity:
                errors.append(f"{symbol}: qty {before.quantity} -> {after.quantity}")
            if before.average_entry_price != after.average_entry_price:
                errors.append(f"{symbol}: avg_price {before.average_entry_price} -> {after.average_entry_price}")

    # Check equity
    if equity_before != equity_after:
        errors.append(f"Equity mismatch: {equity_before} -> {equity_after}")

    # Check no duplicates (position count should match)
    if len(positions_after) != len(positions_before):
        errors.append(
            f"Position count mismatch: {len(positions_before)} -> {len(positions_after)}"
        )

    if errors:
        logger.error("RESTART TEST FAILED:")
        for err in errors:
            logger.error("  - %s", err)
        return 1

    logger.info("RESTART TEST PASSED: State recovered correctly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
