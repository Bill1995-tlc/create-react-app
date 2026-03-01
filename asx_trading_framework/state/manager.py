"""
State and persistence — positions, orders, fills, P&L tracking.

Provides a single source of truth for the framework's trading state.
Persists to JSON files for simplicity; can be swapped for a database.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..core.events import Event, EventBus, EventType
from ..core.types import (
    Fill,
    Order,  # Used for slippage tracking from fill events
    Position,
    Side,
    TradeRecord,
)

logger = logging.getLogger(__name__)


class StateManager:
    """
    Central state manager — tracks positions, fills, and P&L.

    Subscribes to order/fill events and maintains consistent state.
    Persists to disk for recovery.
    """

    def __init__(self, event_bus: EventBus, persist_dir: str = "./state") -> None:
        self.event_bus = event_bus
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        # State
        self._positions: dict[str, Position] = {}
        self._fills: list[Fill] = []
        self._completed_trades: list[TradeRecord] = []
        self._equity: Decimal = Decimal("100000")  # DEFAULT starting equity
        self._initial_equity: Decimal = Decimal("100000")
        self._daily_pnl: Decimal = Decimal("0")
        self._total_commission: Decimal = Decimal("0")

        # Track entry fill prices per symbol for slippage calculation
        self._entry_fill_prices: dict[str, Decimal] = {}
        # Track intended order prices per symbol (from signal) for slippage
        self._intended_prices: dict[str, Decimal] = {}

        # Subscribe to events
        self.event_bus.subscribe(EventType.ORDER_FILLED, self._on_order_filled)
        self.event_bus.subscribe(EventType.ORDER_PARTIALLY_FILLED, self._on_order_filled)

    def set_initial_equity(self, equity: Decimal) -> None:
        """Set starting equity."""
        self._equity = equity
        self._initial_equity = equity

    def _on_order_filled(self, event: Event) -> None:
        """Handle fill events — update positions and P&L."""
        fill: Fill = event.data.get("fill")
        if fill is None:
            return

        # Capture intended price from order for slippage calculation
        order: Order | None = event.data.get("order")
        if order and order.price is not None:
            self._intended_prices[fill.symbol] = order.price

        self._fills.append(fill)
        self._total_commission += fill.commission
        self._update_position(fill)
        self._persist_state()

    def _update_position(self, fill: Fill) -> None:
        """Update position based on a fill."""
        symbol = fill.symbol
        position = self._positions.get(symbol)

        if position is None:
            # New position — record entry fill price for slippage tracking
            quantity = fill.quantity if fill.side == Side.BUY else -fill.quantity
            self._positions[symbol] = Position(
                symbol=symbol,
                quantity=quantity,
                average_entry_price=fill.price,
                strategy_id="",
            )
            self._entry_fill_prices[symbol] = fill.price
            self.event_bus.publish(Event(
                event_type=EventType.POSITION_OPENED,
                data={"position": self._positions[symbol]},
                source="state_manager",
            ))
            return

        # Update existing position
        old_quantity = position.quantity
        delta = fill.quantity if fill.side == Side.BUY else -fill.quantity
        new_quantity = old_quantity + delta

        if new_quantity == 0:
            # Position closed — record the trade
            if old_quantity > 0:
                # Was long, now flat
                pnl = (fill.price - position.average_entry_price) * abs(old_quantity)
            else:
                # Was short, now flat
                pnl = (position.average_entry_price - fill.price) * abs(old_quantity)

            pnl -= fill.commission

            # Compute slippage: difference between intended and actual fill prices
            # Entry slippage: intended entry vs actual entry fill
            # Exit slippage: intended exit vs actual exit fill
            entry_slippage = Decimal("0")
            intended_entry = self._intended_prices.get(symbol)
            entry_fill = self._entry_fill_prices.get(symbol)
            if intended_entry and entry_fill:
                if old_quantity > 0:  # Long: paid more than intended
                    entry_slippage = (entry_fill - intended_entry) * abs(old_quantity)
                else:  # Short: received less than intended
                    entry_slippage = (intended_entry - entry_fill) * abs(old_quantity)
            total_slippage = abs(entry_slippage)

            trade_record = TradeRecord(
                trade_id=fill.fill_id,
                symbol=symbol,
                strategy_id=position.strategy_id,
                side=Side.BUY if old_quantity > 0 else Side.SELL,
                entry_price=position.average_entry_price,
                exit_price=fill.price,
                quantity=abs(old_quantity),
                entry_time=position.opened_at,
                exit_time=fill.timestamp,
                pnl=pnl,
                commission_total=fill.commission,
                slippage=total_slippage,
            )
            self._completed_trades.append(trade_record)
            self._daily_pnl += pnl
            self._equity += pnl

            # Clean up tracking state for this symbol
            self._entry_fill_prices.pop(symbol, None)
            self._intended_prices.pop(symbol, None)
            del self._positions[symbol]
            self.event_bus.publish(Event(
                event_type=EventType.POSITION_CLOSED,
                data={"trade": trade_record},
                source="state_manager",
            ))
            logger.info(
                "Position closed: %s PnL=%s equity=%s",
                symbol, pnl, self._equity,
            )
        else:
            # Position size changed
            if (old_quantity > 0 and delta > 0) or (old_quantity < 0 and delta < 0):
                # Adding to position — update average entry
                total_cost = position.average_entry_price * abs(old_quantity) + fill.price * abs(delta)
                position.average_entry_price = total_cost / abs(new_quantity)
            position.quantity = new_quantity
            self.event_bus.publish(Event(
                event_type=EventType.POSITION_UPDATED,
                data={"position": position},
                source="state_manager",
            ))

    def _persist_state(self) -> None:
        """Persist current state to disk."""
        state = {
            "equity": str(self._equity),
            "daily_pnl": str(self._daily_pnl),
            "total_commission": str(self._total_commission),
            "positions": {
                symbol: {
                    "quantity": pos.quantity,
                    "average_entry_price": str(pos.average_entry_price),
                    "strategy_id": pos.strategy_id,
                    "opened_at": pos.opened_at.isoformat(),
                }
                for symbol, pos in self._positions.items()
            },
            "timestamp": datetime.utcnow().isoformat(),
        }
        state_file = self.persist_dir / "current_state.json"
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)

    def load_state(self) -> bool:
        """Load state from disk. Returns True if loaded successfully."""
        state_file = self.persist_dir / "current_state.json"
        if not state_file.exists():
            return False

        with open(state_file) as f:
            state = json.load(f)

        self._equity = Decimal(state["equity"])
        self._daily_pnl = Decimal(state.get("daily_pnl", "0"))
        self._total_commission = Decimal(state.get("total_commission", "0"))

        self._positions = {}
        for symbol, pos_data in state.get("positions", {}).items():
            self._positions[symbol] = Position(
                symbol=symbol,
                quantity=pos_data["quantity"],
                average_entry_price=Decimal(pos_data["average_entry_price"]),
                strategy_id=pos_data.get("strategy_id", ""),
                opened_at=datetime.fromisoformat(pos_data["opened_at"]),
            )
        logger.info("Loaded state: equity=%s, positions=%d", self._equity, len(self._positions))
        return True

    def reset_daily(self) -> None:
        """Reset daily counters."""
        self._daily_pnl = Decimal("0")

    @property
    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    @property
    def equity(self) -> Decimal:
        return self._equity

    @property
    def daily_pnl(self) -> Decimal:
        return self._daily_pnl

    @property
    def completed_trades(self) -> list[TradeRecord]:
        return list(self._completed_trades)

    @property
    def total_commission(self) -> Decimal:
        return self._total_commission
