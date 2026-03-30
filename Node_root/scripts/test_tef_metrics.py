import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tef_metrics import LatencyRecorder, TokenBucketRateLimiter, ensure_results_dir, find_repo_root


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
