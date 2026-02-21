# GM Story — Price Lift, Leakage & Path to 65%

> One-page visual (16:9) for TLC.  Order-line data: Sep 2025 – Jan 2026.

---

## Files

| File | What it is |
|------|-----------|
| `gm_story_clean_16x9.svg` | Editable vector illustration |
| `gm_story_clean_16x9.png` | 1920×1080 raster (Slack / decks) |
| `gm_governance_to_65.csv` | Full governance levers + category caps (Sheets-ready) |
| `gm_story_clean_builder.py` | Source script — re-run to regenerate |

---

## How Numbers Were Computed

### Price Lift Detection (Panel 1)
- **Base price** = line-item price where `lineitem_discount = 0` and
  `compare_at_price` is blank/0 or ≤ price.
- **Pre-base** = modal clean price Jul 1 – Sep 30 2025.
- **Post-base** = modal clean price Oct 1 – Oct 31 2025.
- SKU is "lifted" if post-base > pre-base by ≥ $5 and ≥ 1%, with ≥ 10 clean
  lines in both periods.
- **Wave dates** = first observed purchase at new price, grouped by category.

### Leakage / Giveback (Panel 2)
- For lifted SKUs only, each month Oct 2025 – Jan 2026:
  - Theoretical lift $ = (new_base − old_base) × units
  - Giveback $ = (new_base − net_price) × units
  - Split: **Markdown** (price below base not explained by line discount)
    vs **Discount code** (`lineitem_discount` component)
  - Retention % = achieved_lift / theoretical_lift

### Path to 65% (Panel 3)
- Current GM% and monthly trading income from Xero P&L exports.
- Target GM = 65%.
- Governance levers and $ impacts estimated from order-line data +
  P&L run-rates.
- US drag isolated by geo-tagged order lines (~$49k/mo, 2.3pp).

---

## Data Sources
- Shopify order exports (order + line items)
- Master SKU list (supplier unit costs)
- Shopify COGS by order report
- Xero P&L export
- Xero Account Transactions export

---

## Validation Targets
These numbers should approximately match if using the same dataset:
- Quilt Covers lifted 24–27 Sep (+18%), Bedding Sets 30 Sep–1 Oct (+6%),
  Quilt Inserts 1–5 Oct (+35%)
- Total giveback Oct–Jan ≈ $612k (~$153k/mo)
- Current GM ~58%, gap to 65% = 7pp (~$77k/mo)

---

## How to Re-Run

```bash
pip install matplotlib numpy
python3 gm_story_clean_builder.py
```
