"""
CLI for Interactive Brokers adapter.

Provides commands to test connectivity, view account info, get quotes,
and place orders on ASX equities.

Usage:
    python -m asx_trading_framework.broker.ib.cli test-connection
    python -m asx_trading_framework.broker.ib.cli account
    python -m asx_trading_framework.broker.ib.cli positions
    python -m asx_trading_framework.broker.ib.cli quote BHP
    python -m asx_trading_framework.broker.ib.cli buy BHP --qty 10 --type market
    python -m asx_trading_framework.broker.ib.cli sell BHP --qty 10 --type limit --limit 45.10
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from .adapter import IBAdapter, IB_LIB
from .config import IBConfig
from .errors import IBAdapterError

logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool = False) -> None:
    """Configure logging for CLI use."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet the ib_async internals unless verbose
    if not verbose:
        logging.getLogger("ib_async").setLevel(logging.WARNING)
        logging.getLogger("ib_insync").setLevel(logging.WARNING)


def _print_json(data: Any) -> None:
    """Pretty-print JSON to stdout."""
    print(json.dumps(data, indent=2, default=str))


def _print_table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> None:
    """Print a simple text table."""
    if not rows:
        print("  (no data)")
        return

    cols = columns or list(rows[0].keys())
    widths = {c: max(len(c), max(len(str(r.get(c, ""))) for r in rows)) for c in cols}

    # Header
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print("  ".join("-" * widths[c] for c in cols))

    # Rows
    for row in rows:
        line = "  ".join(str(row.get(c, "")).ljust(widths[c]) for c in cols)
        print(line)


# ──────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────

def cmd_test_connection(args: argparse.Namespace) -> int:
    """Test connectivity to TWS/IB Gateway."""
    config = IBConfig.from_env()
    print(f"\n  IB Library:  {IB_LIB or 'NOT INSTALLED'}")
    print(f"  {config.describe()}\n")

    if not IB_LIB:
        print("  ERROR: No IB library installed.")
        print("  Fix:   pip install ib_async")
        return 1

    adapter = IBAdapter(config)
    try:
        adapter.connect_sync()
        print("  STATUS: Connected successfully!")
        print(f"  Server: {adapter._ib.client.serverVersion()}")
        print(f"  Accounts: {adapter._ib.managedAccounts()}")

        # Quick contract test
        try:
            contract = adapter.resolve_contract_sync("BHP")
            print(f"  ASX test: BHP resolved → conId={contract.conId}")
        except IBAdapterError as exc:
            print(f"  ASX test: BHP resolution failed — {exc}")

        adapter.disconnect_sync()
        print("\n  All checks passed.\n")
        return 0

    except IBAdapterError as exc:
        print(f"\n  CONNECTION FAILED: {exc}\n")
        _print_troubleshoot()
        return 1


def cmd_account(args: argparse.Namespace) -> int:
    """Display account summary."""
    adapter = IBAdapter()
    try:
        adapter.connect_sync()
        summary = adapter.get_account_summary_sync()
        adapter.disconnect_sync()

        print("\n  Account Summary")
        print("  " + "=" * 40)
        for key, val in summary.items():
            if isinstance(val, float):
                print(f"  {key:<25s}  ${val:>14,.2f}")
            else:
                print(f"  {key:<25s}  {val}")
        print()
        return 0

    except IBAdapterError as exc:
        print(f"\n  ERROR: {exc}\n")
        return 1


def cmd_positions(args: argparse.Namespace) -> int:
    """Display current positions."""
    adapter = IBAdapter()
    try:
        adapter.connect_sync()
        positions = adapter.get_positions_sync()
        adapter.disconnect_sync()

        print(f"\n  Positions ({len(positions)} open)\n")
        if positions:
            _print_table(positions)
        else:
            print("  No open positions.")
        print()
        return 0

    except IBAdapterError as exc:
        print(f"\n  ERROR: {exc}\n")
        return 1


def cmd_quote(args: argparse.Namespace) -> int:
    """Get a market data quote."""
    symbol = args.symbol.upper()
    adapter = IBAdapter()
    try:
        adapter.connect_sync()
        data = adapter.get_market_data_sync(symbol)
        adapter.disconnect_sync()

        print(f"\n  Quote: {symbol}")
        print("  " + "=" * 40)
        for key, val in data.items():
            if val is not None:
                if isinstance(val, float):
                    print(f"  {key:<15s}  {val:>12.4f}")
                else:
                    print(f"  {key:<15s}  {val}")
        print()
        return 0

    except IBAdapterError as exc:
        print(f"\n  ERROR: {exc}\n")
        return 1


def cmd_buy(args: argparse.Namespace) -> int:
    """Place a buy order."""
    return _place_order(args, "BUY")


def cmd_sell(args: argparse.Namespace) -> int:
    """Place a sell order."""
    return _place_order(args, "SELL")


def _place_order(args: argparse.Namespace, side: str) -> int:
    """Place a buy or sell order."""
    symbol = args.symbol.upper()
    qty = args.qty
    order_type = args.type.lower()
    limit_price = getattr(args, "limit", None)

    if order_type == "limit" and limit_price is None:
        print("  ERROR: --limit is required for limit orders.")
        return 1

    config = IBConfig.from_env()
    if config.is_live:
        confirm = input(
            f"\n  LIVE TRADING: {side} {qty} {symbol} ({order_type}). "
            f"Type 'yes' to confirm: "
        )
        if confirm.strip().lower() != "yes":
            print("  Order cancelled by user.")
            return 0

    adapter = IBAdapter(config)
    try:
        adapter.connect_sync()

        if order_type == "market":
            trade = adapter.place_market_order_sync(symbol, side, qty)
        elif order_type == "limit":
            trade = adapter.place_limit_order_sync(symbol, side, qty, limit_price)
        else:
            print(f"  ERROR: Unknown order type '{order_type}'. Use 'market' or 'limit'.")
            adapter.disconnect_sync()
            return 1

        print(f"\n  Order placed: {side} {qty} {symbol} ({order_type})")
        print(f"  Order ID:     {trade.order.orderId}")
        print(f"  Status:       {trade.orderStatus.status}")
        if limit_price:
            print(f"  Limit:        ${limit_price:.4f}")
        print()

        adapter.disconnect_sync()
        return 0

    except IBAdapterError as exc:
        print(f"\n  ORDER FAILED: {exc}\n")
        return 1


def cmd_open_orders(args: argparse.Namespace) -> int:
    """Display open orders."""
    adapter = IBAdapter()
    try:
        adapter.connect_sync()
        orders = adapter.get_open_orders()
        adapter.disconnect_sync()

        print(f"\n  Open Orders ({len(orders)})\n")
        if orders:
            _print_table(orders)
        else:
            print("  No open orders.")
        print()
        return 0

    except IBAdapterError as exc:
        print(f"\n  ERROR: {exc}\n")
        return 1


def cmd_cancel(args: argparse.Namespace) -> int:
    """Cancel an order by ID."""
    order_id = args.order_id
    adapter = IBAdapter()
    try:
        adapter.connect_sync()

        from .adapter import _run
        found = _run(adapter.cancel_order_by_id(order_id))

        adapter.disconnect_sync()

        if found:
            print(f"\n  Cancel request sent for order {order_id}.\n")
            return 0
        else:
            print(f"\n  No open order with ID {order_id} found.\n")
            return 1

    except IBAdapterError as exc:
        print(f"\n  ERROR: {exc}\n")
        return 1


# ──────────────────────────────────────────────
# Troubleshooting helper
# ──────────────────────────────────────────────

def _print_troubleshoot() -> None:
    """Print troubleshooting checklist."""
    print("  Troubleshooting checklist:")
    print("  ─────────────────────────")
    print("  1. Is TWS or IB Gateway running?")
    print("  2. Is the API enabled?")
    print("     TWS → Edit → Global Configuration → API → Settings")
    print("     ✓ Enable ActiveX and Socket Clients")
    print("     ✓ Socket port matches IB_PORT (7497 paper, 7496 live)")
    print("     ✓ Trusted IPs includes 127.0.0.1")
    print("  3. Is another app using the same client ID?")
    print("     Change IB_CLIENT_ID in .env")
    print("  4. For IB Gateway:")
    print("     Paper=4002, Live=4001 (different from TWS defaults)")
    print("  5. Check firewall is not blocking localhost connections")
    print()


# ──────────────────────────────────────────────
# Argument parser
# ──────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="ib",
        description="Interactive Brokers CLI for ASX equities",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # test-connection
    sub.add_parser("test-connection", help="Test TWS/Gateway connectivity")

    # account
    sub.add_parser("account", help="Show account summary")

    # positions
    sub.add_parser("positions", help="Show current positions")

    # quote
    p_quote = sub.add_parser("quote", help="Get market data quote")
    p_quote.add_argument("symbol", help="ASX ticker symbol (e.g. BHP)")

    # buy
    p_buy = sub.add_parser("buy", help="Place a buy order")
    p_buy.add_argument("symbol", help="ASX ticker symbol")
    p_buy.add_argument("--qty", type=int, required=True, help="Number of shares")
    p_buy.add_argument("--type", default="limit", choices=["market", "limit"], help="Order type")
    p_buy.add_argument("--limit", type=float, help="Limit price (required for limit orders)")

    # sell
    p_sell = sub.add_parser("sell", help="Place a sell order")
    p_sell.add_argument("symbol", help="ASX ticker symbol")
    p_sell.add_argument("--qty", type=int, required=True, help="Number of shares")
    p_sell.add_argument("--type", default="limit", choices=["market", "limit"], help="Order type")
    p_sell.add_argument("--limit", type=float, help="Limit price (required for limit orders)")

    # open-orders
    sub.add_parser("open-orders", help="Show open orders")

    # cancel
    p_cancel = sub.add_parser("cancel", help="Cancel an order")
    p_cancel.add_argument("order_id", type=int, help="IB order ID to cancel")

    return parser


COMMAND_MAP = {
    "test-connection": cmd_test_connection,
    "account": cmd_account,
    "positions": cmd_positions,
    "quote": cmd_quote,
    "buy": cmd_buy,
    "sell": cmd_sell,
    "open-orders": cmd_open_orders,
    "cancel": cmd_cancel,
}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    _setup_logging(verbose=args.verbose)

    handler = COMMAND_MAP.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
