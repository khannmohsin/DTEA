import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_load_test_helpers():
    load_test = load_module("load_test_mod", REPO_ROOT / "scripts" / "load_test.py")

    assert load_test.percentile([0.1, 0.2, 0.3, 0.4], 0.95) == 0.4
    method, url, payload = load_test.resolve_request(
        "http://localhost:5600", "access", None, None, {"a": 1}
    )
    assert method == "POST"
    assert url == "http://localhost:5600/access"
    assert payload == {"a": 1}


def test_load_test_main_writes_results(monkeypatch, tmp_path):
    load_test = load_module("load_test_main_mod", REPO_ROOT / "scripts" / "load_test.py")
    monkeypatch.setattr(load_test, "RESULTS_DIR", tmp_path)

    class FakeResponse:
        def __init__(self, status_code=200):
            self.status_code = status_code

    class FakeSession:
        def get(self, _url, timeout=15):
            return FakeResponse(200)

        def request(self, _method, _url, json=None, timeout=15):
            return FakeResponse(200)

    monkeypatch.setattr(load_test.requests, "Session", lambda: FakeSession())
    monkeypatch.setattr(sys, "argv", [
        "load_test.py",
        "--host", "http://localhost:5600",
        "--operation", "health",
        "--concurrency", "2",
        "--total-requests", "4",
        "--duration-cap", "5",
    ])

    load_test.main()

    payload = json.loads((tmp_path / "load_test.json").read_text())
    assert payload["completed_requests"] == 4
    assert payload["error_count"] == 0
    assert payload["throttled_count"] == 0
    assert payload["non_throttle_error_count"] == 0
    assert payload["success_count"] == 4
    assert payload["success_mean_latency_ms"] >= 0
    assert payload["status_counts"]["200"] == 4
    assert payload["throughput_rps"] > 0


def test_load_test_main_classifies_429_as_throttled(monkeypatch, tmp_path):
    load_test = load_module("load_test_throttle_mod", REPO_ROOT / "scripts" / "load_test.py")
    monkeypatch.setattr(load_test, "RESULTS_DIR", tmp_path)

    class FakeResponse:
        def __init__(self, status_code=200):
            self.status_code = status_code

    class FakeSession:
        def __init__(self):
            self.calls = 0

        def get(self, _url, timeout=15):
            return self.request("GET", _url, timeout=timeout)

        def request(self, _method, _url, json=None, timeout=15):
            self.calls += 1
            return FakeResponse(429 if self.calls % 2 == 0 else 200)

    monkeypatch.setattr(load_test.requests, "Session", lambda: FakeSession())
    monkeypatch.setattr(sys, "argv", [
        "load_test.py",
        "--host", "http://localhost:5600",
        "--operation", "health",
        "--concurrency", "2",
        "--total-requests", "4",
        "--duration-cap", "5",
    ])

    load_test.main()

    payload = json.loads((tmp_path / "load_test.json").read_text())
    assert payload["completed_requests"] == 4
    assert payload["error_count"] == 2
    assert payload["throttled_count"] == 2
    assert payload["non_throttle_error_count"] == 0
    assert payload["success_count"] == 2
    assert payload["throttled_rate"] == 0.5
    assert payload["status_counts"]["200"] == 2
    assert payload["status_counts"]["429"] == 2


def test_run_all_experiments_main_writes_aggregate(monkeypatch, tmp_path):
    experiments = load_module("run_all_experiments_mod", REPO_ROOT / "scripts" / "run_all_experiments.py")
    monkeypatch.setattr(experiments, "RESULTS_DIR", tmp_path)

    (tmp_path / "gas_summary.json").write_text(json.dumps({"issueToken": {"count": 2}}))
    (tmp_path / "contract_metrics.json").write_text(json.dumps([{"contract": "TOTALS"}]))
    scenario = {
        "root": {
            "api_url": "http://cloud:5600",
            "rpc_url": "http://cloud:8545",
            "directory": str(tmp_path),
            "node_details": {"signature": "sig-cloud"},
        },
        "nodes": [
            {"tier": "fog", "api_port": 5002, "payload": {"signature": "sig-fog"}, "registration": {"status": "registered", "tx": "0xf1"}},
            {"tier": "edge", "api_port": 5004, "payload": {"signature": "sig-edge"}, "registration": {"status": "registered", "tx": "0xf2"}},
            {"tier": "endpoint", "api_port": 5006, "payload": {"signature": "sig-end"}, "registration": {"status": "endpoint_registered", "tx": "0xf3"}},
        ],
    }
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(scenario))

    monkeypatch.setattr(experiments, "timed_request", lambda *_args, **_kwargs: {
        "status_code": 200,
        "latency_ms": 5.0,
        "ok": True,
        "payload": {"policyId": 7},
    })
    monkeypatch.setattr(experiments, "measure_revocation_propagation", lambda *_args, **_kwargs: 8.0)
    monkeypatch.setattr(experiments, "run_load_test", lambda _host, concurrency, _body: {
        "operation": "access",
        "concurrency": concurrency,
        "completed_requests": concurrency,
        "success_count": concurrency,
        "success_mean_latency_ms": 12.0,
        "success_p95_latency_ms": 20.0,
        "throttled_count": 0,
        "throttled_rate": 0.0,
    })
    monkeypatch.setattr(experiments, "fetch_latency_summary", lambda _host: {
        "issueToken|fog|cold": {
            "operation": "issueToken",
            "condition": "cold",
            "mean_ms": 5.0,
            "stddev_ms": 0.0,
            "count": 1,
        }
    })
    monkeypatch.setattr(experiments, "build_comparison_table", lambda: {
        "baseline_complete": False,
        "table": [{"operation": "issue", "system": "BlockCap", "gas_cost": 100}],
    })
    monkeypatch.setattr(sys, "argv", [
        "run_all_experiments.py",
        "--scenario-file", str(scenario_path),
        "--runs", "2",
    ])

    experiments.main()

    payload = json.loads((tmp_path / "experimental_results.json").read_text())
    assert len(payload["end_to_end_latency"]) == 8
    assert payload["load_tests"]["10"]["completed_requests"] == 10
    assert payload["load_tests"]["10"]["success_mean_latency_ms"] == 12.0
    assert payload["gas_summary"]["issueToken"]["count"] == 2
    assert payload["contract_metrics"][0]["contract"] == "TOTALS"
    assert payload["gas_comparison"]["baseline_complete"] is False
    assert "analysis_notes" in payload


def test_resolve_experiment_case_uses_hierarchy_specific_tuples():
    experiments = load_module("run_all_experiments_case_mod", REPO_ROOT / "scripts" / "run_all_experiments.py")
    scenario = {
        "root": {"node_details": {"signature": "sig-cloud"}},
        "nodes": [
            {"tier": "fog", "payload": {"signature": "sig-fog"}},
            {"tier": "edge", "payload": {"signature": "sig-edge"}},
            {"tier": "endpoint", "payload": {"signature": "sig-end"}},
        ],
    }

    assert experiments.resolve_experiment_case(scenario, "cloud") == {
        "target_sig": "sig-fog",
        "requester_sig": "sig-fog",
        "delegate_parent_sig": "sig-fog",
        "delegate_target_sig": "sig-fog",
        "delegatee_sig": "sig-edge",
        "delegate_enabled": True,
    }
    assert experiments.resolve_experiment_case(scenario, "fog") == {
        "target_sig": "sig-edge",
        "requester_sig": "sig-fog",
        "delegate_parent_sig": "sig-fog",
        "delegate_target_sig": "sig-edge",
        "delegatee_sig": "sig-cloud",
        "delegate_enabled": False,
    }
    assert experiments.resolve_experiment_case(scenario, "edge") == {
        "target_sig": "sig-end",
        "requester_sig": "sig-fog",
        "delegate_parent_sig": "sig-edge",
        "delegate_target_sig": "sig-end",
        "delegatee_sig": "sig-fog",
        "delegate_enabled": True,
    }
    assert experiments.resolve_experiment_case(scenario, "endpoint") == {
        "target_sig": "sig-end",
        "requester_sig": "sig-fog",
        "delegate_parent_sig": "sig-edge",
        "delegate_target_sig": "sig-end",
        "delegatee_sig": "sig-fog",
        "delegate_enabled": True,
    }


def test_tier_experiment_skips_delegate_when_disabled(monkeypatch):
    experiments = load_module("run_all_experiments_skip_delegate_mod", REPO_ROOT / "scripts" / "run_all_experiments.py")

    calls: list[tuple[str, str]] = []

    def fake_timed_request(method, url, *, json_body=None, params=None):
        calls.append((method, url))
        if url.endswith("/grant"):
            return {"status_code": 200, "latency_ms": 1.0, "ok": True, "payload": {"grant": {"policyId": 4}}}
        return {"status_code": 200, "latency_ms": 1.0, "ok": True, "payload": {"policyId": 4}}

    monkeypatch.setattr(experiments, "timed_request", fake_timed_request)
    monkeypatch.setattr(experiments, "measure_revocation_propagation", lambda *_args, **_kwargs: 2.0)

    _rt_rows, lifecycle_rows, prop_samples = experiments.tier_experiment(
        "http://fog-host",
        "fog",
        "sig-edge",
        "sig-fog",
        "sig-cloud",
        1,
        delegate_parent_sig="sig-fog",
        delegate_target_sig="sig-edge",
        delegate_enabled=False,
    )

    assert ("POST", "http://fog-host/delegate") not in calls
    delegate_row = next(row for row in lifecycle_rows if row["operation"] == "delegation_http")
    assert delegate_row["ok"] is True
    assert delegate_row["payload"]["skipped"] is True
    assert prop_samples == [2.0]


def test_load_test_status_treats_pure_429_results_as_throttled():
    experiments = load_module("run_all_experiments_throttle_mod", REPO_ROOT / "scripts" / "run_all_experiments.py")

    status, errors, throttled = experiments.load_test_status({
        "error_count": 200,
        "throttled_count": 200,
        "non_throttle_error_count": 0,
        "throughput_rps": 123.0,
    })

    assert "THROTTLED" in status
    assert "[FAIL]" not in status
    assert errors == 0
    assert throttled == 200


def test_experimental_setup_uses_live_validator_set_when_manifest_is_pending(monkeypatch, tmp_path):
    experiments = load_module("run_all_experiments_setup_mod", REPO_ROOT / "scripts" / "run_all_experiments.py")

    root_dir = tmp_path / "root"
    genesis_dir = root_dir / "genesis"
    genesis_dir.mkdir(parents=True)
    (genesis_dir / "genesis.json").write_text(json.dumps({
        "config": {
            "chainId": 1337,
            "qbft": {
                "blockperiodseconds": 1,
                "epochlength": 30000,
                "requesttimeoutseconds": 2,
            },
        },
    }))

    scenario = {
        "root": {
            "api_url": "http://cloud:5600",
            "directory": str(root_dir),
            "node_details": {
                "signature": "sig-cloud",
                "address": "0x1111111111111111111111111111111111111111",
            },
        },
        "nodes": [
            {
                "tier": "fog",
                "wants_validator": True,
                "registration": {"status": "validator_proposed", "tx": "0xfake"},
                "payload": {
                    "signature": "sig-fog",
                    "address": "0x2222222222222222222222222222222222222222",
                },
            }
        ],
    }

    class FakeResponse:
        def __init__(self, payload):
            self.ok = True
            self._payload = payload

        def json(self):
            return self._payload

    monkeypatch.setattr(
        experiments.requests,
        "get",
        lambda url, timeout=5: FakeResponse({"validators": "['0x1111111111111111111111111111111111111111','0x2222222222222222222222222222222222222222']"}),
    )

    setup = experiments.experimental_setup(scenario)

    assert setup["validator_set_size"] == 2
    assert setup["validator_nodes"] == ["sig-cloud", "sig-fog"]


def test_generate_matplotlib_report_prefers_topology_registration_and_handles_optional_sections(monkeypatch, tmp_path):
    report = load_module("generate_report_mod", REPO_ROOT / "scripts" / "generate_matplotlib_report.py")
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "experimental_results.json").write_text(json.dumps({
        "end_to_end_latency": [
            {"tier": "cloud", "condition": "cold", "mean_latency_ms": 1000.0},
            {"tier": "cloud", "condition": "warm", "mean_latency_ms": 15.0},
            {"tier": "fog", "condition": "cold", "mean_latency_ms": 1200.0},
            {"tier": "fog", "condition": "warm", "mean_latency_ms": 18.0},
            {"tier": "edge", "condition": "cold", "mean_latency_ms": 1100.0},
            {"tier": "edge", "condition": "warm", "mean_latency_ms": 14.0},
            {"tier": "endpoint", "condition": "cold", "mean_latency_ms": 1500.0},
            {"tier": "endpoint", "condition": "warm", "mean_latency_ms": 20.0},
        ],
        "load_tests": {
            "10": {"throughput_rps": 10, "success_mean_latency_ms": 11, "success_p95_latency_ms": 15, "throttled_count": 0, "throttled_rate": 0.0},
            "50": {"throughput_rps": 20, "success_mean_latency_ms": 21, "success_p95_latency_ms": 30, "throttled_count": 5, "throttled_rate": 0.1},
        },
        "token_lifecycle_latency": [
            {"tier": "cloud", "operation": "issueToken", "condition": "warm", "mean_latency_ms": 1000.0, "count": 2, "source_key": "issueToken|cloud|warm"},
            {"tier": "cloud", "operation": "issueTokenDelegable", "condition": "warm", "mean_latency_ms": 1500.0, "count": 2, "source_key": "issueTokenDelegable|cloud|warm"},
        ],
        "internal_latency_summary": {},
        "topology_registration_latency": [
            {"tier": "fog", "operation": "registerNode", "condition": "cold", "mean_latency_ms": 2200.0, "count": 1, "source_key": "topologyRegistration|fog|cold"},
            {"tier": "edge", "operation": "registerNode", "condition": "cold", "mean_latency_ms": 1800.0, "count": 1, "source_key": "topologyRegistration|edge|cold"},
        ],
        "validator_promotion_latency": [
            {"tier": "fog", "operation": "validatorPromotion", "condition": "cold", "mean_latency_ms": 9000.0, "count": 1, "source_key": "validatorPromotion|fog|cold"},
        ],
    }))
    (results_dir / "gas_comparison.json").write_text(json.dumps({"table": []}))
    (results_dir / "contract_metrics.json").write_text(json.dumps([]))

    monkeypatch.setattr(sys, "argv", [
        "generate_matplotlib_report.py",
        "--results-dir", str(results_dir),
        "--scenario", "acm-results",
    ])

    report.main()

    assert (results_dir / "plots" / "figure_11_registration_latency_by_tier.png").exists()
    assert (results_dir / "plots" / "figure_12_validator_promotion_latency_by_tier.png").exists()
    summary = json.loads((results_dir / "metrics_report_summary.json").read_text())
    assert "notes" in summary
