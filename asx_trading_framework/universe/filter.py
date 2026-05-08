"""
Universe and liquidity filter.

Applies Zanger-like volume/price discipline to build a tradeable universe.
Filters are applied daily during the prep phase and on each trade.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from ..core.config import UniverseConfig
from ..core.types import Bar, Quote

logger = logging.getLogger(__name__)


@dataclass
class SymbolMetrics:
    """Pre-computed metrics for a symbol used in universe filtering."""
    symbol: str
    avg_daily_volume: int
    avg_daily_turnover: Decimal
    last_close: Decimal
    avg_spread_bps: Decimal
    days_of_data: int


class UniverseFilter:
    """
    Filters symbols to build a tradeable universe.

    Criteria (all DEFAULT, configurable):
    - Minimum average daily volume
    - Minimum average daily turnover (price * volume)
    - Price range (min/max)
    - Explicit include/exclude lists
    """

    def __init__(self, config: UniverseConfig) -> None:
        self.config = config

    def compute_metrics(
        self, symbol: str, bars: Sequence[Bar]
    ) -> SymbolMetrics | None:
        """Compute liquidity metrics from historical bars."""
        if not bars:
            return None

        daily_volumes: list[int] = []
        daily_turnovers: list[Decimal] = []

        # Group by date
        bars_by_date: dict[str, list[Bar]] = {}
        for bar in bars:
            date_key = bar.timestamp.strftime("%Y-%m-%d")
            bars_by_date.setdefault(date_key, []).append(bar)

        for date_bars in bars_by_date.values():
            total_vol = sum(b.volume for b in date_bars)
            # Approximate turnover: sum(close * volume) per bar
            total_turnover = sum(b.close * b.volume for b in date_bars)
            daily_volumes.append(total_vol)
            daily_turnovers.append(total_turnover)

        if not daily_volumes:
            return None

        avg_vol = sum(daily_volumes) // len(daily_volumes)
        avg_turnover = sum(daily_turnovers) / len(daily_turnovers)
        last_close = bars[-1].close

        return SymbolMetrics(
            symbol=symbol,
            avg_daily_volume=avg_vol,
            avg_daily_turnover=avg_turnover,
            last_close=last_close,
            avg_spread_bps=Decimal("0"),  # Computed from quotes if available
            days_of_data=len(bars_by_date),
        )

    def passes_filter(self, metrics: SymbolMetrics) -> bool:
        """Check if a symbol meets all universe criteria."""
        cfg = self.config

        # Explicit include list overrides other filters
        if cfg.included_symbols and metrics.symbol not in cfg.included_symbols:
            return False

        if metrics.symbol in cfg.excluded_symbols:
            logger.debug("Excluded by explicit list: %s", metrics.symbol)
            return False

        if metrics.avg_daily_volume < cfg.min_avg_daily_volume:
            logger.debug(
                "%s rejected: avg vol %d < %d",
                metrics.symbol, metrics.avg_daily_volume, cfg.min_avg_daily_volume,
            )
            return False

        if metrics.avg_daily_turnover < cfg.min_avg_daily_turnover:
            logger.debug(
                "%s rejected: avg turnover %s < %s",
                metrics.symbol, metrics.avg_daily_turnover, cfg.min_avg_daily_turnover,
            )
            return False

        if metrics.last_close < cfg.min_price or metrics.last_close > cfg.max_price:
            logger.debug(
                "%s rejected: price %s outside [%s, %s]",
                metrics.symbol, metrics.last_close, cfg.min_price, cfg.max_price,
            )
            return False

        return True

    def build_universe(
        self, all_metrics: list[SymbolMetrics]
    ) -> list[SymbolMetrics]:
        """Filter all symbols and return the tradeable universe."""
        universe = [m for m in all_metrics if self.passes_filter(m)]
        # Sort by liquidity (highest turnover first)
        universe.sort(key=lambda m: m.avg_daily_turnover, reverse=True)
        logger.info(
            "Universe: %d of %d symbols passed filter",
            len(universe), len(all_metrics),
        )
        return universe

    def check_trade_liquidity(
        self,
        quote: Quote,
        order_quantity: int,
        recent_volume: int,
        max_participation_rate: Decimal,
        max_spread_bps: Decimal,
    ) -> tuple[bool, str]:
        """
        Per-trade liquidity check (Baldwin/Rotter-inspired, compliant).

        Returns (allowed, reason).
        """
        # Check spread
        if quote.mid > 0:
            spread_bps = (quote.spread / quote.mid) * 10_000
            if spread_bps > max_spread_bps:
                return False, f"Spread {spread_bps:.1f} bps > max {max_spread_bps} bps"

        # Check participation rate
        if recent_volume > 0:
            participation = Decimal(order_quantity) / Decimal(recent_volume)
            if participation > max_participation_rate:
                return False, (
                    f"Participation {participation:.2%} > max "
                    f"{max_participation_rate:.2%}"
                )

        return True, "OK"
