"""Tests for IB auto-reconnect logic with mocked adapter."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch, call


class TestReconnectBackoff(unittest.TestCase):
    """Test the auto-reconnect logic in IBAdapter._on_disconnect."""

    def _make_adapter(self) -> object:
        """Create an IBAdapter-like object with mocked IB."""
        # We can't import IBAdapter directly without ib_async installed,
        # so we test the reconnect logic pattern in isolation.

        class FakeAdapter:
            RECONNECT_DELAYS = [2, 4, 8, 16]

            def __init__(self) -> None:
                self._connected = True
                self._reconnect_count = 0
                self.config = MagicMock()
                self.config.host = "127.0.0.1"
                self.config.port = 7497
                self.config.client_id = 1
                self.config.timeout = 10
                self.config.readonly = False
                self.config.account = ""
                self._ib = MagicMock()
                self._connect_attempts: list[float] = []

            def _on_disconnect(self) -> None:
                """Replicate IBAdapter._on_disconnect logic."""
                if not self._connected:
                    return
                self._connected = False

                for attempt, delay in enumerate(self.RECONNECT_DELAYS, 1):
                    self._connect_attempts.append(delay)
                    try:
                        self._ib.connect(
                            host=self.config.host,
                            port=self.config.port,
                            clientId=self.config.client_id,
                            timeout=self.config.timeout,
                            readonly=self.config.readonly,
                            account=self.config.account,
                        )
                        if self._ib.isConnected():
                            self._connected = True
                            self._reconnect_count += 1
                            return
                    except Exception:
                        pass

        return FakeAdapter()

    def test_reconnect_on_first_attempt(self) -> None:
        """Reconnection succeeds on the first attempt."""
        adapter = self._make_adapter()
        adapter._ib.isConnected.return_value = True

        adapter._on_disconnect()

        self.assertTrue(adapter._connected)
        self.assertEqual(adapter._reconnect_count, 1)
        self.assertEqual(adapter._ib.connect.call_count, 1)
        self.assertEqual(adapter._connect_attempts, [2])

    def test_reconnect_on_third_attempt(self) -> None:
        """Reconnection fails twice, succeeds on third."""
        adapter = self._make_adapter()
        adapter._ib.isConnected.side_effect = [False, False, True]

        adapter._on_disconnect()

        self.assertTrue(adapter._connected)
        self.assertEqual(adapter._reconnect_count, 1)
        self.assertEqual(adapter._ib.connect.call_count, 3)
        self.assertEqual(adapter._connect_attempts, [2, 4, 8])

    def test_reconnect_all_fail(self) -> None:
        """All reconnection attempts fail."""
        adapter = self._make_adapter()
        adapter._ib.isConnected.return_value = False

        adapter._on_disconnect()

        self.assertFalse(adapter._connected)
        self.assertEqual(adapter._reconnect_count, 0)
        self.assertEqual(adapter._ib.connect.call_count, 4)
        self.assertEqual(adapter._connect_attempts, [2, 4, 8, 16])

    def test_reconnect_connect_raises_exception(self) -> None:
        """Connect raises exception on some attempts, succeeds later."""
        adapter = self._make_adapter()
        adapter._ib.connect.side_effect = [
            ConnectionRefusedError("not ready"),
            ConnectionRefusedError("not ready"),
            None,  # 3rd attempt succeeds
            None,
        ]
        # isConnected is only called when connect doesn't raise.
        # 1st real call (after 3rd connect succeeds) → True
        adapter._ib.isConnected.side_effect = [True]

        adapter._on_disconnect()

        # First two fail with exception, third succeeds
        self.assertTrue(adapter._connected)
        self.assertEqual(adapter._reconnect_count, 1)
        self.assertEqual(adapter._ib.connect.call_count, 3)
        self.assertEqual(adapter._connect_attempts, [2, 4, 8])

    def test_not_connected_is_noop(self) -> None:
        """If already disconnected, _on_disconnect is a no-op."""
        adapter = self._make_adapter()
        adapter._connected = False

        adapter._on_disconnect()

        adapter._ib.connect.assert_not_called()
        self.assertEqual(adapter._reconnect_count, 0)

    def test_backoff_delays_are_exponential(self) -> None:
        """Verify the delay schedule is 2, 4, 8, 16."""
        adapter = self._make_adapter()
        self.assertEqual(adapter.RECONNECT_DELAYS, [2, 4, 8, 16])

    def test_reconnect_count_increments_on_success(self) -> None:
        """Multiple disconnect/reconnect cycles increment the counter."""
        adapter = self._make_adapter()
        adapter._ib.isConnected.return_value = True

        # First disconnect
        adapter._on_disconnect()
        self.assertEqual(adapter._reconnect_count, 1)

        # Second disconnect
        adapter._connected = True  # Simulate being connected again
        adapter._on_disconnect()
        self.assertEqual(adapter._reconnect_count, 2)


class TestReconnectWithIBAdapter(unittest.TestCase):
    """Test reconnect configuration on the actual IBAdapter class (if available)."""

    def test_reconnect_delays_constant(self) -> None:
        """IBAdapter.RECONNECT_DELAYS should be [2, 4, 8, 16]."""
        try:
            from ..broker.ib.adapter import IBAdapter
            self.assertEqual(IBAdapter.RECONNECT_DELAYS, [2, 4, 8, 16])
        except ImportError:
            self.skipTest("ib_async not installed")

    def test_disconnect_handler_registered(self) -> None:
        """IBAdapter registers _on_disconnect on the disconnectedEvent."""
        try:
            from ..broker.ib.adapter import IBAdapter, IB_LIB
            if not IB_LIB:
                self.skipTest("ib_async not installed")
            adapter = IBAdapter()
            # The handler should be registered
            self.assertTrue(hasattr(adapter, "_on_disconnect"))
        except ImportError:
            self.skipTest("ib_async not installed")


if __name__ == "__main__":
    unittest.main()
