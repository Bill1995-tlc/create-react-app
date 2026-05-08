"""
Strategy 1: Opening Range Breakout (Crabel-inspired).

Signal definition:
- Define an Opening Range (OR) as the high and low of the first N minutes
  after market open (DEFAULT: 15 minutes, i.e., first 15 1-min bars).
- BUY signal: price breaks above OR high with volume confirmation.
- SELL signal (exit): price breaks below OR low or hits stop/target.

Entry: Market/limit order when price closes above OR high.
Stop: OR low (initial), then trail to breakeven after 1R move.
Target: 2x OR range (DEFAULT), or time-stop at 14:00 AEST.
Position sizing: fixed-risk model from risk engine.

Required data: 1-minute bars, volume.

Failure modes:
- Whipsaw in narrow ORs (filter: min OR range required).
- Gap days with extreme ORs (filter: max OR range as % of price).
- Low volume breakouts (filter: volume must exceed N-day average).

Regime filter:
- Prefer TRENDING_UP or HIGH_VOLATILITY regimes.
- Avoid LOW_VOLATILITY (ORB needs movement).
"""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from typing import Any

from ..core.types import Bar, MarketRegime, Signal, SignalAction
from ..signals.engine import Strategy


class OpeningRangeBreakout(Strategy):
    """
    Crabel-inspired ORB strategy for ASX equities.

    Config keys (all DEFAULT, configurable):
    - or_minutes: Opening range window in minutes (DEFAULT: 15)
    - min_or_range_pct: Minimum OR range as % of price (DEFAULT: 0.3%)
    - max_or_range_pct: Maximum OR range as % of price (DEFAULT: 3.0%)
    - volume_multiplier: Required volume vs 20-day avg (DEFAULT: 1.2x)
    - target_multiplier: Target as multiple of OR range (DEFAULT: 2.0)
    - time_stop: Time to force exit (DEFAULT: 14:00 AEST)
    - allowed_regimes: List of MarketRegime values to trade in
    """

    def __init__(
        self,
        strategy_id: str = "orb_crabel",
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(strategy_id, config)
        self.or_minutes: int = self.config.get("or_minutes", 15)  # DEFAULT
        self.min_or_range_pct = Decimal(str(self.config.get("min_or_range_pct", "0.003")))
        self.max_or_range_pct = Decimal(str(self.config.get("max_or_range_pct", "0.03")))
        self.volume_multiplier = Decimal(str(self.config.get("volume_multiplier", "1.2")))
        self.target_multiplier = Decimal(str(self.config.get("target_multiplier", "2.0")))
        self.time_stop = time.fromisoformat(self.config.get("time_stop", "14:00"))
        self.allowed_regimes: set[MarketRegime] = {
            MarketRegime.TRENDING_UP,
            MarketRegime.HIGH_VOLATILITY,
            MarketRegime.UNKNOWN,  # Allow when regime unknown
        }

        # Per-symbol state
        self._or_high: dict[str, Decimal] = {}
        self._or_low: dict[str, Decimal] = {}
        self._or_complete: dict[str, bool] = {}
        self._or_bars_count: dict[str, int] = {}
        self._or_volume: dict[str, int] = {}
        self._signal_fired: dict[str, bool] = {}
        self._current_date: dict[str, str] = {}

    @property
    def required_history(self) -> int:
        return 20  # Need 20 bars for volume average

    def on_bar(self, bar: Bar, regime: MarketRegime) -> Signal | None:
        """Process a bar and check for ORB signals."""
        symbol = bar.symbol
        date_key = bar.timestamp.strftime("%Y-%m-%d")

        # Reset state on new day
        if self._current_date.get(symbol) != date_key:
            self._current_date[symbol] = date_key
            self._or_high[symbol] = Decimal("-Infinity")
            self._or_low[symbol] = Decimal("Infinity")
            self._or_complete[symbol] = False
            self._or_bars_count[symbol] = 0
            self._or_volume[symbol] = 0
            self._signal_fired[symbol] = False

        bar_time = bar.timestamp.time()
        market_open = time(10, 0)  # ASX opens 10:00 AEST

        # Build opening range
        if not self._or_complete.get(symbol, False):
            if bar_time >= market_open:
                self._or_bars_count[symbol] = self._or_bars_count.get(symbol, 0) + 1
                self._or_high[symbol] = max(self._or_high.get(symbol, bar.high), bar.high)
                self._or_low[symbol] = min(self._or_low.get(symbol, bar.low), bar.low)
                self._or_volume[symbol] = self._or_volume.get(symbol, 0) + bar.volume

                if self._or_bars_count[symbol] >= self.or_minutes:
                    self._or_complete[symbol] = True
            return None

        # Don't fire more than once per day per symbol
        if self._signal_fired.get(symbol, False):
            return None

        # Time stop: no new entries after cutoff
        if bar_time >= self.time_stop:
            return None

        # Regime filter
        if regime not in self.allowed_regimes:
            return None

        or_high = self._or_high[symbol]
        or_low = self._or_low[symbol]
        or_range = or_high - or_low

        # Filter: OR range must be meaningful
        if or_high <= 0:
            return None
        or_range_pct = or_range / or_high
        if or_range_pct < self.min_or_range_pct or or_range_pct > self.max_or_range_pct:
            return None

        # Volume confirmation: current bar volume vs average
        history = self.get_history(symbol)
        if len(history) < 20:
            return None
        avg_volume = sum(b.volume for b in history[-20:]) / 20

        # Check for breakout above OR high
        if bar.close > or_high and bar.volume > float(self.volume_multiplier) * avg_volume:
            self._signal_fired[symbol] = True
            stop_loss = or_low
            target = bar.close + or_range * self.target_multiplier

            return Signal(
                strategy_id=self.strategy_id,
                symbol=symbol,
                action=SignalAction.ENTER_LONG,
                timestamp=bar.timestamp,
                price=bar.close,
                quantity=0,  # Risk engine computes size
                stop_loss=stop_loss,
                take_profit=target,
                confidence=0.0,
                metadata={
                    "or_high": str(or_high),
                    "or_low": str(or_low),
                    "or_range": str(or_range),
                    "or_range_pct": str(or_range_pct),
                    "bar_volume": bar.volume,
                    "avg_volume": avg_volume,
                },
            )

        return None
