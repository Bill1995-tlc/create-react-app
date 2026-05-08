# Operational Readiness / Go-Live Runbook

## Prerequisites

### 1. IBKR Setup

Complete the setup in [`broker/ib/IB_SETUP.md`](broker/ib/IB_SETUP.md):

- [ ] TWS or IB Gateway installed and running
- [ ] API connections enabled: Edit → Global Configuration → API → Settings
- [ ] "Enable ActiveX and Socket Clients" checked
- [ ] Socket port configured (paper: 7497, live: 7496)
- [ ] "Allow connections from localhost only" checked (or trusted IPs configured)
- [ ] Read-only API: **unchecked** (needed for order placement)
- [ ] Paper trading account created and funded

### 2. Market Data Subscription

Without an ASX market data subscription you will receive **delayed data only** (15-20 min delay).

To get real-time ASX data:
1. Log into [Account Management](https://www.interactivebrokers.com/sso/Login)
2. Go to Settings → Market Data Subscriptions
3. Subscribe to: **ASX Total** (or ASX Level 1 minimum)
4. Cost: ~$6 AUD/month

The framework works with delayed data for testing, but live trading requires real-time.

### 3. Python Environment

```bash
pip install pyyaml
pip install ib_async    # or: pip install ib_insync
```

---

## Validation Sequence

Run these phases **in order**. Each phase must pass before proceeding.

### Phase 1: Unit Tests (no IB required)

```bash
cd asx_trading_framework
make check
```

Expected: all tests pass (193+ tests, 0 failures).

### Phase 2: IB Connection Test

Start TWS/IB Gateway on paper account, then:

```bash
cd asx_trading_framework
make test-connection
```

Expected output:
- "Connected in X.XXs"
- Account summary with NetLiquidation
- "Connection test PASSED"

### Phase 3: Smoke Paper

```bash
cd asx_trading_framework
make smoke-paper SYMBOL=BHP
```

This validates:
1. IB paper connection
2. ASX contract resolution for the symbol
3. Account summary + positions query
4. Market data snapshot (bid/ask/last)
5. State file persistence

To also test a tiny order (1 share, auto-cancelled):

```bash
cd asx_trading_framework
CONFIRM_TEST_ORDER=1 make smoke-paper SYMBOL=BHP
```

### Phase 4: Simulate Disconnect / Reconnect

```bash
cd asx_trading_framework
make simulate-disconnect SYMBOL=BHP
```

1. The script connects and subscribes to market data
2. It prints instructions to stop/restart TWS
3. **Manually stop TWS** (File → Exit)
4. Watch the reconnect attempts with exponential backoff (2s, 4s, 8s, 16s)
5. **Restart TWS** within the backoff window
6. The script verifies data resumes

Expected: "RECONNECT TEST PASSED"

### Phase 5: Simulate Restart / State Recovery

```bash
cd asx_trading_framework
make simulate-restart
```

This validates:
1. State is written to disk correctly
2. After a simulated stop/restart, state is recovered
3. No duplicate positions or orders

Expected: "RESTART TEST PASSED"

### Phase 6: Dry-Run Live

Connect to live IB port but with all orders blocked:

```bash
cd asx_trading_framework
IB_PORT=7496 make dry-run-live SYMBOL=BHP
```

This validates:
1. Live connection works
2. Live market data is received
3. Orders are **blocked** by DryRunBrokerAdapter
4. Zero orders placed on the live account

Expected: "DRY-RUN LIVE TEST PASSED"

### Phase 7: Paper Trading with Strategies

Run the full framework in paper mode with strategies enabled:

```bash
cd asx_trading_framework
python -m asx_trading_framework.main \
    --mode paper \
    --config config/ibkr.yaml \
    --symbols BHP CBA CSL
```

Monitor:
- Signals generated (check logs)
- Risk checks passing/vetoing
- Orders submitted to paper broker
- State file updating

### Phase 8: Live Rollout (1 symbol, tiny size)

**Requirements:**
1. All previous phases passed
2. At least 4 weeks of paper trading
3. Paper trading shows positive expectancy

```bash
cd asx_trading_framework
LIVE_TRADING_ENABLED=1 python -m asx_trading_framework.main \
    --mode live \
    --confirm-live YES_I_UNDERSTAND \
    --config config/ibkr.yaml \
    --symbols BHP \
    --max-notional 5000
```

Safety features active in live mode:
- `--confirm-live YES_I_UNDERSTAND` + `LIVE_TRADING_ENABLED=1` both required
- `--max-notional 5000` caps each order at $5,000 AUD
- Default max-notional is $10,000 if not specified
- All risk engine limits still apply (daily loss, max positions, etc.)
- Graceful shutdown on CTRL+C (cancels open orders, persists state)

---

## Safety Architecture

### Three Operating Modes

| Mode | Orders | Data | Use Case |
|------|--------|------|----------|
| `paper` | Simulated fills | Simulated | Strategy development |
| `dry-run` | **HARD BLOCKED** | Real (IB) | Validate live connectivity |
| `live` | Real (IB) | Real (IB) | Production trading |

### Live Mode Double Gate

Live mode requires **both**:
1. CLI flag: `--confirm-live YES_I_UNDERSTAND`
2. Environment variable: `LIVE_TRADING_ENABLED=1`

If either is missing, the framework logs an error and exits with code 1.

### Max-Notional Guard

`--max-notional <AUD>` limits the notional value (price x quantity) of any single order. Default for live mode is $10,000 AUD.

### Dry-Run Blocking

The `DryRunBrokerAdapter` wraps any real broker adapter and raises `DryRunBlocked` on:
- `submit_order()` — always blocked
- `cancel_order()` — always blocked

Read-only operations pass through:
- `get_positions()` — delegated
- `get_order_status()` — delegated
- `connect()` / `disconnect()` — delegated

---

## Troubleshooting

### Connection Issues

| Error | Cause | Fix |
|-------|-------|-----|
| Connection refused | TWS/Gateway not running | Start TWS or IB Gateway |
| Connection timeout | Wrong port | Paper: 7497, Live: 7496, GW Paper: 4002, GW Live: 4001 |
| Duplicate client ID | Another app using same ID | Change `IB_CLIENT_ID` env var |
| Client not authenticated | Trusted IP not set | Add 127.0.0.1 to trusted IPs in TWS config |

### Market Data Issues

| Error Code | Meaning | Fix |
|------------|---------|-----|
| 354 | No market data subscription | Subscribe to ASX data in Account Management |
| 10167 | Delayed data only | Subscribe to real-time ASX data |
| 10197 | Competing live session | Close other TWS sessions |

### Order Issues

| Error Code | Meaning | Fix |
|------------|---------|-----|
| 103 | Duplicate order ID | Restart framework (new session) |
| 201 | Order rejected | Check order parameters, market hours |
| 203 | Insufficient margin | Reduce position size or add funds |
| 110 | Price tick violation | Adjust price to valid tick increment |

### Common IB Error Codes

See `broker/ib/adapter.py` for the full classification:
- **Info codes** (2103, 2104, 2106, etc.): Harmless, logged at DEBUG
- **Connection codes** (502, 504, 1100, 1300): Logged at ERROR
- **Market data codes** (354, 10167): Logged at WARNING
- **Order codes** (103, 201, 203): Logged at WARNING
- **Permission codes** (326, 2100): Logged at ERROR

---

## Quick Reference

```bash
# Run all unit tests
make check

# Test IB connection
make test-connection

# Smoke test paper
make smoke-paper SYMBOL=BHP

# Test market data with 10s stream
make test-market-data SYMBOL=BHP

# Test reconnect handling
make simulate-disconnect SYMBOL=BHP

# Test state recovery
make simulate-restart

# Dry-run on live
IB_PORT=7496 make dry-run-live SYMBOL=BHP

# Test order placement (paper only)
make test-order SYMBOL=BHP

# Full paper trading
python -m asx_trading_framework.main --mode paper --config config/ibkr.yaml --symbols BHP

# Live trading (with all safety gates)
LIVE_TRADING_ENABLED=1 python -m asx_trading_framework.main \
    --mode live --confirm-live YES_I_UNDERSTAND \
    --config config/ibkr.yaml --symbols BHP --max-notional 5000
```
