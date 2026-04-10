import json
import sys
from collections import deque
from pathlib import Path

import pytest
from eth_keys import keys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infrastructure_control import InfrastructureController


def make_controller(tmp_path):
    (tmp_path / ".git").mkdir()
    return InfrastructureController(str(tmp_path))


def test_refresh_topology_updates_stale_manifest_pids(monkeypatch, tmp_path):
    ctl = make_controller(tmp_path)
    scenario = "demo-refresh"
    manifest = {
        "root": {
            "service_pid": 111,
            "chain_pid": 222,
            "api_url": "http://127.0.0.1:5600",
            "rpc_url": "http://127.0.0.1:8545",
        },
        "nodes": [
            {
                "tier": "fog",
                "ordinal": 1,
                "api_pid": 333,
                "chain_pid": 444,
                "api_url": "http://127.0.0.1:5002",
                "rpc_url": "http://127.0.0.1:8547",
            }
        ],
    }
    written = {}

    monkeypatch.setattr(ctl, "_read_manifest", lambda _path: json.loads(json.dumps(manifest)))
    monkeypatch.setattr(
        ctl,
        "_probe_statuses",
        lambda _manifest, _selected: {
            ("root", "api"): {"observed_pid": 911, "manifest_pid": 111, "status": "ok"},
            ("root", "chain"): {"observed_pid": 922, "manifest_pid": 222, "status": "ok"},
            ("fog-1", "api"): {"observed_pid": 933, "manifest_pid": 333, "status": "ok"},
            ("fog-1", "chain"): {"observed_pid": 944, "manifest_pid": 444, "status": "running"},
        },
    )
    monkeypatch.setattr(ctl, "_write_manifest", lambda _path, payload: written.update(payload))

    result = ctl.refresh_topology(scenario, persist=True)

    assert result["manifest_updated"] is True
    assert written["root"]["service_pid"] == 911
    assert written["root"]["chain_pid"] == 922
    assert written["nodes"][0]["api_pid"] == 933
    assert written["nodes"][0]["chain_pid"] == 944


def test_refresh_topology_clears_dead_manifest_pids(monkeypatch, tmp_path):
    ctl = make_controller(tmp_path)
    scenario = "demo-clear"
    manifest = {
        "root": {
            "service_pid": 111,
            "chain_pid": 222,
            "api_url": "http://127.0.0.1:5600",
            "rpc_url": "http://127.0.0.1:8545",
        },
        "nodes": [
            {
                "tier": "fog",
                "ordinal": 1,
                "api_pid": 333,
                "chain_pid": 444,
                "api_url": "http://127.0.0.1:5002",
                "rpc_url": "http://127.0.0.1:8547",
            }
        ],
    }
    written = {}

    monkeypatch.setattr(ctl, "_read_manifest", lambda _path: json.loads(json.dumps(manifest)))
    monkeypatch.setattr(
        ctl,
        "_probe_statuses",
        lambda _manifest, _selected: {
            ("root", "api"): {"observed_pid": None, "status": "down", "observed_running": False},
            ("root", "chain"): {"observed_pid": None, "status": "down", "observed_running": False},
            ("fog-1", "api"): {"observed_pid": None, "status": "down", "observed_running": False},
            ("fog-1", "chain"): {"observed_pid": None, "status": "down", "observed_running": False},
        },
    )
    monkeypatch.setattr(ctl, "_write_manifest", lambda _path, payload: written.update(payload))

    result = ctl.refresh_topology(scenario, persist=True)

    assert result["manifest_updated"] is True
    assert written["root"]["service_pid"] is None
    assert written["root"]["chain_pid"] is None
    assert written["nodes"][0]["api_pid"] is None
    assert written["nodes"][0]["chain_pid"] is None


def test_stop_topology_kills_scenario_processes_and_deletes_directory(monkeypatch, tmp_path):
    ctl = make_controller(tmp_path)
    scenario = "demo-stop"
    scenario_dir = tmp_path / "runtime" / "generated" / scenario
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "topology.json").write_text("{}")
    stopped = []

    monkeypatch.setattr(
        ctl,
        "scenario_details",
        lambda _scenario=None: {
            "selected_scenario": scenario,
            "scenario": {
                "scenario": scenario,
                "node_views": [
                    {
                        "key": "root",
                        "api_url": "http://127.0.0.1:5600",
                        "rpc_url": "http://127.0.0.1:8545",
                        "p2p_port": 30303,
                        "processes": {
                            "api": {"manifest_pid": 101, "observed_pid": 201},
                            "chain": {"manifest_pid": 102, "observed_pid": 202},
                            "registration": {},
                        },
                    },
                    {
                        "key": "fog-1",
                        "api_url": "http://127.0.0.1:5002",
                        "rpc_url": "http://127.0.0.1:8547",
                        "p2p_port": 30304,
                        "processes": {
                            "api": {"manifest_pid": 301, "observed_pid": 301},
                            "chain": {"manifest_pid": 302, "observed_pid": 302},
                            "registration": {},
                        },
                    },
                ],
            },
        },
    )
    monkeypatch.setattr(ctl, "_terminate_pid", lambda pid: stopped.append(pid))
    monkeypatch.setattr(ctl, "_pid_alive", lambda _pid: False)
    monkeypatch.setattr(ctl, "_listening_pids_for_port", lambda _port: [])

    result = ctl.stop_topology(scenario, delete_scenario=True)

    assert sorted(stopped) == [101, 102, 201, 202, 301, 302]
    assert result["remaining_live_pids"] == []
    assert 5600 in result["freed_ports"]
    assert 8545 in result["freed_ports"]
    assert result["deleted_scenario"] is True
    assert result["scenario_exists_after_stop"] is False
    assert not scenario_dir.exists()


def test_delete_topology_requires_no_running_processes(monkeypatch, tmp_path):
    ctl = make_controller(tmp_path)
    scenario = "demo-delete"
    scenario_dir = tmp_path / "runtime" / "generated" / scenario
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "topology.json").write_text("{}")

    monkeypatch.setattr(
        ctl,
        "scenario_details",
        lambda _scenario=None: {
            "selected_scenario": scenario,
            "scenario": {
                "scenario": scenario,
                "node_views": [
                    {
                        "key": "root",
                        "processes": {
                            "api": {"manifest_pid": 101, "observed_pid": 201},
                            "chain": {"manifest_pid": 102, "observed_pid": 202},
                            "registration": {},
                        },
                    },
                ],
            },
        },
    )
    monkeypatch.setattr(ctl, "_pid_alive", lambda pid: int(pid) == 201)

    with pytest.raises(RuntimeError, match="topology_has_running_processes"):
        ctl.delete_topology(scenario)

    monkeypatch.setattr(ctl, "_pid_alive", lambda _pid: False)
    result = ctl.delete_topology(scenario)
    assert result["deleted_scenario"] is True
    assert result["scenario_exists_after_delete"] is False


def test_delete_topology_allows_incomplete_scenario_when_no_processes_are_running(monkeypatch, tmp_path):
    ctl = make_controller(tmp_path)
    scenario = "demo-incomplete"
    scenario_dir = tmp_path / "runtime" / "generated" / scenario
    scenario_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        ctl,
        "scenario_details",
        lambda _scenario=None: {
            "selected_scenario": scenario,
            "scenario": None,
        },
    )
    monkeypatch.setattr(ctl, "_directory_process_pids", lambda _directory: [])

    result = ctl.delete_topology(scenario)

    assert result["deleted_scenario"] is True
    assert result["scenario_exists_after_delete"] is False
    assert not scenario_dir.exists()


def test_open_terminal_uses_osascript_and_selected_log(monkeypatch, tmp_path):
    ctl = make_controller(tmp_path)
    log_path = tmp_path / "runtime" / "generated" / "demo" / "logs" / "root-api.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("ready\n")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        ctl,
        "node_logs",
        lambda **_kwargs: {
            "selected_scenario": "demo",
            "node_key": "root",
            "process": "api",
            "path": str(log_path),
            "command": "python orchestration_service.py --port 5600",
        },
    )
    calls = []

    class DummyProc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, capture_output, text, check):
        calls.append(cmd)
        return DummyProc()

    monkeypatch.setattr("infrastructure_control.subprocess.run", fake_run)

    result = ctl.open_terminal(scenario="demo", node_key="root", process="api")

    assert result["opened"] is True
    assert calls[0][0] == "osascript"
    assert "tail -n 200 -F" in calls[0][2]
    assert "pretty_log_stream.py" in calls[0][2]


def test_scenario_details_backfills_root_signature_and_p2p(monkeypatch, tmp_path):
    ctl = make_controller(tmp_path)
    scenario = "demo-root-meta"
    scenario_dir = tmp_path / "runtime" / "generated" / scenario
    root_dir = scenario_dir / "root"
    data_dir = root_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    private_key_hex = "0x" + ("33" * 32)
    private_key = keys.PrivateKey(bytes.fromhex(private_key_hex[2:]))
    (data_dir / "key.priv").write_text(private_key_hex)
    (data_dir / "key.pub").write_text(private_key.public_key.to_hex())

    manifest = {
        "scenario": scenario,
        "root": {
            "directory": str(root_dir),
            "api_url": "http://127.0.0.1:5600",
            "rpc_url": "http://127.0.0.1:44001",
            "chain_pid": 101,
            "service_pid": 102,
            "node_details": {},
        },
        "nodes": [],
    }
    written = {}

    monkeypatch.setattr(ctl, "_read_manifest", lambda _path: json.loads(json.dumps(manifest)))
    monkeypatch.setattr(ctl, "refresh_topology", lambda _scenario, persist=True: {"selected_scenario": scenario})
    monkeypatch.setattr(
        ctl,
        "_probe_statuses",
        lambda _manifest, _selected: {
            ("root", "api"): {"status": "ok", "label": "api ready"},
            ("root", "chain"): {"status": "ok", "label": "chain ready"},
        },
    )
    monkeypatch.setattr(ctl, "_derive_address", lambda _path: "0x1234567890abcdef1234567890abcdef12345678")
    monkeypatch.setattr(ctl, "_rpc_node_info", lambda _url: {"ports": {"discovery": 30303}})
    monkeypatch.setattr(ctl, "_write_manifest", lambda _path, payload: written.update(payload))

    payload = ctl.scenario_details(scenario)

    root_view = payload["scenario"]["node_views"][0]
    assert root_view["signature"]
    assert root_view["p2p_port"] == 30303
    assert (root_dir / "node-details.json").exists()
    assert written["root"]["p2p_port"] == 30303
    assert written["root"]["node_details"]["signature"] == root_view["signature"]


def test_open_terminal_rejects_missing_log_path(monkeypatch, tmp_path):
    ctl = make_controller(tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        ctl,
        "node_logs",
        lambda **_kwargs: {
            "selected_scenario": "demo",
            "node_key": "root",
            "process": "api",
            "path": "",
            "command": "python orchestration_service.py --port 5600",
        },
    )

    with pytest.raises(RuntimeError, match="log_not_ready"):
        ctl.open_terminal(scenario="demo", node_key="root", process="api")


def test_node_logs_falls_back_to_running_job_logs_before_manifest_is_ready(tmp_path):
    ctl = make_controller(tmp_path)
    scenario = "demo-starting"
    scenario_dir = tmp_path / "runtime" / "generated" / scenario
    logs_dir = scenario_dir / "logs"
    node_dir = scenario_dir / "fog1"
    logs_dir.mkdir(parents=True, exist_ok=True)
    node_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "fog-1-besu.log").write_text("chain booting\n")
    (node_dir / ".env").write_text(
        "\n".join([
            "FLASK_PORT=5002",
            "BESU_PORT=8547",
            "P2P_PORT=30304",
            "ROOT_RPC_URL=http://127.0.0.1:44001",
        ])
    )
    ctl._current_job = {
        "scenario": scenario,
        "status": "running",
        "topology_request": {"host": "127.0.0.1"},
        "log_lines": deque(),
    }

    payload = ctl.node_logs(scenario=scenario, node_key="fog-1", process="chain", lines=20)

    assert payload["exists"] is True
    assert payload["path"].endswith("fog-1-besu.log")
    assert "client_blockchain_init.py" in payload["command"]
    assert payload["content"] == "chain booting"


def test_kill_all_spawned_processes_stops_runner_and_all_scenarios(monkeypatch, tmp_path):
    ctl = make_controller(tmp_path)
    ctl._current_job = {"runner_pid": 999, "status": "running", "log_lines": deque(), "scenario": "demo-a"}
    monkeypatch.setattr(ctl, "list_scenarios", lambda: [{"scenario": "demo-a"}, {"scenario": "demo-b"}])
    stop_calls = []

    def fake_stop_topology(scenario, delete_scenario=False):
        stop_calls.append((scenario, delete_scenario))
        if scenario == "demo-a":
            return {"scenario": scenario, "stopped_pids": [101, 102], "freed_ports": [5600], "remaining_live_pids": []}
        return {"scenario": scenario, "stopped_pids": [201, 202], "freed_ports": [5002, 8547], "remaining_live_pids": []}

    terminated = []
    monkeypatch.setattr(ctl, "stop_topology", fake_stop_topology)
    monkeypatch.setattr(ctl, "_terminate_pid", lambda pid: terminated.append(pid))

    result = ctl.kill_all_spawned_processes()

    assert terminated == [999]
    assert stop_calls == [("demo-a", False), ("demo-b", False)]
    assert result["runner_pid"] == 999
    assert sorted(result["stopped_pids"]) == [101, 102, 201, 202, 999]
    assert sorted(result["freed_ports"]) == [5002, 5600, 8547]
    assert result["remaining_live_pids"] == []


def test_default_scenario_prefers_running_and_more_complete_manifest(monkeypatch, tmp_path):
    ctl = make_controller(tmp_path)
    generated = tmp_path / "runtime" / "generated"
    older = generated / "demo-older"
    newer = generated / "demo-newer"
    older.mkdir(parents=True, exist_ok=True)
    newer.mkdir(parents=True, exist_ok=True)
    (older / "topology.json").write_text("{}")
    (newer / "topology.json").write_text("{}")

    manifests = {
        str(older / "topology.json"): {"root": {}, "nodes": [{}, {}, {}]},
        str(newer / "topology.json"): {"root": {}, "nodes": [{}]},
    }

    monkeypatch.setattr(
        ctl,
        "_read_manifest",
        lambda path: manifests.get(str(path)),
    )
    monkeypatch.setattr(
        ctl,
        "_scenario_summary",
        lambda scenario, manifest: {
            "scenario": scenario,
            "running": scenario == "demo-older",
            "alive_pids": [11, 12] if scenario == "demo-older" else [],
            "node_count": 3 if scenario == "demo-older" else 1,
        },
    )

    assert ctl._default_scenario() == "demo-older"


def test_default_scenario_returns_none_when_nothing_is_running(monkeypatch, tmp_path):
    ctl = make_controller(tmp_path)
    generated = tmp_path / "runtime" / "generated"
    a = generated / "demo-a"
    b = generated / "demo-b"
    a.mkdir(parents=True, exist_ok=True)
    b.mkdir(parents=True, exist_ok=True)
    (a / "topology.json").write_text("{}")
    (b / "topology.json").write_text("{}")

    manifests = {
        str(a / "topology.json"): {"root": {}, "nodes": [{}]},
        str(b / "topology.json"): {"root": {}, "nodes": [{}, {}]},
    }

    monkeypatch.setattr(ctl, "_read_manifest", lambda path: manifests.get(str(path)))
    monkeypatch.setattr(
        ctl,
        "_scenario_summary",
        lambda scenario, manifest: {
            "scenario": scenario,
            "running": False,
            "alive_pids": [],
            "node_count": len(manifest.get("nodes") or []),
        },
    )

    assert ctl._default_scenario() is None
