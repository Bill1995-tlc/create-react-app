"""
CMC Invest alert adapter.

CMC Invest (CMC Markets' ASX stockbroking platform) does NOT provide a
public retail API for automated order submission. Their API offerings
(Direct API, Prime API) are institutional-only.

This adapter operates in SIGNAL-ONLY mode:
- Receives signals from the framework's risk-approved pipeline
- Formats them as actionable alerts with exact order parameters
- Delivers alerts via: console, file log, webhook (Slack/Discord/etc.)
- You execute the orders manually in the CMC Invest app

The alert contains everything needed to place the order:
    Symbol, Side, Quantity, Limit Price, Stop Price, Take Profit

This is the recommended starting path:
    Framework does the thinking → You do the clicking
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..core.config import FrameworkConfig
from ..core.events import Event, EventBus, EventType
from ..core.types import Order, Signal, SignalAction
from ..execution.engine import BrokerAdapter, OrderStatus, transition_order

logger = logging.getLogger(__name__)


@dataclass
class TradeAlert:
    """A formatted alert ready for manual execution."""
    timestamp: datetime
    symbol: str
    action: str          # "BUY" or "SELL"
    quantity: int
    limit_price: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    strategy: str
    risk_dollars: Decimal
    notes: str = ""
    acknowledged: bool = False

    def to_text(self) -> str:
        """Human-readable alert for console/notification."""
        lines = [
            f"{'=' * 50}",
            f"  TRADE ALERT — {self.timestamp.strftime('%H:%M:%S')}",
            f"{'=' * 50}",
            f"  Action:      {self.action} {self.symbol}",
            f"  Quantity:    {self.quantity:,} shares",
            f"  Limit:       ${self.limit_price:.3f}",
        ]
        if self.stop_loss:
            lines.append(f"  Stop Loss:   ${self.stop_loss:.3f}")
        if self.take_profit:
            lines.append(f"  Take Profit: ${self.take_profit:.3f}")
        lines.extend([
            f"  Risk:        ${self.risk_dollars:.2f}",
            f"  Strategy:    {self.strategy}",
        ])
        if self.notes:
            lines.append(f"  Notes:       {self.notes}")
        lines.append(f"{'=' * 50}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serializable dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "action": self.action,
            "quantity": self.quantity,
            "limit_price": str(self.limit_price),
            "stop_loss": str(self.stop_loss) if self.stop_loss else None,
            "take_profit": str(self.take_profit) if self.take_profit else None,
            "strategy": self.strategy,
            "risk_dollars": str(self.risk_dollars),
            "notes": self.notes,
        }


class CMCAlertAdapter(BrokerAdapter):
    """
    Alert-only adapter for CMC Invest users.

    Instead of submitting orders to a broker API, this adapter:
    1. Logs the alert to console (always)
    2. Appends to alert log file (always)
    3. Sends to webhook if configured (Slack, Discord, etc.)
    4. Tracks alerts for EOD review

    Orders are marked as PENDING_NEW → the user manually executes in CMC.
    After execution, the user can mark orders as filled via the manual
    reconciliation interface.
    """

    def __init__(
        self,
        config: FrameworkConfig,
        event_bus: EventBus,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        self.webhook_url: str = config.operations.alerts_webhook_url
        self.alert_log_dir = Path(config.operations.log_directory) / "alerts"
        self.alert_log_dir.mkdir(parents=True, exist_ok=True)

        self._alerts: list[TradeAlert] = []
        self._pending_orders: dict[str, Order] = {}

        # Subscribe to risk-approved signals
        self.event_bus.subscribe(EventType.SIGNAL, self._on_signal)

    def submit_order(self, order: Order) -> bool:
        """
        'Submit' = generate and deliver alert. No actual order placed.
        Order stays in PENDING_NEW until manually reconciled.
        """
        alert = self._order_to_alert(order)
        self._alerts.append(alert)
        self._pending_orders[order.order_id] = order

        # Deliver through all channels
        self._deliver_console(alert)
        self._deliver_file(alert)
        if self.webhook_url:
            self._deliver_webhook(alert)

        # Publish alert event
        self.event_bus.publish(Event(
            event_type=EventType.ALERT,
            data={
                "alert_type": "trade_signal",
                "alert": alert.to_dict(),
                "order_id": order.order_id,
            },
            source="cmc_alert_adapter",
        ))

        # Transition to NEW to indicate alert was sent
        transition_order(order, OrderStatus.NEW)
        return True

    def cancel_order(self, order_id: str) -> bool:
        """Mark a pending alert as cancelled."""
        order = self._pending_orders.pop(order_id, None)
        if order:
            logger.info("Alert cancelled for order %s", order_id[:8])
            return True
        return False

    def get_order_status(self, order_id: str) -> OrderStatus | None:
        """Check status — always NEW until manually reconciled."""
        order = self._pending_orders.get(order_id)
        return order.status if order else None

    def get_positions(self) -> dict[str, int]:
        """
        Cannot query CMC positions via API.
        Returns empty — positions tracked internally by StateManager.
        """
        return {}

    # ──────────────────────────────────────────
    # Manual reconciliation interface
    # ──────────────────────────────────────────

    def mark_filled(
        self,
        order_id: str,
        fill_price: Decimal,
        fill_quantity: int | None = None,
        commission: Decimal = Decimal("11"),  # DEFAULT: CMC flat rate
    ) -> bool:
        """
        Manually reconcile an alert after executing in CMC Invest.

        Call this after you've placed and filled the order in the app.
        This updates the framework's internal state to match reality.
        """
        order = self._pending_orders.get(order_id)
        if order is None:
            logger.warning("Cannot reconcile unknown order: %s", order_id)
            return False

        qty = fill_quantity or order.quantity
        order.filled_quantity = qty
        order.average_fill_price = fill_price
        transition_order(order, OrderStatus.FILLED)
        self._pending_orders.pop(order_id, None)

        # Publish fill event so rest of framework updates
        from ..core.types import Fill
        import uuid
        fill = Fill(
            fill_id=str(uuid.uuid4()),
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=qty,
            price=fill_price,
            commission=commission,
            timestamp=datetime.utcnow(),
        )
        self.event_bus.publish(Event(
            event_type=EventType.ORDER_FILLED,
            data={"order": order, "fill": fill},
            source="cmc_alert_adapter",
        ))

        logger.info(
            "Manually reconciled: %s %s %d @ %s",
            order.side.value, order.symbol, qty, fill_price,
        )
        return True

    def mark_not_filled(self, order_id: str, reason: str = "") -> bool:
        """Mark an alert as not executed (you decided to skip it)."""
        order = self._pending_orders.pop(order_id, None)
        if order is None:
            return False
        transition_order(order, OrderStatus.EXPIRED)
        logger.info("Alert skipped: %s — %s", order_id[:8], reason)
        return True

    def get_pending_alerts(self) -> list[tuple[str, TradeAlert]]:
        """Get all pending alerts awaiting manual execution."""
        result: list[tuple[str, TradeAlert]] = []
        for order_id, order in self._pending_orders.items():
            # Find matching alert
            for alert in reversed(self._alerts):
                if alert.symbol == order.symbol and not alert.acknowledged:
                    result.append((order_id, alert))
                    break
        return result

    # ──────────────────────────────────────────
    # Alert delivery
    # ──────────────────────────────────────────

    def _order_to_alert(self, order: Order) -> TradeAlert:
        """Convert an Order to a human-readable TradeAlert."""
        risk = Decimal("0")
        if order.stop_price and order.price:
            risk = abs(order.price - order.stop_price) * order.quantity

        action = "BUY" if order.side.value == "BUY" else "SELL"
        notes_parts: list[str] = []
        if order.tags:
            for k, v in order.tags.items():
                notes_parts.append(f"{k}={v}")

        return TradeAlert(
            timestamp=datetime.utcnow(),
            symbol=order.symbol,
            action=action,
            quantity=order.quantity,
            limit_price=order.price or Decimal("0"),
            stop_loss=order.stop_price,
            take_profit=None,  # From signal metadata if available
            strategy=order.strategy_id,
            risk_dollars=risk,
            notes="; ".join(notes_parts) if notes_parts else "",
        )

    def _deliver_console(self, alert: TradeAlert) -> None:
        """Print alert to console with clear formatting."""
        print(alert.to_text())
        logger.info(
            "ALERT: %s %s %d @ %s (strategy: %s)",
            alert.action, alert.symbol, alert.quantity,
            alert.limit_price, alert.strategy,
        )

    def _deliver_file(self, alert: TradeAlert) -> None:
        """Append alert to daily log file."""
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        filepath = self.alert_log_dir / f"{date_str}_alerts.jsonl"
        with open(filepath, "a") as f:
            f.write(json.dumps(alert.to_dict(), default=str) + "\n")

    def _deliver_webhook(self, alert: TradeAlert) -> None:
        """Send alert to a webhook (Slack, Discord, etc.)."""
        if not self.webhook_url:
            return
        try:
            payload = json.dumps({
                "text": alert.to_text(),
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*{alert.action} {alert.symbol}*\n"
                                f"Qty: {alert.quantity:,} | "
                                f"Limit: ${alert.limit_price:.3f} | "
                                f"Stop: ${alert.stop_loss:.3f if alert.stop_loss else 'N/A'} | "
                                f"Risk: ${alert.risk_dollars:.2f}\n"
                                f"Strategy: `{alert.strategy}`"
                            ),
                        },
                    }
                ],
            }).encode("utf-8")

            req = urllib.request.Request(
                self.webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != 200:
                    logger.warning("Webhook returned status %d", resp.status)
        except Exception:
            logger.exception("Failed to deliver webhook alert")

    def _on_signal(self, event: Event) -> None:
        """Log signals for observability (does not create orders — that's the framework's job)."""
        pass

    @property
    def alerts(self) -> list[TradeAlert]:
        return list(self._alerts)

    @property
    def pending_count(self) -> int:
        return len(self._pending_orders)
