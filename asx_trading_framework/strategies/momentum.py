"""
Strategy 2: Momentum Continuation (Zanger-inspired).

Signal definition:
- Scan for stocks making N-day highs with above-average volume.
- Entry: Buy on pullback to rising EMA (e.g., 8-EMA) within an uptrend.
- Confirmation: Volume on breakout bar > 1.5x 20-day average.
- Price pattern: stock has gained > X% in last Y days.

Stop: Below the pullback low or N x ATR below entry.
Target: Trail using ATR-based trailing stop.
Time stop: Exit at EOD if configured for day trading.

Required data: Daily or intraday bars with volume.

Failure modes:
- Chasing extended moves (filter: don't enter if > Z% above 20-EMA).
- Climax tops / exhaustion gaps (filter: avoid if volume > 3x avg with reversal candle).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from ..core.types import Bar, MarketRegime, Signal, SignalAction
from ..signals.engine import Strategy


def ema(values: list[Decimal], period: int) -> Decimal:
    """Compute Exponential Moving Average."""
    if not values:
        return Decimal("0")
    if len(values) < period:
        return sum(values) / len(values)

    multiplier = Decimal(2) / (Decimal(period) + 1)
    result = sum(values[:period]) / period

    for val in values[period:]:
        result = (val - result) * multiplier + result
    return result


def atr(bars: list[Bar], period: int = 14) -> Decimal:
    """Compute Average True Range."""
    if len(bars) < 2:
        return Decimal("0")

    true_ranges: list[Decimal] = []
    for i in range(1, len(bars)):
        high_low = bars[i].high - bars[i].low
        high_close = abs(bars[i].high - bars[i - 1].close)
        low_close = abs(bars[i].low - bars[i - 1].close)
        true_ranges.append(max(high_low, high_close, low_close))

    if not true_ranges:
        return Decimal("0")
    return sum(true_ranges[-period:]) / min(len(true_ranges), period)


class MomentumContinuation(Strategy):
    """
    Zanger-inspired momentum continuation strategy.

    Config keys (all DEFAULT):
    - ema_period: EMA period for trend (DEFAULT: 8)
    - lookback_days: Period for high/momentum check (DEFAULT: 20)
    - min_gain_pct: Minimum % gain over lookback (DEFAULT: 5%)
    - volume_breakout_multiplier: Volume vs avg for entry (DEFAULT: 1.5)
    - max_extension_pct: Max % above EMA to avoid chasing (DEFAULT: 5%)
    - atr_stop_multiplier: ATR multiplier for stop (DEFAULT: 2.0)
    - atr_trail_multiplier: ATR multiplier for trailing stop (DEFAULT: 1.5)
    - climax_volume_multiplier: Volume threshold for exhaustion filter (DEFAULT: 3.0)
    """

    def __init__(
        self,
        strategy_id: str = "momentum_zanger",
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(strategy_id, config)
        self.ema_period: int = self.config.get("ema_period", 8)
        self.lookback_days: int = self.config.get("lookback_days", 20)
        self.min_gain_pct = Decimal(str(self.config.get("min_gain_pct", "0.05")))
        self.volume_breakout_mult = Decimal(str(self.config.get("volume_breakout_multiplier", "1.5")))
        self.max_extension_pct = Decimal(str(self.config.get("max_extension_pct", "0.05")))
        self.atr_stop_mult = Decimal(str(self.config.get("atr_stop_multiplier", "2.0")))
        self.atr_trail_mult = Decimal(str(self.config.get("atr_trail_multiplier", "1.5")))
        self.climax_volume_mult = Decimal(str(self.config.get("climax_volume_multiplier", "3.0")))
        self.allowed_regimes: set[MarketRegime] = {
            MarketRegime.TRENDING_UP,
            MarketRegime.HIGH_VOLATILITY,
            MarketRegime.UNKNOWN,
        }

    @property
    def required_history(self) -> int:
        return max(self.lookback_days, self.ema_period) + 5

    def on_bar(self, bar: Bar, regime: MarketRegime) -> Signal | None:
        history = self.get_history(bar.symbol)
        if len(history) < self.required_history:
            return None

        if regime not in self.allowed_regimes:
            return None

        closes = [b.close for b in history]
        current_ema = ema(closes, self.ema_period)

        # Momentum check: has the stock gained enough over lookback?
        lookback_close = closes[-self.lookback_days] if len(closes) >= self.lookback_days else closes[0]
        if lookback_close <= 0:
            return None
        gain_pct = (bar.close - lookback_close) / lookback_close
        if gain_pct < self.min_gain_pct:
            return None

        # Is price near the EMA? (pullback to trend)
        if current_ema <= 0:
            return None
        extension = (bar.close - current_ema) / current_ema

        # Must be above EMA (uptrend) but not too extended
        if extension < 0 or extension > self.max_extension_pct:
            return None

        # Volume confirmation
        avg_vol = sum(b.volume for b in history[-20:]) / 20
        if avg_vol <= 0:
            return None
        if bar.volume < float(self.volume_breakout_mult) * avg_vol:
            return None

        # Exhaustion filter: reject if volume too extreme with reversal candle
        if bar.volume > float(self.climax_volume_mult) * avg_vol:
            # Check for bearish reversal candle (close < open, long upper wick)
            if bar.close < bar.open:
                return None

        # Compute ATR for stop
        current_atr = atr(history, 14)
        if current_atr <= 0:
            return None

        stop_loss = bar.close - current_atr * self.atr_stop_mult

        return Signal(
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            action=SignalAction.ENTER_LONG,
            timestamp=bar.timestamp,
            price=bar.close,
            quantity=0,  # Risk engine computes
            stop_loss=stop_loss,
            confidence=0.0,
            metadata={
                "ema": str(current_ema),
                "gain_pct": str(gain_pct),
                "extension": str(extension),
                "atr": str(current_atr),
                "volume_ratio": f"{bar.volume / avg_vol:.2f}",
            },
        )
