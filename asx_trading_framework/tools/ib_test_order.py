#!/usr/bin/env python3
"""
Test IB order placement — PAPER ONLY, guarded.

Places a tiny test order (1 share), waits for fill or timeout, then
cancels/flattens to leave no residual position.

Usage:
    CONFIRM_TEST_ORDER=1 python -m asx_trading_framework.tools.ib_test_order BHP

Safety:
    - REFUSES to run on live port (7496/4001) unless FORCE_LIVE_TEST=1
    - Requires CONFIRM_TEST_ORDER=1 env var
    - Order qty is always 1 share
    - Auto-cancels after timeout
    - Flattens any resulting position

Exit codes:
    0 = success (order placed and handled)
    1 = connection failed
    2 = safety gate blocked
    3 = order failed
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ib_test_order")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test IB order (paper only)")
    parser.add_argument("symbol", help="ASX ticker (e.g. BHP)")
    parser.add_argument(
        "--side", choices=["BUY", "SELL"], default="BUY",
        help="Order side (default: BUY)",
    )
    parser.add_argument(
        "--timeout", type=int, default=30,
        help="Wait timeout for fill in seconds (default: 30)",
    )
    args = parser.parse_args()

    from ..broker.ib.adapter import IBAdapter
    from ..broker.ib.config import IBConfig
    from ..broker.ib.errors import (
        IBConnectionError as IBConnErr,
        IBOrderError,
    )

    # Safety gate 1: env var
    if os.getenv("CONFIRM_TEST_ORDER") != "1":
        logger.error(
            "SAFETY GATE: Set CONFIRM_TEST_ORDER=1 to enable test orders"
        )
        return 2

    config = IBConfig.from_env()

    # Safety gate 2: refuse live ports unless explicitly forced
    live_ports = {7496, 4001}
    if config.port in live_ports and os.getenv("FORCE_LIVE_TEST") != "1":
        logger.error(
            "SAFETY GATE: Port %d is a LIVE port. "
            "This script is for paper testing only. "
            "Set FORCE_LIVE_TEST=1 to override (at your own risk).",
            config.port,
        )
        return 2

    adapter = IBAdapter(config)

    # Connect
    try:
        adapter.connect_sync()
    except IBConnErr as exc:
        logger.error("CONNECTION FAILED: %s", exc)
        return 1

    # Resolve contract
    try:
        contract = adapter.resolve_contract_sync(args.symbol)
    except Exception as exc:
        logger.error("Contract resolution failed: %s", exc)
        adapter.disconnect_sync()
        return 3

    # Get a market data snapshot for reference
    try:
        data = adapter.get_market_data_sync(args.symbol)
        last = data.get("last") or data.get("close") or 0
        logger.info("Reference price for %s: $%.2f", args.symbol, last)
    except Exception:
        last = 0
        logger.warning("Could not get reference price, proceeding anyway")

    # Place test order: 1 share, market order
    logger.info(
        "Placing TEST order: %s 1 share of %s (MARKET)",
        args.side, args.symbol,
    )
    try:
        trade = adapter.place_market_order_sync(args.symbol, args.side, 1)
        logger.info("Order placed: orderId=%d", trade.order.orderId)
    except IBOrderError as exc:
        logger.error("ORDER FAILED: %s", exc)
        adapter.disconnect_sync()
        return 3

    # Wait for fill
    from ..broker.ib.adapter import _run

    async def _wait_and_cleanup() -> int:
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            if trade.isDone():
                break

        if trade.orderStatus.status == "Filled":
            fill_price = trade.orderStatus.avgFillPrice
            logger.info(
                "TEST ORDER FILLED: %s 1x %s @ $%.4f",
                args.side, args.symbol, fill_price,
            )
            # Flatten: place opposite order
            reverse = "SELL" if args.side == "BUY" else "BUY"
            logger.info("Flattening: %s 1x %s", reverse, args.symbol)
            try:
                flatten_trade = await adapter.place_market_order(
                    args.symbol, reverse, 1,
                )
                # Wait for flatten fill
                for _ in range(60):
                    await asyncio.sleep(0.5)
                    if flatten_trade.isDone():
                        break
                if flatten_trade.orderStatus.status == "Filled":
                    logger.info("Flatten FILLED at $%.4f", flatten_trade.orderStatus.avgFillPrice)
                else:
                    logger.warning("Flatten status: %s", flatten_trade.orderStatus.status)
            except Exception as exc:
                logger.error("Flatten failed: %s — MANUAL CLEANUP REQUIRED", exc)
                return 3
        else:
            logger.warning(
                "Order not filled within %ds (status: %s). Cancelling...",
                args.timeout, trade.orderStatus.status,
            )
            try:
                await adapter.cancel_order(trade)
                await asyncio.sleep(1)
                logger.info("Cancel status: %s", trade.orderStatus.status)
            except Exception as exc:
                logger.warning("Cancel failed: %s", exc)
        return 0

    result = _run(_wait_and_cleanup())

    adapter.disconnect_sync()
    if result == 0:
        logger.info("Order test PASSED")
    return result


if __name__ == "__main__":
    sys.exit(main())
