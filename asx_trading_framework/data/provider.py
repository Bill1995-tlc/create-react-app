"""
Data ingestion: abstract provider interface + CSV and simulated implementations.

Streams Bar and Quote events through the event bus.
"""

from __future__ import annotations

import abc
import csv
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from ..core.config import DataConfig
from ..core.events import Event, EventBus, EventType
from ..core.types import Bar, Quote

logger = logging.getLogger(__name__)


class DataProvider(abc.ABC):
    """Abstract data provider. Concrete adapters implement fetch methods."""

    def __init__(self, config: DataConfig, event_bus: EventBus) -> None:
        self.config = config
        self.event_bus = event_bus

    @abc.abstractmethod
    def get_historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_seconds: int = 60,
    ) -> list[Bar]:
        """Fetch historical bars for backtesting."""

    @abc.abstractmethod
    def stream_bars(self, symbols: list[str]) -> Iterator[Bar]:
        """Stream live/simulated bars. Publishes BAR events."""

    @abc.abstractmethod
    def get_latest_quote(self, symbol: str) -> Quote | None:
        """Get the latest bid/ask quote."""

    def publish_bar(self, bar: Bar) -> None:
        self.event_bus.publish(Event(
            event_type=EventType.BAR,
            data={"bar": bar},
            source="data_provider",
        ))

    def publish_quote(self, quote: Quote) -> None:
        self.event_bus.publish(Event(
            event_type=EventType.QUOTE,
            data={"quote": quote},
            source="data_provider",
        ))


class CSVDataProvider(DataProvider):
    """
    Loads historical data from CSV files.

    Expected CSV format: timestamp,open,high,low,close,volume[,vwap,trade_count]
    File naming: {data_directory}/{symbol}.csv
    """

    def get_historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_seconds: int = 60,
    ) -> list[Bar]:
        file_path = Path(self.config.data_directory) / f"{symbol}.csv"
        if not file_path.exists():
            logger.warning("No data file for %s at %s", symbol, file_path)
            return []

        bars: list[Bar] = []
        with open(file_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = datetime.fromisoformat(row["timestamp"])
                if ts < start or ts > end:
                    continue
                bar = Bar(
                    symbol=symbol,
                    timestamp=ts,
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=int(row["volume"]),
                    vwap=Decimal(row["vwap"]) if row.get("vwap") else None,
                    trade_count=int(row["trade_count"]) if row.get("trade_count") else None,
                )
                bars.append(bar)
        logger.info("Loaded %d bars for %s", len(bars), symbol)
        return bars

    def stream_bars(self, symbols: list[str]) -> Iterator[Bar]:
        """Replay historical bars as a stream (for paper trading / backtest)."""
        all_bars: list[Bar] = []
        for symbol in symbols:
            file_path = Path(self.config.data_directory) / f"{symbol}.csv"
            if not file_path.exists():
                continue
            all_bars.extend(
                self.get_historical_bars(
                    symbol,
                    datetime.min,
                    datetime.max,
                )
            )
        # Sort by timestamp for chronological replay
        all_bars.sort(key=lambda b: b.timestamp)
        for bar in all_bars:
            self.publish_bar(bar)
            yield bar

    def get_latest_quote(self, symbol: str) -> Quote | None:
        """Not available for CSV provider — returns None."""
        return None


class PaperDataProvider(DataProvider):
    """
    Simulated data provider for paper trading.
    Wraps a CSV provider and synthesizes quotes from bars.
    """

    def __init__(self, config: DataConfig, event_bus: EventBus) -> None:
        super().__init__(config, event_bus)
        self._csv = CSVDataProvider(config, event_bus)
        self._latest_bars: dict[str, Bar] = {}

    def get_historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_seconds: int = 60,
    ) -> list[Bar]:
        return self._csv.get_historical_bars(symbol, start, end, interval_seconds)

    def stream_bars(self, symbols: list[str]) -> Iterator[Bar]:
        for bar in self._csv.stream_bars(symbols):
            self._latest_bars[bar.symbol] = bar
            # Synthesize a quote from the bar
            spread = bar.close * Decimal("0.001")  # DEFAULT: 10 bps synthetic spread
            quote = Quote(
                symbol=bar.symbol,
                timestamp=bar.timestamp,
                bid=bar.close - spread / 2,
                bid_size=1000,
                ask=bar.close + spread / 2,
                ask_size=1000,
            )
            self.publish_quote(quote)
            yield bar

    def get_latest_quote(self, symbol: str) -> Quote | None:
        bar = self._latest_bars.get(symbol)
        if bar is None:
            return None
        spread = bar.close * Decimal("0.001")
        return Quote(
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            bid=bar.close - spread / 2,
            bid_size=1000,
            ask=bar.close + spread / 2,
            ask_size=1000,
        )
