import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module():
    script_path = REPO_ROOT / "scripts" / "run_topology.py"
    spec = importlib.util.spec_from_file_location("run_topology_mod", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_make_node_specs_generates_requested_counts(tmp_path):
    topology = load_module()

    specs = topology.make_node_specs(
        fog_count=2,
        edge_count=1,
        endpoint_count=3,
        endpoint_role="Sensor",
        scenario_name="demo",
        scenario_dir=tmp_path,
    )

    assert [spec.tier for spec in specs].count("fog") == 2
    assert [spec.tier for spec in specs].count("edge") == 1
    assert [spec.tier for spec in specs].count("endpoint") == 3
    assert specs[0].api_port > 0
    assert specs[0].rpc_port > 0
    assert specs[1].api_port != specs[0].api_port
    assert specs[2].api_port > 0
    assert specs[-1].node_type == "Sensor"


def test_render_client_env_contains_expected_ports():
    topology = load_module()

    payload = topology.render_client_env(6101, 8601, 30401)

    assert "FLASK_PORT=6101" in payload
    assert "BESU_PORT=8601" in payload
    assert "P2P_PORT=30401" in payload
    assert "NODE_URL=http://127.0.0.1:6101" in payload


def test_allocate_free_port_returns_positive_integer():
    topology = load_module()

    port = topology.allocate_free_port()

    assert isinstance(port, int)
    assert port > 0


def test_build_registration_payload_writes_node_details(monkeypatch, tmp_path):
    topology = load_module()
    node_dir = tmp_path / "node1"
    data_dir = node_dir / "data"
    data_dir.mkdir(parents=True)

    private_key_hex = "0x" + ("11" * 32)
    private_key = topology.keys.PrivateKey(bytes.fromhex(private_key_hex[2:]))
    (data_dir / "key.priv").write_text(private_key_hex)
    (data_dir / "key.pub").write_text(private_key.public_key.to_hex())

    monkeypatch.setattr(topology, "derive_address", lambda _path: "0x1234567890abcdef1234567890abcdef12345678")

    payload = topology.build_registration_payload(
        node_dir=node_dir,
        node_id="DEMO-FOG-001",
        node_name="Fog1",
        node_type="Fog",
        rpc_url="http://127.0.0.1:8601",
        node_url="http://127.0.0.1:6101",
        wants_validator=True,
    )

    assert payload["node_id"] == "DEMO-FOG-001"
    assert payload["address"] == "0x1234567890abcdef1234567890abcdef12345678"
    assert payload["wants_validator"] is True

    written = json.loads((node_dir / "node-details.json").read_text())
    assert written["signature"] == payload["signature"]
    assert written["rpcURL"] == "http://127.0.0.1:8601"


def test_parse_java_major_version_handles_openjdk_output():
    topology = load_module()

    version = topology.parse_java_major_version('openjdk version "17.0.15" 2025-04-15')

    assert version == 17
