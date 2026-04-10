import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tef_metrics import (
    LatencyRecorder,
    ProcessEventRecorder,
    TokenBucketRateLimiter,
    UiResultsRecorder,
    ensure_results_dir,
    ensure_ui_runs_dir,
    find_repo_root,
)


def test_find_repo_root_walks_up_to_git_dir(tmp_path):
    repo_root = tmp_path / "repo"
    nested = repo_root / "a" / "b" / "c"
    (repo_root / ".git").mkdir(parents=True)
    nested.mkdir(parents=True)

    assert find_repo_root(str(nested)) == repo_root


def test_ensure_results_dir_creates_results_under_repo_root(tmp_path):
    repo_root = tmp_path / "repo"
    nested = repo_root / "service" / "runtime"
    (repo_root / ".git").mkdir(parents=True)
    nested.mkdir(parents=True)

    results_dir = ensure_results_dir(str(nested))

    assert results_dir == repo_root / "results"
    assert results_dir.exists()


def test_ensure_ui_runs_dir_creates_subdirectory(tmp_path):
    repo_root = tmp_path / "repo"
    nested = repo_root / "service"
    (repo_root / ".git").mkdir(parents=True)
    nested.mkdir(parents=True)

    runs_dir = ensure_ui_runs_dir(str(nested))

    assert runs_dir == repo_root / "results" / "ui_runs"
    assert runs_dir.exists()


def test_latency_recorder_summarizes_and_writes_json(tmp_path):
    recorder = LatencyRecorder(tmp_path)
    recorder.record("registerNode", "fog", "cold", 0.100)
    recorder.record("registerNode", "fog", "cold", 0.200)
    recorder.record("checkGrant", "edge", "warm", 0.050)

    summary = recorder.summary()

    assert recorder.has_samples("registerNode", "fog") is True
    assert summary["registerNode|fog|cold"]["count"] == 2
    assert summary["registerNode|fog|cold"]["mean_ms"] == 150.0
    assert summary["registerNode|fog|cold"]["min_ms"] == 100.0
    assert summary["registerNode|fog|cold"]["max_ms"] == 200.0
    assert summary["checkGrant|edge|warm"]["stddev_ms"] == 0.0

    output_path = recorder.write_summary()
    written = json.loads(output_path.read_text())
    assert written == summary

    output_path.write_text(json.dumps({"sentinel": True}))
    second_path = recorder.write_summary()
    assert second_path == output_path
    assert json.loads(output_path.read_text()) == {"sentinel": True}


def test_token_bucket_rate_limiter_enforces_retry_and_refill():
    limiter = TokenBucketRateLimiter({"fog": 2.0, "endpoint": 1.0})

    allowed, retry_after_ms = limiter.allow("sig-1", "fog", now=0.0)
    assert allowed is True
    assert retry_after_ms == 0

    allowed, retry_after_ms = limiter.allow("sig-1", "fog", now=0.0)
    assert allowed is True
    assert retry_after_ms == 0

    allowed, retry_after_ms = limiter.allow("sig-1", "fog", now=0.0)
    assert allowed is False
    assert retry_after_ms > 0

    allowed, retry_after_ms = limiter.allow("sig-1", "fog", now=0.5)
    assert allowed is True
    assert retry_after_ms == 0

    allowed, retry_after_ms = limiter.allow("sig-2", "unknown-role", now=1.0)
    assert allowed is True
    assert retry_after_ms == 0


def test_process_event_recorder_persists_and_summarizes_flows(tmp_path):
    recorder = ProcessEventRecorder(tmp_path, node_id="FG-1", node_name="Fog", node_tier="fog", max_events=10)

    started = recorder.emit(
        component="api",
        flow_type="access",
        flow_id="flow-1",
        stage="request_received",
        status="started",
        message="Access request received",
    )
    finished = recorder.emit(
        component="orchestrator",
        flow_type="access",
        flow_id="flow-1",
        stage="access_finished",
        status="ok",
        message="Access granted",
        duration_ms=12.5,
    )

    assert started["sequence"] == 1
    assert finished["sequence"] == 2
    assert recorder.latest_sequence() == 2
    assert recorder.recent(limit=5)[-1]["message"] == "Access granted"
    assert recorder.active_flows() == []
    flows = recorder.flows(limit=5)
    assert flows[0]["flow_id"] == "flow-1"
    assert flows[0]["final_status"] == "ok"
    assert flows[0]["duration_ms"] is not None
    stats = recorder.stats()
    assert stats["status_counts"]["ok"] == 1
    recorder.flush()
    lines = (tmp_path / "process_events.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2


def test_process_event_recorder_wait_for_events_returns_new_items(tmp_path):
    recorder = ProcessEventRecorder(tmp_path)
    recorder.emit(
        component="api",
        flow_type="registration",
        flow_id="flow-2",
        stage="request_received",
        status="started",
        message="Registration request received",
    )

    fresh = recorder.wait_for_events(after_sequence=0, timeout=0.1)
    assert len(fresh) == 1
    assert fresh[0]["flow_type"] == "registration"


def test_process_event_recorder_drops_disk_write_when_queue_is_full(tmp_path):
    recorder = ProcessEventRecorder(tmp_path, max_events=10)
    recorder._write_queue = type(recorder._write_queue)(maxsize=1)
    recorder._write_queue.put_nowait({"preloaded": True})

    event = recorder.emit(
        component="api",
        flow_type="access",
        flow_id="flow-drop",
        stage="request_received",
        status="started",
        message="queued event",
    )

    assert event["flow_id"] == "flow-drop"
    assert recorder.recent(limit=1)[0]["flow_id"] == "flow-drop"


def test_ui_results_recorder_persists_series_and_lists_runs(tmp_path):
    recorder = UiResultsRecorder(tmp_path)

    snapshot = recorder.record_snapshot(
        scenario="demo-web",
        event_stats={"total_events": 5, "status_counts": {"ok": 2, "denied": 1, "error": 1}},
        active_flows=[{"flow_id": "flow-1"}],
        latency_summary={
            "checkGrant|fog|cold": {
                "operation": "checkGrant",
                "node_tier": "fog",
                "condition": "cold",
                "mean_ms": 12.3,
                "count": 1,
            }
        },
        scenario_details={
            "scenario": {
                "node_views": [
                    {"summary_status": "ok"},
                    {"summary_status": "running"},
                ]
            }
        },
        research_sections={"gas_comparison": {"available": True, "rows": [{"operation": "Register"}]}},
        summary_cards=[{"label": "Scenario", "value": "demo-web"}],
    )

    assert snapshot is not None
    assert snapshot["granted"] == 2
    assert snapshot["node_status_counts"]["ok"] == 1
    assert snapshot["latency_points"][0]["operation"] == "checkGrant"

    series = recorder.series("demo-web")
    assert len(series) == 1
    assert recorder.available_runs()[0]["scenario"] == "demo-web"
    loaded = recorder.load_run("demo-web")
    assert loaded is not None
    assert loaded["snapshots"][0]["active_flows"] == 1
    assert loaded["research_sections"]["gas_comparison"]["rows"][0]["operation"] == "Register"
    assert loaded["summary_cards"][0]["label"] == "Scenario"
