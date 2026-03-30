#!/usr/bin/env python3
import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

LIFECYCLE_OPERATIONS = {
    "issueToken",
    "issueTokenDelegable",
    "delegateToken",
    "revokeToken",
    "revokeTokenPropagation",
    "expiryCheck",
    "checkGrant",
    "ensurePolicy",
    "registerNode",
}


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def timed_request(method: str, url: str, *, json_body: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    response = requests.request(method, url, json=json_body, params=params, timeout=30)
    elapsed = time.perf_counter() - started
    payload = None
    try:
        payload = response.json()
    except Exception:
        payload = response.text
    return {
        "status_code": response.status_code,
        "latency_ms": round(elapsed * 1000, 3),
        "ok": 200 <= response.status_code < 300,
        "payload": payload,
    }


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [float(sample["latency_ms"]) for sample in samples]
    return {
        "count": len(samples),
        "mean_latency_ms": round(statistics.fmean(latencies), 3) if latencies else 0.0,
        "stddev_latency_ms": round(statistics.pstdev(latencies), 3) if len(latencies) > 1 else 0.0,
        "p95_latency_ms": round(percentile(latencies, 0.95), 3),
        "error_count": sum(0 if sample["ok"] else 1 for sample in samples),
        "samples": samples,
    }


def host_url(host: str | None) -> str | None:
    if not host:
        return None
    return host.rstrip("/")


def load_scenario(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    return json.loads(Path(path).read_text())


def first_node(scenario: dict[str, Any], tier: str) -> dict[str, Any] | None:
    for node in scenario.get("nodes", []):
        if node.get("tier") == tier:
            return node
    return None


def gather_registered_signatures(scenario: dict[str, Any]) -> list[str]:
    sigs: list[str] = []
    for node in scenario.get("nodes", []):
        sig = ((node.get("payload") or {}).get("signature"))
        if sig:
            sigs.append(sig)
    return sigs


def tier_hosts_from_args(args: argparse.Namespace, scenario: dict[str, Any] | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if scenario:
        mapping["cloud"] = scenario["root"]["api_url"]
        for tier in ("fog", "edge", "endpoint"):
            node = first_node(scenario, tier)
            if node and node.get("api_port"):
                mapping[tier] = f"http://127.0.0.1:{node['api_port']}"
    overrides = {
        "cloud": args.cloud_host,
        "fog": args.fog_host,
        "edge": args.edge_host,
        "endpoint": args.endpoint_host,
    }
    for tier, value in overrides.items():
        if value:
            mapping[tier] = value
    return {tier: url for tier, url in mapping.items() if url}


def choose_actor_signatures(scenario: dict[str, Any], target_sig: str) -> tuple[str, str]:
    sigs = [sig for sig in gather_registered_signatures(scenario) if sig != target_sig]
    if len(sigs) < 2:
        raise RuntimeError("scenario does not contain enough registered signatures for experiments")
    return sigs[0], sigs[1]


def choose_target_signature(scenario: dict[str, Any], tier: str) -> str | None:
    if tier == "cloud":
        for fallback_tier in ("fog", "edge", "endpoint"):
            node = first_node(scenario, fallback_tier)
            sig = ((node or {}).get("payload") or {}).get("signature")
            if sig:
                return sig
        return None
    node = first_node(scenario, tier)
    return ((node or {}).get("payload") or {}).get("signature")


def access_body(from_sig: str, to_sig: str, resource_path: str, *, allow_delegation: bool = False, delegation_depth: int = 0) -> dict[str, Any]:
    return {
        "from_signature": from_sig,
        "to_signature": to_sig,
        "method": "GET",
        "resource_path": resource_path,
        "expiry_secs": 900,
        "allow_delegation": allow_delegation,
        "delegation_depth": delegation_depth,
        "audit": True,
    }


def extract_policy_id(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("policyId")
    if value is None:
        value = ((payload.get("grant") or {}).get("policyId"))
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def run_load_test(host: str, concurrency: int, body: dict[str, Any]) -> Any:
    body_path = RESULTS_DIR / f"load_body_{concurrency}.json"
    body_path.write_text(json.dumps(body, indent=2, sort_keys=True))
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "load_test.py"),
        "--host", host,
        "--operation", "access",
        "--concurrency", str(concurrency),
        "--total-requests", "500",
        "--duration-cap", "60",
        "--body-file", str(body_path),
    ]
    subprocess.run(cmd, check=True)
    result = read_json(RESULTS_DIR / "load_test.json")
    if isinstance(result, dict):
        result["concurrency"] = concurrency
    return result


def fetch_latency_summary(host: str) -> dict[str, Any]:
    response = requests.get(host.rstrip("/") + "/metrics/latency", timeout=30)
    response.raise_for_status()
    payload = response.json()
    return payload.get("summary", {})


def extract_lifecycle_rows(summary_by_tier: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tier, summary in summary_by_tier.items():
        for key, record in sorted(summary.items()):
            if record.get("operation") not in LIFECYCLE_OPERATIONS:
                continue
            rows.append({
                "tier": tier,
                "operation": record.get("operation"),
                "condition": record.get("condition"),
                "mean_latency_ms": record.get("mean_ms"),
                "stddev_latency_ms": record.get("stddev_ms"),
                "count": record.get("count"),
                "source_key": key,
            })
    return rows


def experimental_setup(scenario: dict[str, Any] | None) -> dict[str, Any]:
    root_dir = Path((scenario or {}).get("root", {}).get("directory", REPO_ROOT / "Node_root"))
    genesis_path = root_dir / "genesis" / "genesis.json"
    if not genesis_path.exists():
        genesis_path = REPO_ROOT / "Node_root" / "genesis" / "genesis.json"
    genesis = json.loads(genesis_path.read_text())
    qbft = genesis.get("config", {}).get("qbft", {})
    validator_nodes = []
    if scenario:
        root_sig = ((scenario.get("root") or {}).get("node_details") or {}).get("signature")
        if root_sig:
            validator_nodes.append(root_sig)
        for node in scenario.get("nodes", []):
            status = ((node.get("registration") or {}).get("status") or "")
            if status in {"validator_included", "validator_already_included"}:
                validator_nodes.append((node.get("payload") or {}).get("signature"))
    return {
        "network_id": genesis.get("config", {}).get("chainId"),
        "block_time_seconds": qbft.get("blockperiodseconds"),
        "epoch_length": qbft.get("epochlength"),
        "request_timeout_seconds": qbft.get("requesttimeoutseconds"),
        "validator_set_size": len([sig for sig in validator_nodes if sig]),
        "validator_nodes": [sig for sig in validator_nodes if sig],
        "selection_rationale": {
            "block_time_seconds": "Repo QBFT default chosen for a short private-network confirmation window while keeping local runs stable.",
            "request_timeout_seconds": "Repo QBFT default paired with the block period for local validator-round recovery.",
            "validator_set_size": "Derived from the scenario manifest entries that finished with validator inclusion.",
        },
    }


def build_comparison_table() -> Any:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "build_gas_comparison.py"),
    ]
    subprocess.run(cmd, check=True)
    return read_json(RESULTS_DIR / "gas_comparison.json")


def tier_experiment(host: str, tier: str, target_sig: str, requester_sig: str, delegatee_sig: str, runs: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    roundtrip_rows: list[dict[str, Any]] = []
    lifecycle_samples: list[dict[str, Any]] = []

    for idx in range(runs):
        resource_path = f"/experiments/{tier}/{idx}/{int(time.time() * 1000)}"
        cold = timed_request("POST", host.rstrip("/") + "/access", json_body=access_body(requester_sig, target_sig, resource_path))
        warm = timed_request("POST", host.rstrip("/") + "/access", json_body=access_body(requester_sig, target_sig, resource_path))
        grant_view = timed_request(
            "GET",
            host.rstrip("/") + "/grant",
            params={
                "from_signature": requester_sig,
                "to_signature": target_sig,
                "method": "GET",
                "resource_path": resource_path,
            },
        )
        policy_id = extract_policy_id(warm.get("payload")) or extract_policy_id(cold.get("payload")) or extract_policy_id(grant_view.get("payload"))

        delegable_resource = f"{resource_path}/delegable"
        seed = timed_request(
            "POST",
            host.rstrip("/") + "/access",
            json_body=access_body(requester_sig, target_sig, delegable_resource, allow_delegation=True, delegation_depth=2),
        )
        seed_grant = timed_request(
            "GET",
            host.rstrip("/") + "/grant",
            params={
                "from_signature": requester_sig,
                "to_signature": target_sig,
                "method": "GET",
                "resource_path": delegable_resource,
            },
        )
        seed_policy_id = extract_policy_id(seed.get("payload")) or extract_policy_id(seed_grant.get("payload")) or policy_id
        delegate = timed_request(
            "POST",
            host.rstrip("/") + "/delegate",
            json_body={
                "parent_from_sig": requester_sig,
                "to_sig": target_sig,
                "child_from_sig": delegatee_sig,
                "policy_id": seed_policy_id,
                "ops_csv": "READ",
                "child_expiry_secs": 600,
            },
        )
        expiry = timed_request(
            "GET",
            host.rstrip("/") + "/expiry-check",
            params={
                "from_signature": requester_sig,
                "to_signature": target_sig,
                "policy_id": seed_policy_id,
            },
        )
        revoke = timed_request(
            "POST",
            host.rstrip("/") + "/revoke-grant",
            json_body={
                "from_signature": requester_sig,
                "to_signature": target_sig,
                "policy_id": seed_policy_id,
            },
        )

        roundtrip_rows.extend([
            {"tier": tier, "condition": "cold", **cold},
            {"tier": tier, "condition": "warm", **warm},
        ])
        lifecycle_samples.extend([
            {"tier": tier, "operation": "delegation_http", **delegate},
            {"tier": tier, "operation": "expiry_check_http", **expiry},
            {"tier": tier, "operation": "revoke_http", **revoke},
        ])

    return roundtrip_rows, lifecycle_samples


def summarize_roundtrip(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["tier"], row["condition"]), []).append(row)
    out = []
    for (tier, condition), samples in sorted(grouped.items()):
        summary = summarize_samples(samples)
        out.append({
            "tier": tier,
            "condition": condition,
            "count": summary["count"],
            "mean_latency_ms": summary["mean_latency_ms"],
            "stddev_latency_ms": summary["stddev_latency_ms"],
            "p95_latency_ms": summary["p95_latency_ms"],
            "error_count": summary["error_count"],
        })
    return out


def summarize_table(latency_rows: list[dict[str, Any]]) -> str:
    lines = ["tier\tcondition\tmean_ms\tcount\terrors"]
    for row in latency_rows:
        lines.append(
            f"{row['tier']}\t{row['condition']}\t{row['mean_latency_ms']}\t{row['count']}\t{row['error_count']}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the BlockCap experiment bundle")
    parser.add_argument("--cloud-host")
    parser.add_argument("--fog-host")
    parser.add_argument("--edge-host")
    parser.add_argument("--endpoint-host")
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--scenario-file")
    args = parser.parse_args()

    scenario = load_scenario(args.scenario_file)
    tier_hosts = tier_hosts_from_args(args, scenario)
    if not {"cloud", "fog", "edge"} <= set(tier_hosts):
        raise SystemExit("cloud, fog, and edge hosts are required (directly or via --scenario-file)")
    if scenario and "endpoint" not in tier_hosts:
        raise SystemExit("scenario file does not expose an endpoint host")

    roundtrip_rows: list[dict[str, Any]] = []
    http_lifecycle_rows: list[dict[str, Any]] = []
    internal_latency_summary: dict[str, dict[str, Any]] = {}

    if scenario:
        for tier, host in tier_hosts.items():
            target_sig = choose_target_signature(scenario, tier)
            if not target_sig:
                continue
            requester_sig, delegatee_sig = choose_actor_signatures(scenario, target_sig)
            rt_rows, life_rows = tier_experiment(host, tier, target_sig, requester_sig, delegatee_sig, args.runs)
            roundtrip_rows.extend(rt_rows)
            http_lifecycle_rows.extend(life_rows)
            internal_latency_summary[tier] = fetch_latency_summary(host)
    else:
        for tier, host in tier_hosts.items():
            samples = [
                timed_request("GET", host.rstrip("/") + "/health")
                for _ in range(args.runs)
            ]
            for sample in samples:
                roundtrip_rows.append({"tier": tier, "condition": "warm", **sample})
            internal_latency_summary[tier] = fetch_latency_summary(host)

    load_body = None
    if scenario:
        fog_sig = ((first_node(scenario, "fog") or {}).get("payload") or {}).get("signature")
        requester_sig, _delegatee_sig = choose_actor_signatures(scenario, fog_sig)
        load_body = access_body(requester_sig, fog_sig, "/load/fog/shared")
    else:
        load_body = {"method": "GET", "resource_path": "/temperature", "from_signature": "sig-a", "to_signature": "sig-b"}

    load_results = {
        str(concurrency): run_load_test(tier_hosts["fog"], concurrency, load_body)
        for concurrency in (10, 50, 100)
    }

    gas_summary = read_json(RESULTS_DIR / "gas_summary.json")
    contract_metrics = read_json(RESULTS_DIR / "contract_metrics.json")
    gas_comparison = build_comparison_table()
    result = {
        "experimental_setup": experimental_setup(scenario),
        "end_to_end_latency": summarize_roundtrip(roundtrip_rows),
        "end_to_end_latency_raw": roundtrip_rows,
        "token_lifecycle_latency": extract_lifecycle_rows(internal_latency_summary),
        "token_lifecycle_http": http_lifecycle_rows,
        "internal_latency_summary": internal_latency_summary,
        "load_tests": load_results,
        "gas_summary": gas_summary,
        "contract_metrics": contract_metrics,
        "gas_comparison": gas_comparison,
    }

    output_path = RESULTS_DIR / "experimental_results.json"
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True))
    print(summarize_table(result["end_to_end_latency"]))


if __name__ == "__main__":
    main()
