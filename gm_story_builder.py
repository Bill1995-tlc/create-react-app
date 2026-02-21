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
        "label": "Quilt Covers\nLifted +18%",
        "detail": "Q 185→225 · K 195→235\nSK 205→245 · D 170→205",
        "color": "#2563EB",
    },
    {
        "date": "30 Sep–1 Oct",
        "label": "Bedding Sets\nLifted +6%",
        "detail": "Q 255→270 · K 275→290\nSK 300→320 · D 230→250",
        "color": "#7C3AED",
    },
    {
        "date": "1–5 Oct",
        "label": "Quilt Inserts\nLifted +35%",
        "detail": "+$50 LWI  /  +$100 MDI",
        "color": "#059669",
    },
    {
        "date": "Nov (BFCM)",
        "label": "BFCM Promo\nErased Lift",
        "detail": "$406k giveback\n(mostly markdown)",
        "color": "#DC2626",
    },
    {
        "date": "Jan 2026",
        "label": "Discount Codes\nReturn",
        "detail": "Leakage shifts\nmarkdown → discount",
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
# 2.  COLOUR PALETTE  (consistent throughout)
# ─────────────────────────────────────────────────────────────────────────────

BG_COLOR     = "#FAFBFC"
CARD_BG      = "#FFFFFF"
TEXT_PRIMARY  = "#1F2937"
TEXT_SECONDARY = "#6B7280"
TEXT_MUTED    = "#9CA3AF"
BORDER_COLOR  = "#E5E7EB"
GREEN_ACCENT  = "#059669"
RED_ACCENT    = "#DC2626"
AMBER_ACCENT  = "#F59E0B"


# ─────────────────────────────────────────────────────────────────────────────
# 3.  HELPER — draw a rounded "card" background
# ─────────────────────────────────────────────────────────────────────────────

def _draw_card(fig, rect, radius=0.012):
    """Draw a white rounded-rect card with a subtle border."""
    x, y, w, h = rect
    patch = mpatches.FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad={radius}",
        facecolor=CARD_BG, edgecolor=BORDER_COLOR, linewidth=1.2,
        transform=fig.transFigure, zorder=0,
    )
    fig.patches.append(patch)


# ─────────────────────────────────────────────────────────────────────────────
# 4.  BUILD THE ILLUSTRATION
# ─────────────────────────────────────────────────────────────────────────────

def build_illustration():
    fig = plt.figure(figsize=(19.2, 10.8), dpi=100, facecolor=BG_COLOR)

    # ── Title bar ──────────────────────────────────────────────────────────
    fig.text(
        0.04, 0.97,
        "GM Story:  Price Lift  →  Leakage  →  Path to 65%",
        fontsize=22, fontweight="bold", color=TEXT_PRIMARY, va="top",
    )
    fig.text(
        0.04, 0.945,
        "TLC — Forensic margin analysis  ·  Order data Sep 2025 – Jan 2026",
        fontsize=10, color=TEXT_SECONDARY, va="top",
    )

    # Grid: 2 rows × 2 cols  (top row full-width timeline,
    #   middle = bar + P&L side-by-side, bottom = governance table)
    gs = gridspec.GridSpec(
        3, 2,
        height_ratios=[0.75, 1.0, 1.0],
        left=0.04, right=0.96, top=0.92, bottom=0.03,
        hspace=0.35, wspace=0.08,
    )

    # ══════════════════════════════════════════════════════════════════════
    #   PANEL 1 — TIMELINE  (full width, top row)
    # ══════════════════════════════════════════════════════════════════════
    ax1 = fig.add_subplot(gs[0, :])
    ax1.set_xlim(-0.5, 6.2)
    ax1.set_ylim(-0.9, 1.3)
    ax1.axis("off")
    ax1.text(0.0, 1.0, "1   WHAT CHANGED WHEN",
             transform=ax1.transAxes, fontsize=13, fontweight="bold",
             color=TEXT_PRIMARY, va="top")

    # Timeline backbone
    ax1.plot([-0.15, 5.9], [0, 0], color=BORDER_COLOR, linewidth=5,
             zorder=1, solid_capstyle="round")

    for i, ev in enumerate(TIMELINE_EVENTS):
        x = i * 1.2 + 0.3
        col = ev["color"]

        # Outer dot + inner white ring
        ax1.plot(x, 0, "o", markersize=16, color=col, zorder=3)
        ax1.plot(x, 0, "o", markersize=8, color="white", zorder=4)

        # Date below the line (small, muted)
        ax1.text(x, -0.18, ev["date"], ha="center", va="top",
                 fontsize=9.5, fontweight="bold", color=col)

        # Event label above the line (the key info)
        ax1.text(x, 0.20, ev["label"], ha="center", va="bottom",
                 fontsize=11.5, fontweight="bold", color=TEXT_PRIMARY,
                 linespacing=1.15)

        # Detail (smaller, below date)
        ax1.text(x, -0.42, ev["detail"], ha="center", va="top",
                 fontsize=8, color=TEXT_SECONDARY, linespacing=1.25)

    # Arrow connecting lifts → BFCM blowout
    ax1.annotate(
        "", xy=(3.85, 0.05), xytext=(2.95, 0.05),
        arrowprops=dict(arrowstyle="-|>", color=RED_ACCENT, lw=2,
                        connectionstyle="arc3,rad=0.3"),
    )
    ax1.text(3.40, 0.15, "promo erased lift", ha="center", fontsize=8,
             color=RED_ACCENT, fontstyle="italic", fontweight="bold")

    # ══════════════════════════════════════════════════════════════════════
    #   PANEL 2 — STACKED BAR  (middle-left)
    # ══════════════════════════════════════════════════════════════════════
    ax2 = fig.add_subplot(gs[1, 0])
    ax2.text(0.0, 1.12, "2   WHERE THE LIFT WENT  (lifted SKUs, $k)",
             transform=ax2.transAxes, fontsize=12, fontweight="bold",
             color=TEXT_PRIMARY, va="top")

    x_pos = np.arange(len(MONTHS))
    bar_w = 0.48

    ax2.bar(x_pos, GIVEBACK_MARKDOWN_K, bar_w,
            label="Markdown / Sale", color=RED_ACCENT, alpha=0.85,
            zorder=3, edgecolor="white", linewidth=0.5)
    ax2.bar(x_pos, GIVEBACK_DISCOUNT_K, bar_w,
            bottom=GIVEBACK_MARKDOWN_K, label="Discount Codes",
            color=AMBER_ACCENT, alpha=0.85, zorder=3,
            edgecolor="white", linewidth=0.5)

    ax2.set_xticks(x_pos)
    ax2.set_xticklabels(MONTHS, fontsize=11, fontweight="bold")
    ax2.set_ylabel("Giveback $k", fontsize=10, color=TEXT_SECONDARY)
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)
    ax2.spines["left"].set_color(BORDER_COLOR)
    ax2.spines["bottom"].set_color(BORDER_COLOR)
    ax2.tick_params(colors=TEXT_SECONDARY)
    ax2.grid(axis="y", alpha=0.2, linestyle="--", color=TEXT_MUTED)

    # Annotations — simplified: just total + retention badge
    for i, (md, dc, ret) in enumerate(
        zip(GIVEBACK_MARKDOWN_K, GIVEBACK_DISCOUNT_K, RETENTION_PCT)
    ):
        total = md + dc
        ax2.text(i, total + 8, f"${total}k",
                 ha="center", fontsize=10, fontweight="bold",
                 color=TEXT_PRIMARY)
        ret_color = GREEN_ACCENT if ret > 0 else RED_ACCENT
        ax2.text(i, total + 26, f"{ret}% retained",
                 ha="center", fontsize=8.5, fontweight="bold", color=ret_color)

    max_bar = max(sum(x) for x in zip(GIVEBACK_MARKDOWN_K, GIVEBACK_DISCOUNT_K))
    ax2.set_ylim(-5, max_bar + 60)
    ax2.legend(loc="upper left", fontsize=9, framealpha=0.9,
               edgecolor=BORDER_COLOR)

    # Callout box
    total_giveback = sum(GIVEBACK_MARKDOWN_K) + sum(GIVEBACK_DISCOUNT_K)
    ax2.text(
        0.98, 0.98,
        f"Total Oct–Jan: ${total_giveback}k\n~${total_giveback // 4}k / mo avg",
        transform=ax2.transAxes, ha="right", va="top",
        fontsize=10, fontweight="bold", color=RED_ACCENT,
        bbox=dict(boxstyle="round,pad=0.4", fc="#FEF2F2",
                  ec="#FECACA", lw=1.2),
    )

    # ══════════════════════════════════════════════════════════════════════
    #   PANEL 3 — P&L TIE-OUT  (middle-right)
    # ══════════════════════════════════════════════════════════════════════
    ax3 = fig.add_subplot(gs[1, 1])
    ax3.axis("off")
    ax3.text(0.0, 1.12, "3   MARGIN IMPACT",
             transform=ax3.transAxes, fontsize=12, fontweight="bold",
             color=TEXT_PRIMARY, va="top")

    # ── Big metric cards ──
    metrics = [
        ("Current GM", f"{CURRENT_GM_PCT:.0f}%", RED_ACCENT, 0.02),
        ("Target GM",  f"{TARGET_GM_PCT:.0f}%",  GREEN_ACCENT, 0.35),
        ("Gap",        f"{GM_GAP_PP:.0f}pp",     TEXT_PRIMARY,  0.68),
    ]
    for label, value, color, x_off in metrics:
        ax3.text(x_off, 0.95, label, transform=ax3.transAxes,
                 fontsize=10, color=TEXT_SECONDARY)
        ax3.text(x_off, 0.72, value, transform=ax3.transAxes,
                 fontsize=32, fontweight="bold", color=color)

    ax3.text(
        0.02, 0.60,
        f"~${GM_GAP_DOLLAR_MO / 1000:.0f}k/mo GP gap   |   "
        f"US drag ~${US_DRAG_MO / 1000:.0f}k/mo ({US_DRAG_PP}pp)",
        transform=ax3.transAxes, fontsize=10, color=TEXT_SECONDARY,
    )

    # ── Lever summary (clean list) ──
    ax3.text(0.02, 0.48, "Governance levers to bridge the gap:",
             fontsize=11, fontweight="bold", color=TEXT_PRIMARY,
             transform=ax3.transAxes)

    levers_display = [
        ("Cap promo giveback",       "+$85k/mo", "+2.6pp", "#2563EB"),
        ("Price floor rule",         "+$20k/mo", "+0.6pp", "#7C3AED"),
        ("Fix US drag",              "+$34k/mo", "+1.5pp", "#059669"),
        ("Lock freight/landed cost", "+$15k/mo", "+0.5pp", "#EA580C"),
    ]

    cumulative_pp = CURRENT_GM_PCT
    y = 0.39
    for label, dollar, pp_str, color in levers_display:
        pp_val = float(pp_str.replace("+", "").replace("pp", ""))
        cumulative_pp += pp_val
        ax3.text(0.04, y, f"●  {label}", transform=ax3.transAxes,
                 fontsize=10, color=color, fontweight="bold")
        ax3.text(0.62, y, dollar, transform=ax3.transAxes,
                 fontsize=10, color=color, fontweight="bold", ha="right")
        ax3.text(0.75, y, pp_str, transform=ax3.transAxes,
                 fontsize=10, color=color, fontweight="bold", ha="right")
        ax3.text(0.98, y, f"→ {cumulative_pp:.1f}%", transform=ax3.transAxes,
                 fontsize=10, color=TEXT_SECONDARY, ha="right")
        y -= 0.075

    # Totals line
    total_impact_mo = sum(l["gp_impact_mo"] for l in GOVERNANCE_LEVERS)
    total_pp = sum(l["gm_pp"] for l in GOVERNANCE_LEVERS)
    result_gm = CURRENT_GM_PCT + total_pp

    ax3.plot([0.02, 0.98], [y + 0.025, y + 0.025], color=BORDER_COLOR,
             lw=1.2, transform=ax3.transAxes, clip_on=False)

    ax3.text(0.04, y - 0.03,
             f"TOTAL  +${total_impact_mo / 1000:.0f}k/mo  /  +{total_pp:.1f}pp",
             transform=ax3.transAxes, fontsize=12, fontweight="bold",
             color=GREEN_ACCENT)
    ax3.text(0.98, y - 0.03, f"→  {result_gm:.1f}% GM",
             transform=ax3.transAxes, fontsize=12, fontweight="bold",
             color=GREEN_ACCENT, ha="right",
             bbox=dict(boxstyle="round,pad=0.25", fc="#ECFDF5",
                       ec="#6EE7B7", lw=1.5))

    remaining = TARGET_GM_PCT - result_gm
    if remaining > 0:
        ax3.text(0.04, y - 0.11,
                 f"Remaining {remaining:.1f}pp via further pricing or mix shift.",
                 transform=ax3.transAxes, fontsize=9, color=TEXT_MUTED,
                 fontstyle="italic")

    # ══════════════════════════════════════════════════════════════════════
    #   PANEL 4 — GOVERNANCE TABLE  (full-width bottom row)
    #   Using matplotlib table for clean, aligned rendering
    # ══════════════════════════════════════════════════════════════════════
    ax4 = fig.add_subplot(gs[2, :])
    ax4.axis("off")
    ax4.text(0.0, 1.08,
             "4   GOVERNANCE CONTROL PANEL — What Must Change to Hit 65%",
             transform=ax4.transAxes, fontsize=13, fontweight="bold",
             color=TEXT_PRIMARY, va="top")

    # Build table data
    col_labels = ["Lever", "Current", "Target", "$/mo Impact", "pp", "Owner", "Control"]
    table_data = []
    lever_colors = ["#2563EB", "#2563EB", "#7C3AED", "#059669", "#EA580C"]
    for lev in GOVERNANCE_LEVERS:
        table_data.append([
            lev["lever"].split("(")[0].strip(),  # shorter name
            lev["current"],
            lev["target"],
            f"+${lev['gp_impact_mo'] // 1000}k",
            f"+{lev['gm_pp']}",
            lev["owner"],
            lev["control"],
        ])

    the_table = ax4.table(
        cellText=table_data,
        colLabels=col_labels,
        cellLoc="left",
        loc="upper center",
        colWidths=[0.16, 0.15, 0.18, 0.07, 0.05, 0.10, 0.24],
    )
    the_table.auto_set_font_size(False)
    the_table.set_fontsize(9)
    the_table.scale(1.0, 1.6)

    # Style the table
    for (row, col), cell in the_table.get_celld().items():
        cell.set_edgecolor(BORDER_COLOR)
        cell.set_linewidth(0.8)
        if row == 0:
            # Header row
            cell.set_facecolor("#F3F4F6")
            cell.set_text_props(fontweight="bold", color=TEXT_PRIMARY, fontsize=9)
            cell.set_height(0.08)
        else:
            # Data rows — alternate shading
            cell.set_facecolor("#FAFBFC" if row % 2 == 0 else CARD_BG)
            cell.set_text_props(color=TEXT_PRIMARY, fontsize=8.5)

    # Category discount cap note below the table
    cap_parts = [f"{cat}: {cap}" for cat, _, cap, _ in DISCOUNT_CAP_TABLE]
    ax4.text(0.0, -0.08,
             "Max discount to preserve lift   ·   " + "   |   ".join(cap_parts),
             transform=ax4.transAxes, fontsize=8.5, color=TEXT_SECONDARY,
             fontstyle="italic")

    return fig


# ─────────────────────────────────────────────────────────────────────────────
# 5.  GOVERNANCE CSV
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
        "Est $/mo GP Impact": "", "Est GM pp Impact": "", "Owner": "",
        "Control Mechanism": "",
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
# 6.  README
# ─────────────────────────────────────────────────────────────────────────────

README_TEXT = """\
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
"""


# ─────────────────────────────────────────────────────────────────────────────
# 7.  MAIN
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
    print(f"  Done → {svg_path}")
    print(f"  Done → {png_path}")

    write_governance_csv(csv_path)
    print(f"  Done → {csv_path}")

    with open(readme_path, "w") as f:
        f.write(README_TEXT)
    print(f"  Done → {readme_path}")

    # ── Quick Slack summary ──
    narrative = textwrap.dedent(f"""\
    ─── GM Story Summary ───
     1. Price lifts: Quilt Covers +18%, Bedding Sets +6%, Quilt Inserts +35% (Sep–Oct 2025).
     2. Oct retained 91% of the lift.  BFCM in Nov wiped it out — $406k giveback.
     3. Dec–Jan leaked ~$198k more.  Jan: discount codes overtook markdown as the main leak.
     4. Total giveback Oct–Jan: ~$612k ($153k/mo avg).
     5. GM sits at 58%.  Target 65%.  Gap = 7pp / ~$77k/mo.
     6. US drag alone = ~$49k/mo (2.3pp).
     7. Governance levers recover ~$154k/mo → GM moves to ~63.2%.
     8. Remaining 1.8pp gap requires pricing action or mix shift.
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
