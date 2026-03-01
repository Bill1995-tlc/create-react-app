"""
Custom exceptions for the IB adapter.

Each exception maps to a specific failure mode so callers can handle
them distinctly rather than catching a generic Exception.
"""

from __future__ import annotations


class IBAdapterError(Exception):
    """Base exception for all IB adapter errors."""


class IBConnectionError(IBAdapterError):
    """
    Failed to connect to TWS/IB Gateway.

    Common causes:
    - TWS/Gateway not running
    - Wrong host/port
    - API connections not enabled in TWS settings
    - Duplicate client ID (another app already connected with same ID)
    - Trusted IP not configured in TWS
    """

    def __init__(self, message: str, host: str = "", port: int = 0) -> None:
        self.host = host
        self.port = port
        detail = f" (host={host}, port={port})" if host else ""
        super().__init__(f"{message}{detail}")


class IBContractError(IBAdapterError):
    """
    Contract resolution failed.

    Common causes:
    - Invalid symbol
    - No ASX listing for this symbol
    - Ambiguous contract (multiple matches)
    - No trading permissions for this exchange
    """

    def __init__(self, symbol: str, detail: str = "") -> None:
        self.symbol = symbol
        msg = f"Contract resolution failed for '{symbol}'"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


class IBOrderError(IBAdapterError):
    """
    Order submission or management failed.

    Common causes:
    - Insufficient funds
    - Order size exceeds limits
    - Market closed
    - Invalid order parameters
    - No trading permissions for exchange
    """

    def __init__(self, message: str, order_id: int | None = None) -> None:
        self.order_id = order_id
        super().__init__(message)


class IBMarketDataError(IBAdapterError):
    """
    Market data request failed.

    Common causes:
    - No market data subscription for ASX
    - Market closed and no delayed data
    - Request rate limit exceeded
    - Symbol not found
    """

    def __init__(self, symbol: str, detail: str = "") -> None:
        self.symbol = symbol
        msg = f"Market data unavailable for '{symbol}'"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)
