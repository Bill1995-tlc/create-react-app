"""
Unit tests for the risk engine.

Tests all "Wizard Principles → Coded Constraints":
- Pre-trade risk check rejects when limits exceeded
- Daily/weekly loss limits
- Position limits
- Trade count limits
- Participation rate checks
- Spread checks
- Cooldown logic
- Kill switch
- Manual halt
- Position sizing
"""

import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from ..core.config import (
    BacktestConfig,
    FrameworkConfig,
    NewsConfig,
    RiskConfig,
)
from ..core.events import EventBus, EventType
from ..core.types import (
    Fill,
    Position,
    Quote,
    RiskCheckResult,
    RiskVeto,
    Signal,
    SignalAction,
    Side,
)
from ..risk.engine import RiskEngine


def make_config(**overrides: object) -> FrameworkConfig:
    """Create a test config with optional overrides."""
    config = FrameworkConfig()
    for key, value in overrides.items():
        if hasattr(config.risk, key):
            setattr(config.risk, key, value)
        elif hasattr(config.news, key):
            setattr(config.news, key, value)
    return config


def make_signal(
    symbol: str = "BHP",
    action: SignalAction = SignalAction.ENTER_LONG,
    price: Decimal = Decimal("45.00"),
    quantity: int = 100,
    stop_loss: Decimal | None = Decimal("44.00"),
) -> Signal:
    """Create a test signal."""
    return Signal(
        strategy_id="test_strategy",
        symbol=symbol,
        action=action,
        timestamp=datetime.utcnow(),
        price=price,
        quantity=quantity,
        stop_loss=stop_loss,
    )


def make_quote(
    symbol: str = "BHP",
    bid: Decimal = Decimal("44.95"),
    ask: Decimal = Decimal("45.05"),
    bid_size: int = 1000,
    ask_size: int = 1000,
) -> Quote:
    """Create a test quote."""
    return Quote(
        symbol=symbol,
        timestamp=datetime.utcnow(),
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
    )


class TestRiskEngine(unittest.TestCase):
    """Test suite for the risk engine."""

    def setUp(self) -> None:
        self.event_bus = EventBus()
        self.config = make_config()
        self.risk_engine = RiskEngine(self.config, self.event_bus)

    def test_signal_allowed_when_within_limits(self) -> None:
        """A valid signal with good risk should pass all checks."""
        signal = make_signal()
        result = self.risk_engine.check_signal(signal)
        self.assertTrue(result.allowed)
        self.assertEqual(result.veto_reason, RiskVeto.ALLOWED)
        self.assertGreater(result.worst_case_loss, Decimal("0"))

    def test_kill_switch_blocks_all_signals(self) -> None:
        """When kill switch is active, all signals are rejected."""
        self.risk_engine.activate_kill_switch("test")
        signal = make_signal()
        result = self.risk_engine.check_signal(signal)
        self.assertFalse(result.allowed)
        self.assertEqual(result.veto_reason, RiskVeto.KILL_SWITCH)

    def test_kill_switch_deactivation(self) -> None:
        """Kill switch can be deactivated to resume trading."""
        self.risk_engine.activate_kill_switch("test")
        self.risk_engine.deactivate_kill_switch()
        signal = make_signal()
        result = self.risk_engine.check_signal(signal)
        self.assertTrue(result.allowed)

    def test_daily_loss_limit_blocks_new_trades(self) -> None:
        """After hitting daily loss limit, new trades are rejected."""
        # Simulate losses exceeding daily limit
        self.risk_engine._daily_stats.net_pnl = -self.config.risk.max_daily_loss - Decimal("1")
        signal = make_signal()
        result = self.risk_engine.check_signal(signal)
        self.assertFalse(result.allowed)
        self.assertEqual(result.veto_reason, RiskVeto.DAILY_LOSS_LIMIT)

    def test_weekly_loss_limit_blocks_new_trades(self) -> None:
        """After hitting weekly loss limit, new trades are rejected."""
        self.risk_engine._weekly_pnl = -self.config.risk.max_weekly_loss - Decimal("1")
        signal = make_signal()
        result = self.risk_engine.check_signal(signal)
        self.assertFalse(result.allowed)
        self.assertEqual(result.veto_reason, RiskVeto.WEEKLY_LOSS_LIMIT)

    def test_max_trades_per_day_limit(self) -> None:
        """When max trades/day reached, new signals are rejected."""
        self.risk_engine._trades_today = self.config.risk.max_trades_per_day
        signal = make_signal()
        result = self.risk_engine.check_signal(signal)
        self.assertFalse(result.allowed)
        self.assertEqual(result.veto_reason, RiskVeto.MAX_TRADES_PER_DAY)

    def test_max_positions_limit(self) -> None:
        """When at max positions, new entries are rejected."""
        # Fill up positions
        for i in range(self.config.risk.max_positions):
            self.risk_engine._positions[f"SYM{i}"] = Position(
                symbol=f"SYM{i}",
                quantity=100,
                average_entry_price=Decimal("10"),
            )
        signal = make_signal()
        result = self.risk_engine.check_signal(signal)
        self.assertFalse(result.allowed)
        self.assertEqual(result.veto_reason, RiskVeto.MAX_POSITIONS)

    def test_exit_signal_not_blocked_by_position_limit(self) -> None:
        """Exit signals should not be blocked by max positions."""
        for i in range(self.config.risk.max_positions):
            self.risk_engine._positions[f"SYM{i}"] = Position(
                symbol=f"SYM{i}",
                quantity=100,
                average_entry_price=Decimal("10"),
            )
        signal = make_signal(action=SignalAction.EXIT)
        result = self.risk_engine.check_signal(signal)
        self.assertTrue(result.allowed)

    def test_worst_case_loss_rejects_oversized_trade(self) -> None:
        """Trades where worst-case exceeds limit are rejected."""
        # Signal with very wide stop → large worst-case loss
        signal = make_signal(
            price=Decimal("50"),
            quantity=1000,
            stop_loss=Decimal("40"),  # $10/share * 1000 = $10,000 > $500 default
        )
        result = self.risk_engine.check_signal(signal)
        self.assertFalse(result.allowed)
        self.assertEqual(result.veto_reason, RiskVeto.MAX_LOSS_PER_TRADE)

    def test_spread_too_wide_rejects(self) -> None:
        """Wide spread blocks entry."""
        signal = make_signal()
        # Create a quote with very wide spread (100 bps)
        quote = make_quote(bid=Decimal("44.50"), ask=Decimal("45.50"))
        result = self.risk_engine.check_signal(signal, quote=quote)
        self.assertFalse(result.allowed)
        self.assertEqual(result.veto_reason, RiskVeto.SPREAD_TOO_WIDE)

    def test_participation_rate_rejects(self) -> None:
        """Excessive participation rate blocks entry."""
        # Use small quantity with tight stop so worst-case loss passes,
        # narrow spread so spread check passes,
        # but participation rate (200/1000 = 20%) exceeds 5% default.
        signal = make_signal(
            price=Decimal("10.00"),
            quantity=200,
            stop_loss=Decimal("9.50"),
        )
        quote = make_quote(
            bid=Decimal("9.999"), ask=Decimal("10.001"),  # ~0.2 bps spread
            bid_size=1000, ask_size=1000,
        )
        result = self.risk_engine.check_signal(
            signal, quote=quote, recent_volume=1_000
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.veto_reason, RiskVeto.PARTICIPATION_RATE)

    def test_manual_halt_blocks_signals(self) -> None:
        """Manual halt blocks all signals."""
        self.risk_engine.set_manual_halt(True)
        signal = make_signal()
        result = self.risk_engine.check_signal(signal)
        self.assertFalse(result.allowed)
        self.assertEqual(result.veto_reason, RiskVeto.MANUAL_HALT)

    def test_symbol_halt_blocks_specific_symbol(self) -> None:
        """Halting a specific symbol blocks only that symbol."""
        self.risk_engine.halt_symbol("BHP")
        signal_bhp = make_signal(symbol="BHP")
        signal_cba = make_signal(symbol="CBA")

        result_bhp = self.risk_engine.check_signal(signal_bhp)
        result_cba = self.risk_engine.check_signal(signal_cba)

        self.assertFalse(result_bhp.allowed)
        self.assertTrue(result_cba.allowed)

    def test_news_halt_toggle(self) -> None:
        """News manual halt toggle blocks all trading."""
        self.config.news.manual_halt = True
        signal = make_signal()
        result = self.risk_engine.check_signal(signal)
        self.assertFalse(result.allowed)
        self.assertEqual(result.veto_reason, RiskVeto.NEWS_WINDOW)

    def test_news_halt_symbol_list(self) -> None:
        """Symbols in news halt list are blocked."""
        self.config.news.halt_symbols = ["BHP"]
        signal = make_signal(symbol="BHP")
        result = self.risk_engine.check_signal(signal)
        self.assertFalse(result.allowed)
        self.assertEqual(result.veto_reason, RiskVeto.NEWS_WINDOW)

    def test_position_sizing_fixed_risk(self) -> None:
        """Position size = risk_budget / risk_per_share."""
        self.risk_engine._equity = Decimal("100000")
        # 2% of $100K = $2000 risk budget
        # Entry $50, stop $48 → $2/share risk → 1000 shares
        size = self.risk_engine.compute_position_size(
            entry_price=Decimal("50"),
            stop_price=Decimal("48"),
        )
        self.assertEqual(size, 1000)

    def test_position_sizing_zero_risk(self) -> None:
        """Zero risk per share returns 0 shares."""
        size = self.risk_engine.compute_position_size(
            entry_price=Decimal("50"),
            stop_price=Decimal("50"),
        )
        self.assertEqual(size, 0)

    def test_daily_reset(self) -> None:
        """Daily reset clears daily counters."""
        self.risk_engine._daily_stats.net_pnl = Decimal("-1000")
        self.risk_engine._trades_today = 10
        self.risk_engine.reset_daily()
        self.assertEqual(self.risk_engine._daily_stats.net_pnl, Decimal("0"))
        self.assertEqual(self.risk_engine._trades_today, 0)

    def test_cooldown_activation(self) -> None:
        """Big win triggers cooldown."""
        # Build up recent wins
        for _ in range(10):
            self.risk_engine.record_trade_pnl(Decimal("100"))

        # Record an outsized win (3x+ average)
        self.risk_engine.record_trade_pnl(Decimal("500"))

        # Cooldown should now be active
        self.assertIsNotNone(self.risk_engine._cooldown_until)

    def test_record_trade_pnl_updates_stats(self) -> None:
        """Recording PnL updates daily and weekly stats."""
        self.risk_engine.record_trade_pnl(Decimal("100"))
        self.risk_engine.record_trade_pnl(Decimal("-50"))
        self.assertEqual(self.risk_engine._daily_stats.net_pnl, Decimal("50"))
        self.assertEqual(self.risk_engine._weekly_pnl, Decimal("50"))
        self.assertEqual(self.risk_engine._daily_stats.winning_trades, 1)
        self.assertEqual(self.risk_engine._daily_stats.losing_trades, 1)

    def test_kill_switch_emits_event(self) -> None:
        """Kill switch activation publishes an event."""
        events_captured: list = []
        self.event_bus.subscribe(
            EventType.KILL_SWITCH_ACTIVATED,
            lambda e: events_captured.append(e),
        )
        self.risk_engine.activate_kill_switch("test reason")
        self.assertEqual(len(events_captured), 1)
        self.assertEqual(events_captured[0].data["reason"], "test reason")


class TestPositionSizingWithCooldown(unittest.TestCase):
    """Test position sizing during cooldown periods."""

    def setUp(self) -> None:
        self.event_bus = EventBus()
        self.config = make_config()
        self.risk_engine = RiskEngine(self.config, self.event_bus)
        self.risk_engine._equity = Decimal("100000")

    def test_cooldown_reduces_size(self) -> None:
        """During cooldown, position size is reduced."""
        # Set cooldown active
        self.risk_engine._cooldown_until = datetime.utcnow() + timedelta(minutes=30)

        normal_size = 1000  # Without cooldown: $2000 / $2 = 1000
        cooldown_size = self.risk_engine.compute_position_size(
            entry_price=Decimal("50"),
            stop_price=Decimal("48"),
        )
        # Cooldown reduces by 50% (DEFAULT)
        self.assertEqual(cooldown_size, 500)


if __name__ == "__main__":
    unittest.main()
