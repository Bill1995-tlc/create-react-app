#!/usr/bin/env python3
"""
Test IB market data — connect, resolve contract, fetch snapshot.

Usage:
    python -m asx_trading_framework.tools.ib_test_market_data BHP
    python -m asx_trading_framework.tools.ib_test_market_data CBA --stream 10

Exit codes:
    0 = success (data received)
    1 = connection failed
    2 = contract resolution failed
    3 = no market data received
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
logger = logging.getLogger("ib_test_market_data")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test IB market data")
    parser.add_argument("symbol", help="ASX ticker (e.g. BHP)")
    parser.add_argument(
        "--stream", type=int, default=0,
        help="Stream real-time bars for N seconds (0=snapshot only)",
    )
    parser.add_argument(
        "--timeout", type=int, default=15,
        help="Timeout in seconds for data receipt (default: 15)",
    )
    args = parser.parse_args()

    from ..broker.ib.adapter import IBAdapter
    from ..broker.ib.config import IBConfig
    from ..broker.ib.errors import (
        IBConnectionError as IBConnErr,
        IBContractError,
        IBMarketDataError,
    )

    config = IBConfig.from_env()
    adapter = IBAdapter(config)

    # 1. Connect
    try:
        adapter.connect_sync()
    except IBConnErr as exc:
        logger.error("CONNECTION FAILED: %s", exc)
        return 1

    # 2. Resolve contract
    try:
        contract = adapter.resolve_contract_sync(args.symbol)
        logger.info(
            "Contract resolved: %s (conId=%d, exchange=%s)",
            args.symbol, contract.conId, contract.exchange,
        )
    except IBContractError as exc:
        logger.error("CONTRACT FAILED: %s", exc)
        adapter.disconnect_sync()
        return 2

    # 3. Market data snapshot
    try:
        data = adapter.get_market_data_sync(args.symbol)
        logger.info("=== Market Data Snapshot ===")
        for key, val in data.items():
            if val is not None:
                logger.info("  %s: %s", key, val)
    except IBMarketDataError as exc:
        logger.error("MARKET DATA FAILED: %s", exc)
        adapter.disconnect_sync()
        return 3

    # 4. Optional streaming
    if args.stream > 0:
        logger.info("Streaming real-time data for %ds...", args.stream)
        _stream_bars(adapter, contract, args.symbol, args.stream)

    adapter.disconnect_sync()
    logger.info("Market data test PASSED")
    return 0


def _stream_bars(
    adapter: object, contract: object, symbol: str, duration: int,
) -> None:
    """Subscribe to real-time 5-second bars for a short window."""
    from ..broker.ib.adapter import _run, IB_LIB

    if not IB_LIB:
        logger.warning("No IB library for streaming")
        return

    async def _do_stream() -> None:
        ib = adapter._ib  # type: ignore[attr-defined]
        bars_received = 0

        def on_bar(bars: object, has_new: bool) -> None:
            nonlocal bars_received
            if has_new and bars:
                last = bars[-1]
                bars_received += 1
                logger.info(
                    "  BAR #%d: O=%.2f H=%.2f L=%.2f C=%.2f V=%d",
                    bars_received,
                    last.open, last.high, last.low, last.close,
                    last.volume,
                )

        rt_bars = ib.reqRealTimeBars(contract, 5, "TRADES", False)
        rt_bars.updateEvent += on_bar

        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)

        ib.cancelRealTimeBars(rt_bars)
        logger.info("Received %d bars in %ds", bars_received, duration)

    _run(_do_stream())


if __name__ == "__main__":
    sys.exit(main())
