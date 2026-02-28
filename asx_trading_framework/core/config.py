"""
Configuration management.

All numeric defaults are clearly labeled as DEFAULT and are configurable.
Config is loaded from YAML with environment variable overrides.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml


@dataclass
class BrokerConfig:
    """Broker/API configuration. Design: abstract interface; concrete adapters."""
    adapter: str = "paper"  # DEFAULT: paper trading adapter
    api_key: str = ""
    api_secret: str = ""
    api_url: str = ""
    account_id: str = ""
    timeout_seconds: int = 10  # DEFAULT


@dataclass
class RiskConfig:
    """Hard risk limits. All values are DEFAULT and configurable."""
    max_loss_per_trade: Decimal = Decimal("500")        # DEFAULT: $500 AUD
    max_daily_loss: Decimal = Decimal("2000")            # DEFAULT: $2,000 AUD
    max_weekly_loss: Decimal = Decimal("5000")           # DEFAULT: $5,000 AUD
    max_positions: int = 5                               # DEFAULT
    max_trades_per_day: int = 20                         # DEFAULT
    max_participation_rate: Decimal = Decimal("0.05")    # DEFAULT: 5% of recent volume
    max_spread_bps: Decimal = Decimal("30")              # DEFAULT: 30 basis points
    position_size_pct_of_equity: Decimal = Decimal("0.02")  # DEFAULT: 2% risk per trade
    cooldown_after_big_win_pct: Decimal = Decimal("0.5")    # DEFAULT: reduce size 50%
    cooldown_big_win_threshold: Decimal = Decimal("3.0")    # DEFAULT: 3x avg win
    cooldown_duration_minutes: int = 30                     # DEFAULT


@dataclass
class ExecutionConfig:
    """Execution parameters. All values are DEFAULT."""
    default_order_type: str = "LIMIT"     # DEFAULT
    default_time_in_force: str = "DAY"    # DEFAULT
    max_retries: int = 3                  # DEFAULT
    slippage_limit_bps: Decimal = Decimal("10")  # DEFAULT: 10 bps
    end_of_day_flat: bool = True          # DEFAULT: flatten all at EOD
    eod_flatten_time: str = "15:45"       # DEFAULT: AEST, 15 mins before close


@dataclass
class DataConfig:
    """Data source configuration."""
    historical_provider: str = "csv"   # DEFAULT: csv files; alternatives: api, database
    live_provider: str = "paper"       # DEFAULT: paper/simulated
    bar_interval_seconds: int = 60     # DEFAULT: 1-minute bars
    data_directory: str = "./data"
    symbols_file: str = ""


@dataclass
class UniverseConfig:
    """Universe and liquidity filtering."""
    min_avg_daily_volume: int = 500_000          # DEFAULT: shares/day
    min_avg_daily_turnover: Decimal = Decimal("500000")  # DEFAULT: $500K AUD
    min_price: Decimal = Decimal("0.10")         # DEFAULT: 10 cents
    max_price: Decimal = Decimal("200.00")       # DEFAULT: $200
    excluded_symbols: list[str] = field(default_factory=list)
    included_symbols: list[str] = field(default_factory=list)  # Override: only these


@dataclass
class BacktestConfig:
    """Backtesting parameters."""
    commission_per_trade: Decimal = Decimal("10.00")  # DEFAULT: $10 flat
    commission_pct: Decimal = Decimal("0.001")        # DEFAULT: 0.1% (use max of flat/pct)
    slippage_model: str = "fixed_bps"                 # DEFAULT
    slippage_bps: Decimal = Decimal("5")              # DEFAULT: 5 bps
    walk_forward_train_days: int = 252                # DEFAULT: 1 year
    walk_forward_test_days: int = 63                  # DEFAULT: 3 months
    monte_carlo_iterations: int = 1000                # DEFAULT
    out_of_sample_pct: Decimal = Decimal("0.30")      # DEFAULT: 30%
    min_trades_for_significance: int = 30             # DEFAULT


@dataclass
class OperationsConfig:
    """Operations and journaling."""
    journal_directory: str = "./journal"
    alerts_webhook_url: str = ""
    daily_prep_time: str = "09:00"      # DEFAULT: AEST
    eod_review_time: str = "16:30"      # DEFAULT: AEST
    log_level: str = "INFO"
    log_directory: str = "./logs"
    metrics_enabled: bool = True         # DEFAULT


@dataclass
class RolloutConfig:
    """Staged rollout milestones. All values are DEFAULT."""
    paper_trading_min_weeks: int = 4                     # DEFAULT
    paper_min_trades: int = 100                          # DEFAULT
    paper_max_drawdown_pct: Decimal = Decimal("5.0")     # DEFAULT
    paper_min_expectancy: Decimal = Decimal("0.10")      # DEFAULT: $0.10/$ risked
    live_micro_size_multiplier: Decimal = Decimal("0.1") # DEFAULT: 10% of target
    live_scale_milestones: list[Decimal] = field(
        default_factory=lambda: [
            Decimal("0.1"),  # 10%
            Decimal("0.25"),
            Decimal("0.5"),
            Decimal("0.75"),
            Decimal("1.0"),  # Full size
        ]
    )


@dataclass
class NewsConfig:
    """News/announcement risk configuration."""
    no_trade_minutes_before: int = 15    # DEFAULT: minutes before scheduled event
    no_trade_minutes_after: int = 5      # DEFAULT: minutes after scheduled event
    manual_halt: bool = False            # Manual "halt risk" toggle
    halt_symbols: list[str] = field(default_factory=list)


@dataclass
class FrameworkConfig:
    """Top-level configuration aggregating all sub-configs."""
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    data: DataConfig = field(default_factory=DataConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    operations: OperationsConfig = field(default_factory=OperationsConfig)
    rollout: RolloutConfig = field(default_factory=RolloutConfig)
    news: NewsConfig = field(default_factory=NewsConfig)


def load_config(config_path: str | Path) -> FrameworkConfig:
    """
    Load configuration from a YAML file with environment variable overrides.

    Environment variables follow the pattern: ASX_TF_<SECTION>_<KEY>
    e.g., ASX_TF_RISK_MAX_DAILY_LOSS=3000
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    config = FrameworkConfig()

    # Map YAML sections to dataclass fields
    section_map = {
        "broker": (config.broker, BrokerConfig),
        "risk": (config.risk, RiskConfig),
        "execution": (config.execution, ExecutionConfig),
        "data": (config.data, DataConfig),
        "universe": (config.universe, UniverseConfig),
        "backtest": (config.backtest, BacktestConfig),
        "operations": (config.operations, OperationsConfig),
        "rollout": (config.rollout, RolloutConfig),
        "news": (config.news, NewsConfig),
    }

    for section_name, (section_obj, _section_cls) in section_map.items():
        section_data = raw.get(section_name, {})
        if not isinstance(section_data, dict):
            continue
        for key, value in section_data.items():
            if hasattr(section_obj, key):
                current = getattr(section_obj, key)
                if isinstance(current, Decimal):
                    value = Decimal(str(value))
                setattr(section_obj, key, value)

    # Apply environment variable overrides
    _apply_env_overrides(config, section_map)

    return config


def _apply_env_overrides(
    config: FrameworkConfig,
    section_map: dict[str, tuple[Any, type]],
) -> None:
    """Override config values from environment variables."""
    prefix = "ASX_TF_"
    for env_key, env_value in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        parts = env_key[len(prefix):].lower().split("_", 1)
        if len(parts) != 2:
            continue
        section_name, field_name = parts
        if section_name not in section_map:
            continue
        section_obj, _ = section_map[section_name]
        if not hasattr(section_obj, field_name):
            continue
        current = getattr(section_obj, field_name)
        if isinstance(current, Decimal):
            setattr(section_obj, field_name, Decimal(env_value))
        elif isinstance(current, int):
            setattr(section_obj, field_name, int(env_value))
        elif isinstance(current, bool):
            setattr(section_obj, field_name, env_value.lower() in ("true", "1", "yes"))
        elif isinstance(current, str):
            setattr(section_obj, field_name, env_value)
