"""
Strategy 3: Mean Reversion Under Strict Regime Filters (Raschke/Cook-inspired).

Signal definition:
- Identify oversold conditions using RSI or Bollinger Band touch.
- Entry ONLY when market regime is RANGE_BOUND (Cook-style context filter).
- Buy when price touches lower Bollinger Band AND RSI < threshold.

Stop: Below the recent swing low or N x ATR.
Target: Mean (middle Bollinger Band / 20-SMA).
Time stop: If no reversion within M bars, exit.

Required data: Intraday or daily bars.

Failure modes:
- Trending market = death by mean reversion. STRICT regime filter required.
- Catching falling knives in crashes. Hard stop is non-negotiable.
- News-driven moves that don't revert.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from ..core.types import Bar, MarketRegime, Signal, SignalAction
from ..signals.engine import Strategy


def sma(values: list[Decimal], period: int) -> Decimal:
    """Simple Moving Average."""
    if len(values) < period:
        return sum(values) / len(values) if values else Decimal("0")
    return sum(values[-period:]) / period


def std_dev(values: list[Decimal], period: int) -> Decimal:
    """Standard deviation over period."""
    if len(values) < period:
        return Decimal("0")
    subset = values[-period:]
    mean = sum(subset) / len(subset)
    variance = sum((x - mean) ** 2 for x in subset) / len(subset)
    # Decimal sqrt approximation
    if variance <= 0:
        return Decimal("0")
    # Newton's method for sqrt
    guess = variance / 2
    for _ in range(20):
        if guess <= 0:
            return Decimal("0")
        guess = (guess + variance / guess) / 2
    return guess


def rsi(closes: list[Decimal], period: int = 14) -> Decimal:
    """Relative Strength Index."""
    if len(closes) < period + 1:
        return Decimal("50")

    gains: list[Decimal] = []
    losses: list[Decimal] = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(Decimal("0"))
        else:
            gains.append(Decimal("0"))
            losses.append(abs(change))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return Decimal("100")
    rs = avg_gain / avg_loss
    return Decimal("100") - (Decimal("100") / (1 + rs))


class MeanReversion(Strategy):
    """
    Raschke/Cook-inspired mean reversion. ONLY trades in range-bound regimes.

    Config keys (all DEFAULT):
    - bb_period: Bollinger Band period (DEFAULT: 20)
    - bb_std: Bollinger Band standard deviations (DEFAULT: 2.0)
    - rsi_period: RSI period (DEFAULT: 14)
    - rsi_oversold: RSI threshold for oversold (DEFAULT: 30)
    - rsi_overbought: RSI threshold for overbought (DEFAULT: 70)
    - time_stop_bars: Exit if no reversion after N bars (DEFAULT: 10)
    - atr_stop_mult: ATR multiplier for stop (DEFAULT: 1.5)
    """

    def __init__(
        self,
        strategy_id: str = "mean_reversion_raschke",
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(strategy_id, config)
        self.bb_period: int = self.config.get("bb_period", 20)
        self.bb_std = Decimal(str(self.config.get("bb_std", "2.0")))
        self.rsi_period: int = self.config.get("rsi_period", 14)
        self.rsi_oversold = Decimal(str(self.config.get("rsi_oversold", "30")))
        self.rsi_overbought = Decimal(str(self.config.get("rsi_overbought", "70")))
        self.time_stop_bars: int = self.config.get("time_stop_bars", 10)
        self.atr_stop_mult = Decimal(str(self.config.get("atr_stop_mult", "1.5")))

        # STRICT: only range-bound regime
        self.allowed_regimes: set[MarketRegime] = {
            MarketRegime.RANGE_BOUND,
            MarketRegime.LOW_VOLATILITY,
        }

    @property
    def required_history(self) -> int:
        return max(self.bb_period, self.rsi_period) + 10

    def on_bar(self, bar: Bar, regime: MarketRegime) -> Signal | None:
        history = self.get_history(bar.symbol)
        if len(history) < self.required_history:
            return None

        # STRICT regime filter — this is the key Cook-style constraint
        if regime not in self.allowed_regimes:
            return None

        closes = [b.close for b in history]

        # Bollinger Bands
        middle_band = sma(closes, self.bb_period)
        sd = std_dev(closes, self.bb_period)
        lower_band = middle_band - self.bb_std * sd
        upper_band = middle_band + self.bb_std * sd

        # RSI
        current_rsi = rsi(closes, self.rsi_period)

        # Mean reversion long: price at/below lower band AND RSI oversold
        if bar.close <= lower_band and current_rsi < self.rsi_oversold:
            # Compute stop: recent swing low - ATR buffer
            from .momentum import atr as compute_atr
            current_atr = compute_atr(history, 14)
            recent_low = min(b.low for b in history[-10:])
            stop_loss = recent_low - current_atr * self.atr_stop_mult

            return Signal(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                action=SignalAction.ENTER_LONG,
                timestamp=bar.timestamp,
                price=bar.close,
                quantity=0,
                stop_loss=stop_loss,
                take_profit=middle_band,  # Target = mean
                confidence=0.0,
                metadata={
                    "rsi": str(current_rsi),
                    "lower_band": str(lower_band),
                    "middle_band": str(middle_band),
                    "upper_band": str(upper_band),
                    "regime": regime.value,
                },
            )

        return None
