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


def test_make_node_specs_supports_per_endpoint_roles(tmp_path):
    topology = load_module()

    specs = topology.make_node_specs(
        fog_count=1,
        edge_count=1,
        endpoint_count=3,
        endpoint_role="Sensor",
        endpoint_roles=["Sensor", "Actuator", "Sensor"],
        scenario_name="demo",
        scenario_dir=tmp_path,
    )

    endpoint_specs = [spec for spec in specs if spec.tier == "endpoint"]
    assert [spec.node_type for spec in endpoint_specs] == ["Sensor", "Actuator", "Sensor"]
    assert endpoint_specs[1].name == "Actuator2"


def test_make_node_specs_applies_selected_device_presets(tmp_path):
    topology = load_module()

    specs = topology.make_node_specs(
        fog_count=1,
        edge_count=1,
        endpoint_count=1,
        endpoint_role="Sensor",
        fog_devices=["jetson-orin-nano-8gb"],
        edge_devices=["raspberry-pi-4-4gb"],
        endpoint_devices=["raspberry-pi-zero-2-w"],
        scenario_name="demo",
        scenario_dir=tmp_path,
    )

    fog_spec = next(spec for spec in specs if spec.tier == "fog")
    edge_spec = next(spec for spec in specs if spec.tier == "edge")
    endpoint_spec = next(spec for spec in specs if spec.tier == "endpoint")

    assert fog_spec.simulated_device == "jetson-orin-nano-8gb"
    assert fog_spec.device_profile["memory_mb"] == 8192
    assert edge_spec.simulated_device == "raspberry-pi-4-4gb"
    assert edge_spec.device_profile["chain_backed"] is True
    assert endpoint_spec.simulated_device == "raspberry-pi-zero-2-w"
    assert endpoint_spec.device_profile["chain_backed"] is False


def test_generate_docker_compose_includes_expected_services():
    topology = load_module()

    class Args:
        scenario = "demo-docker"
        fog = 1
        edge = 1
        endpoint = 1
        endpoint_role = "Sensor"

    compose = topology.generate_docker_compose(
        Args(),
        endpoint_roles=["Sensor"],
        fog_devices=["jetson-orin-nano-8gb"],
        edge_devices=["raspberry-pi-4-4gb"],
        endpoint_devices=["raspberry-pi-zero-2-w"],
    )

    assert "cloud" in compose["services"]
    assert "fog1" in compose["services"]
    assert "edge1" in compose["services"]
    assert "endpoint1" in compose["services"]
    assert compose["services"]["fog1"]["environment"]["SIMULATED_DEVICE_ID"] == "jetson-orin-nano-8gb"
    assert compose["services"]["endpoint1"]["environment"]["PARENT_URL"] == "http://fog1:5600"


def test_normalize_endpoint_roles_rejects_mismatched_count():
    topology = load_module()

    try:
        topology.normalize_endpoint_roles(endpoint_count=3, endpoint_role="Sensor", endpoint_roles=["Sensor", "Actuator"])
    except ValueError as exc:
        assert "endpoint count does not match" in str(exc)
    else:
        raise AssertionError("Expected mismatch to raise ValueError")


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


def test_ensure_root_node_details_writes_root_identity(monkeypatch, tmp_path):
    topology = load_module()
    root_dir = tmp_path / "root"
    data_dir = root_dir / "data"
    data_dir.mkdir(parents=True)

    private_key_hex = "0x" + ("22" * 32)
    private_key = topology.keys.PrivateKey(bytes.fromhex(private_key_hex[2:]))
    (data_dir / "key.priv").write_text(private_key_hex)
    (data_dir / "key.pub").write_text(private_key.public_key.to_hex())

    monkeypatch.setattr(topology, "derive_address", lambda _path: "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd")

    payload = topology.ensure_root_node_details(
        root_dir,
        rpc_url="http://127.0.0.1:45001",
        node_url="http://127.0.0.1:5601",
    )

    assert payload["node_id"] == "CLOUD01"
    assert payload["node_name"] == "Root Cloud"
    assert payload["node_type"] == "Cloud"
    assert payload["rpcURL"] == "http://127.0.0.1:45001"
    assert payload["node_url"] == "http://127.0.0.1:5601"
    assert payload["wants_validator"] is False
    assert payload["signature"]

    written = json.loads((root_dir / "node-details.json").read_text())
    assert written["signature"] == payload["signature"]


def test_parse_java_major_version_handles_openjdk_output():
    topology = load_module()

    version = topology.parse_java_major_version('openjdk version "17.0.15" 2025-04-15')

    assert version == 17


def test_start_service_and_chain_allows_reachable_api_when_health_is_still_unhealthy(monkeypatch, tmp_path):
    topology = load_module()

    class DummyProc:
        def __init__(self, pid):
            self.pid = pid

    launches = []

    def fake_launch_background(cmd, cwd, log_path, env):
        launches.append({"cmd": cmd, "cwd": cwd, "log_path": log_path, "env": env})
        return DummyProc(100 + len(launches))

    monkeypatch.setattr(topology, "launch_background", fake_launch_background)
    monkeypatch.setattr(topology, "http_reachable", lambda _url: True)
    monkeypatch.setattr(topology, "rpc_ready", lambda _url: True)
    monkeypatch.setattr(topology, "health_ready", lambda _url: False)
    monkeypatch.setattr(topology, "wait_for", lambda predicate, timeout, interval=1.0: predicate())
    monkeypatch.setattr(topology, "ensure_root_peer_link", lambda **_kwargs: True)

    spec = topology.NodeSpec(
        tier="fog",
        ordinal=1,
        node_type="Fog",
        name="Fog1",
        node_id="DEMO-FOG-001",
        signature_seed="seed",
        directory=str(tmp_path / "fog1"),
        api_port=5002,
        rpc_port=8547,
        p2p_port=30304,
    )
    node_dir = tmp_path / "fog1"
    node_dir.mkdir(parents=True)

    control_pid, api_pid, chain_pid = topology.start_service_and_chain(
        node_dir=node_dir,
        spec=spec,
        env={},
        logs_dir=tmp_path / "logs",
        root_rpc_url="http://127.0.0.1:8545",
        root_enode="enode://root@127.0.0.1:30303",
    )

    assert control_pid is None
    assert api_pid == 101
    assert chain_pid == 102
    assert len(launches) == 2


def test_start_service_and_chain_container_uses_docker_resource_limits(monkeypatch, tmp_path):
    topology = load_module()

    class DummyProc:
        def __init__(self, pid):
            self.pid = pid

    launches = []

    def fake_launch_background(cmd, cwd, log_path, env):
        launches.append({"cmd": cmd, "cwd": cwd, "log_path": log_path, "env": env})
        return DummyProc(100 + len(launches))

    monkeypatch.setattr(topology, "launch_background", fake_launch_background)
    monkeypatch.setattr(topology, "http_reachable", lambda _url: True)
    monkeypatch.setattr(topology, "rpc_ready", lambda _url: True)
    monkeypatch.setattr(topology, "health_ready", lambda _url: True)
    monkeypatch.setattr(topology, "wait_for", lambda predicate, timeout, interval=1.0: predicate())
    monkeypatch.setattr(topology, "ensure_root_peer_link", lambda **_kwargs: True)

    node_dir = tmp_path / "fog1"
    for rel in ("data", "static", "client_inbox"):
        (node_dir / rel).mkdir(parents=True, exist_ok=True)
        if rel != "data":
            (node_dir / rel / "enode.txt").write_text("enode://root@127.0.0.1:30303\n")
    (node_dir / "data" / "enode.txt").write_text("enode://root@127.0.0.1:30303\n")

    spec = topology.NodeSpec(
        tier="fog",
        ordinal=1,
        node_type="Fog",
        name="Fog1",
        node_id="DEMO-FOG-001",
        signature_seed="seed",
        directory=str(node_dir),
        api_port=5002,
        rpc_port=8547,
        p2p_port=30304,
        simulated_device="jetson-orin-nano-8gb",
        runtime_backend="container",
        device_profile={"vcpu": 6.0, "memory_mb": 8192, "network": {"delay_ms": 2, "jitter_ms": 1, "loss_percent": 0.0}},
    )

    control_pid, api_pid, chain_pid = topology.start_service_and_chain(
        node_dir=node_dir,
        spec=spec,
        env={"REAL_INTERACT": "1"},
        logs_dir=tmp_path / "logs",
        root_rpc_url="http://127.0.0.1:8545",
        root_enode="enode://root@127.0.0.1:30303",
        scenario_name="demo-container",
    )

    assert control_pid is None
    assert api_pid == 101
    assert chain_pid == 102
    assert launches[0]["cmd"][:3] == ["docker", "run", "--rm"]
    assert "--cpus" in launches[0]["cmd"]
    assert "6.0" in launches[0]["cmd"]
    assert "--memory" in launches[0]["cmd"]
    assert "8192m" in launches[0]["cmd"]
    assert "blockcap-node:local" in launches[0]["cmd"]


def test_ensure_root_peer_link_adds_peers_on_both_sides(monkeypatch):
    topology = load_module()

    calls = []
    peer_counts = {
        "http://root-rpc": 0,
        "http://fog-rpc": 0,
    }

    def fake_node_enode(url):
        assert url == "http://fog-rpc"
        return "enode://fog@127.0.0.1:30304"

    def fake_add_peer(rpc_url, peer_enode):
        calls.append((rpc_url, peer_enode))
        peer_counts["http://root-rpc"] = 1
        peer_counts["http://fog-rpc"] = 1
        return True

    monkeypatch.setattr(topology, "node_enode", fake_node_enode)
    monkeypatch.setattr(topology, "add_peer", fake_add_peer)
    monkeypatch.setattr(topology, "peer_count", lambda url: peer_counts[url])

    linked = topology.ensure_root_peer_link(
        root_rpc_url="http://root-rpc",
        root_enode="enode://root@127.0.0.1:30303",
        node_rpc_url="http://fog-rpc",
        node_label="fog1",
        timeout=1.0,
    )

    assert linked is True
    assert calls == [
        ("http://fog-rpc", "enode://root@127.0.0.1:30303"),
        ("http://root-rpc", "enode://fog@127.0.0.1:30304"),
    ]


def test_build_node_env_uses_local_rpc_for_fog_and_root_rpc_for_endpoint():
    topology = load_module()
    base_env = {"REAL_INTERACT": "1", "BESU_RPC_URL": "http://127.0.0.1:8545"}

    fog_spec = topology.NodeSpec(
        tier="fog",
        ordinal=1,
        node_type="Fog",
        name="Fog1",
        node_id="DEMO-FOG-001",
        signature_seed="seed",
        directory="/tmp/fog1",
        api_port=5002,
        rpc_port=8547,
        p2p_port=30304,
    )
    endpoint_spec = topology.NodeSpec(
        tier="endpoint",
        ordinal=1,
        node_type="Sensor",
        name="Sensor1",
        node_id="DEMO-END-001",
        signature_seed="seed",
        directory="/tmp/endpoint1",
        api_port=5006,
        rpc_port=None,
        p2p_port=None,
    )

    fog_env = topology.build_node_env(base_env, spec=fog_spec, root_rpc_url="http://127.0.0.1:44001")
    endpoint_env = topology.build_node_env(base_env, spec=endpoint_spec, root_rpc_url="http://127.0.0.1:44001")

    assert fog_env["BESU_RPC_URL"] == "http://127.0.0.1:8547"
    assert fog_env["FLASK_PORT"] == "5002"
    assert fog_env["P2P_PORT"] == "30304"
    assert endpoint_env["BESU_RPC_URL"] == "http://127.0.0.1:44001"
    assert endpoint_env["FLASK_PORT"] == "5006"


def test_ensure_root_contract_deployed_retries_transient_truffle_timeout(monkeypatch, tmp_path):
    topology = load_module()
    root_dir = tmp_path / "root"
    smart_dir = root_dir / "smart_contract_deployment" / "build" / "contracts"
    smart_dir.mkdir(parents=True, exist_ok=True)
    (root_dir / "prefunded_keys.json").write_text(json.dumps({
        "prefunded_accounts": [{"private_key": "0x" + ("11" * 32)}]
    }))
    (smart_dir / "NodeRegistry.json").write_text("{}")

    checks = {"count": 0}
    calls = []

    def fake_root_contract_is_deployed(_root_dir, _env):
        checks["count"] += 1
        return checks["count"] >= 2

    class Result:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd, cwd=None, env=None, capture_output=False, check=True):
        calls.append(cmd)
        if cmd[:3] == ["npx", "truffle", "migrate"]:
            if len([c for c in calls if c[:3] == ["npx", "truffle", "migrate"]]) == 1:
                return Result(1, "", "ESOCKETTIMEDOUT")
            return Result(0, "migration ok", "")
        return Result(0, "", "")

    sleeps = []
    monkeypatch.setattr(topology, "root_contract_is_deployed", fake_root_contract_is_deployed)
    monkeypatch.setattr(topology, "run", fake_run)
    monkeypatch.setattr(topology.time, "sleep", lambda seconds: sleeps.append(seconds))
    copies = []
    monkeypatch.setattr(topology.shutil, "copy2", lambda src, dst: copies.append((src, dst)))

    topology.ensure_root_contract_deployed(root_dir, "http://127.0.0.1:44001", {})

    migrate_calls = [cmd for cmd in calls if cmd[:3] == ["npx", "truffle", "migrate"]]
    assert len(migrate_calls) == 2
    assert 5 in sleeps
    assert copies


def test_initialize_root_scenario_writes_root_only_manifest(monkeypatch, tmp_path):
    topology = load_module()
    monkeypatch.setattr(topology, "GENERATED_ROOT", tmp_path)

    template_dir = tmp_path / "template-root"
    template_dir.mkdir(parents=True)

    def fake_prepare_root_dir(_template_dir, root_dir):
        root_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(topology, "prepare_root_dir", fake_prepare_root_dir)
    monkeypatch.setattr(
        topology,
        "ensure_root_started",
        lambda *_args, **_kwargs: {
            "api_url": "http://127.0.0.1:5600",
            "rpc_url": "http://127.0.0.1:44001",
            "p2p_port": 30303,
            "chain_pid": 111,
            "service_pid": 222,
            "node_details": {"node_id": "CLOUD01", "signature": "sig-root"},
        },
    )

    result = topology.initialize_root_scenario(
        scenario_name="demo-live",
        host="127.0.0.1",
        root_dir_template=str(template_dir),
    )

    manifest = json.loads((tmp_path / "demo-live" / "topology.json").read_text())
    assert result["scenario"] == "demo-live"
    assert manifest["root"]["api_url"] == "http://127.0.0.1:5600"
    assert manifest["root"]["runtime_backend"] == "native"
    assert manifest["nodes"] == []


def test_append_node_to_scenario_keeps_ordinals_unique_and_only_first_fog_is_validator(monkeypatch, tmp_path):
    topology = load_module()
    monkeypatch.setattr(topology, "GENERATED_ROOT", tmp_path)

    scenario_dir = tmp_path / "demo-live"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = scenario_dir / "topology.json"
    manifest_path.write_text(json.dumps({
        "scenario": "demo-live",
        "root": {
            "directory": str(scenario_dir / "root"),
            "api_url": "http://127.0.0.1:5600",
            "rpc_url": "http://127.0.0.1:44001",
        },
        "nodes": [],
    }))

    monkeypatch.setattr(topology, "ensure_container_runtime_prereqs", lambda: None)
    monkeypatch.setattr(topology, "ensure_container_image", lambda: None)
    monkeypatch.setattr(topology, "ensure_container_network", lambda _name: None)
    monkeypatch.setattr(topology, "validate_memory_budget", lambda _specs: None)
    monkeypatch.setattr(topology, "json_rpc", lambda *_args, **_kwargs: {"enode": "enode://root@127.0.0.1:30303"})
    monkeypatch.setattr(topology, "build_node_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(topology, "start_service_and_chain", lambda **kwargs: (900 + kwargs["spec"].ordinal, 1000 + kwargs["spec"].ordinal, 2000 + kwargs["spec"].ordinal))
    monkeypatch.setattr(topology, "post_registration", lambda *_args, **_kwargs: {"ok": True, "status": "validator_proposed"})

    prepared_specs = []

    def fake_prepare_node(spec, *_args, **_kwargs):
        prepared_specs.append(spec)
        node_dir = Path(spec.directory)
        node_dir.mkdir(parents=True, exist_ok=True)
        return node_dir, {"signature": f"sig-{spec.tier}-{spec.ordinal}"}

    monkeypatch.setattr(topology, "prepare_node", fake_prepare_node)

    first = topology.append_node_to_scenario(
        scenario_name="demo-live",
        tier="fog",
        device_id="jetson-orin-nano-8gb",
        host="127.0.0.1",
    )

    manifest = json.loads(manifest_path.read_text())
    manifest["nodes"][0]["lifecycle_status"] = "retired"
    manifest["nodes"][0]["retired_at_ms"] = 123
    manifest_path.write_text(json.dumps(manifest))

    second = topology.append_node_to_scenario(
        scenario_name="demo-live",
        tier="fog",
        device_id="jetson-xavier-nx-8gb",
        host="127.0.0.1",
    )

    updated_manifest = json.loads(manifest_path.read_text())
    assert first["ordinal"] == 1
    assert second["ordinal"] == 2
    assert prepared_specs[0].wants_validator is True
    assert prepared_specs[1].wants_validator is False
    assert updated_manifest["nodes"][0]["lifecycle_status"] == "retired"
    assert updated_manifest["nodes"][1]["lifecycle_status"] == "active"
