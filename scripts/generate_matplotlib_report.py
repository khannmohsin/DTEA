#!/usr/bin/env python3
"""Generate simple matplotlib charts and CSV tables from BlockCap results artifacts."""

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


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "results"
PLOTS_DIR = RESULTS_DIR / "plots"
TABLES_DIR = RESULTS_DIR / "tables"


def load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in fieldnames})


def float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def render_end_to_end_latency(plot_dir: Path, rows: list[dict[str, Any]], edge_sla_ms: float, cloud_sla_ms: float) -> dict[str, Any]:
    tiers = ["cloud", "fog", "edge", "endpoint"]
    cold = {tier: 0.0 for tier in tiers}
    warm = {tier: 0.0 for tier in tiers}

    for row in rows:
        tier = str(row.get("tier", "")).lower()
        condition = str(row.get("condition", "")).lower()
        mean = float_or_none(row.get("mean_latency_ms"))
        if tier not in tiers or mean is None:
            continue
        if condition == "cold":
            cold[tier] = mean
        else:
            warm[tier] = mean

    x = list(range(len(tiers)))
    width = 0.36

    fig, ax = plt.subplots(figsize=(9, 4.6))
    ax.bar([i - width / 2 for i in x], [cold[t] for t in tiers], width=width, label="Cold", color="#1f5f87")
    ax.bar([i + width / 2 for i in x], [warm[t] for t in tiers], width=width, label="Warm", color="#1f7a3c")
    ax.axhline(edge_sla_ms, color="#b12c2c", linestyle="--", linewidth=1.3, label=f"Edge SLA ({edge_sla_ms:.0f} ms)")
    ax.axhline(cloud_sla_ms, color="#b9770f", linestyle=":", linewidth=1.5, label=f"Cloud SLA ({cloud_sla_ms:.0f} ms)")
    ax.set_title("End-to-End Latency by Tier (Cold vs Warm)")
    ax.set_ylabel("Latency (ms)")
    ax.set_xticks(x)
    ax.set_xticklabels([t.capitalize() for t in tiers])
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(plot_dir / "figure_a_end_to_end_latency.png", dpi=160)
    plt.close(fig)

    edge_warm = warm["edge"]
    cloud_warm = warm["cloud"]
    return {
        "edge_warm_latency_ms": edge_warm,
        "cloud_warm_latency_ms": cloud_warm,
        "edge_sla_pass": edge_warm <= edge_sla_ms if edge_warm else False,
        "cloud_sla_pass": cloud_warm <= cloud_sla_ms if cloud_warm else False,
    }


def render_load_test(plot_dir: Path, load_tests: dict[str, dict[str, Any]]) -> None:
    keys = sorted(load_tests.keys(), key=lambda x: int(x))
    conc = [int(k) for k in keys]
    throughput = [float_or_none(load_tests[k].get("throughput_rps")) or 0.0 for k in keys]
    mean_lat = [float_or_none(load_tests[k].get("mean_latency_ms")) or 0.0 for k in keys]
    p95_lat = [float_or_none(load_tests[k].get("p95_latency_ms")) or 0.0 for k in keys]

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))

    axes[0].plot(conc, throughput, marker="o", linewidth=2.0, color="#1f5f87")
    axes[0].set_title("Throughput Under Load")
    axes[0].set_xlabel("Concurrent Requests")
    axes[0].set_ylabel("Throughput (req/s)")
    axes[0].grid(alpha=0.25)

    axes[1].plot(conc, mean_lat, marker="o", linewidth=2.0, label="Mean", color="#1f7a3c")
    axes[1].plot(conc, p95_lat, marker="s", linewidth=2.0, label="P95", color="#b12c2c")
    axes[1].set_title("Latency Under Load")
    axes[1].set_xlabel("Concurrent Requests")
    axes[1].set_ylabel("Latency (ms)")
    axes[1].grid(alpha=0.25)
    axes[1].legend()

    fig.tight_layout()
    fig.savefig(plot_dir / "figure_b_load_scaling.png", dpi=160)
    plt.close(fig)


def render_load_throughput(plot_dir: Path, load_tests: dict[str, dict[str, Any]]) -> None:
    keys = sorted(load_tests.keys(), key=lambda x: int(x))
    conc = [int(k) for k in keys]
    throughput = [float_or_none(load_tests[k].get("throughput_rps")) or 0.0 for k in keys]

    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    ax.plot(conc, throughput, marker="o", linewidth=2.0, color="#1f5f87")
    ax.set_title("Throughput Under Load")
    ax.set_xlabel("Concurrent Requests")
    ax.set_ylabel("Throughput (req/s)")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(plot_dir / "figure_02_throughput_under_load.png", dpi=160)
    plt.close(fig)


def render_load_latency_mean_p95(plot_dir: Path, load_tests: dict[str, dict[str, Any]]) -> None:
    keys = sorted(load_tests.keys(), key=lambda x: int(x))
    conc = [int(k) for k in keys]
    mean_lat = [float_or_none(load_tests[k].get("mean_latency_ms")) or 0.0 for k in keys]
    p95_lat = [float_or_none(load_tests[k].get("p95_latency_ms")) or 0.0 for k in keys]

    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    ax.plot(conc, mean_lat, marker="o", linewidth=2.0, label="Mean", color="#1f7a3c")
    ax.plot(conc, p95_lat, marker="s", linewidth=2.0, label="P95", color="#b12c2c")
    ax.set_title("Mean and P95 Latency Under Load")
    ax.set_xlabel("Concurrent Requests")
    ax.set_ylabel("Latency (ms)")
    ax.grid(alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(plot_dir / "figure_03_mean_p95_latency_under_load.png", dpi=160)
    plt.close(fig)


def render_lifecycle_matrix(plot_dir: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    op_alias = {
        "issuetoken": "issue",
        "issuetokendelegable": "issue",
        "revoketoken": "revoke",
        "revoketokenpropagation": "revoke_propagation",
        "delegatetoken": "delegate",
        "expirycheck": "expiry-check",
        "checkgrant": "check",
    }
    ops = ["issue", "revoke", "delegate", "expiry-check", "check", "revoke_propagation"]
    tiers = ["cloud", "fog", "edge", "endpoint"]

    table_rows: list[dict[str, Any]] = []
    agg: dict[tuple[str, str], list[float]] = defaultdict(list)

    for row in rows:
        raw_op = str(row.get("operation", "")).lower()
        op = op_alias.get(raw_op)
        tier = str(row.get("tier", "")).lower()
        mean = float_or_none(row.get("mean_latency_ms"))
        if not op or tier not in tiers or mean is None:
            continue
        agg[(op, tier)].append(mean)
        table_rows.append(
            {
                "operation": op,
                "tier": tier,
                "condition": row.get("condition", ""),
                "mean_latency_ms": round(mean, 3),
                "count": row.get("count", 0),
            }
        )

    matrix = []
    for op in ops:
        row_vals = []
        for tier in tiers:
            values = agg.get((op, tier), [])
            row_vals.append(sum(values) / len(values) if values else 0.0)
        matrix.append(row_vals)

    fig, ax = plt.subplots(figsize=(9.2, 4.6))
    im = ax.imshow(matrix, cmap="YlGnBu", aspect="auto")
    ax.set_title("Token Lifecycle Latency Matrix")
    ax.set_xticks(list(range(len(tiers))))
    ax.set_xticklabels([t.capitalize() for t in tiers])
    ax.set_yticks(list(range(len(ops))))
    ax.set_yticklabels([op.replace("_", " ") for op in ops])

    for i, op_vals in enumerate(matrix):
        for j, val in enumerate(op_vals):
            ax.text(j, i, f"{val:.1f}", ha="center", va="center", color="black", fontsize=8)

    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("Latency (ms)")
    fig.tight_layout()
    fig.savefig(plot_dir / "figure_c_lifecycle_matrix.png", dpi=160)
    plt.close(fig)

    return table_rows


def _normalize_operation(raw_op: str) -> str | None:
    op_alias = {
        "issuetoken": "issue",
        "issuetokendelegable": "issue",
        "delegatetoken": "delegate",
        "revoketoken": "revoke",
        "revoketokenpropagation": "revoke_propagation",
        "expirycheck": "expiry-check",
        "checkgrant": "check",
    }
    return op_alias.get(raw_op.lower().strip())


def render_operation_by_tier(plot_dir: Path, rows: list[dict[str, Any]], *, operation: str, output_name: str, title: str) -> None:
    tiers = ["cloud", "fog", "edge", "endpoint"]
    by_tier: dict[str, list[float]] = defaultdict(list)

    for row in rows:
        raw_op = str(row.get("operation", ""))
        op = _normalize_operation(raw_op)
        if op != operation:
            continue
        tier = str(row.get("tier", "")).lower()
        mean = float_or_none(row.get("mean_latency_ms"))
        if tier in tiers and mean is not None:
            by_tier[tier].append(mean)

    values = []
    for tier in tiers:
        vals = by_tier.get(tier, [])
        values.append(sum(vals) / len(vals) if vals else 0.0)

    fig, ax = plt.subplots(figsize=(7.2, 4.1))
    ax.bar([t.capitalize() for t in tiers], values, color="#1f5f87")
    ax.set_title(title)
    ax.set_ylabel("Latency (ms)")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(plot_dir / output_name, dpi=160)
    plt.close(fig)


def render_revocation_propagation(plot_dir: Path, lifecycle_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rev_rows = [row for row in lifecycle_rows if row.get("operation") == "revoke_propagation"]
    by_tier: dict[str, list[float]] = defaultdict(list)
    for row in rev_rows:
        tier = str(row.get("tier", "")).lower()
        mean = float_or_none(row.get("mean_latency_ms"))
        if tier and mean is not None:
            by_tier[tier].append(mean)

    tiers = sorted(by_tier.keys())
    values = [sum(by_tier[t]) / len(by_tier[t]) for t in tiers]

    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.bar(tiers, values, color="#b12c2c")
    ax.set_title("Revocation Propagation Latency")
    ax.set_ylabel("Latency (ms)")
    ax.set_xlabel("Tier")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(plot_dir / "figure_d_revocation_propagation.png", dpi=160)
    plt.close(fig)

    return [
        {
            "tier": tier,
            "revocation_propagation_latency_ms": round(val, 3),
        }
        for tier, val in zip(tiers, values)
    ]


def render_gas_comparison(plot_dir: Path, table: list[dict[str, Any]]) -> list[dict[str, Any]]:
    systems = ["BlockCap", "BlendCAC", "ACS-IoT"]
    operations = ["register", "issue", "revoke", "delegate", "check"]

    rows = []
    indexed: dict[tuple[str, str], float] = {}
    for row in table:
        op = str(row.get("operation", "")).lower()
        sys_name = str(row.get("system", ""))
        gas = float_or_none(row.get("gas_cost"))
        if op and sys_name and gas is not None:
            indexed[(op, sys_name)] = gas
        rows.append({"operation": op, "system": sys_name, "gas_cost": row.get("gas_cost")})

    x = list(range(len(operations)))
    width = 0.22
    fig, ax = plt.subplots(figsize=(10, 4.8))

    for idx, system in enumerate(systems):
        vals = [indexed.get((op, system), 0.0) for op in operations]
        xs = [v + (idx - 1) * width for v in x]
        ax.bar(xs, vals, width=width, label=system)

    ax.set_xticks(x)
    ax.set_xticklabels([op.capitalize() for op in operations])
    ax.set_ylabel("Gas Cost")
    ax.set_title("Gas Cost Comparison")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(plot_dir / "figure_e_gas_comparison.png", dpi=160)
    plt.close(fig)

    return rows


def render_contract_metrics(plot_dir: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contract_rows = [row for row in rows if str(row.get("contract", "")).upper() != "TOTALS"]
    contracts = [str(row.get("contract", "")) for row in contract_rows]

    loc = [float_or_none(row.get("source_lines_non_blank")) or 0.0 for row in contract_rows]
    bytecode = [float_or_none(row.get("bytecode_size_kb")) or 0.0 for row in contract_rows]
    deploy_gas = [float_or_none(row.get("deployment_gas_used")) or 0.0 for row in contract_rows]

    fig, axes = plt.subplots(1, 3, figsize=(13, 4.4))

    axes[0].bar(contracts, loc, color="#1f5f87")
    axes[0].set_title("Source LOC")
    axes[0].tick_params(axis="x", rotation=25)

    axes[1].bar(contracts, bytecode, color="#1f7a3c")
    axes[1].set_title("Bytecode Size (KB)")
    axes[1].tick_params(axis="x", rotation=25)

    axes[2].bar(contracts, deploy_gas, color="#b9770f")
    axes[2].set_title("Deployment Gas")
    axes[2].tick_params(axis="x", rotation=25)

    for ax in axes:
        ax.grid(axis="y", alpha=0.25)

    fig.tight_layout()
    fig.savefig(plot_dir / "figure_f_contract_metrics.png", dpi=160)
    plt.close(fig)

    return [
        {
            "contract": row.get("contract"),
            "source_lines_non_blank": row.get("source_lines_non_blank"),
            "bytecode_size_kb": row.get("bytecode_size_kb"),
            "deployment_gas_used": row.get("deployment_gas_used"),
        }
        for row in contract_rows
    ]


def render_contract_loc_size(plot_dir: Path, rows: list[dict[str, Any]]) -> None:
    contract_rows = [row for row in rows if str(row.get("contract", "")).upper() != "TOTALS"]
    contracts = [str(row.get("contract", "")) for row in contract_rows]
    loc = [float_or_none(row.get("source_lines_non_blank")) or 0.0 for row in contract_rows]
    bytecode = [float_or_none(row.get("bytecode_size_kb")) or 0.0 for row in contract_rows]

    x = list(range(len(contracts)))
    width = 0.36

    fig, ax1 = plt.subplots(figsize=(9.4, 4.4))
    ax1.bar([i - width / 2 for i in x], loc, width=width, label="LOC", color="#1f5f87")
    ax1.set_ylabel("Source LOC")

    ax2 = ax1.twinx()
    ax2.bar([i + width / 2 for i in x], bytecode, width=width, label="Bytecode KB", color="#1f7a3c")
    ax2.set_ylabel("Bytecode Size (KB)")

    ax1.set_xticks(x)
    ax1.set_xticklabels(contracts, rotation=25)
    ax1.set_title("Contract Code Size and LOC")
    ax1.grid(axis="y", alpha=0.2)

    handles1, labels1 = ax1.get_legend_handles_labels()
    handles2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(handles1 + handles2, labels1 + labels2, loc="upper left")

    fig.tight_layout()
    fig.savefig(plot_dir / "figure_09_contract_code_size_loc.png", dpi=160)
    plt.close(fig)


def render_contract_deployment_gas(plot_dir: Path, rows: list[dict[str, Any]]) -> None:
    contract_rows = [row for row in rows if str(row.get("contract", "")).upper() != "TOTALS"]
    contracts = [str(row.get("contract", "")) for row in contract_rows]
    deploy_gas = [float_or_none(row.get("deployment_gas_used")) or 0.0 for row in contract_rows]

    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    ax.bar(contracts, deploy_gas, color="#b9770f")
    ax.set_title("Contract Deployment Gas")
    ax.set_ylabel("Gas Used")
    ax.tick_params(axis="x", rotation=25)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(plot_dir / "figure_10_contract_deployment_gas.png", dpi=160)
    plt.close(fig)


def write_summary(path: Path, *, scenario: str, sla: dict[str, Any], load_keys: list[int]) -> None:
    payload = {
        "scenario": scenario,
        "load_concurrency": load_keys,
        "sla": {
            "edge_target_ms": sla.get("edge_target_ms"),
            "cloud_target_ms": sla.get("cloud_target_ms"),
            "edge_warm_latency_ms": sla.get("edge_warm_latency_ms"),
            "cloud_warm_latency_ms": sla.get("cloud_warm_latency_ms"),
            "edge_sla_pass": sla.get("edge_sla_pass"),
            "cloud_sla_pass": sla.get("cloud_sla_pass"),
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate matplotlib metric charts and tables from BlockCap result artifacts")
    parser.add_argument("--results-dir", default=str(RESULTS_DIR))
    parser.add_argument("--edge-sla-ms", type=float, default=200.0)
    parser.add_argument("--cloud-sla-ms", type=float, default=500.0)
    parser.add_argument("--scenario", default="unspecified")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    plots_dir = results_dir / "plots"
    tables_dir = results_dir / "tables"
    plots_dir.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)

    experimental = load_json(results_dir / "experimental_results.json") or {}
    gas = load_json(results_dir / "gas_comparison.json") or {}
    contracts = load_json(results_dir / "contract_metrics.json") or []

    end_to_end_rows = experimental.get("end_to_end_latency") or []
    load_tests = experimental.get("load_tests") or {}
    lifecycle_rows = experimental.get("token_lifecycle_latency") or []

    sla = render_end_to_end_latency(plots_dir, end_to_end_rows, args.edge_sla_ms, args.cloud_sla_ms)
    # Figure 01 for paper sequence
    if (plots_dir / "figure_a_end_to_end_latency.png").exists():
        (plots_dir / "figure_01_end_to_end_latency_by_tier.png").write_bytes(
            (plots_dir / "figure_a_end_to_end_latency.png").read_bytes()
        )
    sla["edge_target_ms"] = args.edge_sla_ms
    sla["cloud_target_ms"] = args.cloud_sla_ms

    if load_tests:
        render_load_test(plots_dir, load_tests)
        render_load_throughput(plots_dir, load_tests)
        render_load_latency_mean_p95(plots_dir, load_tests)

    lifecycle_table_rows = render_lifecycle_matrix(plots_dir, lifecycle_rows)
    render_operation_by_tier(
        plots_dir,
        lifecycle_rows,
        operation="issue",
        output_name="figure_04_token_issue_latency_by_tier.png",
        title="Token Issue Latency by Tier",
    )
    render_operation_by_tier(
        plots_dir,
        lifecycle_rows,
        operation="delegate",
        output_name="figure_05_token_delegate_latency_by_tier.png",
        title="Token Delegate Latency by Tier",
    )
    render_operation_by_tier(
        plots_dir,
        lifecycle_rows,
        operation="revoke",
        output_name="figure_06_token_revoke_latency_by_tier.png",
        title="Token Revoke Latency by Tier",
    )
    revocation_rows = render_revocation_propagation(plots_dir, lifecycle_table_rows)
    if (plots_dir / "figure_d_revocation_propagation.png").exists():
        (plots_dir / "figure_07_revocation_propagation_latency.png").write_bytes(
            (plots_dir / "figure_d_revocation_propagation.png").read_bytes()
        )
    gas_rows = render_gas_comparison(plots_dir, gas.get("table") or [])
    if (plots_dir / "figure_e_gas_comparison.png").exists():
        (plots_dir / "figure_08_gas_comparison_by_operation.png").write_bytes(
            (plots_dir / "figure_e_gas_comparison.png").read_bytes()
        )
    contract_rows = render_contract_metrics(plots_dir, contracts)
    render_contract_loc_size(plots_dir, contracts)
    render_contract_deployment_gas(plots_dir, contracts)

    save_csv(
        tables_dir / "table_t1_operation_tier_latency.csv",
        lifecycle_table_rows,
        ["operation", "tier", "condition", "mean_latency_ms", "count"],
    )
    save_csv(
        tables_dir / "table_t2_operation_system_gas.csv",
        gas_rows,
        ["operation", "system", "gas_cost"],
    )
    save_csv(
        tables_dir / "table_t3_contract_code_metrics.csv",
        contract_rows,
        ["contract", "source_lines_non_blank", "bytecode_size_kb", "deployment_gas_used"],
    )
    save_csv(
        tables_dir / "table_t4_revocation_propagation.csv",
        revocation_rows,
        ["tier", "revocation_propagation_latency_ms"],
    )

    load_keys = sorted([int(k) for k in load_tests.keys()]) if load_tests else []
    write_summary(results_dir / "metrics_report_summary.json", scenario=args.scenario, sla=sla, load_keys=load_keys)

    print("Generated charts in:", plots_dir)
    print("Generated tables in:", tables_dir)
    print("Summary:", results_dir / "metrics_report_summary.json")


if __name__ == "__main__":
    main()
