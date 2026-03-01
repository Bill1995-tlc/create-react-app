"""Tests for signal engine and regime detection."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from ..core.config import FrameworkConfig, SignalConfig
from ..core.events import EventBus, EventType, Event
from ..core.types import Bar, MarketRegime, Signal, SignalAction
from ..signals.engine import RegimeDetector, SignalEngine, Strategy


def make_bar(
    symbol: str = "XJO",
    ts: datetime | None = None,
    close: float = 7500.0,
    high: float | None = None,
    low: float | None = None,
    volume: int = 1000000,
) -> Bar:
    c = Decimal(str(close))
    return Bar(
        symbol=symbol,
        timestamp=ts or datetime(2024, 1, 15, 10, 0),
        open=c,
        high=Decimal(str(high)) if high else c + Decimal("10"),
        low=Decimal(str(low)) if low else c - Decimal("10"),
        close=c,
        volume=volume,
    )


class DummyStrategy(Strategy):
    """Test strategy that fires on every bar."""

    def __init__(self, fire: bool = True) -> None:
        super().__init__("test_strategy")
        self.fire = fire
        self.bars_received: list[Bar] = []

    @property
    def required_history(self) -> int:
        return 1

    def on_bar(self, bar: Bar, regime: MarketRegime) -> Signal | None:
        self.bars_received.append(bar)
        if not self.fire:
            return None
        return Signal(
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            action=SignalAction.ENTER_LONG,
            timestamp=bar.timestamp,
            price=bar.close,
            quantity=100,
        )


# ──────────────────────────────────────────────
# RegimeDetector
# ──────────────────────────────────────────────

class TestRegimeDetector(unittest.TestCase):

    def test_unknown_with_insufficient_data(self) -> None:
        """Regime is UNKNOWN when not enough bars."""
        detector = RegimeDetector()
        for i in range(5):
            detector.update(make_bar(ts=datetime(2024, 1, 1 + i, 10, 0)))
        self.assertEqual(detector.detect(), MarketRegime.UNKNOWN)

    def test_trending_up(self) -> None:
        """Rising prices → TRENDING_UP."""
        detector = RegimeDetector()
        for i in range(25):
            detector.update(make_bar(
                ts=datetime(2024, 1, 1, 10, i),
                close=7500 + i * 10,
                high=7500 + i * 10 + 5,
                low=7500 + i * 10 - 5,
            ))
        regime = detector.detect()
        self.assertEqual(regime, MarketRegime.TRENDING_UP)

    def test_trending_down(self) -> None:
        """Falling prices → TRENDING_DOWN."""
        detector = RegimeDetector()
        for i in range(25):
            detector.update(make_bar(
                ts=datetime(2024, 1, 1, 10, i),
                close=8000 - i * 10,
                high=8000 - i * 10 + 5,
                low=8000 - i * 10 - 5,
            ))
        regime = detector.detect()
        self.assertEqual(regime, MarketRegime.TRENDING_DOWN)

    def test_high_volatility(self) -> None:
        """Wide range → HIGH_VOLATILITY."""
        detector = RegimeDetector()
        for i in range(25):
            # Alternating high and low closes with wide range
            close = 7500 + (200 if i % 2 == 0 else -200)
            detector.update(make_bar(
                ts=datetime(2024, 1, 1, 10, i),
                close=close,
                high=close + 200,
                low=close - 200,
            ))
        regime = detector.detect()
        self.assertEqual(regime, MarketRegime.HIGH_VOLATILITY)

    def test_low_volatility(self) -> None:
        """Very tight range → LOW_VOLATILITY."""
        detector = RegimeDetector()
        for i in range(25):
            detector.update(make_bar(
                ts=datetime(2024, 1, 1, 10, i),
                close=7500.0,
                high=7501.0,
                low=7499.0,
            ))
        regime = detector.detect()
        self.assertEqual(regime, MarketRegime.LOW_VOLATILITY)

    def test_configurable_thresholds(self) -> None:
        """Config overrides affect regime classification."""
        config = SignalConfig(
            regime_trend_threshold_pct=Decimal("0.1"),  # Very sensitive
            regime_high_vol_threshold_pct=Decimal("10.0"),
            regime_low_vol_threshold_pct=Decimal("0.5"),
        )
        detector = RegimeDetector(config)
        # Mild uptrend that wouldn't normally be detected at 1%
        for i in range(25):
            detector.update(make_bar(
                ts=datetime(2024, 1, 1, 10, i),
                close=7500 + i * 5,
                high=7500 + i * 5 + 5,
                low=7500 + i * 5 - 5,
            ))
        regime = detector.detect()
        self.assertEqual(regime, MarketRegime.TRENDING_UP)


# ──────────────────────────────────────────────
# SignalEngine
# ──────────────────────────────────────────────

class TestSignalEngine(unittest.TestCase):

    def setUp(self) -> None:
        self.config = FrameworkConfig()
        self.event_bus = EventBus()
        self.engine = SignalEngine(self.config, self.event_bus)

    def test_register_strategy(self) -> None:
        strategy = DummyStrategy()
        self.engine.register_strategy(strategy)
        self.assertIn("test_strategy", self.engine.strategies)

    def test_unregister_strategy(self) -> None:
        strategy = DummyStrategy()
        self.engine.register_strategy(strategy)
        self.engine.unregister_strategy("test_strategy")
        self.assertNotIn("test_strategy", self.engine.strategies)

    def test_bar_routed_to_strategy(self) -> None:
        """BAR events are routed to registered strategies."""
        strategy = DummyStrategy(fire=False)
        self.engine.register_strategy(strategy)

        bar = make_bar(symbol="BHP", close=45.0)
        self.event_bus.publish(Event(
            event_type=EventType.BAR,
            data={"bar": bar},
            source="test",
        ))

        # Strategy should have added the bar to history
        self.assertEqual(len(strategy.bars_received), 1)

    def test_signal_published_on_bar(self) -> None:
        """Signal from strategy is published to event bus."""
        strategy = DummyStrategy(fire=True)
        self.engine.register_strategy(strategy)

        signals: list[Signal] = []
        self.event_bus.subscribe(
            EventType.SIGNAL, lambda e: signals.append(e.data["signal"]),
        )

        # Need to add a warmup bar first for has_enough_history
        warmup = make_bar(symbol="BHP", close=44.0)
        self.event_bus.publish(Event(
            event_type=EventType.BAR,
            data={"bar": warmup},
            source="test",
        ))

        bar = make_bar(symbol="BHP", close=45.0)
        self.event_bus.publish(Event(
            event_type=EventType.BAR,
            data={"bar": bar},
            source="test",
        ))

        self.assertGreaterEqual(len(signals), 1)
        self.assertEqual(signals[0].symbol, "BHP")

    def test_inactive_strategy_not_called(self) -> None:
        """Deactivated strategy doesn't receive bars."""
        strategy = DummyStrategy(fire=True)
        strategy.deactivate()
        self.engine.register_strategy(strategy)

        bar = make_bar(symbol="BHP", close=45.0)
        self.event_bus.publish(Event(
            event_type=EventType.BAR,
            data={"bar": bar},
            source="test",
        ))

        self.assertEqual(len(strategy.bars_received), 0)

    def test_market_index_updates_regime(self) -> None:
        """XJO bars update the regime detector."""
        self.assertEqual(self.engine.current_regime, MarketRegime.UNKNOWN)

        for i in range(25):
            bar = make_bar(
                symbol="XJO",
                ts=datetime(2024, 1, 1, 10, i),
                close=7500 + i * 50,
            )
            self.event_bus.publish(Event(
                event_type=EventType.BAR,
                data={"bar": bar},
                source="test",
            ))

        self.assertNotEqual(self.engine.current_regime, MarketRegime.UNKNOWN)

    def test_configurable_index_symbols(self) -> None:
        """Custom index symbols are used for regime detection."""
        config = FrameworkConfig()
        config.signal.market_index_symbols = ["MY_INDEX"]
        engine = SignalEngine(config, self.event_bus)

        # XJO should NOT update regime
        for i in range(25):
            bar = make_bar(symbol="XJO", ts=datetime(2024, 1, 1, 10, i))
            self.event_bus.publish(Event(
                event_type=EventType.BAR, data={"bar": bar}, source="test",
            ))
        self.assertEqual(engine.current_regime, MarketRegime.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
