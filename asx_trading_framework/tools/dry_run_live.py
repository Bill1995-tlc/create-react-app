#!/usr/bin/env python3
"""
Dry-run on live connection — connect to live IB, receive data, assert no orders.

Steps:
1. Connect to IB live port (7496)
2. Resolve contract and fetch market data
3. Wrap adapter in DryRunBrokerAdapter
4. Attempt an order → verify DryRunBlocked is raised
5. Assert zero orders were placed

Usage:
    python -m asx_trading_framework.tools.dry_run_live BHP
    IB_PORT=7496 python -m asx_trading_framework.tools.dry_run_live BHP

Exit codes:
    0 = dry-run validated (no orders placed)
    1 = connection failed
    2 = market data failed
    3 = dry-run blocking failed (CRITICAL)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("dry_run_live")


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run on live IB connection")
    parser.add_argument("symbol", nargs="?", default="BHP", help="ASX ticker")
    parser.add_argument(
        "--min-quotes", type=int, default=1,
        help="Minimum quote snapshots to receive (default: 1)",
    )
    args = parser.parse_args()

    from ..broker.ib.adapter import IBAdapter
    from ..broker.ib.config import IBConfig
    from ..broker.ib.errors import (
        IBConnectionError as IBConnErr,
        IBMarketDataError,
    )
    from ..execution.dry_run import DryRunBlocked, DryRunBrokerAdapter
    from ..execution.ibkr_adapter import IBKRBrokerAdapter
    from ..core.events import EventBus
    from ..core.types import Order, OrderType, Side
    from decimal import Decimal

    config = IBConfig.from_env()

    # Default to live port for this test
    if os.getenv("IB_PORT") is None:
        config.port = 7496
        config.mode = "live"

    logger.info("Dry-run live test: %s", config.describe())

    adapter = IBAdapter(config)

    # 1. Connect
    logger.info("Step 1: Connect to IB (live)")
    try:
        adapter.connect_sync()
        logger.info("  PASS: Connected")
    except IBConnErr as exc:
        logger.error("  FAIL: %s", exc)
        return 1

    # 2. Market data
    logger.info("Step 2: Fetch market data for %s", args.symbol)
    quotes_received = 0
    try:
        data = adapter.get_market_data_sync(args.symbol)
        if data.get("last") is not None or data.get("bid") is not None:
            quotes_received += 1
        logger.info("  PASS: Received data (last=%s)", data.get("last"))
    except IBMarketDataError as exc:
        logger.error("  FAIL: %s", exc)
        adapter.disconnect_sync()
        return 2

    if quotes_received < args.min_quotes:
        logger.error(
            "  FAIL: Only %d quotes received (need %d)",
            quotes_received, args.min_quotes,
        )
        adapter.disconnect_sync()
        return 2

    # 3. Wrap in DryRunBrokerAdapter and test blocking
    logger.info("Step 3: Verify order blocking via DryRunBrokerAdapter")

    event_bus = EventBus()
    ibkr_adapter = IBKRBrokerAdapter(
        event_bus=event_bus,
        host=config.host,
        port=config.port,
        client_id=config.client_id + 1,  # Different client ID
        account_id=config.account,
    )
    # We don't actually connect the bridge adapter — we just test the wrapper
    dry_run = DryRunBrokerAdapter(ibkr_adapter)

    test_order = Order(
        order_id="dry-run-test-001",
        symbol=args.symbol,
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=1,
        price=Decimal("45.00"),
    )

    order_blocked = False
    try:
        dry_run.submit_order(test_order)
        logger.error("  CRITICAL FAIL: Order was NOT blocked!")
        adapter.disconnect_sync()
        return 3
    except DryRunBlocked:
        order_blocked = True
        logger.info("  PASS: Order correctly blocked by DryRunBrokerAdapter")

    cancel_blocked = False
    try:
        dry_run.cancel_order("dry-run-test-001")
    except DryRunBlocked:
        cancel_blocked = True
        logger.info("  PASS: Cancel correctly blocked by DryRunBrokerAdapter")

    # 4. Summary
    logger.info("Step 4: Summary")
    logger.info("  Quotes received: %d", quotes_received)
    logger.info("  Orders blocked: %d", dry_run.blocked_count)
    logger.info("  Order blocking: %s", "PASS" if order_blocked else "FAIL")
    logger.info("  Cancel blocking: %s", "PASS" if cancel_blocked else "FAIL")

    adapter.disconnect_sync()

    if order_blocked and cancel_blocked:
        logger.info("DRY-RUN LIVE TEST PASSED")
        return 0
    else:
        logger.error("DRY-RUN LIVE TEST FAILED")
        return 3


if __name__ == "__main__":
    sys.exit(main())
