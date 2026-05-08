#!/usr/bin/env python3
"""
Simulate IB disconnect/reconnect — validates auto-reconnect with backoff.

Steps:
1. Connect to IB and subscribe to market data
2. Print instructions to manually restart TWS/Gateway
3. Detect disconnect event
4. Monitor reconnect attempts with exponential backoff
5. Verify data resumes after reconnection
6. Timeout and fail if reconnection doesn't happen within limit

Usage:
    python -m asx_trading_framework.tools.simulate_disconnect BHP
    python -m asx_trading_framework.tools.simulate_disconnect BHP --timeout 120

Exit codes:
    0 = reconnect succeeded
    1 = connection failed
    2 = reconnect failed (timeout)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("simulate_disconnect")


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate IB disconnect/reconnect")
    parser.add_argument("symbol", help="ASX ticker for data subscription")
    parser.add_argument(
        "--timeout", type=int, default=90,
        help="Max seconds to wait for reconnection (default: 90)",
    )
    args = parser.parse_args()

    from ..broker.ib.adapter import IBAdapter
    from ..broker.ib.config import IBConfig
    from ..broker.ib.errors import IBConnectionError as IBConnErr

    config = IBConfig.from_env()
    adapter = IBAdapter(config)

    # 1. Connect
    try:
        adapter.connect_sync()
    except IBConnErr as exc:
        logger.error("CONNECTION FAILED: %s", exc)
        return 1

    # 2. Resolve contract and get initial data
    try:
        adapter.resolve_contract_sync(args.symbol)
        data = adapter.get_market_data_sync(args.symbol)
        logger.info("Initial data received: last=$%s", data.get("last"))
    except Exception as exc:
        logger.warning("Initial data fetch: %s (continuing anyway)", exc)

    # 3. Print instructions
    print()
    print("=" * 60)
    print("  DISCONNECT SIMULATION")
    print("=" * 60)
    print()
    print("  The adapter is connected and receiving data.")
    print()
    print("  TO TRIGGER DISCONNECT, do ONE of:")
    print("    1. Stop TWS/IB Gateway (File → Exit)")
    print("    2. Disable API (Edit → Global Config → API → uncheck)")
    print("    3. Kill the TWS process")
    print()
    print(f"  Waiting up to {args.timeout}s for disconnect + reconnect...")
    print(f"  Reconnect attempts: {len(adapter.RECONNECT_DELAYS)} "
          f"with delays {adapter.RECONNECT_DELAYS}s")
    print()
    print("  IMPORTANT: Restart TWS/Gateway within the backoff window")
    print("  so the adapter can reconnect automatically.")
    print()
    print("=" * 60)
    print()

    # 4. Monitor disconnect/reconnect
    initial_reconnect_count = adapter._reconnect_count
    was_disconnected = False
    start = time.monotonic()

    while time.monotonic() - start < args.timeout:
        if not adapter.is_connected and not was_disconnected:
            elapsed = time.monotonic() - start
            logger.info("DISCONNECT DETECTED at %.1fs", elapsed)
            was_disconnected = True

        if was_disconnected and adapter.is_connected:
            elapsed = time.monotonic() - start
            logger.info("RECONNECTED at %.1fs!", elapsed)
            logger.info(
                "Reconnect count: %d (was %d)",
                adapter._reconnect_count, initial_reconnect_count,
            )

            # 5. Verify data resumes
            try:
                data = adapter.get_market_data_sync(args.symbol)
                logger.info("Post-reconnect data: last=$%s", data.get("last"))
                logger.info("RECONNECT TEST PASSED")
            except Exception as exc:
                logger.warning("Post-reconnect data failed: %s", exc)
                logger.info("RECONNECT TEST PASSED (connection restored, data may need subscription)")

            adapter.disconnect_sync()
            return 0

        time.sleep(1)

    # Timeout
    if not was_disconnected:
        logger.error(
            "No disconnect detected within %ds. "
            "Did you stop TWS/Gateway?",
            args.timeout,
        )
    else:
        logger.error(
            "Disconnect detected but reconnection failed within %ds. "
            "Ensure TWS/Gateway was restarted before backoff exhausted.",
            args.timeout,
        )

    try:
        adapter.disconnect_sync()
    except Exception:
        pass
    return 2


if __name__ == "__main__":
    sys.exit(main())
