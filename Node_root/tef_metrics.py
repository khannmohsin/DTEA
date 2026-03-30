import json
import os
import statistics
import threading
import time
from dataclasses import dataclass
from pathlib import Path


def find_repo_root(start_path: str) -> Path:
    current = Path(start_path).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def ensure_results_dir(start_path: str) -> Path:
    results_dir = find_repo_root(start_path) / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    return results_dir


@dataclass(frozen=True)
class LatencyKey:
    operation: str
    node_tier: str
    condition: str

    def as_string(self) -> str:
        return f"{self.operation}|{self.node_tier}|{self.condition}"


class LatencyRecorder:
    def __init__(self, results_dir: str | os.PathLike[str]):
        self._results_dir = Path(results_dir)
        self._results_dir.mkdir(parents=True, exist_ok=True)
        self._samples: dict[LatencyKey, list[float]] = {}
        self._lock = threading.RLock()

    def record(self, operation: str, node_tier: str, condition: str, latency_seconds: float) -> None:
        key = LatencyKey(operation=operation, node_tier=node_tier, condition=condition)
        with self._lock:
            self._samples.setdefault(key, []).append(float(latency_seconds))

    def has_samples(self, operation: str, node_tier: str) -> bool:
        with self._lock:
            return any(
                key.operation == operation and key.node_tier == node_tier and values
                for key, values in self._samples.items()
            )

    def summary(self) -> dict[str, dict[str, float | int]]:
        with self._lock:
            payload: dict[str, dict[str, float | int]] = {}
            for key, values in sorted(self._samples.items(), key=lambda item: item[0].as_string()):
                if not values:
                    continue
                payload[key.as_string()] = {
                    "operation": key.operation,
                    "node_tier": key.node_tier,
                    "condition": key.condition,
                    "mean_ms": round(statistics.fmean(values) * 1000, 3),
                    "stddev_ms": round((statistics.pstdev(values) if len(values) > 1 else 0.0) * 1000, 3),
                    "min_ms": round(min(values) * 1000, 3),
                    "max_ms": round(max(values) * 1000, 3),
                    "count": len(values),
                }
            return payload

    def write_summary(self) -> Path:
        output_path = self._results_dir / "latency.json"
        data = self.summary()
        tmp_path = output_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True))
        tmp_path.replace(output_path)
        return output_path


class TokenBucketRateLimiter:
    def __init__(self, default_rates: dict[str, float]):
        self._default_rates = {str(k): float(v) for k, v in default_rates.items()}
        self._buckets: dict[str, dict[str, float]] = {}
        self._lock = threading.RLock()

    def allow(self, bucket_key: str, role: str, now: float | None = None) -> tuple[bool, int]:
        ts = time.monotonic() if now is None else float(now)
        role_key = str(role or "endpoint").lower()
        rate = self._default_rates.get(role_key, self._default_rates.get("endpoint", 10.0))
        capacity = max(rate, 1.0)

        with self._lock:
            bucket = self._buckets.get(bucket_key)
            if bucket is None:
                bucket = {"tokens": capacity, "updated_at": ts}
                self._buckets[bucket_key] = bucket

            elapsed = max(0.0, ts - bucket["updated_at"])
            bucket["tokens"] = min(capacity, bucket["tokens"] + elapsed * rate)
            bucket["updated_at"] = ts

            if bucket["tokens"] >= 1.0:
                bucket["tokens"] -= 1.0
                return True, 0

            deficit = 1.0 - bucket["tokens"]
            retry_after_ms = int((deficit / rate) * 1000) + 1
            return False, retry_after_ms
