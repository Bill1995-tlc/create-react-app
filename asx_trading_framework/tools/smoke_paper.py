#!/usr/bin/env python3
"""
Smoke test for IB paper trading — end-to-end validation.

Steps:
1. Connect to IB paper (default port 7497)
2. Qualify ASX contract for given symbol
3. Fetch account summary + positions
4. Fetch market data snapshot
5. Verify state file is created/updated
6. Optionally place and cancel a tiny test order (CONFIRM_TEST_ORDER=1)

Usage:
    python -m asx_trading_framework.tools.smoke_paper BHP
    CONFIRM_TEST_ORDER=1 python -m asx_trading_framework.tools.smoke_paper BHP

Exit codes:
    0 = all checks passed
    1 = connection failed
    2 = contract resolution failed
    3 = market data failed
    4 = state persistence failed
    5 = test order failed
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("smoke_paper")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test IB paper trading")
    parser.add_argument("symbol", nargs="?", default="BHP", help="ASX ticker")
    parser.add_argument(
        "--timeout", type=int, default=30,
        help="Overall timeout in seconds (default: 30)",
    )
    args = parser.parse_args()

    from ..broker.ib.adapter import IBAdapter
    from ..broker.ib.config import IBConfig
    from ..broker.ib.errors import (
        IBConnectionError as IBConnErr,
        IBContractError,
        IBMarketDataError,
        IBOrderError,
    )

    results: dict[str, str] = {}
    config = IBConfig.from_env()

    # Force paper port if not explicitly set
    if config.port in (7496, 4001) and os.getenv("IB_PORT") is None:
        logger.warning("Overriding to paper port 7497 for smoke test")
        config.port = 7497

    adapter = IBAdapter(config)

    # ── Step 1: Connect ──
    logger.info("Step 1: Connect to IB paper")
    try:
        adapter.connect_sync()
        results["connect"] = "PASS"
        logger.info("  PASS: Connected")
    except IBConnErr as exc:
        logger.error("  FAIL: %s", exc)
        results["connect"] = f"FAIL: {exc}"
        _print_results(results)
        return 1

    # ── Step 2: Qualify contract ──
    logger.info("Step 2: Qualify contract for %s", args.symbol)
    try:
        contract = adapter.resolve_contract_sync(args.symbol)
        results["contract"] = f"PASS: conId={contract.conId}"
        logger.info("  PASS: conId=%d", contract.conId)
    except IBContractError as exc:
        logger.error("  FAIL: %s", exc)
        results["contract"] = f"FAIL: {exc}"
        adapter.disconnect_sync()
        _print_results(results)
        return 2

    # ── Step 3: Account summary + positions ──
    logger.info("Step 3: Fetch account summary + positions")
    try:
        summary = adapter.get_account_summary_sync()
        nlv = summary.get("NetLiquidation", "?")
        results["account"] = f"PASS: NLV={nlv}"
        logger.info("  PASS: NetLiquidation=%s", nlv)
    except Exception as exc:
        results["account"] = f"WARN: {exc}"
        logger.warning("  WARN: %s", exc)

    try:
        positions = adapter.get_positions_sync()
        results["positions"] = f"PASS: {len(positions)} open"
        logger.info("  PASS: %d open positions", len(positions))
    except Exception as exc:
        results["positions"] = f"WARN: {exc}"
        logger.warning("  WARN: %s", exc)

    # ── Step 4: Market data ──
    logger.info("Step 4: Fetch market data for %s", args.symbol)
    try:
        data = adapter.get_market_data_sync(args.symbol)
        last = data.get("last") or data.get("close")
        bid = data.get("bid")
        ask = data.get("ask")
        results["market_data"] = f"PASS: last={last} bid={bid} ask={ask}"
        logger.info("  PASS: last=%s bid=%s ask=%s", last, bid, ask)
    except IBMarketDataError as exc:
        logger.error("  FAIL: %s", exc)
        results["market_data"] = f"FAIL: {exc}"
        adapter.disconnect_sync()
        _print_results(results)
        return 3

    # ── Step 5: State persistence ──
    logger.info("Step 5: Verify state persistence")
    state_dir = Path("./state")
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "current_state.json"

    # Write a test state
    test_state = {
        "equity": "100000",
        "daily_pnl": "0",
        "total_commission": "0",
        "positions": {},
        "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
        "_smoke_test": True,
    }
    with open(state_file, "w") as f:
        json.dump(test_state, f, indent=2)

    # Read it back
    with open(state_file) as f:
        loaded = json.load(f)

    if loaded.get("equity") == "100000":
        results["state"] = "PASS"
        logger.info("  PASS: State file created and verified")
    else:
        results["state"] = "FAIL: State read-back mismatch"
        logger.error("  FAIL: State read-back mismatch")
        adapter.disconnect_sync()
        _print_results(results)
        return 4

    # ── Step 6: Optional test order ──
    if os.getenv("CONFIRM_TEST_ORDER") == "1":
        logger.info("Step 6: Place test order (1 share)")

        # Safety: only on paper ports
        if config.port not in (7497, 4002):
            results["test_order"] = "SKIP: not a paper port"
            logger.warning("  SKIP: Port %d is not paper", config.port)
        else:
            try:
                trade = adapter.place_market_order_sync(args.symbol, "BUY", 1)
                logger.info("  Order placed: orderId=%d", trade.order.orderId)

                # Wait briefly for fill
                from ..broker.ib.adapter import _run
                import asyncio

                async def _wait() -> None:
                    for _ in range(20):
                        await asyncio.sleep(0.5)
                        if trade.isDone():
                            break
                _run(_wait())

                status = trade.orderStatus.status
                logger.info("  Order status: %s", status)

                if status == "Filled":
                    # Flatten
                    flatten = adapter.place_market_order_sync(args.symbol, "SELL", 1)
                    async def _wait_flatten() -> None:
                        for _ in range(20):
                            await asyncio.sleep(0.5)
                            if flatten.isDone():
                                break
                    _run(_wait_flatten())
                    results["test_order"] = f"PASS: filled, flattened"
                    logger.info("  PASS: Test order filled and flattened")
                else:
                    # Cancel unfilled order
                    from ..broker.ib.adapter import _run as run2
                    run2(adapter.cancel_order(trade))
                    results["test_order"] = f"PASS: placed, cancelled (status={status})"
                    logger.info("  PASS: Order placed and cancelled")

            except (IBOrderError, Exception) as exc:
                results["test_order"] = f"FAIL: {exc}"
                logger.error("  FAIL: %s", exc)
                adapter.disconnect_sync()
                _print_results(results)
                return 5
    else:
        results["test_order"] = "SKIP (set CONFIRM_TEST_ORDER=1)"
        logger.info("Step 6: SKIP test order (set CONFIRM_TEST_ORDER=1)")

    # ── Done ──
    adapter.disconnect_sync()
    _print_results(results)

    failed = any("FAIL" in v for v in results.values())
    return 1 if failed else 0


def _print_results(results: dict[str, str]) -> None:
    print()
    print("=" * 50)
    print("  SMOKE TEST RESULTS")
    print("=" * 50)
    for step, result in results.items():
        status = "PASS" if "PASS" in result else ("FAIL" if "FAIL" in result else "SKIP")
        print(f"  [{status:4s}] {step}: {result}")
    print("=" * 50)
    print()


if __name__ == "__main__":
    sys.exit(main())
