"""
Strategy 4: Volatility Expansion with Tight Risk (Pattern-based).

Signal definition:
- Detect a volatility contraction (narrow range bars / low ATR relative to recent).
- Entry on the expansion bar (range > N x recent average range).
- Direction: follow the expansion direction with volume confirmation.

Stop: Opposite end of the expansion bar.
Target: 1.5–2x the expansion range.
Position sizing: tight because stop is already defined by the pattern.

Required data: Intraday bars.

Failure modes:
- False breakouts from contraction (use volume filter).
- Multiple contraction bars with no expansion (patience; no forced entries).
- Expansion into a halt or gap (hard stop; position size already limits damage).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..core.types import Bar, MarketRegime, Signal, SignalAction
from ..signals.engine import Strategy


class VolatilityExpansion(Strategy):
    """
    Volatility contraction → expansion strategy.

    Config keys (all DEFAULT):
    - lookback_bars: Period for computing average range (DEFAULT: 10)
    - contraction_threshold: Current range / avg range must be < this (DEFAULT: 0.6)
    - expansion_threshold: Expansion bar range / avg range must be > this (DEFAULT: 1.5)
    - volume_confirm_mult: Volume on expansion must be > this x avg (DEFAULT: 1.3)
    - target_multiplier: Target as multiple of expansion range (DEFAULT: 1.5)
    - min_contraction_bars: Min consecutive narrow bars (DEFAULT: 3)
    """

    def __init__(
        self,
        strategy_id: str = "vol_expansion",
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(strategy_id, config)
        self.lookback_bars: int = self.config.get("lookback_bars", 10)
        self.contraction_threshold = Decimal(str(self.config.get("contraction_threshold", "0.6")))
        self.expansion_threshold = Decimal(str(self.config.get("expansion_threshold", "1.5")))
        self.volume_confirm_mult = Decimal(str(self.config.get("volume_confirm_mult", "1.3")))
        self.target_multiplier = Decimal(str(self.config.get("target_multiplier", "1.5")))
        self.min_contraction_bars: int = self.config.get("min_contraction_bars", 3)

        # Track contraction state per symbol
        self._contraction_count: dict[str, int] = {}
        self._in_contraction: dict[str, bool] = {}

    @property
    def required_history(self) -> int:
        return self.lookback_bars + self.min_contraction_bars + 5

    def on_bar(self, bar: Bar, regime: MarketRegime) -> Signal | None:
        history = self.get_history(bar.symbol)
        if len(history) < self.required_history:
            return None

        symbol = bar.symbol

        # Compute average range over lookback
        lookback_start = -(self.lookback_bars + 1)
        lookback_end = -1  # Exclude current bar
        lookback_slice = history[lookback_start:lookback_end]
        if not lookback_slice:
            return None

        ranges = [b.high - b.low for b in lookback_slice]
        avg_range = sum(ranges) / len(ranges)
        if avg_range <= 0:
            return None

        current_range = bar.high - bar.low
        range_ratio = current_range / avg_range

        # Track contraction
        if range_ratio < self.contraction_threshold:
            self._contraction_count[symbol] = self._contraction_count.get(symbol, 0) + 1
            self._in_contraction[symbol] = True
            return None

        # Check for expansion after sufficient contraction
        if (
            self._in_contraction.get(symbol, False)
            and self._contraction_count.get(symbol, 0) >= self.min_contraction_bars
            and range_ratio > self.expansion_threshold
        ):
            # Volume confirmation
            avg_vol = sum(b.volume for b in history[-20:]) / 20
            if avg_vol <= 0 or bar.volume < float(self.volume_confirm_mult) * avg_vol:
                self._contraction_count[symbol] = 0
                self._in_contraction[symbol] = False
                return None

            # Determine direction: follow the close
            if bar.close > bar.open:
                # Bullish expansion
                action = SignalAction.ENTER_LONG
                stop_loss = bar.low  # Stop at opposite end
                target = bar.close + current_range * self.target_multiplier
            else:
                # Skip bearish for now (long-only ASX constraint)
                self._contraction_count[symbol] = 0
                self._in_contraction[symbol] = False
                return None

            # Reset contraction state
            self._contraction_count[symbol] = 0
            self._in_contraction[symbol] = False

            return Signal(
                strategy_id=self.strategy_id,
                symbol=symbol,
                action=action,
                timestamp=bar.timestamp,
                price=bar.close,
                quantity=0,
                stop_loss=stop_loss,
                take_profit=target,
                confidence=0.0,
                metadata={
                    "range_ratio": str(range_ratio),
                    "avg_range": str(avg_range),
                    "current_range": str(current_range),
                    "contraction_bars": str(self.min_contraction_bars),
                    "volume_ratio": f"{bar.volume / avg_vol:.2f}" if avg_vol > 0 else "N/A",
                },
            )
        else:
            # Not in contraction or not enough contraction bars
            if range_ratio >= self.contraction_threshold:
                self._contraction_count[symbol] = 0
                self._in_contraction[symbol] = False

        return None
