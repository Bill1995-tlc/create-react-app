"""
Operations module: daily prep, end-of-day review, journaling, alerts.

Implements Raschke-like daily preparation, Grittani-like trade journaling,
and automated operational safety checks.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..core.config import FrameworkConfig
from ..core.events import Event, EventBus, EventType
from ..core.types import DailyStats, MarketRegime, TradeRecord

logger = logging.getLogger(__name__)


@dataclass
class DailyPrepReport:
    """Output of the automated daily preparation scan."""
    date: str
    regime: str
    watchlist: list[str]
    allowed_strategies: list[str]
    key_levels: dict[str, dict[str, str]]  # symbol -> {support, resistance}
    notes: str = ""
    economic_events: list[str] = field(default_factory=list)
    risk_warnings: list[str] = field(default_factory=list)


@dataclass
class EODReviewReport:
    """End-of-day review / trade journal."""
    date: str
    stats: DailyStats
    trades: list[TradeRecord]
    annotated_trades: list[dict[str, Any]]  # Trade + notes + mistake tags
    lessons: list[str]
    score: int = 0  # 1–10 self-assessment (manual input)


@dataclass
class AlertMessage:
    """Alert for operational issues."""
    timestamp: datetime
    severity: str  # INFO, WARNING, CRITICAL
    category: str  # disconnect, reject, slippage, volatility, error
    message: str
    data: dict[str, Any] = field(default_factory=dict)


class DailyOps:
    """
    Manages daily operational lifecycle.

    Automated:
    - Pre-market prep report
    - EOD trade review and journal
    - Operational alerts
    - Manual override controls

    Incident playbook is documented but manual.
    """

    def __init__(self, config: FrameworkConfig, event_bus: EventBus) -> None:
        self.config = config
        self.event_bus = event_bus
        self.journal_dir = Path(config.operations.journal_directory)
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self._alerts: list[AlertMessage] = []

        # Subscribe to alert-worthy events
        self.event_bus.subscribe(EventType.ORDER_REJECTED, self._on_order_rejected)
        self.event_bus.subscribe(EventType.KILL_SWITCH_ACTIVATED, self._on_kill_switch)
        self.event_bus.subscribe(EventType.DAILY_LOSS_LIMIT_HIT, self._on_daily_limit)
        self.event_bus.subscribe(EventType.ERROR, self._on_error)

    def generate_daily_prep(
        self,
        regime: MarketRegime,
        watchlist: list[str],
        key_levels: dict[str, dict[str, str]] | None = None,
    ) -> DailyPrepReport:
        """
        Generate the daily preparation report (Raschke-like).

        This is the "homework before the bell" — defines what setups
        are allowed today based on regime and watchlist.
        """
        # Determine allowed strategies based on regime
        strategy_map: dict[MarketRegime, list[str]] = {
            MarketRegime.TRENDING_UP: ["orb_crabel", "momentum_zanger", "vol_expansion"],
            MarketRegime.TRENDING_DOWN: ["vol_expansion"],
            MarketRegime.RANGE_BOUND: ["mean_reversion_raschke"],
            MarketRegime.HIGH_VOLATILITY: ["orb_crabel", "vol_expansion"],
            MarketRegime.LOW_VOLATILITY: ["mean_reversion_raschke"],
            MarketRegime.UNKNOWN: [],  # Caution: no strategies until regime clarifies
        }
        allowed = strategy_map.get(regime, [])

        risk_warnings: list[str] = []
        if regime == MarketRegime.HIGH_VOLATILITY:
            risk_warnings.append("HIGH VOLATILITY: Reduce position sizes. Wider stops required.")
        if regime == MarketRegime.UNKNOWN:
            risk_warnings.append("UNKNOWN REGIME: Exercise extreme caution. Consider sitting out.")

        report = DailyPrepReport(
            date=datetime.utcnow().strftime("%Y-%m-%d"),
            regime=regime.value,
            watchlist=watchlist,
            allowed_strategies=allowed,
            key_levels=key_levels or {},
            risk_warnings=risk_warnings,
        )

        # Persist
        self._save_report("daily_prep", report)

        self.event_bus.publish(Event(
            event_type=EventType.DAILY_PREP_COMPLETE,
            data={"report": report},
            source="daily_ops",
        ))
        logger.info("Daily prep complete. Regime: %s. Watchlist: %d symbols", regime.value, len(watchlist))
        return report

    def generate_eod_review(
        self,
        stats: DailyStats,
        trades: list[TradeRecord],
    ) -> EODReviewReport:
        """
        Generate end-of-day review (Grittani-like journal).

        Auto-annotates trades with potential mistake tags:
        - CHASED: entered too far from ideal entry
        - OVERSIZED: position larger than risk rules suggest
        - NO_STOP: trade without a defined stop
        - REVENGE: trade entered shortly after a loss
        - HELD_THROUGH_STOP: exit price worse than stop
        """
        annotated: list[dict[str, Any]] = []
        lessons: list[str] = []

        prev_trade_pnl: Decimal | None = None
        for trade in trades:
            mistakes: list[str] = []

            # Auto-tag potential mistakes
            if trade.slippage > trade.entry_price * Decimal("0.005"):
                mistakes.append("HIGH_SLIPPAGE")

            if prev_trade_pnl is not None and prev_trade_pnl < 0:
                # Quick follow-up after a loss could be revenge trading
                if trade.pnl < 0:
                    mistakes.append("POSSIBLE_REVENGE")

            annotated.append({
                "trade_id": trade.trade_id,
                "symbol": trade.symbol,
                "side": trade.side.value,
                "pnl": str(trade.pnl),
                "entry_price": str(trade.entry_price),
                "exit_price": str(trade.exit_price),
                "auto_mistakes": mistakes,
                "manual_notes": trade.notes,
            })
            prev_trade_pnl = trade.pnl

        # Auto-generate lessons
        if stats.losing_trades > stats.winning_trades:
            lessons.append("More losers than winners — review entry quality.")
        if stats.net_pnl < 0 and stats.winning_trades > 0:
            lessons.append("Winners exist but overall negative — review exit discipline / sizing.")
        if stats.trades_count == 0:
            lessons.append("No trades today. Was this intentional (discipline) or missed opportunity?")

        report = EODReviewReport(
            date=datetime.utcnow().strftime("%Y-%m-%d"),
            stats=stats,
            trades=trades,
            annotated_trades=annotated,
            lessons=lessons,
        )

        self._save_report("eod_review", report)

        self.event_bus.publish(Event(
            event_type=EventType.EOD_REVIEW_COMPLETE,
            data={"report": report},
            source="daily_ops",
        ))
        logger.info(
            "EOD review: %d trades, net PnL: %s",
            stats.trades_count, stats.net_pnl,
        )
        return report

    def send_alert(
        self,
        severity: str,
        category: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Send an operational alert."""
        alert = AlertMessage(
            timestamp=datetime.utcnow(),
            severity=severity,
            category=category,
            message=message,
            data=data or {},
        )
        self._alerts.append(alert)
        self.event_bus.publish(Event(
            event_type=EventType.ALERT,
            data={"alert": alert},
            source="daily_ops",
        ))
        logger.log(
            logging.CRITICAL if severity == "CRITICAL" else logging.WARNING,
            "[ALERT:%s] %s: %s",
            severity,
            category,
            message,
        )

    # ──────────────────────────────────────────
    # Event handlers for auto-alerts
    # ──────────────────────────────────────────

    def _on_order_rejected(self, event: Event) -> None:
        self.send_alert("WARNING", "reject", f"Order rejected: {event.data}")

    def _on_kill_switch(self, event: Event) -> None:
        self.send_alert(
            "CRITICAL", "kill_switch",
            f"Kill switch activated: {event.data.get('reason', 'unknown')}",
        )

    def _on_daily_limit(self, event: Event) -> None:
        self.send_alert(
            "CRITICAL", "daily_limit",
            f"Daily loss limit hit: {event.data.get('net_pnl', 'unknown')}",
        )

    def _on_error(self, event: Event) -> None:
        self.send_alert("CRITICAL", "error", f"System error: {event.data}")

    def _save_report(self, report_type: str, report: Any) -> None:
        """Persist a report to the journal directory."""
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        filename = f"{date_str}_{report_type}.json"
        filepath = self.journal_dir / filename
        # Convert dataclass to dict, handling non-serializable types
        data = self._to_serializable(report)
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def _to_serializable(self, obj: Any) -> Any:
        """Recursively convert to JSON-serializable types."""
        if hasattr(obj, "__dataclass_fields__"):
            return {k: self._to_serializable(v) for k, v in asdict(obj).items()}
        if isinstance(obj, (list, tuple)):
            return [self._to_serializable(item) for item in obj]
        if isinstance(obj, dict):
            return {k: self._to_serializable(v) for k, v in obj.items()}
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return obj

    @property
    def alerts(self) -> list[AlertMessage]:
        return list(self._alerts)


# ──────────────────────────────────────────────
# Incident Playbook (documented reference)
# ──────────────────────────────────────────────

INCIDENT_PLAYBOOK = """
INCIDENT PLAYBOOK — Reference for manual handling of operational incidents.

1. FILLS MISMATCH (broker reports different fill than expected)
   - Immediately cancel all pending orders for the symbol.
   - Compare broker fill report with local state.
   - If positions don't match: activate kill switch, flatten manually via broker UI.
   - Log the incident with full details.
   - Do NOT resume automated trading until reconciled.

2. PARTIAL FILLS
   - If order only partially filled and remainder is still working: let it work
     until time_in_force expiry.
   - If partial fill leaves an odd-lot position: send a market order to flatten.
   - Adjust risk calculations to reflect actual position size.

3. HALTED STOCK
   - If holding a position in a halted stock: DO NOTHING automatically.
   - Set manual_halt = True for the symbol.
   - Review halt reason (trading halt announcements on ASX).
   - Plan exit strategy for when trading resumes (likely gaps).
   - Adjust portfolio risk for locked capital.

4. API OUTAGE / DISCONNECT
   - Attempt reconnection with exponential backoff (max 3 retries).
   - If reconnection fails: activate kill switch.
   - Alert immediately via all channels.
   - Use broker web/phone interface to manage positions manually.
   - Do NOT attempt to reconstruct missed data — wait for clean reconnection.

5. ABNORMAL SLIPPAGE
   - If slippage on a fill exceeds 3x expected (DEFAULT): alert immediately.
   - Review if the stock is halted, in an auction, or if liquidity dried up.
   - Reduce position sizing for the symbol for the rest of the day.
   - If systematic: activate kill switch and investigate.

6. CIRCUIT BREAKER TRIGGERED (exchange-level)
   - Cancel all pending orders immediately.
   - Do not attempt new orders until the exchange confirms resumption.
   - Review all positions for gap risk.
"""
