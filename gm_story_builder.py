#!/usr/bin/env python3
"""
GM Story Illustration + 65% Governance Control Panel
=====================================================
Builds a single 16:9 page (SVG + PNG) telling the price-lift / leakage /
governance story, plus a governance CSV.

Data source: validated audit numbers from order-line analysis.
Run:  python3 gm_story_builder.py
"""

import csv
import os
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
import numpy as np

OUT_DIR = Path(__file__).parent

# ─────────────────────────────────────────────────────────────────────────────
# 1.  DATA  (validated audit targets from order-line detection)
# ─────────────────────────────────────────────────────────────────────────────

# -- Panel 1: Timeline of price lifts --
TIMELINE_EVENTS = [
    {
        "date": "24–27 Sep",
        "label": "Quilt Covers\nLifted",
        "pct": "+18%",
        "detail": "Q: 185→225  K: 195→235\nSK: 205→245  D: 170→205",
        "color": "#2563EB",
    },
    {
        "date": "30 Sep–1 Oct",
        "label": "Bedding Sets\nLifted",
        "pct": "+6%",
        "detail": "Q: 255→270  K: 275→290\nSK: 300→320  D: 230→250",
        "color": "#7C3AED",
    },
    {
        "date": "1–5 Oct",
        "label": "Quilt Inserts\nLifted",
        "pct": "+35%",
        "detail": "+$50 LWI  /  +$100 MDI",
        "color": "#059669",
    },
    {
        "date": "Nov (BFCM)",
        "label": "BFCM Promo\nMarkdown Blowout",
        "pct": "",
        "detail": "Giveback $406k\n(mostly markdown)",
        "color": "#DC2626",
    },
    {
        "date": "Jan 2026",
        "label": "Discount Codes\nReturn",
        "pct": "",
        "detail": "Giveback shifts from\nmarkdown → discount",
        "color": "#EA580C",
    },
]

# -- Panel 2: Monthly leakage breakdown (Oct-Jan, lifted SKUs only) --
MONTHS = ["Oct", "Nov", "Dec", "Jan"]
GIVEBACK_MARKDOWN_K = [5, 350, 85, 35]     # $k markdown component
GIVEBACK_DISCOUNT_K = [3, 56, 32, 46]      # $k discount component
RETENTION_PCT = [91, -10, 45, 55]           # % of lift retained
PCT_UNITS_BELOW_BASE = [9, 72, 42, 38]     # % units sold below lifted base

# -- Panel 3: P&L tie-out --
CURRENT_GM_PCT = 58.0
TARGET_GM_PCT = 65.0
MONTHLY_TRADING_INCOME = 1_100_000          # approx monthly run-rate $
GM_GAP_PP = TARGET_GM_PCT - CURRENT_GM_PCT  # 7 pp
GM_GAP_DOLLAR_MO = GM_GAP_PP / 100 * MONTHLY_TRADING_INCOME  # ~$77k/mo
US_DRAG_MO = 49_000                         # $/month
US_DRAG_PP = 2.3                            # pp

# -- Panel 4: Governance levers --
PROMO_GIVEBACK_MO = 153_000                 # current avg $/mo giveback
PROMO_TARGET_MO = 40_000                    # target $/mo giveback

GOVERNANCE_LEVERS = [
    {
        "lever": "Markdown depth cap (BAU)",
        "current": "Uncapped (~30% BFCM)",
        "target": "≤15% BAU / ≤25% promo",
        "delta": "Cap depth",
        "gp_impact_mo": 60_000,
        "gm_pp": 1.8,
        "owner": "Ecom",
        "control": "Shopify Script rule + weekly report",
    },
    {
        "lever": "Discount code cap",
        "current": "Uncapped (stacking)",
        "target": "≤10% per order",
        "delta": "Cap at 10%",
        "gp_impact_mo": 25_000,
        "gm_pp": 0.8,
        "owner": "Ecom / Marketing",
        "control": "Shopify rule: single code, max 10%",
    },
    {
        "lever": "Price floor rule (don't sell below old base)",
        "current": "No floor",
        "target": "Floor = pre-lift base unless clearance approved",
        "delta": "Implement floor",
        "gp_impact_mo": 20_000,
        "gm_pp": 0.6,
        "owner": "Finance / Ecom",
        "control": "Approval gate: CFO sign-off for below-floor",
    },
    {
        "lever": "US unit economics (shipping/fulfilment)",
        "current": "~$49k/mo drag, ~2.3pp dilution",
        "target": "Breakeven or ≤$15k drag",
        "delta": "Reduce by ~$34k/mo",
        "gp_impact_mo": 34_000,
        "gm_pp": 1.5,
        "owner": "Ops / Finance",
        "control": "Monthly P&L review; shipping % cap at 12% rev",
    },
    {
        "lever": "Landed cost / freight governance",
        "current": "Spot pricing, volatile",
        "target": "Contracted rates, quarterly review",
        "delta": "Lock rates",
        "gp_impact_mo": 15_000,
        "gm_pp": 0.5,
        "owner": "Ops",
        "control": "Quarterly freight RFQ; variance report",
    },
]

# Discount cap needed to preserve lift (category-level)
DISCOUNT_CAP_TABLE = [
    ("Bedding Sets", 6, "≤6%", "Lift is ~6%; any deeper discount erases it"),
    ("Quilt Covers", 18, "≤18%", "Lift is ~18%; 18% markdown returns to old base"),
    ("Quilt Inserts (LWI)", 25, "≤25%", "+$50 on ~$200 = 25% headroom"),
    ("Quilt Inserts (MDI)", 35, "≤35%", "+$100 on ~$285 = 35% headroom"),
]


# ─────────────────────────────────────────────────────────────────────────────
# 2.  BUILD THE ILLUSTRATION
# ─────────────────────────────────────────────────────────────────────────────

def build_illustration():
    fig = plt.figure(figsize=(19.2, 10.8), dpi=100, facecolor="white")

    # Title
    fig.suptitle(
        "GM Story: Price Lift  →  Leakage  →  Path to 65%",
        fontsize=24, fontweight="bold", color="#111827", y=0.98,
    )
    fig.text(
        0.5, 0.955,
        "TLC — Forensic margin analysis  |  Order data Sep 2025 – Jan 2026  |  Footnote: order data shows first observed new price purchase",
        ha="center", fontsize=9.5, color="#6B7280",
    )

    # 3-row layout: timeline | bar + P&L | governance
    gs = gridspec.GridSpec(
        3, 2, height_ratios=[0.9, 1.1, 0.9],
        left=0.04, right=0.97, top=0.93, bottom=0.03,
        hspace=0.42, wspace=0.22,
    )

    # ── Panel 1: Timeline (full width) ────────────────────────────────────
    ax1 = fig.add_subplot(gs[0, :])
    ax1.set_xlim(-0.3, 5.8)
    ax1.set_ylim(-1.1, 1.6)
    ax1.axis("off")
    ax1.set_title("PANEL 1 — What Changed When", fontsize=14, fontweight="bold",
                   loc="left", color="#374151", pad=8)

    # Timeline backbone
    ax1.plot([-0.1, 5.5], [0, 0], color="#D1D5DB", linewidth=4, zorder=1, solid_capstyle="round")

    for i, ev in enumerate(TIMELINE_EVENTS):
        x = i * 1.25 + 0.25
        col = ev["color"]
        # Marker dot
        ax1.plot(x, 0, "o", markersize=18, color=col, zorder=3)
        ax1.plot(x, 0, "o", markersize=10, color="white", zorder=4)
        # Date label below line
        ax1.text(x, -0.22, ev["date"], ha="center", va="top", fontsize=10,
                 fontweight="bold", color=col)
        # Event label above line
        ax1.text(x, 0.22, ev["label"], ha="center", va="bottom", fontsize=11,
                 fontweight="bold", color="#111827", linespacing=1.2)
        # Percentage badge (big)
        if ev["pct"]:
            ax1.text(x, 0.72, ev["pct"], ha="center", va="bottom", fontsize=20,
                     fontweight="bold", color=col,
                     bbox=dict(boxstyle="round,pad=0.25", fc=col, alpha=0.12, ec="none"))
        # Detail text
        ax1.text(x, -0.50, ev["detail"], ha="center", va="top", fontsize=8.5,
                 color="#4B5563", linespacing=1.3)

    # Cause-effect arrow: lifts → BFCM blowout
    ax1.annotate(
        "", xy=(3.90, 0.05), xytext=(2.95, 0.05),
        arrowprops=dict(arrowstyle="-|>", color="#DC2626", lw=2.5,
                        connectionstyle="arc3,rad=0.35"),
    )
    ax1.text(3.42, 0.17, "promo erased lift", ha="center", fontsize=8,
             color="#DC2626", fontstyle="italic", fontweight="bold")

    # ── Panel 2: Stacked bar — leakage (bottom-left of middle row) ───────
    ax2 = fig.add_subplot(gs[1, 0])
    ax2.set_title("PANEL 2 — Where the Lift Went (Lifted SKUs, $k)",
                   fontsize=12, fontweight="bold", loc="left", color="#374151")

    x_pos = np.arange(len(MONTHS))
    bar_w = 0.52

    bars_md = ax2.bar(x_pos, GIVEBACK_MARKDOWN_K, bar_w, label="Markdown / Sale",
                       color="#EF4444", alpha=0.88, zorder=3, edgecolor="white", linewidth=0.5)
    bars_dc = ax2.bar(x_pos, GIVEBACK_DISCOUNT_K, bar_w, bottom=GIVEBACK_MARKDOWN_K,
                       label="Discount Codes", color="#F59E0B", alpha=0.88, zorder=3,
                       edgecolor="white", linewidth=0.5)

    ax2.set_xticks(x_pos)
    ax2.set_xticklabels(MONTHS, fontsize=12, fontweight="bold")
    ax2.set_ylabel("Giveback $k", fontsize=11)
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)
    ax2.grid(axis="y", alpha=0.25, linestyle="--")

    for i, (md, dc, ret, pct_below) in enumerate(
        zip(GIVEBACK_MARKDOWN_K, GIVEBACK_DISCOUNT_K, RETENTION_PCT, PCT_UNITS_BELOW_BASE)
    ):
        total = md + dc
        ax2.text(i, total + 6, f"${total}k", ha="center", fontsize=10,
                 fontweight="bold", color="#111827")
        ret_color = "#059669" if ret > 0 else "#DC2626"
        ax2.text(i, total + 22, f"Ret: {ret}%", ha="center", fontsize=9.5,
                 fontweight="bold", color=ret_color,
                 bbox=dict(boxstyle="round,pad=0.15", fc=ret_color, alpha=0.08, ec="none"))
        ax2.text(i, -15, f"{pct_below}% below\nbase", ha="center", fontsize=8,
                 color="#6B7280")

    max_bar = max(sum(x) for x in zip(GIVEBACK_MARKDOWN_K, GIVEBACK_DISCOUNT_K))
    ax2.set_ylim(-25, max_bar + 55)
    ax2.legend(loc="upper left", fontsize=10, framealpha=0.9)

    total_giveback = sum(GIVEBACK_MARKDOWN_K) + sum(GIVEBACK_DISCOUNT_K)
    ax2.text(
        0.98, 0.96,
        f"Total giveback Oct–Jan: ${total_giveback}k\n(~${total_giveback // 4}k/mo avg)",
        transform=ax2.transAxes, ha="right", va="top", fontsize=11, fontweight="bold",
        color="#DC2626",
        bbox=dict(boxstyle="round,pad=0.4", fc="#FEF2F2", ec="#FECACA", lw=1.5),
    )

    # ── Panel 3: P&L summary + bridge (middle-right) ─────────────────────
    ax3 = fig.add_subplot(gs[1, 1])
    ax3.axis("off")
    ax3.set_title("PANEL 3 — So What: Margin Impact", fontsize=12,
                   fontweight="bold", loc="left", color="#374151")

    # Big numbers
    ax3.text(0.05, 0.92, "Current GM", transform=ax3.transAxes,
             fontsize=11, color="#6B7280")
    ax3.text(0.05, 0.78, f"{CURRENT_GM_PCT:.0f}%", transform=ax3.transAxes,
             fontsize=36, fontweight="bold", color="#DC2626")

    ax3.text(0.38, 0.92, "Target GM", transform=ax3.transAxes,
             fontsize=11, color="#6B7280")
    ax3.text(0.38, 0.78, f"{TARGET_GM_PCT:.0f}%", transform=ax3.transAxes,
             fontsize=36, fontweight="bold", color="#059669")

    ax3.text(0.72, 0.92, "Gap", transform=ax3.transAxes,
             fontsize=11, color="#6B7280")
    ax3.text(0.72, 0.78, f"{GM_GAP_PP:.0f}pp", transform=ax3.transAxes,
             fontsize=36, fontweight="bold", color="#111827")

    # Dollar gap
    ax3.text(0.05, 0.65, f"≈ ${GM_GAP_DOLLAR_MO / 1000:.0f}k/mo gross profit gap   |   "
             f"US drag: ~${US_DRAG_MO / 1000:.0f}k/mo ({US_DRAG_PP}pp)",
             transform=ax3.transAxes, fontsize=11, color="#374151")

    # Governance waterfall
    ax3.text(0.05, 0.52, "Governance Levers to Bridge the Gap:",
             fontsize=12, fontweight="bold", color="#111827", transform=ax3.transAxes)

    levers_display = [
        ("Cap promo giveback (md + disc)", f"+$85k/mo", f"+2.6pp", "#2563EB"),
        ("Price floor rule", f"+$20k/mo", f"+0.6pp", "#7C3AED"),
        ("Fix US drag (shipping/fulfilment)", f"+$34k/mo", f"+1.5pp", "#059669"),
        ("Lock freight / landed cost", f"+$15k/mo", f"+0.5pp", "#EA580C"),
    ]

    cumulative_pp = CURRENT_GM_PCT
    y = 0.43
    for label, dollar, pp_str, color in levers_display:
        pp_val = float(pp_str.replace("+", "").replace("pp", ""))
        cumulative_pp += pp_val
        ax3.text(0.06, y, f"▸ {label}", transform=ax3.transAxes,
                 fontsize=10, fontweight="bold", color=color)
        ax3.text(0.70, y, dollar, transform=ax3.transAxes,
                 fontsize=10, fontweight="bold", color=color, ha="right")
        ax3.text(0.82, y, pp_str, transform=ax3.transAxes,
                 fontsize=10, fontweight="bold", color=color, ha="right")
        ax3.text(0.97, y, f"→ {cumulative_pp:.1f}%", transform=ax3.transAxes,
                 fontsize=10, fontweight="bold", color="#374151", ha="right")
        y -= 0.065

    # Separator + total
    total_impact_mo = sum(l["gp_impact_mo"] for l in GOVERNANCE_LEVERS)
    total_pp = sum(l["gm_pp"] for l in GOVERNANCE_LEVERS)
    result_gm = CURRENT_GM_PCT + total_pp

    ax3.plot([0.05, 0.97], [y + 0.02, y + 0.02], color="#9CA3AF", lw=1.5,
             transform=ax3.transAxes, clip_on=False)
    ax3.text(0.06, y - 0.03, f"TOTAL:  +${total_impact_mo / 1000:.0f}k/mo   +{total_pp:.1f}pp",
             transform=ax3.transAxes, fontsize=13, fontweight="bold", color="#059669")
    ax3.text(0.97, y - 0.03, f"→ {result_gm:.1f}% GM",
             transform=ax3.transAxes, fontsize=13, fontweight="bold", color="#059669",
             ha="right",
             bbox=dict(boxstyle="round,pad=0.25", fc="#ECFDF5", ec="#6EE7B7", lw=1.5))

    # Remaining gap note
    remaining = TARGET_GM_PCT - result_gm
    if remaining > 0:
        ax3.text(0.06, y - 0.10,
                 f"Remaining {remaining:.1f}pp requires further pricing action or mix shift.",
                 transform=ax3.transAxes, fontsize=9.5, color="#6B7280", fontstyle="italic")

    # ── Panel 4: Governance control panel (full-width bottom row) ─────────
    ax4 = fig.add_subplot(gs[2, :])
    ax4.axis("off")
    ax4.set_title("PANEL 4 — Governance Control Panel: What Must Change to Hit 65%",
                   fontsize=14, fontweight="bold", loc="left", color="#374151", pad=6)

    # Draw as a table-like layout
    cols_x = [0.02, 0.22, 0.42, 0.58, 0.72, 0.85]
    headers = ["THRESHOLD", "CURRENT", "TARGET", "OWNER", "CONTROL", "CATEGORY CAP"]
    for cx, hdr in zip(cols_x, headers):
        ax4.text(cx, 0.88, hdr, transform=ax4.transAxes, fontsize=9,
                 fontweight="bold", color="#6B7280")

    # Separator line under headers
    ax4.plot([0.02, 0.98], [0.84, 0.84], color="#E5E7EB", lw=1.2,
             transform=ax4.transAxes, clip_on=False)

    thresholds = [
        ("Markdown depth", "Uncapped (~30% BFCM)", "≤15% BAU / ≤25% promo", "Ecom",
         "Shopify Script + weekly report", "Bedding: ≤6%  Covers: ≤18%"),
        ("Discount code", "Uncapped (stacking)", "≤10% per order, no stack", "Ecom / Mktg",
         "Shopify rule: single code", "Inserts LWI: ≤25%  MDI: ≤35%"),
        ("Price floor", "No floor", "≥ pre-lift base price", "Finance / Ecom",
         "CFO approval for below-floor", "Floor = old base unless clearance"),
        ("US shipping/fulfil", "~$49k drag, 2.3pp", "≤$15k drag / ≤12% rev", "Ops / Finance",
         "Monthly P&L review", "≤$18/order cap"),
        ("Freight / landed cost", "Spot rates, volatile", "Contracted, quarterly RFQ", "Ops",
         "Variance report + RFQ", "Lock rates quarterly"),
    ]

    y = 0.76
    row_colors = ["#F9FAFB", "#FFFFFF"]
    for idx, (thresh, cur, tgt, owner, ctrl, catcap) in enumerate(thresholds):
        bg = row_colors[idx % 2]
        # Row background
        ax4.axhspan(y - 0.06, y + 0.06, xmin=0.01, xmax=0.99, color=bg,
                     transform=ax4.transAxes, zorder=0)
        row_data = [thresh, cur, tgt, owner, ctrl, catcap]
        for cx, val in zip(cols_x, row_data):
            ax4.text(cx, y, val, transform=ax4.transAxes, fontsize=9,
                     color="#111827", va="center")
        y -= 0.145

    return fig


# ─────────────────────────────────────────────────────────────────────────────
# 3.  GOVERNANCE CSV
# ─────────────────────────────────────────────────────────────────────────────

def write_governance_csv(path: Path):
    rows = []
    for lev in GOVERNANCE_LEVERS:
        rows.append({
            "Lever": lev["lever"],
            "Current Value": lev["current"],
            "Target Value": lev["target"],
            "Required Delta": lev["delta"],
            "Est $/mo GP Impact": lev["gp_impact_mo"],
            "Est GM pp Impact": lev["gm_pp"],
            "Owner": lev["owner"],
            "Control Mechanism": lev["control"],
        })
    # Discount cap sub-table
    rows.append({
        "Lever": "--- Discount Cap by Category ---",
        "Current Value": "", "Target Value": "", "Required Delta": "",
        "Est $/mo GP Impact": "", "Est GM pp Impact": "", "Owner": "", "Control Mechanism": "",
    })
    for cat, headroom, cap, note in DISCOUNT_CAP_TABLE:
        rows.append({
            "Lever": f"  Max discount to preserve lift: {cat}",
            "Current Value": f"Lift headroom {headroom}%",
            "Target Value": f"Total md+disc {cap}",
            "Required Delta": "Implement cap",
            "Est $/mo GP Impact": "See above",
            "Est GM pp Impact": "See above",
            "Owner": "Ecom / Finance",
            "Control Mechanism": note,
        })

    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ─────────────────────────────────────────────────────────────────────────────
# 4.  README
# ─────────────────────────────────────────────────────────────────────────────

README_TEXT = """\
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
"""


# ─────────────────────────────────────────────────────────────────────────────
# 5.  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("Building GM Story illustration …")

    fig = build_illustration()

    svg_path = OUT_DIR / "gm_story_16x9.svg"
    png_path = OUT_DIR / "gm_story_16x9.png"
    csv_path = OUT_DIR / "gm_governance_to_65.csv"
    readme_path = OUT_DIR / "README_GM_STORY.md"

    fig.savefig(str(svg_path), format="svg", bbox_inches="tight")
    fig.savefig(str(png_path), format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    print(f"  ✓ {svg_path}")
    print(f"  ✓ {png_path}")

    write_governance_csv(csv_path)
    print(f"  ✓ {csv_path}")

    with open(readme_path, "w") as f:
        f.write(README_TEXT)
    print(f"  ✓ {readme_path}")

    # ── 10-line Slack narrative ──
    narrative = textwrap.dedent(f"""\
    ─── GM Story Summary (Slack) ───
    1. Price lifts rolled out late Sep–early Oct 2025: Quilt Covers +18%, Bedding Sets +6%, Quilt Inserts +35%.
    2. Oct retention was strong (91%), but BFCM in Nov wiped out gains — $406k giveback, mostly markdown.
    3. Dec–Jan giveback continued at ~$117k and $81k; Jan saw discount codes overtake markdown as the leak.
    4. Total giveback Oct–Jan on lifted SKUs: ~$612k ($153k/mo avg).
    5. Blended GM sits at ~58%; target is 65% — a 7pp / ~$77k/mo gap.
    6. US-tagged lines are a material drag: ~$49k/mo dilution (2.3pp).
    7. Governance fix #1: Cap markdown at ≤15% BAU / ≤25% promo + discount at ≤10% → recovers ~$85k/mo (+2.6pp).
    8. Governance fix #2: Price floor rule (never below old base without CFO approval) → +$20k/mo (+0.6pp).
    9. Governance fix #3: Fix US unit economics (shipping ≤12% rev) → +$34k/mo (+1.5pp).
    10. With all levers active: ~$154k/mo recovered → GM moves from 58% to ~63.2%. Remaining gap requires pricing action or mix shift.
    """)
    print(narrative)

    return {
        "svg": str(svg_path),
        "png": str(png_path),
        "csv": str(csv_path),
        "readme": str(readme_path),
    }


if __name__ == "__main__":
    main()
