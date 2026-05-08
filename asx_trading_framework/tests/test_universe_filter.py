"""Tests for universe filter."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from ..core.config import UniverseConfig
from ..core.types import Bar, Quote
from ..universe.filter import UniverseFilter, SymbolMetrics


def make_bar(
    symbol: str = "BHP",
    ts: datetime | None = None,
    close: float = 45.0,
    volume: int = 100000,
) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=ts or datetime(2024, 1, 15, 10, 0),
        open=Decimal(str(close - 0.5)),
        high=Decimal(str(close + 0.5)),
        low=Decimal(str(close - 0.5)),
        close=Decimal(str(close)),
        volume=volume,
    )


class TestUniverseFilter(unittest.TestCase):

    def setUp(self) -> None:
        self.config = UniverseConfig()
        self.uf = UniverseFilter(self.config)

    # ──────────────────────────────────────────
    # compute_metrics
    # ──────────────────────────────────────────

    def test_compute_metrics_basic(self) -> None:
        """Metrics are computed from bars."""
        bars = [
            make_bar(ts=datetime(2024, 1, 15, 10, i), close=45.0, volume=100000)
            for i in range(10)
        ]
        metrics = self.uf.compute_metrics("BHP", bars)
        self.assertIsNotNone(metrics)
        self.assertEqual(metrics.symbol, "BHP")
        self.assertEqual(metrics.last_close, Decimal("45.0"))
        self.assertGreater(metrics.avg_daily_volume, 0)

    def test_compute_metrics_empty_bars(self) -> None:
        """Empty bars returns None."""
        self.assertIsNone(self.uf.compute_metrics("BHP", []))

    def test_compute_metrics_multiple_days(self) -> None:
        """Metrics average across multiple days."""
        bars = []
        for day in range(5):
            for minute in range(10):
                bars.append(make_bar(
                    ts=datetime(2024, 1, 15 + day, 10, minute),
                    close=45.0, volume=120000,
                ))
        metrics = self.uf.compute_metrics("BHP", bars)
        self.assertEqual(metrics.days_of_data, 5)
        # 10 bars * 120K volume per day = 1.2M / day
        self.assertEqual(metrics.avg_daily_volume, 1200000)

    # ──────────────────────────────────────────
    # passes_filter
    # ──────────────────────────────────────────

    def test_passes_filter_good_symbol(self) -> None:
        """Liquid, well-priced symbol passes."""
        metrics = SymbolMetrics(
            symbol="BHP",
            avg_daily_volume=1_000_000,
            avg_daily_turnover=Decimal("50000000"),
            last_close=Decimal("45.00"),
            avg_spread_bps=Decimal("10"),
            days_of_data=20,
        )
        self.assertTrue(self.uf.passes_filter(metrics))

    def test_fails_low_volume(self) -> None:
        """Symbol with low volume is rejected."""
        metrics = SymbolMetrics(
            symbol="ILLIQ",
            avg_daily_volume=10_000,  # Way below 500K minimum
            avg_daily_turnover=Decimal("50000000"),
            last_close=Decimal("45.00"),
            avg_spread_bps=Decimal("10"),
            days_of_data=20,
        )
        self.assertFalse(self.uf.passes_filter(metrics))

    def test_fails_low_turnover(self) -> None:
        """Symbol with low turnover is rejected."""
        metrics = SymbolMetrics(
            symbol="LOWTURN",
            avg_daily_volume=1_000_000,
            avg_daily_turnover=Decimal("100000"),  # Below $500K minimum
            last_close=Decimal("0.10"),
            avg_spread_bps=Decimal("10"),
            days_of_data=20,
        )
        self.assertFalse(self.uf.passes_filter(metrics))

    def test_fails_price_too_low(self) -> None:
        """Penny stock rejected."""
        metrics = SymbolMetrics(
            symbol="PENNY",
            avg_daily_volume=1_000_000,
            avg_daily_turnover=Decimal("1000000"),
            last_close=Decimal("0.05"),  # Below $0.10 min
            avg_spread_bps=Decimal("10"),
            days_of_data=20,
        )
        self.assertFalse(self.uf.passes_filter(metrics))

    def test_fails_price_too_high(self) -> None:
        """Very expensive stock rejected."""
        metrics = SymbolMetrics(
            symbol="EXPENSIVE",
            avg_daily_volume=1_000_000,
            avg_daily_turnover=Decimal("50000000"),
            last_close=Decimal("300.00"),  # Above $200 max
            avg_spread_bps=Decimal("10"),
            days_of_data=20,
        )
        self.assertFalse(self.uf.passes_filter(metrics))

    def test_excluded_symbol(self) -> None:
        """Explicitly excluded symbol rejected."""
        config = UniverseConfig(excluded_symbols=["BADCO"])
        uf = UniverseFilter(config)
        metrics = SymbolMetrics(
            symbol="BADCO",
            avg_daily_volume=1_000_000,
            avg_daily_turnover=Decimal("50000000"),
            last_close=Decimal("45.00"),
            avg_spread_bps=Decimal("10"),
            days_of_data=20,
        )
        self.assertFalse(uf.passes_filter(metrics))

    def test_included_list_only(self) -> None:
        """When include list is set, only those symbols pass."""
        config = UniverseConfig(included_symbols=["BHP", "CBA"])
        uf = UniverseFilter(config)

        bhp = SymbolMetrics("BHP", 1_000_000, Decimal("50000000"), Decimal("45"), Decimal("10"), 20)
        other = SymbolMetrics("XYZ", 1_000_000, Decimal("50000000"), Decimal("45"), Decimal("10"), 20)

        self.assertTrue(uf.passes_filter(bhp))
        self.assertFalse(uf.passes_filter(other))

    # ──────────────────────────────────────────
    # build_universe
    # ──────────────────────────────────────────

    def test_build_universe_sorts_by_turnover(self) -> None:
        """Universe is sorted by turnover (highest first)."""
        metrics = [
            SymbolMetrics("LOW", 1_000_000, Decimal("1000000"), Decimal("10"), Decimal("10"), 20),
            SymbolMetrics("HIGH", 1_000_000, Decimal("90000000"), Decimal("45"), Decimal("10"), 20),
            SymbolMetrics("MID", 1_000_000, Decimal("5000000"), Decimal("20"), Decimal("10"), 20),
        ]
        universe = self.uf.build_universe(metrics)
        symbols = [m.symbol for m in universe]
        self.assertEqual(symbols, ["HIGH", "MID", "LOW"])

    # ──────────────────────────────────────────
    # Per-trade liquidity check
    # ──────────────────────────────────────────

    def test_check_trade_liquidity_ok(self) -> None:
        """Normal trade passes liquidity check."""
        quote = Quote(
            symbol="BHP",
            timestamp=datetime.utcnow(),
            bid=Decimal("45.00"),
            bid_size=10000,
            ask=Decimal("45.05"),
            ask_size=10000,
        )
        ok, reason = self.uf.check_trade_liquidity(
            quote, order_quantity=100, recent_volume=500000,
            max_participation_rate=Decimal("0.05"),
            max_spread_bps=Decimal("30"),
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "OK")

    def test_check_trade_spread_too_wide(self) -> None:
        """Wide spread fails liquidity check."""
        quote = Quote(
            symbol="WIDE",
            timestamp=datetime.utcnow(),
            bid=Decimal("10.00"),
            bid_size=1000,
            ask=Decimal("10.50"),  # 500 bps spread
            ask_size=1000,
        )
        ok, reason = self.uf.check_trade_liquidity(
            quote, order_quantity=100, recent_volume=500000,
            max_participation_rate=Decimal("0.05"),
            max_spread_bps=Decimal("30"),
        )
        self.assertFalse(ok)
        self.assertIn("Spread", reason)

    def test_check_trade_participation_too_high(self) -> None:
        """Large order relative to volume fails."""
        quote = Quote(
            symbol="BHP",
            timestamp=datetime.utcnow(),
            bid=Decimal("45.00"),
            bid_size=10000,
            ask=Decimal("45.01"),
            ask_size=10000,
        )
        ok, reason = self.uf.check_trade_liquidity(
            quote, order_quantity=100000, recent_volume=500000,
            max_participation_rate=Decimal("0.05"),
            max_spread_bps=Decimal("30"),
        )
        self.assertFalse(ok)
        self.assertIn("Participation", reason)


if __name__ == "__main__":
    unittest.main()
