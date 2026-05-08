"""
Integration tests for the IB adapter.

These tests require a LIVE connection to TWS/IB Gateway.
They are SKIPPED by default. To run them:

    RUN_IB_INTEGRATION=1 python -m pytest asx_trading_framework/tests/test_ib_integration.py -v

Prerequisites:
    - TWS or IB Gateway running (paper trading recommended)
    - API enabled in TWS settings
    - ib_async installed: pip install ib_async
    - ASX market data subscription (for quote tests)
"""

from __future__ import annotations

import asyncio
import os
import unittest

# Skip entire module if integration flag not set
SKIP_REASON = "Set RUN_IB_INTEGRATION=1 to run IB integration tests"
RUN_INTEGRATION = os.getenv("RUN_IB_INTEGRATION", "").lower() in ("1", "true", "yes")


@unittest.skipUnless(RUN_INTEGRATION, SKIP_REASON)
class TestIBIntegration(unittest.TestCase):
    """
    Live integration tests against TWS/IB Gateway.

    WARNING: These tests connect to your actual (paper) account.
    Order tests are disabled by default — uncomment at your own risk.
    """

    @classmethod
    def setUpClass(cls) -> None:
        from ..broker.ib.adapter import IBAdapter
        cls.adapter = IBAdapter()

    def _run(self, coro):
        from ..broker.ib.adapter import util
        return util.run(coro)

    def test_01_connect(self) -> None:
        """Can connect to TWS/Gateway."""
        self._run(self.adapter.connect())
        self.assertTrue(self.adapter.is_connected)

    def test_02_account_summary(self) -> None:
        """Account summary returns meaningful data."""
        summary = self._run(self.adapter.get_account_summary())
        self.assertIn("account", summary)
        # Should have at least NetLiquidation for a funded account
        print(f"\n  Account summary: {summary}")

    def test_03_positions(self) -> None:
        """Positions query returns a list."""
        positions = self._run(self.adapter.get_positions())
        self.assertIsInstance(positions, list)
        print(f"\n  Open positions: {len(positions)}")
        for pos in positions:
            print(f"    {pos}")

    def test_04_resolve_contract_bhp(self) -> None:
        """BHP resolves to a valid ASX contract."""
        contract = self._run(self.adapter.resolve_contract("BHP"))
        self.assertGreater(contract.conId, 0)
        print(f"\n  BHP → conId={contract.conId}, exchange={contract.exchange}")

    def test_05_resolve_contract_cba(self) -> None:
        """CBA resolves to a valid ASX contract."""
        contract = self._run(self.adapter.resolve_contract("CBA"))
        self.assertGreater(contract.conId, 0)
        print(f"\n  CBA → conId={contract.conId}")

    def test_06_resolve_invalid_symbol(self) -> None:
        """Invalid symbol raises IBContractError."""
        from ..broker.ib.errors import IBContractError
        with self.assertRaises(IBContractError):
            self._run(self.adapter.resolve_contract("ZZZZNOTREAL"))

    def test_07_market_data_bhp(self) -> None:
        """Can get a quote for BHP (requires ASX market data subscription)."""
        try:
            data = self._run(self.adapter.get_market_data("BHP"))
            self.assertEqual(data["symbol"], "BHP")
            print(f"\n  BHP quote: {data}")
        except Exception as exc:
            print(f"\n  BHP quote failed (may need ASX data subscription): {exc}")
            # Don't fail — market may be closed or no subscription
            self.skipTest(f"Market data unavailable: {exc}")

    def test_08_open_orders(self) -> None:
        """Can query open orders."""
        orders = self.adapter.get_open_orders()
        self.assertIsInstance(orders, list)
        print(f"\n  Open orders: {len(orders)}")

    # ──────────────────────────────────────────
    # ORDER TESTS — UNCOMMENT TO TEST LIVE
    # These will place REAL orders on your paper account.
    # ──────────────────────────────────────────

    # def test_09_place_and_cancel_limit_order(self) -> None:
    #     """Place a far-from-market limit order, then cancel it."""
    #     # Place a limit buy at a ridiculously low price (won't fill)
    #     trade = self._run(self.adapter.place_limit_order("BHP", "BUY", 1, 1.00))
    #     self.assertIsNotNone(trade)
    #     print(f"\n  Order placed: ID={trade.order.orderId}")
    #
    #     # Cancel it
    #     self._run(self.adapter.cancel_order(trade))
    #     print(f"  Order cancelled")

    def test_99_disconnect(self) -> None:
        """Disconnect cleanly."""
        self._run(self.adapter.disconnect())
        self.assertFalse(self.adapter.is_connected)


if __name__ == "__main__":
    unittest.main()
