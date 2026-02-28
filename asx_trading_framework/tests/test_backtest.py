"""
Unit tests for the backtesting framework.

Tests metrics computation, cost application, Monte Carlo, and acceptance criteria.
"""

import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from ..backtest.engine import (
    AcceptanceCriteria,
    BacktestResult,
    MonteCarloResult,
    apply_costs,
    check_acceptance,
    compute_backtest_metrics,
    monte_carlo,
    walk_forward_split,
)
from ..core.config import BacktestConfig
from ..core.types import Bar, Side, TradeRecord


def make_trade(
    pnl: Decimal,
    entry_price: Decimal = Decimal("50"),
    quantity: int = 100,
    offset_days: int = 0,
) -> TradeRecord:
    """Create a test trade record."""
    base_time = datetime(2024, 1, 1) + timedelta(days=offset_days)
    return TradeRecord(
        trade_id=f"trade-{offset_days}",
        symbol="BHP",
        strategy_id="test",
        side=Side.BUY,
        entry_price=entry_price,
        exit_price=entry_price + pnl / quantity,
        quantity=quantity,
        entry_time=base_time,
        exit_time=base_time + timedelta(hours=4),
        pnl=pnl,
        commission_total=Decimal("10"),
        slippage=Decimal("5"),
    )


class TestBacktestMetrics(unittest.TestCase):
    """Test backtest metrics computation."""

    def test_empty_trades(self) -> None:
        result = compute_backtest_metrics([], Decimal("100000"))
        self.assertEqual(result.total_trades, 0)

    def test_all_winners(self) -> None:
        trades = [make_trade(Decimal("100"), offset_days=i) for i in range(10)]
        result = compute_backtest_metrics(trades, Decimal("100000"))
        self.assertEqual(result.total_trades, 10)
        self.assertEqual(result.winning_trades, 10)
        self.assertEqual(result.losing_trades, 0)
        self.assertEqual(result.win_rate, Decimal("1"))
        self.assertGreater(result.net_pnl, Decimal("0"))

    def test_mixed_trades(self) -> None:
        trades = [
            make_trade(Decimal("200"), offset_days=0),
            make_trade(Decimal("-100"), offset_days=1),
            make_trade(Decimal("150"), offset_days=2),
            make_trade(Decimal("-80"), offset_days=3),
        ]
        result = compute_backtest_metrics(trades, Decimal("100000"))
        self.assertEqual(result.total_trades, 4)
        self.assertEqual(result.winning_trades, 2)
        self.assertEqual(result.losing_trades, 2)
        self.assertEqual(result.net_pnl, Decimal("170"))
        self.assertEqual(result.win_rate, Decimal("0.5"))

    def test_max_drawdown(self) -> None:
        trades = [
            make_trade(Decimal("500"), offset_days=0),
            make_trade(Decimal("-200"), offset_days=1),
            make_trade(Decimal("-300"), offset_days=2),
            make_trade(Decimal("100"), offset_days=3),
        ]
        result = compute_backtest_metrics(trades, Decimal("100000"))
        self.assertEqual(result.max_drawdown, Decimal("500"))

    def test_max_consecutive_losses(self) -> None:
        trades = [
            make_trade(Decimal("100"), offset_days=0),
            make_trade(Decimal("-50"), offset_days=1),
            make_trade(Decimal("-60"), offset_days=2),
            make_trade(Decimal("-70"), offset_days=3),
            make_trade(Decimal("100"), offset_days=4),
        ]
        result = compute_backtest_metrics(trades, Decimal("100000"))
        self.assertEqual(result.max_consecutive_losses, 3)

    def test_equity_curve(self) -> None:
        trades = [
            make_trade(Decimal("100"), offset_days=0),
            make_trade(Decimal("-50"), offset_days=1),
        ]
        result = compute_backtest_metrics(trades, Decimal("100000"))
        self.assertEqual(result.equity_curve, [
            Decimal("100000"),
            Decimal("100100"),
            Decimal("100050"),
        ])


class TestApplyCosts(unittest.TestCase):
    """Test transaction cost and slippage application."""

    def test_costs_reduce_pnl(self) -> None:
        config = BacktestConfig()
        trades = [make_trade(Decimal("100"))]
        adjusted = apply_costs(trades, config)
        self.assertLess(adjusted[0].pnl, Decimal("100"))
        self.assertGreater(adjusted[0].commission_total, Decimal("0"))
        self.assertGreater(adjusted[0].slippage, Decimal("0"))


class TestMonteCarlo(unittest.TestCase):
    """Test Monte Carlo simulation."""

    def test_profitable_trades_mostly_profitable(self) -> None:
        trades = [make_trade(Decimal("100"), offset_days=i) for i in range(50)]
        result = monte_carlo(trades, Decimal("100000"), iterations=100)
        self.assertGreater(result.prob_profitable, 0.8)
        self.assertGreater(result.median_pnl, Decimal("0"))

    def test_losing_trades_mostly_unprofitable(self) -> None:
        trades = [make_trade(Decimal("-100"), offset_days=i) for i in range(50)]
        result = monte_carlo(trades, Decimal("100000"), iterations=100)
        self.assertLess(result.prob_profitable, 0.2)
        self.assertLess(result.median_pnl, Decimal("0"))

    def test_empty_trades(self) -> None:
        result = monte_carlo([], Decimal("100000"))
        self.assertEqual(result.iterations, 0)


class TestAcceptanceCriteria(unittest.TestCase):
    """Test acceptance criteria checking."""

    def test_good_result_passes(self) -> None:
        # Vary PnL to produce non-zero Sharpe ratio
        import random as _rng
        _rng.seed(42)
        trades = [
            make_trade(Decimal(str(_rng.randint(50, 200))), offset_days=i)
            for i in range(50)
        ]
        result = compute_backtest_metrics(trades, Decimal("100000"))
        passed, failures = check_acceptance(result)
        self.assertTrue(passed, f"Failures: {failures}")

    def test_insufficient_trades_fails(self) -> None:
        trades = [make_trade(Decimal("100"), offset_days=i) for i in range(5)]
        result = compute_backtest_metrics(trades, Decimal("100000"))
        passed, failures = check_acceptance(result)
        self.assertFalse(passed)
        self.assertTrue(any("Insufficient trades" in f for f in failures))

    def test_negative_expectancy_fails(self) -> None:
        trades = [make_trade(Decimal("-100"), offset_days=i) for i in range(50)]
        result = compute_backtest_metrics(trades, Decimal("100000"))
        passed, failures = check_acceptance(result)
        self.assertFalse(passed)


class TestWalkForwardSplit(unittest.TestCase):
    """Test walk-forward data splitting."""

    def test_basic_split(self) -> None:
        bars: list[Bar] = []
        base = datetime(2024, 1, 1)
        for day in range(100):
            for minute in range(10):
                bars.append(Bar(
                    symbol="BHP",
                    timestamp=base + timedelta(days=day, minutes=minute),
                    open=Decimal("50"),
                    high=Decimal("51"),
                    low=Decimal("49"),
                    close=Decimal("50.5"),
                    volume=10000,
                ))

        splits = walk_forward_split(bars, train_days=50, test_days=25)
        self.assertGreater(len(splits), 0)
        for train_bars, test_bars in splits:
            self.assertGreater(len(train_bars), 0)
            self.assertGreater(len(test_bars), 0)
            # Test bars should come after train bars
            self.assertGreater(
                min(b.timestamp for b in test_bars),
                max(b.timestamp for b in train_bars),
            )


if __name__ == "__main__":
    unittest.main()
