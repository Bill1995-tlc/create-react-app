# Interactive Brokers Setup Guide — ASX Equities

This guide walks you through connecting the ASX trading framework to
Interactive Brokers for trading Australian equities in AUD.

## Prerequisites

- An Interactive Brokers account (paper or live)
- TWS (Trader Workstation) or IB Gateway installed
- Python 3.11+
- `ib_async` package

---

## Step 1: Install TWS or IB Gateway

Download from: https://www.interactivebrokers.com/en/trading/tws.php

**Which to use?**

| Feature | TWS | IB Gateway |
|---------|-----|------------|
| GUI | Full trading interface | Minimal login window |
| Resource usage | Heavy (~1GB RAM) | Light (~200MB RAM) |
| Auto-restart | Manual | Supports scheduled restart |
| Best for | Manual + automated | Headless/automated only |

For getting started: **use TWS** (easier to verify things are working).

For production/headless: **use IB Gateway**.

---

## Step 2: Enable API Connections in TWS

1. Open TWS and log in (paper account first!)
2. Go to **Edit → Global Configuration → API → Settings**
3. Enable these settings:

```
✅ Enable ActiveX and Socket Clients
✅ Read-Only API  (uncheck this later when you want to place orders)
✅ Download open orders on connection
✅ Include FX positions when calculating account values

Socket port: 7497  (paper trading default)
             7496  (live trading)

Trusted IPs: 127.0.0.1
```

4. Click **Apply** then **OK**
5. If using IB Gateway: ports are 4002 (paper) and 4001 (live)

---

## Step 3: Install Python Dependencies

```bash
# From the project root
pip install ib_async

# Or install the framework with IB extras
pip install -e ".[ib]"
```

---

## Step 4: Configure the Connection

Copy the example environment file:

```bash
cp .env.example .env
```

Edit `.env`:

```bash
IB_HOST=127.0.0.1
IB_PORT=7497          # 7497=TWS paper, 7496=TWS live, 4002=Gateway paper, 4001=Gateway live
IB_CLIENT_ID=1
IB_ACCOUNT=           # Leave empty to auto-detect
IB_MODE=paper
```

---

## Step 5: Test the Connection

```bash
# Test connectivity (recommended first step)
python -m asx_trading_framework.broker.ib test-connection
```

Expected output:
```
  IB Library:  ib_async
  IB Config: host=127.0.0.1, port=7497, client_id=1, mode=paper, ...

  STATUS: Connected successfully!
  Server: 163
  Accounts: ['DU1234567']
  ASX test: BHP resolved → conId=4815747

  All checks passed.
```

---

## Step 6: Try the CLI Commands

```bash
# View account summary
python -m asx_trading_framework.broker.ib account

# View positions
python -m asx_trading_framework.broker.ib positions

# Get a quote (requires ASX market data subscription)
python -m asx_trading_framework.broker.ib quote BHP

# Place a limit buy (paper account!)
python -m asx_trading_framework.broker.ib buy BHP --qty 10 --type limit --limit 40.00

# Place a market sell
python -m asx_trading_framework.broker.ib sell BHP --qty 10 --type market

# View open orders
python -m asx_trading_framework.broker.ib open-orders

# Cancel an order
python -m asx_trading_framework.broker.ib cancel 42
```

---

## Step 7: ASX Market Data Subscription

To get live ASX quotes, you need a market data subscription:

1. Log into **Account Management** (https://www.interactivebrokers.com.au)
2. Go to **Settings → User Settings → Market Data Subscriptions**
3. Subscribe to: **ASX Total (Australian Securities Exchange)**
4. Cost: approximately $6 AUD/month for non-professional

Without this subscription:
- Contract resolution still works
- Order placement still works
- But `quote` will return delayed data or fail

---

## Troubleshooting

### "Connection refused"

- TWS/Gateway is not running, or you're using the wrong port
- Paper trading: 7497 (TWS) or 4002 (Gateway)
- Live trading: 7496 (TWS) or 4001 (Gateway)

### "Duplicate client ID"

- Another application is connected with the same `IB_CLIENT_ID`
- Change `IB_CLIENT_ID` in your `.env` to a different number (e.g., 2)

### "Not authenticated" / "Client not subscribed"

- Check **Trusted IPs** includes `127.0.0.1` in TWS API settings
- If using a paper account, you may need to check "Allow connections from localhost"

### "No market data subscription"

- Subscribe to ASX data in Account Management
- Or use `IB_MODE=paper` where delayed data may be available

### "Contract not found" for ASX symbol

- Ensure you're using the correct ASX ticker (e.g., "BHP" not "BHP.AX")
- Some contracts may require specifying exchange explicitly

### Connection drops after a few seconds

- TWS has auto-logoff. Disable it:
  **Edit → Global Configuration → Lock and Exit → Never auto logoff**
- Or use IB Gateway with the `stable` channel for production

### Timeout on connect

- TWS may still be starting up — wait 30 seconds and retry
- Check if a firewall is blocking port 7497

---

## Paper Trading First!

**Always test with paper trading before going live.**

Paper trading provides:
- Simulated fills (not always realistic, but good for testing)
- Same API behaviour as live
- No real money at risk
- Reset account balance anytime in Account Management

Switching to live:
1. Change `IB_MODE=live` in `.env`
2. Change `IB_PORT=7496` (or 4001 for Gateway)
3. Log into TWS/Gateway with your live credentials
4. The CLI will prompt for confirmation before placing live orders

---

## Running Tests

```bash
# Unit tests (no IB connection needed)
python -m pytest asx_trading_framework/tests/test_ib_adapter.py -v

# Integration tests (requires TWS/Gateway running)
RUN_IB_INTEGRATION=1 python -m pytest asx_trading_framework/tests/test_ib_integration.py -v
```
