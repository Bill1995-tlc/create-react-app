"""
Signal engine — pluggable strategy framework.

Each strategy is a plugin that processes bars/quotes and emits Signal objects.
The engine manages strategy registration, data routing, and signal publishing.
"""

from __future__ import annotations

import abc
import logging
from datetime import datetime
from typing import Any

from ..core.config import FrameworkConfig, SignalConfig
from ..core.events import Event, EventBus, EventType
from ..core.types import Bar, MarketRegime, Quote, Signal, SignalAction

logger = logging.getLogger(__name__)


class Strategy(abc.ABC):
    """
    Abstract strategy base class.

    Every strategy must define:
    - strategy_id: unique identifier
    - on_bar(): process a new bar
    - on_quote(): process a new quote (optional)
    - required_history: how many bars of warmup needed
    """

    def __init__(self, strategy_id: str, config: dict[str, Any] | None = None) -> None:
        self.strategy_id = strategy_id
        self.config = config or {}
        self._bar_history: dict[str, list[Bar]] = {}
        self._active = True

    @property
    @abc.abstractmethod
    def required_history(self) -> int:
        """Number of bars required for warmup before generating signals."""

    @abc.abstractmethod
    def on_bar(self, bar: Bar, regime: MarketRegime) -> Signal | None:
        """Process a bar and optionally return a signal."""

    def on_quote(self, quote: Quote) -> Signal | None:
        """Process a quote. Override if the strategy uses L1 data."""
        return None

    def add_bar(self, bar: Bar) -> None:
        """Maintain bar history per symbol."""
        self._bar_history.setdefault(bar.symbol, []).append(bar)
        # Keep only what we need + some buffer
        max_len = self.required_history * 2
        if len(self._bar_history[bar.symbol]) > max_len:
            self._bar_history[bar.symbol] = self._bar_history[bar.symbol][-max_len:]

    def get_history(self, symbol: str) -> list[Bar]:
        return self._bar_history.get(symbol, [])

    def has_enough_history(self, symbol: str) -> bool:
        return len(self.get_history(symbol)) >= self.required_history

    def deactivate(self) -> None:
        self._active = False

    def activate(self) -> None:
        self._active = True

    @property
    def is_active(self) -> bool:
        return self._active


class RegimeDetector:
    """
    Cook-style market context / regime detection.

    Uses simple, deterministic rules on broad market data.
    All thresholds are configurable via SignalConfig.
    """

    def __init__(self, config: SignalConfig | None = None) -> None:
        cfg = config or SignalConfig()
        self.lookback_bars: int = cfg.regime_lookback_bars
        self.trend_threshold_pct = cfg.regime_trend_threshold_pct
        self.high_vol_threshold_pct = cfg.regime_high_vol_threshold_pct
        self.low_vol_threshold_pct = cfg.regime_low_vol_threshold_pct
        self._market_bars: list[Bar] = []

    def update(self, bar: Bar) -> None:
        """Feed market-level bar (e.g., XJO index)."""
        self._market_bars.append(bar)
        if len(self._market_bars) > self.lookback_bars * 3:
            self._market_bars = self._market_bars[-self.lookback_bars * 3:]

    def detect(self) -> MarketRegime:
        """Classify current market regime."""
        if len(self._market_bars) < self.lookback_bars:
            return MarketRegime.UNKNOWN

        recent = self._market_bars[-self.lookback_bars:]
        closes = [b.close for b in recent]

        # Trend: simple linear direction
        first_half_avg = sum(closes[:len(closes)//2]) / (len(closes)//2)
        second_half_avg = sum(closes[len(closes)//2:]) / (len(closes) - len(closes)//2)

        # Volatility: range / mean
        mean_close = sum(closes) / len(closes)
        high_range = max(b.high for b in recent) - min(b.low for b in recent)
        volatility_pct = (high_range / mean_close) * 100 if mean_close else 0

        trend_threshold = mean_close * self.trend_threshold_pct / 100

        if volatility_pct > self.high_vol_threshold_pct:
            return MarketRegime.HIGH_VOLATILITY

        if second_half_avg - first_half_avg > trend_threshold:
            return MarketRegime.TRENDING_UP
        elif first_half_avg - second_half_avg > trend_threshold:
            return MarketRegime.TRENDING_DOWN

        if volatility_pct < self.low_vol_threshold_pct:
            return MarketRegime.LOW_VOLATILITY

        return MarketRegime.RANGE_BOUND


class SignalEngine:
    """
    Manages strategy plugins and routes data to them.

    Collects signals and publishes them through the event bus.
    """

    def __init__(self, config: FrameworkConfig, event_bus: EventBus) -> None:
        self.config = config
        self.event_bus = event_bus
        self.strategies: dict[str, Strategy] = {}
        self.regime_detector = RegimeDetector(config.signal)
        self._market_index_symbols: set[str] = set(config.signal.market_index_symbols)
        self._current_regime = MarketRegime.UNKNOWN

        # Subscribe to data events
        self.event_bus.subscribe(EventType.BAR, self._on_bar_event)
        self.event_bus.subscribe(EventType.QUOTE, self._on_quote_event)

    def register_strategy(self, strategy: Strategy) -> None:
        """Register a strategy plugin."""
        self.strategies[strategy.strategy_id] = strategy
        logger.info("Registered strategy: %s", strategy.strategy_id)

    def unregister_strategy(self, strategy_id: str) -> None:
        """Remove a strategy."""
        self.strategies.pop(strategy_id, None)

    def _on_bar_event(self, event: Event) -> None:
        """Handle incoming bar events."""
        bar: Bar = event.data["bar"]

        # Update regime detector with market index bars
        if bar.symbol in self._market_index_symbols:
            self.regime_detector.update(bar)
            self._current_regime = self.regime_detector.detect()

        # Route to all active strategies
        for strategy in self.strategies.values():
            if not strategy.is_active:
                continue
            strategy.add_bar(bar)
            if not strategy.has_enough_history(bar.symbol):
                continue
            signal = strategy.on_bar(bar, self._current_regime)
            if signal is not None and signal.action != SignalAction.NO_ACTION:
                self._publish_signal(signal)

    def _on_quote_event(self, event: Event) -> None:
        """Handle incoming quote events."""
        quote: Quote = event.data["quote"]
        for strategy in self.strategies.values():
            if not strategy.is_active:
                continue
            signal = strategy.on_quote(quote)
            if signal is not None and signal.action != SignalAction.NO_ACTION:
                self._publish_signal(signal)

    def _publish_signal(self, signal: Signal) -> None:
        """Publish a signal event for the risk engine to evaluate."""
        self.event_bus.publish(Event(
            event_type=EventType.SIGNAL,
            data={"signal": signal},
            source=f"strategy:{signal.strategy_id}",
        ))
        logger.info(
            "Signal: %s %s %s @ %s (stop=%s)",
            signal.strategy_id,
            signal.action.value,
            signal.symbol,
            signal.price,
            signal.stop_loss,
        )

    @property
    def current_regime(self) -> MarketRegime:
        return self._current_regime
