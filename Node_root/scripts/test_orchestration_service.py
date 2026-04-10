import importlib
import io
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class DummyLatencyRecorder:
    def __init__(self, output_path: Path, summary: dict):
        self.output_path = output_path
        self.summary = summary

    def write_summary(self):
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(self.summary, indent=2, sort_keys=True))
        return self.output_path


class FakeOrchestrator:
    config = {}
    last_instance = None

    def __init__(self, repo_root=None, registrar_role="Fog", enforce_signature=True, **_kwargs):
        cfg = type(self).config
        type(self).last_instance = self
        self.repo_root = repo_root
        self.registrar_role = registrar_role
        self.enforce_signature = enforce_signature
        self.local_node_tier = cfg.get("local_node_tier", "fog")
        self.local_node_id = cfg.get("local_node_id", "FG-1")
        self.local_node_name = cfg.get("local_node_name", "FogOne")
        self.besu_rpc_url = cfg.get("besu_rpc_url", "http://rpc:8545")
        self.latency_recorder = DummyLatencyRecorder(
            cfg["latency_output_path"],
            cfg.get("latency_summary", {"registerNode|fog|cold": {"count": 1}}),
        )
        self.begin_calls = []
        self.end_calls = 0
        self.request_id = None
        self.current_flow = None
        self.events = []
        self.access_calls = []
        self.delegate_calls = []
        self.revoke_calls = []
        self.grant_lookup_calls = []
        self.expiry_calls = []
        self.policy_calls = []
        self.listener_started = False

    @classmethod
    def reset(cls, tmp_path):
        cls.config = {
            "latency_output_path": tmp_path / "results" / "latency.json",
            "latency_summary": {
                "checkGrant|fog|cold": {
                    "operation": "checkGrant",
                    "node_tier": "fog",
                    "condition": "cold",
                    "mean_ms": 12.3,
                    "stddev_ms": 0.0,
                    "min_ms": 12.3,
                    "max_ms": 12.3,
                    "count": 1,
                }
            },
            "duplicate_node_id": False,
            "registered_signatures": set(),
            "verify_registration_sig": True,
            "registration_flow_result": {"ok": True, "status": "registered", "ack_sent": False, "ack_status": "queued", "ack_required": True, "tx": "0xreg"},
            "access_flow_result": {"ok": True, "granted": True, "op": "READ", "policyId": 7, "ctx": "api:GET:/temperature"},
            "delegate_flow_result": {"ok": True, "granted": True, "tx": "0xdel"},
            "grant_lookup_result": {"policyId": 7, "isIssued": True},
            "grant_expired": False,
            "find_policy_result": {"ok": True, "stdout": "7", "stderr": ""},
            "create_policy_result": {"ok": True, "stdout": "0xcreate", "stderr": ""},
            "ensure_policy_result": {"status": "created", "policyId": 7, "note": "ok"},
            "get_policy_result": {"fromRole": "Edge", "toRole": "Fog", "ops": "READ", "ctxSchema": "api:GET:/temperature"},
            "update_policy_result": {"ok": True, "stdout": "0xupdate", "stderr": ""},
            "deprecate_policy_result": {"ok": True, "stdout": "0xdeprecate", "stderr": ""},
            "node_details": {},
            "validator": False,
            "deployed": True,
            "validators": "[0x1]",
        }
        cls.last_instance = None

    def begin_request(self, node_tier=None, condition=None):
        self.request_id = "req-test"
        self.begin_calls.append({"node_tier": node_tier, "condition": condition})

    def end_request(self):
        self.end_calls += 1
        self.request_id = None
        self.current_flow = None

    def check_if_deployed(self):
        return type(self).config["deployed"]

    def qbft_get_validators(self):
        return type(self).config["validators"]

    def is_validator(self):
        return type(self).config["validator"]

    def start_validator_listener(self):
        self.listener_started = True

    def latency_summary(self):
        return type(self).config["latency_summary"]

    def start_flow(self, flow_type, *, stage, message, component="api", details=None, set_current=True, flow_id=None, **kwargs):
        self.current_flow = flow_id or f"{flow_type}-1"
        self.emit_event(
            component=component,
            flow_type=flow_type,
            flow_id=self.current_flow,
            stage=stage,
            status="started",
            message=message,
            details=details,
            **kwargs,
        )
        return self.current_flow

    def emit_event(self, *, component, stage, status, message, flow_type=None, flow_id=None, details=None, **kwargs):
        event = {
            "sequence": len(self.events) + 1,
            "event_id": f"evt-{len(self.events) + 1}",
            "ts_unix_ms": 1_700_000_000_000 + len(self.events),
            "node_id": self.local_node_id,
            "node_name": self.local_node_name,
            "node_tier": self.local_node_tier,
            "component": component,
            "flow_type": flow_type or "daemon",
            "flow_id": flow_id or self.current_flow or "flow-unknown",
            "stage": stage,
            "status": status,
            "message": message,
            "details": details or {},
            "duration_ms": kwargs.get("duration_ms"),
            "request_id": self.request_id,
            "tx_hash": kwargs.get("tx_hash"),
            "policy_id": kwargs.get("policy_id"),
            "from_signature": kwargs.get("from_signature"),
            "to_signature": kwargs.get("to_signature"),
        }
        self.events.append(event)
        return event

    def finish_flow(self, status, *, stage, message, details=None, component="orchestrator", flow_id=None, flow_type=None, **kwargs):
        return self.emit_event(
            component=component,
            stage=stage,
            status=status,
            message=message,
            details=details,
            flow_id=flow_id or self.current_flow,
            flow_type=flow_type or "access",
            **kwargs,
        )

    def recent_events(self, limit=100):
        return self.events[-limit:]

    def flow_summaries(self, limit=50):
        grouped = {}
        for event in self.events:
            grouped.setdefault(event["flow_id"], []).append(event)
        rows = []
        for flow_id, events in grouped.items():
            rows.append({
                "flow_id": flow_id,
                "flow_type": events[0]["flow_type"],
                "node_tier": events[0]["node_tier"],
                "started_at_ms": events[0]["ts_unix_ms"],
                "duration_ms": events[-1]["ts_unix_ms"] - events[0]["ts_unix_ms"],
                "final_status": events[-1]["status"],
                "last_stage": events[-1]["stage"],
                "message": events[-1]["message"],
                "events": events,
            })
        return rows[:limit]

    def active_flow_summaries(self):
        return []

    def event_stats(self):
        status_counts = {}
        flow_type_counts = {}
        for event in self.events:
            status_counts[event["status"]] = status_counts.get(event["status"], 0) + 1
            flow_type_counts[event["flow_type"]] = flow_type_counts.get(event["flow_type"], 0) + 1
        return {
            "total_events": len(self.events),
            "status_counts": status_counts,
            "flow_type_counts": flow_type_counts,
            "stage_counts": {},
            "node_tier_counts": {self.local_node_tier: len(self.events)},
            "top_reasons": [],
        }

    def wait_for_events(self, after_sequence=0, timeout=2.0):
        return [event for event in self.events if event["sequence"] > after_sequence]

    def is_node_id_taken(self, _node_id):
        return type(self).config["duplicate_node_id"]

    def is_node_registered(self, signature):
        return signature in type(self).config["registered_signatures"]

    def verify_registration_sig(self, _req):
        return type(self).config["verify_registration_sig"]

    def registration_flow(self, req):
        type(self).config["last_registration_payload"] = req
        return type(self).config["registration_flow_result"]

    def get_node_by_sig(self, signature):
        return type(self).config["node_details"].get(signature, {"nodeType": 2})

    def access_flow(
        self,
        from_sig,
        to_sig,
        method,
        resource_path,
        expiry_secs=900,
        allow_delegation=False,
        delegation_depth=0,
        audit=True,
    ):
        self.access_calls.append({
            "from_sig": from_sig,
            "to_sig": to_sig,
            "method": method,
            "resource_path": resource_path,
            "expiry_secs": expiry_secs,
            "allow_delegation": allow_delegation,
            "delegation_depth": delegation_depth,
            "audit": audit,
        })
        return type(self).config["access_flow_result"]

    def delegate_flow(self, parent_from_sig, to_sig, child_from_sig, ops_csv, child_expiry_secs):
        self.delegate_calls.append({
            "parent_from_sig": parent_from_sig,
            "to_sig": to_sig,
            "child_from_sig": child_from_sig,
            "ops_csv": ops_csv,
            "child_expiry_secs": child_expiry_secs,
        })
        return type(self).config["delegate_flow_result"]

    def get_grant_ex_auto(self, from_sig, to_sig, method=None, resource_path=None, ctx=None):
        self.grant_lookup_calls.append({
            "from_sig": from_sig,
            "to_sig": to_sig,
            "method": method,
            "resource_path": resource_path,
            "ctx": ctx,
        })
        return type(self).config["grant_lookup_result"]

    def revoke_grant(self, from_sig, to_sig, policy_id):
        self.revoke_calls.append((from_sig, to_sig, policy_id))
        return "0xrevoke"

    def is_grant_expired(self, from_sig, to_sig, policy_id=None):
        self.expiry_calls.append((from_sig, to_sig, policy_id))
        return type(self).config["grant_expired"]

    def find_policy_id(self, from_role, to_role, ops_csv, ctx_schema):
        self.policy_calls.append(("find", from_role, to_role, ops_csv, ctx_schema))
        return type(self).config["find_policy_result"]

    def create_policy(self, from_role, to_role, ops_csv, ctx_schema=None):
        self.policy_calls.append(("create", from_role, to_role, ops_csv, ctx_schema))
        return type(self).config["create_policy_result"]

    def ensure_policy(self, from_role, to_role, ops_csv, ctx_schema=""):
        self.policy_calls.append(("ensure", from_role, to_role, ops_csv, ctx_schema))
        return type(self).config["ensure_policy_result"]

    def get_policy(self, policy_id):
        self.policy_calls.append(("get", policy_id))
        return type(self).config["get_policy_result"]

    def update_policy(self, policy_id, ops_csv, ctx_schema=None):
        self.policy_calls.append(("update", policy_id, ops_csv, ctx_schema))
        return type(self).config["update_policy_result"]

    def deprecate_policy(self, policy_id):
        self.policy_calls.append(("deprecate", policy_id))
        return type(self).config["deprecate_policy_result"]


class DenyLimiter:
    def __init__(self, _rates):
        pass

    def allow(self, _bucket_key, _role):
        return False, 250


class FakeInfrastructureController:
    config = {}
    last_instance = None

    def __init__(self, _repo_root):
        type(self).last_instance = self
        self.start_calls = []
        self.start_root_calls = []
        self.spawn_node_calls = []
        self.stop_calls = []
        self.stop_live_calls = []
        self.kill_all_calls = []
        self.delete_calls = []
        self.stop_process_calls = []
        self.start_process_calls = []
        self.stop_node_calls = []
        self.refresh_calls = []
        self.open_terminal_calls = []

    @classmethod
    def reset(cls):
        cls.config = {
            "job": None,
            "scenarios": [],
            "start_result": {"job_id": "job-1", "status": "running", "scenario": "demo-web"},
            "start_root_result": {"job_id": "root-job-1", "status": "running", "scenario": "demo-live"},
            "spawn_node_result": {"job_id": "spawn-job-1", "status": "running", "scenario": "demo-live"},
            "stop_result": {"scenario": "demo-web", "stopped_pids": [123, 456], "freed_ports": [5600, 8545], "remaining_live_pids": [], "deleted_scenario": False, "scenario_exists_after_stop": True},
            "stop_live_result": {"scenario": "demo-live", "stopped_pids": [123, 456], "freed_ports": [5600, 8545], "remaining_live_pids": [], "deleted_scenario": False, "scenario_exists_after_stop": True},
            "delete_result": {"scenario": "demo-web", "deleted_scenario": True, "scenario_exists_after_delete": False},
            "kill_all_result": {"stopped_pids": [111, 222, 333], "freed_ports": [5002, 5600, 8545], "remaining_live_pids": [], "scenarios": [{"scenario": "demo-web"}], "runner_pid": 999},
            "refresh_result": {"scenario": "demo-web", "manifest_updated": True},
            "stop_process_result": {"scenario": "demo-web", "node_key": "fog-1", "process": "chain", "stopped_pids": [444], "remaining_live_pids": [], "freed_ports": [8547], "node": {"key": "fog-1"}},
            "start_process_result": {"scenario": "demo-web", "node_key": "root", "process": "api", "started_pid": 777, "node": {"key": "root"}},
            "stop_node_result": {"scenario": "demo-web", "node_key": "fog-1", "stopped_processes": ["api", "chain"], "stopped_pids": [333, 444], "remaining_live_pids": [], "freed_ports": [5002, 8547], "node": {"key": "fog-1"}},
            "details": {
                "selected_scenario": "demo-web",
                "scenario": {
                    "scenario": "demo-web",
                    "node_count": 2,
                    "root_api_url": "http://127.0.0.1:5600",
                    "root_rpc_url": "http://127.0.0.1:8545",
                    "graph": {
                        "lanes": [{"key": "cloud", "label": "Cloud"}],
                        "nodes": [{"key": "root", "label": "Root Cloud", "status": "ok", "x": 0.5, "y": 0}],
                        "edges": [],
                    },
                    "node_views": [
                        {
                            "key": "root",
                            "name": "Root Cloud",
                            "tier": "cloud",
                            "node_id": "CLOUD01",
                            "signature": "sig-root",
                            "api_url": "http://127.0.0.1:5600",
                            "rpc_url": "http://127.0.0.1:8545",
                            "p2p_port": 30303,
                            "summary_status": "ok",
                            "summary_label": "ready",
                            "processes": {
                                "api": {"status": "ok", "label": "API ready", "manifest_pid": 111, "observed_pid": 111, "pid_status": "matched_manifest_pid"},
                                "chain": {"status": "ok", "label": "Chain ready", "manifest_pid": 222, "observed_pid": 222, "pid_status": "matched_manifest_pid"},
                                "registration": {"status": "ok", "label": "root active"},
                            },
                            "logs": {"api": "/tmp/root-api.log", "chain": "/tmp/root-besu.log"},
                            "control_url": "http://127.0.0.1:5600/control",
                            "dashboard_url": "http://127.0.0.1:5600/dashboard",
                        },
                        {
                            "key": "fog-1",
                            "name": "Fog1",
                            "tier": "fog",
                            "node_id": "FG-1",
                            "signature": "sig-fog",
                            "api_url": "http://127.0.0.1:5002",
                            "rpc_url": "http://127.0.0.1:8547",
                            "p2p_port": 30304,
                            "summary_status": "running",
                            "summary_label": "provisioning",
                            "processes": {
                                "api": {"status": "ok", "label": "API ready", "manifest_pid": 333, "observed_pid": 334, "pid_status": "stale_manifest_pid"},
                                "chain": {"status": "running", "label": "Chain starting", "manifest_pid": 444, "observed_pid": 444, "pid_status": "matched_manifest_pid"},
                                "registration": {"status": "pending", "label": "registration pending"},
                            },
                            "logs": {"api": "/tmp/fog-api.log", "chain": "/tmp/fog-besu.log"},
                            "control_url": "http://127.0.0.1:5002/control",
                            "dashboard_url": "http://127.0.0.1:5002/dashboard",
                        },
                    ],
                },
            },
            "node_logs": {
                "selected_scenario": "demo-web",
                "node_key": "root",
                "process": "api",
                "lines": 80,
                "exists": True,
                "path": "/tmp/root-api.log",
                "content": "[topology] root api is ready",
                "command": "python orchestration_service.py --port 5600",
            },
            "shells": {
                "selected_scenario": "demo-web",
                "shells": [
                    {
                        "node_key": "root",
                        "node_name": "Root Cloud",
                        "tier": "cloud",
                        "process": "api",
                        "command": "python orchestration_service.py --port 5600",
                        "content": "[topology] root api is ready",
                        "running": True,
                        "status": "ok",
                        "label": "API ready",
                        "updated_at_ms": 1700000000000,
                    }
                ],
            },
            "terminal_result": {
                "selected_scenario": "demo-web",
                "node_key": "root",
                "process": "api",
                "path": "/tmp/root-api.log",
                "command": "python orchestration_service.py --port 5600",
                "opened": True,
            },
        }
        cls.last_instance = None

    def current_job(self):
        return type(self).config["job"]

    def list_scenarios(self):
        return type(self).config["scenarios"]

    def start_topology(self, **kwargs):
        self.start_calls.append(kwargs)
        return type(self).config["start_result"]

    def start_root(self, **kwargs):
        self.start_root_calls.append(kwargs)
        return type(self).config["start_root_result"]

    def spawn_node(self, **kwargs):
        self.spawn_node_calls.append(kwargs)
        return type(self).config["spawn_node_result"]

    def stop_topology(self, scenario, delete_scenario=False):
        self.stop_calls.append({"scenario": scenario, "delete_scenario": delete_scenario})
        return type(self).config["stop_result"]

    def stop_live_topology(self):
        self.stop_live_calls.append({})
        return type(self).config["stop_live_result"]

    def delete_topology(self, scenario):
        self.delete_calls.append({"scenario": scenario})
        payload = dict(type(self).config["delete_result"])
        payload.update({"scenario": scenario})
        return payload

    def kill_all_spawned_processes(self):
        self.kill_all_calls.append({})
        return type(self).config["kill_all_result"]

    def stop_process(self, *, scenario, node_key, process):
        self.stop_process_calls.append({"scenario": scenario, "node_key": node_key, "process": process})
        payload = dict(type(self).config["stop_process_result"])
        payload.update({"scenario": scenario, "node_key": node_key, "process": process})
        return payload

    def start_process(self, *, scenario, node_key, process):
        self.start_process_calls.append({"scenario": scenario, "node_key": node_key, "process": process})
        payload = dict(type(self).config["start_process_result"])
        payload.update({"scenario": scenario, "node_key": node_key, "process": process})
        return payload

    def stop_node(self, *, scenario, node_key):
        self.stop_node_calls.append({"scenario": scenario, "node_key": node_key})
        payload = dict(type(self).config["stop_node_result"])
        payload.update({"scenario": scenario, "node_key": node_key})
        return payload

    def refresh_topology(self, scenario, persist=True):
        self.refresh_calls.append({"scenario": scenario, "persist": persist})
        return type(self).config["refresh_result"]

    def suggested_scenario_name(self):
        return "demo-123"

    def scenario_details(self, scenario=None, active_only=False):
        return type(self).config["details"]

    def node_logs(self, *, scenario=None, node_key="root", process="api", lines=80):
        payload = dict(type(self).config["node_logs"])
        payload.update({
            "selected_scenario": scenario or payload["selected_scenario"],
            "node_key": node_key,
            "process": process,
            "lines": lines,
        })
        return payload

    def shell_grid(self, *, scenario=None, lines=80):
        payload = dict(type(self).config["shells"])
        payload["selected_scenario"] = scenario or payload["selected_scenario"]
        return payload

    def open_terminal(self, *, scenario=None, node_key="root", process="api"):
        self.open_terminal_calls.append({"scenario": scenario, "node_key": node_key, "process": process})
        payload = dict(type(self).config["terminal_result"])
        payload.update({"selected_scenario": scenario or payload["selected_scenario"], "node_key": node_key, "process": process})
        return payload


class DummyUpstreamResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "application/json"}
        self.content = json.dumps(payload).encode("utf-8")

    def json(self):
        return self._payload


@pytest.fixture
def service_module():
    return importlib.import_module("orchestration_service")


@pytest.fixture
def app(monkeypatch, tmp_path, service_module):
    FakeOrchestrator.reset(tmp_path)
    FakeInfrastructureController.reset()
    monkeypatch.setattr(service_module, "Orchestrator", FakeOrchestrator)
    monkeypatch.setattr(service_module, "InfrastructureController", FakeInfrastructureController)
    monkeypatch.setattr(service_module, "_local_signature_from_node_details", lambda _repo_root: "sig-local")
    return service_module.make_app(repo_root=str(tmp_path))


def test_health_endpoint_reports_status_and_request_hooks(app):
    client = app.test_client()

    response = client.get("/health", headers={"X-Latency-Condition": "warm"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["deployed"] is True
    orch = FakeOrchestrator.last_instance
    assert orch.begin_calls == [{"node_tier": "fog", "condition": "warm"}]
    assert orch.end_calls == 1


def test_register_node_rejects_duplicate_node_id(monkeypatch, tmp_path, service_module):
    FakeOrchestrator.reset(tmp_path)
    FakeOrchestrator.config["duplicate_node_id"] = True
    monkeypatch.setattr(service_module, "Orchestrator", FakeOrchestrator)
    monkeypatch.setattr(service_module, "_local_signature_from_node_details", lambda _repo_root: "sig-local")
    client = service_module.make_app(repo_root=str(tmp_path)).test_client()

    response = client.post("/register-node", json={
        "node_id": "FG-1",
        "node_name": "Fog",
        "node_type": "Fog",
        "public_key": "pk",
        "address": "0x1",
        "rpcURL": "http://fog",
        "signature": "sig-fog",
    })

    assert response.status_code == 409
    assert response.get_json()["error"] == "duplicate_node_id"


def test_register_node_returns_async_ack_metadata(app):
    client = app.test_client()

    response = client.post("/register-node", json={
        "node_id": "FG-1",
        "node_name": "Fog",
        "node_type": "Fog",
        "public_key": "pk",
        "address": "0x1",
        "rpcURL": "http://fog",
        "signature": "sig-fog",
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ack_sent"] is False
    assert payload["ack_status"] == "queued"
    assert payload["ack_required"] is True


def test_acknowledgement_accepts_manifest_json_and_fetches_bootstrap(app, monkeypatch):
    client = app.test_client()
    service_module = importlib.import_module("orchestration_service")

    def fake_download(url, *, expected_sha256="", timeout=5.0):
        mapping = {
            "http://root/bootstrap/genesis.json": b'{"config":"genesis"}',
            "http://root/bootstrap/node-registry.json": b'{"abi":"registry"}',
            "http://root/bootstrap/prefunded_keys.json": b'{"prefunded_accounts":[]}',
        }
        payload = mapping[url]
        if expected_sha256:
            assert service_module._sha256_bytes(payload) == expected_sha256
        return payload

    monkeypatch.setattr(service_module, "_download_bootstrap_artifact", fake_download)

    genesis_bytes = b'{"config":"genesis"}'
    registry_bytes = b'{"abi":"registry"}'
    prefunded_bytes = b'{"prefunded_accounts":[]}'
    response = client.post("/acknowledgement", json={
        "node_id": "FG-1",
        "node_type": "Fog",
        "enode": "enode://abcdef@127.0.0.1:30303",
        "genesis_url": "http://root/bootstrap/genesis.json",
        "registry_url": "http://root/bootstrap/node-registry.json",
        "prefunded_keys_url": "http://root/bootstrap/prefunded_keys.json",
        "genesis_sha256": service_module._sha256_bytes(genesis_bytes),
        "registry_sha256": service_module._sha256_bytes(registry_bytes),
        "prefunded_sha256": service_module._sha256_bytes(prefunded_bytes),
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["mode"] == "manifest"


def test_access_endpoint_uses_local_signature_and_passes_audit_flag(app):
    client = app.test_client()

    response = client.post("/access", json={
        "from_signature": "sig-edge",
        "method": "GET",
        "resource_path": "/temperature",
        "expiry_secs": 120,
        "allow_delegation": True,
        "delegation_depth": 2,
        "audit": False,
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    orch = FakeOrchestrator.last_instance
    assert orch.access_calls[0]["to_sig"] == "sig-local"
    assert orch.access_calls[0]["audit"] is False
    assert orch.access_calls[0]["resource_path"] == "/temperature"


def test_access_endpoint_returns_429_when_rate_limited(monkeypatch, tmp_path, service_module):
    FakeOrchestrator.reset(tmp_path)
    monkeypatch.setattr(service_module, "Orchestrator", FakeOrchestrator)
    monkeypatch.setattr(service_module, "TokenBucketRateLimiter", DenyLimiter)
    monkeypatch.setattr(service_module, "_local_signature_from_node_details", lambda _repo_root: "sig-local")
    client = service_module.make_app(repo_root=str(tmp_path)).test_client()

    response = client.post("/access", json={
        "from_signature": "sig-edge",
        "method": "GET",
        "resource_path": "/temperature",
    })

    assert response.status_code == 429
    payload = response.get_json()
    assert payload["reason"] == "rate_limit_exceeded"
    assert payload["retry_after_ms"] == 250


def test_latency_metrics_endpoint_returns_summary_and_output_path(app):
    client = app.test_client()

    response = client.get("/metrics/latency")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["summary"]["checkGrant|fog|cold"]["count"] == 1
    assert Path(payload["output"]).exists()


def test_revoke_grant_resolves_policy_id_from_grant_lookup(app):
    client = app.test_client()

    response = client.post("/revoke-grant", json={
        "from_signature": "sig-edge",
        "to_signature": "sig-local",
        "method": "GET",
        "resource_path": "/temperature",
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["tx"] == "0xrevoke"
    orch = FakeOrchestrator.last_instance
    assert orch.grant_lookup_calls[0]["method"] == "GET"
    assert orch.revoke_calls == [("sig-edge", "sig-local", 7)]


def test_expiry_check_uses_resolved_policy_when_policy_id_missing(app):
    client = app.test_client()

    response = client.get("/expiry-check", query_string={
        "from_signature": "sig-edge",
        "to_signature": "sig-local",
        "method": "GET",
        "resource_path": "/temperature",
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["expired"] is False
    orch = FakeOrchestrator.last_instance
    assert orch.expiry_calls == [("sig-edge", "sig-local", 7)]


def test_acknowledgement_persists_bootstrap_files(app, tmp_path):
    client = app.test_client()

    response = client.post(
        "/acknowledgement",
        data={
            "node_id": "FG-1",
            "enode": "enode://abc123@127.0.0.1:30303",
            "genesis_file": (io.BytesIO(b"{\"config\":{}}"), "genesis.json"),
            "node_registry_file": (io.BytesIO(b"{\"abi\":[]}"), "NodeRegistry.json"),
            "prefunded_keys_file": (io.BytesIO(b"{\"prefunded_accounts\":[]}"), "prefunded_keys.json"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    repo_dir = Path(FakeOrchestrator.last_instance.repo_root)
    assert (repo_dir / "genesis" / "genesis.json").exists()
    assert (repo_dir / "data" / "NodeRegistry.json").exists()
    assert (repo_dir / "prefunded_keys.json").exists()
    assert (repo_dir / "data" / "enode.txt").read_text().strip() == "enode://abc123@127.0.0.1:30303"


def test_bootstrap_ack_persists_embedded_bootstrap_payload(monkeypatch, tmp_path, service_module):
    FakeOrchestrator.reset(tmp_path)
    FakeInfrastructureController.reset()
    monkeypatch.setattr(service_module, "Orchestrator", FakeOrchestrator)
    monkeypatch.setattr(service_module, "InfrastructureController", FakeInfrastructureController)
    monkeypatch.setattr(service_module, "_local_signature_from_node_details", lambda _repo_root: "sig-local")
    monkeypatch.setenv("NODE_ROLE", "fog")
    client = service_module.make_app(repo_root=str(tmp_path)).test_client()

    response = client.post("/bootstrap-ack", json={
        "genesis_b64": "eyJjb25maWciOnt9fQ==",
        "node_registry": {"abi": []},
        "prefunded_keys": {"prefunded_accounts": []},
        "enode": "enode://abc123@127.0.0.1:30303",
    })

    assert response.status_code == 200
    repo_dir = Path(FakeOrchestrator.last_instance.repo_root)
    assert (repo_dir / "genesis" / "genesis.json").read_text() == '{"config":{}}'
    assert json.loads((repo_dir / "data" / "NodeRegistry.json").read_text()) == {"abi": []}
    assert json.loads((repo_dir / "prefunded_keys.json").read_text()) == {"prefunded_accounts": []}
    assert (repo_dir / "data" / ".bootstrap_ready").read_text() == "1"


def test_bootstrap_enode_returns_current_root_enode(monkeypatch, tmp_path, service_module):
    FakeOrchestrator.reset(tmp_path)
    FakeInfrastructureController.reset()
    monkeypatch.setattr(service_module, "Orchestrator", FakeOrchestrator)
    monkeypatch.setattr(service_module, "InfrastructureController", FakeInfrastructureController)
    monkeypatch.setattr(service_module, "_local_signature_from_node_details", lambda _repo_root: "sig-local")
    monkeypatch.setattr(service_module.AcknowledgementSender, "_cached_enode", classmethod(lambda cls, *_args, **_kwargs: "enode://abc123@127.0.0.1:30303"))
    monkeypatch.setenv("NODE_ROLE", "cloud")
    client = service_module.make_app(repo_root=str(tmp_path)).test_client()

    response = client.get("/bootstrap/enode.txt")

    assert response.status_code == 200
    assert response.get_data(as_text=True).strip() == "enode://abc123@127.0.0.1:30303"


def test_endpoint_access_forwards_to_parent(monkeypatch, tmp_path, service_module):
    FakeOrchestrator.reset(tmp_path)
    monkeypatch.setattr(service_module, "Orchestrator", FakeOrchestrator)
    monkeypatch.setattr(service_module, "_local_signature_from_node_details", lambda _repo_root: "sig-local")
    monkeypatch.setenv("NODE_ROLE", "endpoint")
    monkeypatch.setenv("PARENT_URL", "http://fog:5600")

    forwarded = {}

    def fake_post(url, json=None, timeout=0):
        forwarded.update({"url": url, "json": json, "timeout": timeout})
        return DummyUpstreamResponse({"ok": True, "granted": True, "upstream": "fog"})

    monkeypatch.setattr(service_module.requests, "post", fake_post)
    client = service_module.make_app(repo_root=str(tmp_path)).test_client()

    response = client.post("/access", json={
        "from_signature": "sig-edge",
        "method": "GET",
        "resource_path": "/temperature",
    })

    assert response.status_code == 200
    assert response.get_json()["upstream"] == "fog"
    assert forwarded["url"] == "http://fog:5600/access"
    assert forwarded["json"]["resource_path"] == "/temperature"
    assert FakeOrchestrator.last_instance.access_calls == []


def test_endpoint_delegate_and_grant_forward_to_parent(monkeypatch, tmp_path, service_module):
    FakeOrchestrator.reset(tmp_path)
    monkeypatch.setattr(service_module, "Orchestrator", FakeOrchestrator)
    monkeypatch.setattr(service_module, "_local_signature_from_node_details", lambda _repo_root: "sig-local")
    monkeypatch.setenv("NODE_ROLE", "endpoint")
    monkeypatch.setenv("PARENT_URL", "http://fog:5600")

    calls = []

    def fake_post(url, json=None, timeout=0):
        calls.append(("POST", url, json, timeout))
        return DummyUpstreamResponse({"ok": True, "route": url.rsplit("/", 1)[-1]})

    def fake_get(url, params=None, timeout=0):
        calls.append(("GET", url, params, timeout))
        return DummyUpstreamResponse({"ok": True, "route": url.rsplit("/", 1)[-1], "params": dict(params or {})})

    monkeypatch.setattr(service_module.requests, "post", fake_post)
    monkeypatch.setattr(service_module.requests, "get", fake_get)
    client = service_module.make_app(repo_root=str(tmp_path)).test_client()

    delegate_response = client.post("/delegate", json={
        "parent_from_sig": "sig-parent",
        "to_sig": "sig-to",
        "child_from_sig": "sig-child",
        "ops_csv": "READ",
    })
    grant_response = client.get("/grant", query_string={
        "from_signature": "sig-parent",
        "to_signature": "sig-to",
    })

    assert delegate_response.status_code == 200
    assert grant_response.status_code == 200
    assert calls[0][1] == "http://fog:5600/delegate"
    assert calls[1][1] == "http://fog:5600/grant"
    assert calls[1][2]["from_signature"] == "sig-parent"


def test_temperature_route_reuses_access_flow_helper(app):
    client = app.test_client()

    response = client.get("/temperature", query_string={
        "from_signature": "sig-edge",
        "resource_path": "/temperature",
    })

    assert response.status_code == 200
    orch = FakeOrchestrator.last_instance
    assert orch.access_calls[0]["method"] == "GET"
    assert orch.access_calls[0]["resource_path"] == "/temperature"


def test_dashboard_and_event_endpoints_return_live_data(app):
    client = app.test_client()

    client.post("/access", json={
        "from_signature": "sig-edge",
        "method": "GET",
        "resource_path": "/temperature",
    })

    recent = client.get("/events/recent", query_string={"limit": 10})
    flows = client.get("/events/flows", query_string={"limit": 10})
    active = client.get("/events/active")
    stats = client.get("/events/stats")
    dashboard = client.get("/dashboard")
    results = client.get("/results")
    dashboard_data = client.get("/dashboard/data")
    results_data = client.get("/results/data")
    chart = client.get("/results/chart/end_to_end_latency")
    stream = client.get("/events/stream", query_string={"follow": 0, "after": 0})

    assert recent.status_code == 200
    assert len(recent.get_json()["events"]) >= 1
    assert flows.status_code == 200
    assert flows.get_json()["flows"][0]["flow_type"] == "access"
    assert active.status_code == 200
    assert active.get_json()["flows"] == []
    assert stats.status_code == 200
    assert stats.get_json()["stats"]["total_events"] >= 1
    assert dashboard.status_code == 200
    assert "BlockCap Results" in dashboard.get_data(as_text=True)
    assert results.status_code == 200
    assert "BlockCap Results" in results.get_data(as_text=True)
    assert "scenario-select" in results.get_data(as_text=True)
    assert "Clear Selection" in results.get_data(as_text=True)
    assert "Existing Topologies" in results.get_data(as_text=True)
    assert "Saved Results" in results.get_data(as_text=True)
    assert "End-to-End Access Latency" in results.get_data(as_text=True)
    assert "Cold and warm access round-trip time from request to grant or deny at Cloud, Fog, Edge, and Endpoint tiers." in results.get_data(as_text=True)
    assert "Load-Test Throughput" in results.get_data(as_text=True)
    assert "Load-Test Latency" in results.get_data(as_text=True)
    assert "Token Lifecycle Latencies" in results.get_data(as_text=True)
    assert "Revocation Propagation" in results.get_data(as_text=True)
    assert "Experimental Setup" in results.get_data(as_text=True)
    assert "Smart Contract Deployment Gas" in results.get_data(as_text=True)
    assert "Gas Cost Comparison" in results.get_data(as_text=True)
    assert dashboard_data.status_code == 200
    payload = dashboard_data.get_json()
    assert payload["node"]["node_id"] == "FG-1"
    assert payload["health"]["deployed"] is True
    assert results_data.status_code == 200
    assert results_data.get_json()["selected_scenario"] == "demo-web"
    assert "scenarios" in results_data.get_json()
    assert "result_scenarios" in results_data.get_json()
    assert "research_sections" in results_data.get_json()
    assert chart.status_code == 200
    assert chart.mimetype == "image/svg+xml"
    lines = [line for line in stream.get_data(as_text=True).splitlines() if line.strip()]
    assert len(lines) >= 1
    assert json.loads(lines[0])["flow_type"] == "access"


def test_live_dashboard_and_api_results_routes(app):
    client = app.test_client()

    live_dashboard = client.get("/live-dashboard")
    api_results = client.get("/api/results")
    dashboard_stream = client.get("/dashboard/stream", buffered=False)
    spec = client.get("/spec")

    assert live_dashboard.status_code == 200
    assert "BlockCap Live Dashboard" in live_dashboard.get_data(as_text=True)
    assert api_results.status_code == 200
    payload = api_results.get_json()
    assert payload["ok"] is True
    assert "nodes" in payload
    assert "latency" in payload
    assert dashboard_stream.status_code == 200
    assert dashboard_stream.mimetype == "text/event-stream"
    assert spec.status_code == 200
    assert spec.get_json()["node_role"] == "cloud"


def test_admin_routes_require_token(monkeypatch, tmp_path, service_module):
    FakeOrchestrator.reset(tmp_path)
    FakeInfrastructureController.reset()
    monkeypatch.setattr(service_module, "Orchestrator", FakeOrchestrator)
    monkeypatch.setattr(service_module, "InfrastructureController", FakeInfrastructureController)
    monkeypatch.setattr(service_module, "_local_signature_from_node_details", lambda _repo_root: "sig-local")
    monkeypatch.setenv("NODE_ROLE", "cloud")
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    client = service_module.make_app(repo_root=str(tmp_path)).test_client()

    unauthorized = client.get("/admin/policy/list")
    assert unauthorized.status_code == 401

    headers = {"Authorization": "Bearer secret"}
    nodes = client.get("/admin/nodes/list", headers=headers)
    policies = client.get("/admin/policy/list", headers=headers)
    create = client.post("/admin/policy/create", headers=headers, json={
        "from_role": "Fog",
        "to_role": "Sensor",
        "ops": "READ",
        "resource": "/temperature",
    })
    revoke = client.post("/admin/grant/revoke", headers=headers, json={
        "from_sig": "sig-a",
        "to_sig": "sig-b",
        "policy_id": 7,
    })

    assert nodes.status_code == 200
    assert policies.status_code == 200
    assert create.status_code == 200
    assert revoke.status_code == 200


def test_admin_policy_create_rejects_invalid_roles(monkeypatch, tmp_path, service_module):
    FakeOrchestrator.reset(tmp_path)
    FakeInfrastructureController.reset()
    monkeypatch.setattr(service_module, "Orchestrator", FakeOrchestrator)
    monkeypatch.setattr(service_module, "InfrastructureController", FakeInfrastructureController)
    monkeypatch.setattr(service_module, "_local_signature_from_node_details", lambda _repo_root: "sig-local")
    monkeypatch.setenv("NODE_ROLE", "cloud")
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    client = service_module.make_app(repo_root=str(tmp_path)).test_client()

    response = client.post("/admin/policy/create", headers={"Authorization": "Bearer secret"}, json={
        "from_role": "Edge",
        "to_role": "Endpoint",
        "ops": "READ",
        "resource": "/invalid-endpoint-check",
    })

    assert response.status_code == 422
    payload = response.get_json()
    assert payload["error"] == "invalid_policy_roles"
    assert "Endpoint" in payload["detail"]
    assert FakeOrchestrator.last_instance.policy_calls == []


def test_results_data_surfaces_research_sections_from_artifacts(app, tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "experimental_results.json").write_text(json.dumps({
        "end_to_end_latency": [
            {"tier": "cloud", "condition": "cold", "mean_latency_ms": 12.5, "p95_latency_ms": 14.1, "stddev_latency_ms": 1.1, "count": 3, "error_count": 0},
            {"tier": "fog", "condition": "warm", "mean_latency_ms": 8.0, "p95_latency_ms": 9.1, "stddev_latency_ms": 0.6, "count": 3, "error_count": 1},
        ],
        "token_lifecycle_latency": [
            {"operation": "issueToken", "tier": "cloud", "condition": "cold", "mean_latency_ms": 21.0, "stddev_latency_ms": 1.0, "count": 2, "source_key": "issueToken|cloud|cold"},
            {"operation": "revokeTokenPropagation", "tier": "fog", "condition": "warm", "mean_latency_ms": 31.0, "stddev_latency_ms": 1.2, "count": 2, "source_key": "revokeTokenPropagation|fog|warm"},
        ],
        "experimental_setup": {
            "block_time_seconds": 5,
            "validator_set_size": 3,
            "validator_nodes": ["Root", "Fog1", "Fog2"],
            "request_timeout_seconds": 4,
            "epoch_length": 30000,
            "selection_rationale": "QBFT defaults for local experiments",
        },
        "contract_metrics": [
            {"contract": "NodeRegistry", "source_lines_non_blank": 100, "bytecode_size_kb": 2.5, "estimated_deployment_gas": 1000, "deployment_gas_used": 1200},
            {"contract": "TOTALS", "source_lines_non_blank": 100, "bytecode_size_kb": 2.5, "estimated_deployment_gas": 1000, "deployment_gas_used": 1200},
        ],
        "gas_comparison": {
            "baseline_complete": False,
            "baseline_source": str(tmp_path / "experiment_baselines" / "gas_baselines.json"),
            "blockcap_source": str(results_dir / "gas_summary.json"),
            "table": [
                {"operation": "register", "system": "BlockCap", "gas_cost": 100},
                {"operation": "register", "system": "BlendCAC", "gas_cost": 120},
                {"operation": "register", "system": "ACS-IoT", "gas_cost": None},
            ],
        },
        "load_tests": {
            "10": {"throughput_rps": 15.0, "mean_latency_ms": 20.0, "p95_latency_ms": 30.0, "error_count": 0, "completed_requests": 100},
            "50": {"throughput_rps": 10.0, "mean_latency_ms": 40.0, "p95_latency_ms": 60.0, "error_count": 1, "completed_requests": 100},
            "100": {"throughput_rps": 5.0, "mean_latency_ms": 80.0, "p95_latency_ms": 120.0, "error_count": 3, "completed_requests": 100},
        },
    }))
    (results_dir / "gas_comparison.json").write_text(json.dumps({
        "baseline_complete": False,
        "baseline_source": str(tmp_path / "experiment_baselines" / "gas_baselines.json"),
        "blockcap_source": str(results_dir / "gas_summary.json"),
        "table": [
            {"operation": "register", "system": "BlockCap", "gas_cost": 100},
            {"operation": "register", "system": "BlendCAC", "gas_cost": 120},
            {"operation": "register", "system": "ACS-IoT", "gas_cost": None},
        ],
    }))

    client = app.test_client()
    response = client.get("/results/data")

    assert response.status_code == 200
    payload = response.get_json()
    sections = payload["research_sections"]
    assert sections["end_to_end_latency"]["available"] is True
    assert sections["end_to_end_latency"]["rows"][0]["tier"] == "Cloud"
    assert sections["load_tests"]["rows"][0]["concurrency"] == 10
    assert sections["token_lifecycle_latency"]["rows"][0]["operation"] == "Issue"
    assert sections["revocation_propagation"]["rows"][0]["operation"] == "Revocation Propagation"
    assert sections["experimental_setup"]["rows"][0]["label"] == "Block Time (s)"
    assert sections["contract_metrics"]["rows"][0]["contract"] == "NodeRegistry"
    assert sections["gas_comparison"]["baseline_complete"] is False
    assert "Baseline file is incomplete" in sections["gas_comparison"]["note"]
    assert payload["artifacts"]["gas_comparison"]["table"][0]["system"] == "BlockCap"


def test_results_data_returns_empty_sections_when_artifacts_are_missing(app):
    client = app.test_client()

    response = client.get("/results/data")

    assert response.status_code == 200
    sections = response.get_json()["research_sections"]
    assert sections["end_to_end_latency"]["available"] is False
    assert sections["load_tests"]["available"] is False
    assert sections["token_lifecycle_latency"]["available"] is False
    assert sections["revocation_propagation"]["available"] is False
    assert sections["experimental_setup"]["available"] is False
    assert sections["contract_metrics"]["available"] is False
    assert sections["gas_comparison"]["available"] is False


def test_results_data_prefers_selected_topology_runtime_metrics(app, monkeypatch, service_module):
    client = app.test_client()

    monkeypatch.setattr(
        service_module,
        "_topology_runtime_metrics",
        lambda **kwargs: {
            "source": "topology_root_api",
            "event_stats": {
                "total_events": 11,
                "status_counts": {"ok": 4, "denied": 3, "error": 1},
            },
            "active_flows": [{"flow_id": "flow-1"}],
            "recent_events": [{"event_id": "evt-live", "message": "registration running"}],
            "recent_flows": [{"flow_id": "flow-1", "flow_type": "registration"}],
            "latency_summary": {
                "ensurePolicy|fog|cold": {
                    "operation": "ensurePolicy",
                    "node_tier": "fog",
                    "condition": "cold",
                    "mean_ms": 44.0,
                    "count": 2,
                }
            },
            "live_series": [],
            "scenario_details": {
                "selected_scenario": "demo-web",
                "scenario": {"node_count": 7, "node_views": [{}, {}, {}, {}, {}, {}, {}]},
            },
        },
    )

    payload = client.get("/results/data").get_json()

    assert payload["summary_cards"][1]["value"] == 7
    assert payload["event_stats"]["total_events"] == 11
    assert payload["active_flows"][0]["flow_id"] == "flow-1"
    assert payload["recent_events"][0]["event_id"] == "evt-live"
    assert payload["recent_flows"][0]["flow_type"] == "registration"
    assert payload["latency_summary"]["ensurePolicy|fog|cold"]["mean_ms"] == 44.0


def test_home_and_console_pages_render(app):
    client = app.test_client()

    home = client.get("/")
    console = client.get("/control")
    topology = client.get("/topology")
    control_data = client.get("/control/data")
    topology_data = client.get("/topology/data")

    assert home.status_code == 200
    assert "BlockCap Web Operator" in home.get_data(as_text=True)
    assert "Open Topology Page" in home.get_data(as_text=True)
    assert console.status_code == 200
    html = console.get_data(as_text=True)
    assert "BlockCap Control" in html
    assert "Scenario Selection" in html
    assert "Open Topology Page" in html
    assert "Recent Activity" in html
    assert "Topology Version" in html
    assert topology.status_code == 200
    topology_html = topology.get_data(as_text=True)
    assert "BlockCap Topology" in topology_html
    assert "Start Root" in topology_html
    assert "Spawn Fog" in topology_html
    assert "Spawn Edge" in topology_html
    assert "Spawn Endpoint" in topology_html
    assert "Stop Live Topology" in topology_html
    assert "Main Terminal" in topology_html
    assert "Topology runner shell" in topology_html
    assert 'id="scenario-name"' not in topology_html
    assert "Topology Version" not in topology_html
    assert "Delete Current Topology" not in topology_html
    assert control_data.status_code == 200
    payload = control_data.get_json()
    assert payload["ok"] is True
    assert payload["node"]["local_signature"] == "sig-local"
    assert payload["health"]["deployed"] is True
    assert payload["suggested_scenario"] == "demo-123"
    assert payload["selected_scenario"] == "demo-web"
    assert payload["selection_mode"] == "auto"
    assert payload["node_cards"][0]["key"] == "root"
    assert payload["node_cards"][0]["signature"] == "sig-root"
    assert payload["node_cards"][0]["stage"] == "Ready"
    assert payload["node_cards"][0]["api_ready"] is True
    assert payload["node_cards"][0]["phases"][0]["label"] == "API"
    assert payload["node_cards"][0]["phases"][0]["status"] == "complete"
    assert payload["node_cards"][0]["action_capabilities"]["access"]["enabled"] is True
    assert payload["node_cards"][0]["defaults"]["target_signature"] == "sig-fog"
    assert payload["node_cards"][1]["tier"] == "fog"
    assert payload["node_cards"][1]["chain_ready"] is False
    assert payload["node_cards"][1]["phases"][1]["label"] == "Chain"
    assert payload["measured_metrics"][0]["label"] == "End-to-End Access Latency"
    assert topology_data.status_code == 200
    topology_payload = topology_data.get_json()
    assert topology_payload["ok"] is True
    assert topology_payload["selection_mode"] == "live"
    assert topology_payload["spawn_controls"]["root_enabled"] is False
    assert topology_payload["spawn_controls"]["fog_enabled"] is True
    assert topology_payload["node_cards"][0]["key"] == "root"
    assert topology_payload["root_terminals"]["api"]["visible"] is True
    assert topology_payload["root_terminals"]["chain"]["visible"] is True
    assert "root api is ready" in " ".join(topology_payload["root_terminals"]["api"]["preview_lines"]).lower()


def test_control_data_prefers_selected_topology_runtime_events_for_node_phases(app, monkeypatch):
    client = app.test_client()
    service_module = importlib.import_module("orchestration_service")

    def fake_runtime_metrics(**_kwargs):
        return {
            "recent_events": [
                {
                    "node_id": "FG-1",
                    "node_name": "Fog1",
                    "node_tier": "fog",
                    "flow_type": "registration",
                    "stage": "registration_completed",
                    "status": "ok",
                    "message": "Registration complete",
                },
                {
                    "node_id": "FG-1",
                    "node_name": "Fog1",
                    "node_tier": "fog",
                    "flow_type": "validator",
                    "stage": "validator_inclusion_result",
                    "status": "ok",
                    "message": "Validator included",
                },
            ],
        }

    monkeypatch.setattr(service_module, "_topology_runtime_metrics", fake_runtime_metrics)

    response = client.get("/control/data", query_string={"scenario": "demo-web"})

    assert response.status_code == 200
    payload = response.get_json()
    fog = next(card for card in payload["node_cards"] if card["key"] == "fog-1")
    phase_map = {phase["key"]: phase["status"] for phase in fog["phases"]}
    assert phase_map["registration"] == "complete"
    assert phase_map["consensus"] == "complete"


def test_control_data_uses_signature_linked_registration_events_for_fog_phase(app, monkeypatch):
    client = app.test_client()
    service_module = importlib.import_module("orchestration_service")

    def fake_runtime_metrics(**_kwargs):
        return {
            "recent_events": [
                {
                    "node_id": "CLOUD01",
                    "node_name": "Root Cloud",
                    "node_tier": "fog",
                    "flow_type": "registration",
                    "stage": "registration_submit",
                    "status": "started",
                    "message": "Submitting node registration transaction",
                    "from_signature": "sig-fog",
                    "to_signature": "",
                },
            ],
        }

    monkeypatch.setattr(service_module, "_topology_runtime_metrics", fake_runtime_metrics)

    response = client.get("/control/data", query_string={"scenario": "demo-web"})

    assert response.status_code == 200
    payload = response.get_json()
    fog = next(card for card in payload["node_cards"] if card["key"] == "fog-1")
    phase_map = {phase["key"]: phase["status"] for phase in fog["phases"]}
    assert phase_map["registration"] == "active"


def test_control_data_keeps_registration_complete_while_validator_consensus_is_active(app, monkeypatch):
    client = app.test_client()
    service_module = importlib.import_module("orchestration_service")

    node_views = FakeInfrastructureController.config["details"]["scenario"]["node_views"]
    fog = next(node for node in node_views if node["key"] == "fog-1")
    fog["processes"]["registration"] = {"status": "ok", "label": "validator proposed"}

    def fake_runtime_metrics(**_kwargs):
        return {
            "recent_events": [
                {
                    "node_id": "",
                    "node_name": "",
                    "node_tier": "fog",
                    "flow_type": "validator",
                    "stage": "validator_vote",
                    "status": "started",
                    "message": "Submitting validator vote",
                    "from_signature": "sig-fog",
                    "to_signature": "",
                },
            ],
        }

    monkeypatch.setattr(service_module, "_topology_runtime_metrics", fake_runtime_metrics)

    response = client.get("/control/data", query_string={"scenario": "demo-web"})

    assert response.status_code == 200
    payload = response.get_json()
    fog = next(card for card in payload["node_cards"] if card["key"] == "fog-1")
    phase_map = {phase["key"]: phase["status"] for phase in fog["phases"]}
    assert phase_map["registration"] == "complete"
    assert phase_map["consensus"] == "active"


def test_control_data_shows_placeholder_nodes_while_topology_is_starting(app):
    client = app.test_client()
    FakeInfrastructureController.config["job"] = {
        "job_id": "job-2",
        "scenario": "demo-pending",
        "status": "running",
        "runner_command": "/tmp/.venv/bin/python scripts/run_topology.py --cloud 1 --fog 2 --edge 1 --endpoint 2 --scenario demo-pending",
        "log_lines": [
            "[topology] starting root chain attempt 1 on rpc=44001 p2p=30303",
            "[topology] starting root api on port 5600",
            "[topology] starting fog1 api on port 5002",
            "[topology] starting fog1 chain on rpc=8547 p2p=30304",
            "[topology] root chain is ready",
            "[topology] root api is ready",
        ],
        "topology_request": {
            "cloud": 1,
            "fog": 2,
            "edge": 1,
            "endpoint": 2,
            "endpoint_role": "Sensor",
            "endpoint_roles": ["Sensor", "Actuator"],
            "fog_devices": ["jetson-orin-nano-8gb", "jetson-xavier-nx-8gb"],
            "edge_devices": ["raspberry-pi-4-4gb"],
            "endpoint_devices": ["raspberry-pi-zero-2-w", "compute-module-4-2gb"],
            "host": "127.0.0.1",
        },
    }
    FakeInfrastructureController.config["details"] = {
        "selected_scenario": "demo-pending",
        "scenario": None,
    }

    response = client.get("/control/data", query_string={"scenario": "demo-pending"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["selected_scenario"] == "demo-pending"
    assert payload["selection_mode"] == "manual"
    assert "run_topology.py" in payload["job"]["runner_command"]
    assert len(payload["node_cards"]) == 6
    assert payload["node_cards"][0]["key"] == "root"
    assert payload["node_cards"][0]["stage"] == "Ready"
    assert payload["node_cards"][0]["api_ready"] is True
    assert payload["node_cards"][0]["chain_ready"] is True
    assert payload["node_cards"][0]["phases"][2]["status"] == "complete"
    assert payload["node_cards"][0]["api_url"] == "http://127.0.0.1:5600"
    assert payload["node_cards"][0]["rpc_url"] == "http://127.0.0.1:44001"
    assert payload["node_cards"][0]["p2p_port"] == "30303"
    assert payload["node_cards"][1]["key"] == "fog-1"
    assert payload["node_cards"][1]["api_url"] == "http://127.0.0.1:5002"
    assert payload["node_cards"][1]["rpc_url"] == "http://127.0.0.1:8547"
    assert payload["node_cards"][1]["p2p_port"] == "30304"
    assert payload["node_cards"][1]["phases"][0]["status"] == "active"
    assert payload["node_cards"][1]["phases"][1]["status"] == "active"
    assert payload["node_cards"][1]["simulated_device"] == "Jetson Orin Nano 8GB"
    assert payload["node_cards"][-1]["name"] == "Endpoint2 (Actuator)"
    assert payload["node_cards"][-1]["simulated_device"] == "Compute Module 4 2GB"


def test_build_node_cards_does_not_keep_terminals_ready_from_stale_job_logs(monkeypatch):
    service_module = importlib.import_module("orchestration_service")

    scenario_payload = {
        "node_views": [
            {
                "key": "root",
                "name": "Root Cloud",
                "node_id": "CLOUD01",
                "signature": "sig-root",
                "tier": "cloud",
                "ordinal": 0,
                "api_url": "http://127.0.0.1:5600",
                "rpc_url": "http://127.0.0.1:44001",
                "p2p_port": 30303,
                "summary_status": "down",
                "summary_label": "stopped",
                "control_url": None,
                "dashboard_url": None,
                "processes": {
                    "api": {"status": "down"},
                    "chain": {"status": "down"},
                    "registration": {"status": "ok"},
                },
            }
        ]
    }
    monkeypatch.setattr(service_module, "_build_phase_progress", lambda *_args, **_kwargs: [
        {"key": "api", "label": "API", "status": "pending"},
        {"key": "chain", "label": "Chain", "status": "pending"},
        {"key": "registration", "label": "Registration", "status": "complete"},
        {"key": "bootstrap_ack", "label": "Bootstrap ACK", "status": "not_applicable"},
        {"key": "access", "label": "Access", "status": "pending"},
        {"key": "consensus", "label": "Consensus", "status": "pending"},
    ])

    cards = service_module._build_node_cards(
        scenario_payload,
        [],
        local_node_id="CLOUD01",
        local_node_name="Root Cloud",
        selected_scenario="demo-web",
        job={"status": "running", "log_lines": ["[topology] root api is ready", "[topology] root chain is ready"]},
    )

    assert cards[0]["api_ready"] is False
    assert cards[0]["chain_ready"] is False
    assert cards[0]["stop_enabled"] is False


def test_infrastructure_endpoints_start_stop_and_report_status(app):
    client = app.test_client()
    FakeInfrastructureController.config["job"] = {"job_id": "job-1", "status": "running"}
    FakeInfrastructureController.config["scenarios"] = [{"scenario": "demo-web", "running": True}]

    status = client.get("/infrastructure/status")
    assert status.status_code == 200
    payload = status.get_json()
    assert payload["job"]["job_id"] == "job-1"
    assert payload["scenarios"][0]["scenario"] == "demo-web"

    started = client.post("/infrastructure/start-topology", json={
        "scenario": "demo-web",
        "cloud": 1,
        "fog": 2,
        "edge": 2,
        "endpoint": 3,
        "endpoint_roles": ["Sensor", "Actuator", "Sensor"],
        "fog_devices": ["jetson-orin-nano-8gb", "jetson-xavier-nx-8gb"],
        "edge_devices": ["raspberry-pi-5-8gb-edge", "raspberry-pi-4-4gb"],
        "endpoint_devices": ["raspberry-pi-zero-2-w", "raspberry-pi-4-2gb", "compute-module-5-2gb"],
        "host": "127.0.0.1",
    })
    assert started.status_code == 202
    infra = FakeInfrastructureController.last_instance
    assert infra.start_calls[0]["scenario"] == "demo-web"
    assert infra.start_calls[0]["fog"] == 2
    assert infra.start_calls[0]["endpoint_roles"] == ["Sensor", "Actuator", "Sensor"]
    assert infra.start_calls[0]["fog_devices"] == ["jetson-orin-nano-8gb", "jetson-xavier-nx-8gb"]
    assert infra.start_calls[0]["endpoint_devices"] == ["raspberry-pi-zero-2-w", "raspberry-pi-4-2gb", "compute-module-5-2gb"]

    start_root = client.post("/infrastructure/start-root", json={"host": "127.0.0.1"})
    spawn_fog = client.post("/infrastructure/spawn-node", json={
        "tier": "fog",
        "device_id": "jetson-orin-nano-8gb",
        "host": "127.0.0.1",
    })
    spawn_endpoint = client.post("/infrastructure/spawn-node", json={
        "tier": "endpoint",
        "device_id": "raspberry-pi-zero-2-w",
        "endpoint_role": "Actuator",
        "host": "127.0.0.1",
    })
    stop_live = client.post("/infrastructure/stop-live-topology", json={})
    assert start_root.status_code == 202
    assert spawn_fog.status_code == 202
    assert spawn_endpoint.status_code == 202
    assert stop_live.status_code == 200
    assert infra.start_root_calls == [{"host": "127.0.0.1"}]
    assert infra.spawn_node_calls == [
        {"tier": "fog", "device_id": "jetson-orin-nano-8gb", "endpoint_role": "Sensor", "host": "127.0.0.1"},
        {"tier": "endpoint", "device_id": "raspberry-pi-zero-2-w", "endpoint_role": "Actuator", "host": "127.0.0.1"},
    ]
    assert infra.stop_live_calls == [{}]

    refreshed = client.post("/infrastructure/refresh-topology", json={"scenario": "demo-web"})
    opened = client.post("/infrastructure/open-terminal", json={"scenario": "demo-web", "node_key": "fog-1", "process": "chain"})
    started_process = client.post("/infrastructure/start-process", json={"scenario": "demo-web", "node_key": "root", "process": "api"})
    stopped_process = client.post("/infrastructure/stop-process", json={"scenario": "demo-web", "node_key": "fog-1", "process": "chain"})
    stopped_node = client.post("/infrastructure/stop-node", json={"scenario": "demo-web", "node_key": "fog-1"})
    killed = client.post("/infrastructure/kill-all", json={})
    deleted = client.post("/infrastructure/delete-topology", json={"scenario": "demo-web"})
    FakeInfrastructureController.config["stop_result"]["deleted_scenario"] = True
    FakeInfrastructureController.config["stop_result"]["scenario_exists_after_stop"] = False
    stopped = client.post("/infrastructure/stop-topology", json={"scenario": "demo-web", "delete_scenario": True})
    assert refreshed.status_code == 200
    assert opened.status_code == 200
    assert started_process.status_code == 200
    assert stopped_process.status_code == 200
    assert stopped_node.status_code == 200
    assert killed.status_code == 200
    assert deleted.status_code == 200
    assert stopped.status_code == 200
    assert FakeInfrastructureController.last_instance.refresh_calls == [{"scenario": "demo-web", "persist": True}]
    assert FakeInfrastructureController.last_instance.open_terminal_calls == [{"scenario": "demo-web", "node_key": "fog-1", "process": "chain"}]
    assert FakeInfrastructureController.last_instance.start_process_calls == [{"scenario": "demo-web", "node_key": "root", "process": "api"}]
    assert FakeInfrastructureController.last_instance.stop_process_calls == [{"scenario": "demo-web", "node_key": "fog-1", "process": "chain"}]
    assert FakeInfrastructureController.last_instance.stop_node_calls == [{"scenario": "demo-web", "node_key": "fog-1"}]
    assert FakeInfrastructureController.last_instance.kill_all_calls == [{}]
    assert FakeInfrastructureController.last_instance.delete_calls == [{"scenario": "demo-web"}]
    assert FakeInfrastructureController.last_instance.stop_calls[-1] == {"scenario": "demo-web", "delete_scenario": True}


def test_infrastructure_details_and_logs_endpoints_return_ui_payload(app):
    client = app.test_client()

    details = client.get("/infrastructure/details")
    logs = client.get("/infrastructure/node-logs", query_string={"scenario": "demo-web", "node": "fog-1", "process": "chain", "lines": 40})
    shells = client.get("/infrastructure/shells", query_string={"scenario": "demo-web", "lines": 40})

    assert details.status_code == 200
    details_payload = details.get_json()
    assert details_payload["selected_scenario"] == "demo-web"
    assert details_payload["scenario"]["node_views"][0]["name"] == "Root Cloud"
    assert details_payload["scenario"]["node_views"][1]["summary_label"] == "provisioning"
    assert details_payload["scenario"]["graph"]["nodes"][0]["key"] == "root"
    assert details_payload["scenario"]["node_views"][0]["p2p_port"] == 30303
    assert details_payload["scenario"]["node_views"][1]["processes"]["api"]["pid_status"] == "stale_manifest_pid"
    assert details_payload["node_cards"][0]["badge"] == "R"
    assert details_payload["node_cards"][1]["stage"] == "Chain"
    assert details_payload["measured_metrics"][0]["label"] == "End-to-End Access Latency"

    assert logs.status_code == 200
    logs_payload = logs.get_json()
    assert logs_payload["node_key"] == "fog-1"
    assert logs_payload["process"] == "chain"
    assert logs_payload["lines"] == 40
    assert "root api is ready" in logs_payload["content"]

    assert shells.status_code == 200
    shells_payload = shells.get_json()
    assert shells_payload["selected_scenario"] == "demo-web"
    assert shells_payload["shells"][0]["node_key"] == "root"


def test_control_node_inspector_returns_node_and_flow_lanes(app):
    client = app.test_client()
    orch = FakeOrchestrator.last_instance
    orch.events = [
        {
            "sequence": 1,
            "event_id": "evt-1",
            "ts_unix_ms": 1_700_000_000_000,
            "node_id": "FG-1",
            "node_name": "FogOne",
            "node_tier": "fog",
            "component": "api",
            "flow_type": "access",
            "flow_id": "access-1",
            "stage": "request_received",
            "status": "started",
            "message": "Access request received",
            "details": {},
            "request_id": "req-1",
            "from_signature": "sig-fog",
            "to_signature": "sig-root",
            "policy_id": None,
            "tx_hash": None,
        },
        {
            "sequence": 2,
            "event_id": "evt-2",
            "ts_unix_ms": 1_700_000_000_100,
            "node_id": "FG-1",
            "node_name": "FogOne",
            "node_tier": "fog",
            "component": "orchestrator",
            "flow_type": "access",
            "flow_id": "access-1",
            "stage": "grant_issue_or_reuse",
            "status": "ok",
            "message": "Issued a fresh grant",
            "details": {},
            "request_id": "req-1",
            "from_signature": "sig-fog",
            "to_signature": "sig-root",
            "policy_id": 7,
            "tx_hash": "0xabc",
        },
        {
            "sequence": 3,
            "event_id": "evt-3",
            "ts_unix_ms": 1_700_000_000_200,
            "node_id": "FG-1",
            "node_name": "FogOne",
            "node_tier": "fog",
            "component": "orchestrator",
            "flow_type": "access",
            "flow_id": "access-1",
            "stage": "access_finished",
            "status": "ok",
            "message": "Access granted",
            "details": {},
            "request_id": "req-1",
            "from_signature": "sig-fog",
            "to_signature": "sig-root",
            "policy_id": 7,
            "tx_hash": None,
        },
    ]

    response = client.get("/control/node-inspector", query_string={"scenario": "demo-web", "node_key": "fog-1"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["node"]["key"] == "fog-1"
    assert payload["node"]["signature"] == "sig-fog"
    assert payload["node"]["action_capabilities"]["access"]["enabled"] is True
    assert payload["node"]["defaults"]["target_signature"] == "sig-root"
    assert payload["outgoing_flows"][0]["action"] == "Access Request"
    assert payload["outgoing_flows"][0]["current_stage"] in {"Result", "Grant", "Request"}
    assert payload["outgoing_flows"][0]["stages"][0]["label"] == "Request"
    assert payload["incoming_flows"] == []
    assert payload["node"]["policy_capabilities"]["policy_create"]["enabled"] is False


def test_control_node_inspector_root_exposes_policy_controls(app):
    client = app.test_client()

    response = client.get("/control/node-inspector", query_string={"scenario": "demo-web", "node_key": "root"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["node"]["key"] == "root"
    assert payload["node"]["policy_capabilities"]["policy_create"]["enabled"] is True
    assert payload["node"]["policy_defaults"]["from_role"] == "Edge"


def test_control_node_inspector_uses_visible_node_cards_while_topology_is_pending(app):
    client = app.test_client()
    FakeInfrastructureController.config["job"] = {
        "job_id": "job-2",
        "scenario": "demo-pending",
        "status": "running",
        "runner_command": "/tmp/.venv/bin/python scripts/run_topology.py --cloud 1 --fog 2 --edge 1 --endpoint 2 --scenario demo-pending",
        "log_lines": [
            "[topology] starting root chain attempt 1 on rpc=44001 p2p=30303",
            "[topology] starting root api on port 5600",
            "[topology] starting fog1 api on port 5002",
            "[topology] starting fog1 chain on rpc=8547 p2p=30304",
            "[topology] root chain is ready",
            "[topology] root api is ready",
        ],
        "topology_request": {
            "cloud": 1,
            "fog": 2,
            "edge": 1,
            "endpoint": 2,
            "endpoint_roles": ["Sensor", "Actuator"],
            "fog_devices": ["jetson-orin-nano-8gb", "jetson-xavier-nx-8gb"],
            "edge_devices": ["raspberry-pi-4-4gb"],
            "endpoint_devices": ["raspberry-pi-zero-2-w", "compute-module-4-2gb"],
            "host": "127.0.0.1",
        },
    }
    FakeInfrastructureController.config["details"] = {
        "selected_scenario": "demo-pending",
        "scenario": None,
    }

    response = client.get("/control/node-inspector", query_string={"scenario": "demo-pending", "node_key": "fog-1"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["selected_scenario"] == "demo-pending"
    assert payload["node"]["key"] == "fog-1"
    assert payload["node"]["name"] == "Fog1"
    assert payload["node"]["api_url"] == "http://127.0.0.1:5002"
    assert payload["node"]["rpc_url"] == "http://127.0.0.1:8547"
    assert payload["node"]["simulated_device"] == "Jetson Orin Nano 8GB"
    assert payload["node"]["summary_label"] in {"initializing", "API", "Chain", "Registration", "Ready"}
    assert isinstance(payload["node"]["target_options"], list)


def test_control_page_renders_node_inspector_controls(app):
    client = app.test_client()

    response = client.get("/control")
    topology_response = client.get("/topology")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    topology_body = topology_response.get_data(as_text=True)
    assert "Node Inspector" in body
    assert "Clear Selection" in body
    assert "Choose which topology version the control surface should target." in body
    assert "Open Topology Page" in body
    assert "Click a node card to open access control and policy actions" in body
    assert "Access Request" in body
    assert "Grant Lookup" in body
    assert "Create Policy" in body
    assert "Find Policy" in body
    assert "Access Control" in body
    assert "Policy Controls" in body
    assert topology_response.status_code == 200
    assert "Start Root" in topology_body
    assert "Spawn Fog" in topology_body
    assert "Spawn Edge" in topology_body
    assert "Spawn Endpoint" in topology_body
    assert "Stop Live Topology" in topology_body
    assert "Start Root first, then spawn Fog, Edge, and Endpoint nodes one at a time into the live topology." in topology_body
    assert "Shows the main topology runner command and live execution output." in topology_body
    assert "Click a node card to open its node inspector" in topology_body
    assert "IETF Constrained Endpoint Reference" in topology_body
    assert 'id="scenario-name"' not in topology_body
    assert "Topology Version" not in topology_body
    assert "Delete Current Topology" not in topology_body


def test_policy_routes_work_for_root(monkeypatch, tmp_path, service_module):
    FakeOrchestrator.reset(tmp_path)
    FakeInfrastructureController.reset()
    FakeOrchestrator.config["local_node_tier"] = "cloud"
    monkeypatch.setattr(service_module, "Orchestrator", FakeOrchestrator)
    monkeypatch.setattr(service_module, "InfrastructureController", FakeInfrastructureController)
    monkeypatch.setattr(service_module, "_local_signature_from_node_details", lambda _repo_root: "sig-local")
    selected_root = tmp_path / "runtime" / "generated" / "demo-web" / "root"
    selected_root.mkdir(parents=True, exist_ok=True)
    FakeInfrastructureController.config["details"]["scenario"]["root"] = {
        "directory": str(selected_root),
        "api_url": "http://127.0.0.1:5600",
        "rpc_url": "http://127.0.0.1:44001",
    }
    client = service_module.make_app(repo_root=str(tmp_path)).test_client()

    create_response = client.post("/policy/create", json={
        "scenario": "demo-web",
        "from_role": "Edge",
        "to_role": "Fog",
        "ops_csv": "READ",
        "ctx_schema": "api:GET:/temperature",
    })
    find_response = client.get("/policy/find", query_string={
        "scenario": "demo-web",
        "from_role": "Edge",
        "to_role": "Fog",
        "ops_csv": "READ",
        "ctx_schema": "api:GET:/temperature",
    })
    get_response = client.get("/policy/7", query_string={"scenario": "demo-web"})
    update_response = client.post("/policy/update", json={
        "scenario": "demo-web",
        "policy_id": 7,
        "ops_csv": "READ|WRITE",
        "ctx_schema": "api:GET:/temperature",
    })
    deprecate_response = client.post("/policy/deprecate", json={"scenario": "demo-web", "policy_id": 7})

    assert create_response.status_code == 200
    assert create_response.get_json()["policy_id"] == 7
    assert find_response.status_code == 200
    assert find_response.get_json()["policy_id"] == 7
    assert get_response.status_code == 200
    assert get_response.get_json()["policy"]["fromRole"] == "Edge"
    assert update_response.status_code == 200
    assert update_response.get_json()["policy_id"] == 7
    assert deprecate_response.status_code == 200
    assert deprecate_response.get_json()["policy_id"] == 7
    assert FakeOrchestrator.last_instance.repo_root == str(selected_root)
    assert FakeOrchestrator.last_instance.besu_rpc_url == "http://127.0.0.1:44001"
