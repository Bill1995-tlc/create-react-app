# GM Story Illustration + 65% Governance Control Panel

## Files
| File | Description |
|------|-------------|
| `gm_story_16x9.svg` | Primary editable illustration (16:9) |
| `gm_story_16x9.png` | 1920×1080 raster for Slack / deck |
| `gm_governance_to_65.csv` | Governance levers table (Google Sheets compatible) |
| `gm_story_builder.py` | Source script — re-run to regenerate |

## Chart Logic

### Panel 1 — Timeline: "What Changed When"
Plots the price-lift rollout detected from order-line data:
- **Quilt Covers** lifted 24–27 Sep 2025 (+18%)
- **Bedding Sets** lifted overnight 30 Sep → 1 Oct 2025 (+6%)
- **Quilt Inserts** lifted 1–5 Oct 2025 (+35%)
- **BFCM** promo period in Nov erased most of the lift
- **Jan 2026** saw discount codes return as the primary leakage channel

Price-lift detection method: modal "clean" line-item price (no discount,
no compare-at markup) compared pre (Jul–Sep) vs post (Oct).

### Panel 2 — Stacked Bar: "Where the Lift Went"
For lifted SKUs only, each month shows:
- **Red bar** = markdown / sale component of giveback ($k)
- **Amber bar** = discount code component of giveback ($k)
- Annotated with retention %, total giveback, and % units sold below
  the lifted base price.

### Panel 3 — P&L Tie-Out
Shows current blended GM% (58%), target (65%), gap in pp and $/mo,
and the US drag (≈$49k/mo, 2.3pp).

### Panel 4 — Governance Waterfall
Bridges from 58% → 63.2% via four lever groups:
1. Cap promo giveback (markdown + discount caps)
2. Price floor rule
3. Fix US drag
4. Lock freight / landed cost

Each lever shows $/mo GP impact and GM pp contribution.

### Governance CSV
The CSV contains one row per lever with: current value, target,
required delta, estimated impact ($/mo and pp), owner, and control
mechanism. Also includes per-category discount-cap headroom.

## How to Re-Run
```bash
pip install matplotlib pandas numpy
python3 gm_story_builder.py
```

## Assumptions
- "Customer-facing base price" = line-item price where discount = 0
  and compare_at_price is blank/0 or ≤ price.
- Order data shows *first observed new price purchase* as the lift date.
- P&L figures are approximate monthly run-rates from Xero exports.
- US drag isolated by geo-tagged order lines.
