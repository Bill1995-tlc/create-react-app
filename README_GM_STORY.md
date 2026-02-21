# GM Story — Price Lift, Leakage & Path to 65%

> One-page visual telling the full margin story for TLC.
> Order-line data: Sep 2025 – Jan 2026.

---

## Files

| File | What it is |
|------|-----------|
| `gm_story_16x9.svg` | Editable illustration (16:9) |
| `gm_story_16x9.png` | 1920×1080 raster for Slack / decks |
| `gm_governance_to_65.csv` | Governance levers table (Google Sheets ready) |
| `gm_story_builder.py` | Source script — re-run to regenerate everything |

---

## The 4 Panels

### Panel 1 — Timeline: What Changed When
Price lifts rolled out in three waves:
- **Quilt Covers** — 24–27 Sep 2025 (+18%)
- **Bedding Sets** — 30 Sep → 1 Oct 2025 (+6%)
- **Quilt Inserts** — 1–5 Oct 2025 (+35%)

Then the gains leaked away:
- **Nov BFCM** — promo markdowns erased most of the lift
- **Jan 2026** — discount codes became the main leakage channel

### Panel 2 — Bar Chart: Where the Lift Went
Monthly breakdown for lifted SKUs only:
- **Red** = markdown / sale giveback ($k)
- **Amber** = discount code giveback ($k)
- Each bar shows total $ giveback and % of lift retained

### Panel 3 — Margin Impact
- Current GM: **58%** → Target: **65%** → Gap: **7pp (~$77k/mo)**
- US operations drag: ~$49k/mo (2.3pp)
- Four governance levers bridge the gap to ~63.2%

### Panel 4 — Governance Control Panel
A table of the five levers needed to close the gap, showing:
current state, target, $/mo impact, pp impact, owner, and control mechanism.

Below the table: max discount thresholds per category to preserve the lift.

---

## How to Re-Run

```bash
pip install matplotlib numpy
python3 gm_story_builder.py
```

This regenerates the SVG, PNG, CSV, and this README.

---

## Key Assumptions
- **Base price** = line-item price where discount = 0 and compare_at_price is blank/0 or ≤ price
- **Lift date** = first observed purchase at the new price
- **P&L figures** = approximate monthly run-rates from Xero exports
- **US drag** = isolated by geo-tagged order lines
