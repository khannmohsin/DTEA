#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "results"
DEFAULT_BASELINE = REPO_ROOT / "experiment_baselines" / "gas_baselines.json"

OPERATION_MAP = {
    "register": ["registerNode"],
    "issue": ["issueToken"],
    "revoke": ["revokeToken"],
    # Some deployments expose delegable issuance but not a dedicated delegateToken call.
    "delegate": ["delegateToken", "issueTokenDelegable"],
    # Prefer direct check, then fallback to instrumented check-and-log when available.
    "check": ["checkGrant", "checkGrantAndLog"],
}


def load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _coerce_number(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except Exception:
        return None
    if parsed.is_integer():
        return int(parsed)
    return parsed


def blockcap_gas_rows(summary: dict[str, Any]) -> dict[str, float | int | None]:
    rows: dict[str, float | int | None] = {}
    for op_name, fn_names in OPERATION_MAP.items():
        gas_value: float | int | None = None
        for fn_name in fn_names:
            record = summary.get(fn_name) or {}
            gas_value = _coerce_number(record.get("mean_gas_used"))
            if gas_value is not None:
                break
        if op_name == "check" and gas_value is None:
            gas_value = 0
        rows[op_name] = gas_value
    return rows


def baseline_rows(raw_baseline: Any) -> tuple[list[str], dict[str, dict[str, float | int | None]]]:
    """Return (systems, rows) for legacy and enriched baseline schemas.

    Supported schemas:
      1) Legacy:
         {
           "BlendCAC": {"register": 1, ...},
           "ACS-IoT":  {"register": 2, ...}
         }

      2) Enriched:
         {
           "systems": {
             "BlendCAC": {
               "metrics": {
                 "register": {"gas_cost": 1},
                 ...
               }
             }
           }
         }
    """
    systems: list[str] = []
    rows: dict[str, dict[str, float | int | None]] = {}

    if not isinstance(raw_baseline, dict):
        return systems, rows

    baseline_systems = raw_baseline.get("systems")
    if isinstance(baseline_systems, dict):
        for system_name, payload in baseline_systems.items():
            if not isinstance(system_name, str):
                continue
            metrics = {}
            if isinstance(payload, dict):
                metrics = payload.get("metrics") or {}
            op_rows: dict[str, float | int | None] = {}
            for op_name in OPERATION_MAP.keys():
                metric_row = metrics.get(op_name)
                if isinstance(metric_row, dict):
                    op_rows[op_name] = _coerce_number(metric_row.get("gas_cost"))
                else:
                    op_rows[op_name] = _coerce_number(metric_row)
            systems.append(system_name)
            rows[system_name] = op_rows
        return systems, rows

    # Legacy fallback: treat every top-level dict key (except metadata) as a system.
    for system_name, payload in raw_baseline.items():
        if system_name in {"metadata", "notes", "sources"}:
            continue
        if not isinstance(system_name, str) or not isinstance(payload, dict):
            continue
        op_rows = {op_name: _coerce_number(payload.get(op_name)) for op_name in OPERATION_MAP.keys()}
        systems.append(system_name)
        rows[system_name] = op_rows

    return systems, rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build gas comparison table for BlockCap and literature baselines")
    parser.add_argument("--gas-summary", default=str(RESULTS_DIR / "gas_summary.json"))
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--output", default=str(RESULTS_DIR / "gas_comparison.json"))
    args = parser.parse_args()

    summary = load_json(Path(args.gas_summary)) or {}
    baseline = load_json(Path(args.baseline)) or {}
    blockcap = blockcap_gas_rows(summary)
    baseline_systems, baseline_values = baseline_rows(baseline)

    systems = ["BlockCap", *baseline_systems]
    table: list[dict[str, Any]] = []
    baseline_complete = True

    for operation in OPERATION_MAP.keys():
        for system in systems:
            if system == "BlockCap":
                gas_cost = blockcap.get(operation)
            else:
                gas_cost = (baseline_values.get(system) or {}).get(operation)
                if gas_cost is None:
                    baseline_complete = False
            table.append({
                "operation": operation,
                "system": system,
                "gas_cost": gas_cost,
            })

    output = {
        "baseline_complete": baseline_complete,
        "blockcap_source": str(Path(args.gas_summary)),
        "baseline_source": str(Path(args.baseline)),
        "table": table,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True))
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
