import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_watch_node_helpers_and_commands(monkeypatch):
    watch_node = load_module("watch_node_mod", REPO_ROOT / "scripts" / "watch_node.py")

    assert "access" in watch_node.format_event({
        "ts_unix_ms": 1_700_000_000_000,
        "node_tier": "fog",
        "flow_type": "access",
        "stage": "request_received",
        "status": "started",
        "message": "Access request received",
    })

    def fake_get_json(_host, path, **_params):
        if path == "/events/recent":
            return {"events": [{
                "sequence": 1,
                "ts_unix_ms": 1_700_000_000_000,
                "node_tier": "fog",
                "flow_type": "access",
                "stage": "request_received",
                "status": "started",
                "message": "Access request received",
            }]}
        if path == "/events/flows":
            return {"flows": [{
                "flow_id": "access-1",
                "node_tier": "fog",
                "flow_type": "access",
                "final_status": "ok",
                "duration_ms": 25,
                "message": "Access granted",
            }]}
        if path == "/events/active":
            return {"flows": [{
                "flow_id": "registration-1",
                "node_tier": "edge",
                "flow_type": "registration",
                "last_status": "waiting",
                "last_stage": "peer_wait",
                "message": "Waiting for peers",
            }]}
        if path == "/events/stats":
            return {"stats": {
                "status_counts": {"ok": 2, "denied": 1},
                "flow_type_counts": {"access": 2},
                "top_reasons": [{"reason": "from_not_registered", "count": 1}],
            }}
        raise AssertionError(path)

    monkeypatch.setattr(watch_node, "http_get_json", fake_get_json)

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert watch_node.main.__call__
        watch_node.cmd_watch(type("Args", (), {"host": "http://x", "flow_type": None, "status": None, "json": False, "limit": 10, "follow": False, "interval": 0.1})())
    assert "Access request received" in buf.getvalue()

    buf = io.StringIO()
    with redirect_stdout(buf):
        watch_node.cmd_flows(type("Args", (), {"host": "http://x", "flow_type": None, "status": None, "json": False, "limit": 10})())
    assert "access-1" in buf.getvalue()

    buf = io.StringIO()
    with redirect_stdout(buf):
        watch_node.cmd_active(type("Args", (), {"host": "http://x", "flow_type": None, "status": None, "json": False})())
    assert "registration-1" in buf.getvalue()

    buf = io.StringIO()
    with redirect_stdout(buf):
        watch_node.cmd_stats(type("Args", (), {"host": "http://x", "json": False})())
    assert "from_not_registered" in buf.getvalue()


def test_watch_node_main_json_output(monkeypatch):
    watch_node = load_module("watch_node_main_mod", REPO_ROOT / "scripts" / "watch_node.py")

    monkeypatch.setattr(watch_node, "http_get_json", lambda *_args, **_kwargs: {
        "flows": [{
            "flow_id": "access-2",
            "node_tier": "fog",
            "flow_type": "access",
            "final_status": "ok",
            "duration_ms": 11,
            "message": "Access granted",
        }]
    })
    monkeypatch.setattr(sys, "argv", ["watch_node.py", "--json", "flows"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        watch_node.main()
    payload = json.loads(buf.getvalue().strip())
    assert payload["flow_id"] == "access-2"
