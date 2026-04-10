import importlib

import pytest

from acknowledgement import ALLOWED_ACK_ROLES
from test_orchestration_service import FakeInfrastructureController, FakeOrchestrator


class RegistrationFakeOrchestrator(FakeOrchestrator):
    @classmethod
    def reset(cls, tmp_path):
        super().reset(tmp_path)
        cls.config["registration_flow_result"] = {}
        cls.config["registration_flow_calls"] = []
        cls.config["ack_targets"] = []

    def registration_flow(self, req):
        cfg = type(self).config
        payload = dict(req)
        cfg["registration_flow_calls"].append(payload)
        node_type = str(payload.get("node_type") or "")
        ack_required = node_type in ALLOWED_ACK_ROLES
        if ack_required:
            cfg["ack_targets"].append(payload.get("rpcURL"))
        result = dict(cfg.get("registration_flow_result") or {})
        result.setdefault("ok", True)
        result.setdefault("status", "endpoint_registered" if node_type in {"Sensor", "Actuator"} else "registered")
        result.setdefault("ack_required", ack_required)
        result.setdefault("ack_status", "queued" if ack_required else "not_needed")
        result.setdefault("ack_sent", False)
        result.setdefault("tx", "0xreg")
        return result


@pytest.fixture
def service_module():
    return importlib.import_module("orchestration_service")


def _make_client(monkeypatch, tmp_path, service_module, *, node_role):
    RegistrationFakeOrchestrator.reset(tmp_path)
    FakeInfrastructureController.reset()
    monkeypatch.setattr(service_module, "Orchestrator", RegistrationFakeOrchestrator)
    monkeypatch.setattr(service_module, "InfrastructureController", FakeInfrastructureController)
    monkeypatch.setattr(service_module, "_local_signature_from_node_details", lambda _repo_root: "sig-local")
    app = service_module.make_app(repo_root=str(tmp_path), node_role=node_role)
    return app.test_client()


def _payload(node_type, **overrides):
    payload = {
        "node_id": "NODE-1",
        "node_name": f"{node_type}Node",
        "node_type": node_type,
        "public_key": "pk-1",
        "address": "0x1111111111111111111111111111111111111111",
        "rpcURL": f"http://{node_type.lower()}:5600",
        "signature": f"sig-{node_type.lower()}-1",
    }
    payload.update(overrides)
    return payload


def test_root_registers_fog(monkeypatch, tmp_path, service_module):
    client = _make_client(monkeypatch, tmp_path, service_module, node_role="cloud")
    req = _payload("Fog", node_id="FG-1", node_name="FogOne", rpcURL="http://fog:5600", signature="sig-fog")

    response = client.post("/register-node", json=req)

    assert response.status_code == 200
    body = response.get_json()
    assert "status" in body
    assert len(RegistrationFakeOrchestrator.config["registration_flow_calls"]) == 1
    call = RegistrationFakeOrchestrator.config["registration_flow_calls"][0]
    for key, value in req.items():
        assert call[key] == value
    assert RegistrationFakeOrchestrator.config["ack_targets"] == ["http://fog:5600"]


def test_fog_registers_edge(monkeypatch, tmp_path, service_module):
    client = _make_client(monkeypatch, tmp_path, service_module, node_role="fog")
    req = _payload("Edge", node_id="ED-1", node_name="EdgeOne", rpcURL="http://edge:5600", signature="sig-edge")

    response = client.post("/register-node", json=req)

    assert response.status_code == 200
    assert len(RegistrationFakeOrchestrator.config["registration_flow_calls"]) == 1
    assert RegistrationFakeOrchestrator.config["ack_targets"] == ["http://edge:5600"]
    assert RegistrationFakeOrchestrator.last_instance.listener_started is False


def test_edge_registers_sensor(monkeypatch, tmp_path, service_module):
    client = _make_client(monkeypatch, tmp_path, service_module, node_role="edge")
    req = _payload("Sensor", node_id="SN-1", node_name="SensorOne", rpcURL="http://sensor:5600", signature="sig-sensor")

    response = client.post("/register-node", json=req)

    assert response.status_code == 200
    body = response.get_json()
    assert body["ack_required"] is False
    assert len(RegistrationFakeOrchestrator.config["registration_flow_calls"]) == 1
    assert RegistrationFakeOrchestrator.config["ack_targets"] == []
    assert RegistrationFakeOrchestrator.last_instance.listener_started is False


def test_sequential_topology_registration_chain(monkeypatch, tmp_path, service_module):
    cloud_client = _make_client(monkeypatch, tmp_path / "cloud", service_module, node_role="cloud")
    fog_response = cloud_client.post("/register-node", json=_payload("Fog", node_id="FG-1", rpcURL="http://fog:5600", signature="sig-fg-1"))
    fog_ack_targets = list(RegistrationFakeOrchestrator.config["ack_targets"])

    fog_client = _make_client(monkeypatch, tmp_path / "fog", service_module, node_role="fog")
    edge_response = fog_client.post("/register-node", json=_payload("Edge", node_id="ED-1", rpcURL="http://edge:5600", signature="sig-ed-1"))
    edge_ack_targets = list(RegistrationFakeOrchestrator.config["ack_targets"])

    edge_client = _make_client(monkeypatch, tmp_path / "edge", service_module, node_role="edge")
    sensor_response = edge_client.post("/register-node", json=_payload("Sensor", node_id="SN-1", rpcURL="http://sensor:5600", signature="sig-sn-1"))
    sensor_body = sensor_response.get_json()
    sensor_ack_targets = list(RegistrationFakeOrchestrator.config["ack_targets"])

    assert fog_response.status_code == 200
    assert edge_response.status_code == 200
    assert sensor_response.status_code == 200
    assert fog_ack_targets == ["http://fog:5600"]
    assert edge_ack_targets == ["http://edge:5600"]
    assert sensor_ack_targets == []
    assert sensor_body["ack_required"] is False


def test_duplicate_signature_returns_conflict(monkeypatch, tmp_path, service_module):
    client = _make_client(monkeypatch, tmp_path, service_module, node_role="cloud")
    RegistrationFakeOrchestrator.config["registered_signatures"] = {"sig-fog-dup"}

    response = client.post("/register-node", json=_payload("Fog", signature="sig-fog-dup"))

    assert response.status_code == 409
    assert "Already Registered" in response.get_json()["error"]
    assert RegistrationFakeOrchestrator.config["registration_flow_calls"] == []


def test_duplicate_node_id_returns_conflict(monkeypatch, tmp_path, service_module):
    client = _make_client(monkeypatch, tmp_path, service_module, node_role="cloud")
    RegistrationFakeOrchestrator.config["duplicate_node_id"] = True

    response = client.post("/register-node", json=_payload("Fog", node_id="FG-DUP"))

    assert response.status_code == 409
    assert "duplicate_node_id" in response.get_json()["error"]
    assert RegistrationFakeOrchestrator.config["registration_flow_calls"] == []


def test_bad_signature_returns_forbidden(monkeypatch, tmp_path, service_module):
    client = _make_client(monkeypatch, tmp_path, service_module, node_role="cloud")
    RegistrationFakeOrchestrator.config["verify_registration_sig"] = False

    response = client.post("/register-node", json=_payload("Fog", signature="sig-bad"))

    assert response.status_code == 403
    assert "bad_registration_sig" in response.get_json()["error"]
    assert RegistrationFakeOrchestrator.config["registration_flow_calls"] == []


def test_tier_mismatch_blocks_edge_registering_fog(monkeypatch, tmp_path, service_module):
    client = _make_client(monkeypatch, tmp_path, service_module, node_role="edge")

    response = client.post("/register-node", json=_payload("Fog", node_id="FG-UP", signature="sig-fg-up"))

    assert response.status_code == 403
    assert "tier_mismatch" in response.get_json()["error"]
    assert RegistrationFakeOrchestrator.config["registration_flow_calls"] == []


def test_same_tier_fog_registers_fog(monkeypatch, tmp_path, service_module):
    client = _make_client(monkeypatch, tmp_path, service_module, node_role="fog")

    response = client.post("/register-node", json=_payload("Fog", node_id="FG-PEER", signature="sig-fg-peer"))

    assert response.status_code == 200
    assert len(RegistrationFakeOrchestrator.config["registration_flow_calls"]) == 1


def test_same_tier_edge_registers_edge(monkeypatch, tmp_path, service_module):
    client = _make_client(monkeypatch, tmp_path, service_module, node_role="edge")

    response = client.post("/register-node", json=_payload("Edge", node_id="ED-PEER", signature="sig-ed-peer"))

    assert response.status_code == 200
    assert len(RegistrationFakeOrchestrator.config["registration_flow_calls"]) == 1


def test_sensor_registrar_role_is_rejected(monkeypatch, tmp_path, service_module):
    client = _make_client(monkeypatch, tmp_path, service_module, node_role="endpoint")

    response = client.post("/register-node", json=_payload("Fog", node_id="FG-ENDPOINT", signature="sig-fg-endpoint"))

    assert response.status_code == 403
    assert response.get_json()["error"] == "registrar_role_forbidden"
    assert RegistrationFakeOrchestrator.config["registration_flow_calls"] == []
