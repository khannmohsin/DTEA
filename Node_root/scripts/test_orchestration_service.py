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

    def __init__(self, repo_root=None, enforce_signature=True):
        cfg = type(self).config
        type(self).last_instance = self
        self.repo_root = repo_root
        self.enforce_signature = enforce_signature
        self.local_node_tier = cfg.get("local_node_tier", "fog")
        self.latency_recorder = DummyLatencyRecorder(
            cfg["latency_output_path"],
            cfg.get("latency_summary", {"registerNode|fog|cold": {"count": 1}}),
        )
        self.begin_calls = []
        self.end_calls = 0
        self.access_calls = []
        self.delegate_calls = []
        self.revoke_calls = []
        self.grant_lookup_calls = []
        self.expiry_calls = []
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
            "registration_flow_result": {"ok": True, "status": "registered", "ack_sent": True, "tx": "0xreg"},
            "access_flow_result": {"ok": True, "granted": True, "op": "READ", "policyId": 7, "ctx": "api:GET:/temperature"},
            "delegate_flow_result": {"ok": True, "granted": True, "tx": "0xdel"},
            "grant_lookup_result": {"policyId": 7, "isIssued": True},
            "grant_expired": False,
            "node_details": {},
            "validator": False,
            "deployed": True,
            "validators": "[0x1]",
        }
        cls.last_instance = None

    def begin_request(self, node_tier=None, condition=None):
        self.begin_calls.append({"node_tier": node_tier, "condition": condition})

    def end_request(self):
        self.end_calls += 1

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


class DenyLimiter:
    def __init__(self, _rates):
        pass

    def allow(self, _bucket_key, _role):
        return False, 250


@pytest.fixture
def service_module():
    return importlib.import_module("orchestration_service")


@pytest.fixture
def app(monkeypatch, tmp_path, service_module):
    FakeOrchestrator.reset(tmp_path)
    monkeypatch.setattr(service_module, "Orchestrator", FakeOrchestrator)
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
