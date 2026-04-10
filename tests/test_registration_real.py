"""
Real-world integration tests for BlockCap registration.
All nodes are running in Docker containers with live Besu chains.
No mocking. Tests hit actual HTTP endpoints and verify on-chain state.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import requests
from eth_keys import keys as eth_keys

REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_ROOT = REPO_ROOT / "Node_root"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(NODE_ROOT) not in sys.path:
    sys.path.insert(0, str(NODE_ROOT))

from scripts.run_topology import build_identity_signature, derive_address


ROOT_URL = os.getenv("ROOT_URL", "").rstrip("/")
FOG_URL = os.getenv("FOG_URL", "").rstrip("/")
EDGE_URL = os.getenv("EDGE_URL", "").rstrip("/")
SENSOR_URL = os.getenv("SENSOR_URL", "").rstrip("/")
ROOT_RPC = os.getenv("ROOT_RPC", "").rstrip("/")
FOG_DATA_DIR = Path(os.environ.get("FOG_DATA_DIR", "/fixtures/fog-data"))
EDGE_DATA_DIR = Path(os.environ.get("EDGE_DATA_DIR", "/fixtures/edge-data"))
SENSOR_DATA_DIR = Path(os.environ.get("SENSOR_DATA_DIR", "/fixtures/sensor-data"))
ACK_HOST = os.environ.get("ACK_HOST", "test-runner")
LIVE_ENV_READY = all((ROOT_URL, FOG_URL, EDGE_URL, SENSOR_URL, ROOT_RPC))

pytestmark = pytest.mark.skipif(not LIVE_ENV_READY, reason="live Docker registration environment is not configured")


def wait_until(fn, timeout_sec: float = 30.0, poll_sec: float = 1.0, label: str = "condition"):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(poll_sec)
    raise TimeoutError(f"Timed out waiting for: {label}")


def http_get_json(url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def register(url: str, payload: dict[str, Any], timeout: int = 30) -> requests.Response:
    return requests.post(f"{url}/register-node", json=payload, timeout=timeout)


def query_chain(method: str, params: list[Any] | None = None) -> dict[str, Any]:
    response = requests.post(
        ROOT_RPC,
        json={"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def get_validators() -> list[str]:
    result = query_chain("qbft_getValidatorsByBlockNumber", ["latest"])
    return [str(value).lower() for value in (result.get("result") or [])]


def get_node(signature: str, *, timeout: float = 10.0) -> requests.Response:
    return requests.get(f"{ROOT_URL}/node/{signature}", timeout=timeout)


def node_registered_on_chain(signature: str) -> bool:
    response = get_node(signature)
    return response.status_code == 200


def wait_for_node_registered(signature: str, *, timeout_sec: float = 30.0):
    wait_until(
        lambda: get_node(signature, timeout=5).status_code == 200,
        timeout_sec=timeout_sec,
        poll_sec=1.0,
        label=f"node {signature} registration",
    )


def assert_node_not_registered(signature: str, *, delay_sec: float = 5.0):
    time.sleep(delay_sec)
    response = get_node(signature, timeout=5)
    assert response.status_code == 404


def wait_for_validator(address: str, *, timeout_sec: float = 60.0):
    address_lc = address.lower()
    wait_until(
        lambda: address_lc in get_validators(),
        timeout_sec=timeout_sec,
        poll_sec=1.0,
        label=f"validator {address_lc}",
    )


def read_node_details(data_dir: Path) -> dict[str, Any]:
    details_path = data_dir / "node-details.json"
    wait_until(details_path.exists, timeout_sec=30, poll_sec=1.0, label=str(details_path))
    return json.loads(details_path.read_text())


def service_healthy(url: str) -> bool:
    try:
        response = requests.get(f"{url}/health", timeout=5)
        return response.status_code == 200
    except requests.RequestException:
        return False


@dataclass
class AckStub:
    base_url: str
    _hits: list[dict[str, Any]]
    _lock: threading.Lock
    _server: ThreadingHTTPServer
    _thread: threading.Thread

    def hit_count(self) -> int:
        with self._lock:
            return len(self._hits)

    def wait_for_hits(self, minimum_count: int, *, timeout_sec: float = 10.0):
        wait_until(
            lambda: self.hit_count() >= minimum_count,
            timeout_sec=timeout_sec,
            poll_sec=0.2,
            label=f"ack hit count >= {minimum_count}",
        )

    def shutdown(self):
        self._server.shutdown()
        self._thread.join(timeout=5)


class _AckHandler(BaseHTTPRequestHandler):
    hits: list[dict[str, Any]] = []
    lock = threading.Lock()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            payload = {"raw": raw.decode("utf-8", errors="replace")}
        with self.lock:
            self.hits.append({"path": self.path, "headers": dict(self.headers), "json": payload})
        body = json.dumps({"ok": True, "status": "bootstrap_ready"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args, **_kwargs):
        return


def start_ack_stub() -> AckStub:
    server = ThreadingHTTPServer(("0.0.0.0", 0), _AckHandler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="ack-stub")
    thread.start()
    return AckStub(
        base_url=f"http://{ACK_HOST}:{port}",
        _hits=_AckHandler.hits,
        _lock=_AckHandler.lock,
        _server=server,
        _thread=thread,
    )


def make_identity(
    node_type: str,
    *,
    node_id: str | None = None,
    node_name: str | None = None,
    wants_validator: bool = False,
    node_url: str | None = None,
    rpc_url: str = "http://127.0.0.1:8545",
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="blockcap-live-") as temp_dir:
        temp_path = Path(temp_dir)
        private_key = eth_keys.PrivateKey(secrets.token_bytes(32))
        private_key_path = temp_path / "key.priv"
        private_key_path.write_text(private_key.to_hex())
        public_key = private_key.public_key.to_hex()
        node_id_value = node_id or f"{node_type.upper()}-TEST-{secrets.token_hex(4).upper()}"
        node_name_value = node_name or f"Test {node_type}"
        payload = {
            "node_id": node_id_value,
            "node_name": node_name_value,
            "node_type": node_type,
            "public_key": public_key,
            "address": derive_address(private_key_path),
            "rpcURL": rpc_url,
            "signature": build_identity_signature(
                node_id_value,
                node_name_value,
                node_type,
                public_key,
                private_key_path,
            ),
        }
        if wants_validator:
            payload["wants_validator"] = True
        if node_url:
            payload["node_url"] = node_url
        return payload


@pytest.fixture(scope="session", autouse=True)
def ensure_services_ready():
    for url, label in (
        (ROOT_URL, "root"),
        (FOG_URL, "fog"),
        (EDGE_URL, "edge"),
        (SENSOR_URL, "sensor"),
    ):
        wait_until(lambda current=url: service_healthy(current), timeout_sec=180, poll_sec=2, label=f"{label} health")


@pytest.fixture(scope="session")
def startup_nodes() -> dict[str, dict[str, Any]]:
    return {
        "fog": read_node_details(FOG_DATA_DIR),
        "edge": read_node_details(EDGE_DATA_DIR),
        "sensor": read_node_details(SENSOR_DATA_DIR),
    }


@pytest.fixture(scope="session")
def ack_stub() -> AckStub:
    stub = start_ack_stub()
    yield stub
    stub.shutdown()


@pytest.fixture(scope="session")
def promoted_fog_result(ack_stub: AckStub) -> dict[str, Any]:
    baseline_validators = get_validators()
    baseline_hits = ack_stub.hit_count()
    payload = make_identity(
        "Fog",
        wants_validator=True,
        node_url=ack_stub.base_url,
        node_name="Promoted Fog",
    )
    started_at = time.time()
    response = register(ROOT_URL, payload)
    http_elapsed = time.time() - started_at
    assert response.status_code == 200, response.text
    confirmation_started = time.time()
    wait_for_node_registered(payload["signature"], timeout_sec=10)
    confirmation_elapsed = time.time() - confirmation_started
    promotion_started = time.time()
    wait_for_validator(payload["address"], timeout_sec=30)
    promotion_elapsed = time.time() - promotion_started
    ack_stub.wait_for_hits(baseline_hits + 1, timeout_sec=10)
    return {
        "payload": payload,
        "response": response.json(),
        "validators_before": baseline_validators,
        "validators_after": get_validators(),
        "http_elapsed": http_elapsed,
        "confirmation_elapsed": confirmation_elapsed,
        "promotion_elapsed": promotion_elapsed,
    }


class TestTopologyStartup:
    def test_A1_root_is_healthy(self):
        assert requests.get(f"{ROOT_URL}/health", timeout=10).status_code == 200

    def test_A2_fog_is_healthy(self):
        assert requests.get(f"{FOG_URL}/health", timeout=10).status_code == 200

    def test_A3_edge_is_healthy(self):
        assert requests.get(f"{EDGE_URL}/health", timeout=10).status_code == 200

    def test_A4_sensor_is_healthy(self):
        assert requests.get(f"{SENSOR_URL}/health", timeout=10).status_code == 200

    def test_A5_fog_is_registered_on_chain(self, startup_nodes):
        response = get_node(startup_nodes["fog"]["signature"])
        assert response.status_code == 200

    def test_A6_edge_is_registered_on_chain(self, startup_nodes):
        response = get_node(startup_nodes["edge"]["signature"])
        assert response.status_code == 200

    def test_A7_sensor_is_registered_on_chain(self, startup_nodes):
        response = get_node(startup_nodes["sensor"]["signature"])
        assert response.status_code == 200

    def test_A8_fog_is_validator(self, startup_nodes):
        wait_until(
            lambda: startup_nodes["fog"]["address"].lower() in get_validators(),
            timeout_sec=60,
            poll_sec=1,
            label="startup fog validator",
        )
        validators = get_validators()
        assert len(validators) >= 2
        assert startup_nodes["fog"]["address"].lower() in validators

    def test_A9_edge_is_not_validator(self, startup_nodes):
        assert startup_nodes["edge"]["address"].lower() not in get_validators()

    def test_A10_sensor_is_not_validator(self, startup_nodes):
        assert startup_nodes["sensor"]["address"].lower() not in get_validators()


class TestHappyPathRegistration:
    def test_B1_root_registers_new_fog(self, ack_stub: AckStub):
        payload = make_identity("Fog", node_url=ack_stub.base_url)
        baseline_hits = ack_stub.hit_count()
        response = register(ROOT_URL, payload)
        assert response.status_code == 200
        body = response.json()
        assert body.get("ok") is True
        ack_stub.wait_for_hits(baseline_hits + 1, timeout_sec=10)

    def test_B2_new_fog_appears_on_chain(self, ack_stub: AckStub):
        payload = make_identity("Fog", node_url=ack_stub.base_url)
        baseline_hits = ack_stub.hit_count()
        response = register(ROOT_URL, payload)
        assert response.status_code == 200
        wait_for_node_registered(payload["signature"], timeout_sec=30)
        ack_stub.wait_for_hits(baseline_hits + 1, timeout_sec=10)

    def test_B3_fog_registers_new_edge(self, ack_stub: AckStub):
        payload = make_identity("Edge", node_url=ack_stub.base_url)
        baseline_hits = ack_stub.hit_count()
        response = register(FOG_URL, payload)
        assert response.status_code == 200
        assert response.json().get("ok") is True
        ack_stub.wait_for_hits(baseline_hits + 1, timeout_sec=10)

    def test_B4_edge_registers_new_edge(self, ack_stub: AckStub):
        payload = make_identity("Edge", node_url=ack_stub.base_url)
        baseline_hits = ack_stub.hit_count()
        response = register(EDGE_URL, payload)
        assert response.status_code == 200
        assert response.json().get("ok") is True
        ack_stub.wait_for_hits(baseline_hits + 1, timeout_sec=10)

    def test_B5_fog_registers_fog(self, ack_stub: AckStub):
        payload = make_identity("Fog", node_url=ack_stub.base_url)
        baseline_hits = ack_stub.hit_count()
        response = register(FOG_URL, payload)
        assert response.status_code == 200
        assert response.json().get("ok") is True
        ack_stub.wait_for_hits(baseline_hits + 1, timeout_sec=10)

    def test_B6_edge_registers_sensor(self):
        payload = make_identity("Sensor")
        response = register(EDGE_URL, payload)
        assert response.status_code == 200
        assert response.json().get("ok") is True
        wait_for_node_registered(payload["signature"], timeout_sec=30)

    def test_B7_sensor_has_no_besu_rpc(self):
        response = requests.get(f"{SENSOR_URL}/health", timeout=10)
        assert response.status_code == 200
        with pytest.raises(requests.RequestException):
            requests.post(
                "http://sensor:8545",
                json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
                timeout=3,
            )


class TestTierMismatch:
    def test_C1_edge_cannot_register_fog(self):
        payload = make_identity("Fog")
        response = register(EDGE_URL, payload)
        assert response.status_code == 403
        assert "tier_mismatch" in response.json().get("error", "")

    def test_C1b_rejected_fog_not_registered_after_tier_mismatch(self):
        payload = make_identity("Fog")
        response = register(EDGE_URL, payload)
        assert response.status_code == 403
        assert_node_not_registered(payload["signature"])

    def test_C2_edge_cannot_register_cloud(self):
        payload = make_identity("Cloud")
        response = register(EDGE_URL, payload)
        assert response.status_code == 403
        assert "tier_mismatch" in response.json().get("error", "")

    def test_C3_fog_cannot_register_cloud(self):
        payload = make_identity("Cloud")
        response = register(FOG_URL, payload)
        assert response.status_code == 403
        assert "tier_mismatch" in response.json().get("error", "")

    def test_C4_sensor_cannot_register_anyone(self):
        payload = make_identity("Sensor")
        response = register(SENSOR_URL, payload, timeout=10)
        assert response.status_code == 403
        assert "registrar_role_forbidden" in response.json().get("error", "")


class TestDuplicateRegistration:
    def test_D1_duplicate_signature_rejected(self, ack_stub: AckStub):
        payload = make_identity("Fog", node_url=ack_stub.base_url)
        first = register(ROOT_URL, payload)
        assert first.status_code == 200
        second = register(ROOT_URL, payload)
        assert second.status_code == 409
        assert "already registered" in second.json().get("error", "").lower()

    def test_D2_duplicate_node_id_rejected(self, ack_stub: AckStub):
        fixed_id = f"DUPLICATE-{secrets.token_hex(4).upper()}"
        first_payload = make_identity("Fog", node_id=fixed_id, node_url=ack_stub.base_url)
        second_payload = make_identity("Fog", node_id=fixed_id, node_url=ack_stub.base_url)
        first = register(ROOT_URL, first_payload)
        assert first.status_code == 200
        second = register(ROOT_URL, second_payload)
        assert second.status_code == 409
        assert "duplicate_node_id" in second.json().get("error", "")


class TestBadRequests:
    def test_E1_missing_node_id(self):
        payload = make_identity("Fog")
        payload.pop("node_id", None)
        response = register(ROOT_URL, payload)
        assert response.status_code == 422

    def test_E2_missing_signature(self):
        payload = make_identity("Fog")
        payload.pop("signature", None)
        response = register(ROOT_URL, payload)
        assert response.status_code == 422

    def test_E3_missing_node_type(self):
        payload = make_identity("Fog")
        payload.pop("node_type", None)
        response = register(ROOT_URL, payload)
        assert response.status_code == 422

    def test_E4_empty_payload(self):
        response = register(ROOT_URL, {})
        assert response.status_code == 422

    def test_E5_malformed_non_json_body(self):
        response = requests.post(
            f"{ROOT_URL}/register-node",
            data="not-json",
            headers={"Content-Type": "text/plain"},
            timeout=10,
        )
        assert response.status_code == 415


class TestValidatorPromotion:
    def test_F1_new_fog_gets_promoted_to_validator(self, promoted_fog_result):
        payload = promoted_fog_result["payload"]
        validators_after = promoted_fog_result["validators_after"]
        assert payload["address"].lower() in validators_after
        assert len(validators_after) == len(promoted_fog_result["validators_before"]) + 1

    def test_F2_edge_not_promoted_to_validator(self, ack_stub: AckStub):
        validators_before = get_validators()
        payload = make_identity("Edge", node_url=ack_stub.base_url)
        baseline_hits = ack_stub.hit_count()
        response = register(FOG_URL, payload)
        assert response.status_code == 200
        ack_stub.wait_for_hits(baseline_hits + 1, timeout_sec=10)
        time.sleep(10)
        validators_after = get_validators()
        assert payload["address"].lower() not in validators_after
        assert len(validators_after) == len(validators_before)


class TestRegistrationLatency:
    def test_G1_fog_registration_completes_under_5s(self, ack_stub: AckStub):
        payload = make_identity("Fog", node_url=ack_stub.base_url)
        baseline_hits = ack_stub.hit_count()
        started_at = time.time()
        response = register(ROOT_URL, payload, timeout=30)
        elapsed = time.time() - started_at
        assert response.status_code == 200
        assert elapsed < 5.0, f"Registration took {elapsed:.2f}s"
        print(f"G1 HTTP registration latency: {elapsed:.2f}s")
        ack_stub.wait_for_hits(baseline_hits + 1, timeout_sec=10)

    def test_G2_fog_on_chain_confirmation_under_5s(self, ack_stub: AckStub):
        payload = make_identity("Fog", node_url=ack_stub.base_url)
        baseline_hits = ack_stub.hit_count()
        response = register(ROOT_URL, payload)
        assert response.status_code == 200
        started_at = time.time()
        wait_for_node_registered(payload["signature"], timeout_sec=10)
        elapsed = time.time() - started_at
        assert elapsed < 5.0, f"On-chain confirmation took {elapsed:.2f}s"
        print(f"G2 on-chain confirmation latency: {elapsed:.2f}s")
        ack_stub.wait_for_hits(baseline_hits + 1, timeout_sec=10)

    def test_G3_validator_promotion_under_30s(self, promoted_fog_result):
        elapsed = promoted_fog_result["promotion_elapsed"]
        assert elapsed < 30.0, f"Validator promotion took {elapsed:.2f}s"
        print(f"G3 validator promotion latency: {elapsed:.2f}s")
