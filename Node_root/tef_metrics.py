import json
import os
import queue
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


def ensure_ui_runs_dir(start_path: str | os.PathLike[str]) -> Path:
    runs_dir = Path(start_path)
    if runs_dir.name != "ui_runs":
        runs_dir = ensure_results_dir(str(start_path)) / "ui_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    return runs_dir


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
        self._last_write = 0.0

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
        now = time.time()
        if output_path.exists() and (now - self._last_write) < 5.0:
            return output_path
        self._last_write = now
        data = self.summary()
        output_path.parent.mkdir(parents=True, exist_ok=True)
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
        self._write_queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1000)
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True, name="process-event-writer")
        self._writer_thread.start()

    @property
    def output_path(self) -> Path:
        return self._output_path

    def _writer_loop(self) -> None:
        while True:
            item = self._write_queue.get()
            try:
                if item is None:
                    return
                with self._output_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(item, sort_keys=True) + "\n")
            except Exception:
                pass
            finally:
                self._write_queue.task_done()

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
            event_dict = event.to_dict()
            try:
                self._write_queue.put_nowait(event_dict)
            except queue.Full:
                pass
            self._condition.notify_all()
            return event_dict

    def flush(self, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            if self._write_queue.unfinished_tasks == 0:
                return
            time.sleep(0.01)

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


class UiResultsRecorder:
    def __init__(self, start_path: str | os.PathLike[str], *, max_snapshots: int = 720):
        self._runs_dir = ensure_ui_runs_dir(start_path)
        self._max_snapshots = max(30, int(max_snapshots))
        self._series: dict[str, deque[dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def _run_path(self, scenario: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in scenario).strip("-") or "scenario"
        return self._runs_dir / f"{safe}.json"

    def _latency_points(self, latency_summary: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for key, item in sorted((latency_summary or {}).items()):
            rows.append({
                "key": key,
                "operation": item.get("operation"),
                "node_tier": item.get("node_tier"),
                "condition": item.get("condition"),
                "mean_ms": item.get("mean_ms", 0.0),
                "count": item.get("count", 0),
            })
        return rows

    def _node_status_counts(self, scenario_details: dict[str, Any] | None) -> dict[str, int]:
        counts = Counter()
        node_views = ((scenario_details or {}).get("scenario") or {}).get("node_views") or []
        for node in node_views:
            counts[str(node.get("summary_status") or "unknown")] += 1
        return dict(sorted(counts.items()))

    def record_snapshot(
        self,
        *,
        scenario: str | None,
        event_stats: dict[str, Any],
        active_flows: list[dict[str, Any]],
        latency_summary: dict[str, dict[str, Any]],
        scenario_details: dict[str, Any] | None,
        research_sections: dict[str, Any] | None = None,
        summary_cards: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        if not scenario:
            return None
        snapshot = {
            "ts_unix_ms": int(time.time() * 1000),
            "total_events": int((event_stats or {}).get("total_events", 0)),
            "granted": int(((event_stats or {}).get("status_counts") or {}).get("ok", 0)),
            "denied": int(((event_stats or {}).get("status_counts") or {}).get("denied", 0)),
            "errors": int(((event_stats or {}).get("status_counts") or {}).get("error", 0)),
            "active_flows": len(active_flows or []),
            "node_status_counts": self._node_status_counts(scenario_details),
            "latency_points": self._latency_points(latency_summary or {}),
        }
        with self._lock:
            bucket = self._series.setdefault(scenario, deque(maxlen=self._max_snapshots))
            bucket.append(snapshot)
            payload = {
                "scenario": scenario,
                "updated_at_ms": snapshot["ts_unix_ms"],
                "snapshots": list(bucket),
                "summary_cards": list(summary_cards or []),
                "research_sections": research_sections or {},
                "scenario_details": scenario_details or {},
            }
            tmp_path = self._run_path(scenario).with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
            tmp_path.replace(self._run_path(scenario))
        return snapshot

    def load_run(self, scenario: str) -> dict[str, Any] | None:
        path = self._run_path(scenario)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    def available_runs(self) -> list[dict[str, Any]]:
        rows = []
        for path in sorted(self._runs_dir.glob("*.json")):
            data = self.load_run(path.stem)
            if not data:
                continue
            snapshots = data.get("snapshots") or []
            rows.append({
                "scenario": data.get("scenario") or path.stem,
                "snapshot_count": len(snapshots),
                "updated_at_ms": data.get("updated_at_ms") or (snapshots[-1]["ts_unix_ms"] if snapshots else 0),
                "path": str(path),
            })
        rows.sort(key=lambda item: item["updated_at_ms"], reverse=True)
        return rows

    def series(self, scenario: str | None) -> list[dict[str, Any]]:
        if not scenario:
            return []
        with self._lock:
            if scenario in self._series:
                return list(self._series[scenario])
        data = self.load_run(scenario)
        return list((data or {}).get("snapshots") or [])
