#!/usr/bin/env python3
"""
GM Story — Clean 16:9 One-Page Illustration
============================================
Strict design rules: 12-col grid, min 16px body text, max 4 accent colors,
generous whitespace, no overlaps, no tiny tables.

Run:  python3 gm_story_clean_builder.py
"""

import csv
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.patheffects as pe
import numpy as np

OUT_DIR = Path(__file__).parent

# ─────────────────────────────────────────────────────────────────────────────
#  DESIGN TOKENS
# ─────────────────────────────────────────────────────────────────────────────
# Page: 1920×1080 at 100 dpi → 19.2×10.8 inches
W, H = 19.2, 10.8
DPI = 100
MARGIN = 80 / (W * DPI)  # 80px as fraction of figure width
MARGIN_Y = 80 / (H * DPI)

# Font sizes in *points* (matplotlib default unit).
# At 100 dpi, 1pt ≈ 1.333px.  So 16px ≈ 12pt, 22px ≈ 16.5pt, 44px ≈ 33pt.
FONT_TITLE    = 30      # ~40px
FONT_SUBTITLE = 11      # ~15px (subtitle only)
FONT_SECTION  = 16.5    # ~22px
FONT_BODY     = 12      # ~16px  (minimum for body)
FONT_BODY_LG  = 14      # ~19px
FONT_BIG_NUM  = 28      # ~37px
FONT_FOOTNOTE = 9       # ~12px

# Font family
FONT_FAMILY = "DejaVu Sans"

# 4-colour palette + neutrals
C_ACCENT  = "#2563EB"   # blue — lift events
C_RED     = "#DC2626"   # red — giveback / leakage
C_AMBER   = "#F59E0B"   # amber — discount codes (sub of red family)
C_GREEN   = "#059669"   # green — governance / target
C_BG      = "#FAFBFC"   # page background
C_CARD    = "#FFFFFF"   # card fill
C_TEXT    = "#1F2937"   # primary text
C_MUTED   = "#6B7280"   # secondary text
C_BORDER  = "#E5E7EB"   # lines / axes
C_LIGHT   = "#F3F4F6"   # alternate row


# ─────────────────────────────────────────────────────────────────────────────
#  DATA  (validated audit numbers — matches order-line detection)
# ─────────────────────────────────────────────────────────────────────────────

TIMELINE = [
    {"date": "24–27 Sep",  "cat": "Quilt Covers",  "pct": "+18%", "eg": "Q: $185 → $225", "color": C_ACCENT},
    {"date": "30 Sep–1 Oct","cat": "Bedding Sets",  "pct": "+6%",  "eg": "Q: $255 → $270", "color": C_ACCENT},
    {"date": "1–5 Oct",    "cat": "Quilt Inserts", "pct": "+35%", "eg": "LWI +$50 / MDI +$100", "color": C_ACCENT},
    {"date": "Nov (BFCM)", "cat": "Markdown Blowout","pct": "",   "eg": "$406k giveback",  "color": C_RED},
    {"date": "Jan 2026",   "cat": "Codes Return",  "pct": "",     "eg": "Discount > markdown","color": C_RED},
]

MONTHS = ["Oct", "Nov", "Dec", "Jan"]
GIVEBACK_MD_K  = [5, 350, 85, 35]
GIVEBACK_DC_K  = [3,  56, 32, 46]
RETENTION_PCT  = [91, -10, 45, 55]

CURRENT_GM = 58.0
TARGET_GM  = 65.0
MONTHLY_REV = 1_100_000
GAP_PP     = TARGET_GM - CURRENT_GM
GAP_DOLLAR = GAP_PP / 100 * MONTHLY_REV

LEVERS = [
    ("Cap markdown depth",          85_000, 2.6),
    ("Cap discount codes",          25_000, 0.8),  # included in line above for diagram
    ("Price floor rule",            20_000, 0.6),
    ("Fix US shipping drag",        34_000, 1.5),
    ("Lock freight / landed cost",  15_000, 0.5),
]

# For the diagram we combine the first two levers (both are "cap promo giveback")
LEVERS_DISPLAY = [
    ("Cap promo giveback (md + codes)", 85_000, 2.6, C_GREEN),
    ("Price floor rule",                20_000, 0.6, C_GREEN),
    ("Fix US shipping drag",            34_000, 1.5, C_GREEN),
    ("Lock freight / landed cost",      15_000, 0.5, C_GREEN),
]

GOVERNANCE_CSV_ROWS = [
    {
        "Lever": "Markdown depth cap (BAU)",
        "Current": "Uncapped (~30% BFCM)",
        "Target": "≤15% BAU / ≤25% promo",
        "Delta": "Cap depth",
        "$/mo GP Impact": 60_000,
        "GM pp Impact": 1.8,
        "Owner": "Ecom",
        "Control": "Shopify Script rule + weekly report",
        "Notes": "Biggest single lever; covers BFCM-style blowouts",
    },
    {
        "Lever": "Discount code cap",
        "Current": "Uncapped (stacking)",
        "Target": "≤10% per order, no stacking",
        "Delta": "Cap at 10%",
        "$/mo GP Impact": 25_000,
        "GM pp Impact": 0.8,
        "Owner": "Ecom / Marketing",
        "Control": "Shopify rule: single code, max 10%",
        "Notes": "Jan showed discount codes overtaking markdown as main leak",
    },
    {
        "Lever": "Price floor rule",
        "Current": "No floor",
        "Target": "Floor = pre-lift base unless clearance approved",
        "Delta": "Implement floor",
        "$/mo GP Impact": 20_000,
        "GM pp Impact": 0.6,
        "Owner": "Finance / Ecom",
        "Control": "CFO sign-off required for below-floor pricing",
        "Notes": "Prevents selling below old base price",
    },
    {
        "Lever": "US unit economics (shipping/fulfilment)",
        "Current": "~$49k/mo drag, ~2.3pp dilution",
        "Target": "Breakeven or ≤$15k drag",
        "Delta": "Reduce by ~$34k/mo",
        "$/mo GP Impact": 34_000,
        "GM pp Impact": 1.5,
        "Owner": "Ops / Finance",
        "Control": "Monthly P&L review; shipping cap at 12% of rev",
        "Notes": "US-tagged lines isolated by geo-tagged order data",
    },
    {
        "Lever": "Landed cost / freight governance",
        "Current": "Spot pricing, volatile",
        "Target": "Contracted rates, quarterly review",
        "Delta": "Lock rates",
        "$/mo GP Impact": 15_000,
        "GM pp Impact": 0.5,
        "Owner": "Ops",
        "Control": "Quarterly freight RFQ; variance report",
        "Notes": "Stabilise input costs to protect margin floor",
    },
    # Category-level discount caps
    {
        "Lever": "Category cap: Bedding Sets",
        "Current": "Lift headroom 6%",
        "Target": "Total md+disc ≤6%",
        "Delta": "Implement cap",
        "$/mo GP Impact": "See above",
        "GM pp Impact": "See above",
        "Owner": "Ecom / Finance",
        "Control": "Lift ~6%; any deeper discount erases it",
        "Notes": "Tightest cap — very thin lift margin",
    },
    {
        "Lever": "Category cap: Quilt Covers",
        "Current": "Lift headroom 18%",
        "Target": "Total md+disc ≤18%",
        "Delta": "Implement cap",
        "$/mo GP Impact": "See above",
        "GM pp Impact": "See above",
        "Owner": "Ecom / Finance",
        "Control": "18% markdown returns to old base price",
        "Notes": "",
    },
    {
        "Lever": "Category cap: Quilt Inserts (LWI)",
        "Current": "Lift headroom 25%",
        "Target": "Total md+disc ≤25%",
        "Delta": "Implement cap",
        "$/mo GP Impact": "See above",
        "GM pp Impact": "See above",
        "Owner": "Ecom / Finance",
        "Control": "+$50 on ~$200 = 25% headroom",
        "Notes": "",
    },
    {
        "Lever": "Category cap: Quilt Inserts (MDI)",
        "Current": "Lift headroom 35%",
        "Target": "Total md+disc ≤35%",
        "Delta": "Implement cap",
        "$/mo GP Impact": "See above",
        "GM pp Impact": "See above",
        "Owner": "Ecom / Finance",
        "Control": "+$100 on ~$285 = 35% headroom",
        "Notes": "",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
#  HELPER: coordinate converters (pixels → figure fraction)
# ─────────────────────────────────────────────────────────────────────────────

def px(pixels_x):
    """Convert pixel x-coordinate to figure fraction."""
    return pixels_x / (W * DPI)

def py(pixels_y):
    """Convert pixel y-coordinate to figure fraction (from top)."""
    return 1.0 - pixels_y / (H * DPI)


# ─────────────────────────────────────────────────────────────────────────────
#  BUILD
# ─────────────────────────────────────────────────────────────────────────────

def build():
    fig = plt.figure(figsize=(W, H), dpi=DPI, facecolor=C_BG)

    # ── TITLE BAR (y: 0–100px) ────────────────────────────────────────────
    fig.text(px(80), py(52), "GM Story — Price Lift → Leakage → Path to 65%",
             fontsize=FONT_TITLE, fontweight="bold", color=C_TEXT,
             fontfamily=FONT_FAMILY, va="center")
    fig.text(px(80), py(90),
             "TLC  ·  Order data Sep 2025 – Jan 2026  ·  Forensic margin analysis",
             fontsize=FONT_SUBTITLE, color=C_MUTED, fontfamily=FONT_FAMILY,
             va="center")

    # ── Divider line under title ──
    line_y = py(110)
    fig.add_artist(plt.Line2D([px(80), px(1840)], [line_y, line_y],
                              color=C_BORDER, linewidth=1.5,
                              transform=fig.transFigure, clip_on=False))

    # ══════════════════════════════════════════════════════════════════════
    #  SECTION 1 — TIMELINE  (y: 120–380px, full width)
    # ══════════════════════════════════════════════════════════════════════
    ax1 = fig.add_axes([px(80), py(380), px(1760), py(120) - py(380)])
    ax1.set_xlim(-0.3, 5.8)
    ax1.set_ylim(-1.0, 1.5)
    ax1.axis("off")

    ax1.text(-0.25, 1.35, "1   WHAT CHANGED WHEN",
             fontsize=FONT_SECTION, fontweight="bold", color=C_TEXT,
             fontfamily=FONT_FAMILY)

    # Backbone
    ax1.plot([-0.05, 5.55], [0, 0], color=C_BORDER, linewidth=5,
             solid_capstyle="round", zorder=1)

    for i, ev in enumerate(TIMELINE):
        x = i * 1.15 + 0.25
        col = ev["color"]

        # Dot
        ax1.plot(x, 0, "o", markersize=18, color=col, zorder=3)
        ax1.plot(x, 0, "o", markersize=9, color="white", zorder=4)

        # Percentage badge (above)
        if ev["pct"]:
            ax1.text(x, 0.65, ev["pct"],
                     ha="center", va="bottom",
                     fontsize=FONT_BIG_NUM, fontweight="bold", color=col,
                     fontfamily=FONT_FAMILY,
                     bbox=dict(boxstyle="round,pad=0.2", fc=col, alpha=0.10, ec="none"))

        # Category name (just above line)
        ax1.text(x, 0.22, ev["cat"],
                 ha="center", va="bottom",
                 fontsize=FONT_BODY, fontweight="bold", color=C_TEXT,
                 fontfamily=FONT_FAMILY)

        # Date (just below line)
        ax1.text(x, -0.18, ev["date"],
                 ha="center", va="top",
                 fontsize=FONT_BODY, color=col, fontweight="bold",
                 fontfamily=FONT_FAMILY)

        # Example (one line, below date)
        ax1.text(x, -0.48, ev["eg"],
                 ha="center", va="top",
                 fontsize=FONT_BODY, color=C_MUTED,
                 fontfamily=FONT_FAMILY)

    # ══════════════════════════════════════════════════════════════════════
    #  SECTION 2 — STACKED BAR (y: 400–760px, left 60%)
    # ══════════════════════════════════════════════════════════════════════
    bar_left   = px(80)
    bar_bottom = py(760)
    bar_width  = px(1040)   # ~60% of content area
    bar_height = py(420) - py(760)

    ax2 = fig.add_axes([bar_left, bar_bottom, bar_width, bar_height])

    ax2.text(0.0, 1.10, "2   WHERE THE LIFT WENT  (lifted SKUs, $k)",
             transform=ax2.transAxes,
             fontsize=FONT_SECTION, fontweight="bold", color=C_TEXT,
             fontfamily=FONT_FAMILY, va="top")

    x_pos = np.arange(len(MONTHS))
    bw = 0.45

    ax2.bar(x_pos, GIVEBACK_MD_K, bw, label="Markdown / Sale",
            color=C_RED, alpha=0.85, zorder=3, edgecolor="white", linewidth=0.5)
    ax2.bar(x_pos, GIVEBACK_DC_K, bw, bottom=GIVEBACK_MD_K,
            label="Discount Codes", color=C_AMBER, alpha=0.85, zorder=3,
            edgecolor="white", linewidth=0.5)

    ax2.set_xticks(x_pos)
    ax2.set_xticklabels(MONTHS, fontsize=FONT_BODY, fontweight="bold",
                        fontfamily=FONT_FAMILY)
    ax2.set_ylabel("Giveback $k", fontsize=FONT_BODY, color=C_MUTED,
                    fontfamily=FONT_FAMILY)
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)
    ax2.spines["left"].set_color(C_BORDER)
    ax2.spines["bottom"].set_color(C_BORDER)
    ax2.tick_params(colors=C_MUTED, labelsize=FONT_BODY)
    ax2.grid(axis="y", alpha=0.2, linestyle="--", color=C_MUTED)

    # Annotations: total + retention
    for i, (md, dc, ret) in enumerate(
        zip(GIVEBACK_MD_K, GIVEBACK_DC_K, RETENTION_PCT)
    ):
        total = md + dc
        # Total above bar
        ax2.text(i, total + 8, f"${total}k",
                 ha="center", fontsize=FONT_BODY, fontweight="bold",
                 color=C_TEXT, fontfamily=FONT_FAMILY)
        # Retention badge
        ret_col = C_GREEN if ret > 0 else C_RED
        ax2.text(i, total + 30, f"Ret: {ret}%",
                 ha="center", fontsize=FONT_BODY, fontweight="bold",
                 color=ret_col, fontfamily=FONT_FAMILY)

    max_bar = max(md + dc for md, dc in zip(GIVEBACK_MD_K, GIVEBACK_DC_K))
    ax2.set_ylim(-5, max_bar + 85)

    ax2.legend(loc="upper left", fontsize=FONT_BODY, framealpha=0.9,
               edgecolor=C_BORDER, prop={"family": FONT_FAMILY})

    # Callout box
    total_gb = sum(GIVEBACK_MD_K) + sum(GIVEBACK_DC_K)
    ax2.text(0.97, 0.97,
             f"Total Oct–Jan: ${total_gb}k\n~${total_gb // 4}k / mo avg",
             transform=ax2.transAxes, ha="right", va="top",
             fontsize=FONT_BODY, fontweight="bold", color=C_RED,
             fontfamily=FONT_FAMILY,
             bbox=dict(boxstyle="round,pad=0.4", fc="#FEF2F2",
                       ec="#FECACA", lw=1.2))

    # ══════════════════════════════════════════════════════════════════════
    #  SECTION 3 — PATH TO 65% (y: 400–760px, right 40%)
    # ══════════════════════════════════════════════════════════════════════
    sc_left   = px(1200)
    sc_bottom = py(760)
    sc_width  = px(640)
    sc_height = py(420) - py(760)

    ax3 = fig.add_axes([sc_left, sc_bottom, sc_width, sc_height])
    ax3.axis("off")

    ax3.text(0.0, 1.10, "3   PATH TO 65% GM",
             transform=ax3.transAxes,
             fontsize=FONT_SECTION, fontweight="bold", color=C_TEXT,
             fontfamily=FONT_FAMILY, va="top")

    # ── Big scoreboard numbers ──
    metrics = [
        ("Current", f"{CURRENT_GM:.0f}%", C_RED,   0.00),
        ("Target",  f"{TARGET_GM:.0f}%",  C_GREEN, 0.35),
        ("Gap",     f"{GAP_PP:.0f}pp",    C_TEXT,  0.70),
    ]
    for label, value, color, xoff in metrics:
        ax3.text(xoff, 0.95, label,
                 transform=ax3.transAxes, fontsize=FONT_BODY,
                 color=C_MUTED, fontfamily=FONT_FAMILY)
        ax3.text(xoff, 0.73, value,
                 transform=ax3.transAxes, fontsize=FONT_BIG_NUM,
                 fontweight="bold", color=color, fontfamily=FONT_FAMILY)

    ax3.text(0.00, 0.62,
             f"~${GAP_DOLLAR / 1000:.0f}k/mo GP gap  ·  US drag ~$49k/mo (2.3pp)",
             transform=ax3.transAxes, fontsize=FONT_BODY, color=C_MUTED,
             fontfamily=FONT_FAMILY)

    # ── Governance bridge (4 steps) ──
    ax3.text(0.00, 0.50, "Governance levers:",
             fontsize=FONT_BODY_LG, fontweight="bold", color=C_TEXT,
             fontfamily=FONT_FAMILY, transform=ax3.transAxes)

    cumulative = CURRENT_GM
    y = 0.42
    step = 0.085
    for label, dollars, pp, color in LEVERS_DISPLAY:
        cumulative += pp
        ax3.text(0.02, y, label,
                 fontsize=FONT_BODY, color=C_TEXT,
                 fontfamily=FONT_FAMILY, transform=ax3.transAxes)
        ax3.text(0.98, y, f"+${dollars // 1000}k/mo  (+{pp}pp)",
                 fontsize=FONT_BODY, fontweight="bold", color=color,
                 fontfamily=FONT_FAMILY, transform=ax3.transAxes,
                 ha="right")
        y -= step

    # Separator + total
    total_dollars = sum(d for _, d, _, _ in LEVERS_DISPLAY)
    total_pp = sum(p for _, _, p, _ in LEVERS_DISPLAY)
    result_gm = CURRENT_GM + total_pp

    ax3.plot([0.0, 1.0], [y + 0.03, y + 0.03], color=C_BORDER, lw=1.2,
             transform=ax3.transAxes, clip_on=False)
    ax3.text(0.02, y - 0.02,
             f"= +${total_dollars // 1000}k/mo  (+{total_pp:.1f}pp)  →  {result_gm:.1f}% GM",
             fontsize=FONT_BODY_LG, fontweight="bold", color=C_GREEN,
             fontfamily=FONT_FAMILY, transform=ax3.transAxes)

    remaining = TARGET_GM - result_gm
    if remaining > 0.05:
        ax3.text(0.02, y - 0.10,
                 f"Remaining {remaining:.1f}pp via pricing / mix actions.",
                 fontsize=FONT_BODY, color=C_MUTED, fontstyle="italic",
                 fontfamily=FONT_FAMILY, transform=ax3.transAxes)

    # ══════════════════════════════════════════════════════════════════════
    #  FOOTNOTE  (single line, bottom)
    # ══════════════════════════════════════════════════════════════════════
    fig.text(px(80), py(1050),
             "Order data shows first observed purchase at new price; "
             "admin change time may be earlier.  ·  Detail in gm_governance_to_65.csv",
             fontsize=FONT_FOOTNOTE, color=C_MUTED, fontfamily=FONT_FAMILY,
             va="center")

    return fig


# ─────────────────────────────────────────────────────────────────────────────
#  VERIFICATION: check no text smaller than ~16px (12pt) except footnote
# ─────────────────────────────────────────────────────────────────────────────

def verify_fonts(fig):
    """Check all text objects for minimum font size. Returns list of violations."""
    violations = []
    for ax in fig.get_axes():
        for txt in ax.texts:
            if txt.get_fontsize() < FONT_BODY - 0.5:
                violations.append(f"  WARN: '{txt.get_text()[:30]}…' is {txt.get_fontsize():.1f}pt")
    # Also check figure-level texts (skip footnote)
    for txt in fig.texts:
        if txt.get_fontsize() < FONT_FOOTNOTE - 0.5:
            violations.append(f"  WARN: fig text '{txt.get_text()[:30]}…' is {txt.get_fontsize():.1f}pt")
    return violations


# ─────────────────────────────────────────────────────────────────────────────
#  CSV
# ─────────────────────────────────────────────────────────────────────────────

def write_csv(path: Path):
    fieldnames = list(GOVERNANCE_CSV_ROWS[0].keys())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(GOVERNANCE_CSV_ROWS)


# ─────────────────────────────────────────────────────────────────────────────
#  README
# ─────────────────────────────────────────────────────────────────────────────

README = """\
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
"""


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("Building clean GM Story illustration …")

    fig = build()

    # Verify fonts
    violations = verify_fonts(fig)
    if violations:
        print("Font-size warnings:")
        for v in violations:
            print(v)
    else:
        print("  ✓ All body text ≥ 12pt (~16px)")

    svg_path = OUT_DIR / "gm_story_clean_16x9.svg"
    png_path = OUT_DIR / "gm_story_clean_16x9.png"
    csv_path = OUT_DIR / "gm_governance_to_65.csv"
    readme_path = OUT_DIR / "README_GM_STORY.md"

    fig.savefig(str(svg_path), format="svg", bbox_inches="tight")
    fig.savefig(str(png_path), format="png", dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  → {svg_path}")
    print(f"  → {png_path}")

    write_csv(csv_path)
    print(f"  → {csv_path}")

    with open(readme_path, "w") as f:
        f.write(README)
    print(f"  → {readme_path}")

    # Slack summary
    total_gb = sum(GIVEBACK_MD_K) + sum(GIVEBACK_DC_K)
    total_pp = sum(p for _, _, p, _ in LEVERS_DISPLAY)
    result_gm = CURRENT_GM + total_pp
    print(textwrap.dedent(f"""\
    ─── Slack Summary ───
    1. Price lifts Sep–Oct 2025: Covers +18%, Bedding +6%, Inserts +35%.
    2. BFCM in Nov wiped gains — $406k giveback. Jan: discount codes overtook markdown.
    3. Total giveback Oct–Jan: ~${total_gb}k ($153k/mo avg).
    4. GM sits at 58%. Target 65%. Gap = 7pp / ~$77k/mo.
    5. Governance levers recover ~$154k/mo → GM to ~{result_gm:.1f}%.
    6. Remaining {TARGET_GM - result_gm:.1f}pp requires pricing action or mix shift.
    """))


if __name__ == "__main__":
    main()
