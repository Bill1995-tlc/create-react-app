"""
Risk engine — pre-trade, intra-trade, and portfolio-level risk management.

Wizard Principles → Coded Constraints:
- Pre-trade risk check computes worst-case loss and rejects if > limit.
- Daily loss limit stops new trading for the day.
- Weekly loss limit stops new trading for the week.
- Max positions limit.
- Max trades per day limit.
- Participation rate limit (liquidity).
- Spread/impact limits.
- Post-win cool-down.
- News/announcement no-trade windows.
- Kill-switch and circuit breakers.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from ..core.config import FrameworkConfig, NewsConfig, RiskConfig
from ..core.events import Event, EventBus, EventType
from ..core.types import (
    DailyStats,
    Fill,
    Order,
    Position,
    Quote,
    RiskCheckResult,
    RiskVeto,
    Signal,
    SignalAction,
    TradeRecord,
)

logger = logging.getLogger(__name__)


class RiskEngine:
    """
    Central risk management.

    All signals pass through here BEFORE reaching the execution engine.
    Risk checks are deterministic and testable.
    """

    def __init__(self, config: FrameworkConfig, event_bus: EventBus) -> None:
        self.config = config
        self.risk_config: RiskConfig = config.risk
        self.news_config: NewsConfig = config.news
        self.event_bus = event_bus

        # State
        self._positions: dict[str, Position] = {}
        self._daily_stats = DailyStats(date=datetime.utcnow())
        self._weekly_pnl = Decimal("0")
        self._weekly_start: datetime = datetime.utcnow()
        self._trades_today: int = 0
        self._recent_wins: list[Decimal] = []
        self._cooldown_until: datetime | None = None
        self._kill_switch_active: bool = False
        self._manual_halt: bool = False
        self._halted_symbols: set[str] = set()
        self._equity: Decimal = Decimal("100000")  # DEFAULT: track via state module

        # Subscribe to events
        self.event_bus.subscribe(EventType.SIGNAL, self._on_signal)
        self.event_bus.subscribe(EventType.ORDER_FILLED, self._on_fill)

    def set_equity(self, equity: Decimal) -> None:
        """Update current equity. Called by state module."""
        self._equity = equity

    def set_positions(self, positions: dict[str, Position]) -> None:
        """Sync positions from state module."""
        self._positions = positions

    def reset_daily(self) -> None:
        """Reset daily counters. Called at start of each trading day."""
        self._daily_stats = DailyStats(date=datetime.utcnow())
        self._trades_today = 0
        logger.info("Daily risk counters reset")

    def reset_weekly(self) -> None:
        """Reset weekly counters."""
        self._weekly_pnl = Decimal("0")
        self._weekly_start = datetime.utcnow()
        logger.info("Weekly risk counters reset")

    # ──────────────────────────────────────────
    # Pre-trade risk check
    # ──────────────────────────────────────────

    def check_signal(
        self,
        signal: Signal,
        quote: Quote | None = None,
        recent_volume: int = 0,
    ) -> RiskCheckResult:
        """
        Full pre-trade risk check. Returns allowed/denied with reason.

        This is the main entry point. Every signal MUST pass through here.
        """
        # Kill switch
        if self._kill_switch_active:
            return RiskCheckResult(
                allowed=False,
                veto_reason=RiskVeto.KILL_SWITCH,
                worst_case_loss=Decimal("0"),
                details="Kill switch is active",
            )

        # Manual halt
        if self._manual_halt or signal.symbol in self._halted_symbols:
            return RiskCheckResult(
                allowed=False,
                veto_reason=RiskVeto.MANUAL_HALT,
                worst_case_loss=Decimal("0"),
                details="Manual halt active",
            )

        # Daily loss limit
        if self._daily_stats.net_pnl <= -self.risk_config.max_daily_loss:
            self.event_bus.publish(Event(
                event_type=EventType.DAILY_LOSS_LIMIT_HIT,
                data={"net_pnl": str(self._daily_stats.net_pnl)},
                source="risk_engine",
            ))
            return RiskCheckResult(
                allowed=False,
                veto_reason=RiskVeto.DAILY_LOSS_LIMIT,
                worst_case_loss=Decimal("0"),
                details=f"Daily loss {self._daily_stats.net_pnl} >= limit {self.risk_config.max_daily_loss}",
            )

        # Weekly loss limit
        if self._weekly_pnl <= -self.risk_config.max_weekly_loss:
            self.event_bus.publish(Event(
                event_type=EventType.WEEKLY_LOSS_LIMIT_HIT,
                data={"weekly_pnl": str(self._weekly_pnl)},
                source="risk_engine",
            ))
            return RiskCheckResult(
                allowed=False,
                veto_reason=RiskVeto.WEEKLY_LOSS_LIMIT,
                worst_case_loss=Decimal("0"),
                details=f"Weekly loss {self._weekly_pnl} >= limit {self.risk_config.max_weekly_loss}",
            )

        # Max trades per day
        if self._trades_today >= self.risk_config.max_trades_per_day:
            return RiskCheckResult(
                allowed=False,
                veto_reason=RiskVeto.MAX_TRADES_PER_DAY,
                worst_case_loss=Decimal("0"),
                details=f"Trades today: {self._trades_today} >= {self.risk_config.max_trades_per_day}",
            )

        # Max positions (only for new entries)
        if signal.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT):
            if len(self._positions) >= self.risk_config.max_positions:
                return RiskCheckResult(
                    allowed=False,
                    veto_reason=RiskVeto.MAX_POSITIONS,
                    worst_case_loss=Decimal("0"),
                    details=f"Positions: {len(self._positions)} >= {self.risk_config.max_positions}",
                )

        # Compute worst-case loss
        worst_case = self._compute_worst_case_loss(signal)
        if worst_case > self.risk_config.max_loss_per_trade:
            return RiskCheckResult(
                allowed=False,
                veto_reason=RiskVeto.MAX_LOSS_PER_TRADE,
                worst_case_loss=worst_case,
                details=f"Worst-case loss {worst_case} > max {self.risk_config.max_loss_per_trade}",
            )

        # Cooldown check
        if self._cooldown_until and datetime.utcnow() < self._cooldown_until:
            return RiskCheckResult(
                allowed=False,
                veto_reason=RiskVeto.COOLDOWN_ACTIVE,
                worst_case_loss=worst_case,
                details=f"Cooldown until {self._cooldown_until}",
            )

        # News window check
        if self.news_config.manual_halt:
            return RiskCheckResult(
                allowed=False,
                veto_reason=RiskVeto.NEWS_WINDOW,
                worst_case_loss=worst_case,
                details="News halt toggle is ON",
            )
        if signal.symbol in self.news_config.halt_symbols:
            return RiskCheckResult(
                allowed=False,
                veto_reason=RiskVeto.NEWS_WINDOW,
                worst_case_loss=worst_case,
                details=f"{signal.symbol} in news halt list",
            )

        # Liquidity checks (if quote available)
        if quote is not None:
            # Spread check
            if quote.mid > 0:
                spread_bps = (quote.spread / quote.mid) * 10_000
                if spread_bps > self.risk_config.max_spread_bps:
                    return RiskCheckResult(
                        allowed=False,
                        veto_reason=RiskVeto.SPREAD_TOO_WIDE,
                        worst_case_loss=worst_case,
                        details=f"Spread {spread_bps:.1f} bps > max {self.risk_config.max_spread_bps}",
                    )

            # Participation rate
            if recent_volume > 0:
                participation = Decimal(signal.quantity) / Decimal(recent_volume)
                if participation > self.risk_config.max_participation_rate:
                    return RiskCheckResult(
                        allowed=False,
                        veto_reason=RiskVeto.PARTICIPATION_RATE,
                        worst_case_loss=worst_case,
                        details=f"Participation {participation:.2%} > max {self.risk_config.max_participation_rate:.2%}",
                    )

        return RiskCheckResult(
            allowed=True,
            veto_reason=RiskVeto.ALLOWED,
            worst_case_loss=worst_case,
            details="All pre-trade checks passed",
        )

    def _compute_worst_case_loss(self, signal: Signal) -> Decimal:
        """
        Compute worst-case loss for a signal.

        If stop_loss is set: loss = |entry - stop| * quantity + estimated costs.
        If no stop_loss: loss = entry * quantity * position_size_pct (gap risk).
        """
        if signal.stop_loss is not None and signal.stop_loss > 0:
            per_share_loss = abs(signal.price - signal.stop_loss)
            raw_loss = per_share_loss * signal.quantity
        else:
            # No stop defined — assume worst-case gap of 5% (DEFAULT)
            raw_loss = signal.price * signal.quantity * Decimal("0.05")

        # Add estimated transaction costs
        commission = max(
            self.config.backtest.commission_per_trade * 2,  # Round trip
            signal.price * signal.quantity * self.config.backtest.commission_pct * 2,
        )
        slippage = signal.price * signal.quantity * self.config.backtest.slippage_bps / 10_000

        return raw_loss + commission + slippage

    # ──────────────────────────────────────────
    # Position sizing
    # ──────────────────────────────────────────

    def compute_position_size(
        self,
        entry_price: Decimal,
        stop_price: Decimal,
    ) -> int:
        """
        Compute position size based on fixed-risk model.

        Risk per trade = equity * position_size_pct_of_equity.
        Shares = risk_amount / (entry - stop).
        """
        risk_per_share = abs(entry_price - stop_price)
        if risk_per_share <= 0:
            return 0

        risk_budget = self._equity * self.risk_config.position_size_pct_of_equity

        # Apply cooldown size reduction if recently had a big win
        if self._cooldown_until and datetime.utcnow() < self._cooldown_until:
            risk_budget *= self.risk_config.cooldown_after_big_win_pct

        shares = int(risk_budget / risk_per_share)
        return max(0, shares)

    # ──────────────────────────────────────────
    # Event handlers
    # ──────────────────────────────────────────

    def _on_signal(self, event: Event) -> None:
        """Handle signal events — run risk check and forward or veto."""
        signal: Signal = event.data["signal"]
        result = self.check_signal(signal)

        if not result.allowed:
            self.event_bus.publish(Event(
                event_type=EventType.RISK_VETO,
                data={
                    "signal": signal,
                    "result": result,
                },
                source="risk_engine",
            ))
            logger.warning(
                "VETOED: %s %s — %s: %s",
                signal.action.value,
                signal.symbol,
                result.veto_reason.value,
                result.details,
            )

    def _on_fill(self, event: Event) -> None:
        """Update risk state on fills."""
        fill: Fill = event.data.get("fill")
        if fill is None:
            return
        self._trades_today += 1

    def record_trade_pnl(self, pnl: Decimal) -> None:
        """Record a closed trade's PnL. Updates daily/weekly stats + cooldown."""
        self._daily_stats.trades_count += 1
        self._daily_stats.net_pnl += pnl
        self._weekly_pnl += pnl

        if pnl > 0:
            self._daily_stats.winning_trades += 1
            self._daily_stats.largest_win = max(self._daily_stats.largest_win, pnl)
            self._recent_wins.append(pnl)
            self._check_cooldown(pnl)
        else:
            self._daily_stats.losing_trades += 1
            self._daily_stats.largest_loss = min(self._daily_stats.largest_loss, pnl)

    def _check_cooldown(self, win_pnl: Decimal) -> None:
        """Activate cooldown if a win is outsized (reduces overconfidence)."""
        if len(self._recent_wins) < 5:
            return
        avg_win = sum(self._recent_wins[-10:]) / len(self._recent_wins[-10:])
        if avg_win > 0 and win_pnl > avg_win * self.risk_config.cooldown_big_win_threshold:
            self._cooldown_until = datetime.utcnow() + timedelta(
                minutes=self.risk_config.cooldown_duration_minutes
            )
            logger.info(
                "Cooldown activated: win %s > %sx avg (%s). Until %s",
                win_pnl,
                self.risk_config.cooldown_big_win_threshold,
                avg_win,
                self._cooldown_until,
            )

    # ──────────────────────────────────────────
    # Kill switch and circuit breakers
    # ──────────────────────────────────────────

    def activate_kill_switch(self, reason: str = "") -> None:
        """Activate kill switch — no new orders, flatten all positions."""
        self._kill_switch_active = True
        self.event_bus.publish(Event(
            event_type=EventType.KILL_SWITCH_ACTIVATED,
            data={"reason": reason},
            source="risk_engine",
        ))
        logger.critical("KILL SWITCH ACTIVATED: %s", reason)

    def deactivate_kill_switch(self) -> None:
        """Deactivate kill switch (manual action required)."""
        self._kill_switch_active = False
        logger.info("Kill switch deactivated")

    def set_manual_halt(self, active: bool) -> None:
        """Toggle manual halt."""
        self._manual_halt = active
        logger.info("Manual halt: %s", active)

    def halt_symbol(self, symbol: str) -> None:
        """Halt trading on a specific symbol."""
        self._halted_symbols.add(symbol)
        logger.info("Halted symbol: %s", symbol)

    def unhalt_symbol(self, symbol: str) -> None:
        """Resume trading on a symbol."""
        self._halted_symbols.discard(symbol)

    @property
    def kill_switch_active(self) -> bool:
        return self._kill_switch_active

    @property
    def daily_stats(self) -> DailyStats:
        return self._daily_stats

    @property
    def weekly_pnl(self) -> Decimal:
        return self._weekly_pnl
