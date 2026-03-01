"""
Main orchestrator — wires all modules together and runs the framework.

Modes:
- backtest: Run backtests on historical data
- paper: Run paper trading with simulated execution
- live: Run live trading (requires broker configuration)

Usage:
    python -m asx_trading_framework.main --mode paper --config config/default.yaml
"""

from __future__ import annotations

import argparse
import logging
import signal as signal_module
import sys
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path

from .core.config import FrameworkConfig, load_config
from .core.events import Event, EventBus, EventType
from .core.types import MarketRegime
from .data.provider import CSVDataProvider, DataProvider, PaperDataProvider
from .execution.engine import (
    BrokerAdapter,
    ExecutionEngine,
    PaperBrokerAdapter,
)
from .execution.cmc_alert import CMCAlertAdapter
from .operations.daily_ops import DailyOps
from .risk.engine import RiskEngine
from .signals.engine import SignalEngine
from .state.manager import StateManager
from .strategies.mean_reversion import MeanReversion
from .strategies.momentum import MomentumContinuation
from .strategies.orb import OpeningRangeBreakout
from .strategies.volatility_expansion import VolatilityExpansion

logger = logging.getLogger(__name__)


class TradingFramework:
    """
    Top-level orchestrator that wires all modules together.

    ┌──────────────────────────────────────────────────────────────────┐
    │                    TRADING FRAMEWORK                             │
    │                                                                  │
    │  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌─────────────┐  │
    │  │  Data     │──▶│  Signal  │──▶│  Risk    │──▶│  Execution  │  │
    │  │  Provider │   │  Engine  │   │  Engine  │   │  Engine     │  │
    │  └──────────┘   └──────────┘   └──────────┘   └─────────────┘  │
    │       │              │              │               │            │
    │       │              │              │               │            │
    │       ▼              ▼              ▼               ▼            │
    │  ┌──────────────────────────────────────────────────────────┐   │
    │  │                     EVENT BUS                             │   │
    │  └──────────────────────────────────────────────────────────┘   │
    │       │              │              │               │            │
    │       ▼              ▼              ▼               ▼            │
    │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐    │
    │  │  State   │  │  Daily   │  │  Kill    │  │  Logging /  │    │
    │  │  Manager │  │  Ops     │  │  Switch  │  │  Metrics    │    │
    │  └──────────┘  └──────────┘  └──────────┘  └─────────────┘    │
    └──────────────────────────────────────────────────────────────────┘
    """

    def __init__(self, config: FrameworkConfig) -> None:
        self.config = config
        self._shutdown_requested = False

        # Core event bus
        self.event_bus = EventBus()

        # Modules
        self.data_provider: DataProvider = self._create_data_provider()
        self.signal_engine = SignalEngine(config, self.event_bus)
        self.risk_engine = RiskEngine(config, self.event_bus)
        self.broker: BrokerAdapter = self._create_broker()
        self.execution_engine = ExecutionEngine(config, self.event_bus, self.broker)
        self.state_manager = StateManager(self.event_bus)
        self.daily_ops = DailyOps(config, self.event_bus)

        # Recover state from previous session if available
        if self.state_manager.load_state():
            logger.info("Recovered state from previous session")

        # Register strategies
        self._register_strategies()

        # Wire up signal → risk → execution pipeline
        self.event_bus.subscribe(EventType.SIGNAL, self._on_signal_for_execution)

        # Wire up logging
        self.event_bus.subscribe_all(self._log_event)

        # Register graceful shutdown handler
        signal_module.signal(signal_module.SIGINT, self._handle_shutdown)
        signal_module.signal(signal_module.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum: int, frame: object) -> None:
        """Graceful shutdown: cancel orders, persist state, disconnect."""
        if self._shutdown_requested:
            logger.warning("Force shutdown requested")
            sys.exit(1)

        self._shutdown_requested = True
        sig_name = signal_module.Signals(signum).name
        logger.info("Shutdown signal received (%s). Cleaning up...", sig_name)

        # Cancel all open orders
        cancelled = self.execution_engine.cancel_all_orders()
        if cancelled:
            logger.info("Cancelled %d open orders", cancelled)

        # Persist final state
        self.state_manager._persist_state()

        # Disconnect broker if it supports it
        if hasattr(self.broker, "disconnect"):
            self.broker.disconnect()

        self.event_bus.publish(Event(
            event_type=EventType.SYSTEM_SHUTDOWN,
            data={"reason": sig_name},
            source="framework",
        ))
        logger.info("Graceful shutdown complete")
        sys.exit(0)

    def _create_data_provider(self) -> DataProvider:
        if self.config.data.live_provider == "paper":
            return PaperDataProvider(self.config.data, self.event_bus)
        return CSVDataProvider(self.config.data, self.event_bus)

    def _create_broker(self) -> BrokerAdapter:
        if self.config.broker.adapter == "paper":
            return PaperBrokerAdapter()
        if self.config.broker.adapter == "cmc_alert":
            return CMCAlertAdapter(self.config, self.event_bus)
        if self.config.broker.adapter == "ibkr":
            from .execution.ibkr_adapter import IBKRBrokerAdapter
            import os
            adapter = IBKRBrokerAdapter(
                event_bus=self.event_bus,
                host=os.getenv("IB_HOST", self.config.broker.api_url or "127.0.0.1"),
                port=int(os.getenv("IB_PORT", "7497")),
                client_id=int(os.getenv("IB_CLIENT_ID", "1")),
                account_id=os.getenv("IB_ACCOUNT", self.config.broker.account_id),
            )
            if not adapter.connect():
                raise ConnectionError(
                    "Failed to connect to IB TWS/Gateway. "
                    "See broker/ib/IB_SETUP.md for setup instructions."
                )
            return adapter
        raise ValueError(f"Unknown broker adapter: {self.config.broker.adapter}")

    def _register_strategies(self) -> None:
        """Register all strategy plugins."""
        self.signal_engine.register_strategy(OpeningRangeBreakout())
        self.signal_engine.register_strategy(MomentumContinuation())
        self.signal_engine.register_strategy(MeanReversion())
        self.signal_engine.register_strategy(VolatilityExpansion())

    def _on_signal_for_execution(self, event: Event) -> None:
        """Pipeline: signal → risk check → execution."""
        from .core.types import Signal, SignalAction

        signal: Signal = event.data["signal"]
        quote = self.data_provider.get_latest_quote(signal.symbol)

        # Risk check
        result = self.risk_engine.check_signal(signal, quote=quote)
        if not result.allowed:
            return

        # Compute position size if not set
        if signal.quantity == 0 and signal.stop_loss:
            quantity = self.risk_engine.compute_position_size(
                signal.price, signal.stop_loss
            )
            if quantity == 0:
                return
            # Create a new signal with computed quantity
            signal = Signal(
                strategy_id=signal.strategy_id,
                symbol=signal.symbol,
                action=signal.action,
                timestamp=signal.timestamp,
                price=signal.price,
                quantity=quantity,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                confidence=signal.confidence,
                metadata=signal.metadata,
            )

            # Re-check risk with actual quantity
            result = self.risk_engine.check_signal(signal, quote=quote)
            if not result.allowed:
                return

        # Create and submit order
        order = self.execution_engine.create_order_from_signal(signal, quote)
        self.execution_engine.submit_order(order)

    def _log_event(self, event: Event) -> None:
        """Global event logger."""
        logger.debug("[EVENT] %s from %s", event.event_type.value, event.source)

    def run_paper(self, symbols: list[str]) -> None:
        """Run paper trading mode."""
        logger.info("Starting paper trading mode with %d symbols", len(symbols))

        self.event_bus.publish(Event(
            event_type=EventType.SYSTEM_START,
            source="framework",
        ))

        # Daily prep
        regime = self.signal_engine.current_regime
        self.daily_ops.generate_daily_prep(regime, symbols)

        # Stream data and process
        for bar in self.data_provider.stream_bars(symbols):
            # Check EOD flatten
            bar_time = bar.timestamp.time()
            if self.execution_engine.should_flatten_eod(bar_time):
                self._flatten_all()
                break

        # EOD review
        self.daily_ops.generate_eod_review(
            self.risk_engine.daily_stats,
            self.state_manager.completed_trades,
        )

        self.event_bus.publish(Event(
            event_type=EventType.SYSTEM_SHUTDOWN,
            source="framework",
        ))
        logger.info("Paper trading session complete")

    def _flatten_all(self) -> None:
        """Flatten all positions for EOD."""
        self.execution_engine.cancel_all_orders()
        for symbol, position in self.state_manager.positions.items():
            logger.info("EOD flatten: %s qty=%d", symbol, position.quantity)

    def run_backtest(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> None:
        """Run backtest mode."""
        from .backtest.engine import (
            AcceptanceCriteria,
            apply_costs,
            check_acceptance,
            compute_backtest_metrics,
            monte_carlo,
        )

        logger.info("Starting backtest: %s to %s, %d symbols", start, end, len(symbols))

        # Load historical data
        for symbol in symbols:
            bars = self.data_provider.get_historical_bars(symbol, start, end)
            for bar in bars:
                self.event_bus.publish(Event(
                    event_type=EventType.BAR,
                    data={"bar": bar},
                    source="backtest",
                ))

        # Compute results
        trades = self.state_manager.completed_trades
        if not trades:
            logger.warning("No trades generated during backtest")
            return

        adjusted_trades = apply_costs(trades, self.config.backtest)
        result = compute_backtest_metrics(adjusted_trades, self.state_manager.equity)

        logger.info("=== BACKTEST RESULTS ===")
        logger.info("Total trades: %d", result.total_trades)
        logger.info("Win rate: %.1f%%", float(result.win_rate * 100))
        logger.info("Net PnL: %s", result.net_pnl)
        logger.info("Max drawdown: %s (%.1f%%)", result.max_drawdown, float(result.max_drawdown_pct))
        logger.info("Sharpe ratio: %.2f", result.sharpe_ratio)
        logger.info("Profit factor: %s", result.profit_factor)

        # Monte Carlo
        mc_result = monte_carlo(
            adjusted_trades,
            self.state_manager.equity,
            iterations=self.config.backtest.monte_carlo_iterations,
        )
        logger.info("=== MONTE CARLO ===")
        logger.info("Median PnL: %s", mc_result.median_pnl)
        logger.info("5th pct PnL: %s", mc_result.pct_5_pnl)
        logger.info("P(profitable): %.1f%%", mc_result.prob_profitable * 100)
        logger.info("P(ruin): %.1f%%", mc_result.prob_ruin * 100)

        # Acceptance
        passed, failures = check_acceptance(result, mc_result)
        if passed:
            logger.info("ACCEPTANCE: PASSED")
        else:
            logger.warning("ACCEPTANCE: FAILED")
            for f in failures:
                logger.warning("  - %s", f)


def setup_logging(level: str = "INFO", log_dir: str = "./logs") -> None:
    """Configure logging."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                Path(log_dir) / f"framework_{datetime.utcnow().strftime('%Y%m%d')}.log"
            ),
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="ASX Trading Framework")
    parser.add_argument(
        "--mode",
        choices=["backtest", "paper", "live"],
        default="paper",
        help="Operating mode (default: paper)",
    )
    parser.add_argument(
        "--config",
        default="config/default.yaml",
        help="Path to configuration file",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["BHP", "CBA", "CSL", "WBC", "ANZ"],
        help="Symbols to trade",
    )
    args = parser.parse_args()

    # Load config
    config_path = Path(args.config)
    if config_path.exists():
        config = load_config(config_path)
    else:
        logger.warning("Config file not found, using defaults")
        config = FrameworkConfig()

    setup_logging(config.operations.log_level, config.operations.log_directory)

    framework = TradingFramework(config)

    if args.mode == "backtest":
        framework.run_backtest(
            args.symbols,
            start=datetime(2023, 1, 1),
            end=datetime(2024, 1, 1),
        )
    elif args.mode == "paper":
        framework.run_paper(args.symbols)
    elif args.mode == "live":
        if config.broker.adapter == "paper":
            logger.error(
                "Live mode requires a real broker adapter (ibkr or cmc_alert). "
                "Set broker.adapter in your config YAML."
            )
            sys.exit(1)
        logger.info("Starting LIVE mode with broker=%s", config.broker.adapter)
        framework.run_paper(args.symbols)  # Same loop — broker adapter handles real execution


if __name__ == "__main__":
    main()
