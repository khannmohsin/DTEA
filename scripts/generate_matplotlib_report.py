#!/usr/bin/env python3
"""Generate publication-quality matplotlib charts from BlockCap results artifacts."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.ticker import FuncFormatter


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "results"
PLOTS_DIR = RESULTS_DIR / "plots"
TABLES_DIR = RESULTS_DIR / "tables"

# Tiers that have a blockchain client — endpoint is excluded from aggregation
BLOCKCHAIN_TIERS = ["cloud", "fog", "edge"]

CONDITION_COLORS = {"cold": "#d56f3e", "warm": "#3f7d58", "concurrent": "#3f7d58"}
PUBLICATION_DPI = 300          # print-quality for paper
COL_W = 3.5                    # single-column width (inches) for double-column template

# Consistent operation display names
OP_LABELS = {
    "issue":              "Issue Token",
    "issue-delegable":    "Issue Delegable",
    "delegate":           "Delegate Token",
    "revoke":             "Revoke Token",
    "revoke_propagation": "Revoke Propagation",
    "check":              "Check Grant",
    "expiry-check":       "Expiry Check",
    "ensure-policy":      "Ensure Policy",
    "register":           "Register Node",
    "validator-promotion":"Validator Promotion",
}

OP_ALIAS = {
    "issuetoken":            "issue",
    "issuetokendelegable":   "issue-delegable",
    "delegatetoken":         "delegate",
    "revoketoken":           "revoke",
    "revoketokenpropagation":"revoke_propagation",
    "expirycheck":           "expiry-check",
    "checkgrant":            "check",
    "registernode":          "register",
    "validatorpromotion":    "validator-promotion",
    "ensurepolicy":          "ensure-policy",
}


def apply_publication_theme() -> None:
    plt.style.use("default")
    matplotlib.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": "#334155",
            "axes.labelcolor": "#0f172a",
            "axes.titlesize": 7.5,
            "axes.titleweight": "semibold",
            "axes.labelsize": 7,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "xtick.color": "#334155",
            "ytick.color": "#334155",
            "xtick.labelsize": 6.5,
            "ytick.labelsize": 6.5,
            "grid.color": "#cbd5e1",
            "grid.alpha": 0.5,
            "grid.linewidth": 0.6,
            "legend.frameon": False,
            "legend.fontsize": 6,
            "font.size": 7,
            "savefig.facecolor": "white",
            "savefig.bbox": "tight",
        }
    )


def ms_label(value: float) -> str:
    if value >= 1000.0:
        return f"{value / 1000.0:.2f}s"
    if value >= 100.0:
        return f"{value:.0f}ms"
    return f"{value:.1f}ms"


def human_latency_axis(ax) -> None:
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: ms_label(float(v))))


def load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def float_or_none(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except Exception:
        return None


def normalize_op(raw_op: str) -> str | None:
    return OP_ALIAS.get(raw_op.lower().strip())


def save_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in fieldnames})


def collect_lifecycle_rows(experimental: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect lifecycle rows from both sources, keeping one row per (tier, operation, condition).
    When duplicates exist, prefer the row with the highest sample count (most representative)."""
    candidates: dict[tuple[str, str, str], dict[str, Any]] = {}

    def _upsert(row: dict[str, Any]) -> None:
        tier = str(row.get("tier", "")).lower()
        op   = str(row.get("operation", ""))
        cond = str(row.get("condition", "")).lower()
        key  = (tier, op, cond)
        existing = candidates.get(key)
        count = int(row.get("count") or 0)
        if existing is None or count > int(existing.get("count") or 0):
            candidates[key] = row

    for row in (experimental.get("token_lifecycle_latency") or []):
        _upsert(row)

    for tier, summary in (experimental.get("internal_latency_summary") or {}).items():
        for key, record in sorted((summary or {}).items()):
            row = {
                "tier": tier,
                "operation": record.get("operation"),
                "condition": record.get("condition"),
                "mean_latency_ms": record.get("mean_ms"),
                "stddev_latency_ms": record.get("stddev_ms"),
                "min_latency_ms": record.get("min_ms"),
                "max_latency_ms": record.get("max_ms"),
                "count": record.get("count"),
                "source_key": key,
            }
            _upsert(row)

    return list(candidates.values())


def _agg_op_condition(rows: list[dict[str, Any]], op: str, condition: str,
                      tiers: list[str] = BLOCKCHAIN_TIERS) -> tuple[float, float, float, int]:
    """Aggregate across tiers for a given op+condition.
    Returns (mean, lo_err, hi_err, count) where lo/hi are min/max based error bars.
    lo_err = mean - global_min, hi_err = global_max - mean (always non-negative, safe on log scale)."""
    means, counts, mins, maxs = [], [], [], []
    for row in rows:
        if normalize_op(str(row.get("operation", ""))) != op:
            continue
        cond = str(row.get("condition", "")).lower()
        if cond not in (condition, "concurrent" if condition == "warm" else ""):
            if not (condition == "warm" and cond == "concurrent"):
                continue
        tier = str(row.get("tier", "")).lower()
        if tier not in tiers:
            continue
        m = float_or_none(row.get("mean_latency_ms"))
        c = int(row.get("count") or 1)
        if m is None:
            continue
        means.append(m)
        counts.append(c)
        mn = float_or_none(row.get("min_latency_ms"))
        mx = float_or_none(row.get("max_latency_ms"))
        mins.append(mn if mn is not None else m)
        maxs.append(mx if mx is not None else m)
    if not means:
        return 0.0, 0.0, 0.0, 0
    mean = sum(m * c for m, c in zip(means, counts)) / sum(counts)
    global_min = min(mins)
    global_max = max(maxs)
    lo_err = max(0.0, mean - global_min)
    hi_err = max(0.0, global_max - mean)
    return mean, lo_err, hi_err, sum(counts)


def _bar_label(ax, bar, value: float, err_hi: float, ymax: float, offset_frac: float = 0.03) -> None:
    if value <= 0:
        return
    offset = max(ymax * offset_frac, 8.0)
    ax.text(
        bar.get_x() + bar.get_width() / 2,
        value + err_hi + offset,
        ms_label(value),
        ha="center", va="bottom", fontsize=8,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Figure 1 — Access Latency (aggregated across cloud+fog+edge)
# ─────────────────────────────────────────────────────────────────────────────
def render_figure1_access_latency(plot_dir: Path, rows: list[dict[str, Any]]) -> None:
    # Write ops: both cold and warm go on-chain — show single averaged bar (warm preferred, higher n)
    write_ops = [
        ("issue",              "Issue\nToken"),
        ("issue-delegable",    "Issue\nDelegable"),
        ("delegate",           "Delegate\nToken"),
        ("revoke",             "Revoke\nToken"),
        ("revoke_propagation", "Revoke\nPropagation"),
    ]
    # Read ops: warm = cache hit, cold = cache miss — cold/warm distinction is meaningful
    read_ops = [
        ("check",        "Check Grant"),
        ("expiry-check", "Expiry Check"),
    ]

    err_kw = {"elinewidth": 0.8, "ecolor": "#444", "capsize": 2}
    fig, (ax_w, ax_r) = plt.subplots(
        2, 1,
        figsize=(COL_W, 4.8),
    )
    fig.suptitle("Access Control Latency  (Cloud + Fog + Edge aggregated)", fontsize=7.5, fontweight="semibold")

    # ── Left panel: write operations (averaged across conditions, warm preferred) ──
    w_labels, w_vals, w_lo, w_hi = [], [], [], []
    for op, label in write_ops:
        wm, wlo, whi, wc = _agg_op_condition(rows, op, "warm")
        cm, clo, chi, cc = _agg_op_condition(rows, op, "cold")
        if wc >= cc and wm > 0:
            m, lo, hi = wm, wlo, whi
        elif cm > 0:
            m, lo, hi = cm, clo, chi
        else:
            m, lo, hi = 0.0, 0.0, 0.0
        w_labels.append(label)
        w_vals.append(m)
        w_lo.append(lo)
        w_hi.append(hi)

    x_w = list(range(len(write_ops)))
    bars_w = ax_w.bar(
        x_w, w_vals, color="#205781", alpha=0.88,
        yerr=[w_lo, w_hi], error_kw=err_kw,
    )
    ax_w.set_xticks(x_w)
    ax_w.set_xticklabels(w_labels, ha="center")
    ax_w.set_ylabel("Latency")
    ax_w.set_title("Write Operations (on-chain tx each call)", fontsize=7)
    human_latency_axis(ax_w)
    ax_w.grid(axis="y", alpha=0.35)
    ymax_w = max(w_vals) if w_vals else 1.0
    ax_w.set_ylim(0, ymax_w * 1.3)
    offset_w = max(ymax_w * 0.02, 8.0)
    for bar, val, hi in zip(bars_w, w_vals, w_hi):
        if val > 0:
            ax_w.text(bar.get_x() + bar.get_width() / 2, val + hi + offset_w,
                      ms_label(val), ha="center", va="bottom", fontsize=5.5)

    # ── Right panel: read operations (cold vs warm) ──
    r_labels = [label for _, label in read_ops]
    r_cold_vals, r_cold_lo, r_cold_hi = [], [], []
    r_warm_vals, r_warm_lo, r_warm_hi = [], [], []
    for op, _ in read_ops:
        cm, clo, chi, _ = _agg_op_condition(rows, op, "cold")
        wm, wlo, whi, _ = _agg_op_condition(rows, op, "warm")
        r_cold_vals.append(cm); r_cold_lo.append(clo); r_cold_hi.append(chi)
        r_warm_vals.append(wm); r_warm_lo.append(wlo); r_warm_hi.append(whi)

    x_r = list(range(len(read_ops)))
    width = 0.35
    cold_bars_r = ax_r.bar(
        [i - width / 2 for i in x_r], r_cold_vals, width=width,
        label="Cold (cache miss)", color=CONDITION_COLORS["cold"],
        yerr=[r_cold_lo, r_cold_hi], error_kw=err_kw,
    )
    warm_bars_r = ax_r.bar(
        [i + width / 2 for i in x_r], r_warm_vals, width=width,
        label="Warm (cache hit)", color=CONDITION_COLORS["warm"],
        yerr=[r_warm_lo, r_warm_hi], error_kw=err_kw,
    )
    ax_r.set_xticks(x_r)
    ax_r.set_xticklabels(r_labels, ha="center")
    ax_r.set_title("Read Operations (cold = cache miss, warm = cache hit)", fontsize=7)
    human_latency_axis(ax_r)
    ax_r.legend(loc="upper right", fontsize=6)
    ax_r.grid(axis="y", alpha=0.35)
    ymax_r = max(r_cold_vals + r_warm_vals) if (r_cold_vals or r_warm_vals) else 1.0
    ax_r.set_ylim(0, ymax_r * 1.5)
    offset_r = max(ymax_r * 0.03, 0.5)
    for bar, val, hi in zip(cold_bars_r, r_cold_vals, r_cold_hi):
        if val > 0:
            ax_r.text(bar.get_x() + bar.get_width() / 2, val + hi + offset_r,
                      ms_label(val), ha="center", va="bottom", fontsize=5.5)
    for bar, val, hi in zip(warm_bars_r, r_warm_vals, r_warm_hi):
        if val > 0:
            ax_r.text(bar.get_x() + bar.get_width() / 2, val + hi + offset_r,
                      ms_label(val), ha="center", va="bottom", fontsize=5.5)

    fig.tight_layout()
    fig.savefig(plot_dir / "figure_01_access_latency.pdf", dpi=PUBLICATION_DPI)
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# Figure 2 — Registration Latency
# ─────────────────────────────────────────────────────────────────────────────
def render_figure2_registration_latency(
    plot_dir: Path,
    topology_rows: list[dict[str, Any]],
    promotion_rows: list[dict[str, Any]],
) -> None:
    # Collect non-validator registrations (edge, endpoint) and validator (fog)
    non_validator: list[float] = []
    validator_reg: list[float] = []
    promotion: list[float] = []

    MAX_REG_MS = 10_000  # values above this are timeouts, not real registration latency
    for row in topology_rows:
        if str(row.get("status", "")).lower() == "already_registered":
            continue
        m = float_or_none(row.get("mean_latency_ms"))
        if m is None or m > MAX_REG_MS:
            continue
        if row.get("wants_validator"):
            validator_reg.append(m)
        else:
            non_validator.append(m)

    for row in promotion_rows:
        m = float_or_none(row.get("mean_latency_ms"))
        if m is not None:
            promotion.append(m)

    bars_data = [
        ("Non-Validator\nRegistration", non_validator, "#4f6d3a"),
        ("Validator\nRegistration",    validator_reg,  "#205781"),
        ("Validator\nPromotion",       promotion,      "#8a5a8c"),
    ]

    labels, values, errors, colors = [], [], [], []
    for label, data, color in bars_data:
        if not data:
            continue
        mean = sum(data) / len(data)
        std = (sum((x - mean) ** 2 for x in data) / len(data)) ** 0.5 if len(data) > 1 else 0.0
        labels.append(label)
        values.append(mean)
        errors.append(std)
        colors.append(color)

    if not values:
        return

    errs_lo = [min(e, v) for e, v in zip(errors, values)]
    fig, ax = plt.subplots(figsize=(COL_W, 2.8))
    bars = ax.bar(
        labels, values, color=colors,
        yerr=[errs_lo, errors], capsize=5,
        error_kw={"elinewidth": 1.4, "ecolor": "#444"},
    )
    ax.set_title("Registration Latency")
    ax.set_ylabel("Latency")
    human_latency_axis(ax)
    ax.grid(axis="y", alpha=0.35)

    ymax = max(values)
    ax.set_ylim(0, ymax * 1.3)
    for bar, val, err in zip(bars, values, errors):
        _bar_label(ax, bar, val, err, ymax)

    fig.tight_layout()
    fig.savefig(plot_dir / "figure_02_registration_latency.pdf", dpi=PUBLICATION_DPI)
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# Figure 3 — Policy Latency (creation, lookup, propagation)
# ─────────────────────────────────────────────────────────────────────────────
def render_figure3_policy_latency(plot_dir: Path, rows: list[dict[str, Any]]) -> None:
    # ensurePolicy: both cold and warm hit the chain (warm just skips policy creation,
    # but still verifies on-chain). Use warm (higher n, more representative).
    ep_warm_m, ep_warm_lo, ep_warm_hi, ep_warm_c = _agg_op_condition(rows, "ensure-policy", "warm")
    ep_cold_m, ep_cold_lo, ep_cold_hi, ep_cold_c = _agg_op_condition(rows, "ensure-policy", "cold")
    if ep_warm_c >= ep_cold_c and ep_warm_m > 0:
        ep_m, ep_lo, ep_hi = ep_warm_m, ep_warm_lo, ep_warm_hi
    else:
        ep_m, ep_lo, ep_hi = ep_cold_m, ep_cold_lo, ep_cold_hi

    prop_m, prop_lo, prop_hi, _ = _agg_op_condition(rows, "revoke_propagation", "warm")

    entries = [
        ("Policy\nEnforcement", ep_m,   ep_lo,   ep_hi,   "#205781"),
        ("Revoke\nPropagation", prop_m, prop_lo, prop_hi, "#c17037"),
    ]

    labels = [e[0] for e in entries if e[1] > 0]
    values = [e[1] for e in entries if e[1] > 0]
    lo_errs = [e[2] for e in entries if e[1] > 0]
    hi_errs = [e[3] for e in entries if e[1] > 0]
    colors  = [e[4] for e in entries if e[1] > 0]

    if not values:
        return

    fig, ax = plt.subplots(figsize=(COL_W, 2.8))
    bars = ax.bar(
        labels, values, color=colors,
        yerr=[lo_errs, hi_errs], capsize=5,
        error_kw={"elinewidth": 1.4, "ecolor": "#444"},
        width=0.45,
    )
    ax.set_title("Policy Enforcement & Revocation Propagation Latency\n(Cloud + Fog + Edge aggregated)")
    ax.set_ylabel("Latency")
    human_latency_axis(ax)
    ax.grid(axis="y", alpha=0.35)

    ymax = max(values)
    ax.set_ylim(0, ymax * 1.3)
    for bar, val, hi in zip(bars, values, hi_errs):
        _bar_label(ax, bar, val, hi, ymax)

    fig.tight_layout()
    fig.savefig(plot_dir / "figure_03_policy_latency.pdf", dpi=PUBLICATION_DPI)
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# Figure 4 — Throughput, Latency & Throttling Under Load
# ─────────────────────────────────────────────────────────────────────────────
def render_figure4_load(plot_dir: Path, load_tests: dict[str, dict[str, Any]]) -> None:
    if not load_tests:
        return
    keys = sorted(load_tests.keys(), key=lambda x: int(x))
    conc = [int(k) for k in keys]

    success_count      = [int(load_tests[k].get("success_count") or 0) for k in keys]
    duration_s         = [float_or_none(load_tests[k].get("duration_seconds")) or 1.0 for k in keys]
    success_throughput = [sc / dur for sc, dur in zip(success_count, duration_s)]
    success_mean       = [float_or_none(load_tests[k].get("success_mean_latency_ms")) or 0.0 for k in keys]
    success_p95        = [float_or_none(load_tests[k].get("success_p95_latency_ms")) or 0.0 for k in keys]
    success_std        = [float_or_none(load_tests[k].get("success_stddev_latency_ms")) or 0.0 for k in keys]
    throttle_rate      = [100.0 * float(load_tests[k].get("throttled_rate") or 0.0) for k in keys]

    # Print latency summary for caption writing
    print("\n[Figure 04] Load test latency summary:")
    print(f"  {'N':>5}  {'Throughput':>12}  {'Throttle':>9}  {'μ latency':>10}  {'p95 latency':>12}")
    for c, tp, tr, m, p in zip(conc, success_throughput, throttle_rate, success_mean, success_p95):
        print(f"  {c:>5}  {tp:>10.1f}  {tr:>8.1f}%  {ms_label(m):>10}  {ms_label(p):>12}")
    print()

    fig, ax = plt.subplots(figsize=(COL_W, 3.6))
    ax_r = ax.twinx()

    y_max_tp = max(success_throughput) if success_throughput else 1
    ax_r_max = 115.0
    ax.set_ylim(0, y_max_tp * 1.45)
    ax_r.set_ylim(0, ax_r_max)

    # Bars — successful throughput (primary left axis)
    bar_w = max(3, (conc[-1] - conc[0]) // (len(conc) + 2)) if len(conc) > 1 else 5
    bars = ax.bar(conc, success_throughput, width=bar_w, color="#205781",
                  alpha=0.80, label="Successful Throughput (req/s)", zorder=2)

    # Track bar-top positions in ax data-units for throttle label collision check
    ax_ylim_top = ax.get_ylim()[1]
    tp_label_ys = [bar.get_height() + ax_ylim_top * 0.022 for bar in bars]

    # Throttle rate line + dynamically positioned labels
    ax_r.plot(conc, throttle_rate, marker="o", linewidth=1.5, color="#d56f3e",
              linestyle="--", label="429 Throttle Rate (%)", zorder=3)

    for i, (xv, yr) in enumerate(zip(conc, throttle_rate)):
        # Convert bar-top throughput label position into right-axis units so we
        # can measure true collision distance regardless of axis scaling.
        tp_label_in_r = (tp_label_ys[i] / ax_ylim_top) * ax_r_max
        gap = yr - tp_label_in_r          # positive → throttle dot is above label
        collision_zone = ax_r_max * 0.14  # 14 % of right-axis range ≈ one label height

        if abs(gap) < collision_zone:
            # Collision: push label well to the side (left) to completely clear the bar text
            dx, dy, ha = -6, 0, "right"
        elif gap > 0:
            # Throttle dot safely above the bar label → put % below the dot
            dx, dy, ha = 5, -10, "left"
        else:
            # Throttle dot safely below the bar label → put % above the dot
            dx, dy, ha = 5, 5, "left"

        ax_r.annotate(
            f"{yr:.0f}%", (xv, yr),
            textcoords="offset points", xytext=(dx, dy),
            fontsize=5.5, color="#d56f3e", ha=ha,
            bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.75),
        )

    ax.set_ylabel("Successful Throughput (req/s)", color="#205781")
    ax.tick_params(axis="y", labelcolor="#205781")
    ax_r.set_ylabel("429 Throttle Rate (%)", color="#d56f3e")
    ax_r.tick_params(axis="y", labelcolor="#d56f3e")
    ax_r.spines["right"].set_visible(True)
    ax.set_xticks(conc)
    ax.grid(axis="y", alpha=0.35, zorder=0)

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax_r.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=6, framealpha=0.9)

    fig.tight_layout()
    fig.savefig(plot_dir / "figure_04_throughput_and_latency_under_load.pdf", dpi=PUBLICATION_DPI)
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# Figure 5 — Gas Cost Comparison (BlockCap vs baselines)
# ─────────────────────────────────────────────────────────────────────────────
def render_figure5_gas_comparison(
    plot_dir: Path,
    gas_table: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # Pull actual BlockCap gas from contract_metrics deployment gas
    # Operation gas comes from the gas_comparison table where BlockCap values may be None
    # We use the table as-is and show "N/A" for missing values

    operations = ["register", "issue", "revoke", "delegate", "check"]

    systems_seen: list[str] = []
    indexed: dict[tuple[str, str], float] = {}
    rows_out = []

    for row in gas_table:
        op = str(row.get("operation", "")).lower()
        sys_name = str(row.get("system", ""))
        gas = float_or_none(row.get("gas_cost"))
        if sys_name and sys_name not in systems_seen:
            systems_seen.append(sys_name)
        if op and sys_name and gas is not None:
            indexed[(op, sys_name)] = gas
        rows_out.append({"operation": op, "system": sys_name, "gas_cost": row.get("gas_cost")})

    systems = [s for s in systems_seen if s and s != "BlockCap"]
    if "BlockCap" in systems_seen:
        systems = ["BlockCap"] + systems

    # Filter ops that have at least one non-zero value
    active_ops = [op for op in operations if any(indexed.get((op, s), 0) > 0 for s in systems)]
    if not active_ops:
        return rows_out

    x = list(range(len(active_ops)))
    n = len(systems)
    width = min(0.7 / max(n, 1), 0.28)
    sys_colors = ["#205781", "#c17037", "#4f6d3a", "#8a5a8c", "#a61e2d"]

    fig, ax = plt.subplots(figsize=(COL_W, 3.0))
    center = (n - 1) / 2.0
    for idx, (system, color) in enumerate(zip(systems, sys_colors)):
        vals = [indexed.get((op, system), 0.0) for op in active_ops]
        xs = [v + (idx - center) * width for v in x]
        bars = ax.bar(xs, vals, width=width, label=system, color=color, alpha=0.88)
        for bar, val in zip(bars, vals):
            if val > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(ax.get_ylim()[1] * 0.01, 500),
                        f"{val:,.0f}", ha="center", fontsize=5.5, rotation=0)

    ax.set_xticks(x)
    ax.set_xticklabels([op.capitalize() for op in active_ops])
    ax.set_ylabel("Gas Cost (units)")
    ax.set_title("Gas Cost Comparison — BlockCap vs Baselines")
    ax.legend(loc="upper right")
    ax.grid(axis="y", alpha=0.3)
    ymax = max((indexed.get((op, s), 0) for op in active_ops for s in systems), default=1)
    ax.set_ylim(0, ymax * 1.25)

    fig.tight_layout()
    fig.savefig(plot_dir / "figure_05_gas_comparison.pdf", dpi=PUBLICATION_DPI)
    plt.close(fig)
    return rows_out


# ─────────────────────────────────────────────────────────────────────────────
# Figure 6 — Contract Bytecode & Deployment Gas
# ─────────────────────────────────────────────────────────────────────────────
def render_figure6_contract_metrics(plot_dir: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contract_rows = [r for r in rows if str(r.get("contract", "")).upper() != "TOTALS"]
    if not contract_rows:
        return []

    CONTRACT_ABBR = {
        "NodeRegistry": "NodeReg", "CapabilityGrant": "CapGrant",
        "ValidatorGovernance": "ValGov", "PolicyMultisig": "PolicyMS",
    }
    contracts  = [CONTRACT_ABBR.get(str(r.get("contract", "")), str(r.get("contract", ""))) for r in contract_rows]
    loc        = [float_or_none(r.get("source_lines_non_blank")) or 0.0 for r in contract_rows]
    bytecode   = [float_or_none(r.get("bytecode_size_kb")) or 0.0 for r in contract_rows]
    deploy_gas = [float_or_none(r.get("deployment_gas_used")) or 0.0 for r in contract_rows]

    x = list(range(len(contracts)))
    width = 0.28

    fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(COL_W, 5.0))

    # Top: Bytecode KB + LOC side-by-side
    bars_bc = ax_top.bar([i - width / 2 for i in x], bytecode, width=width,
                         label="Bytecode (KB)", color="#1f7a3c", alpha=0.88)
    ax_loc = ax_top.twinx()
    bars_loc = ax_loc.bar([i + width / 2 for i in x], loc, width=width,
                          label="Source LOC", color="#1f5f87", alpha=0.75)
    ax_top.set_xticks(x)
    ax_top.set_xticklabels(contracts, rotation=30, ha="right")
    ax_top.set_ylabel("Bytecode Size (KB)")
    ax_loc.set_ylabel("Source Lines (non-blank)")
    ax_top.set_title("Contract Bytecode Size & Source Lines")
    ax_top.grid(axis="y", alpha=0.25)
    for bar, val in zip(bars_bc, bytecode):
        if val > 0:
            ax_top.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(max(bytecode) * 0.02, 0.2),
                        f"{val:.1f}", ha="center", fontsize=5.5)
    for bar, val in zip(bars_loc, loc):
        if val > 0:
            ax_loc.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(max(loc) * 0.02, 2),
                        f"{val:.0f}", ha="center", fontsize=5.5)
    h1, l1 = ax_top.get_legend_handles_labels()
    h2, l2 = ax_loc.get_legend_handles_labels()
    ax_top.legend(h1 + h2, l1 + l2, loc="upper right")

    # Bottom: Deployment Gas
    bars_gas = ax_bot.bar(contracts, deploy_gas, color="#b9770f", alpha=0.88)
    ax_bot.set_ylabel("Deployment Gas (units)")
    ax_bot.set_title("Contract Deployment Gas")
    ax_bot.set_xticks(list(range(len(contracts))))
    ax_bot.set_xticklabels(contracts, rotation=30, ha="right")
    ax_bot.grid(axis="y", alpha=0.25)
    ymax_gas = max(deploy_gas) if deploy_gas else 1
    for bar, val in zip(bars_gas, deploy_gas):
        if val > 0:
            ax_bot.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + ymax_gas * 0.02,
                        f"{val:,.0f}", ha="center", fontsize=5.5)
    ax_bot.set_ylim(0, ymax_gas * 1.2)

    fig.tight_layout(h_pad=3.0)
    fig.savefig(plot_dir / "figure_06_contract_metrics.pdf", dpi=PUBLICATION_DPI)
    plt.close(fig)

    return [
        {
            "contract": r.get("contract"),
            "source_lines_non_blank": r.get("source_lines_non_blank"),
            "bytecode_size_kb": r.get("bytecode_size_kb"),
            "deployment_gas_used": r.get("deployment_gas_used"),
        }
        for r in contract_rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Figure 0 — Unified Latency Overview (access + registration + policy)
# ─────────────────────────────────────────────────────────────────────────────
def render_figure0_unified_latency(
    plot_dir: Path,
    lifecycle_rows: list[dict[str, Any]],
    topology_rows: list[dict[str, Any]],
) -> None:
    def _both(op: str) -> tuple[float, float, float, float, float, float]:
        """Returns (cold_mean, cold_lo, cold_hi, warm_mean, warm_lo, warm_hi)."""
        cm, clo, chi, _ = _agg_op_condition(lifecycle_rows, op, "cold")
        wm, wlo, whi, _ = _agg_op_condition(lifecycle_rows, op, "warm")
        return cm, clo, chi, wm, wlo, whi

    def _reg(want_validator: bool) -> tuple[float, float, float]:
        """Returns (mean, lo_err, hi_err) for registration rows."""
        vals = [
            r["mean_latency_ms"] for r in topology_rows
            if bool(r.get("wants_validator")) == want_validator
            and str(r.get("status", "")).lower() != "already_registered"
            and (r.get("mean_latency_ms") or 0) <= 10_000
        ]
        if not vals:
            return 0.0, 0.0, 0.0
        mean = sum(vals) / len(vals)
        return mean, 0.0, 0.0

    CAT_COLORS = {
        "Access — Write":  "#205781",
        "Access — Read":   "#4f6d3a",
        "Registration":    "#8a5a8c",
        "Policy":          "#c17037",
    }
    COLD_ALPHA = 0.55   # cold bars slightly transparent to visually distinguish
    WARM_ALPHA = 0.92

    reg_nonval_m, reg_nonval_lo, reg_nonval_hi = _reg(False)
    reg_val_m,    reg_val_lo,   reg_val_hi    = _reg(True)

    def _write(op):
        """Single bar for write ops — prefer warm (higher n), return (val, lo, hi)."""
        cm, clo, chi, wm, wlo, whi = _both(op)
        return (wm, wlo, whi) if wm > 0 else (cm, clo, chi)

    # fmt: (label, pri_val, pri_lo, pri_hi, cold_val, cold_lo, cold_hi, category, show_cold)
    op_entries = [
        ("Issue Token",  *_write("issue"),              0.0, 0.0, 0.0, "Access — Write", False),
        ("Delegable",    *_write("issue-delegable"),    0.0, 0.0, 0.0, "Access — Write", False),
        ("Delegate",     *_write("delegate"),           0.0, 0.0, 0.0, "Access — Write", False),
        ("Revoke Token", *_write("revoke"),             0.0, 0.0, 0.0, "Access — Write", False),
        ("Revoke Prop.", *_write("revoke_propagation"), 0.0, 0.0, 0.0, "Access — Write", False),
        # Read ops: warm bar (pri) + cold bar side by side
        ("Check Grant",  *[v for v in _both("check")[3:6]],   *_both("check")[:3],   "Access — Read", True),
        ("Expiry Check", *[v for v in _both("expiry-check")[3:6]], *_both("expiry-check")[:3], "Access — Read", True),
        ("Non-Val. Reg", reg_nonval_m, reg_nonval_lo, reg_nonval_hi, 0.0, 0.0, 0.0, "Registration", False),
        ("Val. Reg",     reg_val_m,    reg_val_lo,    reg_val_hi,    0.0, 0.0, 0.0, "Registration", False),
        ("Policy Enf.",  *_write("ensure-policy"),      0.0, 0.0, 0.0, "Policy",         False),
    ]

    op_entries = [e for e in op_entries if e[1] > 0 or e[4] > 0]

    op_labels  = [e[0] for e in op_entries]
    pri_vals   = [e[1] for e in op_entries]
    pri_lo     = [e[2] for e in op_entries]
    pri_hi     = [e[3] for e in op_entries]
    cold_vals  = [e[4] for e in op_entries]
    cold_lo    = [e[5] for e in op_entries]
    cold_hi    = [e[6] for e in op_entries]
    cats       = [e[7] for e in op_entries]
    show_cold  = [e[8] for e in op_entries]
    cat_colors = [CAT_COLORS[c] for c in cats]

    n = len(op_entries)
    x = list(range(n))
    width = 0.38        # bar width
    pair_offset = 0.21  # offset from group centre for paired cold/warm bars
    err_kw = {"elinewidth": 1.1, "ecolor": "#333", "capsize": 3}

    fig, ax = plt.subplots(figsize=(COL_W, 4.0))

    # Draw bars — cold (hatched, left offset) only where show_cold=True
    cold_bar_refs = []
    warm_bar_refs = []
    for i, (pv, plo, phi, cv, clo, chi, color, sc) in enumerate(
        zip(pri_vals, pri_lo, pri_hi, cold_vals, cold_lo, cold_hi, cat_colors, show_cold)
    ):
        x_warm = i + (pair_offset if sc else 0)
        x_cold = i - pair_offset

        wb = ax.bar(
            x_warm, pv if pv > 0 else float("nan"),
            width=width, alpha=WARM_ALPHA, color=color,
            yerr=[[min(plo, pv) if pv > 0 else 0], [phi if pv > 0 else 0]],
            error_kw=err_kw,
        )
        warm_bar_refs.append((wb[0], pv, phi))

        if sc and cv > 0:
            cb = ax.bar(
                x_cold, cv,
                width=width, alpha=COLD_ALPHA, color=color,
                yerr=[[min(clo, cv)], [chi]],
                error_kw=err_kw,
                hatch="//", edgecolor="white", linewidth=0.4,
            )
            cold_bar_refs.append((cb[0], cv, chi))

    # Log scale
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: ms_label(float(v))))
    ax.yaxis.set_minor_formatter(FuncFormatter(lambda v, _: ""))
    ax.set_xticks(x)
    ax.set_xticklabels(op_labels, ha="right", rotation=35, fontsize=6)
    ax.set_xlim(-0.6, n - 0.4)
    ax.set_ylabel("Latency (log scale)")
    ax.grid(axis="y", alpha=0.3, which="major")

    # Category shading and separators
    prev_cat = None
    cat_start: dict[str, int] = {}
    cat_end:   dict[str, int] = {}
    for i, cat in enumerate(cats):
        if cat != prev_cat:
            cat_start[cat] = i
            prev_cat = cat
        cat_end[cat] = i

    for cat, color in CAT_COLORS.items():
        if cat not in cat_start:
            continue
        lo, hi = cat_start[cat] - 0.5, cat_end[cat] + 0.5
        ax.axvspan(lo, hi, alpha=0.07, color=color, zorder=0)
        if lo > 0:
            ax.axvline(lo, color="#aaa", linewidth=0.7, linestyle="--", zorder=1)

    # Combined legend: category colour patches + cold/warm path patches
    cat_handles = [
        mpatches.Patch(facecolor=color, alpha=0.75, label=cat)
        for cat, color in CAT_COLORS.items()
        if cat in cat_start
    ]
    path_handles = [
        mpatches.Patch(facecolor="#888", hatch="//", edgecolor="white",
                       alpha=COLD_ALPHA, label="Cold (cache miss)"),
        mpatches.Patch(facecolor="#888", alpha=WARM_ALPHA, label="Warm (cache hit)"),
    ]
    ax.legend(handles=cat_handles + path_handles,
              loc="upper center", bbox_to_anchor=(0.5, -0.28),
              ncols=3, fontsize=5.5, frameon=False)

    fig.tight_layout(rect=[0, 0.12, 1, 1.0])
    fig.savefig(plot_dir / "figure_00_unified_latency_overview.pdf", dpi=PUBLICATION_DPI)
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# Figure 7 — Lifecycle Heatmap (aggregated, cold vs warm columns)
# ─────────────────────────────────────────────────────────────────────────────
def render_figure7_lifecycle_matrix(plot_dir: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ops = [
        "issue", "issue-delegable", "delegate",
        "revoke", "revoke_propagation",
        "check", "expiry-check", "ensure-policy",
    ]
    conditions = ["cold", "warm"]

    # agg[(op, cond)] = list of means across blockchain tiers
    agg: dict[tuple[str, str], list[float]] = defaultdict(list)
    table_rows: list[dict[str, Any]] = []

    for row in rows:
        op = normalize_op(str(row.get("operation", "")))
        tier = str(row.get("tier", "")).lower()
        cond = str(row.get("condition", "")).lower()
        mean = float_or_none(row.get("mean_latency_ms"))
        if op not in ops or tier not in BLOCKCHAIN_TIERS or mean is None:
            continue
        agg_cond = "warm" if cond == "concurrent" else cond
        if agg_cond in conditions:
            agg[(op, agg_cond)].append(mean)
        table_rows.append({
            "operation": op, "tier": tier, "condition": cond,
            "mean_latency_ms": round(mean, 3),
        })

    matrix = []
    cell_labels = []
    for op in ops:
        row_vals, row_labels = [], []
        for cond in conditions:
            vals = agg.get((op, cond), [])
            v = sum(vals) / len(vals) if vals else 0.0
            row_vals.append(v)
            row_labels.append(ms_label(v) if v > 0 else "—")
        matrix.append(row_vals)
        cell_labels.append(row_labels)

    col_labels = ["Cold path", "Warm path"]
    row_labels_display = [OP_LABELS.get(op, op) for op in ops]

    mat = [[v if v > 0 else float("nan") for v in row] for row in matrix]

    fig, ax = plt.subplots(figsize=(COL_W, 3.8))
    im = ax.imshow(mat, cmap="YlOrRd", aspect="auto")

    ax.set_xticks([0, 1])
    ax.set_xticklabels(col_labels, fontsize=6)
    ax.set_yticks(list(range(len(ops))))
    ax.set_yticklabels(row_labels_display, fontsize=6)
    ax.set_title("Token Lifecycle Latency Matrix\n(Cloud + Fog + Edge aggregated, Endpoint excluded)")

    for i, row_vals in enumerate(cell_labels):
        for j, label in enumerate(row_vals):
            color = "white" if (matrix[i][j] or 0) > (max(
                v for row in matrix for v in row if v > 0) * 0.6) else "black"
            ax.text(j, i, label, ha="center", va="center", color=color, fontsize=5.5, fontweight="bold")

    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label("Latency (ms)")

    fig.tight_layout()
    fig.savefig(plot_dir / "figure_07_lifecycle_matrix.pdf", dpi=PUBLICATION_DPI)
    plt.close(fig)

    return table_rows


# ─────────────────────────────────────────────────────────────────────────────
# Summary writer
# ─────────────────────────────────────────────────────────────────────────────
def write_summary(path: Path, *, scenario: str, load_keys: list[int]) -> None:
    payload = {
        "scenario": scenario,
        "load_concurrency": load_keys,
        "figures": {
            "figure_01": "Access control latency — issue, delegate, revoke, check, expiry (cold+warm, aggregated)",
            "figure_02": "Registration latency — non-validator, validator, promotion",
            "figure_03": "Policy enforcement, lookup and propagation latency",
            "figure_04": "Throughput, success latency and throttling under load",
            "figure_05": "Gas cost comparison vs ACS-IoT and IoTChain",
            "figure_06": "Contract bytecode size, LOC and deployment gas",
            "figure_07": "Token lifecycle latency heatmap (cold vs warm, aggregated)",
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Generate BlockCap publication figures")
    parser.add_argument("--results-dir", default=str(RESULTS_DIR))
    parser.add_argument("--scenario", default="unspecified")
    args = parser.parse_args()
    apply_publication_theme()

    results_dir = Path(args.results_dir)
    plots_dir = results_dir / "plots"
    tables_dir = results_dir / "tables"
    plots_dir.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)

    experimental = load_json(results_dir / "experimental_results.json") or {}
    contracts_raw = load_json(results_dir / "contract_metrics.json") or []

    lifecycle_rows         = collect_lifecycle_rows(experimental)
    topology_reg_rows      = experimental.get("topology_registration_latency") or []
    validator_prom_rows    = experimental.get("validator_promotion_latency") or []
    load_tests             = experimental.get("load_tests") or {}
    gas_table              = (experimental.get("gas_comparison") or {}).get("table") or []

    # Figure 0 — Unified latency overview
    render_figure0_unified_latency(plots_dir, lifecycle_rows, topology_reg_rows)

    # Figure 1 — Access latency
    render_figure1_access_latency(plots_dir, lifecycle_rows)

    # Figure 2 — Registration latency
    render_figure2_registration_latency(plots_dir, topology_reg_rows, validator_prom_rows)

    # Figure 3 — Policy latency
    render_figure3_policy_latency(plots_dir, lifecycle_rows)

    # Figure 4 — Load test
    render_figure4_load(plots_dir, load_tests)

    # Figure 5 — Gas comparison
    gas_rows = render_figure5_gas_comparison(plots_dir, gas_table)

    # Figure 6 — Contract metrics
    contract_rows = render_figure6_contract_metrics(plots_dir, contracts_raw)

    # Figure 7 — Lifecycle matrix
    table_rows = render_figure7_lifecycle_matrix(plots_dir, lifecycle_rows)

    # CSV tables
    save_csv(
        tables_dir / "table_t1_lifecycle_latency.csv",
        table_rows,
        ["operation", "tier", "condition", "mean_latency_ms"],
    )
    save_csv(
        tables_dir / "table_t2_gas_comparison.csv",
        gas_rows,
        ["operation", "system", "gas_cost"],
    )
    save_csv(
        tables_dir / "table_t3_contract_metrics.csv",
        contract_rows,
        ["contract", "source_lines_non_blank", "bytecode_size_kb", "deployment_gas_used"],
    )

    load_keys = sorted([int(k) for k in load_tests.keys()]) if load_tests else []
    write_summary(results_dir / "metrics_report_summary.json", scenario=args.scenario, load_keys=load_keys)

    print("Figures saved to:", plots_dir)
    print("Tables saved to:", tables_dir)


if __name__ == "__main__":
    main()
