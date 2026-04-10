import os
import time
import tracemalloc
import resource
import functools
import socket
from flask import Flask, Response
from prometheus_client import Summary, Gauge, REGISTRY, generate_latest, CONTENT_TYPE_LATEST
import csv

root_path = os.path.dirname(os.path.abspath(__file__))

# --- Setup Prometheus registry ---
# Do NOT set PROMETHEUS_MULTIPROC_DIR — each node process runs its own
# single-process registry. Multiprocess mode would require a shared tmpfs
# and causes MultiProcessCollector to shadow real metrics.
os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)


def _get_or_create_metric(factory, name, documentation, labelnames):
    """Register a metric, silently reuse it if already registered."""
    try:
        return factory(name, documentation, labelnames)
    except ValueError:
        # Already registered in this Python process — retrieve by name.
        for collector in list(REGISTRY._names_to_collectors.values()):
            if getattr(collector, "_name", None) == name:
                return collector
        raise

# --- Define Metrics ---
# Prometheus metrics
FUNCTION_DURATION = _get_or_create_metric(Summary, "function_duration_seconds", "Time spent in function", ["function"])
FUNCTION_MEMORY = _get_or_create_metric(Gauge, "function_memory_kb", "Memory used by function (in KB)", ["function"])
FUNCTION_CPU_USER = _get_or_create_metric(Gauge, "function_cpu_user_seconds", "User CPU time used by function", ["function"])
FUNCTION_CPU_SYSTEM = _get_or_create_metric(Gauge, "function_cpu_system_seconds", "System CPU time used by function", ["function"])
FUNCTION_CPU_TOTAL = _get_or_create_metric(Gauge, "function_cpu_total_seconds", "Total CPU time (user + system) used by function", ["function"])

# Request-level metrics
REQUEST_DURATION = _get_or_create_metric(Summary, 'request_duration_seconds', 'Time for outgoing request', ['function'])
REQUEST_PAYLOAD_SIZE = _get_or_create_metric(Gauge, 'request_payload_size_bytes', 'Payload size in bytes', ['function'])
REQUEST_RESPONSE_SIZE = _get_or_create_metric(Gauge, 'request_response_size_bytes', 'Response size in bytes', ['function'])



# --- Flask Server for /metrics ---
app = Flask(__name__)

@app.route("/metrics")
def metrics():
    """Expose Prometheus metrics."""
    data = generate_latest(REGISTRY)
    return Response(data, mimetype=CONTENT_TYPE_LATEST)

# --- Utility to Track Function Performance ---
def track_performance(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        fname = func.__name__

        # Start tracking
        tracemalloc.start()
        usage_start = resource.getrusage(resource.RUSAGE_SELF)
        start_time = time.time()

        result = func(*args, **kwargs)

        # End tracking
        end_time = time.time()
        usage_end = resource.getrusage(resource.RUSAGE_SELF)
        current_mem, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # Calculate times
        user_cpu = usage_end.ru_utime - usage_start.ru_utime
        sys_cpu = usage_end.ru_stime - usage_start.ru_stime
        total_cpu = user_cpu + sys_cpu

        # Export metrics
        FUNCTION_DURATION.labels(fname).observe(end_time - start_time)
        FUNCTION_MEMORY.labels(fname).set(current_mem / 1024)  # Convert to KB
        FUNCTION_CPU_USER.labels(fname).set(user_cpu)
        FUNCTION_CPU_SYSTEM.labels(fname).set(sys_cpu)
        FUNCTION_CPU_TOTAL.labels(fname).set(total_cpu)

        # Append metrics to CSV with duration_seconds, memory_kb, and total_cpu_time_seconds
        csv_file = os.path.join(root_path, "measurements", "function_metrics.csv")
        file_exists = os.path.isfile(csv_file)
        with open(csv_file, mode='a', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["timestamp", "function", "duration_seconds", "memory_kb", "total_cpu_time_seconds"])
            writer.writerow([
            time.strftime('%Y-%m-%d %H:%M:%S'),
            fname,
            f"{end_time - start_time:.4f}",
            f"{current_mem / 1024:.2f}",
            f"{total_cpu:.4f}"
            ])
        

        return result

    return wrapper

def observe_request_metrics(func_name, payload_size, response_size, duration):
    REQUEST_DURATION.labels(func_name).observe(duration)
    REQUEST_PAYLOAD_SIZE.labels(func_name).set(payload_size)
    REQUEST_RESPONSE_SIZE.labels(func_name).set(response_size)
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    csv_file = os.path.join(root_path, "measurements", "request_metrics.csv")
    file_exists = os.path.isfile(csv_file)
    with open(csv_file, mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "function", "duration_seconds", "payload_size_bytes", "response_size_bytes"])
        writer.writerow([timestamp, func_name, f"{duration:.4f}", payload_size, response_size])

# --- Main Entry ---
if __name__ == "__main__":
    print("[Flask] Starting Flask server on port 9101...")
    app.run(host="0.0.0.0", port=9101)
