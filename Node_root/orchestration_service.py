#!/usr/bin/env python3
"""
Flask service that exposes orchestration endpoints backed by orchestrator.py.
- Registration flow that can include validator proposal/inclusion.
- Access flow that creates resource-scoped policies and issues (or reuses) grants.
- Delegation flow for parent->child grant delegation.
- Helpful read-only endpoints (node details, grant info, validators, health).

Environment:
  REAL_INTERACT=1   -> talk to a live chain via web3.py (default: mock in tests)
  FROM_IDX=0        -> signer index to use for transactions (default: 0)
  ORCH_TRACE=1      -> log underlying node commands for debugging

Run:
  python orchestration_service.py --host 0.0.0.0 --port 8080
"""

import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime
from functools import wraps
from typing import Any, Dict
import base64
import io
import json
import requests
import urllib.parse
import urllib.request

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from werkzeug.middleware.proxy_fix import ProxyFix
from pathlib import Path
# import orchestrator module you already have
from device_catalog import device_catalog_payload, default_device_id, resolve_device
from infrastructure_control import InfrastructureController
from acknowledgement import AcknowledgementSender
from orchestrator import Orchestrator, METHOD_TO_OP, ROLE
from tef_metrics import TokenBucketRateLimiter, UiResultsRecorder, ensure_results_dir

try:
    from flasgger import Swagger
except Exception:  # pragma: no cover - optional dependency fallback
    class Swagger:  # type: ignore[override]
        def __init__(self, *_args, **_kwargs):
            pass

# Optional perf decorator (fallback to no-op if absent)
try:
    from monitor import track_performance
except Exception:
    def track_performance(fn):
        return fn


# ------------ helpers ------------

PARENT_URL = os.getenv("PARENT_URL", "").rstrip("/")
NODE_ROLE = os.getenv("NODE_ROLE", "cloud").strip().lower()
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()
POLICY_FILE = os.getenv("POLICY_FILE", "").strip()
VALID_POLICY_ROLES = sorted(name for name, value in ROLE.items() if value > 0)

def ok(data: Dict[str, Any], code: int = 200):
    return jsonify({"ok": True, **data}), code

def err(message: str, code: int = 400, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra or {})
    return jsonify(payload), code


def invalid_policy_roles(*roles: str) -> list[str]:
    return [role for role in roles if role not in VALID_POLICY_ROLES]


def require_admin(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        token_required = os.getenv("ADMIN_TOKEN", ADMIN_TOKEN).strip()
        if token_required:
            token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            if token != token_required:
                return err("unauthorized", 401)
        return fn(*args, **kwargs)

    return wrapped

def require_json(keys):
    """Simple required-keys validator. Returns (json, error_response_or_None)."""
    if not request.is_json:
        return None, err("expected application/json body", 415)
    try:
        data = request.get_json(force=True, silent=False)
    except Exception:
        return None, err("malformed JSON", 400)
    missing = [k for k in keys if k not in data or data[k] in (None, "")]
    if missing:
        return None, err(f"missing required field: {', '.join(missing)}", 422)
    return data, None

def _b64read(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    return base64.b64encode(p.read_bytes()).decode("ascii")

def _local_signature_from_node_details(repo_root: str | None) -> str | None:
    # tries Node_root/node-details.json by default; falls back to repo_root
    try:
        # common locations
        candidates = [
            Path("node-details.json"),
            Path(repo_root or ".") / "node-details.json",
            Path(repo_root or ".") / "Node_root" / "node-details.json",
        ]
        for p in candidates:
            if p.exists():
                with p.open("r") as f:
                    data = json.load(f)
                sig = data.get("signature")
                if isinstance(sig, str) and len(sig) > 0:
                    return sig
    except Exception:
        pass
    return None


def _int_arg(name: str, default: int) -> int:
    raw = request.args.get(name, str(default))
    try:
        return int(raw)
    except Exception:
        return default


def _safe_read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _sha256_bytes(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def _download_bootstrap_artifact(url: str, *, expected_sha256: str = "", timeout: float = 5.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read()
    if expected_sha256 and _sha256_bytes(payload).lower() != str(expected_sha256).lower():
        raise RuntimeError(f"checksum_mismatch_for_{url}")
    return payload


def _collect_result_artifacts(results_dir: Path) -> dict[str, Any]:
    files = {
        "latency": results_dir / "latency.json",
        "load_test": results_dir / "load_test.json",
        "experimental_results": results_dir / "experimental_results.json",
        "gas_summary": results_dir / "gas_summary.json",
        "contract_metrics": results_dir / "contract_metrics.json",
        "gas_comparison": results_dir / "gas_comparison.json",
    }
    return {name: _safe_read_json(path) for name, path in files.items()}


def _collect_result_artifact_meta(results_dir: Path) -> dict[str, Any]:
    files = {
        "latency": results_dir / "latency.json",
        "load_test": results_dir / "load_test.json",
        "experimental_results": results_dir / "experimental_results.json",
        "gas_summary": results_dir / "gas_summary.json",
        "contract_metrics": results_dir / "contract_metrics.json",
        "gas_comparison": results_dir / "gas_comparison.json",
    }
    rows: dict[str, Any] = {}
    latest_ms = 0
    for name, path in files.items():
        exists = path.exists()
        updated_at_ms = int(path.stat().st_mtime * 1000) if exists else 0
        latest_ms = max(latest_ms, updated_at_ms)
        rows[name] = {
            "path": str(path),
            "exists": exists,
            "updated_at_ms": updated_at_ms,
        }
    rows["latest_updated_at_ms"] = latest_ms
    return rows


def _format_timestamp_ms(value: int | float | None) -> str:
    try:
        if not value:
            return "-"
        return datetime.fromtimestamp(float(value) / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "-"


def _select_research_sections(
    *,
    selected_scenario: str | None,
    artifacts: dict[str, Any],
    artifact_meta: dict[str, Any],
    ui_recorder: UiResultsRecorder,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    def has_available_sections(sections: dict[str, Any]) -> bool:
        for value in (sections or {}).values():
            if isinstance(value, dict) and value.get("available") is True:
                return True
        return False

    if selected_scenario:
        saved_run = ui_recorder.load_run(selected_scenario)
        saved_sections = (saved_run or {}).get("research_sections") or {}
        if saved_sections and has_available_sections(saved_sections):
            run_updated = int((saved_run or {}).get("updated_at_ms") or 0)
            artifact_updated = int(artifact_meta.get("latest_updated_at_ms") or 0)
            return saved_sections, {
                "mode": "scenario_snapshot",
                "scenario": selected_scenario,
                "updated_at_ms": run_updated,
                "updated_at_label": _format_timestamp_ms(run_updated),
                "artifact_latest_updated_at_ms": artifact_updated,
                "artifact_latest_updated_at_label": _format_timestamp_ms(artifact_updated),
                "note": f"Research metrics are being shown from scenario snapshot '{selected_scenario}' recorded at {_format_timestamp_ms(run_updated)}. The shared artifact files were last updated at {_format_timestamp_ms(artifact_updated)}.",
            }
    fallback_note = "Research metrics are being shown from the shared results artifacts under results/."
    if selected_scenario:
        fallback_note = f"No saved results were found yet for topology '{selected_scenario}'. Showing the shared results artifacts under results/ instead."
    return _build_research_sections(artifacts), {
        "mode": "shared_artifacts",
        "scenario": None,
        "updated_at_ms": int(artifact_meta.get("latest_updated_at_ms") or 0),
        "updated_at_label": _format_timestamp_ms(artifact_meta.get("latest_updated_at_ms")),
        "artifact_latest_updated_at_ms": int(artifact_meta.get("latest_updated_at_ms") or 0),
        "artifact_latest_updated_at_label": _format_timestamp_ms(artifact_meta.get("latest_updated_at_ms")),
        "note": fallback_note,
    }


def _result_scenarios(
    *,
    infra_rows: list[dict[str, Any]],
    saved_runs: list[dict[str, Any]],
    selected_scenario: str | None,
) -> list[dict[str, Any]]:
    infra_by_name = {
        str(row.get("scenario") or ""): row
        for row in (infra_rows or [])
        if row.get("scenario")
    }
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for run in saved_runs or []:
        name = str(run.get("scenario") or "").strip()
        if not name or name in seen:
            continue
        infra_row = infra_by_name.get(name, {})
        rows.append({
            "scenario": name,
            "exists": bool(infra_row.get("exists", True)),
            "running": bool(infra_row.get("running", False)),
            "has_results": True,
            "updated_at_ms": int(run.get("updated_at_ms") or 0),
        })
        seen.add(name)

    if selected_scenario and selected_scenario not in seen:
        infra_row = infra_by_name.get(selected_scenario, {})
        rows.append({
            "scenario": selected_scenario,
            "exists": bool(infra_row.get("exists", True)),
            "running": bool(infra_row.get("running", False)),
            "has_results": False,
            "updated_at_ms": 0,
        })
        seen.add(selected_scenario)

    rows.sort(key=lambda item: (not bool(item.get("running")), -(int(item.get("updated_at_ms") or 0)), item.get("scenario") or ""))
    return rows


def _http_json(url: str, *, timeout: float = 2.0) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
        return json.loads(payload)
    except Exception:
        return None


def _saved_run_runtime_metrics(saved_run: dict[str, Any] | None) -> dict[str, Any] | None:
    if not saved_run:
        return None
    snapshots = list((saved_run or {}).get("snapshots") or [])
    latest = snapshots[-1] if snapshots else {}
    scenario_details = (saved_run or {}).get("scenario_details") or {}
    latency_summary: dict[str, Any] = {}
    for item in latest.get("latency_points") or []:
        key = str(item.get("key") or "")
        if not key:
            continue
        latency_summary[key] = {
            "operation": item.get("operation"),
            "node_tier": item.get("node_tier"),
            "condition": item.get("condition"),
            "mean_ms": item.get("mean_ms", 0.0),
            "count": item.get("count", 0),
        }
    return {
        "source": "scenario_snapshot",
        "event_stats": {
            "total_events": int(latest.get("total_events", 0) or 0),
            "status_counts": {
                "ok": int(latest.get("granted", 0) or 0),
                "denied": int(latest.get("denied", 0) or 0),
                "error": int(latest.get("errors", 0) or 0),
            },
        },
        "active_flows": [],
        "recent_events": [],
        "recent_flows": [],
        "latency_summary": latency_summary,
        "live_series": snapshots,
        "scenario_details": scenario_details,
    }


def _topology_runtime_metrics(
    *,
    infra_details: dict[str, Any],
    current_api_url: str,
    orch: Orchestrator,
) -> dict[str, Any] | None:
    scenario = (infra_details or {}).get("scenario") or {}
    root = scenario.get("root") or {}
    root_api_url = str(root.get("api_url") or "").rstrip("/")
    current_api = str(current_api_url or "").rstrip("/")
    if not root_api_url:
        return None
    if root_api_url == current_api:
        return {
            "source": "local_root",
            "event_stats": orch.event_stats(),
            "active_flows": orch.active_flow_summaries(),
            "recent_events": orch.recent_events(limit=100),
            "recent_flows": orch.flow_summaries(limit=50),
            "latency_summary": orch.latency_summary(),
            "live_series": [],
            "scenario_details": infra_details,
        }
    stats_payload = _http_json(f"{root_api_url}/events/stats")
    active_payload = _http_json(f"{root_api_url}/events/active")
    recent_payload = _http_json(f"{root_api_url}/events/recent?limit=100")
    flows_payload = _http_json(f"{root_api_url}/events/flows?limit=50")
    latency_payload = _http_json(f"{root_api_url}/metrics/latency")
    if not any([stats_payload, active_payload, recent_payload, flows_payload, latency_payload]):
        return None
    return {
        "source": "topology_root_api",
        "event_stats": (stats_payload or {}).get("stats") or {},
        "active_flows": (active_payload or {}).get("flows") or [],
        "recent_events": (recent_payload or {}).get("events") or [],
        "recent_flows": (flows_payload or {}).get("flows") or [],
        "latency_summary": (latency_payload or {}).get("summary") or {},
        "live_series": [],
        "scenario_details": infra_details,
    }


TIER_LABELS = {
    "cloud": "Cloud",
    "fog": "Fog",
    "edge": "Edge",
    "endpoint": "Endpoint",
}

OPERATION_LABELS = {
    "issueToken": "Issue",
    "issue": "Issue",
    "issue grant": "Issue",
    "issueTokenDelegable": "Delegate",
    "delegateToken": "Delegate",
    "delegate": "Delegate",
    "revokeToken": "Revoke",
    "revoke": "Revoke",
    "expiryCheck": "Expiry Check",
    "checkGrant": "Check Grant",
    "revokeTokenPropagation": "Revocation Propagation",
}


def _display_tier(value: str | None) -> str:
    key = str(value or "").strip().lower()
    return TIER_LABELS.get(key, key.title() or "Unknown")


def _display_operation(value: str | None) -> str:
    key = str(value or "").strip()
    return OPERATION_LABELS.get(key, key or "Unknown")


def _empty_research_section(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason, "rows": [], "chart": {}}


def _build_end_to_end_latency_section(experimental: dict[str, Any] | None) -> dict[str, Any]:
    rows = []
    for row in (experimental or {}).get("end_to_end_latency") or []:
        rows.append({
            "tier": _display_tier(row.get("tier")),
            "condition": str(row.get("condition") or "").title(),
            "mean_latency_ms": row.get("mean_latency_ms"),
            "p95_latency_ms": row.get("p95_latency_ms"),
            "stddev_latency_ms": row.get("stddev_latency_ms"),
            "count": row.get("count", 0),
            "error_count": row.get("error_count", 0),
        })
    if not rows:
        return _empty_research_section("No end-to-end latency results found.")
    tiers = sorted({row["tier"] for row in rows})
    cold = [{"label": row["tier"], "value": row.get("mean_latency_ms", 0)} for row in rows if row["condition"] == "Cold"]
    warm = [{"label": row["tier"], "value": row.get("mean_latency_ms", 0)} for row in rows if row["condition"] == "Warm"]
    return {
        "available": True,
        "rows": rows,
        "chart": {"tiers": tiers, "series": [{"name": "Cold", "points": cold}, {"name": "Warm", "points": warm}]},
    }


def _build_load_tests_section(experimental: dict[str, Any] | None, artifacts: dict[str, Any]) -> dict[str, Any]:
    raw = (experimental or {}).get("load_tests") or {}
    rows = []
    for concurrency in sorted(raw, key=lambda item: int(item)):
        item = raw.get(concurrency) or {}
        rows.append({
            "concurrency": int(concurrency),
            "throughput_rps": item.get("throughput_rps"),
            "mean_latency_ms": item.get("mean_latency_ms"),
            "p95_latency_ms": item.get("p95_latency_ms"),
            "error_count": item.get("error_count"),
            "completed_requests": item.get("completed_requests"),
        })
    if not rows and isinstance(artifacts.get("load_test"), dict):
        item = artifacts["load_test"]
        rows = [{
            "concurrency": item.get("concurrency"),
            "throughput_rps": item.get("throughput_rps"),
            "mean_latency_ms": item.get("mean_latency_ms"),
            "p95_latency_ms": item.get("p95_latency_ms"),
            "error_count": item.get("error_count"),
            "completed_requests": item.get("completed_requests"),
        }]
    if not rows:
        return _empty_research_section("No load-test results found.")
    return {
        "available": True,
        "rows": rows,
        "chart": {
            "categories": [row["concurrency"] for row in rows],
            "throughput": [row.get("throughput_rps", 0) or 0 for row in rows],
            "mean_latency": [row.get("mean_latency_ms", 0) or 0 for row in rows],
            "p95_latency": [row.get("p95_latency_ms", 0) or 0 for row in rows],
        },
    }


def _build_token_lifecycle_section(experimental: dict[str, Any] | None) -> dict[str, Any]:
    rows = []
    for row in (experimental or {}).get("token_lifecycle_latency") or []:
        rows.append({
            "operation": _display_operation(row.get("operation")),
            "tier": _display_tier(row.get("tier")),
            "condition": str(row.get("condition") or "").title(),
            "mean_latency_ms": row.get("mean_latency_ms"),
            "stddev_latency_ms": row.get("stddev_latency_ms"),
            "count": row.get("count", 0),
            "source_key": row.get("source_key"),
        })
    if not rows:
        return _empty_research_section("No token lifecycle latency rows found.")
    return {
        "available": True,
        "rows": rows,
        "chart": {
            "labels": [f'{row["operation"]} / {row["tier"]}' for row in rows],
            "values": [row.get("mean_latency_ms", 0) or 0 for row in rows],
        },
    }


def _build_revocation_propagation_section(lifecycle_section: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in lifecycle_section.get("rows", []) if row.get("operation") == "Revocation Propagation"]
    if not rows:
        return _empty_research_section("No revocation propagation results found.")
    return {
        "available": True,
        "rows": rows,
        "chart": {
            "labels": [row.get("tier") for row in rows],
            "values": [row.get("mean_latency_ms", 0) or 0 for row in rows],
        },
    }


def _build_experimental_setup_section(experimental: dict[str, Any] | None) -> dict[str, Any]:
    setup = (experimental or {}).get("experimental_setup") or {}
    if not setup:
        return _empty_research_section("No experimental setup metadata found.")
    rows = [
        {"label": "Block Time (s)", "value": setup.get("block_time_seconds")},
        {"label": "Validator Set Size", "value": setup.get("validator_set_size")},
        {"label": "Validator Nodes", "value": ", ".join(setup.get("validator_nodes") or [])},
        {"label": "Request Timeout (s)", "value": setup.get("request_timeout_seconds")},
        {"label": "Epoch Length", "value": setup.get("epoch_length")},
        {"label": "Selection Rationale", "value": setup.get("selection_rationale")},
    ]
    return {"available": True, "rows": rows, "chart": {}}


def _build_contract_metrics_section(experimental: dict[str, Any] | None, artifacts: dict[str, Any]) -> dict[str, Any]:
    rows = (experimental or {}).get("contract_metrics") or artifacts.get("contract_metrics") or []
    if not rows:
        return _empty_research_section("No smart-contract metric rows found.")
    normalized = [{
        "contract": row.get("contract"),
        "source_lines_non_blank": row.get("source_lines_non_blank"),
        "bytecode_size_kb": row.get("bytecode_size_kb"),
        "estimated_deployment_gas": row.get("estimated_deployment_gas"),
        "deployment_gas_used": row.get("deployment_gas_used"),
    } for row in rows]
    chart_rows = [row for row in normalized if row.get("contract") != "TOTALS"]
    return {
        "available": True,
        "rows": normalized,
        "chart": {
            "labels": [row.get("contract") for row in chart_rows],
            "bytecode_kb": [row.get("bytecode_size_kb", 0) or 0 for row in chart_rows],
            "deployment_gas": [row.get("deployment_gas_used", 0) or 0 for row in chart_rows],
        },
    }


def _build_gas_comparison_section(experimental: dict[str, Any] | None, artifacts: dict[str, Any]) -> dict[str, Any]:
    comparison = (experimental or {}).get("gas_comparison") or artifacts.get("gas_comparison") or {}
    table = comparison.get("table") or []
    if not table:
        return _empty_research_section("No gas-comparison rows found.")
    rows = [{
        "operation": _display_operation(row.get("operation")),
        "system": row.get("system"),
        "gas_cost": row.get("gas_cost"),
    } for row in table]
    operations = sorted({row["operation"] for row in rows})
    systems = ["BlockCap", "BlendCAC", "ACS-IoT"]
    chart_series = []
    for system in systems:
        chart_series.append({
            "name": system,
            "points": [{"label": op, "value": next((row["gas_cost"] for row in rows if row["operation"] == op and row["system"] == system), None)} for op in operations],
        })
    note = None if comparison.get("baseline_complete", True) else "Baseline file is incomplete. Fill experiment_baselines/gas_baselines.json to complete the comparison."
    return {
        "available": True,
        "rows": rows,
        "baseline_complete": comparison.get("baseline_complete", False),
        "baseline_source": comparison.get("baseline_source"),
        "blockcap_source": comparison.get("blockcap_source"),
        "note": note,
        "chart": {"operations": operations, "series": chart_series},
    }


def _build_research_sections(artifacts: dict[str, Any]) -> dict[str, Any]:
    experimental = artifacts.get("experimental_results") if isinstance(artifacts.get("experimental_results"), dict) else {}
    lifecycle = _build_token_lifecycle_section(experimental)
    return {
        "end_to_end_latency": _build_end_to_end_latency_section(experimental),
        "load_tests": _build_load_tests_section(experimental, artifacts),
        "token_lifecycle_latency": lifecycle,
        "revocation_propagation": _build_revocation_propagation_section(lifecycle),
        "experimental_setup": _build_experimental_setup_section(experimental),
        "contract_metrics": _build_contract_metrics_section(experimental, artifacts),
        "gas_comparison": _build_gas_comparison_section(experimental, artifacts),
    }


TIER_ORDER = {"cloud": 0, "fog": 1, "edge": 2, "endpoint": 3}
TIER_BADGES = {"cloud": "R", "fog": "F", "edge": "E", "endpoint": "EP"}
PHASE_ORDER = ["api", "chain", "registration", "bootstrap_ack", "access", "consensus"]
PHASE_LABELS = {
    "api": "API",
    "chain": "Chain",
    "registration": "Registration",
    "bootstrap_ack": "Bootstrap ACK",
    "access": "Access",
    "consensus": "Consensus",
}


def _selected_device_payload(node: dict[str, Any] | None, *, tier: str, allow_default: bool = False) -> dict[str, Any]:
    row = (node or {}).get("selected_device") or {}
    if isinstance(row, dict) and row.get("label"):
        return row
    preset_id = str((node or {}).get("simulated_device") or "").strip()
    if preset_id:
        try:
            return resolve_device(preset_id, tier)
        except Exception:
            pass
    if allow_default and tier in {"fog", "edge", "endpoint"}:
        try:
            return resolve_device(default_device_id(tier), tier)
        except Exception:
            pass
    return {}


def _measured_metrics_catalog() -> list[dict[str, str]]:
    return [
        {
            "label": "End-to-End Access Latency",
            "detail": "Cold and warm access round-trip time from request to grant or deny at Cloud, Fog, Edge, and Endpoint tiers.",
        },
        {
            "label": "Load-Test Throughput and Latency",
            "detail": "Concurrency runs at 10, 50, and 100 simultaneous requests with throughput, mean latency, and p95 latency.",
        },
        {
            "label": "Token Lifecycle Latencies",
            "detail": "Per-operation latency for issue, delegate, revoke, expiry check, check grant, and ensure policy.",
        },
        {
            "label": "Revocation Propagation",
            "detail": "Time until validators process the block carrying the revocation and the result becomes visible across the active set.",
        },
        {
            "label": "Live Flow Activity",
            "detail": "Realtime counts for total events, granted decisions, denied decisions, errors, and active flows.",
        },
        {
            "label": "Smart Contract Metrics",
            "detail": "Source lines of code, compiled bytecode size, and measured deployment gas for each contract module.",
        },
        {
            "label": "Gas Cost Comparison",
            "detail": "Per-operation gas comparison across BlockCap, BlendCAC, and ACS-IoT using the baseline JSON file.",
        },
        {
            "label": "Experimental Setup",
            "detail": "Consensus parameters used in experiments: block time, validator set, timeout, epoch length, and rationale.",
        },
    ]


def _event_targets_node(event: dict[str, Any], node: dict[str, Any], local_node_id: str, local_node_name: str) -> bool:
    event_tier = str(event.get("node_tier") or "").lower()
    node_tier = str(node.get("tier") or "").lower()
    if event_tier and node_tier and event_tier != node_tier:
        return False
    node_signature = str(node.get("signature") or "").strip()
    event_from_signature = str(event.get("from_signature") or "").strip()
    event_to_signature = str(event.get("to_signature") or "").strip()
    if node_signature and node_signature in {event_from_signature, event_to_signature}:
        return True
    event_id = str(event.get("node_id") or "")
    event_name = str(event.get("node_name") or "")
    node_id = str(node.get("node_id") or "")
    node_name = str(node.get("name") or "")
    if event_id and node_id and event_id == node_id:
        return True
    if event_name and node_name and event_name == node_name:
        return True
    if node.get("key") == "root" and (event_id == local_node_id or event_name == local_node_name):
        return True
    return False



def _map_process_to_phase(status: str | None, *, allow_na: bool = False) -> str:
    value = str(status or "").lower()
    if allow_na and value == "not_applicable":
        return "not_applicable"
    if value in {"ok", "reused"}:
        return "complete"
    if value in {"running", "started"}:
        return "active"
    if value in {"pending", "waiting"}:
        return "waiting"
    if value in {"error", "denied"}:
        return "error"
    if value == "down":
        return "pending"
    return "pending"


def _map_event_to_phase(flow_type: str, stage: str) -> str | None:
    if str(stage or "").startswith("acknowledgement_"):
        return "bootstrap_ack"
    if flow_type in {"access", "delegation", "revocation"}:
        return "access"
    if flow_type == "registration":
        return "registration"
    if flow_type in {"validator", "cache"}:
        return "consensus"
    if stage in {"grant_lookup", "grant_issue_or_reuse", "grant_check", "grant_audit", "policy_lookup", "policy_create"}:
        return "access"
    if stage in {"validator_proposal", "validator_vote", "validator_inclusion_result", "peer_wait", "listener_started", "validator_wait"}:
        return "consensus"
    return None


def _event_status_to_phase_status(status: str) -> str:
    value = str(status or "").lower()
    if value in {"ok", "reused"}:
        return "complete"
    if value in {"started", "running"}:
        return "active"
    if value in {"waiting", "pending"}:
        return "waiting"
    if value in {"error", "denied"}:
        return "error"
    return "pending"


def _phase_priority(status: str) -> int:
    return {"error": 4, "active": 3, "waiting": 2, "complete": 1, "pending": 0, "not_applicable": -1}.get(status, 0)


def _merge_phase_status(current: str, new_status: str) -> str:
    if current == "error" or new_status == "not_applicable":
        return current
    if new_status == "error":
        return "error"
    if new_status == "pending":
        return current
    return new_status if _phase_priority(new_status) >= _phase_priority(current) or current in {"pending", "waiting", "complete", "active"} else current


def _build_phase_progress(node: dict[str, Any], recent_events: list[dict[str, Any]], local_node_id: str, local_node_name: str) -> list[dict[str, str]]:
    tier = str(node.get("tier") or "endpoint").lower()
    processes = node.get("processes") or {}
    registration_process = processes.get("registration") or {}
    phases = {
        "api": _map_process_to_phase((processes.get("api") or {}).get("status")),
        "chain": _map_process_to_phase((processes.get("chain") or {}).get("status"), allow_na=True),
        "registration": _map_process_to_phase(registration_process.get("status")),
        "bootstrap_ack": "pending" if tier in {"fog", "edge"} else "not_applicable",
        "access": "pending",
        "consensus": "pending" if tier in {"cloud", "fog", "edge"} else "not_applicable",
    }
    if tier == "endpoint":
        phases["chain"] = "not_applicable"
    if node.get("key") == "root" and phases["registration"] == "pending":
        phases["registration"] = "complete"
    registration_label = str(registration_process.get("label") or "").lower()
    if "validator included" in registration_label or "registered" in registration_label:
        phases["registration"] = "complete"
    if "validator included" in registration_label:
        phases["consensus"] = "complete"
    ack_status = str(((node.get("registration") or {}).get("ack_status") or "")).lower()
    if tier in {"fog", "edge"}:
        if ack_status in {"completed", "sent", "delivered", "ok"}:
            phases["bootstrap_ack"] = "complete"
        elif ack_status in {"failed", "error"}:
            phases["bootstrap_ack"] = "error"
        elif ack_status in {"queued", "pending"} and phases["registration"] == "complete":
            phases["bootstrap_ack"] = "waiting"

    for event in recent_events or []:
        if not _event_targets_node(event, node, local_node_id, local_node_name):
            continue
        phase_key = _map_event_to_phase(str(event.get("flow_type") or ""), str(event.get("stage") or ""))
        if not phase_key:
            continue
        phases[phase_key] = _merge_phase_status(phases.get(phase_key, "pending"), _event_status_to_phase_status(str(event.get("status") or "")))

    return [{"key": key, "label": PHASE_LABELS[key], "status": phases[key]} for key in PHASE_ORDER]


def _phase_summary(phases: list[dict[str, str]]) -> dict[str, str]:
    for phase in phases:
        if phase["status"] == "error":
            return {"stage": phase["label"], "status": "error"}
    for phase in phases:
        if phase["status"] == "active":
            return {"stage": phase["label"], "status": "running"}
    for phase in phases:
        if phase["status"] == "waiting":
            return {"stage": phase["label"], "status": "pending"}
    phase_map = {phase["key"]: phase["status"] for phase in phases}
    startup_ready = (
        phase_map.get("api") == "complete"
        and phase_map.get("registration") == "complete"
        and phase_map.get("chain") in {"complete", "not_applicable"}
    )
    if startup_ready:
        return {"stage": "Ready", "status": "ok"}
    meaningful = [phase for phase in phases if phase["status"] not in {"not_applicable"}]
    if meaningful and all(phase["status"] == "complete" for phase in meaningful):
        return {"stage": "Ready", "status": "ok"}
    completed = [phase for phase in meaningful if phase["status"] == "complete"]
    if completed:
        return {"stage": completed[-1]["label"], "status": "ok"}
    return {"stage": "Initializing", "status": "down"}


def _build_node_cards(
    scenario_payload: dict[str, Any] | None,
    recent_events: list[dict[str, Any]],
    *,
    local_node_id: str,
    local_node_name: str,
    selected_scenario: str | None,
    job: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    node_views = list((scenario_payload or {}).get("node_views") or [])
    node_views.sort(key=lambda item: (TIER_ORDER.get(str(item.get("tier") or "endpoint"), 99), int(item.get("ordinal") or 0), str(item.get("name") or "")))
    cards = []
    for node in node_views:
        phases = _build_phase_progress(node, recent_events, local_node_id, local_node_name)
        stage = _phase_summary(phases)
        tier = str(node.get("tier") or "endpoint").lower()
        secondary_process = "chain" if tier in {"cloud", "fog", "edge"} else None
        processes = node.get("processes") or {}
        api_status = str((processes.get("api") or {}).get("status") or "")
        chain_status = str((processes.get("chain") or {}).get("status") or "")
        api_running = api_status in {"ok", "running", "started"}
        chain_running = chain_status in {"ok", "running", "started"}
        api_ready = api_status == "ok"
        chain_ready = chain_status == "ok"
        node_page_status = dict(node.get("node_page") or {})
        node_page_running = str(node_page_status.get("status") or "") in {"ok", "running", "started"}
        node_page_ready = str(node_page_status.get("status") or "") == "ok"
        selected_device = _selected_device_payload(node, tier=tier, allow_default=False)
        cards.append({
            "key": node.get("key"),
            "name": node.get("name"),
            "node_id": node.get("node_id"),
            "signature": node.get("signature"),
            "tier": tier,
            "badge": TIER_BADGES.get(tier, "?"),
            "api_url": node.get("api_url"),
            "rpc_url": node.get("rpc_url"),
            "p2p_port": node.get("p2p_port"),
            "stage": stage["stage"],
            "stage_status": stage["status"],
            "phases": phases,
            "summary_status": node.get("summary_status"),
            "summary_label": node.get("summary_label"),
            "control_url": node.get("control_url"),
            "dashboard_url": node.get("dashboard_url"),
            "node_page_running": node_page_running,
            "node_page_ready": node_page_ready,
            "node_page_label": node_page_status.get("label") or "",
            "default_process": "api",
            "secondary_process": secondary_process,
            "api_running": api_running,
            "chain_running": chain_running,
            "api_ready": api_ready,
            "chain_ready": chain_ready,
            "stop_enabled": api_running or chain_running,
            "selected_scenario": selected_scenario,
            "runtime_backend": node.get("runtime_backend") or ("container" if tier in {"fog", "edge", "endpoint"} else "native"),
            "simulated_device": selected_device.get("label") or "",
            "selected_device": selected_device,
            "action_capabilities": _node_action_capabilities(node, node_views),
            "defaults": _node_action_defaults(node, node_views),
            "policy_capabilities": _node_policy_capabilities(node),
            "policy_defaults": _node_policy_defaults(),
        })
    return cards


def _inferred_process_readiness_from_job(
    job: dict[str, Any] | None,
    *,
    key: str,
    tier: str,
    ordinal: int,
) -> dict[str, bool]:
    text = "\n".join(str(line) for line in ((job or {}).get("log_lines") or [])).lower()
    if not text:
        return {"api_ready": False, "chain_ready": False}
    if key == "root":
        return {
            "api_ready": "[topology] root api is ready" in text,
            "chain_ready": "[topology] root chain is ready" in text or "[topology] root chain already reachable" in text,
        }
    label = f"{tier}{ordinal}"
    api_ready = f"[topology] {label} api is ready" in text or f"[topology] {label} api and chain are ready" in text
    chain_ready = f"[topology] {label} api and chain are ready" in text
    return {"api_ready": api_ready, "chain_ready": chain_ready}


def _inferred_node_network_from_job(
    job: dict[str, Any] | None,
    *,
    key: str,
    tier: str,
    ordinal: int,
    host: str,
) -> dict[str, str]:
    lines = [str(line) for line in ((job or {}).get("log_lines") or [])]
    api_port = ""
    rpc_port = ""
    p2p_port = ""

    if key == "root":
        for line in lines:
            root_chain = re.search(r"\[topology\] starting root chain attempt \d+ on rpc=(\d+) p2p=(\d+)", line)
            if root_chain:
                rpc_port, p2p_port = root_chain.group(1), root_chain.group(2)
            root_api = re.search(r"\[topology\] starting root api on port (\d+)", line)
            if root_api:
                api_port = root_api.group(1)
    else:
        label = f"{tier}{ordinal}"
        for line in lines:
            api_match = re.search(rf"\[topology\] starting {re.escape(label)} api on port (\d+)", line)
            if api_match:
                api_port = api_match.group(1)
            chain_match = re.search(rf"\[topology\] starting {re.escape(label)} chain on rpc=(\d+) p2p=(\d+)", line)
            if chain_match:
                rpc_port, p2p_port = chain_match.group(1), chain_match.group(2)

    return {
        "api_url": f"http://{host}:{api_port}" if api_port else "",
        "rpc_url": f"http://{host}:{rpc_port}" if rpc_port else "",
        "p2p_port": p2p_port,
    }


def _inferred_node_page_from_job(
    job: dict[str, Any] | None,
    *,
    key: str,
    tier: str,
    ordinal: int,
    host: str,
) -> dict[str, Any]:
    if key == "root":
        return {"control_url": "", "ready": False, "running": False}
    lines = [str(line) for line in ((job or {}).get("log_lines") or [])]
    label = f"{tier}{ordinal}"
    control_port = ""
    for line in lines:
        page_match = re.search(rf"\[topology\] starting {re.escape(label)} page on port (\d+)", line)
        if page_match:
            control_port = page_match.group(1)
    ready = any(f"[topology] {label} page is ready" in line for line in lines)
    return {
        "control_url": f"http://{host}:{control_port}" if control_port else "",
        "ready": ready,
        "running": bool(control_port),
    }


def _build_pending_node_cards(job: dict[str, Any] | None, *, selected_scenario: str | None) -> list[dict[str, Any]]:
    if not job or job.get("status") != "running":
        return []
    topo = dict(job.get("topology_request") or {})
    fog_count = int(topo.get("fog") or 0)
    edge_count = int(topo.get("edge") or 0)
    endpoint_count = int(topo.get("endpoint") or 0)
    host = str(topo.get("host") or "127.0.0.1")
    endpoint_roles = [str(role) for role in (topo.get("endpoint_roles") or [])]
    fog_devices = [str(device) for device in (topo.get("fog_devices") or [])]
    edge_devices = [str(device) for device in (topo.get("edge_devices") or [])]
    endpoint_devices = [str(device) for device in (topo.get("endpoint_devices") or [])]
    if not endpoint_roles:
        endpoint_roles = [str(topo.get("endpoint_role") or "Sensor")] * endpoint_count
    if not fog_devices:
        fog_devices = [default_device_id("fog")] * fog_count
    if not edge_devices:
        edge_devices = [default_device_id("edge")] * edge_count
    if not endpoint_devices:
        endpoint_devices = [default_device_id("endpoint")] * endpoint_count

    def pending_node_details(key: str, tier: str, ordinal: int) -> dict[str, Any]:
        if not selected_scenario:
            return {}
        generated_root = Path(__file__).resolve().parent.parent / "runtime" / "generated" / str(selected_scenario)
        if key == "root":
            details_path = generated_root / "root" / "node-details.json"
        else:
            details_path = generated_root / f"{tier}{ordinal}" / "node-details.json"
        if not details_path.exists():
            return {}
        try:
            return json.loads(details_path.read_text())
        except Exception:
            return {}

    def card_for(*, key: str, name: str, node_id: str, tier: str, ordinal: int, role_label: str | None = None, device_id: str | None = None) -> dict[str, Any]:
        title = name if not role_label else f"{name} ({role_label})"
        inferred_ready = _inferred_process_readiness_from_job(job, key=key, tier=tier, ordinal=ordinal)
        inferred_network = _inferred_node_network_from_job(job, key=key, tier=tier, ordinal=ordinal, host=host)
        inferred_page = _inferred_node_page_from_job(job, key=key, tier=tier, ordinal=ordinal, host=host)
        node_details = pending_node_details(key, tier, ordinal)
        selected_device = _selected_device_payload({"simulated_device": device_id}, tier=tier, allow_default=True)
        is_root_start_job = key == "root" and str((job or {}).get("job_kind") or "") == "start-root"
        api_running = inferred_ready["api_ready"] or bool(inferred_network["api_url"]) or is_root_start_job
        chain_running = False if tier == "endpoint" else (inferred_ready["chain_ready"] or bool(inferred_network["rpc_url"]) or is_root_start_job)
        phases = [
            {"key": "api", "label": "API", "status": "complete" if inferred_ready["api_ready"] else ("active" if inferred_network["api_url"] else "pending")},
            {"key": "chain", "label": "Chain", "status": "not_applicable" if tier == "endpoint" else ("complete" if inferred_ready["chain_ready"] else ("active" if inferred_network["rpc_url"] else "pending"))},
            {"key": "registration", "label": "Registration", "status": "complete" if key == "root" else "pending"},
            {"key": "bootstrap_ack", "label": "Bootstrap ACK", "status": "pending" if tier in {"fog", "edge"} else "not_applicable"},
            {"key": "access", "label": "Access", "status": "pending"},
            {"key": "consensus", "label": "Consensus", "status": "not_applicable" if tier == "endpoint" else "pending"},
        ]
        stage = _phase_summary(phases)
        return {
            "key": key,
            "name": title,
            "node_id": node_id,
            "signature": node_details.get("signature") or "",
            "tier": tier,
            "badge": TIER_BADGES.get(tier, "?"),
            "api_url": inferred_network["api_url"],
            "rpc_url": inferred_network["rpc_url"],
            "p2p_port": inferred_network["p2p_port"],
            "stage": stage["stage"],
            "stage_status": stage["status"],
            "phases": phases,
            "summary_status": "running",
            "summary_label": "container loading" if inferred_page["running"] and not inferred_page["ready"] else "initializing",
            "control_url": inferred_page["control_url"],
            "dashboard_url": None,
            "node_page_running": inferred_page["running"],
            "node_page_ready": inferred_page["ready"],
            "node_page_label": "Node page ready" if inferred_page["ready"] else ("Node page loading" if inferred_page["running"] else "Node page pending"),
            "default_process": "api",
            "secondary_process": "chain" if tier in {"cloud", "fog", "edge"} else None,
            "api_running": api_running,
            "chain_running": chain_running,
            "api_ready": inferred_ready["api_ready"],
            "chain_ready": inferred_ready["chain_ready"] if tier in {"cloud", "fog", "edge"} else False,
            "stop_enabled": bool(inferred_network["api_url"] or inferred_network["rpc_url"]),
            "selected_scenario": selected_scenario,
            "runtime_backend": "container" if tier in {"fog", "edge", "endpoint"} else "native",
            "simulated_device": selected_device.get("label") or "",
            "selected_device": selected_device,
            "action_capabilities": {
                "access": {"enabled": False, "reason": "Node interaction is not ready yet."},
                "delegate": {"enabled": False, "reason": "Node interaction is not ready yet."},
                "revoke_grant": {"enabled": False, "reason": "Node interaction is not ready yet."},
                "grant_lookup": {"enabled": False, "reason": "Node interaction is not ready yet."},
                "expiry_check": {"enabled": False, "reason": "Node interaction is not ready yet."},
            },
            "defaults": {},
            "policy_capabilities": {
                "policy_create": {"enabled": False, "reason": "Node interaction is not ready yet."},
                "policy_find": {"enabled": False, "reason": "Node interaction is not ready yet."},
                "policy_get": {"enabled": False, "reason": "Node interaction is not ready yet."},
                "policy_update": {"enabled": False, "reason": "Node interaction is not ready yet."},
                "policy_deprecate": {"enabled": False, "reason": "Node interaction is not ready yet."},
            },
            "policy_defaults": _node_policy_defaults(),
            "ordinal": ordinal,
        }

    cards = [
        card_for(key="root", name="Root Cloud", node_id="CLOUD01", tier="cloud", ordinal=0),
    ]
    for idx in range(1, fog_count + 1):
        cards.append(card_for(key=f"fog-{idx}", name=f"Fog{idx}", node_id=f"FG-{idx}", tier="fog", ordinal=idx, device_id=fog_devices[idx - 1] if idx - 1 < len(fog_devices) else None))
    for idx in range(1, edge_count + 1):
        cards.append(card_for(key=f"edge-{idx}", name=f"Edge{idx}", node_id=f"ED-{idx}", tier="edge", ordinal=idx, device_id=edge_devices[idx - 1] if idx - 1 < len(edge_devices) else None))
    for idx in range(1, endpoint_count + 1):
        role = endpoint_roles[idx - 1] if idx - 1 < len(endpoint_roles) else "Sensor"
        cards.append(card_for(key=f"endpoint-{idx}", name=f"Endpoint{idx}", node_id=f"EP-{idx}", tier="endpoint", ordinal=idx, role_label=role, device_id=endpoint_devices[idx - 1] if idx - 1 < len(endpoint_devices) else None))
    cards.sort(key=lambda item: (TIER_ORDER.get(str(item.get("tier") or "endpoint"), 99), int(item.get("ordinal") or 0)))
    return cards


def _terminal_preview_lines(content: str, *, limit: int = 2, width: int = 96) -> list[str]:
    rows = []
    for raw in str(content or "").splitlines():
        text = re.sub(r"\s+", " ", str(raw).strip())
        if not text:
            continue
        if len(text) > width:
            text = text[: width - 1].rstrip() + "…"
        rows.append(text)
    if not rows:
        return []
    return rows[-limit:]


def _root_terminal_preview_payload(
    *,
    infra: Any,
    scenario: str | None,
    root_card: dict[str, Any] | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "api": {"visible": False, "preview_lines": []},
        "chain": {"visible": False, "preview_lines": []},
    }
    if not scenario or not root_card:
        return result

    process_rows = {
        "api": bool(root_card.get("api_running")),
        "chain": bool(root_card.get("chain_running")),
    }
    for process, visible in process_rows.items():
        if not visible:
            continue
        try:
            payload = infra.node_logs(scenario=scenario, node_key="root", process=process, lines=4)
        except Exception:
            payload = {"content": "", "command": "", "exists": False}
        result[process] = {
            "visible": True,
            "preview_lines": _terminal_preview_lines(str(payload.get("content") or "")),
            "command": str(payload.get("command") or ""),
            "exists": bool(payload.get("exists")),
        }
    return result


ACTION_REASON_LABELS = {
    "signature_missing": "Node signature is not available yet.",
    "api_not_ready": "API is not ready on this node yet.",
    "no_target_nodes": "No other signed nodes are available in this topology.",
    "no_child_nodes": "No child node is available for delegation.",
    "node_not_ready": "Node interaction is not ready yet.",
    "root_only": "Policy controls are available only on the root node.",
}

INSPECTOR_STAGE_ORDER = ["request", "policy", "grant", "consensus", "result"]
INSPECTOR_STAGE_LABELS = {
    "request": "Request",
    "policy": "Policy",
    "grant": "Grant",
    "consensus": "Consensus",
    "result": "Result",
}
FLOW_TYPE_LABELS = {
    "access": "Access Request",
    "delegation": "Delegation",
    "revocation": "Revocation",
    "registration": "Registration",
    "validator": "Consensus",
    "cache": "Policy Cache",
    "policy": "Policy",
    "daemon": "System",
}


def _node_action_capabilities(node: dict[str, Any], node_views: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    signature = str(node.get("signature") or "").strip()
    api_status = str(((node.get("processes") or {}).get("api") or {}).get("status") or "")
    api_ready = api_status in {"ok", "running", "started"}
    other_signatures = [
        str(other.get("signature") or "").strip()
        for other in node_views
        if str(other.get("key") or "") != str(node.get("key") or "")
        and str(other.get("signature") or "").strip()
    ]

    def capability(enabled: bool, reason_key: str = "") -> dict[str, Any]:
        return {"enabled": bool(enabled), "reason": ACTION_REASON_LABELS.get(reason_key, "")}

    if not signature:
        return {
            "access": capability(False, "signature_missing"),
            "delegate": capability(False, "signature_missing"),
            "revoke_grant": capability(False, "signature_missing"),
            "grant_lookup": capability(False, "signature_missing"),
            "expiry_check": capability(False, "signature_missing"),
        }
    if not api_ready:
        return {
            "access": capability(False, "api_not_ready"),
            "delegate": capability(False, "api_not_ready"),
            "revoke_grant": capability(False, "api_not_ready"),
            "grant_lookup": capability(False, "api_not_ready"),
            "expiry_check": capability(False, "api_not_ready"),
        }

    has_target = bool(other_signatures)
    has_child = len(other_signatures) >= 2
    return {
        "access": capability(has_target, "no_target_nodes"),
        "delegate": capability(has_child, "no_child_nodes"),
        "revoke_grant": capability(has_target, "no_target_nodes"),
        "grant_lookup": capability(has_target, "no_target_nodes"),
        "expiry_check": capability(has_target, "no_target_nodes"),
    }


def _node_action_defaults(node: dict[str, Any], node_views: list[dict[str, Any]]) -> dict[str, Any]:
    others = [
        other
        for other in node_views
        if str(other.get("key") or "") != str(node.get("key") or "")
        and str(other.get("signature") or "").strip()
    ]
    primary_target = others[0] if others else {}
    secondary_target = others[1] if len(others) > 1 else primary_target
    return {
        "target_signature": str(primary_target.get("signature") or ""),
        "target_node_key": str(primary_target.get("key") or ""),
        "child_signature": str(secondary_target.get("signature") or ""),
        "child_node_key": str(secondary_target.get("key") or ""),
        "method": "GET",
        "resource_path": "/temperature",
        "expiry_secs": 900,
        "allow_delegation": False,
        "delegation_depth": 0,
        "ops_csv": "READ",
        "child_expiry_secs": 600,
        "policy_id": "",
        "ctx": "",
    }


def _node_policy_capabilities(node: dict[str, Any]) -> dict[str, dict[str, Any]]:
    api_status = str(((node.get("processes") or {}).get("api") or {}).get("status") or "")
    api_ready = api_status in {"ok", "running", "started"}
    is_root = str(node.get("key") or "") == "root" or str(node.get("tier") or "").lower() == "cloud"

    def capability(enabled: bool, reason_key: str = "") -> dict[str, Any]:
        return {"enabled": bool(enabled), "reason": ACTION_REASON_LABELS.get(reason_key, "")}

    keys = ("policy_create", "policy_find", "policy_get", "policy_update", "policy_deprecate")
    if not is_root:
        return {key: capability(False, "root_only") for key in keys}
    if not api_ready:
        return {key: capability(False, "api_not_ready") for key in keys}
    return {key: capability(True) for key in keys}


def _node_policy_defaults() -> dict[str, Any]:
    return {
        "from_role": "Edge",
        "to_role": "Fog",
        "ops_csv": "READ",
        "ctx_schema": "api:GET:/temperature",
        "policy_id": "",
    }


def _flow_source_target(events: list[dict[str, Any]]) -> tuple[str, str]:
    from_sig = ""
    to_sig = ""
    for event in events:
        if not from_sig and event.get("from_signature"):
            from_sig = str(event.get("from_signature") or "")
        if not to_sig and event.get("to_signature"):
            to_sig = str(event.get("to_signature") or "")
        if from_sig and to_sig:
            break
    return from_sig, to_sig


def _flow_matches_signature(events: list[dict[str, Any]], signature: str, direction: str) -> bool:
    if not signature:
        return False
    from_sig, to_sig = _flow_source_target(events)
    if direction == "outgoing":
        return from_sig == signature
    return to_sig == signature


def _map_business_stage(flow_type: str, stage: str) -> str:
    if stage in {"request_received", "rate_limit_check", "registration_validation", "role_resolution", "resource_context", "parent_grant_fetch", "delegation_preconditions", "revocation_resolution"}:
        return "request"
    if flow_type == "policy" or stage in {"policy_lookup", "policy_create"}:
        return "policy"
    if stage in {"grant_lookup", "grant_issue_or_reuse", "delegated_grant_verification"}:
        return "grant"
    if flow_type in {"validator", "cache"} or stage in {
        "validator_proposal",
        "validator_vote",
        "validator_inclusion_result",
        "peer_wait",
        "listener_started",
        "validator_wait",
        "revocation_propagation_wait",
        "revocation_propagation_observed",
    }:
        return "consensus"
    if stage.endswith("_finished") or stage in {"access_finished", "delegation_finished", "revocation_finished"}:
        return "result"
    if flow_type in {"access", "delegation", "revocation"}:
        return "request"
    return "request"


def _flow_stage_rows(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    stages = {key: "pending" for key in INSPECTOR_STAGE_ORDER}
    for event in events:
        key = _map_business_stage(str(event.get("flow_type") or ""), str(event.get("stage") or ""))
        stages[key] = _merge_phase_status(stages.get(key, "pending"), _event_status_to_phase_status(str(event.get("status") or "")))
    return [{"key": key, "label": INSPECTOR_STAGE_LABELS[key], "status": stages[key]} for key in INSPECTOR_STAGE_ORDER]


def _flow_stage_summary(stages: list[dict[str, str]]) -> dict[str, str]:
    for stage in stages:
        if stage["status"] == "error":
            return {"label": stage["label"], "status": "error"}
    for stage in reversed(stages):
        if stage["status"] == "active":
            return {"label": stage["label"], "status": "running"}
    for stage in reversed(stages):
        if stage["status"] == "waiting":
            return {"label": stage["label"], "status": "pending"}
    for stage in reversed(stages):
        if stage["status"] == "complete":
            return {"label": stage["label"], "status": "ok"}
    return {"label": "Request", "status": "down"}


def _flow_details_for_signature(
    flow_summaries: list[dict[str, Any]],
    *,
    node_signature: str,
    node_lookup_by_sig: dict[str, dict[str, Any]],
    direction: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for flow in flow_summaries:
        events = list(flow.get("events") or [])
        if not _flow_matches_signature(events, node_signature, direction):
            continue
        from_sig, to_sig = _flow_source_target(events)
        stages = _flow_stage_rows(events)
        summary = _flow_stage_summary(stages)
        source_node = node_lookup_by_sig.get(from_sig, {})
        target_node = node_lookup_by_sig.get(to_sig, {})
        rows.append({
            "flow_id": flow.get("flow_id"),
            "action": FLOW_TYPE_LABELS.get(str(flow.get("flow_type") or ""), str(flow.get("flow_type") or "Flow").title()),
            "direction": direction,
            "source_signature": from_sig,
            "target_signature": to_sig,
            "source_name": source_node.get("name") or (from_sig[:16] + "..." if from_sig else "Unknown"),
            "target_name": target_node.get("name") or (to_sig[:16] + "..." if to_sig else "Unknown"),
            "status": flow.get("final_status") or summary["status"],
            "current_stage": summary["label"],
            "stage_status": summary["status"],
            "duration_ms": flow.get("duration_ms"),
            "message": flow.get("message"),
            "stages": stages,
            "technical_events": [
                {
                    "ts_unix_ms": event.get("ts_unix_ms"),
                    "stage": event.get("stage"),
                    "status": event.get("status"),
                    "message": event.get("message"),
                    "policy_id": event.get("policy_id"),
                    "tx_hash": event.get("tx_hash"),
                }
                for event in events
            ],
        })
    rows.sort(key=lambda item: int(item.get("duration_ms") or 0), reverse=True)
    return rows[:12]


def _node_inspector_payload(
    *,
    infra_details: dict[str, Any],
    node_key: str,
    flow_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    scenario = infra_details.get("scenario") or {}
    node_views = list(scenario.get("node_views") or [])
    selected_node = next((node for node in node_views if str(node.get("key") or "") == str(node_key or "")), None)
    if not selected_node and node_views:
        selected_node = node_views[0]
    if not selected_node:
        return {"selected_scenario": infra_details.get("selected_scenario"), "node": None, "outgoing_flows": [], "incoming_flows": [], "latest_result": None}

    node_lookup_by_sig = {
        str(node.get("signature") or ""): node
        for node in node_views
        if str(node.get("signature") or "").strip()
    }
    signature = str(selected_node.get("signature") or "")
    options = [
        {
            "key": node.get("key"),
            "name": node.get("name"),
            "tier": node.get("tier"),
            "signature": node.get("signature"),
            "is_self": str(node.get("key") or "") == str(selected_node.get("key") or ""),
        }
        for node in node_views
        if str(node.get("signature") or "").strip()
    ]
    return {
        "selected_scenario": infra_details.get("selected_scenario"),
        "node": {
            "key": selected_node.get("key"),
            "name": selected_node.get("name"),
            "tier": selected_node.get("tier"),
            "node_id": selected_node.get("node_id"),
            "signature": signature,
            "api_url": selected_node.get("api_url"),
            "rpc_url": selected_node.get("rpc_url"),
            "p2p_port": selected_node.get("p2p_port"),
            "runtime_backend": selected_node.get("runtime_backend") or "",
            "simulated_device": selected_node.get("simulated_device") or "",
            "selected_device": selected_node.get("selected_device") or {},
            "summary_status": selected_node.get("summary_status"),
            "summary_label": selected_node.get("summary_label"),
            "action_capabilities": _node_action_capabilities(selected_node, node_views),
            "defaults": _node_action_defaults(selected_node, node_views),
            "policy_capabilities": _node_policy_capabilities(selected_node),
            "policy_defaults": _node_policy_defaults(),
            "target_options": options,
        },
        "outgoing_flows": _flow_details_for_signature(
            flow_summaries,
            node_signature=signature,
            node_lookup_by_sig=node_lookup_by_sig,
            direction="outgoing",
        ),
        "incoming_flows": _flow_details_for_signature(
            flow_summaries,
            node_signature=signature,
            node_lookup_by_sig=node_lookup_by_sig,
            direction="incoming",
        ),
        "latest_result": None,
    }


def _node_inspector_payload_from_cards(
    *,
    selected_scenario: str | None,
    node_cards: list[dict[str, Any]],
    node_key: str,
    flow_summaries: list[dict[str, Any]],
    pending_note: str | None = None,
) -> dict[str, Any]:
    cards = list(node_cards or [])
    selected_card = next((card for card in cards if str(card.get("key") or "") == str(node_key or "")), None)
    if not selected_card and cards:
        selected_card = cards[0]
    if not selected_card:
        return {
            "selected_scenario": selected_scenario,
            "node": None,
            "outgoing_flows": [],
            "incoming_flows": [],
            "latest_result": None,
        }

    node_lookup_by_sig = {
        str(card.get("signature") or ""): card
        for card in cards
        if str(card.get("signature") or "").strip()
    }
    signature = str(selected_card.get("signature") or "")
    options = [
        {
            "key": card.get("key"),
            "name": card.get("name"),
            "tier": card.get("tier"),
            "signature": card.get("signature"),
            "is_self": str(card.get("key") or "") == str(selected_card.get("key") or ""),
        }
        for card in cards
        if str(card.get("signature") or "").strip()
    ]
    payload = {
        "selected_scenario": selected_scenario,
        "node": {
            "key": selected_card.get("key"),
            "name": selected_card.get("name"),
            "tier": selected_card.get("tier"),
            "node_id": selected_card.get("node_id"),
            "signature": signature,
            "api_url": selected_card.get("api_url"),
            "rpc_url": selected_card.get("rpc_url"),
            "p2p_port": selected_card.get("p2p_port"),
            "runtime_backend": selected_card.get("runtime_backend") or "",
            "simulated_device": selected_card.get("simulated_device") or "",
            "selected_device": selected_card.get("selected_device") or {},
            "summary_status": selected_card.get("summary_status") or selected_card.get("stage_status"),
            "summary_label": selected_card.get("summary_label") or selected_card.get("stage"),
            "action_capabilities": selected_card.get("action_capabilities") or {},
            "defaults": selected_card.get("defaults") or {},
            "policy_capabilities": selected_card.get("policy_capabilities") or {},
            "policy_defaults": selected_card.get("policy_defaults") or {},
            "target_options": options,
        },
        "outgoing_flows": _flow_details_for_signature(
            flow_summaries,
            node_signature=signature,
            node_lookup_by_sig=node_lookup_by_sig,
            direction="outgoing",
        ) if signature else [],
        "incoming_flows": _flow_details_for_signature(
            flow_summaries,
            node_signature=signature,
            node_lookup_by_sig=node_lookup_by_sig,
            direction="incoming",
        ) if signature else [],
        "latest_result": None,
    }
    if pending_note:
        payload["pending_note"] = pending_note
    return payload


def _home_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>BlockCap Control</title>
  <style>
    :root { --bg:#f5f1e7; --card:#fffdf8; --ink:#172125; --muted:#627075; --line:#d6cec1; --accent:#1d5f88; --accent2:#9b4d22; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:"IBM Plex Sans","Segoe UI",sans-serif; background:linear-gradient(180deg,#ede4d4 0%,#f8f4eb 100%); color:var(--ink); }
    .wrap { max-width:1100px; margin:0 auto; padding:32px 24px; }
    .hero { background:var(--card); border:1px solid var(--line); border-radius:24px; padding:28px; box-shadow:0 10px 32px rgba(40,35,24,.08); }
    h1,h2 { margin:0 0 10px; } p { color:var(--muted); line-height:1.5; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:16px; margin-top:20px; }
    .card { background:var(--card); border:1px solid var(--line); border-radius:18px; padding:20px; }
    .btns { display:flex; flex-wrap:wrap; gap:10px; margin-top:16px; }
    a.btn { display:inline-block; text-decoration:none; color:white; background:var(--accent); padding:10px 14px; border-radius:12px; font-weight:700; }
    a.btn.alt { background:var(--accent2); }
    code { background:#f2eee3; padding:2px 6px; border-radius:8px; }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>BlockCap Web Operator</h1>
      <p>Operate BlockCap from the browser with a clearer split between topology lifecycle, node-to-node access control, and results observability.</p>
      <div class="btns">
        <a class="btn" href="/topology">Open Topology Page</a>
        <a class="btn alt" href="/control">Open Control Center</a>
        <a class="btn alt" href="/dashboard">Open Observability Dashboard</a>
      </div>
    </section>
    <div class="grid">
      <section class="card">
        <h2>Topology</h2>
        <p>Start, stop, and inspect simulated root, fog, edge, and endpoint nodes with device presets, runner output, live node status, and process actions.</p>
      </section>
      <section class="card">
        <h2>Control Center</h2>
        <p>Use browser forms to call <code>/access</code>, <code>/delegate</code>, <code>/revoke-grant</code>, <code>/grant</code>, <code>/expiry-check</code>, and the policy routes against the selected topology.</p>
      </section>
      <section class="card">
        <h2>Observability</h2>
        <p>Watch active flows, recent event timelines, flow details, counters, and latency metrics from the live event recorder.</p>
      </section>
    </div>
  </div>
</body>
</html>"""


def _simple_console_page_html(page_mode: str = "control") -> str:
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>__PAGE_TITLE__</title>
  <style>
    :root { --bg:#f4efe5; --panel:#fffdf9; --ink:#182127; --muted:#647177; --line:#d9d0c2; --primary:#1f5f87; --secondary:#8b4d27; --ok:#1f7a3c; --run:#2564a6; --warn:#b9770f; --error:#b12c2c; --down:#90979c; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--ink); font-family:"IBM Plex Sans","Segoe UI",sans-serif; }
    .wrap { max-width:1500px; margin:0 auto; padding:18px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:18px; padding:16px; margin-bottom:14px; }
    .hero { margin-bottom:14px; display:flex; justify-content:space-between; gap:12px; align-items:flex-start; flex-wrap:wrap; }
    .hero h1, .panel h2, .panel h3 { margin:0 0 10px; }
    .sub { color:var(--muted); font-size:13px; }
    .btns, .actions, .toolbar, .inline { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
    button, .btn { border:none; cursor:pointer; text-decoration:none; color:#fff; background:var(--primary); padding:10px 14px; border-radius:12px; font-weight:700; font:inherit; }
    button.alt, .btn.alt { background:var(--secondary); }
    button.ghost { background:#d8e6ef; color:#12384e; }
    button.soft { background:#eef2f5; color:#17364a; }
    button[disabled] { opacity:.55; cursor:not-allowed; }
    label { display:grid; gap:4px; align-content:start; font-size:13px; color:var(--muted); }
    input, select { width:100%; margin-top:0; padding:10px 11px; border-radius:10px; border:1px solid #d2c8b7; background:#fff; font:inherit; }
    input[type="checkbox"] { width:auto; }
    .top-grid { display:grid; gap:10px; grid-template-columns:repeat(auto-fit,minmax(120px,max-content)); align-items:end; justify-content:start; }
    .top-grid label { width:max-content; min-width:0; }
    .field-label { display:inline-flex; align-items:center; gap:8px; color:var(--ink); font-weight:600; }
    .tier-icon { width:18px; height:18px; display:inline-block; color:#1f5f87; }
    .tier-icon svg { width:18px; height:18px; display:block; }
    #root-count { width:72px; }
    #fog-count, #edge-count, #endpoint-count { width:88px; }
    #scenario-name { width:210px; }
    #topology-version { width:220px; }
    .device-matrix, .spawn-grid { display:grid; gap:12px; margin-top:14px; }
    .spawn-grid { grid-template-columns:1fr; }
    .spawn-card { background:#fff; border:1px solid var(--line); border-radius:16px; padding:14px; display:grid; grid-template-columns:minmax(180px,220px) minmax(240px,340px) minmax(320px,1fr) auto; gap:14px; align-items:start; }
    .spawn-card.root { grid-template-columns:minmax(220px,280px) minmax(420px,1fr) auto; }
    .spawn-header { display:grid; gap:8px; }
    .spawn-card h3 { margin:0; display:flex; align-items:center; gap:8px; }
    .spawn-inputs, .spawn-spec { display:grid; gap:10px; }
    .spawn-actions { display:flex; justify-content:flex-end; align-items:flex-start; }
    .terminal-mini-grid { display:grid; gap:10px; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); }
    .terminal-mini { border:1px solid var(--line); border-radius:12px; padding:10px; background:#fffefb; display:grid; gap:8px; }
    .terminal-mini-top { display:flex; justify-content:space-between; gap:8px; align-items:center; }
    .terminal-mini-top b { font-size:13px; }
    .terminal-mini-preview { display:grid; gap:6px; font-size:12px; color:var(--muted); }
    .terminal-mini-preview span { display:block; border-left:3px solid #d8e6ef; padding-left:8px; }
    .terminal-mini-status { display:inline-flex; align-items:center; gap:6px; padding:4px 8px; border-radius:999px; font-size:11px; font-weight:700; }
    .terminal-mini-status.running { background:#e9f7ee; color:#1f7a3c; }
    .terminal-mini-status.loading { background:#eef4fb; color:#1f5f87; }
    .terminal-mini-status.stopped { background:#f1f3f5; color:#66727a; }
    .device-row { display:grid; grid-template-columns:minmax(160px,180px) minmax(180px,240px) minmax(160px,220px) minmax(280px,1fr); gap:10px; align-items:start; background:#fff; border:1px solid var(--line); border-radius:14px; padding:12px; }
    .device-row.endpoint { grid-template-columns:minmax(160px,180px) minmax(150px,180px) minmax(180px,240px) minmax(280px,1fr); }
    .device-label { font-weight:700; display:flex; align-items:center; gap:8px; }
    .device-card { background:#fffefb; border:1px solid var(--line); border-radius:12px; padding:10px; font-size:12px; display:grid; gap:8px; }
    .device-card h4 { margin:0; font-size:13px; }
    .device-meta { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:6px 10px; }
    .device-meta b { display:block; color:var(--muted); margin-bottom:2px; }
    .device-note { font-size:12px; color:var(--muted); }
    .device-chip { display:inline-flex; align-items:center; gap:6px; padding:5px 8px; border-radius:999px; background:#eef2f5; color:#18313f; font-size:11px; font-weight:700; }
    .device-chip.runtime { background:#e6f4ea; color:#1f7a3c; }
    .device-reference { margin-top:12px; background:#fff8e7; border:1px solid #ebd4a5; border-radius:14px; padding:12px; }
    .device-reference-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; margin-top:10px; }
    .banner { display:none; margin-bottom:12px; padding:10px 12px; border-radius:12px; font-size:13px; }
    .banner.show { display:block; }
    .banner.ok { background:#e9f7ee; color:#1f7a3c; border:1px solid #b7dfc2; }
    .banner.error { background:#fdecec; color:#9f2424; border:1px solid #e8bcbc; }
    .summary { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-bottom:14px; }
    .summary-card { background:#fff; border:1px solid var(--line); border-radius:14px; padding:12px; }
    .summary-card b { display:block; margin-top:4px; font-size:18px; }
    .result-box { background:#101820; color:#d8f2dd; border-radius:14px; padding:12px; min-height:120px; border:1px solid #1f303d; }
    .terminal-meta { display:flex; gap:12px; align-items:center; justify-content:space-between; flex-wrap:wrap; margin-bottom:10px; }
    .terminal-command { min-height:auto; margin-bottom:10px; color:#9fd3ff; }
    .terminal-output { max-height:320px; overflow:auto; }
    .terminal-lines { display:grid; gap:6px; font-family:"IBM Plex Mono","SFMono-Regular",monospace; font-size:12px; }
    .terminal-line { display:grid; grid-template-columns:86px 90px 1fr; gap:10px; align-items:start; padding:4px 0; border-bottom:1px dashed rgba(159,211,255,.08); }
    .terminal-line:last-child { border-bottom:none; }
    .terminal-time { color:#91a8b8; }
    .terminal-kind { font-weight:700; letter-spacing:.04em; }
    .kind-exec .terminal-kind, .kind-flow .terminal-kind { color:#6fd3ff; }
    .kind-spawn .terminal-kind, .kind-start .terminal-kind { color:#8cbcff; }
    .kind-ready .terminal-kind { color:#74e39a; }
    .kind-warn .terminal-kind { color:#ffd27a; }
    .kind-error .terminal-kind { color:#ff8d8d; }
    .kind-http .terminal-kind { color:#d3d9de; }
    .terminal-message { color:#d8f2dd; white-space:pre-wrap; word-break:break-word; }
    pre { margin:0; white-space:pre-wrap; word-break:break-word; font-size:12px; font-family:"IBM Plex Mono","SFMono-Regular",monospace; }
    .node-strip { display:flex; gap:12px; overflow-x:auto; padding-bottom:6px; }
    .node-card { flex:0 0 290px; background:#fff; border:1px solid var(--line); border-radius:16px; padding:14px; cursor:pointer; transition:transform .14s ease, box-shadow .14s ease; }
    .node-card:hover { transform:translateY(-2px); box-shadow:0 10px 20px rgba(35,35,35,.08); }
    .node-card.selected { border-color:#1f5f87; box-shadow:0 0 0 2px rgba(31,95,135,.18); }
    .node-head { display:flex; gap:10px; align-items:flex-start; justify-content:space-between; margin-bottom:10px; }
    .tier-badge { width:38px; height:38px; border-radius:50%; background:#e7eff5; color:#12384e; display:grid; place-items:center; flex:none; }
    .tier-badge svg { width:22px; height:22px; display:block; }
    .stage-row { display:flex; align-items:center; gap:10px; margin-bottom:10px; }
    .stage-chip { display:inline-flex; align-items:center; gap:8px; padding:6px 10px; border-radius:999px; background:#eef2f5; color:#18313f; font-size:12px; font-weight:700; }
    .stage-chip.ok { background:#e6f4ea; color:#1f7a3c; }
    .stage-chip.running { background:#e5eef9; color:#225f9b; }
    .stage-chip.pending { background:#fff4df; color:#9c6907; }
    .stage-chip.error { background:#fdecec; color:#a92f2f; }
    .stage-chip.down { background:#edf0f2; color:#6c767d; }
    .phase-strip { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px 8px; margin-bottom:10px; }
    .phase-item { display:flex; flex-direction:column; gap:6px; align-items:center; text-align:center; min-width:0; }
    .phase-dot { width:14px; height:14px; border-radius:50%; background:#cfd6da; box-shadow:inset 0 0 0 2px rgba(255,255,255,.45); }
    .phase-dot.complete { background:var(--ok); }
    .phase-dot.active { background:var(--run); animation:pulseDot .95s infinite ease-in-out; box-shadow:0 0 0 0 rgba(34,95,155,.45), inset 0 0 0 2px rgba(255,255,255,.45); }
    .phase-dot.waiting { background:var(--warn); }
    .phase-dot.error { background:var(--error); }
    .phase-dot.pending, .phase-dot.not_applicable { background:var(--down); opacity:.55; }
    .phase-dot.stopping { background:var(--warn); animation:pulseDot .95s infinite ease-in-out; }
    .phase-label { font-size:10px; color:var(--muted); line-height:1.25; word-break:break-word; overflow-wrap:anywhere; }
    .dot-wave { display:inline-flex; gap:4px; align-items:center; }
    .dot-wave span { width:6px; height:6px; border-radius:50%; background:currentColor; opacity:.25; animation:waveBlink 1s infinite ease-in-out; }
    .dot-wave span:nth-child(2) { animation-delay:.18s; }
    .dot-wave span:nth-child(3) { animation-delay:.36s; }
    .url-list { display:grid; gap:6px; font-size:12px; color:var(--muted); margin-bottom:10px; }
    .small-actions { display:flex; gap:8px; flex-wrap:wrap; }
    .small-actions button { padding:7px 10px; border-radius:10px; font-size:12px; }
    .event-list { display:grid; gap:8px; }
    .event-item { background:#fff; border:1px solid var(--line); border-radius:12px; padding:10px; font-size:13px; }
    .event-top { display:flex; justify-content:space-between; gap:10px; color:var(--muted); font-size:12px; margin-bottom:4px; }
    .inspector-layer { position:fixed; inset:0; pointer-events:none; z-index:40; }
    .inspector { position:fixed; width:560px; max-width:calc(100vw - 32px); background:var(--panel); border:1px solid var(--line); border-radius:20px; box-shadow:0 16px 34px rgba(30,30,30,.18); pointer-events:auto; overflow:hidden; }
    .inspector.hidden { display:none; }
    .inspector-head { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; padding:14px 16px; background:#f2ede4; border-bottom:1px solid var(--line); cursor:move; }
    .inspector-body { padding:16px; max-height:calc(100vh - 180px); overflow:auto; }
    .inspector-grid { display:grid; gap:14px; }
    .inspector-meta { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px 12px; font-size:12px; color:var(--muted); }
    .inspector-meta > div, .summary-grid > div { min-width:0; }
    .inspector-meta b, .summary-grid b { display:block; margin-bottom:2px; }
    .inspector-meta > div > div, .summary-grid > div > div { overflow-wrap:anywhere; word-break:break-word; white-space:pre-wrap; }
    .inspector-box { background:#fff; border:1px solid var(--line); border-radius:14px; padding:12px; }
    .inspector-sections { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; }
    .inspector-toolbar { display:flex; gap:8px; flex-wrap:wrap; }
    .inspector-toolbar button.active, .tabbar button.active { background:var(--primary); color:#fff; }
    .inspector-form { display:grid; gap:10px; }
    .inspector-form .row { display:grid; gap:10px; grid-template-columns:repeat(2,minmax(0,1fr)); }
    .summary-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px 12px; font-size:12px; }
    .summary-grid b { display:block; color:var(--muted); font-weight:600; margin-bottom:2px; }
    .flow-tabs, .tabbar { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; }
    .flow-row { border:1px solid var(--line); border-radius:12px; padding:10px; background:#fffefb; margin-bottom:8px; }
    .flow-top { display:flex; justify-content:space-between; gap:10px; align-items:center; margin-bottom:8px; }
    .flow-route { font-weight:700; }
    .flow-stages { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:6px; margin-bottom:8px; }
    .flow-stage { display:flex; flex-direction:column; align-items:center; gap:4px; font-size:10px; color:var(--muted); text-align:center; }
    .flow-dot { width:12px; height:12px; border-radius:50%; background:#cfd6da; }
    .flow-dot.complete { background:var(--ok); }
    .flow-dot.active { background:var(--run); }
    .flow-dot.waiting { background:var(--warn); }
    .flow-dot.error { background:var(--error); }
    .flow-dot.pending { background:var(--down); opacity:.55; }
    details.tech { border:1px solid var(--line); border-radius:12px; padding:10px; background:#fff; }
    details.tech summary { cursor:pointer; font-weight:700; }
    .tech-event { border-top:1px dashed var(--line); padding-top:8px; margin-top:8px; font-size:12px; }
    .muted-note { background:#fff8e7; border:1px solid #ebd4a5; color:#6a4b00; padding:10px 12px; border-radius:12px; font-size:13px; }
    __MODE_CSS__
    @media (max-width: 900px) {
      .inspector { width:calc(100vw - 20px); left:10px !important; right:10px !important; }
      .inspector-form .row, .summary-grid, .inspector-meta { grid-template-columns:1fr; }
      .spawn-card, .spawn-card.root { grid-template-columns:1fr; }
      .spawn-actions { justify-content:flex-start; }
    }
    @keyframes pulseDot { 0%,100% { transform:scale(.9); opacity:.55; } 50% { transform:scale(1.15); opacity:1; } }
    @keyframes waveBlink { 0%, 80%, 100% { transform:scale(.75); opacity:.2; } 40% { transform:scale(1.25); opacity:1; } }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div>
        <h1>__HERO_TITLE__</h1>
        <div class="sub">__HERO_SUBTITLE__</div>
      </div>
      <div class="btns">
        <a class="btn" href="/topology">Topology</a>
        <a class="btn alt" href="/control">Control</a>
        <a class="btn" href="/results">Results</a>
        <a class="btn alt" href="/">Home</a>
      </div>
    </section>

    <section class="panel">
      <h2 class="topology-only">1. Live Topology</h2>
      <h2 class="control-only">1. Scenario Selection</h2>
      <div class="sub topology-only" style="margin-bottom:12px;">Start Root first, then spawn Fog, Edge, and Endpoint nodes one at a time into the live topology. Endpoint keeps both role and device selection. Retired nodes are hidden from this page.</div>
      <div class="sub control-only" style="margin-bottom:12px;">Choose which topology version the control surface should target. Topology lifecycle, device simulation, runner logs, and process terminals now live on <code>/topology</code>.</div>
      <div id="banner" class="banner"></div>
      <div class="top-grid control-only">
        __SCENARIO_FIELD__
        __TOPOLOGY_VERSION_FIELD__
      </div>
      <div id="topology-spawn-grid" class="spawn-grid topology-only"></div>
      <div class="device-reference topology-only">
        <b>IETF Constrained Endpoint Reference</b>
        <div class="sub" style="margin-top:6px;">RFC 7228 constrained classes are shown for reference only. The current BlockCap endpoint runtime requires Linux-capable devices, so runnable endpoint presets in this UI are all above class C2.</div>
        <div id="endpoint-reference" class="device-reference-grid"></div>
      </div>
      <div class="actions topology-only" style="margin-top:12px;">
        <button type="button" class="alt" onclick="stopLiveTopology()">Stop Live Topology</button>
      </div>
      <div class="actions control-only" style="margin-top:12px;">
        <button type="button" class="ghost" onclick="clearSelection()">Clear Selection</button>
        <a class="btn alt" href="/topology">Open Topology Page</a>
      </div>
    </section>

    <div id="summary" class="summary"></div>

    <section class="panel topology-only">
      <h2>2. Main Terminal</h2>
      <div class="sub" style="margin-bottom:12px;">Shows the main topology runner command and live execution output. This is separate from the API and chain terminals of individual nodes.</div>
      <div class="terminal-meta">
        <div class="sub">Topology runner shell</div>
        <div id="main-terminal-state" class="sub">Idle</div>
      </div>
      <div id="main-terminal-command" class="result-box terminal-command"><pre>$ No topology job has been started yet.</pre></div>
      <div class="result-box terminal-output"><div id="main-terminal-output" class="terminal-lines"><div class="terminal-line kind-info"><span class="terminal-time">--:--:--</span><span class="terminal-kind">INFO</span><span class="terminal-message">[runner] waiting for a topology job...</span></div></div></div>
    </section>

    <section class="panel">
      <h2 class="topology-only">3. Nodes</h2>
      <h2 class="control-only">2. Nodes</h2>
      <div class="sub topology-only" style="margin-bottom:12px;">Click a node card to open its node inspector. Use the three buttons for API, chain, and stop actions.</div>
      <div class="sub topology-only" style="margin-bottom:12px;">Phase dots progress through API, Chain, Registration, Access, and Consensus.</div>
      <div class="sub control-only" style="margin-bottom:12px;">Click a node card to open access control and policy actions for the selected topology version.</div>
      <div id="node-strip" class="node-strip"></div>
    </section>

    <section class="panel">
      <h2>Recent Activity</h2>
      <div id="recent-events" class="event-list"></div>
    </section>
  </div>

  <div class="inspector-layer">
    <section id="node-inspector" class="inspector hidden" style="top:120px; right:18px;">
      <div id="inspector-head" class="inspector-head">
        <div id="inspector-title"><b>Node Inspector</b><div class="sub">Select a node</div></div>
        <div class="actions">
          <button type="button" class="ghost" onclick="closeInspector()">Close</button>
        </div>
      </div>
      <div class="inspector-body" id="inspector-body"></div>
    </section>
  </div>

  <script>
    const PAGE_MODE = "__PAGE_MODE__";
    const CONTROL_DATA_ENDPOINT = PAGE_MODE === 'topology' ? '/topology/data' : '/control/data';
    const INSPECTOR_ENDPOINT = PAGE_MODE === 'topology' ? '/topology/node-inspector' : '/control/node-inspector';
    const DEVICE_CATALOG = __DEVICE_CATALOG_JSON__;
    const STORAGE_KEYS = {
      selectedScenario: 'blockcap.control.selectedScenario',
      selectionMode: 'blockcap.control.selectionMode',
    };
    const state = {
      selectedScenario: '',
      selectionMode: 'auto',
      details: null,
      pendingStart: null,
      starting: {},
      stopping: {},
      refreshSeq: 0,
      refreshTimer: null,
      inspector: {
        open: false,
        nodeKey: '',
        activeSection: 'access',
        activeAction: 'access',
        flowTab: 'outgoing',
        results: {},
        data: null,
        drag: null,
        refreshSeq: 0,
        refreshTimer: null,
      },
    };
    const esc = (v) => String(v ?? '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    const banner = document.getElementById('banner');
    const scenarioInputEl = () => document.getElementById('scenario-name');
    const scenarioInputValue = () => {
      const node = scenarioInputEl();
      return node ? node.value.trim() : '';
    };
    const setScenarioInputValue = (value) => {
      const node = scenarioInputEl();
      if (node) node.value = value || '';
    };

    function loadSelectionState() {
      try {
        state.selectedScenario = window.localStorage.getItem(STORAGE_KEYS.selectedScenario) || '';
        state.selectionMode = window.localStorage.getItem(STORAGE_KEYS.selectionMode) || (state.selectedScenario ? 'manual' : 'auto');
      } catch (_err) {
        state.selectedScenario = '';
        state.selectionMode = 'auto';
      }
    }

    function persistSelectionState() {
      try {
        if (state.selectedScenario) {
          window.localStorage.setItem(STORAGE_KEYS.selectedScenario, state.selectedScenario);
        } else {
          window.localStorage.removeItem(STORAGE_KEYS.selectedScenario);
        }
        window.localStorage.setItem(STORAGE_KEYS.selectionMode, state.selectionMode || 'auto');
      } catch (_err) {
        // no-op
      }
    }

    function tierIcon(tier) {
      const stroke = '#1f5f87';
      const fill = '#e7eff5';
      const icons = {
        cloud: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 18h10a4 4 0 0 0 .5-7.97A5.5 5.5 0 0 0 7.1 8.6 4 4 0 0 0 7 18Z" fill="${fill}" stroke="${stroke}" stroke-width="1.7" stroke-linejoin="round"/></svg>`,
        fog: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 9.5h14M4 13h16M6 16.5h12" stroke="${stroke}" stroke-width="2" stroke-linecap="round"/><circle cx="8" cy="7" r="2" fill="${fill}" stroke="${stroke}" stroke-width="1.5"/><circle cx="16" cy="7" r="2" fill="${fill}" stroke="${stroke}" stroke-width="1.5"/></svg>`,
        edge: `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="5" width="14" height="10" rx="2" fill="${fill}" stroke="${stroke}" stroke-width="1.7"/><path d="M9 19h6M12 15v4" stroke="${stroke}" stroke-width="1.7" stroke-linecap="round"/></svg>`,
        endpoint: `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="4.5" width="8" height="15" rx="2.2" fill="${fill}" stroke="${stroke}" stroke-width="1.7"/><circle cx="12" cy="16.5" r="1" fill="${stroke}"/></svg>`,
      };
      return icons[tier] || icons.endpoint;
    }

    function renderStaticTierIcons() {
      document.querySelectorAll('[data-tier-icon]').forEach((node) => {
        node.innerHTML = tierIcon(node.getAttribute('data-tier-icon'));
      });
    }

    function showBanner(kind, message) {
      banner.className = `banner show ${kind}`;
      banner.textContent = message;
    }

    function clearBanner() {
      banner.className = 'banner';
      banner.textContent = '';
    }

    function presetById(tier, presetId) {
      const rows = ((DEVICE_CATALOG.tiers || {})[tier] || []);
      return rows.find((row) => row && row.id === presetId) || rows[0] || null;
    }

    function specCardMarkup(preset) {
      if (!preset) return '<div class="device-card"><div class="device-note">No device preset selected.</div></div>';
      const specs = preset.official_specs || {};
      return `<div class="device-card">
        <h4>${esc(preset.label)}</h4>
        <div>
          <div class="device-meta">
            <div><b>CPU</b><div>${esc(specs.cpu || '-')}</div></div>
            <div><b>RAM</b><div>${esc(specs.ram || '-')}</div></div>
            <div><b>Storage / Boot</b><div>${esc(specs.storage || '-')}</div></div>
            <div><b>Networking</b><div>${esc(specs.networking || '-')}</div></div>
            <div><b>Power class</b><div>${esc(specs.power || '-')}</div></div>
            <div><b>IETF class</b><div>${esc(specs.ietf_class_relation || preset.ietf_class_badge || '-')}</div></div>
          </div>
        </div>
      </div>`;
    }

    function renderEndpointReference() {
      const holder = document.getElementById('endpoint-reference');
      const ref = DEVICE_CATALOG.endpoint_reference || {};
      const classRows = (ref.classes || []).map((row) => `<div class="device-card"><h4>${esc(row.class)}</h4><div class="device-meta"><div><b>RAM</b><div>${esc(row.ram || '-')}</div></div><div><b>Code</b><div>${esc(row.code || '-')}</div></div></div></div>`);
      const deviceRows = (ref.devices || []).map((row) => `<div class="device-card"><h4>${esc(row.label)}</h4><div class="device-meta"><div><b>CPU</b><div>${esc(row.cpu || '-')}</div></div><div><b>RAM</b><div>${esc(row.ram || '-')}</div></div><div><b>Storage</b><div>${esc(row.storage || '-')}</div></div><div><b>Networking</b><div>${esc(row.networking || '-')}</div></div></div><div class="device-note">${esc(row.note || '')}</div></div>`);
      holder.innerHTML = classRows.concat(deviceRows).join('');
    }

    function updateDeviceCard(rowEl) {
      const tier = rowEl.getAttribute('data-tier');
      const select = rowEl.querySelector('[data-device-select]');
      const preset = presetById(tier, select ? select.value : '');
      const card = rowEl.querySelector('[data-device-card]');
      if (card) card.innerHTML = specCardMarkup(preset);
    }

    function deviceOptionsMarkup(tier, selectedId) {
      const rows = ((DEVICE_CATALOG.tiers || {})[tier] || []);
      return rows.map((row) => `<option value="${esc(row.id)}" ${row.id === selectedId ? 'selected' : ''}>${esc(row.label)}</option>`).join('');
    }

    function buildSpawnControls() {
      const holder = document.getElementById('topology-spawn-grid');
      if (!holder) return;
      holder.innerHTML = `
        <div class="spawn-card root" data-spawn-tier="cloud">
          <div class="spawn-header">
            <h3><span class="tier-icon">${tierIcon('cloud')}</span>Root</h3>
          </div>
          <div class="spawn-spec" id="root-live-shells"></div>
          <div class="actions spawn-actions"><button type="button" id="start-root-btn" onclick="startRoot()">Start Root</button></div>
        </div>
        <div class="spawn-card" data-spawn-tier="fog">
          <div class="spawn-header">
            <h3><span class="tier-icon">${tierIcon('fog')}</span>Fog</h3>
            <div class="device-note">Chain-backed fog node. The first fog spawned into a fresh topology remains the validator candidate.</div>
          </div>
          <div class="spawn-inputs">
            <label><span class="field-label">Simulated Device</span><select id="fog-device-select">${deviceOptionsMarkup('fog', (DEVICE_CATALOG.defaults || {}).fog || '')}</select></label>
          </div>
          <div class="spawn-spec" id="fog-device-card"></div>
          <div class="actions spawn-actions"><button type="button" id="spawn-fog-btn" onclick="spawnNode('fog')">Spawn Fog</button></div>
        </div>
        <div class="spawn-card" data-spawn-tier="edge">
          <div class="spawn-header">
            <h3><span class="tier-icon">${tierIcon('edge')}</span>Edge</h3>
            <div class="device-note">Chain-backed edge node with a local API service and local Besu process.</div>
          </div>
          <div class="spawn-inputs">
            <label><span class="field-label">Simulated Device</span><select id="edge-device-select">${deviceOptionsMarkup('edge', (DEVICE_CATALOG.defaults || {}).edge || '')}</select></label>
          </div>
          <div class="spawn-spec" id="edge-device-card"></div>
          <div class="actions spawn-actions"><button type="button" id="spawn-edge-btn" onclick="spawnNode('edge')">Spawn Edge</button></div>
        </div>
        <div class="spawn-card" data-spawn-tier="endpoint">
          <div class="spawn-header">
            <h3><span class="tier-icon">${tierIcon('endpoint')}</span>Endpoint</h3>
            <div class="device-note">API-only endpoint node. Choose both the functional role and the simulated Linux-capable device preset.</div>
          </div>
          <div class="spawn-inputs">
            <label><span class="field-label">Role</span><select id="endpoint-role-select"><option value="Sensor">Sensor</option><option value="Actuator">Actuator</option></select></label>
            <label><span class="field-label">Simulated Device</span><select id="endpoint-device-select">${deviceOptionsMarkup('endpoint', (DEVICE_CATALOG.defaults || {}).endpoint || '')}</select></label>
          </div>
          <div class="spawn-spec" id="endpoint-device-card"></div>
          <div class="actions spawn-actions"><button type="button" id="spawn-endpoint-btn" onclick="spawnNode('endpoint')">Spawn Endpoint</button></div>
        </div>`;
      ['fog', 'edge', 'endpoint'].forEach((tier) => {
        const select = document.getElementById(`${tier}-device-select`);
        if (select) {
          const render = () => {
            const target = document.getElementById(`${tier}-device-card`);
            if (target) target.innerHTML = specCardMarkup(presetById(tier, select.value));
          };
          select.addEventListener('change', render);
          render();
        }
      });
      renderEndpointReference();
    }

    function renderSpawnControls(payload) {
      if (PAGE_MODE !== 'topology') return;
      const controls = (payload && payload.spawn_controls) || {};
      const nodeCards = ((payload && payload.node_cards) || []).length
        ? (payload.node_cards || [])
        : ((((state.pendingStart || {}).node_cards) || []));
      const rootCard = (nodeCards || []).find((card) => card && card.key === 'root') || null;
      const rootTerminals = (payload && payload.root_terminals) || {};
      const setDisabled = (id, disabled) => {
        const node = document.getElementById(id);
        if (node) node.disabled = Boolean(disabled);
      };
      setDisabled('start-root-btn', !controls.root_enabled);
      setDisabled('spawn-fog-btn', !controls.fog_enabled);
      setDisabled('spawn-edge-btn', !controls.edge_enabled);
      setDisabled('spawn-endpoint-btn', !controls.endpoint_enabled);
      const rootHolder = document.getElementById('root-live-shells');
      if (rootHolder) {
        const renderProcess = (process, title) => {
          const isRunning = Boolean(rootCard && rootCard[`${process}_running`]);
          const isReady = Boolean(rootCard && rootCard[`${process}_ready`]);
          const isStarting = Boolean(state.starting[`root:${process}`]);
          const isStopping = Boolean(state.stopping[`root:${process}`]);
          const preview = ((rootTerminals[process] || {}).preview_lines || []);
          let statusClass = 'stopped';
          let statusLabel = 'Not Running';
          let previewHtml = '<span>Not Running</span>';
          let buttonHtml = '';
          const hasRoot = Boolean(rootCard && currentScenarioName(payload || state.details || {}));
          if (isStarting) {
            statusClass = 'loading';
            statusLabel = 'Starting';
            previewHtml = `<span>Starting ${esc(title)}...</span>`;
          } else if (isStopping) {
            statusClass = 'loading';
            statusLabel = 'Stopping';
            previewHtml = `<span>Stopping ${esc(title)}...</span>`;
          } else if (isRunning && !isReady) {
            statusClass = 'loading';
            statusLabel = 'Loading';
            previewHtml = `<span>Loading ${esc(title)}...</span>`;
          } else if (isRunning && isReady) {
            statusClass = 'running';
            statusLabel = 'Running';
            previewHtml = preview.length
              ? preview.map((line) => `<span>${esc(line)}</span>`).join('')
              : '<span>Waiting for terminal output...</span>';
            buttonHtml = `<button type="button" class="ghost" onclick="return openTerminalFromButton(event, 'root', '${esc(process)}', true)">Open ${esc(title)}</button>`;
            buttonHtml += ` <button type="button" class="ghost" ${isStopping ? 'disabled' : ''} onclick="return stopProcessFromButton(event, 'root', '${esc(process)}')">Stop ${esc(title)}</button>`;
          } else if (preview.length) {
            previewHtml = preview.map((line) => `<span>${esc(line)}</span>`).join('');
          }
          if (!isRunning && !isStarting && hasRoot) {
            buttonHtml = `<button type="button" class="ghost" onclick="return startProcessFromButton(event, 'root', '${esc(process)}')">Start ${esc(title)}</button>`;
          }
          return `
            <div class="terminal-mini">
              <div class="terminal-mini-top">
                <b>${esc(title)}</b>
                <div class="inline">
                  <span class="terminal-mini-status ${esc(statusClass)}">${esc(statusLabel)}</span>
                  ${buttonHtml}
                </div>
              </div>
              <div class="terminal-mini-preview">${previewHtml}</div>
            </div>`;
        };
        rootHolder.innerHTML = `<div class="terminal-mini-grid">${renderProcess('api', 'API')}${renderProcess('chain', 'Chain')}</div>`;
      }
    }

    function buildDeviceMatrix() {
      if (!document.getElementById('fog-count') || !document.getElementById('device-matrix')) return;
      document.getElementById('root-count').value = '1';
      const fogCount = Math.max(0, Number(document.getElementById('fog-count').value || 0));
      const edgeCount = Math.max(0, Number(document.getElementById('edge-count').value || 0));
      const endpointCount = Math.max(0, Number(document.getElementById('endpoint-count').value || 0));
      const holder = document.getElementById('device-matrix');
      const previousRoles = Array.from(document.querySelectorAll('[data-endpoint-role]')).map((node) => node.value);
      const previousFogDevices = Array.from(document.querySelectorAll('[data-fog-device]')).map((node) => node.value);
      const previousEdgeDevices = Array.from(document.querySelectorAll('[data-edge-device]')).map((node) => node.value);
      const previousEndpointDevices = Array.from(document.querySelectorAll('[data-endpoint-device]')).map((node) => node.value);
      holder.innerHTML = '';

      for (let i = 0; i < fogCount; i += 1) {
        const selectedId = previousFogDevices[i] || (DEVICE_CATALOG.defaults || {}).fog || '';
        const row = document.createElement('div');
        row.className = 'device-row';
        row.setAttribute('data-tier', 'fog');
        row.innerHTML = `
          <div class="device-label"><span class="tier-icon">${tierIcon('fog')}</span>Fog ${i + 1}</div>
          <label><span class="field-label">Device</span><select data-device-select data-fog-device>${deviceOptionsMarkup('fog', selectedId)}</select></label>
          <div class="device-note">Chain-backed fog node. First fog node remains the validator candidate in the current topology runner.</div>
          <div data-device-card></div>`;
        holder.appendChild(row);
        updateDeviceCard(row);
      }

      for (let i = 0; i < edgeCount; i += 1) {
        const selectedId = previousEdgeDevices[i] || (DEVICE_CATALOG.defaults || {}).edge || '';
        const row = document.createElement('div');
        row.className = 'device-row';
        row.setAttribute('data-tier', 'edge');
        row.innerHTML = `
          <div class="device-label"><span class="tier-icon">${tierIcon('edge')}</span>Edge ${i + 1}</div>
          <label><span class="field-label">Device</span><select data-device-select data-edge-device>${deviceOptionsMarkup('edge', selectedId)}</select></label>
          <div class="device-note">Chain-backed edge node with local Besu and API services.</div>
          <div data-device-card></div>`;
        holder.appendChild(row);
        updateDeviceCard(row);
      }

      for (let i = 0; i < endpointCount; i += 1) {
        const selectedRole = previousRoles[i] || 'Sensor';
        const selectedId = previousEndpointDevices[i] || (DEVICE_CATALOG.defaults || {}).endpoint || '';
        const row = document.createElement('div');
        row.className = 'device-row endpoint';
        row.setAttribute('data-tier', 'endpoint');
        row.innerHTML = `
          <div class="device-label"><span class="tier-icon">${tierIcon('endpoint')}</span>Endpoint ${i + 1}</div>
          <label><span class="field-label">Role</span><select data-endpoint-role><option value="Sensor" ${selectedRole === 'Sensor' ? 'selected' : ''}>Sensor</option><option value="Actuator" ${selectedRole === 'Actuator' ? 'selected' : ''}>Actuator</option></select></label>
          <label><span class="field-label">Device</span><select data-device-select data-endpoint-device>${deviceOptionsMarkup('endpoint', selectedId)}</select></label>
          <div data-device-card></div>`;
        holder.appendChild(row);
        updateDeviceCard(row);
      }

      holder.querySelectorAll('[data-device-select]').forEach((node) => {
        node.addEventListener('change', (event) => updateDeviceCard(event.target.closest('.device-row')));
      });
      renderEndpointReference();
    }

    function selectedScenarioQuery() {
      if (state.selectionMode === 'manual' && state.selectedScenario) {
        return `?scenario=${encodeURIComponent(state.selectedScenario)}`;
      }
      return '';
    }

    function currentScenarioName(payload) {
      if (state.selectionMode === 'manual' && state.selectedScenario) return state.selectedScenario;
      return (payload && payload.selected_scenario) || state.selectedScenario || '';
    }

    function renderTopologyVersions(payload) {
      const select = document.getElementById('topology-version');
      const scenarioInput = scenarioInputEl();
      if (!select) {
        if (scenarioInput && !scenarioInput.value && payload.suggested_scenario) {
          scenarioInput.value = payload.suggested_scenario;
        }
        return;
      }
      const scenarios = (payload.scenarios || []).filter((row) => row && row.scenario);
      const available = new Set(scenarios.map((row) => row.scenario));
      if (state.selectionMode === 'manual' && state.selectedScenario && !available.has(state.selectedScenario)) {
        state.selectedScenario = '';
        state.selectionMode = 'auto';
        persistSelectionState();
      }
      const selected = state.selectionMode === 'manual' ? state.selectedScenario : ((payload && payload.selected_scenario) || '');
      select.innerHTML = `<option value="">None selected</option>` + scenarios.map((row) => {
        const status = row.running ? 'running' : (row.exists ? 'stopped' : 'incomplete');
        return `<option value="${esc(row.scenario)}">${esc(`${row.scenario} | ${status}`)}</option>`;
      }).join('');
      select.value = scenarios.some((row) => row.scenario === selected) ? selected : '';
      if (scenarioInput && selected) {
        scenarioInput.value = selected;
      } else if (scenarioInput && !scenarioInput.value && payload.suggested_scenario) {
        scenarioInput.value = payload.suggested_scenario;
      }
    }

    function renderSummary(payload) {
      const cards = [
        ['Scenario', currentScenarioName(payload) || '-'],
        ['Mode', PAGE_MODE === 'topology' ? 'live' : state.selectionMode],
        ['Nodes', (payload.scenario && payload.scenario.node_count) || ((payload.node_cards && payload.node_cards.length) || 0)],
        ['Deployed', payload.health ? payload.health.deployed : false],
        ['Validator', payload.health ? payload.health.is_validator : false],
        ['Events', payload.event_stats ? payload.event_stats.total_events || 0 : 0],
      ];
      document.getElementById('summary').innerHTML = cards.map(([label, value]) => `<div class="summary-card"><div class="sub">${esc(label)}</div><b>${esc(value)}</b></div>`).join('');
    }

    function classifyRunnerLine(line) {
      const text = String(line || '');
      const lower = text.toLowerCase();
      if (text.startsWith('[exec]')) return { kind: 'exec', label: 'EXEC', message: text.replace('[exec]', '').trim() };
      if (text.startsWith('[spawn]')) return { kind: 'spawn', label: 'SPAWN', message: text.replace('[spawn]', '').trim() };
      if (lower.includes('traceback') || lower.includes('runtimeerror') || lower.includes(' failed') || lower.includes('exception')) return { kind: 'error', label: 'ERROR', message: text };
      if (lower.includes('warning') || lower.includes('deprecated')) return { kind: 'warn', label: 'WARN', message: text };
      if (lower.includes('register')) return { kind: 'flow', label: 'REG', message: text };
      if (lower.includes('access') || lower.includes('delegate') || lower.includes('revoke') || lower.includes('grant') || lower.includes('policy')) return { kind: 'flow', label: 'FLOW', message: text };
      if (lower.includes('ready') || lower.includes('already reachable') || lower.includes('healthy')) return { kind: 'ready', label: 'READY', message: text };
      if (lower.includes('starting') || lower.includes('initializing') || lower.includes('provisioning')) return { kind: 'start', label: 'START', message: text };
      if (text.includes('HTTP/1.1')) return { kind: 'http', label: 'HTTP', message: text };
      return { kind: 'info', label: 'INFO', message: text };
    }

    function prettyRunnerLine(line) {
      const item = classifyRunnerLine(line);
      const time = new Date().toLocaleTimeString();
      return `<div class="terminal-line kind-${esc(item.kind)}"><span class="terminal-time">${esc(time)}</span><span class="terminal-kind">${esc(item.label)}</span><span class="terminal-message">${esc(item.message)}</span></div>`;
    }

    function renderMainTerminal(job) {
      const stateEl = document.getElementById('main-terminal-state');
      const commandEl = document.getElementById('main-terminal-command');
      const outputEl = document.getElementById('main-terminal-output');
      if (!job) {
        stateEl.textContent = 'Idle';
        commandEl.innerHTML = '<pre>$ No topology job has been started yet.</pre>';
        outputEl.innerHTML = `<div class="terminal-line kind-info"><span class="terminal-time">--:--:--</span><span class="terminal-kind">INFO</span><span class="terminal-message">[runner] waiting for a topology job...</span></div>`;
        return;
      }
      const exitCode = job.exit_code == null ? '-' : job.exit_code;
      stateEl.textContent = `status=${job.status || '-'} | exit=${exitCode}`;
      commandEl.innerHTML = `<pre>$ ${esc(job.runner_command || 'Topology command not available yet.')}</pre>`;
      const lines = (job.log_lines || []);
      outputEl.innerHTML = lines.length
        ? lines.map((line) => prettyRunnerLine(line)).join('')
        : `<div class="terminal-line kind-info"><span class="terminal-time">--:--:--</span><span class="terminal-kind">INFO</span><span class="terminal-message">[runner] waiting for output...</span></div>`;
      outputEl.parentElement.scrollTop = outputEl.parentElement.scrollHeight;
    }

    function clearInspectorRefreshTimer() {
      if (state.inspector.refreshTimer) {
        clearTimeout(state.inspector.refreshTimer);
        state.inspector.refreshTimer = null;
      }
    }

    function scheduleInspectorRefresh() {
      clearInspectorRefreshTimer();
      if (!state.inspector.open || !state.inspector.nodeKey) return;
      state.inspector.refreshTimer = setTimeout(() => {
        state.inspector.refreshTimer = null;
        refreshInspector();
      }, 400);
    }

    function scheduleRefresh(payload) {
      if (state.refreshTimer) {
        clearTimeout(state.refreshTimer);
        state.refreshTimer = null;
      }
      const running = Boolean(payload && payload.job && payload.job.status === 'running');
      const delayMs = running ? 500 : 2000;
      state.refreshTimer = setTimeout(() => {
        state.refreshTimer = null;
        refresh();
      }, delayMs);
    }

    function withTransientStopState(card) {
      const process = state.stopping[card.key];
      if (!process) return card;
      const phases = (card.phases || []).map((phase) => {
        if (process === 'node' && (phase.key === 'api' || phase.key === 'chain')) return { ...phase, status: 'stopping' };
        return phase;
      });
      return { ...card, stage: 'Stopping API + Chain', stage_status: 'pending', phases };
    }

    function renderStageChip(card) {
      if (!state.stopping[card.key]) return `<span class="stage-chip ${esc(card.stage_status)}">${esc(card.stage)}</span>`;
      return `<span class="stage-chip pending">Stopping API + Chain <span class="dot-wave" aria-hidden="true"><span></span><span></span><span></span></span></span>`;
    }

    function renderNodes(cards) {
      const root = document.getElementById('node-strip');
      if (!(cards || []).length) {
        root.innerHTML = '<div class="sub">No topology is running yet.</div>';
        return;
      }
      root.innerHTML = cards.map((rawCard) => {
        const card = withTransientStopState(rawCard);
        const nodePageButton = card.runtime_backend === 'container'
          ? `<button type="button" class="ghost" ${(card.control_url && card.node_page_ready) ? '' : 'disabled'} onclick="return openNodePageFromButton(event, '${esc(card.control_url || '')}')">${card.node_page_ready ? 'Open Node Page' : 'Node Page Loading'}</button>`
          : '';
        return `
        <div class="node-card ${state.inspector.nodeKey === card.key ? 'selected' : ''}" onclick="openInspector('${esc(card.key)}')">
          <div class="node-head">
            <div style="display:flex; gap:10px;">
              <div class="tier-badge">${tierIcon(card.tier)}</div>
              <div>
                <div><b>${esc(card.name)}</b></div>
                <div class="sub">${esc(card.node_id || '')} | ${esc((card.tier || '').toUpperCase())}</div>
                ${card.simulated_device ? `<div class="sub">${esc(card.simulated_device)} | ${esc(card.runtime_backend || 'container')}</div>` : ''}
              </div>
            </div>
          </div>
          <div class="stage-row">${renderStageChip(card)}</div>
          <div class="phase-strip">
            ${(card.phases || []).map((phase) => `<div class="phase-item"><span class="phase-dot ${esc(phase.status)}"></span><span class="phase-label">${esc(phase.label)}</span></div>`).join('')}
          </div>
          <div class="url-list">
            <div>API: ${esc(card.api_url || '-')}</div>
            <div>RPC: ${esc(card.rpc_url || '-')}</div>
            <div>P2P: ${esc(card.p2p_port || '-')}</div>
            ${card.runtime_backend === 'container' ? `<div>Node Page: ${esc(card.control_url || (card.node_page_running ? 'loading...' : '-'))}</div>` : ''}
          </div>
          ${PAGE_MODE === 'topology' ? `<div class="small-actions">
            ${nodePageButton}
            <button type="button" ${card.api_ready ? '' : 'disabled'} onclick="return openTerminalFromButton(event, '${esc(card.key)}', 'api', ${card.api_ready ? 'true' : 'false'})">Show API</button>
            <button type="button" class="ghost" ${(card.secondary_process && card.chain_ready) ? '' : 'disabled'} onclick="return openTerminalFromButton(event, '${esc(card.key)}', 'chain', ${(card.secondary_process && card.chain_ready) ? 'true' : 'false'})">Show Chain</button>
            <button type="button" class="ghost" ${(card.key === 'root' || state.stopping[card.key] || !card.stop_enabled) ? 'disabled' : ''} onclick="return stopNodeFromButton(event, '${esc(card.key)}')">Stop API + Chain</button>
          </div>` : ''}
        </div>`;
      }).join('');
    }

    function localPendingCard({ key, name, nodeId, tier, ordinal, roleLabel = '', scenario, host, apiPort = '', controlPort = '', rpcPort = '', p2pPort = '', simulatedDevice = '', selectedDevice = null }) {
      const title = roleLabel ? `${name} (${roleLabel})` : name;
      const hasChain = tier !== 'endpoint';
      const apiRunning = Boolean(apiPort);
      const chainRunning = hasChain && Boolean(rpcPort);
      const nodePageRunning = Boolean(controlPort);
      return {
        key,
        name: title,
        node_id: nodeId,
        signature: '',
        tier,
        badge: tier,
        api_url: apiPort ? `http://${host}:${apiPort}` : '',
        rpc_url: rpcPort ? `http://${host}:${rpcPort}` : '',
        p2p_port: p2pPort || '',
        stage: 'Initializing',
        stage_status: 'running',
        phases: [
          { key: 'api', label: 'API', status: apiPort ? 'active' : 'pending' },
          { key: 'chain', label: 'Chain', status: hasChain ? (rpcPort ? 'active' : 'pending') : 'not_applicable' },
          { key: 'registration', label: 'Registration', status: key === 'root' ? 'complete' : 'pending' },
          { key: 'bootstrap_ack', label: 'Bootstrap ACK', status: (tier === 'fog' || tier === 'edge') ? 'pending' : 'not_applicable' },
          { key: 'access', label: 'Access', status: 'pending' },
          { key: 'consensus', label: 'Consensus', status: hasChain ? 'pending' : 'not_applicable' },
        ],
        summary_status: 'running',
        summary_label: nodePageRunning ? 'container loading' : 'initializing',
        control_url: controlPort ? `http://${host}:${controlPort}` : '',
        dashboard_url: null,
        node_page_running: nodePageRunning,
        node_page_ready: false,
        node_page_label: nodePageRunning ? 'Node page loading' : 'Node page pending',
        default_process: 'api',
        secondary_process: hasChain ? 'chain' : null,
        api_running: apiRunning,
        chain_running: chainRunning,
        api_ready: false,
        chain_ready: false,
        stop_enabled: Boolean(apiPort || rpcPort),
        selected_scenario: scenario,
        runtime_backend: tier === 'cloud' ? 'native' : 'container',
        simulated_device: simulatedDevice,
        selected_device: selectedDevice || {},
        action_capabilities: {},
        defaults: {},
        policy_capabilities: {},
        policy_defaults: {},
      };
    }

    function buildLocalPendingCards(body) {
      const host = body.host || '127.0.0.1';
      const cards = [localPendingCard({ key: 'root', name: 'Root Cloud', nodeId: 'CLOUD01', tier: 'cloud', ordinal: 0, scenario: body.scenario, host })];
      const fogDevices = Array.isArray(body.fog_devices) ? body.fog_devices : [];
      const edgeDevices = Array.isArray(body.edge_devices) ? body.edge_devices : [];
      const endpointDevices = Array.isArray(body.endpoint_devices) ? body.endpoint_devices : [];
      for (let idx = 1; idx <= Number(body.fog || 0); idx += 1) {
        const selectedDevice = presetById('fog', fogDevices[idx - 1] || ((DEVICE_CATALOG.defaults || {}).fog || ''));
        cards.push(localPendingCard({ key: `fog-${idx}`, name: `Fog${idx}`, nodeId: `FG-${idx}`, tier: 'fog', ordinal: idx, scenario: body.scenario, host, simulatedDevice: (selectedDevice || {}).label || '', selectedDevice }));
      }
      for (let idx = 1; idx <= Number(body.edge || 0); idx += 1) {
        const selectedDevice = presetById('edge', edgeDevices[idx - 1] || ((DEVICE_CATALOG.defaults || {}).edge || ''));
        cards.push(localPendingCard({ key: `edge-${idx}`, name: `Edge${idx}`, nodeId: `ED-${idx}`, tier: 'edge', ordinal: idx, scenario: body.scenario, host, simulatedDevice: (selectedDevice || {}).label || '', selectedDevice }));
      }
      const endpointRoles = Array.isArray(body.endpoint_roles) ? body.endpoint_roles : [];
      for (let idx = 1; idx <= Number(body.endpoint || 0); idx += 1) {
        const selectedDevice = presetById('endpoint', endpointDevices[idx - 1] || ((DEVICE_CATALOG.defaults || {}).endpoint || ''));
        cards.push(localPendingCard({
          key: `endpoint-${idx}`,
          name: `Endpoint${idx}`,
          nodeId: `EP-${idx}`,
          tier: 'endpoint',
          ordinal: idx,
          roleLabel: endpointRoles[idx - 1] || 'Sensor',
          scenario: body.scenario,
          host,
          simulatedDevice: (selectedDevice || {}).label || '',
          selectedDevice,
        }));
      }
      return cards;
    }

    function buildIncrementalPendingCards(kind, payload = {}) {
      const existing = (((state.details || {}).node_cards) || []).slice();
      const scenario = payload.scenario || currentScenarioName(state.details || {}) || '';
      if (kind === 'start-root') {
        return [localPendingCard({ key: 'root', name: 'Root Cloud', nodeId: 'CLOUD01', tier: 'cloud', ordinal: 0, scenario, host: '127.0.0.1', apiPort: '5600', rpcPort: '8545', p2pPort: '30303' })];
      }
      const tier = payload.tier || 'endpoint';
      const nodeId = `PENDING-${String(tier).toUpperCase()}`;
      const selectedDevice = presetById(tier, payload.device_id || ((DEVICE_CATALOG.defaults || {})[tier] || ''));
      existing.push(localPendingCard({
        key: `pending-${tier}-${Date.now()}`,
        name: `Spawning ${tier[0].toUpperCase()}${tier.slice(1)}`,
        nodeId,
        tier,
        ordinal: 0,
        roleLabel: tier === 'endpoint' ? (payload.endpoint_role || 'Sensor') : '',
        scenario,
        host: '127.0.0.1',
        simulatedDevice: (selectedDevice || {}).label || '',
        selectedDevice,
      }));
      return existing;
    }

    function renderEvents(events) {
      const root = document.getElementById('recent-events');
      if (!(events || []).length) {
        root.innerHTML = '<div class="sub">No events yet.</div>';
        return;
      }
      root.innerHTML = events.slice().reverse().slice(0, 14).map((event) => `
        <div class="event-item">
          <div class="event-top"><span>${esc(event.flow_type)} | ${esc(event.stage)}</span><span>${esc(new Date(event.ts_unix_ms).toLocaleTimeString())}</span></div>
          <div><b>${esc(event.message)}</b></div>
          <div class="sub">${esc(event.node_name || event.node_id || '')} | ${esc(event.status)}</div>
        </div>`).join('');
    }

    function closeInspector() {
      clearInspectorRefreshTimer();
      state.inspector.open = false;
      state.inspector.nodeKey = '';
      state.inspector.data = null;
      renderInspector();
    }
    window.closeInspector = closeInspector;

    function fallbackInspectorDataForNode(nodeKey) {
      const savedNodeKey = state.inspector.nodeKey;
      state.inspector.nodeKey = nodeKey;
      const data = fallbackInspectorData();
      state.inspector.nodeKey = savedNodeKey;
      return data;
    }

    async function openInspector(nodeKey) {
      clearInspectorRefreshTimer();
      state.inspector.open = true;
      state.inspector.nodeKey = nodeKey;
      state.inspector.data = fallbackInspectorDataForNode(nodeKey);
      const fallbackNode = (state.inspector.data || {}).node || {};
      if (PAGE_MODE === 'topology') {
        state.inspector.activeSection = 'topology';
      } else if (fallbackNode.tier !== 'cloud') {
        state.inspector.activeSection = 'access';
      }
      if (!document.getElementById('node-inspector').style.left && !document.getElementById('node-inspector').style.right) {
        document.getElementById('node-inspector').style.right = '18px';
        document.getElementById('node-inspector').style.top = '120px';
      }
      renderInspector();
      await refreshInspector();
      renderNodes((state.details && state.details.node_cards) || []);
    }
    window.openInspector = openInspector;

    function inspectorResult(nodeKey) {
      return state.inspector.results[nodeKey] || null;
    }

    function fallbackInspectorData() {
      const nodeKey = state.inspector.nodeKey;
      if (!nodeKey) return null;
      const cards = (state.details && state.details.node_cards) || [];
      const card = cards.find((item) => item && item.key === nodeKey);
      if (!card) return null;
      const targetOptions = cards
        .filter((item) => item && item.signature)
        .map((item) => ({
          key: item.key,
          name: item.name,
          tier: item.tier,
          signature: item.signature,
          is_self: item.key === nodeKey,
        }));
      return {
        selected_scenario: currentScenarioName(state.details || {}),
        node: {
          key: card.key,
          name: card.name,
          tier: card.tier,
          node_id: card.node_id,
          signature: card.signature || '',
          api_url: card.api_url || '',
          rpc_url: card.rpc_url || '',
          p2p_port: card.p2p_port || '',
          runtime_backend: card.runtime_backend || '',
          simulated_device: card.simulated_device || '',
          selected_device: card.selected_device || {},
          summary_status: card.summary_status || card.stage_status || 'down',
          summary_label: card.summary_label || card.stage || 'initializing',
          action_capabilities: card.action_capabilities || {},
          defaults: card.defaults || {},
          target_options: targetOptions,
        },
        outgoing_flows: [],
        incoming_flows: [],
        latest_result: null,
        pending_note: 'This node is visible in the topology, but its full inspector data is still loading.',
      };
    }

    function formatValue(value) {
      if (value === null || value === undefined || value === '') return '-';
      if (Array.isArray(value)) return value.join(', ');
      if (typeof value === 'object') return JSON.stringify(value);
      return String(value);
    }

    function renderResultSummary(result) {
      if (!result) return '<div class="muted-note">Run an action from this node to see the latest output here.</div>';
      const payload = result.payload || {};
      let rows = [];
      if (result.action === 'access') {
        rows = [
          ['Granted', payload.granted],
          ['Policy ID', payload.policyId],
          ['Context', payload.ctx],
          ['Operation', payload.op],
          ['Flow ID', payload.flow_id],
          ['Why', payload.why || ''],
        ];
      } else if (result.action === 'delegate') {
        rows = [
          ['Granted', payload.granted],
          ['TX Hash', payload.tx],
          ['Flow ID', payload.flow_id],
          ['Why', payload.why || ''],
        ];
      } else if (result.action === 'revoke') {
        rows = [
          ['TX Hash', payload.tx],
          ['Flow ID', payload.flow_id],
          ['Policy ID', payload.policy_id || ''],
          ['Why', payload.why || ''],
        ];
      } else if (result.action === 'grant_lookup') {
        const grant = payload.grant || {};
        rows = [
          ['Policy ID', grant.policyId],
          ['Issued', grant.isIssued],
          ['Revoked', grant.isRevoked],
          ['Expires At', grant.expiresAt],
          ['Delegable', grant.delegationAllowed],
          ['Delegation Depth', grant.delegationDepth],
        ];
      } else if (result.action === 'expiry_check') {
        rows = [
          ['Expired', payload.expired],
          ['Policy ID', payload.policy_id || ''],
        ];
      } else if (result.action === 'policy_create') {
        rows = [
          ['Policy ID', payload.policy_id],
          ['Status', payload.status || 'created'],
          ['TX Hash', payload.tx_hash || ''],
        ];
      } else if (result.action === 'policy_find') {
        rows = [
          ['Found', payload.found],
          ['Policy ID', payload.policy_id || ''],
        ];
      } else if (result.action === 'policy_get') {
        const policy = payload.policy || {};
        rows = [
          ['Policy ID', payload.policy_id],
          ['From Role', policy.fromRole || policy.from_role || ''],
          ['To Role', policy.toRole || policy.to_role || ''],
          ['Ops', policy.ops || ''],
          ['Ctx', policy.ctxSchema || policy.ctx_schema || ''],
        ];
      } else if (result.action === 'policy_update') {
        rows = [
          ['Policy ID', payload.policy_id],
          ['TX Hash', payload.tx_hash || ''],
        ];
      } else if (result.action === 'policy_deprecate') {
        rows = [
          ['Policy ID', payload.policy_id],
          ['TX Hash', payload.tx_hash || ''],
        ];
      }
      return `
        <div class="summary-grid">
          ${rows.map(([label, value]) => `<div><b>${esc(label)}</b><div>${esc(formatValue(value))}</div></div>`).join('')}
        </div>
        <details class="tech" style="margin-top:10px;">
          <summary>Raw Output</summary>
          <pre>${esc(JSON.stringify(payload, null, 2))}</pre>
        </details>`;
    }

    function renderFlowRows(rows) {
      if (!(rows || []).length) return '<div class="sub">No flows yet for this direction.</div>';
      return rows.map((flow) => `
        <div class="flow-row">
          <div class="flow-top">
            <div>
              <div class="flow-route">${esc(flow.source_name)} → ${esc(flow.target_name)}</div>
              <div class="sub">${esc(flow.action)} | ${esc(flow.message || '')}</div>
            </div>
            <span class="stage-chip ${esc(flow.stage_status)}">${esc(flow.current_stage)}</span>
          </div>
          <div class="flow-stages">
            ${(flow.stages || []).map((stage) => `<div class="flow-stage"><span class="flow-dot ${esc(stage.status)}"></span><span>${esc(stage.label)}</span></div>`).join('')}
          </div>
          <details class="tech">
            <summary>Technical Stages</summary>
            ${(flow.technical_events || []).map((event) => `<div class="tech-event"><div><b>${esc(event.stage)}</b> | ${esc(event.status)}</div><div class="sub">${esc(event.message || '')}</div><div class="sub">${esc(new Date(event.ts_unix_ms || 0).toLocaleTimeString())} | policy=${esc(event.policy_id || '-')} | tx=${esc(event.tx_hash || '-')}</div></div>`).join('')}
          </details>
        </div>`).join('');
    }

    function renderActionForm(node, action) {
      const caps = node.action_capabilities || {};
      const defaults = node.defaults || {};
      const policyCaps = node.policy_capabilities || {};
      const policyDefaults = node.policy_defaults || {};
      const options = (node.target_options || []).filter((item) => !item.is_self);
      const targetOptions = options.map((item) => `<option value="${esc(item.signature)}" ${item.signature === defaults.target_signature ? 'selected' : ''}>${esc(item.name)} (${esc(item.tier)})</option>`).join('');
      const childOptions = options.map((item) => `<option value="${esc(item.signature)}" ${item.signature === defaults.child_signature ? 'selected' : ''}>${esc(item.name)} (${esc(item.tier)})</option>`).join('');
      const keyMap = { access: 'access', delegate: 'delegate', revoke: 'revoke_grant', grant_lookup: 'grant_lookup', expiry_check: 'expiry_check' };
      const cap = caps[keyMap[action]] || { enabled: false, reason: 'Action unavailable.' };
      const disabled = cap.enabled ? '' : 'disabled';
      const reason = cap.enabled ? '' : `<div class="muted-note" style="margin-top:10px;">${esc(cap.reason || 'Action unavailable.')}</div>`;
      if (action === 'access') {
        return `<form class="inspector-form" onsubmit="return submitInspectorAction(event, 'access')">
          <div class="row">
            <label>Target Node<select name="to_signature">${targetOptions}</select></label>
            <label>Method<select name="method"><option ${defaults.method === 'GET' ? 'selected' : ''}>GET</option><option>POST</option><option>PUT</option><option>PATCH</option><option>DELETE</option></select></label>
          </div>
          <div class="row">
            <label>Resource Path<input name="resource_path" value="${esc(defaults.resource_path || '/temperature')}"></label>
            <label>Expiry Seconds<input name="expiry_secs" type="number" value="${esc(defaults.expiry_secs || 900)}"></label>
          </div>
          <div class="row">
            <label>Delegation Depth<input name="delegation_depth" type="number" value="${esc(defaults.delegation_depth || 0)}"></label>
            <label class="inline">Allow Delegation <input name="allow_delegation" type="checkbox" ${defaults.allow_delegation ? 'checked' : ''}></label>
          </div>
          <div class="actions"><button type="submit" ${disabled}>Run Access</button></div>
          ${reason}
        </form>`;
      }
      if (action === 'delegate') {
        return `<form class="inspector-form" onsubmit="return submitInspectorAction(event, 'delegate')">
          <div class="row">
            <label>Target Node<select name="to_sig">${targetOptions}</select></label>
            <label>Child Node<select name="child_from_sig">${childOptions}</select></label>
          </div>
          <div class="row">
            <label>Operations CSV<input name="ops_csv" value="${esc(defaults.ops_csv || 'READ')}"></label>
            <label>Child Expiry Seconds<input name="child_expiry_secs" type="number" value="${esc(defaults.child_expiry_secs || 600)}"></label>
          </div>
          <div class="row">
            <label>Policy ID<input name="policy_id" value="${esc(defaults.policy_id || '')}"></label>
          </div>
          <div class="actions"><button type="submit" ${disabled}>Run Delegation</button></div>
          ${reason}
        </form>`;
      }
      if (action === 'revoke') {
        return `<form class="inspector-form" onsubmit="return submitInspectorAction(event, 'revoke')">
          <div class="row">
            <label>Target Node<select name="to_signature">${targetOptions}</select></label>
            <label>Policy ID<input name="policy_id" value="${esc(defaults.policy_id || '')}"></label>
          </div>
          <div class="row">
            <label>Method<select name="method"><option ${defaults.method === 'GET' ? 'selected' : ''}>GET</option><option>POST</option><option>PUT</option><option>PATCH</option><option>DELETE</option></select></label>
            <label>Resource Path<input name="resource_path" value="${esc(defaults.resource_path || '/temperature')}"></label>
          </div>
          <div class="actions"><button type="submit" ${disabled}>Run Revocation</button></div>
          ${reason}
        </form>`;
      }
      if (action === 'grant_lookup') {
        return `<form class="inspector-form" onsubmit="return submitInspectorAction(event, 'grant_lookup')">
          <div class="row">
            <label>Target Node<select name="to_signature">${targetOptions}</select></label>
            <label>Method<select name="method"><option ${defaults.method === 'GET' ? 'selected' : ''}>GET</option><option>POST</option><option>PUT</option><option>PATCH</option><option>DELETE</option></select></label>
          </div>
          <div class="row">
            <label>Resource Path<input name="resource_path" value="${esc(defaults.resource_path || '/temperature')}"></label>
            <label>Context<input name="ctx" value="${esc(defaults.ctx || '')}" placeholder="api:GET:/temperature"></label>
          </div>
          <div class="actions"><button type="submit" ${disabled}>Grant Lookup</button></div>
          ${reason}
        </form>`;
      }
      if (action === 'policy_create') {
        const cap = policyCaps.policy_create || { enabled: false, reason: 'Policy controls are unavailable.' };
        const disabled = cap.enabled ? '' : 'disabled';
        const reason = cap.enabled ? '' : `<div class="muted-note" style="margin-top:10px;">${esc(cap.reason || 'Policy controls are unavailable.')}</div>`;
        return `<form class="inspector-form" onsubmit="return submitInspectorAction(event, 'policy_create')">
          <div class="row">
            <label>From Role<select name="from_role"><option ${policyDefaults.from_role === 'Cloud' ? 'selected' : ''}>Cloud</option><option ${policyDefaults.from_role === 'Fog' ? 'selected' : ''}>Fog</option><option ${policyDefaults.from_role === 'Edge' ? 'selected' : ''}>Edge</option><option ${policyDefaults.from_role === 'Sensor' ? 'selected' : ''}>Sensor</option><option ${policyDefaults.from_role === 'Actuator' ? 'selected' : ''}>Actuator</option></select></label>
            <label>To Role<select name="to_role"><option ${policyDefaults.to_role === 'Cloud' ? 'selected' : ''}>Cloud</option><option ${policyDefaults.to_role === 'Fog' ? 'selected' : ''}>Fog</option><option ${policyDefaults.to_role === 'Edge' ? 'selected' : ''}>Edge</option><option ${policyDefaults.to_role === 'Sensor' ? 'selected' : ''}>Sensor</option><option ${policyDefaults.to_role === 'Actuator' ? 'selected' : ''}>Actuator</option></select></label>
          </div>
          <div class="row">
            <label>Operations CSV<input name="ops_csv" value="${esc(policyDefaults.ops_csv || 'READ')}"></label>
            <label>Context Schema<input name="ctx_schema" value="${esc(policyDefaults.ctx_schema || '')}" placeholder="api:GET:/temperature"></label>
          </div>
          <div class="actions"><button type="submit" ${disabled}>Create Policy</button></div>${reason}
        </form>`;
      }
      if (action === 'policy_find') {
        const cap = policyCaps.policy_find || { enabled: false, reason: 'Policy controls are unavailable.' };
        const disabled = cap.enabled ? '' : 'disabled';
        const reason = cap.enabled ? '' : `<div class="muted-note" style="margin-top:10px;">${esc(cap.reason || 'Policy controls are unavailable.')}</div>`;
        return `<form class="inspector-form" onsubmit="return submitInspectorAction(event, 'policy_find')">
          <div class="row">
            <label>From Role<select name="from_role"><option ${policyDefaults.from_role === 'Cloud' ? 'selected' : ''}>Cloud</option><option ${policyDefaults.from_role === 'Fog' ? 'selected' : ''}>Fog</option><option ${policyDefaults.from_role === 'Edge' ? 'selected' : ''}>Edge</option><option ${policyDefaults.from_role === 'Sensor' ? 'selected' : ''}>Sensor</option><option ${policyDefaults.from_role === 'Actuator' ? 'selected' : ''}>Actuator</option></select></label>
            <label>To Role<select name="to_role"><option ${policyDefaults.to_role === 'Cloud' ? 'selected' : ''}>Cloud</option><option ${policyDefaults.to_role === 'Fog' ? 'selected' : ''}>Fog</option><option ${policyDefaults.to_role === 'Edge' ? 'selected' : ''}>Edge</option><option ${policyDefaults.to_role === 'Sensor' ? 'selected' : ''}>Sensor</option><option ${policyDefaults.to_role === 'Actuator' ? 'selected' : ''}>Actuator</option></select></label>
          </div>
          <div class="row">
            <label>Operations CSV<input name="ops_csv" value="${esc(policyDefaults.ops_csv || 'READ')}"></label>
            <label>Context Schema<input name="ctx_schema" value="${esc(policyDefaults.ctx_schema || '')}" placeholder="api:GET:/temperature"></label>
          </div>
          <div class="actions"><button type="submit" ${disabled}>Find Policy</button></div>${reason}
        </form>`;
      }
      if (action === 'policy_get') {
        const cap = policyCaps.policy_get || { enabled: false, reason: 'Policy controls are unavailable.' };
        const disabled = cap.enabled ? '' : 'disabled';
        const reason = cap.enabled ? '' : `<div class="muted-note" style="margin-top:10px;">${esc(cap.reason || 'Policy controls are unavailable.')}</div>`;
        return `<form class="inspector-form" onsubmit="return submitInspectorAction(event, 'policy_get')">
          <div class="row"><label>Policy ID<input name="policy_id" value="${esc(policyDefaults.policy_id || '')}"></label></div>
          <div class="actions"><button type="submit" ${disabled}>Get Policy</button></div>${reason}
        </form>`;
      }
      if (action === 'policy_update') {
        const cap = policyCaps.policy_update || { enabled: false, reason: 'Policy controls are unavailable.' };
        const disabled = cap.enabled ? '' : 'disabled';
        const reason = cap.enabled ? '' : `<div class="muted-note" style="margin-top:10px;">${esc(cap.reason || 'Policy controls are unavailable.')}</div>`;
        return `<form class="inspector-form" onsubmit="return submitInspectorAction(event, 'policy_update')">
          <div class="row">
            <label>Policy ID<input name="policy_id" value="${esc(policyDefaults.policy_id || '')}"></label>
            <label>Operations CSV<input name="ops_csv" value="${esc(policyDefaults.ops_csv || 'READ')}"></label>
          </div>
          <div class="row"><label>Context Schema<input name="ctx_schema" value="${esc(policyDefaults.ctx_schema || '')}" placeholder="api:GET:/temperature"></label></div>
          <div class="actions"><button type="submit" ${disabled}>Update Policy</button></div>${reason}
        </form>`;
      }
      if (action === 'policy_deprecate') {
        const cap = policyCaps.policy_deprecate || { enabled: false, reason: 'Policy controls are unavailable.' };
        const disabled = cap.enabled ? '' : 'disabled';
        const reason = cap.enabled ? '' : `<div class="muted-note" style="margin-top:10px;">${esc(cap.reason || 'Policy controls are unavailable.')}</div>`;
        return `<form class="inspector-form" onsubmit="return submitInspectorAction(event, 'policy_deprecate')">
          <div class="row"><label>Policy ID<input name="policy_id" value="${esc(policyDefaults.policy_id || '')}"></label></div>
          <div class="actions"><button type="submit" ${disabled}>Deprecate Policy</button></div>${reason}
        </form>`;
      }
      return `<form class="inspector-form" onsubmit="return submitInspectorAction(event, 'expiry_check')">
        <div class="row">
          <label>Target Node<select name="to_signature">${targetOptions}</select></label>
          <label>Policy ID<input name="policy_id" value="${esc(defaults.policy_id || '')}"></label>
        </div>
        <div class="row">
          <label>Method<select name="method"><option ${defaults.method === 'GET' ? 'selected' : ''}>GET</option><option>POST</option><option>PUT</option><option>PATCH</option><option>DELETE</option></select></label>
          <label>Resource Path<input name="resource_path" value="${esc(defaults.resource_path || '/temperature')}"></label>
        </div>
        <div class="actions"><button type="submit" ${disabled}>Expiry Check</button></div>
        ${reason}
      </form>`;
    }

    function renderInspector() {
      const root = document.getElementById('node-inspector');
      const body = document.getElementById('inspector-body');
      const title = document.getElementById('inspector-title');
      if (!state.inspector.open) {
        root.classList.add('hidden');
        body.innerHTML = '';
        return;
      }
      root.classList.remove('hidden');
      const data = state.inspector.data || fallbackInspectorData() || {};
      const node = data.node;
      if (!node) {
        title.innerHTML = '<b>Node Inspector</b><div class="sub">No node selected</div>';
        body.innerHTML = PAGE_MODE === 'topology'
          ? '<div class="muted-note">Pick a node card to inspect topology state, device profile, and process actions.</div>'
          : '<div class="muted-note">Pick a node card to inspect node-to-node access control.</div>';
        return;
      }
      const liveCard = (((state.details || {}).node_cards) || []).find((item) => item && item.key === node.key) || {};
      title.innerHTML = `<div class="inline"><span class="tier-badge" style="width:32px;height:32px;">${tierIcon(node.tier)}</span><div><b>${esc(node.name)}</b><div class="sub">${esc(node.node_id || '')} | ${esc(node.tier || '')}</div></div></div>`;
      if (PAGE_MODE !== 'topology' && node.tier !== 'cloud' && state.inspector.activeSection !== 'access') {
        state.inspector.activeSection = 'access';
      }
      const activeSection = state.inspector.activeSection || 'access';
      const activeAction = state.inspector.activeAction || 'access';
      const result = inspectorResult(node.key);
      const flows = state.inspector.flowTab === 'incoming' ? (data.incoming_flows || []) : (data.outgoing_flows || []);
      if (PAGE_MODE === 'topology') {
        body.innerHTML = `
          <div class="inspector-grid">
            <div class="inspector-box">
              <div class="stage-row"><span class="stage-chip ${esc(node.summary_status || 'down')}">${esc(node.summary_label || 'unknown')}</span></div>
              ${data.pending_note ? `<div class="muted-note" style="margin-bottom:10px;">${esc(data.pending_note)}</div>` : ''}
              <div class="inspector-meta">
                <div><b>Signature</b><div>${esc(node.signature || '-')}</div></div>
                <div><b>API</b><div>${esc(node.api_url || '-')}</div></div>
                <div><b>RPC</b><div>${esc(node.rpc_url || '-')}</div></div>
                <div><b>P2P</b><div>${esc(node.p2p_port || '-')}</div></div>
                <div><b>Device</b><div>${esc(node.simulated_device || '-')}</div></div>
                <div><b>Runtime</b><div>${esc(node.runtime_backend || '-')}</div></div>
              </div>
              ${node.selected_device && node.selected_device.label ? specCardMarkup(node.selected_device) : ''}
            </div>
            <div class="inspector-box">
              <h3>Process Actions</h3>
              <div class="actions" style="margin-bottom:10px;">
                <button type="button" ${(liveCard.api_ready || node.api_url) ? '' : 'disabled'} onclick="return openTerminalFromButton(event, '${esc(node.key)}', 'api', ${(liveCard.api_ready || node.api_url) ? 'true' : 'false'})">Show API</button>
                <button type="button" class="ghost" ${((liveCard.secondary_process && liveCard.chain_ready) || node.rpc_url) ? '' : 'disabled'} onclick="return openTerminalFromButton(event, '${esc(node.key)}', 'chain', ${((liveCard.secondary_process && liveCard.chain_ready) || node.rpc_url) ? 'true' : 'false'})">Show Chain</button>
                <button type="button" class="ghost" ${(state.stopping[node.key] || !(liveCard.stop_enabled || node.api_url || node.rpc_url)) ? 'disabled' : ''} onclick="return stopNodeFromButton(event, '${esc(node.key)}')">Stop API + Chain</button>
              </div>
              <div class="muted-note">Topology page only shows node lifecycle and process controls. Use the control page for access, delegation, revocation, and policy operations.</div>
            </div>
            <div class="inspector-box">
              <div class="flow-tabs">
                <button type="button" class="${state.inspector.flowTab === 'outgoing' ? 'active' : 'soft'}" onclick="setFlowTab('outgoing')">Outgoing</button>
                <button type="button" class="${state.inspector.flowTab === 'incoming' ? 'active' : 'soft'}" onclick="setFlowTab('incoming')">Incoming</button>
              </div>
              ${renderFlowRows(flows)}
            </div>
          </div>`;
        return;
      }
      body.innerHTML = `
        <div class="inspector-grid">
          <div class="inspector-box">
            <div class="stage-row"><span class="stage-chip ${esc(node.summary_status || 'down')}">${esc(node.summary_label || 'unknown')}</span></div>
            ${data.pending_note ? `<div class="muted-note" style="margin-bottom:10px;">${esc(data.pending_note)}</div>` : ''}
            <div class="inspector-meta">
              <div><b>Signature</b><div>${esc(node.signature || '-')}</div></div>
              <div><b>API</b><div>${esc(node.api_url || '-')}</div></div>
              <div><b>RPC</b><div>${esc(node.rpc_url || '-')}</div></div>
              <div><b>P2P</b><div>${esc(node.p2p_port || '-')}</div></div>
              <div><b>Device</b><div>${esc(node.simulated_device || '-')}</div></div>
              <div><b>Runtime</b><div>${esc(node.runtime_backend || '-')}</div></div>
            </div>
            ${node.selected_device && node.selected_device.label ? specCardMarkup(node.selected_device) : ''}
          </div>
          <div class="inspector-box">
            ${node.tier === 'cloud' ? `<div class="inspector-sections">
              <button type="button" class="${activeSection === 'access' ? 'active' : 'soft'}" onclick="setInspectorSection('access')">Access Control</button>
              <button type="button" class="${activeSection === 'policy' ? 'active' : 'soft'}" onclick="setInspectorSection('policy')">Policy Controls</button>
            </div>` : ''}
            <div class="inspector-toolbar">
              ${(activeSection === 'policy' && node.tier === 'cloud'
                ? [
                    ['policy_create', 'Create Policy'],
                    ['policy_find', 'Find Policy'],
                    ['policy_get', 'Get Policy'],
                    ['policy_update', 'Update Policy'],
                    ['policy_deprecate', 'Deprecate Policy'],
                  ]
                : [
                    ['access', 'Access Request'],
                    ['delegate', 'Delegate'],
                    ['revoke', 'Revoke Grant'],
                    ['grant_lookup', 'Grant Lookup'],
                    ['expiry_check', 'Expiry Check'],
                  ]
              ).map(([key, label]) => `<button type="button" class="${activeAction === key ? 'active' : 'soft'}" onclick="setInspectorAction('${key}')">${esc(label)}</button>`).join('')}
            </div>
            <div style="margin-top:12px;">${renderActionForm(node, activeAction)}</div>
          </div>
          <div class="inspector-box">
            <h3>Latest Output</h3>
            ${renderResultSummary(result)}
          </div>
          <div class="inspector-box">
            <div class="flow-tabs">
              <button type="button" class="${state.inspector.flowTab === 'outgoing' ? 'active' : 'soft'}" onclick="setFlowTab('outgoing')">Outgoing</button>
              <button type="button" class="${state.inspector.flowTab === 'incoming' ? 'active' : 'soft'}" onclick="setFlowTab('incoming')">Incoming</button>
            </div>
            ${renderFlowRows(flows)}
          </div>
        </div>`;
    }

    function setInspectorAction(action) {
      state.inspector.activeSection = action.startsWith('policy_') ? 'policy' : 'access';
      state.inspector.activeAction = action;
      renderInspector();
    }
    window.setInspectorAction = setInspectorAction;

    function setInspectorSection(section) {
      state.inspector.activeSection = section;
      state.inspector.activeAction = section === 'policy' ? 'policy_create' : 'access';
      renderInspector();
    }
    window.setInspectorSection = setInspectorSection;

    function setFlowTab(tab) {
      state.inspector.flowTab = tab;
      renderInspector();
    }
    window.setFlowTab = setFlowTab;

    async function refreshInspector(parentRefreshSeq = 0) {
      if (!state.inspector.open || !state.inspector.nodeKey) {
        clearInspectorRefreshTimer();
        renderInspector();
        return;
      }
      const inspectorSeq = ++state.inspector.refreshSeq;
      const params = new URLSearchParams();
      const scenario = currentScenarioName(state.details || {});
      if (scenario) params.set('scenario', scenario);
      params.set('node_key', state.inspector.nodeKey);
      const payload = await fetch(`${INSPECTOR_ENDPOINT}?${params.toString()}`).then((r) => r.json());
      if (parentRefreshSeq && parentRefreshSeq !== state.refreshSeq) return;
      if (inspectorSeq !== state.inspector.refreshSeq) return;
      state.inspector.data = (payload && payload.node) ? payload : (fallbackInspectorData() || payload);
      renderInspector();
      scheduleInspectorRefresh();
    }

    async function startRoot() {
      clearBanner();
      const response = await fetch('/infrastructure/start-root', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host: '127.0.0.1' }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        showBanner('error', payload.detail || payload.error || 'Root start failed.');
        return;
      }
      state.selectionMode = 'manual';
      state.selectedScenario = payload.scenario || '';
      const optimisticJob = {
        job_id: payload.job_id || 'start-root',
        scenario: state.selectedScenario,
        status: 'running',
        exit_code: null,
        runner_command: `./.venv/bin/python scripts/run_topology.py start-root --scenario ${state.selectedScenario} --host 127.0.0.1`,
        log_lines: [
          `[topology] provisioning scenario ${state.selectedScenario}`,
          '[topology] root job queued',
        ],
      };
      state.pendingStart = {
        scenario: state.selectedScenario,
        node_cards: buildIncrementalPendingCards('start-root', { scenario: state.selectedScenario }),
      };
      persistSelectionState();
      showBanner('ok', `Root start queued for ${state.selectedScenario}.`);
      renderMainTerminal(optimisticJob);
      renderNodes(state.pendingStart.node_cards);
      refresh();
    }
    window.startRoot = startRoot;

    async function spawnNode(tier) {
      clearBanner();
      const controls = ((state.details || {}).spawn_controls) || {};
      if (!controls[`${tier}_enabled`]) {
        showBanner('error', `Cannot spawn ${tier} right now.`);
        return;
      }
      const body = {
        tier,
        device_id: ((document.getElementById(`${tier}-device-select`) || {}).value || ''),
        endpoint_role: tier === 'endpoint' ? (((document.getElementById('endpoint-role-select') || {}).value) || 'Sensor') : undefined,
        host: '127.0.0.1',
      };
      const response = await fetch('/infrastructure/spawn-node', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        showBanner('error', payload.detail || payload.error || `Failed to spawn ${tier}.`);
        return;
      }
      const scenario = payload.scenario || currentScenarioName(state.details || {});
      state.selectedScenario = scenario || state.selectedScenario;
      state.pendingStart = {
        scenario,
        node_cards: buildIncrementalPendingCards(`spawn-${tier}`, { ...body, scenario }),
      };
      if (tier === 'fog') {
        const fogSpec = document.getElementById('fog-device-card');
        if (fogSpec) fogSpec.innerHTML = '';
      }
      showBanner('ok', `${tier[0].toUpperCase()}${tier.slice(1)} spawn queued.`);
      refresh();
    }
    window.spawnNode = spawnNode;

    async function stopLiveTopology() {
      clearBanner();
      const scenario = currentScenarioName(state.details || {});
      if (!scenario) {
        showBanner('error', 'Start a topology first.');
        return;
      }
      const response = await fetch('/infrastructure/stop-live-topology', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        showBanner('error', payload.detail || payload.error || 'Failed to stop live topology.');
        return;
      }
      state.selectedScenario = '';
      state.selectionMode = 'auto';
      persistSelectionState();
      state.pendingStart = null;
      showBanner('ok', `Stopped ${scenario}. Freed ports: ${(payload.freed_ports || []).join(', ') || 'none reported'}.`);
      refresh();
    }
    window.stopLiveTopology = stopLiveTopology;

    function clearSelection() {
      state.selectionMode = 'auto';
      state.selectedScenario = '';
      state.pendingStart = null;
      persistSelectionState();
      const topologyVersionSelect = document.getElementById('topology-version');
      if (topologyVersionSelect) topologyVersionSelect.value = '';
      setScenarioInputValue('');
      clearBanner();
      refresh();
    }
    window.clearSelection = clearSelection;

    async function stopNode(nodeKey) {
      clearBanner();
      const scenario = currentScenarioName(state.details || {}) || scenarioInputValue();
      if (!scenario) {
        showBanner('error', 'Start a topology before stopping a node.');
        return;
      }
      state.stopping[nodeKey] = 'node';
      renderNodes((state.details && state.details.node_cards) || []);
      const response = await fetch('/infrastructure/stop-node', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario, node_key: nodeKey }),
      });
      const payload = await response.json();
      delete state.stopping[nodeKey];
      if (!response.ok || !payload.ok) {
        showBanner('error', payload.detail || payload.error || 'Failed to stop node processes.');
        renderNodes((state.details && state.details.node_cards) || []);
        return;
      }
      showBanner('ok', `Stopped ${nodeKey}. Freed ports: ${(payload.freed_ports || []).join(', ') || 'none reported'}.`);
      refresh();
    }

    async function stopProcess(nodeKey, process) {
      clearBanner();
      const scenario = currentScenarioName(state.details || {}) || scenarioInputValue();
      if (!scenario) {
        showBanner('error', 'Start a topology before stopping a process.');
        return;
      }
      state.stopping[`${nodeKey}:${process}`] = process;
      renderSpawnControls(state.details || {});
      renderNodes((state.details && state.details.node_cards) || []);
      const response = await fetch('/infrastructure/stop-process', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario, node_key: nodeKey, process }),
      });
      const payload = await response.json();
      delete state.stopping[`${nodeKey}:${process}`];
      if (!response.ok || !payload.ok) {
        showBanner('error', payload.detail || payload.error || `Failed to stop ${process}.`);
        renderSpawnControls(state.details || {});
        renderNodes((state.details && state.details.node_cards) || []);
        return;
      }
      showBanner('ok', `Stopped ${nodeKey} ${process}. Freed ports: ${(payload.freed_ports || []).join(', ') || 'none reported'}.`);
      refresh();
    }

    async function startProcess(nodeKey, process) {
      clearBanner();
      const scenario = currentScenarioName(state.details || {}) || scenarioInputValue();
      if (!scenario) {
        showBanner('error', 'Start a topology before starting a process.');
        return;
      }
      state.starting[`${nodeKey}:${process}`] = process;
      renderSpawnControls(state.details || {});
      renderNodes((state.details && state.details.node_cards) || []);
      const response = await fetch('/infrastructure/start-process', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario, node_key: nodeKey, process }),
      });
      const payload = await response.json();
      delete state.starting[`${nodeKey}:${process}`];
      if (!response.ok || !payload.ok) {
        showBanner('error', payload.detail || payload.error || `Failed to start ${process}.`);
        renderSpawnControls(state.details || {});
        renderNodes((state.details && state.details.node_cards) || []);
        return;
      }
      showBanner('ok', `Started ${nodeKey} ${process}.`);
      refresh();
    }

    async function openTerminal(nodeKey, process, ready=true) {
      clearBanner();
      if (!ready) {
        showBanner('error', `${process.toUpperCase()} terminal is not ready yet for ${nodeKey}. Wait until the node moves past initialization.`);
        return;
      }
      const scenario = currentScenarioName(state.details || {}) || scenarioInputValue();
      if (!scenario) {
        showBanner('error', 'Start a topology before opening a node terminal.');
        return;
      }
      const response = await fetch('/infrastructure/open-terminal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario, node_key: nodeKey, process }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        showBanner('error', payload.detail || payload.error || 'Failed to open Terminal.');
        return;
      }
      showBanner('ok', `Opened ${nodeKey} ${process} log in Terminal.`);
    }

    async function openTerminalFromButton(ev, nodeKey, process, ready) {
      if (ev) {
        ev.preventDefault();
        ev.stopPropagation();
      }
      await openTerminal(nodeKey, process, ready);
      return false;
    }
    window.openTerminalFromButton = openTerminalFromButton;

    function openNodePageFromButton(ev, url) {
      if (ev) {
        ev.preventDefault();
        ev.stopPropagation();
      }
      if (!url) return false;
      window.open(url, '_blank', 'noopener');
      return false;
    }
    window.openNodePageFromButton = openNodePageFromButton;

    async function stopProcessFromButton(ev, nodeKey, process) {
      if (ev) {
        ev.preventDefault();
        ev.stopPropagation();
      }
      await stopProcess(nodeKey, process);
      return false;
    }
    window.stopProcessFromButton = stopProcessFromButton;

    async function startProcessFromButton(ev, nodeKey, process) {
      if (ev) {
        ev.preventDefault();
        ev.stopPropagation();
      }
      await startProcess(nodeKey, process);
      return false;
    }
    window.startProcessFromButton = startProcessFromButton;

    async function stopNodeFromButton(ev, nodeKey) {
      if (ev) {
        ev.preventDefault();
        ev.stopPropagation();
      }
      await stopNode(nodeKey);
      return false;
    }
    window.stopNodeFromButton = stopNodeFromButton;

    async function submitInspectorAction(event, action) {
      event.preventDefault();
      const form = event.currentTarget;
      const node = (state.inspector.data || {}).node;
      if (!node) return false;
      const formData = new FormData(form);
      let url = '';
      let init = {};
      if (action === 'access') {
        const body = {
          from_signature: node.signature,
          to_signature: formData.get('to_signature'),
          method: formData.get('method'),
          resource_path: formData.get('resource_path'),
          expiry_secs: Number(formData.get('expiry_secs') || 900),
          allow_delegation: formData.get('allow_delegation') === 'on',
          delegation_depth: Number(formData.get('delegation_depth') || 0),
        };
        url = '/access';
        init = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
      } else if (action === 'delegate') {
        const body = {
          parent_from_sig: node.signature,
          to_sig: formData.get('to_sig'),
          child_from_sig: formData.get('child_from_sig'),
          ops_csv: formData.get('ops_csv'),
          child_expiry_secs: Number(formData.get('child_expiry_secs') || 600),
          policy_id: formData.get('policy_id') || undefined,
        };
        url = '/delegate';
        init = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
      } else if (action === 'revoke') {
        const body = {
          from_signature: node.signature,
          to_signature: formData.get('to_signature'),
          policy_id: formData.get('policy_id') || undefined,
          method: formData.get('method') || undefined,
          resource_path: formData.get('resource_path') || undefined,
        };
        url = '/revoke-grant';
        init = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
      } else if (action === 'grant_lookup') {
        const qs = new URLSearchParams({
          from_signature: node.signature,
          to_signature: String(formData.get('to_signature') || ''),
          method: String(formData.get('method') || ''),
          resource_path: String(formData.get('resource_path') || ''),
          ctx: String(formData.get('ctx') || ''),
        });
        url = `/grant?${qs.toString()}`;
        init = { method: 'GET' };
      } else if (action === 'policy_create') {
        const body = {
          scenario: currentScenarioName(state.details || {}) || '',
          from_role: formData.get('from_role'),
          to_role: formData.get('to_role'),
          ops_csv: formData.get('ops_csv'),
          ctx_schema: formData.get('ctx_schema') || undefined,
        };
        url = '/policy/create';
        init = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
      } else if (action === 'policy_find') {
        const qs = new URLSearchParams({
          scenario: currentScenarioName(state.details || {}) || '',
          from_role: String(formData.get('from_role') || ''),
          to_role: String(formData.get('to_role') || ''),
          ops_csv: String(formData.get('ops_csv') || ''),
          ctx_schema: String(formData.get('ctx_schema') || ''),
        });
        url = `/policy/find?${qs.toString()}`;
        init = { method: 'GET' };
      } else if (action === 'policy_get') {
        const qs = new URLSearchParams({
          scenario: currentScenarioName(state.details || {}) || '',
        });
        url = `/policy/${encodeURIComponent(String(formData.get('policy_id') || '').trim())}?${qs.toString()}`;
        init = { method: 'GET' };
      } else if (action === 'policy_update') {
        const body = {
          scenario: currentScenarioName(state.details || {}) || '',
          policy_id: Number(formData.get('policy_id') || 0),
          ops_csv: formData.get('ops_csv'),
          ctx_schema: formData.get('ctx_schema') || undefined,
        };
        url = '/policy/update';
        init = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
      } else if (action === 'policy_deprecate') {
        const body = {
          scenario: currentScenarioName(state.details || {}) || '',
          policy_id: Number(formData.get('policy_id') || 0),
        };
        url = '/policy/deprecate';
        init = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
      } else {
        const qs = new URLSearchParams({
          from_signature: node.signature,
          to_signature: String(formData.get('to_signature') || ''),
          policy_id: String(formData.get('policy_id') || ''),
          method: String(formData.get('method') || ''),
          resource_path: String(formData.get('resource_path') || ''),
        });
        url = `/expiry-check?${qs.toString()}`;
        init = { method: 'GET' };
      }
      const response = await fetch(url, init);
      const payload = await response.json();
      state.inspector.results[node.key] = { action, payload, ok: response.ok };
      renderInspector();
      if (!response.ok || !payload.ok) {
        showBanner('error', payload.detail || payload.error || 'Node action failed.');
      } else {
        showBanner('ok', `${node.name} ${action.replace(/_/g, ' ')} completed.`);
      }
      await refreshInspector();
      await refresh();
      return false;
    }
    window.submitInspectorAction = submitInspectorAction;

    async function refresh() {
      const refreshSeq = ++state.refreshSeq;
      const requestedScenario = state.selectionMode === 'manual' ? state.selectedScenario : '';
      const query = requestedScenario ? `?scenario=${encodeURIComponent(requestedScenario)}` : '';
      const payload = await fetch(`${CONTROL_DATA_ENDPOINT}${query}`).then((r) => r.json());
      if (refreshSeq !== state.refreshSeq) return;
      state.details = payload;
      const payloadScenario = payload.selected_scenario || ((payload.job && payload.job.scenario) || '');
      const jobRunning = Boolean(payload.job && payload.job.status === 'running');
      if (state.selectionMode === 'auto') {
        state.selectedScenario = payloadScenario;
      }
      if (!scenarioInputValue()) {
        if (state.selectionMode === 'manual' && state.selectedScenario) {
          setScenarioInputValue(state.selectedScenario);
        } else if (payload.suggested_scenario) {
          setScenarioInputValue(payload.suggested_scenario);
        }
      } else if (state.selectionMode === 'manual' && state.selectedScenario) {
        setScenarioInputValue(state.selectedScenario);
      }
      renderTopologyVersions(payload);
      renderSummary(payload);
      renderSpawnControls(payload);
      renderMainTerminal(payload.job);
      let nodeCards = payload.node_cards || [];
      if (!(nodeCards || []).length && state.pendingStart && jobRunning && state.pendingStart.scenario && state.pendingStart.scenario === payloadScenario) {
        nodeCards = state.pendingStart.node_cards || [];
      }
      if ((nodeCards || []).length && (!jobRunning || !state.pendingStart || state.pendingStart.scenario !== payloadScenario || (payload.node_cards || []).length)) {
        state.pendingStart = null;
      }
      renderNodes(nodeCards);
      renderEvents(payload.recent_events || []);
      if (payload.job && payload.job.status === 'failed') {
        showBanner('error', payload.job.detail || payload.job.error || 'Topology start failed.');
      }
      scheduleRefresh(payload);
      await refreshInspector(refreshSeq);
    }

    const topologyVersionSelect = document.getElementById('topology-version');
    if (topologyVersionSelect) {
      topologyVersionSelect.addEventListener('change', (event) => {
        state.selectedScenario = event.target.value || '';
        state.selectionMode = state.selectedScenario ? 'manual' : 'auto';
        persistSelectionState();
        setScenarioInputValue(state.selectedScenario || '');
        clearBanner();
        refresh();
      });
    }
    (function initInspectorDrag() {
      const root = document.getElementById('node-inspector');
      const head = document.getElementById('inspector-head');
      head.addEventListener('mousedown', (event) => {
        state.inspector.drag = {
          dx: event.clientX - root.getBoundingClientRect().left,
          dy: event.clientY - root.getBoundingClientRect().top,
        };
      });
      document.addEventListener('mousemove', (event) => {
        if (!state.inspector.drag) return;
        root.style.left = `${Math.max(8, event.clientX - state.inspector.drag.dx)}px`;
        root.style.top = `${Math.max(8, event.clientY - state.inspector.drag.dy)}px`;
        root.style.right = 'auto';
      });
      document.addEventListener('mouseup', () => { state.inspector.drag = null; });
    })();

    loadSelectionState();
    renderStaticTierIcons();
    if (PAGE_MODE === 'topology') {
      buildSpawnControls();
    } else {
      buildDeviceMatrix();
    }
    refresh();
  </script>
</body>
</html>"""
    mode_css = (
        ".control-only { display:none !important; }"
        if page_mode == "topology"
        else ".topology-only { display:none !important; }"
    )
    hero_title = "BlockCap Topology" if page_mode == "topology" else "BlockCap Control"
    hero_subtitle = (
        "Manage simulated root, fog, edge, and endpoint nodes, device presets, runner state, and node processes in one topology-focused workspace."
        if page_mode == "topology"
        else "Run access, delegation, revocation, grant lookup, expiry, and policy actions against the selected topology without topology lifecycle controls in the way."
    )
    return (
        html.replace("__DEVICE_CATALOG_JSON__", json.dumps(device_catalog_payload()))
        .replace("__PAGE_MODE__", page_mode)
        .replace("__PAGE_TITLE__", hero_title)
        .replace("__HERO_TITLE__", hero_title)
        .replace("__HERO_SUBTITLE__", hero_subtitle)
        .replace("__MODE_CSS__", mode_css)
        .replace(
            "__SCENARIO_FIELD__",
            ""
            if page_mode == "topology"
            else '<label><span class="field-label">Scenario</span><input id="scenario-name" type="text" placeholder="demo-..."></label>',
        )
        .replace(
            "__TOPOLOGY_VERSION_FIELD__",
            ""
            if page_mode == "topology"
            else '<label><span class="field-label">Topology Version</span><select id="topology-version"><option value="">None selected</option></select></label>',
        )
    )


def _simple_control_page_html() -> str:
    return _simple_console_page_html("control")


def _simple_topology_page_html() -> str:
    return _simple_console_page_html("topology")


def _simple_results_page_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>BlockCap Results</title>
  <style>
    :root { --bg:#f4efe5; --panel:#fffdf9; --ink:#182127; --muted:#647177; --line:#d9d0c2; --primary:#1f5f87; --ok:#1f7a3c; --warn:#b9770f; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--ink); font-family:"IBM Plex Sans","Segoe UI",sans-serif; }
    .wrap { max-width:1500px; margin:0 auto; padding:18px; }
    .hero, .panel, .row { background:var(--panel); border:1px solid var(--line); border-radius:18px; }
    .hero, .panel { padding:18px; margin-bottom:14px; }
    .hero { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; flex-wrap:wrap; }
    .hero h1, .panel h2, .row h3 { margin:0 0 10px; }
    .sub { color:var(--muted); font-size:13px; }
    .btns { display:flex; gap:10px; flex-wrap:wrap; }
    .btn { border:none; text-decoration:none; color:#fff; background:var(--primary); padding:10px 14px; border-radius:12px; font-weight:700; }
    .summary, .metrics-list { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; }
    .summary-card, .metric-card { background:#fff; border:1px solid var(--line); border-radius:14px; padding:12px; }
    .summary-card b { display:block; margin-top:4px; font-size:18px; }
    .setup-table { width:100%; border-collapse:collapse; font-size:14px; }
    .setup-table th, .setup-table td { text-align:left; padding:10px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
    .setup-field { display:flex; align-items:center; gap:10px; }
    .setup-icon { width:28px; height:28px; border-radius:50%; background:#e7eff5; color:#12384e; display:grid; place-items:center; font-weight:800; font-size:11px; flex:none; }
    .rows { display:grid; gap:14px; }
    .row { display:grid; grid-template-columns:minmax(0, 1.1fr) minmax(320px, .9fr); gap:16px; padding:16px; }
    .chart-box, .text-box { background:#fff; border:1px solid var(--line); border-radius:14px; padding:12px; min-height:280px; }
    .chart-box img { width:100%; height:100%; object-fit:contain; display:block; }
    .text-box table { width:100%; border-collapse:collapse; font-size:13px; }
    .text-box th, .text-box td { text-align:left; padding:7px 6px; border-bottom:1px solid var(--line); vertical-align:top; }
    .note { background:#fff8e7; border:1px solid #ebd4a5; color:#6a4b00; padding:10px 12px; border-radius:12px; font-size:13px; margin-top:10px; }
    .placeholder { display:grid; place-items:center; color:var(--muted); min-height:260px; font-size:13px; }
    @media (max-width: 1100px) { .row { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div>
        <h1>BlockCap Results</h1>
        <div class="sub">Realtime Python-style charts built from the metrics already measured by the BlockCap runtime and experiment artifacts.</div>
      </div>
      <div class="btns">
        <a class="btn" href="/topology">Topology</a>
        <a class="btn" href="/control">Control</a>
        <a class="btn" href="/">Home</a>
      </div>
    </section>

    <section class="panel">
      <div class="btns" style="justify-content:space-between; align-items:end;">
        <div style="min-width:260px;">
          <div class="sub" style="margin-bottom:6px;">Topology</div>
          <select id="scenario-select" style="min-width:260px;">
            <option value="">Auto</option>
          </select>
        </div>
        <div class="btns">
          <button class="btn" type="button" onclick="clearScenarioSelection()">Clear Selection</button>
        </div>
      </div>
      <div class="sub" id="scenario-source" style="margin-top:10px;">Showing the default results selection.</div>
    </section>

    <div id="summary" class="summary"></div>

    <section class="panel">
      <h2>Experimental Setup</h2>
      <div class="sub" style="margin-bottom:10px;">Consensus parameters used in experiments: block time, validator set, timeout, epoch length, and rationale.</div>
      <div id="research-source" class="note" style="margin-bottom:10px;">Research metrics are being shown from the shared results artifacts under results/.</div>
      <div id="experimental-setup-top"></div>
    </section>

    <div id="rows" class="rows"></div>
  </div>
  <script>
    const esc = (v) => String(v ?? '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    const state = { scenario: '', chartStamp: '', rowsBuilt: false, manualScenario: '' };
    const rowConfig = [
      { key:'end_to_end_latency', chart:'end_to_end_latency', title:'End-to-End Access Latency', description:'Cold and warm access round-trip time from request to grant or deny at Cloud, Fog, Edge, and Endpoint tiers. X-axis: node tier. Y-axis: latency in milliseconds.' },
      { key:'load_tests', chart:'load_test_throughput', title:'Load-Test Throughput', description:'Concurrency runs at 10, 50, and 100 simultaneous requests with throughput. X-axis: concurrent requests. Y-axis: requests per second.' },
      { key:'load_tests', chart:'load_test_latency', title:'Load-Test Latency', description:'Concurrency runs at 10, 50, and 100 simultaneous requests with mean latency and p95 latency. X-axis: concurrent requests. Y-axis: latency in milliseconds.' },
      { key:'token_lifecycle_latency', chart:'token_lifecycle_latency', title:'Token Lifecycle Latencies', description:'Per-operation latency for issue, delegate, revoke, expiry check, check grant, and ensure policy. X-axis: latency in milliseconds. Y-axis: operation at tier.' },
      { key:'revocation_propagation', chart:'revocation_propagation', title:'Revocation Propagation', description:'Time until validators process the block carrying the revocation and the result becomes visible across the active set. X-axis: tier or scenario bucket. Y-axis: latency in milliseconds.' },
      { key:'live_flow_activity', chart:'live_flow_activity', title:'Live Flow Activity', description:'Realtime counts for total events, granted decisions, denied decisions, errors, and active flows. X-axis: time. Y-axis: count.' },
      { key:'contract_metrics', chart:'contract_loc', title:'Smart Contract LOC', description:'Source lines of code for each contract module. X-axis: contract module. Y-axis: non-blank LOC.' },
      { key:'contract_metrics', chart:'contract_deployment_gas', title:'Smart Contract Deployment Gas', description:'Measured deployment gas for each contract module. X-axis: contract module. Y-axis: gas used.' },
      { key:'contract_metrics', chart:'contract_bytecode_kb', title:'Smart Contract Bytecode Size', description:'Compiled bytecode size for each contract module. X-axis: contract module. Y-axis: kilobytes.' },
      { key:'gas_comparison', chart:'gas_comparison', title:'Gas Cost Comparison', description:'Per-operation gas comparison across BlockCap, BlendCAC, and ACS-IoT using the baseline JSON file. X-axis: operation. Y-axis: gas cost.' },
    ];

    function renderSummary(cards) {
      document.getElementById('summary').innerHTML = (cards || []).map((item) => `<div class="summary-card"><div class="sub">${esc(item.label)}</div><b>${esc(item.value)}</b></div>`).join('');
    }

    function renderScenarioSelect(payload) {
      const select = document.getElementById('scenario-select');
      const scenarios = (payload.scenarios || []).filter((row) => row && row.exists);
      const existingNames = new Set(scenarios.map((row) => row.scenario));
      const savedOnlyRuns = (payload.saved_runs || []).filter((row) => row && row.scenario && !existingNames.has(row.scenario));
      const current = state.manualScenario || payload.selected_scenario || '';
      const existingOptions = scenarios.map((row) => {
        const suffix = row.running ? ' | running' : ' | existing';
        return `<option value="${esc(row.scenario)}">${esc(row.scenario + suffix)}</option>`;
      }).join('');
      const savedOptions = savedOnlyRuns.map((row) => {
        const updated = row.updated_at_ms ? ` | ${new Date(row.updated_at_ms).toLocaleDateString()}` : '';
        return `<option value="${esc(row.scenario)}">${esc(row.scenario + ' | saved snapshot' + updated)}</option>`;
      }).join('');
      const groups = [
        scenarios.length ? `<optgroup label="Existing Topologies">${existingOptions}</optgroup>` : '',
        savedOnlyRuns.length ? `<optgroup label="Saved Results">${savedOptions}</optgroup>` : '',
      ].filter(Boolean).join('');
      select.innerHTML = `<option value="">Auto</option>${groups}`;
      const selectableNames = new Set([...scenarios.map((row) => row.scenario), ...savedOnlyRuns.map((row) => row.scenario)]);
      select.value = selectableNames.has(current) ? current : '';
      const selectedLabel = state.manualScenario
        ? `Showing results for topology '${state.manualScenario}'.`
        : `Showing ${payload.selected_scenario ? `the currently running topology '${payload.selected_scenario}'` : 'the default results selection'}.`;
      document.getElementById('scenario-source').textContent = selectedLabel;
    }

    function renderResearchSource(source) {
      const mode = source && source.mode === 'scenario_snapshot' ? 'Scenario Snapshot' : 'Shared Artifacts';
      const note = source && source.note ? source.note : 'Research metrics are being shown from the shared results artifacts under results/.';
      document.getElementById('research-source').innerHTML = `<b>${esc(mode)}</b><div style="margin-top:6px;">${esc(note)}</div>`;
    }

    function formatValue(value, key='') {
      if (value === null || value === undefined || value === '') return '-';
      if (typeof value === 'number' && key === 'ts_unix_ms') {
        return new Date(value).toLocaleTimeString();
      }
      if (Array.isArray(value)) {
        if (!value.length) return '-';
        if (typeof value[0] === 'object') return `${value.length} items`;
        return value.join(', ');
      }
      if (typeof value === 'object') {
        const entries = Object.entries(value || {});
        if (!entries.length) return '-';
        return entries.map(([k, v]) => `${k}: ${v}`).join(', ');
      }
      return String(value);
    }

    function sectionTable(section) {
      if (!section || section.available === false) {
        return `<div class="placeholder">${esc((section && section.reason) || 'No data yet.')}</div>`;
      }
      const rows = section.rows || [];
      if (!rows.length) {
        return '<div class="placeholder">No rows yet.</div>';
      }
      const headers = Object.keys(rows[0]);
      return `<table><thead><tr>${headers.map((key) => `<th>${esc(key.replaceAll('_',' '))}</th>`).join('')}</tr></thead><tbody>${rows.slice(0, 12).map((row) => `<tr>${headers.map((key) => `<td>${esc(formatValue(row[key], key))}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    }

    function renderExperimentalSetupTop(section) {
      const root = document.getElementById('experimental-setup-top');
      if (!section || section.available === false) {
        root.innerHTML = `<div class="placeholder">${esc((section && section.reason) || 'No data yet.')}</div>`;
        return;
      }
      const iconMap = {
        'Block Time (s)': 'BT',
        'Validator Set Size': 'VS',
        'Validator Nodes': 'VN',
        'Request Timeout (s)': 'RT',
        'Epoch Length': 'EL',
        'Selection Rationale': 'SR',
      };
      root.innerHTML = `<table class="setup-table"><thead><tr><th>Field</th><th>Value</th></tr></thead><tbody>${(section.rows || []).map((row) => `
        <tr>
          <td><div class="setup-field"><span class="setup-icon">${esc(iconMap[row.label] || 'I')}</span><span>${esc(row.label)}</span></div></td>
          <td>${esc(formatValue(row.value, row.label))}</td>
        </tr>`).join('')}</tbody></table>`;
    }

    function ensureRows() {
      if (state.rowsBuilt) return;
      document.getElementById('rows').innerHTML = rowConfig.map((row) => {
        const left = row.chart
          ? `<div class="chart-box"><img id="chart-${esc(row.chart)}" alt=""></div>`
          : `<div class="chart-box" id="static-${esc(row.key)}"></div>`;
        const right = `<div class="text-box"><h3>${esc(row.title)}</h3><div class="sub">${esc(row.description)}</div><div id="detail-${esc(row.chart || row.key)}"></div></div>`;
        return `<section class="row">${left}${right}</section>`;
      }).join('');
      state.rowsBuilt = true;
    }

    function renderRows(payload) {
      ensureRows();
      const sections = payload.research_sections || {};
      const scenario = payload.selected_scenario || '';
      renderExperimentalSetupTop(sections.experimental_setup || {});
      const nowStamp = String(Math.floor(Date.now() / 10000));
      const scenarioChanged = state.scenario !== scenario;
      const shouldRefreshCharts = scenarioChanged || state.chartStamp !== nowStamp;
      state.scenario = scenario;
      if (shouldRefreshCharts) state.chartStamp = nowStamp;

      rowConfig.forEach((row) => {
        const section = row.key === 'live_flow_activity' ? { available:true, rows:(payload.live_series || []) } : (sections[row.key] || {});
        const note = section.note ? `<div class="note">${esc(section.note)}</div>` : '';
        const detailTarget = document.getElementById(`detail-${row.chart || row.key}`);
        if (detailTarget) {
          detailTarget.innerHTML = `${note}${sectionTable(section)}`;
        }
        if (row.chart) {
          const img = document.getElementById(`chart-${row.chart}`);
          if (img && shouldRefreshCharts) {
            img.src = `/results/chart/${encodeURIComponent(row.chart)}?scenario=${encodeURIComponent(scenario)}&t=${state.chartStamp}`;
          }
        } else {
          const staticBox = document.getElementById(`static-${row.key}`);
          if (staticBox) {
            staticBox.innerHTML = sectionTable(section);
          }
        }
      });
    }

    async function refresh() {
      const params = new URLSearchParams();
      if (state.manualScenario) params.set('scenario', state.manualScenario);
      const qs = params.toString();
      const payload = await fetch(`/results/data${qs ? `?${qs}` : ''}`).then((r) => r.json());
      renderScenarioSelect(payload);
      renderSummary(payload.summary_cards || []);
      renderResearchSource(payload.research_source || null);
      renderRows(payload);
    }

    function clearScenarioSelection() {
      state.manualScenario = '';
      document.getElementById('scenario-select').value = '';
      refresh();
    }
    window.clearScenarioSelection = clearScenarioSelection;

    document.getElementById('scenario-select').addEventListener('change', (event) => {
      state.manualScenario = event.target.value || '';
      refresh();
    });

    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>"""


def make_app(repo_root: str | None = None, node_role: str | None = None) -> Flask:
    app = Flask(__name__)
    Swagger(app, template={
        "info": {"title": "BlockCap Node API", "version": "1.0"},
        "securityDefinitions": {
            "AdminToken": {"type": "apiKey", "in": "header", "name": "Authorization"}
        },
    })
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # if behind a proxy
    node_role = str(node_role or os.getenv("NODE_ROLE", NODE_ROLE)).strip().lower()
    parent_url = os.getenv("PARENT_URL", PARENT_URL).rstrip("/")
    policy_file = os.getenv("POLICY_FILE", POLICY_FILE).strip()

    # Construct orchestrator. In REAL mode, signature enforcement can be toggled.
    enforce_signature = os.getenv("ORCH_ENFORCE_SIG", "1") != "0"
    registrar_role_map = {
      "cloud": "Cloud",
      "fog": "Fog",
      "edge": "Edge",
      "endpoint": "Sensor",
    }
    orch = Orchestrator(
      repo_root=repo_root,
      registrar_role=registrar_role_map.get(node_role, "Cloud"),
      enforce_signature=enforce_signature,
    )
    infra = InfrastructureController(repo_root or os.getcwd())
    results_dir = ensure_results_dir(repo_root or os.getcwd())
    ui_recorder = UiResultsRecorder(results_dir)
    # If you prefer a fixed registrar, set it here (else orchestrator uses prefunded_keys.json[0])
    # orch.registrar_addr = "0x1111..."
    local_sig = _local_signature_from_node_details(repo_root)
    rate_limiter = TokenBucketRateLimiter({
        "cloud": 200.0,
        "fog": 100.0,
        "edge": 50.0,
        "sensor": 10.0,
        "actuator": 10.0,
        "endpoint": 10.0,
        "unknown": 10.0,
    })
    # Ensure validator auto‑voter runs even after restarts
    try:
        if local_sig and orch.is_validator():
            orch.start_validator_listener()   # idempotent
    except Exception as _e:
        print(f"[listener] startup check skipped: {_e}")

    def _node_root_dir() -> Path:
        return Path(repo_root or ".").resolve()

    def _data_dir() -> Path:
        raw = os.getenv("DATA_DIR", "").strip()
        return Path(raw).resolve() if raw else _node_root_dir() / "data"

    def _genesis_dir() -> Path:
        raw = os.getenv("GENESIS_DIR", "").strip()
        return Path(raw).resolve() if raw else _node_root_dir() / "genesis"

    def _bootstrap_payload_paths() -> tuple[Path, Path, Path]:
        data_dir = _data_dir()
        genesis_dir = _genesis_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        genesis_dir.mkdir(parents=True, exist_ok=True)
        node_root_dir = _node_root_dir()
        return (
            genesis_dir / "genesis.json",
            data_dir / "NodeRegistry.json",
            node_root_dir / "prefunded_keys.json",
        )

    def _persist_bootstrap_payload(payload: dict[str, Any]) -> dict[str, str]:
        genesis_path, registry_path, prefunded_path = _bootstrap_payload_paths()
        written: dict[str, str] = {}
        genesis_b64 = str(payload.get("genesis_b64") or "").strip()
        if genesis_b64:
            genesis_path.write_bytes(base64.b64decode(genesis_b64))
            written["genesis"] = str(genesis_path)
        node_registry = payload.get("node_registry")
        if node_registry is not None:
            registry_path.write_text(json.dumps(node_registry))
            written["node_registry"] = str(registry_path)
        prefunded_keys = payload.get("prefunded_keys")
        if prefunded_keys is not None:
            prefunded_path.write_text(json.dumps(prefunded_keys))
            written["prefunded_keys"] = str(prefunded_path)
        enode = str(payload.get("enode") or "").strip()
        if enode:
            data_dir = _data_dir()
            for rel in ("enode.txt",):
                path = data_dir / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(enode + "\n")
            for rel in ("data/enode.txt", "static/enode.txt", "client_inbox/enode.txt"):
                path = _node_root_dir() / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(enode + "\n")
            written["enode"] = enode
        ready_flag = _data_dir() / ".bootstrap_ready"
        ready_flag.write_text("1")
        written["ready_flag"] = str(ready_flag)
        return written

    def _forward_to_parent(path: str):
        if node_role != "endpoint" or not parent_url:
            return None
        target_url = f"{parent_url}{path}"
        try:
            if request.method == "GET":
                upstream = requests.get(target_url, params=request.args, timeout=10)
            else:
                upstream = requests.post(target_url, json=request.get_json(silent=True), timeout=10)
            try:
                payload = upstream.json()
                return jsonify(payload), upstream.status_code
            except ValueError:
                return Response(
                    upstream.content,
                    status=upstream.status_code,
                    content_type=upstream.headers.get("Content-Type", "application/json"),
                )
        except requests.RequestException as exc:
            return err("parent_forward_failed", 502, detail=str(exc), parent_url=target_url)

    def _role_allows(*roles: str):
        allowed = {str(role).strip().lower() for role in roles}
        if node_role not in allowed:
            return err("route_unavailable", 404, detail=f"available_on:{','.join(sorted(allowed))}")
        return None

    def _iter_policies(active_orch: Orchestrator) -> list[dict[str, Any]]:
        policies: list[dict[str, Any]] = []
        if not hasattr(active_orch, "_w3_call") or not hasattr(active_orch, "_contract"):
            return policies
        try:
            next_policy_id = int(active_orch._w3_call(active_orch._contract.functions.nextPolicyId()))  # type: ignore[attr-defined]
        except Exception:
            next_policy_id = 0
        for policy_id in range(1, max(1, next_policy_id)):
            try:
                policy = active_orch.get_policy(policy_id)
            except Exception:
                continue
            normalized = dict(policy)
            normalized["policyId"] = policy_id
            if not normalized.get("isDeprecated", False):
                policies.append(normalized)
        return policies

    def _registered_nodes_snapshot() -> list[dict[str, Any]]:
        details = infra.scenario_details(active_only=True)
        scenario = (details or {}).get("scenario") or {}
        rows: list[dict[str, Any]] = []
        root_payload = scenario.get("root") or {}
        if root_payload:
            root_details = dict(root_payload.get("node_details") or {})
            rows.append({
                "key": "root",
                "node_id": root_details.get("node_id") or root_details.get("nodeId") or orch.local_node_id,
                "node_name": root_details.get("node_name") or root_details.get("nodeName") or orch.local_node_name,
                "tier": "cloud",
                "signature": root_details.get("signature") or local_sig,
                "rpc_url": root_payload.get("rpc_url"),
                "api_url": root_payload.get("api_url"),
                "directory": root_payload.get("directory"),
            })
        for node in list(scenario.get("nodes") or []):
            rows.append({
                "key": node.get("key"),
                "node_id": node.get("node_id") or node.get("nodeId"),
                "node_name": node.get("name") or node.get("node_name") or node.get("nodeName"),
                "tier": node.get("tier"),
                "signature": node.get("signature"),
                "rpc_url": node.get("rpc_url"),
                "api_url": node.get("api_url"),
                "directory": node.get("directory"),
                "simulated_device": node.get("simulated_device"),
            })
        return rows

    def _spec_payload() -> dict[str, Any]:
        base_endpoints = [
            {"path": "/health", "methods": ["GET"], "roles": ["cloud", "fog", "edge", "endpoint"]},
            {"path": "/metrics/latency", "methods": ["GET"], "roles": ["cloud", "fog", "edge", "endpoint"]},
            {"path": "/access", "methods": ["POST"], "roles": ["cloud", "fog", "edge", "endpoint"]},
            {"path": "/delegate", "methods": ["POST"], "roles": ["cloud", "fog", "edge"]},
            {"path": "/grant", "methods": ["GET"], "roles": ["cloud", "fog", "edge", "endpoint"]},
            {"path": "/register-node", "methods": ["POST"], "roles": ["cloud", "fog", "edge"]},
            {"path": "/bootstrap-ack", "methods": ["POST"], "roles": ["fog", "edge", "endpoint"]},
        ]
        if node_role == "cloud":
            base_endpoints.extend([
                {"path": "/admin/policy/create", "methods": ["POST"], "roles": ["cloud"]},
                {"path": "/admin/policy/deprecate", "methods": ["POST"], "roles": ["cloud"]},
                {"path": "/admin/policy/list", "methods": ["GET"], "roles": ["cloud"]},
                {"path": "/admin/nodes/list", "methods": ["GET"], "roles": ["cloud"]},
                {"path": "/admin/grant/revoke", "methods": ["POST"], "roles": ["cloud"]},
            ])
        return {
            "node_role": node_role,
            "parent_url": parent_url,
            "admin_token_required": bool(os.getenv("ADMIN_TOKEN", ADMIN_TOKEN).strip()),
            "endpoints": base_endpoints,
        }

    def _load_policy_file_at_startup() -> None:
        if not policy_file or not os.path.exists(policy_file):
            return
        for _ in range(30):
            try:
                if orch.check_if_deployed():
                    break
            except Exception:
                pass
            time.sleep(1.0)
        try:
            with open(policy_file, "r") as handle:
                config = json.load(handle)
        except Exception as exc:
            print(f"[startup] failed to load policy file {policy_file}: {exc}")
            return
        for policy in list(config.get("policies") or []):
            try:
                result = orch.ensure_policy(
                    policy["from_role"],
                    policy["to_role"],
                    policy["ops"],
                    policy.get("resource", ""),
                )
                print(f"[startup] policy {policy}: {result.get('status')}")
            except Exception as exc:
                print(f"[startup] policy load failed for {policy}: {exc}")

    def _health_payload() -> dict[str, Any]:
        try:
            return {
                "deployed": orch.check_if_deployed(),
                "validators": orch.qbft_get_validators(),
                "is_validator": orch.is_validator(),
            }
        except Exception as exc:
            return {"deployed": False, "validators": "", "is_validator": False, "detail": str(exc)}

    def _sample_ui_results(
        selected_scenario: str | None,
        infra_details: dict[str, Any],
        *,
        research_sections: dict[str, Any] | None = None,
        summary_cards: list[dict[str, Any]] | None = None,
        event_stats: dict[str, Any] | None = None,
        active_flows: list[dict[str, Any]] | None = None,
        latency_summary: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        ui_recorder.record_snapshot(
            scenario=selected_scenario,
            event_stats=event_stats if event_stats is not None else orch.event_stats(),
            active_flows=active_flows if active_flows is not None else orch.active_flow_summaries(),
            latency_summary=latency_summary if latency_summary is not None else orch.latency_summary(),
            scenario_details=infra_details,
            research_sections=research_sections,
            summary_cards=summary_cards,
        )
        return ui_recorder.series(selected_scenario)

    def _control_payload(selected_scenario: str | None = None) -> dict[str, Any]:
        infra_details = infra.scenario_details(selected_scenario)
        active_scenario = infra_details.get("selected_scenario")
        current_job = infra.current_job()
        runtime_metrics = _topology_runtime_metrics(
            infra_details=infra_details,
            current_api_url=request.host_url.rstrip("/"),
            orch=orch,
        )
        recent_events = (runtime_metrics or {}).get("recent_events") or orch.recent_events(limit=40)
        node_cards = _build_node_cards(
            infra_details.get("scenario"),
            recent_events,
            local_node_id=orch.local_node_id,
            local_node_name=orch.local_node_name,
            selected_scenario=active_scenario,
            job=current_job,
        )
        if not node_cards:
            node_cards = _build_pending_node_cards(current_job, selected_scenario=active_scenario or selected_scenario)
        measured_metrics = _measured_metrics_catalog()
        return {
            "node": {
                "node_id": orch.local_node_id,
                "node_name": orch.local_node_name,
                "node_tier": orch.local_node_tier,
                "rpc_url": orch.besu_rpc_url,
                "api_url": request.host_url.rstrip("/"),
                "local_signature": local_sig,
            },
            "health": _health_payload(),
            "selected_scenario": active_scenario,
            "selection_mode": "manual" if selected_scenario else "auto",
            "scenario": infra_details.get("scenario"),
            "job": current_job,
            "scenarios": infra.list_scenarios(),
            "event_stats": orch.event_stats(),
            "recent_events": recent_events,
            "suggested_scenario": infra.suggested_scenario_name(),
            "node_cards": node_cards,
            "measured_metrics": measured_metrics,
        }

    def _topology_payload() -> dict[str, Any]:
        infra_details = infra.scenario_details(active_only=True)
        active_scenario = infra_details.get("selected_scenario")
        current_job = infra.current_job()
        runtime_metrics = _topology_runtime_metrics(
            infra_details=infra_details,
            current_api_url=request.host_url.rstrip("/"),
            orch=orch,
        )
        recent_events = (runtime_metrics or {}).get("recent_events") or orch.recent_events(limit=40)
        node_cards = _build_node_cards(
            infra_details.get("scenario"),
            recent_events,
            local_node_id=orch.local_node_id,
            local_node_name=orch.local_node_name,
            selected_scenario=active_scenario,
            job=current_job,
        )
        if not node_cards:
            node_cards = _build_pending_node_cards(current_job, selected_scenario=active_scenario)
        job_running = bool(current_job and current_job.get("status") == "running")
        root_card = next((card for card in node_cards if card.get("key") == "root"), None)
        has_live_root = root_card is not None
        return {
            "node": {
                "node_id": orch.local_node_id,
                "node_name": orch.local_node_name,
                "node_tier": orch.local_node_tier,
                "rpc_url": orch.besu_rpc_url,
                "api_url": request.host_url.rstrip("/"),
                "local_signature": local_sig,
            },
            "health": _health_payload(),
            "selected_scenario": active_scenario,
            "selection_mode": "live",
            "scenario": infra_details.get("scenario"),
            "job": current_job,
            "event_stats": orch.event_stats(),
            "recent_events": recent_events,
            "node_cards": node_cards,
            "root_terminals": _root_terminal_preview_payload(
                infra=infra,
                scenario=active_scenario,
                root_card=root_card,
            ),
            "live_topology": {
                "scenario": active_scenario,
                "root_active": has_live_root,
            },
            "spawn_controls": {
                "root_enabled": (not active_scenario) and (not job_running),
                "fog_enabled": has_live_root and (not job_running),
                "edge_enabled": has_live_root and (not job_running),
                "endpoint_enabled": has_live_root and (not job_running),
            },
        }

    def _results_payload(
        selected_scenario: str | None = None,
        compare_scenario: str | None = None,
        *,
        sample: bool = True,
    ) -> dict[str, Any]:
        infra_details = infra.scenario_details(selected_scenario)
        active_scenario = infra_details.get("selected_scenario")
        scenario_rows = infra.list_scenarios()
        saved_runs = ui_recorder.available_runs()
        saved_run = ui_recorder.load_run(active_scenario) if active_scenario else None
        runtime_metrics = _topology_runtime_metrics(
            infra_details=infra_details,
            current_api_url=request.host_url.rstrip("/"),
            orch=orch,
        ) or _saved_run_runtime_metrics(saved_run)
        artifacts = _collect_result_artifacts(results_dir)
        artifact_meta = _collect_result_artifact_meta(results_dir)
        research_sections, research_source = _select_research_sections(
            selected_scenario=active_scenario,
            artifacts=artifacts,
            artifact_meta=artifact_meta,
            ui_recorder=ui_recorder,
        )
        event_stats = (runtime_metrics or {}).get("event_stats") or orch.event_stats()
        active_flows = (runtime_metrics or {}).get("active_flows") or orch.active_flow_summaries()
        recent_events = (runtime_metrics or {}).get("recent_events") or orch.recent_events(limit=100)
        recent_flows = (runtime_metrics or {}).get("recent_flows") or orch.flow_summaries(limit=50)
        latency_summary = (runtime_metrics or {}).get("latency_summary") or orch.latency_summary()
        scenario_for_summary = infra_details
        snapshot_scenario = ((saved_run or {}).get("scenario_details") or {})
        live_scenario = (runtime_metrics or {}).get("scenario_details") or {}
        current_scenario_payload = (infra_details.get("scenario") or {})
        live_scenario_payload = (live_scenario.get("scenario") or {})
        if int(live_scenario_payload.get("node_count") or 0) > 0:
            scenario_for_summary = live_scenario
        elif not current_scenario_payload and snapshot_scenario:
            scenario_for_summary = snapshot_scenario
        elif int(current_scenario_payload.get("node_count") or 0) == 0 and snapshot_scenario:
            scenario_for_summary = snapshot_scenario
        node_count = int(((scenario_for_summary.get("scenario") or {}).get("node_count") or 0))
        if not node_count:
            latest_snapshot = list((saved_run or {}).get("snapshots") or [])
            if latest_snapshot:
                node_count = int(sum(((latest_snapshot[-1].get("node_status_counts") or {}).values())))
        summary_cards = [
            {"label": "Scenario", "value": active_scenario or "-"},
            {"label": "Nodes", "value": node_count},
            {"label": "Events", "value": event_stats.get("total_events", 0)},
            {"label": "Granted", "value": ((event_stats.get("status_counts") or {}).get("ok", 0))},
            {"label": "Denied", "value": ((event_stats.get("status_counts") or {}).get("denied", 0))},
            {"label": "Errors", "value": ((event_stats.get("status_counts") or {}).get("error", 0))},
            {"label": "Research Source", "value": "Scenario Snapshot" if (research_source or {}).get("mode") == "scenario_snapshot" else "Shared Artifacts"},
            {"label": "Research Updated", "value": (research_source or {}).get("updated_at_label") or "-"},
        ]
        if sample:
            live_series = _sample_ui_results(
                active_scenario,
                scenario_for_summary,
                research_sections=research_sections,
                summary_cards=summary_cards,
                event_stats=event_stats,
                active_flows=active_flows,
                latency_summary=latency_summary,
            )
        else:
            live_series = (runtime_metrics or {}).get("live_series") or ui_recorder.series(active_scenario)
        return {
            "node": {
                "node_id": orch.local_node_id,
                "node_name": orch.local_node_name,
                "node_tier": orch.local_node_tier,
                "rpc_url": orch.besu_rpc_url,
                "api_url": request.host_url.rstrip("/"),
                "local_signature": local_sig,
            },
            "health": _health_payload(),
            "selected_scenario": active_scenario,
            "scenario": scenario_for_summary.get("scenario"),
            "event_stats": event_stats,
            "active_flows": active_flows,
            "recent_events": recent_events,
            "recent_flows": recent_flows,
            "latency_summary": latency_summary,
            "live_series": live_series,
            "summary_cards": summary_cards,
            "saved_runs": saved_runs,
            "compare_run": ui_recorder.load_run(compare_scenario) if compare_scenario else None,
            "scenarios": scenario_rows,
            "result_scenarios": _result_scenarios(
                infra_rows=scenario_rows,
                saved_runs=saved_runs,
                selected_scenario=active_scenario,
            ),
            "artifacts": artifacts,
            "artifact_meta": artifact_meta,
            "research_sections": research_sections,
            "research_source": research_source,
            "measured_metrics": _measured_metrics_catalog(),
        }

    def _chart_placeholder_svg(message: str) -> str:
        safe = (
            str(message or "No data")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="420" viewBox="0 0 960 420">'
            '<rect x="0" y="0" width="960" height="420" fill="#fffdf9" stroke="#d9d0c2"/>'
            f'<text x="480" y="210" text-anchor="middle" fill="#647177" font-size="20">{safe}</text>'
            '</svg>'
        )

    def _results_chart_svg(chart_id: str, selected_scenario: str | None = None) -> str:
        payload = _results_payload(selected_scenario, sample=False)
        sections = payload.get("research_sections") or {}
        live_series = payload.get("live_series") or []

        try:
            mpl_config_dir = results_dir / ".mplconfig"
            mpl_config_dir.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("MPLCONFIGDIR", str(mpl_config_dir))
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except Exception as exc:
            return _chart_placeholder_svg(f"Matplotlib unavailable: {exc}")

        try:
            plt.style.use("seaborn-v0_8-whitegrid")
        except Exception:
            pass

        def finish(fig) -> str:
            buffer = io.StringIO()
            fig.tight_layout()
            fig.savefig(buffer, format="svg", bbox_inches="tight")
            plt.close(fig)
            return buffer.getvalue()

        if chart_id == "end_to_end_latency":
            section = sections.get("end_to_end_latency") or {}
            rows = section.get("rows") or []
            if not rows:
                return _chart_placeholder_svg(section.get("reason") or "No end-to-end latency data")
            tiers = ["Cloud", "Fog", "Edge", "Endpoint"]
            cold_map = {row.get("tier"): row.get("mean_latency_ms", 0) or 0 for row in rows if row.get("condition") == "Cold"}
            warm_map = {row.get("tier"): row.get("mean_latency_ms", 0) or 0 for row in rows if row.get("condition") == "Warm"}
            fig, ax = plt.subplots(figsize=(8.8, 4.2))
            x = list(range(len(tiers)))
            width = 0.35
            ax.bar([idx - width / 2 for idx in x], [cold_map.get(tier, 0) for tier in tiers], width=width, label="Cold", color="#1f5f87")
            ax.bar([idx + width / 2 for idx in x], [warm_map.get(tier, 0) for tier in tiers], width=width, label="Warm", color="#1f7a3c")
            ax.set_title("End-to-End Access Latency")
            ax.set_xlabel("Node Tier")
            ax.set_ylabel("Latency (ms)")
            ax.set_xticks(x, tiers)
            ax.legend()
            return finish(fig)

        if chart_id in {"load_test_throughput", "load_test_latency"}:
            section = sections.get("load_tests") or {}
            rows = section.get("rows") or []
            if not rows:
                return _chart_placeholder_svg(section.get("reason") or "No load-test data")
            rows = sorted(rows, key=lambda item: int(item.get("concurrency") or 0))
            conc = [int(row.get("concurrency") or 0) for row in rows]
            fig, ax = plt.subplots(figsize=(8.8, 4.2))
            if chart_id == "load_test_throughput":
                ax.plot(conc, [row.get("throughput_rps", 0) or 0 for row in rows], marker="o", linewidth=2.5, color="#1f5f87")
                ax.set_title("Load-Test Throughput")
                ax.set_ylabel("Throughput (req/s)")
            else:
                ax.plot(conc, [row.get("mean_latency_ms", 0) or 0 for row in rows], marker="o", linewidth=2.5, color="#1f5f87", label="Mean")
                ax.plot(conc, [row.get("p95_latency_ms", 0) or 0 for row in rows], marker="o", linewidth=2.5, color="#b12c2c", label="P95")
                ax.set_title("Load-Test Latency")
                ax.set_ylabel("Latency (ms)")
                ax.legend()
            ax.set_xlabel("Concurrent Requests")
            ax.set_xticks(conc)
            return finish(fig)

        if chart_id == "token_lifecycle_latency":
            section = sections.get("token_lifecycle_latency") or {}
            rows = section.get("rows") or []
            if not rows:
                return _chart_placeholder_svg(section.get("reason") or "No token lifecycle data")
            labels = [f'{row.get("operation")} @ {row.get("tier")}' for row in rows]
            values = [row.get("mean_latency_ms", 0) or 0 for row in rows]
            fig, ax = plt.subplots(figsize=(9.0, max(4.5, len(labels) * 0.45)))
            ax.barh(labels, values, color="#1f5f87")
            ax.set_title("Token Lifecycle Latencies")
            ax.set_xlabel("Mean Latency (ms)")
            ax.set_ylabel("Operation / Tier")
            ax.invert_yaxis()
            return finish(fig)

        if chart_id == "revocation_propagation":
            section = sections.get("revocation_propagation") or {}
            rows = section.get("rows") or []
            if not rows:
                return _chart_placeholder_svg(section.get("reason") or "No revocation propagation data")
            labels = [row.get("tier") or "Unknown" for row in rows]
            values = [row.get("mean_latency_ms", 0) or 0 for row in rows]
            fig, ax = plt.subplots(figsize=(8.8, 4.2))
            ax.bar(labels, values, color="#8b4d27")
            ax.set_title("Revocation Propagation Latency")
            ax.set_xlabel("Tier")
            ax.set_ylabel("Latency (ms)")
            return finish(fig)

        if chart_id == "live_flow_activity":
            if not live_series:
                return _chart_placeholder_svg("No live flow samples yet")
            tail = live_series[-30:]
            labels = [time.strftime("%H:%M:%S", time.localtime((point.get("ts_unix_ms", 0) or 0) / 1000)) for point in tail]
            x = list(range(len(tail)))
            fig, ax = plt.subplots(figsize=(9.0, 4.4))
            ax.plot(x, [point.get("total_events", 0) for point in tail], label="Total Events", color="#1f5f87", linewidth=2.2)
            ax.plot(x, [point.get("granted", 0) for point in tail], label="Granted", color="#1f7a3c", linewidth=2.0)
            ax.plot(x, [point.get("denied", 0) for point in tail], label="Denied", color="#b9770f", linewidth=2.0)
            ax.plot(x, [point.get("errors", 0) for point in tail], label="Errors", color="#b12c2c", linewidth=2.0)
            ax.plot(x, [point.get("active_flows", 0) for point in tail], label="Active Flows", color="#6a4fb5", linewidth=2.0)
            tick_indexes = [idx for idx in range(len(labels)) if idx % max(1, len(labels) // 6 or 1) == 0]
            ax.set_xticks(tick_indexes, [labels[idx] for idx in tick_indexes], rotation=20)
            ax.set_title("Live Flow Activity")
            ax.set_xlabel("Time")
            ax.set_ylabel("Count")
            ax.legend(loc="upper left")
            return finish(fig)

        if chart_id in {"contract_loc", "contract_deployment_gas", "contract_bytecode_kb"}:
            section = sections.get("contract_metrics") or {}
            rows = [row for row in (section.get("rows") or []) if row.get("contract") != "TOTALS"]
            if not rows:
                return _chart_placeholder_svg(section.get("reason") or "No contract metrics data")
            labels = [row.get("contract") or "Unknown" for row in rows]
            fig, ax = plt.subplots(figsize=(8.8, 4.2))
            if chart_id == "contract_loc":
                values = [row.get("source_lines_non_blank", 0) or 0 for row in rows]
                title = "Smart Contract Source Lines"
                ylabel = "Non-Blank LOC"
                color = "#1f5f87"
            elif chart_id == "contract_deployment_gas":
                values = [row.get("deployment_gas_used", 0) or 0 for row in rows]
                title = "Smart Contract Deployment Gas"
                ylabel = "Gas Used"
                color = "#8b4d27"
            else:
                values = [row.get("bytecode_size_kb", 0) or 0 for row in rows]
                title = "Smart Contract Bytecode Size"
                ylabel = "Bytecode Size (KB)"
                color = "#1f7a3c"
            ax.bar(labels, values, color=color)
            ax.set_title(title)
            ax.set_xlabel("Contract Module")
            ax.set_ylabel(ylabel)
            ax.tick_params(axis="x", rotation=20)
            return finish(fig)

        if chart_id == "gas_comparison":
            section = sections.get("gas_comparison") or {}
            operations = (section.get("chart") or {}).get("operations") or []
            series = (section.get("chart") or {}).get("series") or []
            if not operations or not series:
                return _chart_placeholder_svg(section.get("reason") or "No gas comparison data")
            fig, ax = plt.subplots(figsize=(9.0, 4.4))
            x = list(range(len(operations)))
            width = 0.22
            colors = ["#1f5f87", "#1f7a3c", "#8b4d27"]
            offsets = [-(width), 0.0, width]
            for index, item in enumerate(series[:3]):
                values = [(point.get("value") or 0) for point in (item.get("points") or [])]
                ax.bar([value + offsets[index] for value in x], values, width=width, label=item.get("name"), color=colors[index])
            ax.set_title("Gas Cost Comparison")
            ax.set_xlabel("Operation")
            ax.set_ylabel("Gas Cost")
            ax.set_xticks(x, operations)
            ax.legend()
            return finish(fig)

        return _chart_placeholder_svg(f"Unknown chart: {chart_id}")

    def _sampling_loop() -> None:
        while True:
            try:
                details = infra.scenario_details()
                runtime_metrics = _topology_runtime_metrics(
                    infra_details=details,
                    current_api_url="",
                    orch=orch,
                )
                stats = (runtime_metrics or {}).get("event_stats") or orch.event_stats()
                _sample_ui_results(
                    details.get("selected_scenario"),
                    details,
                    research_sections=_build_research_sections(_collect_result_artifacts(results_dir)),
                    summary_cards=[
                        {"label": "Scenario", "value": details.get("selected_scenario") or "-"},
                        {"label": "Nodes", "value": ((details.get("scenario") or {}).get("node_count", 0))},
                        {"label": "Events", "value": stats.get("total_events", 0)},
                        {"label": "Granted", "value": ((stats.get("status_counts") or {}).get("ok", 0))},
                        {"label": "Denied", "value": ((stats.get("status_counts") or {}).get("denied", 0))},
                        {"label": "Errors", "value": ((stats.get("status_counts") or {}).get("error", 0))},
                    ],
                    event_stats=stats,
                    active_flows=(runtime_metrics or {}).get("active_flows") or orch.active_flow_summaries(),
                    latency_summary=(runtime_metrics or {}).get("latency_summary") or orch.latency_summary(),
                )
            except Exception:
                pass
            time.sleep(2.0)

    threading.Thread(target=_sampling_loop, daemon=True, name="ui-results-sampler").start()

    @app.before_request
    def _start_request_timer():
        orch.begin_request(
            node_tier=orch.local_node_tier,
            condition=request.headers.get("X-Latency-Condition")
        )

    @app.teardown_request
    def _finish_request_timer(_exc):
        orch.end_request()

    # --------------- routes ---------------
    @app.get("/")
    def home():
        return Response(_home_html(), mimetype="text/html")

    @app.get("/control")
    def control():
        return Response(_simple_control_page_html(), mimetype="text/html")

    @app.get("/topology")
    def topology():
        return Response(_simple_topology_page_html(), mimetype="text/html")

    @app.get("/infrastructure/status")
    def infrastructure_status():
        return ok({
            "job": infra.current_job(),
            "scenarios": infra.list_scenarios(),
            "suggested_scenario": infra.suggested_scenario_name(),
        })

    @app.get("/infrastructure/details")
    def infrastructure_details():
        scenario = request.args.get("scenario")
        payload = infra.scenario_details(scenario)
        payload["node_cards"] = _build_node_cards(
            payload.get("scenario"),
            orch.recent_events(limit=40),
            local_node_id=orch.local_node_id,
            local_node_name=orch.local_node_name,
            selected_scenario=payload.get("selected_scenario"),
        )
        payload["measured_metrics"] = _measured_metrics_catalog()
        payload["job"] = infra.current_job()
        payload["scenarios"] = infra.list_scenarios()
        payload["suggested_scenario"] = infra.suggested_scenario_name()
        return ok(payload)

    @app.get("/infrastructure/shells")
    def infrastructure_shells():
        scenario = request.args.get("scenario")
        lines = max(1, min(_int_arg("lines", 80), 400))
        return ok(infra.shell_grid(scenario=scenario, lines=lines))

    @app.get("/infrastructure/node-logs")
    def infrastructure_node_logs():
        try:
            scenario = request.args.get("scenario")
            node_key = request.args.get("node", "root")
            process = request.args.get("process", "api")
            lines = max(1, min(_int_arg("lines", 80), 400))
            return ok(infra.node_logs(scenario=scenario, node_key=node_key, process=process, lines=lines))
        except Exception as exc:
            return err("infrastructure_log_query_failed", 404, detail=str(exc))

    @app.post("/infrastructure/open-terminal")
    def infrastructure_open_terminal():
        req, bad = require_json(["scenario", "node_key"])
        if bad:
            return bad
        try:
            return ok(
                infra.open_terminal(
                    scenario=str(req["scenario"]),
                    node_key=str(req["node_key"]),
                    process=str(req.get("process", "api")),
                )
            )
        except Exception as exc:
            return err("infrastructure_terminal_failed", 500, detail=str(exc))

    @app.post("/infrastructure/refresh-topology")
    def infrastructure_refresh_topology():
        req, bad = require_json(["scenario"])
        if bad:
            return bad
        try:
            refreshed = infra.refresh_topology(str(req["scenario"]), persist=True)
            details = infra.scenario_details(str(req["scenario"]))
            details["job"] = infra.current_job()
            details["refresh"] = refreshed
            return ok(details)
        except Exception as exc:
            return err("infrastructure_refresh_failed", 500, detail=str(exc))

    @app.post("/infrastructure/start-topology")
    def infrastructure_start_topology():
        req, bad = require_json([])
        if bad:
            return bad
        try:
            endpoint_roles = req.get("endpoint_roles") if isinstance(req.get("endpoint_roles"), list) else []
            fog_devices = req.get("fog_devices") if isinstance(req.get("fog_devices"), list) else []
            edge_devices = req.get("edge_devices") if isinstance(req.get("edge_devices"), list) else []
            endpoint_devices = req.get("endpoint_devices") if isinstance(req.get("endpoint_devices"), list) else []
            endpoint_count = int(req.get("endpoint", len(endpoint_roles) or 3))
            result = infra.start_topology(
                scenario=str(req.get("scenario") or f"demo-{int(Path.cwd().stat().st_mtime)}"),
                cloud=int(req.get("cloud", 1)),
                fog=int(req.get("fog", 2)),
                edge=int(req.get("edge", 2)),
                endpoint=endpoint_count,
                endpoint_role=str(req.get("endpoint_role", "Sensor")),
                endpoint_roles=[str(role) for role in endpoint_roles],
                fog_devices=[str(device) for device in fog_devices],
                edge_devices=[str(device) for device in edge_devices],
                endpoint_devices=[str(device) for device in endpoint_devices],
                host=str(req.get("host", "127.0.0.1")),
            )
            return ok(result, 202)
        except Exception as exc:
            return err("infrastructure_start_failed", 500, detail=str(exc))

    @app.post("/infrastructure/start-root")
    def infrastructure_start_root():
        req, bad = require_json([])
        if bad:
            return bad
        try:
            result = infra.start_root(host=str(req.get("host", "127.0.0.1")))
            return ok(result, 202)
        except Exception as exc:
            return err("infrastructure_start_root_failed", 500, detail=str(exc))

    @app.post("/infrastructure/spawn-node")
    def infrastructure_spawn_node():
        req, bad = require_json(["tier", "device_id"])
        if bad:
            return bad
        try:
            result = infra.spawn_node(
                tier=str(req["tier"]),
                device_id=str(req["device_id"]),
                endpoint_role=str(req.get("endpoint_role", "Sensor")),
                host=str(req.get("host", "127.0.0.1")),
            )
            return ok(result, 202)
        except Exception as exc:
            return err("infrastructure_spawn_node_failed", 500, detail=str(exc))

    @app.post("/infrastructure/stop-topology")
    def infrastructure_stop_topology():
        req, bad = require_json(["scenario"])
        if bad:
            return bad
        try:
            scenario_name = str(req["scenario"])
            delete_scenario = bool(req.get("delete_scenario", False))
            result = infra.stop_topology(scenario_name, delete_scenario=delete_scenario)
            scenario_details = infra.scenario_details(scenario_name) if result.get("scenario_exists_after_stop") else {"selected_scenario": scenario_name, "scenario": None}
            _sample_ui_results(
                scenario_name,
                scenario_details,
                research_sections=_build_research_sections(_collect_result_artifacts(results_dir)),
            )
            return ok(result)
        except Exception as exc:
            return err("infrastructure_stop_failed", 500, detail=str(exc))

    @app.post("/infrastructure/stop-live-topology")
    def infrastructure_stop_live_topology():
        req, bad = require_json([])
        if bad:
            return bad
        try:
            result = infra.stop_live_topology()
            return ok(result)
        except Exception as exc:
            return err("infrastructure_stop_live_failed", 500, detail=str(exc))

    @app.post("/infrastructure/stop-process")
    def infrastructure_stop_process():
        req, bad = require_json(["scenario", "node_key", "process"])
        if bad:
            return bad
        try:
            return ok(
                infra.stop_process(
                    scenario=str(req["scenario"]),
                    node_key=str(req["node_key"]),
                    process=str(req["process"]),
                )
            )
        except Exception as exc:
            return err("infrastructure_stop_process_failed", 500, detail=str(exc))

    @app.post("/infrastructure/start-process")
    def infrastructure_start_process():
        req, bad = require_json(["scenario", "node_key", "process"])
        if bad:
            return bad
        try:
            return ok(
                infra.start_process(
                    scenario=str(req["scenario"]),
                    node_key=str(req["node_key"]),
                    process=str(req["process"]),
                )
            )
        except Exception as exc:
            return err("infrastructure_start_process_failed", 500, detail=str(exc))

    @app.post("/infrastructure/stop-node")
    def infrastructure_stop_node():
        req, bad = require_json(["scenario", "node_key"])
        if bad:
            return bad
        try:
            return ok(
                infra.stop_node(
                    scenario=str(req["scenario"]),
                    node_key=str(req["node_key"]),
                )
            )
        except Exception as exc:
            return err("infrastructure_stop_node_failed", 500, detail=str(exc))

    @app.post("/infrastructure/delete-topology")
    def infrastructure_delete_topology():
        req, bad = require_json(["scenario"])
        if bad:
            return bad
        try:
            return ok(infra.delete_topology(str(req["scenario"])))
        except Exception as exc:
            return err("infrastructure_delete_topology_failed", 500, detail=str(exc))

    @app.post("/infrastructure/kill-all")
    def infrastructure_kill_all():
        try:
            return ok(infra.kill_all_spawned_processes())
        except Exception as exc:
            return err("infrastructure_kill_all_failed", 500, detail=str(exc))

    @app.get("/health")
    #@track_performance
    def health():
        def _health_bridge_mode() -> str:
            chooser = getattr(orch, "_should_use_js", None)
            if callable(chooser):
                try:
                    return "legacy_js" if chooser() else "direct_web3"
                except Exception:
                    pass
            return "legacy_js" if not os.getenv("REAL_INTERACT") else "direct_web3"

        def _health_failure_payload(exc: Exception) -> tuple[dict[str, Any], int]:
            detail = str(exc).strip()
            payload: dict[str, Any] = {
                "ok": False,
                "status": "unhealthy",
                "node_role": node_role,
                "mode": {
                    "real_interact": bool(os.getenv("REAL_INTERACT")),
                    "bridge": _health_bridge_mode(),
                },
                "summary": "Health check failed",
                "checks": {
                    "contract_bridge": "failed",
                },
            }
            if "Cannot find module" in detail and "interact.js" in detail:
                payload.update({
                    "error": "legacy_js_bridge_missing",
                    "summary": "Legacy JS bridge is selected, but interact.js is missing",
                    "problem": "The server tried to use the old Node.js blockchain bridge and could not find interact.js.",
                    "recommendation": "Start the server with REAL_INTERACT=1 to use direct web3.py, or restore Node_root/interact.js if you intentionally want the legacy bridge.",
                    "checks": {
                        "contract_bridge": "missing_interact_js",
                    },
                    "detail": "interact.js not found",
                })
                return payload, 500
            payload["error"] = "unhealthy"
            payload["detail"] = detail or type(exc).__name__
            if os.getenv("FLASK_DEBUG", "0") == "1":
                payload["trace"] = traceback.format_exc()
            return payload, 500

        try:
            deployed = orch.check_if_deployed()
            validators_raw = (orch.qbft_get_validators() or "").strip()
            validators = [line.strip() for line in validators_raw.splitlines() if line.strip()] if validators_raw else []
            return ok({
                "status": "healthy",
                "summary": "Node is healthy",
                "node_role": node_role,
                "deployed": bool(deployed),
                "from_idx": os.getenv("FROM_IDX", "0"),
                "real_interact": bool(os.getenv("REAL_INTERACT")),
                "mode": {
                    "from_idx": os.getenv("FROM_IDX", "0"),
                    "real_interact": bool(os.getenv("REAL_INTERACT")),
                    "bridge": _health_bridge_mode(),
                },
                "checks": {
                    "contract_deployed": bool(deployed),
                    "validator_count": len(validators),
                },
                "validators": validators,
            })
        except Exception as e:
            payload, status_code = _health_failure_payload(e)
            return jsonify(payload), status_code

    @app.get("/spec")
    def spec():
        return ok(_spec_payload())

    @app.post("/register-node")
    #@track_performance
    def register_node():
        deny = _role_allows("cloud", "fog", "edge")
        if deny:
            return err("registrar_role_forbidden", 403, detail="available_on:cloud,edge,fog")
        req, bad = require_json(
            ["node_id", "node_name", "node_type", "public_key", "address", "rpcURL", "signature"]
        )

        print(f"[register] payload: {req}")

        if bad: return bad
        req["bootstrap_base_url"] = request.host_url.rstrip("/")
        flow_id = orch.start_flow(
            "registration",
            stage="request_received",
            message="Registration request received",
            component="api",
            details={"node_id": req.get("node_id"), "node_type": req.get("node_type"), "rpc_url": req.get("rpcURL")},
            from_signature=req.get("signature"),
        )

        # --- Registration hardening preflight (soft-fail if orchestrator lacks helpers) ---
        try:
            # 1) duplicate nodeId check (HTTP 409)
            if hasattr(orch, "is_node_id_taken") and orch.is_node_id_taken(req.get("node_id", "")):
                orch.finish_flow(
                    "denied",
                    stage="registration_finished",
                    message="Registration denied because the node_id already exists",
                    details={"node_id": req.get("node_id")},
                    from_signature=req.get("signature"),
                )
                return err("duplicate_node_id", 409)

            # 2) duplicate node signature check (HTTP 409)
            if hasattr(orch, "is_node_registered") and orch.is_node_registered(req.get("signature", "")):
                orch.finish_flow(
                    "denied",
                    stage="registration_finished",
                    message="Registration denied because the signature is already registered",
                    from_signature=req.get("signature"),
                )
                return err("Already Registered", 409)

            # 3) signature verification over canonical payload (HTTP 403)
            #    Expect orchestrator.verify_registration_sig(req) -> bool
            if hasattr(orch, "verify_registration_sig"):
                if not orch.verify_registration_sig(req):
                    orch.finish_flow(
                        "error",
                        stage="registration_finished",
                        message="Registration denied because signature verification failed",
                        from_signature=req.get("signature"),
                    )
                    return err("bad_registration_sig", 403)
        except Exception as _pre_e:
            # Do not crash on preflight; log and continue to on-chain checks
            print(f"[preflight] registration checks skipped due to: {_pre_e}")

        # 4) Tier-authority check: a node may only register nodes of equal or lower tier.
        #    Prevents Fog from registering with an Edge node (Edge cannot vote for Fog validators).
        registrant_type = req.get("node_type", "")
        registrar_tier = ROLE.get(registrar_role_map.get(node_role, "Cloud"), 1)
        registrant_tier = ROLE.get(registrant_type, 999)
        if registrant_tier < registrar_tier:
            orch.finish_flow(
                "denied",
                stage="registration_finished",
                message=f"Registrar tier ({node_role}) cannot register a higher-tier node ({registrant_type})",
                from_signature=req.get("signature"),
            )
            return err(
                f"tier_mismatch: a {node_role} node cannot register a {registrant_type} node",
                403,
            )

        # Optional flags accepted in payload:
        # - wants_validator (bool)
        try:
            out = orch.registration_flow(req)
            if not out.get("ok", True):
                why = out.get("why", "registration_flow_rejected")
                orch.finish_flow(
                    "error",
                    stage="registration_finished",
                    message=f"Registration flow rejected: {why}",
                    details={"why": why},
                    from_signature=req.get("signature"),
                )
                return err(why, 502, detail=why)
            try:
                # Only Fog/Cloud nodes can be validators in your model
                if req.get("node_type") in {"Fog", "Cloud"}:
                    status = out.get("status")

                    # If we’re already included (or this node was already a validator), start immediately.
                    if status in {"validator_included", "validator_already_included", "already_registered"} and orch.is_validator():
                        print("[listener] post-register: already a validator, starting listener")
                        orch.start_validator_listener()
            except Exception as _e:
                # Don’t fail registration just because the listener start logic hiccupped.
                print(f"[listener] post-register start logic error: {_e}")
            # normalize response for clients
            orch.finish_flow(
                "ok",
                stage="registration_finished",
                message=f"Registration flow completed with status {out.get('status')}",
                details={
                    "status": out.get("status"),
                    "ack_sent": out.get("ack_sent", False),
                    "ack_status": out.get("ack_status", "not_needed"),
                    "ack_required": out.get("ack_required", False),
                },
                tx_hash=out.get("tx"),
                from_signature=req.get("signature"),
            )
            return ok({
                "status": out.get("status"),
                "ack_sent": out.get("ack_sent", False),
                "ack_status": out.get("ack_status", "not_needed"),
                "ack_required": out.get("ack_required", False),
                "tx": out.get("tx"),
                "flow_id": flow_id,
            })
        except Exception as e:
            orch.finish_flow(
                "error",
                stage="registration_finished",
                message="Registration flow failed",
                details={"detail": str(e)},
                from_signature=req.get("signature"),
            )
            return err("registration_failed", 500, detail=str(e), trace=traceback.format_exc())

    @app.get("/node/<signature>")
    #@track_performance
    def node_details(signature: str):
        try:
            # quick reg check
            is_reg = orch.is_node_registered(signature)
            if not is_reg:
                return err("node_not_registered", 404)
            details = orch.get_node_by_sig(signature)
            return ok({"details": details})
        except Exception as e:
            return err("node_query_failed", 500, detail=str(e))

    @app.get("/validators")
    #@track_performance
    def validators():
        try:
            v = orch.qbft_get_validators()
            return ok({"validators": v})
        except Exception as e:
            return err("validator_query_failed", 500, detail=str(e))

    @app.get("/metrics/latency")
    def latency_metrics():
        try:
            output_path = orch.latency_recorder.write_summary()
            return ok({"summary": orch.latency_summary(), "output": str(output_path)})
        except Exception as e:
            return err("latency_metrics_failed", 500, detail=str(e))

    @app.get("/events/recent")
    def recent_events():
        limit = max(1, min(_int_arg("limit", 100), 500))
        return ok({"events": orch.recent_events(limit=limit)})

    @app.get("/events/flows")
    def event_flows():
        limit = max(1, min(_int_arg("limit", 50), 200))
        return ok({"flows": orch.flow_summaries(limit=limit)})

    @app.get("/events/active")
    def active_flows():
        return ok({"flows": orch.active_flow_summaries()})

    @app.get("/events/stats")
    def event_stats():
        return ok({"stats": orch.event_stats()})

    @app.get("/events/stream")
    def stream_events():
        follow = request.args.get("follow", "1") != "0"
        after = _int_arg("after", 0)
        limit = max(1, min(_int_arg("limit", 100), 500))

        def generate():
            events = [event for event in orch.recent_events(limit=limit) if int(event.get("sequence", 0)) > after]
            current = after
            for event in events:
                current = max(current, int(event.get("sequence", 0)))
                yield json.dumps(event, sort_keys=True) + "\n"
            if not follow:
                return
            while True:
                fresh = orch.wait_for_events(after_sequence=current, timeout=2.0)
                if not fresh:
                    continue
                for event in fresh:
                    current = max(current, int(event.get("sequence", 0)))
                    yield json.dumps(event, sort_keys=True) + "\n"

        return Response(stream_with_context(generate()), mimetype="application/x-ndjson")

    @app.get("/dashboard")
    def dashboard():
        return Response(_simple_results_page_html(), mimetype="text/html")

    @app.get("/live-dashboard")
    def live_dashboard():
        static_dir = _node_root_dir() / "static"
        if not (static_dir / "dashboard.html").exists():
            static_dir = Path(__file__).resolve().parent / "static"
        return send_from_directory(str(static_dir), "dashboard.html")

    @app.get("/results")
    def results():
        return Response(_simple_results_page_html(), mimetype="text/html")

    @app.get("/results/chart/<chart_id>")
    def results_chart(chart_id: str):
        svg = _results_chart_svg(chart_id, request.args.get("scenario"))
        return Response(svg, mimetype="image/svg+xml")

    @app.get("/dashboard/data")
    def dashboard_data():
        return ok(_results_payload(request.args.get("scenario"), request.args.get("compare")))

    @app.get("/api/results")
    def api_results():
        payload = _results_payload(request.args.get("scenario"), request.args.get("compare"), sample=False)
        return ok({
            "nodes": _registered_nodes_snapshot(),
            "latency": payload.get("latency_summary") or {},
            "event_stats": payload.get("event_stats") or {},
            "active_flows": payload.get("active_flows") or [],
            "recent_events": payload.get("recent_events") or [],
        })

    @app.get("/dashboard/stream")
    def dashboard_stream():
        after = _int_arg("after", 0)

        def generate():
            current = after
            while True:
                fresh = orch.wait_for_events(after_sequence=current, timeout=2.0)
                if not fresh:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                for event in fresh:
                    current = max(current, int(event.get("sequence", 0)))
                    yield f"data: {json.dumps(event, sort_keys=True)}\n\n"

        return Response(stream_with_context(generate()), mimetype="text/event-stream")

    @app.get("/results/data")
    def results_data():
        return ok(_results_payload(request.args.get("scenario"), request.args.get("compare")))

    @app.get("/control/data")
    def control_data():
        return ok(_control_payload(request.args.get("scenario")))

    @app.get("/topology/data")
    def topology_data():
        return ok(_topology_payload())

    @app.get("/control/node-inspector")
    def control_node_inspector():
        scenario = request.args.get("scenario")
        node_key = request.args.get("node_key") or "root"
        details = infra.scenario_details(scenario)
        flow_summaries = orch.flow_summaries(limit=100)
        payload = _node_inspector_payload(
            infra_details=details,
            node_key=node_key,
            flow_summaries=flow_summaries,
        )
        if not payload.get("node"):
            control_payload = _control_payload(scenario)
            payload = _node_inspector_payload_from_cards(
                selected_scenario=control_payload.get("selected_scenario"),
                node_cards=list(control_payload.get("node_cards") or []),
                node_key=node_key,
                flow_summaries=flow_summaries,
            )
        return ok(payload)

    @app.get("/topology/node-inspector")
    def topology_node_inspector():
        node_key = request.args.get("node_key") or "root"
        details = infra.scenario_details(active_only=True)
        flow_summaries = orch.flow_summaries(limit=100)
        payload = _node_inspector_payload(
            infra_details=details,
            node_key=node_key,
            flow_summaries=flow_summaries,
        )
        if not payload.get("node"):
            topology_payload = _topology_payload()
            payload = _node_inspector_payload_from_cards(
                selected_scenario=topology_payload.get("selected_scenario"),
                node_cards=list(topology_payload.get("node_cards") or []),
                node_key=node_key,
                flow_summaries=flow_summaries,
            )
        return ok(payload)

    @app.get("/bootstrap/genesis.json")
    def bootstrap_genesis():
        path = _genesis_dir() / "genesis.json"
        if not path.exists():
            return err("bootstrap_missing", 404, detail="genesis_not_found")
        return Response(path.read_bytes(), mimetype="application/json")

    @app.get("/bootstrap/node-registry.json")
    def bootstrap_node_registry():
        path = _data_dir() / "NodeRegistry.json"
        if not path.exists():
            return err("bootstrap_missing", 404, detail="node_registry_not_found")
        return Response(path.read_bytes(), mimetype="application/json")

    @app.get("/bootstrap/prefunded_keys.json")
    def bootstrap_prefunded_keys():
        path = _node_root_dir() / "prefunded_keys.json"
        if not path.exists():
            return err("bootstrap_missing", 404, detail="prefunded_keys_not_found")
        return Response(path.read_bytes(), mimetype="application/json")

    @app.get("/bootstrap/enode.txt")
    def bootstrap_enode():
        try:
            enode = AcknowledgementSender._cached_enode(  # type: ignore[attr-defined]
                orch.besu_rpc_url,
                timeout=5.0,
                verify_ssl=False,
            )
            if not enode:
                return err("bootstrap_missing", 404, detail="enode_not_found")
            return Response(enode + "\n", mimetype="text/plain")
        except Exception as exc:
            return err("bootstrap_missing", 404, detail=str(exc))

    @app.post("/bootstrap-ack")
    def bootstrap_ack():
        deny = _role_allows("fog", "edge", "endpoint")
        if deny:
            return deny
        try:
            payload = request.get_json(force=True) or {}
            written = _persist_bootstrap_payload(payload)
            return ok({"status": "bootstrap_ready", "written": written})
        except Exception as exc:
            return err("bootstrap_ack_failed", 500, detail=str(exc), trace=traceback.format_exc())

    @app.post("/acknowledgement")
    def acknowledgement():
        try:
            payload = request.get_json(silent=True) if request.is_json else None
            node_id = (payload or {}).get("node_id") if payload else request.form.get("node_id")
            enode = (payload or {}).get("enode") if payload else request.form.get("enode")
            if not node_id or not enode:
                return err("Missing node_id or enode", 400)

            genesis_path, registry_path, prefunded_path = _bootstrap_payload_paths()
            repo_dir = _node_root_dir()

            if payload:
                manifest_urls = [
                    ("genesis_url", "genesis_sha256", genesis_path),
                    ("registry_url", "registry_sha256", registry_path),
                    ("prefunded_keys_url", "prefunded_sha256", prefunded_path),
                ]
                for url_key, sha_key, target_path in manifest_urls:
                    url = str(payload.get(url_key) or "").strip()
                    if not url:
                        continue
                    target_path.write_bytes(
                        _download_bootstrap_artifact(
                            url,
                            expected_sha256=str(payload.get(sha_key) or ""),
                        )
                    )
            else:
                if request.files.get("genesis_file"):
                    request.files["genesis_file"].save(genesis_path)
                if request.files.get("node_registry_file"):
                    request.files["node_registry_file"].save(registry_path)
                if request.files.get("prefunded_keys_file"):
                    request.files["prefunded_keys_file"].save(prefunded_path)

            _persist_bootstrap_payload({"enode": enode})

            return ok({
                "status": "success",
                "message": f"Acknowledgment received for {node_id}",
                "enode": enode,
                "mode": "manifest" if payload else "multipart",
            })
        except Exception as e:
            return err("acknowledgement_failed", 500, detail=str(e), trace=traceback.format_exc())

    @app.post("/access")
    ##@track_performance
    def access():
        """
        Request body:
        {
          "from_signature": "...",
          "to_signature": "...",
          "method": "GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD",
          "resource_path": "/sensors/1",
          "expiry_secs": 900,                # optional
          "allow_delegation": false,         # optional
          "delegation_depth": 0              # optional
        }
        """
        forwarded = _forward_to_parent("/access")
        if forwarded is not None:
            return forwarded
        req, bad = require_json(["from_signature", "method", "resource_path"])
        if bad: return bad
        flow_id = orch.start_flow(
            "access",
            stage="request_received",
            message="Access request received",
            component="api",
            details={"method": req.get("method"), "resource_path": req.get("resource_path")},
            from_signature=req.get("from_signature"),
        )
        
        to_sig = req.get("to_signature") or local_sig
        if not to_sig:
            orch.finish_flow(
                "error",
                stage="access_finished",
                message="Access request failed because to_signature could not be resolved",
                from_signature=req.get("from_signature"),
            )
            return err("to_signature missing and local node signature not found", 422)

        orch.emit_event(
            component="rate_limiter",
            stage="rate_limit_check",
            status="started",
            message="Evaluating per-object rate limit",
            from_signature=req.get("from_signature"),
            to_signature=to_sig,
        )
        allowed, retry_after_ms = rate_limiter.allow(to_sig, orch.local_node_tier)
        if not allowed:
            orch.finish_flow(
                "denied",
                stage="rate_limit_check",
                message="Access request was rate limited",
                component="rate_limiter",
                details={"retry_after_ms": retry_after_ms},
                from_signature=req.get("from_signature"),
                to_signature=to_sig,
            )
            return err("rate_limit_exceeded", 429, reason="rate_limit_exceeded", retry_after_ms=retry_after_ms)
        orch.emit_event(
            component="rate_limiter",
            stage="rate_limit_check",
            status="ok",
            message="Rate limit check passed",
            from_signature=req.get("from_signature"),
            to_signature=to_sig,
        )
        
        try:
            result = orch.access_flow(
                req["from_signature"], to_sig,
                req["method"], req["resource_path"],
                int(req.get("expiry_secs", 900)),
                bool(req.get("allow_delegation", False)),
                int(req.get("delegation_depth", 0)),
                bool(req.get("audit", True))
            )
            print("yoar taaam chu waatnaavun")
            if not result.get("ok"):
                # bubble up reason
                orch.finish_flow(
                    "denied",
                    stage="access_finished",
                    message=result.get("why", "access_denied"),
                    details={k: v for k, v in result.items() if k != "ok"},
                    from_signature=req.get("from_signature"),
                    to_signature=to_sig,
                )
                return err(result.get("why", "access_denied"), 403, **{k:v for k,v in result.items() if k not in {"ok"}})
            orch.finish_flow(
                "ok" if result.get("granted") else "denied",
                stage="access_finished",
                message="Access granted" if result.get("granted") else "Access denied by grant check",
                details={"granted": result.get("granted"), "policy_id": result.get("policyId"), "ctx": result.get("ctx")},
                policy_id=result.get("policyId"),
                from_signature=req.get("from_signature"),
                to_signature=to_sig,
            )
            return ok({**result, "flow_id": flow_id})
        except Exception as e:
            orch.finish_flow(
                "error",
                stage="access_finished",
                message="Access flow failed",
                details={"detail": str(e)},
                from_signature=req.get("from_signature"),
                to_signature=to_sig,
            )
            return err("access_flow_failed", 500, detail=str(e), trace=traceback.format_exc())

    @app.post("/delegate")
    #@track_performance
    def delegate():
        """
        Body:
        {
          "parent_from_sig": "...",   # existing grant owner (delegable, depth>0)
          "to_sig": "...",
          "child_from_sig": "...",
          "ops_csv": "READ"           # or "READ,WRITE" (subset should be enforced by contract)
          "child_expiry_secs": 600
        }
        """
        forwarded = _forward_to_parent("/delegate")
        if forwarded is not None:
            return forwarded
        deny = _role_allows("cloud", "fog", "edge")
        if deny:
            return deny
        req, bad = require_json(["parent_from_sig", "to_sig", "child_from_sig", "ops_csv"])
        if bad: return bad
        flow_id = orch.start_flow(
            "delegation",
            stage="request_received",
            message="Delegation request received",
            component="api",
            details={"ops_csv": req.get("ops_csv")},
            from_signature=req.get("parent_from_sig"),
            to_signature=req.get("to_sig"),
        )

        try:
            res = orch.delegate_flow(
                req["parent_from_sig"], req["to_sig"], req["child_from_sig"],
                req["ops_csv"], int(req.get("child_expiry_secs", 600)),
                int(req["policy_id"]) if req.get("policy_id") is not None else None
            )
            if not res.get("ok"):
                orch.finish_flow(
                    "denied",
                    stage="delegation_finished",
                    message=res.get("why", "delegate_failed"),
                    details={k: v for k, v in res.items() if k != "ok"},
                    from_signature=req.get("parent_from_sig"),
                    to_signature=req.get("to_sig"),
                )
                return err(res.get("why", "delegate_failed"), 400, **{k:v for k,v in res.items() if k not in {"ok"}})
            orch.finish_flow(
                "ok" if res.get("granted", True) else "denied",
                stage="delegation_finished",
                message="Delegation flow completed",
                details={"granted": res.get("granted", False)},
                tx_hash=res.get("tx"),
                from_signature=req.get("child_from_sig"),
                to_signature=req.get("to_sig"),
            )
            return ok({**res, "flow_id": flow_id})
        except Exception as e:
            orch.finish_flow(
                "error",
                stage="delegation_finished",
                message="Delegation flow failed",
                details={"detail": str(e)},
                from_signature=req.get("parent_from_sig"),
                to_signature=req.get("to_sig"),
            )
            return err("delegate_flow_failed", 500, detail=str(e), trace=traceback.format_exc())

    @app.post("/revoke-grant")
    #@track_performance
    def revoke_grant():
        req, bad = require_json(["from_signature", "to_signature"])
        if bad: return bad
        flow_id = orch.start_flow(
            "revocation",
            stage="request_received",
            message="Revocation request received",
            component="api",
            from_signature=req.get("from_signature"),
            to_signature=req.get("to_signature"),
        )
        try:
            policy_id = req.get("policy_id")
            if policy_id is None:
                orch.emit_event(
                    component="orchestrator",
                    stage="revocation_resolution",
                    status="started",
                    message="Resolving the policy_id for revocation",
                    from_signature=req.get("from_signature"),
                    to_signature=req.get("to_signature"),
                )
                if req.get("ctx") or (req.get("method") and req.get("resource_path")):
                    grant = orch.get_grant_ex_auto(
                        req["from_signature"],
                        req["to_signature"],
                        method=req.get("method"),
                        resource_path=req.get("resource_path"),
                        ctx=req.get("ctx")
                    )
                    policy_id = grant.get("policyId")
            if policy_id is None:
                orch.finish_flow(
                    "error",
                    stage="revocation_finished",
                    message="Revocation failed because policy_id could not be resolved",
                    from_signature=req.get("from_signature"),
                    to_signature=req.get("to_signature"),
                )
                return err("missing policy_id", 422)
            tx = orch.revoke_grant(req["from_signature"], req["to_signature"], int(policy_id))
            orch.finish_flow(
                "ok",
                stage="revocation_finished",
                message="Revocation flow completed",
                policy_id=int(policy_id),
                tx_hash=tx,
                from_signature=req.get("from_signature"),
                to_signature=req.get("to_signature"),
            )
            return ok({"tx": tx, "flow_id": flow_id})
        except Exception as e:
            orch.finish_flow(
                "error",
                stage="revocation_finished",
                message="Revocation flow failed",
                details={"detail": str(e)},
                from_signature=req.get("from_signature"),
                to_signature=req.get("to_signature"),
            )
            return err("revoke_failed", 500, detail=str(e))

    @app.get("/grant")
    #@track_performance
    def grant_info():
        forwarded = _forward_to_parent("/grant")
        if forwarded is not None:
            return forwarded
        deny = _role_allows("cloud", "fog", "edge", "endpoint")
        if deny:
            return deny
        from_sig  = request.args.get("from_signature")
        to_sig    = request.args.get("to_signature")
        policy_id = request.args.get("policy_id")      # direct lookup by policy ID
        method    = request.args.get("method")          # e.g., GET/POST/PUT/DELETE
        path      = request.args.get("resource_path")   # e.g., /temperature
        ctx       = request.args.get("ctx")             # optional raw ctx (api:METHOD:/path)

        if not from_sig or not to_sig:
            return err("from_signature and to_signature are required", 422)

        try:
            if policy_id is not None:
                gx = orch.get_grant_ex_any(from_sig, to_sig, int(policy_id))
            else:
                gx = orch.get_grant_ex_auto(
                    from_sig, to_sig,
                    method=method, resource_path=path, ctx=ctx
                )
            return ok({"grant": gx})
        except Exception as e:
            return err("grant_query_failed", 500, detail=str(e))

    @app.get("/expiry-check")
    def expiry_check():
        from_sig = request.args.get("from_signature")
        to_sig = request.args.get("to_signature")
        if not from_sig or not to_sig:
            return err("from_signature and to_signature are required", 422)

        policy_id = request.args.get("policy_id")
        method = request.args.get("method")
        path = request.args.get("resource_path")
        ctx = request.args.get("ctx")

        try:
            expired = orch.is_grant_expired(
                from_sig,
                to_sig,
                int(policy_id) if policy_id is not None else None,
            ) if policy_id is not None else orch.is_grant_expired(
                from_sig,
                to_sig,
                orch.get_grant_ex_auto(
                    from_sig,
                    to_sig,
                    method=method,
                    resource_path=path,
                    ctx=ctx,
                ).get("policyId")
            )
            return ok({"expired": bool(expired)})
        except Exception as e:
            return err("expiry_check_failed", 500, detail=str(e))

    def _policy_orchestrator_from_request():
        selected_scenario = None
        if request.method == "GET":
            selected_scenario = request.args.get("scenario")
        else:
            payload = request.get_json(silent=True) or {}
            selected_scenario = payload.get("scenario")
        if not selected_scenario:
            return orch
        details = infra.scenario_details(str(selected_scenario))
        scenario_payload = details.get("scenario") or {}
        root_payload = scenario_payload.get("root") or {}
        root_dir = Path(str(root_payload.get("directory") or "")).resolve() if root_payload.get("directory") else None
        if root_dir and root_dir.exists():
            active_orch = Orchestrator(repo_root=str(root_dir), enforce_signature=enforce_signature)
            root_rpc_url = str(root_payload.get("rpc_url") or "").strip()
            if root_rpc_url:
                active_orch.besu_rpc_url = root_rpc_url
            return active_orch
        return orch

    def _require_root_policy_controls(active_orch):
        if str(getattr(active_orch, "local_node_tier", "")).lower() != "cloud":
            return err("policy_controls_unavailable", 403, detail="root_only")
        return None

    @app.post("/policy/create")
    def policy_create():
        active_orch = _policy_orchestrator_from_request()
        deny = _require_root_policy_controls(active_orch)
        if deny:
            return deny
        req, bad = require_json(["from_role", "to_role", "ops_csv"])
        if bad:
            return bad
        try:
            result = active_orch.create_policy(req["from_role"], req["to_role"], req["ops_csv"], req.get("ctx_schema"))
            if not result.get("ok"):
                return err("policy_create_failed", 500, detail=result.get("stderr") or result.get("stdout"))
            stdout = str(result.get("stdout") or "")
            policy_id = None
            if stdout.startswith("exists:"):
                try:
                    policy_id = int(stdout.split(":", 1)[1].strip())
                except Exception:
                    policy_id = None
            if policy_id is None:
                found = active_orch.find_policy_id(req["from_role"], req["to_role"], req["ops_csv"], req.get("ctx_schema") or "")
                if found.get("ok"):
                    try:
                        policy_id = int(str(found.get("stdout") or "0").strip() or 0) or None
                    except Exception:
                        policy_id = None
            return ok({
                "status": "exists" if stdout.startswith("exists:") else "created",
                "policy_id": policy_id,
                "tx_hash": stdout,
            })
        except Exception as e:
            return err("policy_create_failed", 500, detail=str(e))

    @app.get("/policy/find")
    def policy_find():
        active_orch = _policy_orchestrator_from_request()
        deny = _require_root_policy_controls(active_orch)
        if deny:
            return deny
        from_role = request.args.get("from_role")
        to_role = request.args.get("to_role")
        ops_csv = request.args.get("ops_csv")
        if not from_role or not to_role or not ops_csv:
            return err("from_role, to_role, and ops_csv are required", 422)
        try:
            result = active_orch.find_policy_id(from_role, to_role, ops_csv, request.args.get("ctx_schema") or "")
            if not result.get("ok"):
                return err("policy_find_failed", 500, detail=result.get("stderr") or result.get("stdout"))
            policy_id = int(str(result.get("stdout") or "0").strip() or 0)
            return ok({"found": bool(policy_id), "policy_id": policy_id or None})
        except Exception as e:
            return err("policy_find_failed", 500, detail=str(e))

    @app.get("/policy/<int:policy_id>")
    def policy_get(policy_id: int):
        active_orch = _policy_orchestrator_from_request()
        deny = _require_root_policy_controls(active_orch)
        if deny:
            return deny
        try:
            return ok({"policy_id": policy_id, "policy": active_orch.get_policy(policy_id)})
        except Exception as e:
            return err("policy_get_failed", 500, detail=str(e))

    @app.post("/policy/update")
    def policy_update():
        active_orch = _policy_orchestrator_from_request()
        deny = _require_root_policy_controls(active_orch)
        if deny:
            return deny
        req, bad = require_json(["policy_id", "ops_csv"])
        if bad:
            return bad
        try:
            result = active_orch.update_policy(int(req["policy_id"]), req["ops_csv"], req.get("ctx_schema"))
            if not result.get("ok"):
                return err("policy_update_failed", 500, detail=result.get("stderr") or result.get("stdout"))
            return ok({"policy_id": int(req["policy_id"]), "tx_hash": result.get("stdout")})
        except Exception as e:
            return err("policy_update_failed", 500, detail=str(e))

    @app.post("/policy/deprecate")
    def policy_deprecate():
        active_orch = _policy_orchestrator_from_request()
        deny = _require_root_policy_controls(active_orch)
        if deny:
            return deny
        req, bad = require_json(["policy_id"])
        if bad:
            return bad
        try:
            result = active_orch.deprecate_policy(int(req["policy_id"]))
            if not result.get("ok"):
                return err("policy_deprecate_failed", 500, detail=result.get("stderr") or result.get("stdout"))
            return ok({"policy_id": int(req["policy_id"]), "tx_hash": result.get("stdout")})
        except Exception as e:
            return err("policy_deprecate_failed", 500, detail=str(e))

    if node_role == "cloud":
        @app.post("/admin/policy/create")
        @require_admin
        def admin_policy_create():
            req, bad = require_json(["from_role", "to_role", "ops"])
            if bad:
                return bad
            invalid_roles = invalid_policy_roles(req["from_role"], req["to_role"])
            if invalid_roles:
                return err(
                    "invalid_policy_roles",
                    422,
                    detail=f"Unsupported node role(s): {', '.join(invalid_roles)}",
                    allowed_roles=VALID_POLICY_ROLES,
                )
            try:
                result = orch.ensure_policy(req["from_role"], req["to_role"], req["ops"], req.get("resource", ""))
                return ok({"result": result})
            except Exception as exc:
                return err("admin_policy_create_failed", 500, detail=str(exc))

        @app.post("/admin/policy/deprecate")
        @require_admin
        def admin_policy_deprecate():
            req, bad = require_json(["policy_id"])
            if bad:
                return bad
            try:
                result = orch.deprecate_policy(int(req["policy_id"]))
                return ok({"result": result})
            except Exception as exc:
                return err("admin_policy_deprecate_failed", 500, detail=str(exc))

        @app.get("/admin/policy/list")
        @require_admin
        def admin_policy_list():
            try:
                return ok({"policies": _iter_policies(orch)})
            except Exception as exc:
                return err("admin_policy_list_failed", 500, detail=str(exc))

        @app.get("/admin/nodes/list")
        @require_admin
        def admin_nodes_list():
            return ok({"nodes": _registered_nodes_snapshot()})

        @app.post("/admin/grant/revoke")
        @require_admin
        def admin_grant_revoke():
            req, bad = require_json(["from_sig", "to_sig", "policy_id"])
            if bad:
                return bad
            try:
                tx = orch.revoke_grant(req["from_sig"], req["to_sig"], int(req["policy_id"]))
                return ok({"tx": tx})
            except Exception as exc:
                return err("admin_grant_revoke_failed", 500, detail=str(exc))

    # Convenience endpoints that mirror your old style (read/write/update/remove)
    # These just call /access under the hood with the correct op, using METHOD_TO_OP mapping.

    def _simple_access(expected_method: str):
        # query params for convenience: from_signature, to_signature, resource_path
        from_sig = request.args.get("from_signature")
        path = request.args.get("resource_path")
        to_sig = local_sig
        if not from_sig or not path:
            return err("from_signature and resource_path are required", 422)
        if not to_sig:
            return err("to_signature missing and local node signature not found", 422)
        try:
            res = orch.access_flow(from_sig, to_sig, expected_method, path)
            if not res.get("ok"):
                return err(res.get("why", "access_denied"), 403, **{k:v for k,v in res.items() if k not in {"ok"}})
            return ok(res)
        except Exception as e:
            return err("access_flow_failed", 500, detail=str(e))

    # Example: Reading temperature data from a sensor
    @app.get("/temperature")
    def read_temperature():
        return _simple_access("GET")   # GET means READ in access control

    # Example: Posting new temperature reading to Edge
    @app.post("/temperature")
    #@track_performance
    def post_temperature():

        return _simple_access("POST")  # POST means WRITE in access control

    # Example: Updating firmware on a device
    @app.put("/firmware")
    #@track_performance
    def update_firmware():
        return _simple_access("PUT")   # PUT means UPDATE in access control

    # Example: Removing a firmware version from a device
    @app.delete("/firmware")
    #@track_performance
    def remove_firmware():
        return _simple_access("DELETE")  # DELETE means REMOVE in access control

    # Example: Reading alerts from a node
    @app.get("/alerts")
    #@track_performance
    def read_alerts():
        return _simple_access("GET")

    # Example: Creating a new alert
    @app.post("/alerts")
    #@track_performance
    def create_alert():
        return _simple_access("POST")

    # Example: Controlling LED (update state)
    @app.put("/control/led")
    #@track_performance
    def control_led():
        return _simple_access("PUT")

    # Example: Stopping motor control (remove control rights)
    @app.delete("/control/motor")
    #@track_performance
    def stop_motor():
        return _simple_access("DELETE")

    @app.post("/admin/cache/clear")
    @require_admin
    def clear_grant_cache():
        """Clear all in-memory caches so the next access request hits the chain (true cold path)."""
        with orch._grant_cache_lock:
            orch._grant_cache.clear()
        with orch._grant_policy_cache_lock:
            orch._grant_policy_id_cache.clear()
        with orch._policy_details_cache_lock:
            orch._policy_details_cache.clear()
        with orch._policy_lock:
            orch.policy_index.clear()
        return ok({"cleared": True})

    if policy_file:
        threading.Thread(target=_load_policy_file_at_startup, name="policy-file-loader", daemon=True).start()

    return app


# --------------- main ---------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Orchestration API")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--repo-root", default=None, help="Repo root where contract artifacts live (defaults to this file's dir)")
    args = parser.parse_args()

    app = make_app(repo_root=args.repo_root)
    # start Flask when run directly
    app.run(host=args.host, port=args.port, debug=(os.getenv("FLASK_DEBUG", "0") == "1"))
