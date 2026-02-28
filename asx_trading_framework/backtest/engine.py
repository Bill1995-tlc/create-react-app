"""
Backtesting framework with transaction costs, slippage, walk-forward,
Monte Carlo, and reality checks.

Testing & Evidence Standards:
- Walk-forward testing with parameter stability
- Out-of-sample split
- Monte Carlo bootstrapping
- Sensitivity to costs, spread, liquidity, regime
- Acceptance criteria clearly defined
"""

from __future__ import annotations

import logging
import random
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable

from ..core.config import BacktestConfig, FrameworkConfig
from ..core.events import EventBus
from ..core.types import Bar, Side, TradeRecord
from ..data.provider import CSVDataProvider
from ..execution.engine import ExecutionEngine, PaperBrokerAdapter
from ..risk.engine import RiskEngine
from ..signals.engine import SignalEngine, Strategy
from ..state.manager import StateManager

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Results of a single backtest run."""
    start_date: datetime
    end_date: datetime
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    gross_pnl: Decimal = Decimal("0")
    total_commission: Decimal = Decimal("0")
    total_slippage: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    max_drawdown_pct: Decimal = Decimal("0")
    sharpe_ratio: float = 0.0
    profit_factor: Decimal = Decimal("0")
    win_rate: Decimal = Decimal("0")
    avg_win: Decimal = Decimal("0")
    avg_loss: Decimal = Decimal("0")
    expectancy: Decimal = Decimal("0")  # $ per $ risked
    max_consecutive_losses: int = 0
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[Decimal] = field(default_factory=list)
    daily_returns: list[float] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)


def compute_backtest_metrics(
    trades: list[TradeRecord],
    initial_equity: Decimal,
) -> BacktestResult:
    """Compute all metrics from a list of completed trades."""
    result = BacktestResult(
        start_date=trades[0].entry_time if trades else datetime.min,
        end_date=trades[-1].exit_time if trades else datetime.min,
        trades=trades,
    )
    if not trades:
        return result

    result.total_trades = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    result.winning_trades = len(wins)
    result.losing_trades = len(losses)
    result.gross_pnl = sum(t.pnl + t.commission_total for t in trades)
    result.total_commission = sum(t.commission_total for t in trades)
    result.total_slippage = sum(t.slippage for t in trades)
    result.net_pnl = sum(t.pnl for t in trades)

    if result.total_trades > 0:
        result.win_rate = Decimal(result.winning_trades) / Decimal(result.total_trades)

    if wins:
        result.avg_win = sum(t.pnl for t in wins) / len(wins)
    if losses:
        result.avg_loss = sum(t.pnl for t in losses) / len(losses)

    # Profit factor
    total_wins = sum(t.pnl for t in wins) if wins else Decimal("0")
    total_losses = abs(sum(t.pnl for t in losses)) if losses else Decimal("1")
    if total_losses > 0:
        result.profit_factor = total_wins / total_losses

    # Expectancy (per dollar risked — simplified)
    if result.total_trades > 0:
        result.expectancy = result.net_pnl / result.total_trades

    # Equity curve and drawdown
    equity = initial_equity
    peak = equity
    max_dd = Decimal("0")
    equity_curve = [equity]
    for trade in trades:
        equity += trade.pnl
        equity_curve.append(equity)
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    result.equity_curve = equity_curve
    result.max_drawdown = max_dd
    if peak > 0:
        result.max_drawdown_pct = max_dd / peak * 100

    # Max consecutive losses
    max_consec = 0
    current_consec = 0
    for trade in trades:
        if trade.pnl <= 0:
            current_consec += 1
            max_consec = max(max_consec, current_consec)
        else:
            current_consec = 0
    result.max_consecutive_losses = max_consec

    # Sharpe ratio (annualized, simplified)
    if len(trades) > 1:
        returns = [float(t.pnl / initial_equity) for t in trades]
        result.daily_returns = returns
        mean_ret = statistics.mean(returns)
        std_ret = statistics.stdev(returns) if len(returns) > 1 else 1.0
        if std_ret > 0:
            result.sharpe_ratio = (mean_ret / std_ret) * (252 ** 0.5)

    return result


def apply_costs(
    trades: list[TradeRecord],
    config: BacktestConfig,
) -> list[TradeRecord]:
    """Apply transaction costs and slippage to trade records."""
    adjusted = []
    for trade in trades:
        trade_value = trade.entry_price * trade.quantity
        commission = max(
            config.commission_per_trade,
            trade_value * config.commission_pct,
        ) * 2  # Round trip

        slippage_per_side = trade.entry_price * config.slippage_bps / 10_000
        slippage = slippage_per_side * trade.quantity * 2

        adjusted_pnl = trade.pnl - commission - slippage
        adjusted.append(TradeRecord(
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            strategy_id=trade.strategy_id,
            side=trade.side,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            quantity=trade.quantity,
            entry_time=trade.entry_time,
            exit_time=trade.exit_time,
            pnl=adjusted_pnl,
            commission_total=commission,
            slippage=slippage,
            tags=trade.tags,
        ))
    return adjusted


# ──────────────────────────────────────────────
# Walk-Forward Testing
# ──────────────────────────────────────────────

@dataclass
class WalkForwardResult:
    """Results of walk-forward analysis."""
    windows: list[dict[str, Any]] = field(default_factory=list)
    in_sample_results: list[BacktestResult] = field(default_factory=list)
    out_of_sample_results: list[BacktestResult] = field(default_factory=list)
    combined_oos_result: BacktestResult | None = None
    parameter_stability: dict[str, list[Any]] = field(default_factory=dict)


def walk_forward_split(
    bars: list[Bar],
    train_days: int,
    test_days: int,
) -> list[tuple[list[Bar], list[Bar]]]:
    """
    Split bar data into walk-forward train/test windows.
    Returns list of (train_bars, test_bars) tuples.
    """
    if not bars:
        return []

    bars_sorted = sorted(bars, key=lambda b: b.timestamp)

    # Group by date
    bars_by_date: dict[str, list[Bar]] = {}
    for bar in bars_sorted:
        date_key = bar.timestamp.strftime("%Y-%m-%d")
        bars_by_date.setdefault(date_key, []).append(bar)

    dates = sorted(bars_by_date.keys())
    splits: list[tuple[list[Bar], list[Bar]]] = []

    i = 0
    while i + train_days + test_days <= len(dates):
        train_dates = dates[i:i + train_days]
        test_dates = dates[i + train_days:i + train_days + test_days]

        train_bars: list[Bar] = []
        for d in train_dates:
            train_bars.extend(bars_by_date[d])

        test_bars: list[Bar] = []
        for d in test_dates:
            test_bars.extend(bars_by_date[d])

        splits.append((train_bars, test_bars))
        i += test_days  # Step forward by test window size

    return splits


# ──────────────────────────────────────────────
# Monte Carlo Simulation
# ──────────────────────────────────────────────

@dataclass
class MonteCarloResult:
    """Results of Monte Carlo simulation on trade outcomes."""
    iterations: int = 0
    median_pnl: Decimal = Decimal("0")
    pct_5_pnl: Decimal = Decimal("0")      # 5th percentile (worst case)
    pct_95_pnl: Decimal = Decimal("0")     # 95th percentile (best case)
    median_max_dd: Decimal = Decimal("0")
    pct_95_max_dd: Decimal = Decimal("0")  # 95th percentile drawdown
    prob_profitable: float = 0.0
    prob_ruin: float = 0.0  # Probability of hitting max drawdown limit


def monte_carlo(
    trades: list[TradeRecord],
    initial_equity: Decimal,
    iterations: int = 1000,
    max_drawdown_limit: Decimal = Decimal("0.20"),  # DEFAULT: 20%
) -> MonteCarloResult:
    """
    Bootstrap Monte Carlo simulation.

    Randomly reorders trades to assess distributional properties
    of equity curves under different trade sequences.
    """
    if not trades:
        return MonteCarloResult()

    pnls = [t.pnl for t in trades]
    final_pnls: list[Decimal] = []
    max_dds: list[Decimal] = []
    ruin_count = 0

    for _ in range(iterations):
        shuffled = list(pnls)
        random.shuffle(shuffled)

        equity = initial_equity
        peak = equity
        max_dd = Decimal("0")

        for pnl in shuffled:
            equity += pnl
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else Decimal("0")
            if dd > max_dd:
                max_dd = dd

        final_pnls.append(equity - initial_equity)
        max_dds.append(max_dd)
        if max_dd > max_drawdown_limit:
            ruin_count += 1

    final_pnls.sort()
    max_dds.sort()

    return MonteCarloResult(
        iterations=iterations,
        median_pnl=final_pnls[len(final_pnls) // 2],
        pct_5_pnl=final_pnls[int(len(final_pnls) * 0.05)],
        pct_95_pnl=final_pnls[int(len(final_pnls) * 0.95)],
        median_max_dd=max_dds[len(max_dds) // 2],
        pct_95_max_dd=max_dds[int(len(max_dds) * 0.95)],
        prob_profitable=sum(1 for p in final_pnls if p > 0) / len(final_pnls),
        prob_ruin=ruin_count / iterations,
    )


# ──────────────────────────────────────────────
# Acceptance Criteria
# ──────────────────────────────────────────────

@dataclass
class AcceptanceCriteria:
    """Defines minimum thresholds for a strategy to be considered viable."""
    min_trades: int = 30                              # DEFAULT
    min_win_rate: Decimal = Decimal("0.35")           # DEFAULT: 35%
    min_profit_factor: Decimal = Decimal("1.2")       # DEFAULT
    max_drawdown_pct: Decimal = Decimal("15.0")       # DEFAULT: 15%
    min_sharpe: float = 0.5                           # DEFAULT
    min_expectancy: Decimal = Decimal("0")            # DEFAULT: must be positive
    max_prob_ruin: float = 0.10                       # DEFAULT: 10%
    min_prob_profitable: float = 0.60                 # DEFAULT: 60%


def check_acceptance(
    result: BacktestResult,
    mc_result: MonteCarloResult | None = None,
    criteria: AcceptanceCriteria | None = None,
) -> tuple[bool, list[str]]:
    """
    Check if backtest results meet acceptance criteria.
    Returns (passed, list_of_failures).
    """
    if criteria is None:
        criteria = AcceptanceCriteria()

    failures: list[str] = []

    if result.total_trades < criteria.min_trades:
        failures.append(
            f"Insufficient trades: {result.total_trades} < {criteria.min_trades}"
        )

    if result.win_rate < criteria.min_win_rate:
        failures.append(
            f"Win rate too low: {result.win_rate:.2%} < {criteria.min_win_rate:.2%}"
        )

    if result.profit_factor < criteria.min_profit_factor:
        failures.append(
            f"Profit factor too low: {result.profit_factor:.2f} < {criteria.min_profit_factor}"
        )

    if result.max_drawdown_pct > criteria.max_drawdown_pct:
        failures.append(
            f"Max drawdown too high: {result.max_drawdown_pct:.1f}% > {criteria.max_drawdown_pct}%"
        )

    if result.sharpe_ratio < criteria.min_sharpe:
        failures.append(
            f"Sharpe too low: {result.sharpe_ratio:.2f} < {criteria.min_sharpe}"
        )

    if result.expectancy < criteria.min_expectancy:
        failures.append(
            f"Expectancy negative: {result.expectancy}"
        )

    if mc_result is not None:
        if mc_result.prob_ruin > criteria.max_prob_ruin:
            failures.append(
                f"Ruin probability too high: {mc_result.prob_ruin:.1%} > {criteria.max_prob_ruin:.1%}"
            )
        if mc_result.prob_profitable < criteria.min_prob_profitable:
            failures.append(
                f"Profitability probability too low: {mc_result.prob_profitable:.1%} < {criteria.min_prob_profitable:.1%}"
            )

    return len(failures) == 0, failures
