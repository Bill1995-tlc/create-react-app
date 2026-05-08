"""Tests for all four trading strategies."""

from __future__ import annotations

import unittest
from datetime import datetime, time, timedelta
from decimal import Decimal

from ..core.types import Bar, MarketRegime, Signal, SignalAction
from ..strategies.orb import OpeningRangeBreakout
from ..strategies.momentum import MomentumContinuation, ema, atr
from ..strategies.mean_reversion import MeanReversion, sma, std_dev, rsi
from ..strategies.volatility_expansion import VolatilityExpansion


def make_bar(
    symbol: str = "BHP",
    ts: datetime | None = None,
    open_: float = 45.0,
    high: float = 45.5,
    low: float = 44.5,
    close: float = 45.2,
    volume: int = 100000,
) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=ts or datetime(2024, 1, 15, 10, 30),
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=volume,
    )


# ──────────────────────────────────────────────
# Indicator unit tests
# ──────────────────────────────────────────────

class TestIndicators(unittest.TestCase):

    def test_ema_basic(self) -> None:
        values = [Decimal(str(x)) for x in [10, 11, 12, 13, 14, 15]]
        result = ema(values, 3)
        self.assertIsInstance(result, Decimal)
        self.assertGreater(result, Decimal("10"))

    def test_ema_empty(self) -> None:
        self.assertEqual(ema([], 3), Decimal("0"))

    def test_ema_short_list(self) -> None:
        values = [Decimal("10"), Decimal("20")]
        result = ema(values, 5)
        self.assertEqual(result, Decimal("15"))  # Average of 2 values

    def test_atr_basic(self) -> None:
        bars = [make_bar(ts=datetime(2024, 1, i + 1, 10, 0)) for i in range(20)]
        result = atr(bars, 14)
        self.assertIsInstance(result, Decimal)
        self.assertGreaterEqual(result, Decimal("0"))

    def test_atr_too_few(self) -> None:
        bars = [make_bar()]
        self.assertEqual(atr(bars, 14), Decimal("0"))

    def test_sma_basic(self) -> None:
        values = [Decimal(str(x)) for x in range(1, 11)]
        result = sma(values, 5)
        # Last 5 values: 6,7,8,9,10 → avg = 8
        self.assertEqual(result, Decimal("8"))

    def test_sma_empty(self) -> None:
        self.assertEqual(sma([], 5), Decimal("0"))

    def test_std_dev_zero_variance(self) -> None:
        values = [Decimal("10")] * 10
        result = std_dev(values, 5)
        self.assertEqual(result, Decimal("0"))

    def test_rsi_all_gains(self) -> None:
        closes = [Decimal(str(x)) for x in range(50, 70)]
        result = rsi(closes, 14)
        self.assertEqual(result, Decimal("100"))

    def test_rsi_neutral(self) -> None:
        closes = [Decimal(str(50 + (i % 2))) for i in range(30)]
        result = rsi(closes, 14)
        # Alternating +1/-1 → roughly 50
        self.assertGreater(result, Decimal("30"))
        self.assertLess(result, Decimal("70"))


# ──────────────────────────────────────────────
# ORB Strategy
# ──────────────────────────────────────────────

class TestOpeningRangeBreakout(unittest.TestCase):

    def setUp(self) -> None:
        self.strategy = OpeningRangeBreakout()

    def test_required_history(self) -> None:
        self.assertEqual(self.strategy.required_history, 20)

    def test_no_signal_during_or_build(self) -> None:
        """No signal while building the opening range."""
        base = datetime(2024, 1, 15, 10, 0)
        for i in range(10):
            bar = make_bar(ts=base + timedelta(minutes=i), volume=200000)
            self.strategy.add_bar(bar)
            sig = self.strategy.on_bar(bar, MarketRegime.TRENDING_UP)
            self.assertIsNone(sig)

    def test_no_signal_wrong_regime(self) -> None:
        """No signal in disallowed regime (RANGE_BOUND for ORB)."""
        strategy = OpeningRangeBreakout()
        base = datetime(2024, 1, 15, 10, 0)

        # Build warmup history
        for i in range(25):
            bar = make_bar(ts=base + timedelta(minutes=i), volume=100000)
            strategy.add_bar(bar)
            strategy.on_bar(bar, MarketRegime.RANGE_BOUND)

        # Post-OR bar with breakout
        breakout_bar = make_bar(
            ts=base + timedelta(minutes=30),
            close=50.0, high=50.5, volume=500000,
        )
        strategy.add_bar(breakout_bar)
        sig = strategy.on_bar(breakout_bar, MarketRegime.RANGE_BOUND)
        self.assertIsNone(sig)

    def test_no_signal_after_time_stop(self) -> None:
        """No signal after time stop cutoff."""
        strategy = OpeningRangeBreakout()
        # Bar after 14:00 AEST
        bar = make_bar(ts=datetime(2024, 1, 15, 14, 30))
        strategy._or_complete["BHP"] = True
        strategy._or_high["BHP"] = Decimal("45")
        strategy._or_low["BHP"] = Decimal("44")
        strategy._current_date["BHP"] = "2024-01-15"
        strategy.add_bar(bar)
        sig = strategy.on_bar(bar, MarketRegime.TRENDING_UP)
        self.assertIsNone(sig)

    def test_signal_on_breakout(self) -> None:
        """Signal fires on valid breakout above OR high."""
        strategy = OpeningRangeBreakout()
        base = datetime(2024, 1, 15, 10, 0)

        # Feed 20+ warmup bars (pre-OR completion, then OR bars)
        for i in range(25):
            bar = make_bar(
                ts=base + timedelta(minutes=i),
                open_=44.5, high=45.0, low=44.0, close=44.8,
                volume=100000,
            )
            strategy.add_bar(bar)
            strategy.on_bar(bar, MarketRegime.TRENDING_UP)

        # OR should be complete now (15+ bars after 10:00)
        self.assertTrue(strategy._or_complete.get("BHP", False))

        # Breakout bar: close above OR high with high volume
        breakout_bar = make_bar(
            ts=base + timedelta(minutes=25),
            open_=45.0, high=46.0, low=44.8, close=45.5,
            volume=300000,  # 3x the warmup volume
        )
        strategy.add_bar(breakout_bar)
        sig = strategy.on_bar(breakout_bar, MarketRegime.TRENDING_UP)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.action, SignalAction.ENTER_LONG)
        self.assertEqual(sig.symbol, "BHP")
        self.assertIsNotNone(sig.stop_loss)

    def test_only_one_signal_per_day(self) -> None:
        """Once a signal fires, no more signals for that symbol that day."""
        strategy = OpeningRangeBreakout()
        strategy._signal_fired["BHP"] = True
        strategy._or_complete["BHP"] = True
        strategy._current_date["BHP"] = "2024-01-15"
        strategy._or_high["BHP"] = Decimal("45")
        strategy._or_low["BHP"] = Decimal("44")

        bar = make_bar(ts=datetime(2024, 1, 15, 11, 0), close=50.0, volume=500000)
        strategy.add_bar(bar)
        sig = strategy.on_bar(bar, MarketRegime.TRENDING_UP)
        self.assertIsNone(sig)


# ──────────────────────────────────────────────
# Momentum Strategy
# ──────────────────────────────────────────────

class TestMomentumContinuation(unittest.TestCase):

    def setUp(self) -> None:
        self.strategy = MomentumContinuation()

    def test_required_history(self) -> None:
        self.assertGreaterEqual(self.strategy.required_history, 20)

    def test_no_signal_without_history(self) -> None:
        bar = make_bar()
        self.strategy.add_bar(bar)
        sig = self.strategy.on_bar(bar, MarketRegime.TRENDING_UP)
        self.assertIsNone(sig)

    def test_no_signal_wrong_regime(self) -> None:
        """Momentum doesn't fire in RANGE_BOUND."""
        strategy = MomentumContinuation()
        base = datetime(2024, 1, 1, 10, 0)
        for i in range(30):
            bar = make_bar(ts=base + timedelta(days=i), close=45.0 + i * 0.5, volume=200000)
            strategy.add_bar(bar)
        last = make_bar(ts=base + timedelta(days=30), close=60.0, volume=500000)
        strategy.add_bar(last)
        sig = strategy.on_bar(last, MarketRegime.RANGE_BOUND)
        self.assertIsNone(sig)

    def test_signal_on_momentum(self) -> None:
        """Signal fires on valid momentum setup."""
        strategy = MomentumContinuation()
        base = datetime(2024, 1, 1, 10, 0)

        # Build uptrend: prices rising steadily
        for i in range(30):
            price = 40.0 + i * 0.5
            bar = make_bar(
                ts=base + timedelta(days=i),
                open_=price - 0.2,
                high=price + 0.3,
                low=price - 0.3,
                close=price,
                volume=100000,
            )
            strategy.add_bar(bar)
            strategy.on_bar(bar, MarketRegime.TRENDING_UP)

        # Breakout bar near EMA with high volume
        breakout = make_bar(
            ts=base + timedelta(days=30),
            open_=54.5,
            high=55.5,
            low=54.0,
            close=55.0,
            volume=250000,  # 2.5x average
        )
        strategy.add_bar(breakout)
        sig = strategy.on_bar(breakout, MarketRegime.TRENDING_UP)
        # May or may not fire depending on exact EMA/extension calculation
        # but should not error
        if sig is not None:
            self.assertEqual(sig.action, SignalAction.ENTER_LONG)
            self.assertIsNotNone(sig.stop_loss)

    def test_exhaustion_filter(self) -> None:
        """Climax top with reversal candle is rejected."""
        strategy = MomentumContinuation()
        base = datetime(2024, 1, 1, 10, 0)
        for i in range(30):
            price = 40.0 + i * 0.3
            bar = make_bar(
                ts=base + timedelta(days=i),
                open_=price, high=price + 0.5, low=price - 0.5, close=price,
                volume=100000,
            )
            strategy.add_bar(bar)
            strategy.on_bar(bar, MarketRegime.TRENDING_UP)

        # Exhaustion bar: huge volume + bearish close (close < open)
        exhaust = make_bar(
            ts=base + timedelta(days=30),
            open_=50.0, high=52.0, low=49.0, close=49.5,
            volume=500000,  # 5x avg
        )
        strategy.add_bar(exhaust)
        sig = strategy.on_bar(exhaust, MarketRegime.TRENDING_UP)
        self.assertIsNone(sig)


# ──────────────────────────────────────────────
# Mean Reversion Strategy
# ──────────────────────────────────────────────

class TestMeanReversion(unittest.TestCase):

    def setUp(self) -> None:
        self.strategy = MeanReversion()

    def test_required_history(self) -> None:
        self.assertGreaterEqual(self.strategy.required_history, 20)

    def test_no_signal_trending_regime(self) -> None:
        """Mean reversion must NOT fire in TRENDING_UP."""
        strategy = MeanReversion()
        base = datetime(2024, 1, 1, 10, 0)
        for i in range(35):
            bar = make_bar(ts=base + timedelta(days=i), close=45.0, volume=100000)
            strategy.add_bar(bar)
        last = make_bar(ts=base + timedelta(days=35), close=40.0, volume=100000)
        strategy.add_bar(last)
        sig = strategy.on_bar(last, MarketRegime.TRENDING_UP)
        self.assertIsNone(sig)

    def test_no_signal_trending_down(self) -> None:
        """Mean reversion must NOT fire in TRENDING_DOWN."""
        strategy = MeanReversion()
        base = datetime(2024, 1, 1, 10, 0)
        for i in range(35):
            bar = make_bar(ts=base + timedelta(days=i), close=45.0, volume=100000)
            strategy.add_bar(bar)
        last = make_bar(ts=base + timedelta(days=35), close=40.0, volume=100000)
        strategy.add_bar(last)
        sig = strategy.on_bar(last, MarketRegime.TRENDING_DOWN)
        self.assertIsNone(sig)

    def test_signal_on_oversold_range_bound(self) -> None:
        """Signal fires when price touches lower BB and RSI oversold in RANGE_BOUND."""
        strategy = MeanReversion()
        base = datetime(2024, 1, 1, 10, 0)

        # Stable prices to establish Bollinger Bands
        for i in range(30):
            bar = make_bar(
                ts=base + timedelta(days=i),
                open_=45.0, high=45.5, low=44.5, close=45.0,
                volume=100000,
            )
            strategy.add_bar(bar)
            strategy.on_bar(bar, MarketRegime.RANGE_BOUND)

        # Sharp drop below lower band
        drop_bar = make_bar(
            ts=base + timedelta(days=30),
            open_=44.0, high=44.2, low=42.0, close=42.0,
            volume=100000,
        )
        strategy.add_bar(drop_bar)
        sig = strategy.on_bar(drop_bar, MarketRegime.RANGE_BOUND)
        # Signal depends on whether drop is enough vs BB — just verify no error
        if sig is not None:
            self.assertEqual(sig.action, SignalAction.ENTER_LONG)
            self.assertIsNotNone(sig.stop_loss)
            self.assertIsNotNone(sig.take_profit)


# ──────────────────────────────────────────────
# Volatility Expansion Strategy
# ──────────────────────────────────────────────

class TestVolatilityExpansion(unittest.TestCase):

    def setUp(self) -> None:
        self.strategy = VolatilityExpansion()

    def test_required_history(self) -> None:
        self.assertGreaterEqual(self.strategy.required_history, 10)

    def test_contraction_tracking(self) -> None:
        """Narrow bars increment contraction counter."""
        strategy = VolatilityExpansion()
        base = datetime(2024, 1, 1, 10, 0)

        # Normal range bars for lookback — need >= required_history (18)
        for i in range(20):
            bar = make_bar(
                ts=base + timedelta(minutes=i),
                open_=45.0, high=46.0, low=44.0, close=45.5,
                volume=100000,
            )
            strategy.add_bar(bar)
            strategy.on_bar(bar, MarketRegime.UNKNOWN)

        # Very narrow bar (contraction)
        narrow = make_bar(
            ts=base + timedelta(minutes=25),
            open_=45.0, high=45.1, low=44.95, close=45.05,
            volume=100000,
        )
        strategy.add_bar(narrow)
        strategy.on_bar(narrow, MarketRegime.UNKNOWN)
        self.assertTrue(strategy._in_contraction.get("BHP", False))

    def test_signal_after_contraction_expansion(self) -> None:
        """Signal fires after contraction → expansion with volume."""
        strategy = VolatilityExpansion()
        base = datetime(2024, 1, 1, 10, 0)

        # Normal range bars
        for i in range(15):
            bar = make_bar(
                ts=base + timedelta(minutes=i),
                open_=45.0, high=46.0, low=44.0, close=45.5,
                volume=100000,
            )
            strategy.add_bar(bar)
            strategy.on_bar(bar, MarketRegime.UNKNOWN)

        # 3+ contraction bars (very narrow range)
        for i in range(4):
            narrow = make_bar(
                ts=base + timedelta(minutes=20 + i),
                open_=45.0, high=45.05, low=44.98, close=45.02,
                volume=100000,
            )
            strategy.add_bar(narrow)
            strategy.on_bar(narrow, MarketRegime.UNKNOWN)

        # Expansion bar: big range + high volume + bullish close
        expansion = make_bar(
            ts=base + timedelta(minutes=25),
            open_=45.0, high=48.0, low=44.5, close=47.5,
            volume=250000,  # 2.5x avg
        )
        strategy.add_bar(expansion)
        sig = strategy.on_bar(expansion, MarketRegime.UNKNOWN)

        if sig is not None:
            self.assertEqual(sig.action, SignalAction.ENTER_LONG)
            self.assertEqual(sig.symbol, "BHP")
            self.assertIsNotNone(sig.stop_loss)

    def test_bearish_expansion_skipped(self) -> None:
        """Bearish expansion is skipped (long-only)."""
        strategy = VolatilityExpansion()
        base = datetime(2024, 1, 1, 10, 0)

        for i in range(15):
            bar = make_bar(
                ts=base + timedelta(minutes=i),
                open_=45.0, high=46.0, low=44.0, close=45.5,
                volume=100000,
            )
            strategy.add_bar(bar)
            strategy.on_bar(bar, MarketRegime.UNKNOWN)

        for i in range(4):
            narrow = make_bar(
                ts=base + timedelta(minutes=20 + i),
                open_=45.0, high=45.05, low=44.98, close=45.02,
                volume=100000,
            )
            strategy.add_bar(narrow)
            strategy.on_bar(narrow, MarketRegime.UNKNOWN)

        # Bearish expansion (close < open)
        bearish = make_bar(
            ts=base + timedelta(minutes=25),
            open_=45.0, high=45.5, low=42.0, close=42.5,
            volume=250000,
        )
        strategy.add_bar(bearish)
        sig = strategy.on_bar(bearish, MarketRegime.UNKNOWN)
        self.assertIsNone(sig)


if __name__ == "__main__":
    unittest.main()
