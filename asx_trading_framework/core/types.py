"""
Core type definitions for the ASX trading framework.

All domain types are defined here to ensure consistency across modules.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from typing import Any


# ──────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────

class Side(enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(enum.Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(enum.Enum):
    """Order lifecycle states — deterministic state machine."""
    PENDING_NEW = "PENDING_NEW"
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    PENDING_CANCEL = "PENDING_CANCEL"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class TimeInForce(enum.Enum):
    DAY = "DAY"        # Expire at end of trading session
    IOC = "IOC"        # Immediate or cancel
    FOK = "FOK"        # Fill or kill
    GTC = "GTC"        # Good till cancelled


class MarketRegime(enum.Enum):
    """Regime classification for context filters (Cook-style)."""
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGE_BOUND = "RANGE_BOUND"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    UNKNOWN = "UNKNOWN"


class TradingPhase(enum.Enum):
    """ASX trading phases."""
    PRE_OPEN = "PRE_OPEN"            # 07:00–10:00 AEST
    OPEN_AUCTION = "OPEN_AUCTION"    # ~10:00:00+random
    CONTINUOUS = "CONTINUOUS"         # 10:00–16:00 AEST
    PRE_CLOSE = "PRE_CLOSE"          # 16:00–16:10
    CLOSE_AUCTION = "CLOSE_AUCTION"  # ~16:10+random
    CLOSED = "CLOSED"


class SignalAction(enum.Enum):
    ENTER_LONG = "ENTER_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    EXIT = "EXIT"
    NO_ACTION = "NO_ACTION"


class RiskVeto(enum.Enum):
    ALLOWED = "ALLOWED"
    MAX_LOSS_PER_TRADE = "MAX_LOSS_PER_TRADE"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    WEEKLY_LOSS_LIMIT = "WEEKLY_LOSS_LIMIT"
    MAX_POSITIONS = "MAX_POSITIONS"
    MAX_TRADES_PER_DAY = "MAX_TRADES_PER_DAY"
    PARTICIPATION_RATE = "PARTICIPATION_RATE"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
    NEWS_WINDOW = "NEWS_WINDOW"
    KILL_SWITCH = "KILL_SWITCH"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    MANUAL_HALT = "MANUAL_HALT"


# ──────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────

@dataclass(frozen=True)
class Bar:
    """OHLCV bar — immutable."""
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    vwap: Decimal | None = None
    trade_count: int | None = None


@dataclass(frozen=True)
class Quote:
    """Best bid/ask snapshot."""
    symbol: str
    timestamp: datetime
    bid: Decimal
    bid_size: int
    ask: Decimal
    ask_size: int

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2


@dataclass
class Order:
    """Mutable order object tracking lifecycle."""
    order_id: str
    symbol: str
    side: Side
    order_type: OrderType
    quantity: int
    price: Decimal | None = None  # None for MARKET orders
    stop_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    status: OrderStatus = OrderStatus.PENDING_NEW
    filled_quantity: int = 0
    average_fill_price: Decimal | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    strategy_id: str = ""
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def remaining_quantity(self) -> int:
        return self.quantity - self.filled_quantity

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        )


@dataclass
class Fill:
    """Execution report for a fill."""
    fill_id: str
    order_id: str
    symbol: str
    side: Side
    quantity: int
    price: Decimal
    commission: Decimal
    timestamp: datetime
    exchange_trade_id: str = ""


@dataclass
class Position:
    """Current position in a symbol."""
    symbol: str
    quantity: int  # Positive = long, negative = short
    average_entry_price: Decimal
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    strategy_id: str = ""
    opened_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class Signal:
    """Output of the signal engine — request for an action."""
    strategy_id: str
    symbol: str
    action: SignalAction
    timestamp: datetime
    price: Decimal
    quantity: int
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    confidence: float = 0.0  # 0-1, informational only
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskCheckResult:
    """Result of a pre-trade risk check."""
    allowed: bool
    veto_reason: RiskVeto
    worst_case_loss: Decimal
    details: str = ""


@dataclass
class TradeRecord:
    """Completed round-trip trade for journaling."""
    trade_id: str
    symbol: str
    strategy_id: str
    side: Side
    entry_price: Decimal
    exit_price: Decimal
    quantity: int
    entry_time: datetime
    exit_time: datetime
    pnl: Decimal
    commission_total: Decimal
    slippage: Decimal
    tags: dict[str, str] = field(default_factory=dict)
    notes: str = ""
    mistake_tags: list[str] = field(default_factory=list)


@dataclass
class DailyStats:
    """Aggregated daily performance stats."""
    date: datetime
    trades_count: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    gross_pnl: Decimal = Decimal("0")
    total_commission: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    max_gain: Decimal = Decimal("0")
    largest_win: Decimal = Decimal("0")
    largest_loss: Decimal = Decimal("0")
