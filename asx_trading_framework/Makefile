# ASX Trading Framework — Operational Makefile
#
# Usage:
#   make check                         Run unit tests
#   make smoke-paper SYMBOL=BHP        Smoke test on IB paper
#   make dry-run-live SYMBOL=BHP       Dry-run on live IB (no orders)
#   make simulate-disconnect SYMBOL=BHP  Test auto-reconnect
#   make simulate-restart              Test state persistence & recovery
#   make test-connection               Quick IB connection test
#   make test-market-data SYMBOL=BHP   Test market data only
#   make test-order SYMBOL=BHP         Test order (paper, guarded)

.PHONY: check smoke-paper dry-run-live simulate-disconnect simulate-restart \
        test-connection test-market-data test-order lint help

# Defaults
SYMBOL ?= BHP
PYTHON ?= python
TIMEOUT ?= 30
STREAM_DURATION ?= 10

# ──────────────────────────────────────────────
# Unit tests
# ──────────────────────────────────────────────

check:
	@echo "══════════════════════════════════════════"
	@echo "  Running unit tests"
	@echo "══════════════════════════════════════════"
	@cd .. && $(PYTHON) -m unittest \
		asx_trading_framework.tests.test_signal_engine \
		asx_trading_framework.tests.test_strategies \
		asx_trading_framework.tests.test_state_manager \
		asx_trading_framework.tests.test_data_provider \
		asx_trading_framework.tests.test_universe_filter \
		asx_trading_framework.tests.test_cmc_alert \
		asx_trading_framework.tests.test_ib_adapter \
		asx_trading_framework.tests.test_order_state_machine \
		asx_trading_framework.tests.test_backtest \
		asx_trading_framework.tests.test_risk_engine \
		asx_trading_framework.tests.test_dry_run \
		asx_trading_framework.tests.test_reconnect \
		2>&1; \
	EXIT=$$?; \
	echo; \
	if [ $$EXIT -eq 0 ]; then \
		echo "  ✓ All tests PASSED"; \
	else \
		echo "  ✗ Tests FAILED (exit code $$EXIT)"; \
	fi; \
	echo "══════════════════════════════════════════"; \
	exit $$EXIT

lint:
	@echo "Running lint (if ruff is installed)..."
	@which ruff > /dev/null 2>&1 && cd .. && ruff check asx_trading_framework/ || echo "ruff not installed, skipping"

# ──────────────────────────────────────────────
# IB Operational tests (require TWS/Gateway)
# ──────────────────────────────────────────────

test-connection:
	@echo "Testing IB connection..."
	@cd .. && $(PYTHON) -m asx_trading_framework.tools.ib_test_connection

test-market-data:
	@echo "Testing IB market data for $(SYMBOL)..."
	@cd .. && $(PYTHON) -m asx_trading_framework.tools.ib_test_market_data $(SYMBOL) --stream $(STREAM_DURATION)

test-order:
	@echo "Testing IB order for $(SYMBOL) (paper only, guarded)..."
	@cd .. && CONFIRM_TEST_ORDER=1 $(PYTHON) -m asx_trading_framework.tools.ib_test_order $(SYMBOL)

# ──────────────────────────────────────────────
# Smoke test — full paper validation
# ──────────────────────────────────────────────

smoke-paper:
	@echo "══════════════════════════════════════════"
	@echo "  Smoke test: IB paper for $(SYMBOL)"
	@echo "══════════════════════════════════════════"
	@cd .. && $(PYTHON) -m asx_trading_framework.tools.smoke_paper $(SYMBOL) --timeout $(TIMEOUT)

# ──────────────────────────────────────────────
# Dry-run live — connect to live, verify no orders
# ──────────────────────────────────────────────

dry-run-live:
	@echo "══════════════════════════════════════════"
	@echo "  Dry-run live test for $(SYMBOL)"
	@echo "  Port: $${IB_PORT:-7496} (live default)"
	@echo "══════════════════════════════════════════"
	@cd .. && $(PYTHON) -m asx_trading_framework.tools.dry_run_live $(SYMBOL)

# ──────────────────────────────────────────────
# Simulate disconnect + reconnect
# ──────────────────────────────────────────────

simulate-disconnect:
	@echo "══════════════════════════════════════════"
	@echo "  Disconnect simulation for $(SYMBOL)"
	@echo "══════════════════════════════════════════"
	@cd .. && $(PYTHON) -m asx_trading_framework.tools.simulate_disconnect $(SYMBOL) --timeout 90

# ──────────────────────────────────────────────
# Simulate restart + state recovery
# ──────────────────────────────────────────────

simulate-restart:
	@echo "══════════════════════════════════════════"
	@echo "  Restart simulation (state recovery)"
	@echo "══════════════════════════════════════════"
	@cd .. && $(PYTHON) -m asx_trading_framework.tools.simulate_restart --duration 5

# ──────────────────────────────────────────────
# Help
# ──────────────────────────────────────────────

help:
	@echo ""
	@echo "ASX Trading Framework — Operational Commands"
	@echo ""
	@echo "  Unit tests (no IB required):"
	@echo "    make check                          Run all unit tests"
	@echo "    make lint                           Run linter (if installed)"
	@echo ""
	@echo "  IB connection tests:"
	@echo "    make test-connection                Quick connect/disconnect"
	@echo "    make test-market-data SYMBOL=BHP    Fetch market data"
	@echo "    make test-order SYMBOL=BHP          Place test order (paper)"
	@echo ""
	@echo "  Operational validation:"
	@echo "    make smoke-paper SYMBOL=BHP         Full paper smoke test"
	@echo "    make dry-run-live SYMBOL=BHP        Dry-run on live connection"
	@echo "    make simulate-disconnect SYMBOL=BHP Test auto-reconnect"
	@echo "    make simulate-restart               Test state recovery"
	@echo ""
	@echo "  Environment variables:"
	@echo "    IB_HOST     IB host (default: 127.0.0.1)"
	@echo "    IB_PORT     IB port (paper: 7497, live: 7496)"
	@echo "    IB_MODE     paper or live"
	@echo "    SYMBOL      ASX ticker (default: BHP)"
	@echo ""
