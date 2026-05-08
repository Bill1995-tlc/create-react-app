"""
Interactive Brokers adapter for ASX equities.

Uses ib_async (modern async IB API library) instead of the raw ibapi
callback-style library. Provides:

- Async connection management with auto-reconnect
- ASX contract resolution and qualification
- Account summary and position queries
- Market data snapshots
- Market and limit order placement
- Order cancellation
- CLI for interactive use

Usage:
    from asx_trading_framework.broker.ib import IBAdapter

    adapter = IBAdapter()
    await adapter.connect()
    positions = await adapter.get_positions()
    await adapter.disconnect()
"""

from .adapter import IBAdapter
from .config import IBConfig
from .errors import (
    IBAdapterError,
    IBConnectionError,
    IBContractError,
    IBOrderError,
    IBMarketDataError,
)

__all__ = [
    "IBAdapter",
    "IBConfig",
    "IBAdapterError",
    "IBConnectionError",
    "IBContractError",
    "IBOrderError",
    "IBMarketDataError",
]
