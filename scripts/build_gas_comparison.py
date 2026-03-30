#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "results"
DEFAULT_BASELINE = REPO_ROOT / "experiment_baselines" / "gas_baselines.json"

OPERATION_MAP = {
    "register": "registerNode",
    "issue": "issueToken",
    "revoke": "revokeToken",
    "delegate": "delegateToken",
    "check": "checkGrant",
}


def load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def blockcap_gas_rows(summary: dict[str, Any]) -> dict[str, float | int]:
    rows: dict[str, float | int] = {}
    for op_name, fn_name in OPERATION_MAP.items():
        if fn_name == "checkGrant":
            rows[op_name] = 0
            continue
        record = summary.get(fn_name) or {}
        rows[op_name] = record.get("mean_gas_used")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build gas comparison table for BlockCap and literature baselines")
    parser.add_argument("--gas-summary", default=str(RESULTS_DIR / "gas_summary.json"))
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--output", default=str(RESULTS_DIR / "gas_comparison.json"))
    args = parser.parse_args()

    summary = load_json(Path(args.gas_summary)) or {}
    baseline = load_json(Path(args.baseline)) or {}
    blockcap = blockcap_gas_rows(summary)

    systems = ["BlockCap", "BlendCAC", "ACS-IoT"]
    table: list[dict[str, Any]] = []
    baseline_complete = True

    for operation in OPERATION_MAP:
        for system in systems:
            if system == "BlockCap":
                gas_cost = blockcap.get(operation)
            else:
                gas_cost = ((baseline.get(system) or {}).get(operation))
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
