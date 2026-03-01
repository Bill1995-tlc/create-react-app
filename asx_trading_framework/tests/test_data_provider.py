"""Tests for data providers (CSV and Paper)."""

from __future__ import annotations

import os
import shutil
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ..core.config import DataConfig
from ..core.events import EventBus, EventType
from ..core.types import Bar, Quote
from ..data.provider import CSVDataProvider, PaperDataProvider


TEMP_DATA_DIR = "/tmp/asx_test_data"


class TestCSVDataProvider(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        """Create test CSV data."""
        data_dir = Path(TEMP_DATA_DIR)
        data_dir.mkdir(parents=True, exist_ok=True)

        # Write BHP.csv
        csv_content = (
            "timestamp,open,high,low,close,volume\n"
            "2024-01-15T10:00:00,45.00,45.50,44.50,45.20,100000\n"
            "2024-01-15T10:01:00,45.20,45.80,45.00,45.60,120000\n"
            "2024-01-15T10:02:00,45.60,46.00,45.30,45.90,90000\n"
            "2024-01-16T10:00:00,46.00,46.50,45.80,46.20,110000\n"
        )
        (data_dir / "BHP.csv").write_text(csv_content)

        # Write CBA.csv (empty, just header)
        (data_dir / "CBA.csv").write_text("timestamp,open,high,low,close,volume\n")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(TEMP_DATA_DIR, ignore_errors=True)

    def setUp(self) -> None:
        self.config = DataConfig(data_directory=TEMP_DATA_DIR)
        self.event_bus = EventBus()
        self.provider = CSVDataProvider(self.config, self.event_bus)

    def test_load_historical_bars(self) -> None:
        """Loads bars from CSV within date range."""
        bars = self.provider.get_historical_bars(
            "BHP",
            start=datetime(2024, 1, 15),
            end=datetime(2024, 1, 16),
        )
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[0].symbol, "BHP")
        self.assertEqual(bars[0].open, Decimal("45.00"))

    def test_load_filters_by_date(self) -> None:
        """Only bars within date range are returned."""
        bars = self.provider.get_historical_bars(
            "BHP",
            start=datetime(2024, 1, 16),
            end=datetime(2024, 1, 17),
        )
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].close, Decimal("46.20"))

    def test_missing_file_returns_empty(self) -> None:
        """Non-existent symbol returns empty list."""
        bars = self.provider.get_historical_bars(
            "NONEXISTENT", datetime(2024, 1, 1), datetime(2024, 12, 31),
        )
        self.assertEqual(bars, [])

    def test_empty_csv_returns_empty(self) -> None:
        """CSV with only headers returns empty."""
        bars = self.provider.get_historical_bars(
            "CBA", datetime(2024, 1, 1), datetime(2024, 12, 31),
        )
        self.assertEqual(bars, [])

    def test_stream_bars_publishes_events(self) -> None:
        """stream_bars publishes BAR events."""
        received: list[Bar] = []
        self.event_bus.subscribe(
            EventType.BAR, lambda e: received.append(e.data["bar"]),
        )

        bars = list(self.provider.stream_bars(["BHP"]))
        self.assertEqual(len(bars), 4)
        self.assertEqual(len(received), 4)

    def test_stream_bars_sorted_chronologically(self) -> None:
        """Bars from stream are in chronological order."""
        bars = list(self.provider.stream_bars(["BHP"]))
        timestamps = [b.timestamp for b in bars]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_get_latest_quote_returns_none(self) -> None:
        """CSV provider has no quote data."""
        self.assertIsNone(self.provider.get_latest_quote("BHP"))


class TestPaperDataProvider(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        data_dir = Path(TEMP_DATA_DIR)
        data_dir.mkdir(parents=True, exist_ok=True)
        csv_content = (
            "timestamp,open,high,low,close,volume\n"
            "2024-01-15T10:00:00,45.00,45.50,44.50,45.20,100000\n"
        )
        (data_dir / "BHP.csv").write_text(csv_content)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(TEMP_DATA_DIR, ignore_errors=True)

    def setUp(self) -> None:
        self.config = DataConfig(data_directory=TEMP_DATA_DIR)
        self.event_bus = EventBus()
        self.provider = PaperDataProvider(self.config, self.event_bus)

    def test_stream_publishes_quotes(self) -> None:
        """Paper provider synthesizes quotes from bars."""
        quotes: list[Quote] = []
        self.event_bus.subscribe(
            EventType.QUOTE, lambda e: quotes.append(e.data["quote"]),
        )

        list(self.provider.stream_bars(["BHP"]))
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].symbol, "BHP")
        self.assertGreater(quotes[0].ask, quotes[0].bid)

    def test_get_latest_quote_after_stream(self) -> None:
        """Can get latest quote after streaming."""
        list(self.provider.stream_bars(["BHP"]))
        quote = self.provider.get_latest_quote("BHP")
        self.assertIsNotNone(quote)
        self.assertEqual(quote.symbol, "BHP")
        # Spread should be ~10 bps of close
        spread = quote.ask - quote.bid
        self.assertGreater(spread, Decimal("0"))

    def test_get_latest_quote_no_data(self) -> None:
        """Returns None when no data has been streamed."""
        self.assertIsNone(self.provider.get_latest_quote("UNKNOWN"))

    def test_synthetic_spread(self) -> None:
        """Synthetic spread is 10 bps of close."""
        list(self.provider.stream_bars(["BHP"]))
        quote = self.provider.get_latest_quote("BHP")
        close = Decimal("45.20")
        expected_spread = close * Decimal("0.001")
        actual_spread = quote.ask - quote.bid
        self.assertAlmostEqual(float(actual_spread), float(expected_spread), places=4)


if __name__ == "__main__":
    unittest.main()
