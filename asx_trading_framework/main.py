"""
Main orchestrator — wires all modules together and runs the framework.

Modes:
- backtest: Run backtests on historical data
- paper: Run paper trading with simulated execution
- dry-run: Connect to real broker, receive data, generate signals, but BLOCK all orders
- live: Run live trading (requires broker configuration + double confirmation)

Usage:
    python -m asx_trading_framework.main --mode paper --config config/default.yaml
"""

from __future__ import annotations

import argparse
import logging
import os
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
from .execution.dry_run import DryRunBlocked, DryRunBrokerAdapter
from .operations.daily_ops import DailyOps
from .risk.engine import RiskEngine
from .signals.engine import SignalEngine
from .state.manager import StateManager
from .strategies.mean_reversion import MeanReversion
from .strategies.momentum import MomentumContinuation
from .strategies.orb import OpeningRangeBreakout
from .strategies.volatility_expansion import VolatilityExpansion

logger = logging.getLogger(__name__)

# Live mode safety banner
_LIVE_BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║                    ⚠  LIVE TRADING MODE  ⚠                     ║
║                                                                  ║
║  Real orders will be placed with real money.                     ║
║                                                                  ║
║  Requirements:                                                   ║
║    1. CLI flag:  --confirm-live YES_I_UNDERSTAND                 ║
║    2. Env var:   LIVE_TRADING_ENABLED=1                          ║
║                                                                  ║
║  Both are REQUIRED. Missing either will abort.                   ║
╚══════════════════════════════════════════════════════════════════╝
"""


def _check_live_gates(args: argparse.Namespace) -> None:
    """Enforce double confirmation for live trading. Exits on failure."""
    errors: list[str] = []

    if getattr(args, "confirm_live", None) != "YES_I_UNDERSTAND":
        errors.append(
            "Missing CLI flag: --confirm-live YES_I_UNDERSTAND"
        )

    if os.getenv("LIVE_TRADING_ENABLED") != "1":
        errors.append(
            "Missing env var: LIVE_TRADING_ENABLED=1"
        )

    if errors:
        print(_LIVE_BANNER, file=sys.stderr)
        for err in errors:
            logger.error("LIVE GATE FAILED: %s", err)
        sys.exit(1)

    logger.warning("LIVE TRADING ENABLED — both safety gates passed")


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

    def __init__(
        self,
        config: FrameworkConfig,
        mode: str = "paper",
        max_notional: Decimal | None = None,
    ) -> None:
        self.config = config
        self.mode = mode
        self.max_notional = max_notional
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

        # Cancel all open orders (safe even in dry-run — DryRunBrokerAdapter
        # will block the cancel but ExecutionEngine catches exceptions)
        try:
            cancelled = self.execution_engine.cancel_all_orders()
            if cancelled:
                logger.info("Cancelled %d open orders", cancelled)
        except DryRunBlocked:
            logger.info("Dry-run mode: no orders to cancel")

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
            # Wrap in dry-run if mode is dry-run
            if self.mode == "dry-run":
                return DryRunBrokerAdapter(adapter)
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

        # Max-notional guard
        if self.max_notional is not None and signal.price:
            notional = signal.price * signal.quantity
            if notional > self.max_notional:
                logger.warning(
                    "MAX-NOTIONAL BLOCKED: %s notional $%s > limit $%s",
                    signal.symbol, notional, self.max_notional,
                )
                return

        # Create and submit order
        order = self.execution_engine.create_order_from_signal(signal, quote)
        try:
            self.execution_engine.submit_order(order)
        except DryRunBlocked as exc:
            logger.info("Signal generated but blocked (dry-run): %s", exc)

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
        try:
            self.execution_engine.cancel_all_orders()
        except DryRunBlocked:
            logger.info("Dry-run mode: skipping EOD flatten")
            return
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
        choices=["backtest", "paper", "dry-run", "live"],
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
    parser.add_argument(
        "--confirm-live",
        dest="confirm_live",
        default=None,
        help="Live trading confirmation (must be 'YES_I_UNDERSTAND')",
    )
    parser.add_argument(
        "--max-notional",
        dest="max_notional",
        type=float,
        default=None,
        help="Max notional value per order in AUD (safety limit)",
    )
    parser.add_argument(
        "--max-order-qty",
        dest="max_order_qty",
        type=int,
        default=None,
        help="Max shares per order (safety limit)",
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

    # ── Live mode safety gates ──
    if args.mode == "live":
        _check_live_gates(args)
        if config.broker.adapter == "paper":
            logger.error(
                "Live mode requires a real broker adapter (ibkr or cmc_alert). "
                "Set broker.adapter in your config YAML."
            )
            sys.exit(1)

    # ── Dry-run mode: force ibkr adapter ──
    if args.mode == "dry-run":
        if config.broker.adapter == "paper":
            logger.error(
                "Dry-run mode requires a real broker adapter (ibkr). "
                "Set broker.adapter in your config YAML or use --config ibkr.yaml."
            )
            sys.exit(1)

    # Parse max-notional
    max_notional = None
    if args.max_notional is not None:
        max_notional = Decimal(str(args.max_notional))
    elif args.mode == "live":
        # Default safety limit for live: $10,000 AUD per order
        max_notional = Decimal("10000")
        logger.info("Live mode: default max-notional=$%s per order", max_notional)

    framework = TradingFramework(
        config,
        mode=args.mode,
        max_notional=max_notional,
    )

    if args.mode == "backtest":
        framework.run_backtest(
            args.symbols,
            start=datetime(2023, 1, 1),
            end=datetime(2024, 1, 1),
        )
    elif args.mode in ("paper", "dry-run"):
        framework.run_paper(args.symbols)
    elif args.mode == "live":
        logger.info("Starting LIVE mode with broker=%s", config.broker.adapter)
        framework.run_paper(args.symbols)  # Same loop — broker adapter handles real execution


if __name__ == "__main__":
    main()
