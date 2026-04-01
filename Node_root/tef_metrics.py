import json
import os
import statistics
import threading
import time
import uuid
from collections import Counter, deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


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


@dataclass(frozen=True)
class ProcessEvent:
    sequence: int
    event_id: str
    ts_unix_ms: int
    node_id: str
    node_name: str
    node_tier: str
    component: str
    flow_type: str
    flow_id: str
    stage: str
    status: str
    message: str
    details: dict[str, Any]
    duration_ms: float | None = None
    request_id: str | None = None
    tx_hash: str | None = None
    policy_id: int | None = None
    from_signature: str | None = None
    to_signature: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProcessEventRecorder:
    TERMINAL_STATUSES = {"ok", "denied", "error"}

    def __init__(
        self,
        results_dir: str | os.PathLike[str],
        *,
        node_id: str = "",
        node_name: str = "",
        node_tier: str = "unknown",
        max_events: int = 1000,
    ):
        self._results_dir = Path(results_dir)
        self._results_dir.mkdir(parents=True, exist_ok=True)
        self._output_path = self._results_dir / "process_events.jsonl"
        self._node_id = str(node_id or "")
        self._node_name = str(node_name or "")
        self._node_tier = str(node_tier or "unknown").lower()
        self._buffer: deque[ProcessEvent] = deque(maxlen=max(1, int(max_events)))
        self._seq = 0
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)

    @property
    def output_path(self) -> Path:
        return self._output_path

    def emit(
        self,
        *,
        component: str,
        flow_type: str,
        flow_id: str,
        stage: str,
        status: str,
        message: str,
        details: dict[str, Any] | None = None,
        duration_ms: float | None = None,
        request_id: str | None = None,
        tx_hash: str | None = None,
        policy_id: int | None = None,
        from_signature: str | None = None,
        to_signature: str | None = None,
        node_id: str | None = None,
        node_name: str | None = None,
        node_tier: str | None = None,
    ) -> dict[str, Any]:
        with self._condition:
            self._seq += 1
            event = ProcessEvent(
                sequence=self._seq,
                event_id=str(uuid.uuid4()),
                ts_unix_ms=int(time.time() * 1000),
                node_id=str(node_id or self._node_id),
                node_name=str(node_name or self._node_name),
                node_tier=str(node_tier or self._node_tier or "unknown").lower(),
                component=str(component),
                flow_type=str(flow_type),
                flow_id=str(flow_id),
                stage=str(stage),
                status=str(status),
                message=str(message),
                details=dict(details or {}),
                duration_ms=round(float(duration_ms), 3) if duration_ms is not None else None,
                request_id=request_id or None,
                tx_hash=tx_hash or None,
                policy_id=int(policy_id) if policy_id is not None else None,
                from_signature=from_signature or None,
                to_signature=to_signature or None,
            )
            self._buffer.append(event)
            with self._output_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
            self._condition.notify_all()
            return event.to_dict()

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        count = max(1, int(limit))
        with self._lock:
            return [event.to_dict() for event in list(self._buffer)[-count:]]

    def latest_sequence(self) -> int:
        with self._lock:
            return self._seq

    def wait_for_events(self, after_sequence: int = 0, timeout: float = 2.0) -> list[dict[str, Any]]:
        end_time = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while self._seq <= after_sequence:
                remaining = end_time - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(timeout=remaining)
            return [event.to_dict() for event in self._buffer if event.sequence > after_sequence]

    def active_flows(self) -> list[dict[str, Any]]:
        with self._lock:
            flow_events: dict[str, list[ProcessEvent]] = {}
            for event in self._buffer:
                flow_events.setdefault(event.flow_id, []).append(event)

            active: list[dict[str, Any]] = []
            for flow_id, events in flow_events.items():
                first = events[0]
                last = events[-1]
                if last.status in self.TERMINAL_STATUSES:
                    continue
                active.append(
                    {
                        "flow_id": flow_id,
                        "flow_type": first.flow_type,
                        "node_tier": first.node_tier,
                        "started_at_ms": first.ts_unix_ms,
                        "last_stage": last.stage,
                        "last_status": last.status,
                        "message": last.message,
                        "event_count": len(events),
                        "request_id": first.request_id,
                    }
                )
            return sorted(active, key=lambda item: item["started_at_ms"], reverse=True)

    def flows(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            grouped: dict[str, list[ProcessEvent]] = {}
            for event in self._buffer:
                grouped.setdefault(event.flow_id, []).append(event)

            summaries: list[dict[str, Any]] = []
            for flow_id, events in grouped.items():
                first = events[0]
                last = events[-1]
                duration_ms = max(0, last.ts_unix_ms - first.ts_unix_ms)
                summaries.append(
                    {
                        "flow_id": flow_id,
                        "flow_type": first.flow_type,
                        "node_tier": first.node_tier,
                        "request_id": first.request_id,
                        "started_at_ms": first.ts_unix_ms,
                        "ended_at_ms": last.ts_unix_ms if last.status in self.TERMINAL_STATUSES else None,
                        "duration_ms": duration_ms if last.status in self.TERMINAL_STATUSES else None,
                        "final_status": last.status,
                        "last_stage": last.stage,
                        "message": last.message,
                        "events": [event.to_dict() for event in events],
                    }
                )
            summaries.sort(key=lambda item: item["started_at_ms"], reverse=True)
            return summaries[: max(1, int(limit))]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            recent = list(self._buffer)
            flow_status_counts = Counter()
            stage_counts = Counter()
            tier_counts = Counter()
            reason_counts = Counter()
            flow_type_counts = Counter()

            grouped: dict[str, list[ProcessEvent]] = {}
            for event in recent:
                stage_counts[event.stage] += 1
                tier_counts[event.node_tier] += 1
                flow_type_counts[event.flow_type] += 1
                grouped.setdefault(event.flow_id, []).append(event)

            for events in grouped.values():
                last = events[-1]
                flow_status_counts[last.status] += 1
                if last.status in {"denied", "error"}:
                    reason = last.message or last.stage
                    reason_counts[reason] += 1

            return {
                "total_events": len(recent),
                "flow_type_counts": dict(sorted(flow_type_counts.items())),
                "stage_counts": dict(sorted(stage_counts.items())),
                "status_counts": dict(sorted(flow_status_counts.items())),
                "node_tier_counts": dict(sorted(tier_counts.items())),
                "top_reasons": [
                    {"reason": reason, "count": count}
                    for reason, count in reason_counts.most_common(10)
                ],
            }


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
