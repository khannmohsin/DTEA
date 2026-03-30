#!/usr/bin/env python3
import argparse
import json
import math
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
from typing import Any

import requests


RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[idx]


def resolve_request(host: str, operation: str, method: str | None, path_override: str | None, body: dict[str, Any] | None):
    path_map = {
        "health": ("GET", "/health"),
        "latency": ("GET", "/metrics/latency"),
        "access": ("POST", "/access"),
        "delegate": ("POST", "/delegate"),
        "expiryCheck": ("GET", "/expiry-check"),
        "registerNode": ("POST", "/register-node"),
        "revokeToken": ("POST", "/revoke-grant"),
        "grant": ("GET", "/grant"),
    }
    default_method, default_path = path_map.get(operation, ("GET", f"/{operation.lstrip('/')}"))
    req_method = (method or default_method).upper()
    req_path = path_override or default_path
    return req_method, host.rstrip("/") + req_path, body


def load_json(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    return json.loads(Path(path).read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description="Concurrent HTTP load test for BlockCap endpoints")
    parser.add_argument("--host", required=True)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--total-requests", type=int, default=500)
    parser.add_argument("--duration-cap", type=float, default=60.0)
    parser.add_argument("--method")
    parser.add_argument("--path")
    parser.add_argument("--body-file")
    args = parser.parse_args()

    body = load_json(args.body_file)
    method, url, payload = resolve_request(args.host, args.operation, args.method, args.path, body)
    session = requests.Session()
    lock = threading.Lock()
    latencies: list[float] = []
    errors = 0
    started = 0
    completed = 0
    start_time = time.monotonic()
    deadline = start_time + args.duration_cap

    def fire_once():
        nonlocal errors
        req_start = time.perf_counter()
        try:
            if method == "GET":
                resp = session.get(url, timeout=15)
            else:
                resp = session.request(method, url, json=payload, timeout=15)
            ok = 200 <= resp.status_code < 300
        except Exception:
            ok = False
        elapsed = time.perf_counter() - req_start
        with lock:
            latencies.append(elapsed)
            if not ok:
                errors += 1

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = set()
        while time.monotonic() < deadline and started < args.total_requests:
            while len(futures) < args.concurrency and started < args.total_requests and time.monotonic() < deadline:
                futures.add(pool.submit(fire_once))
                started += 1
            if not futures:
                break
            done, futures = wait(futures, return_when=FIRST_COMPLETED)
            completed += len(done)

        if futures:
            done, _ = wait(futures)
            completed += len(done)

    wall_seconds = max(0.001, time.monotonic() - start_time)
    mean_ms = statistics.fmean(latencies) * 1000 if latencies else 0.0
    stddev_ms = (statistics.pstdev(latencies) * 1000) if len(latencies) > 1 else 0.0
    p95_ms = percentile(latencies, 0.95) * 1000
    throughput_rps = completed / wall_seconds

    result = {
        "host": args.host,
        "operation": args.operation,
        "method": method,
        "url": url,
        "concurrency": args.concurrency,
        "total_requests": started,
        "completed_requests": completed,
        "duration_seconds": round(wall_seconds, 3),
        "mean_latency_ms": round(mean_ms, 3),
        "stddev_latency_ms": round(stddev_ms, 3),
        "p95_latency_ms": round(p95_ms, 3),
        "throughput_rps": round(throughput_rps, 3),
        "error_count": errors,
    }

    output_path = RESULTS_DIR / "load_test.json"
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
