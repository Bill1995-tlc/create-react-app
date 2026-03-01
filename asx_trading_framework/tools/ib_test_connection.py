#!/usr/bin/env python3
"""
Test IB connection — connect, fetch account summary + positions, disconnect.

Usage:
    python -m asx_trading_framework.tools.ib_test_connection
    IB_PORT=7496 python -m asx_trading_framework.tools.ib_test_connection

Exit codes:
    0 = success
    1 = connection failed
    2 = account query failed
"""

from __future__ import annotations

import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ib_test_connection")

TIMEOUT = 15  # seconds


def main() -> int:
    from ..broker.ib.adapter import IBAdapter
    from ..broker.ib.config import IBConfig
    from ..broker.ib.errors import IBConnectionError as IBConnErr

    config = IBConfig.from_env()
    logger.info("Testing connection: %s", config.describe())

    adapter = IBAdapter(config)
    start = time.monotonic()

    # 1. Connect
    try:
        adapter.connect_sync()
    except IBConnErr as exc:
        logger.error("CONNECTION FAILED: %s", exc)
        return 1
    except Exception as exc:
        logger.error("UNEXPECTED ERROR: %s", exc)
        return 1

    elapsed = time.monotonic() - start
    logger.info("Connected in %.2fs", elapsed)

    # 2. Account summary
    try:
        summary = adapter.get_account_summary_sync()
        logger.info("Account: %s", summary.get("account", "?"))
        logger.info("Mode: %s", summary.get("mode", "?"))
        for key in ("NetLiquidation", "TotalCashValue", "BuyingPower", "UnrealizedPnL"):
            if key in summary:
                logger.info("  %s: %s", key, summary[key])
    except Exception as exc:
        logger.error("ACCOUNT QUERY FAILED: %s", exc)
        adapter.disconnect_sync()
        return 2

    # 3. Positions
    try:
        positions = adapter.get_positions_sync()
        if positions:
            logger.info("Open positions: %d", len(positions))
            for pos in positions:
                logger.info(
                    "  %s: %d shares @ $%.2f",
                    pos["symbol"], pos["quantity"], pos["avg_cost"],
                )
        else:
            logger.info("No open positions")
    except Exception as exc:
        logger.warning("Position query failed: %s", exc)

    # 4. Disconnect
    adapter.disconnect_sync()
    total = time.monotonic() - start
    logger.info("Connection test PASSED (%.2fs total)", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
