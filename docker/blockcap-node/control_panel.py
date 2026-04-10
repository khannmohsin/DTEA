import os
import signal
import socket
import subprocess
import threading
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template_string, request, url_for


CONTROL_PORT = int(os.environ.get("BLOCKCAP_CONTROL_PORT", "8080"))
PUBLIC_CONTROL_PORT = int(os.environ.get("BLOCKCAP_PUBLIC_CONTROL_PORT", str(CONTROL_PORT)))
API_PORT = int(os.environ.get("FLASK_PORT", os.environ.get("BLOCKCAP_API_PORT", "5600")))
CHAIN_RPC_PORT = int(os.environ.get("BLOCKCAP_CHAIN_RPC_PORT", os.environ.get("RPC_HTTP_PORT", "8545")))
CHAIN_P2P_PORT = int(os.environ.get("BLOCKCAP_CHAIN_P2P_PORT", os.environ.get("P2P_PORT", "30303")))
CHAIN_HOST = os.environ.get("BLOCKCAP_CHAIN_HOST", "127.0.0.1")
WORKSPACE_DIR = Path(os.environ.get("BLOCKCAP_WORKSPACE", "/workspace"))
FALLBACK_API_ROOT = Path("/opt/blockcap/Node_root")
MANAGED_MODE = os.environ.get("BLOCKCAP_MANAGED_MODE", "standalone").strip().lower()
EXTERNAL_API_URL = os.environ.get("BLOCKCAP_EXTERNAL_API_URL", "").strip()
EXTERNAL_CHAIN_URL = os.environ.get("BLOCKCAP_EXTERNAL_CHAIN_URL", "").strip()

app = Flask(__name__)


def _container_ip() -> str:
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "unknown"


def _script_candidates(filename: str) -> list[Path]:
    return [WORKSPACE_DIR / filename, FALLBACK_API_ROOT / filename]


def _resolve_api_script() -> tuple[Path | None, Path | None]:
    for candidate in _script_candidates("orchestration_service.py"):
        if candidate.exists():
            return candidate, candidate.parent
    return None, None


def _resolve_chain_script() -> tuple[Path | None, Path | None]:
    candidate = WORKSPACE_DIR / "client_blockchain_init.py"
    if candidate.exists():
        return candidate, candidate.parent
    return None, None


class ProcessRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, dict[str, object]] = {}

    def _read_tail(self, log_path: Path, lines: int = 20) -> str:
        if not log_path.exists():
            return ""
        return "\n".join(log_path.read_text(errors="replace").splitlines()[-lines:])

    def status(self, name: str) -> dict[str, object]:
        with self._lock:
            item = self._items.get(name)
            if not item:
                return {"running": False, "pid": None, "command": None, "log": "", "log_path": None}
            process = item["process"]
            log_path = item["log_path"]
            assert isinstance(log_path, Path)
            return {
                "running": process.poll() is None,
                "pid": process.pid,
                "command": item["command"],
                "log": self._read_tail(log_path),
                "log_path": str(log_path),
            }

    def start(self, name: str, command: list[str], cwd: Path, env: dict[str, str], log_path: Path) -> tuple[bool, str]:
        with self._lock:
            current = self._items.get(name)
            if current and current["process"].poll() is None:
                return False, f"{name} is already running"
            if current and current.get("handle"):
                current["handle"].close()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handle = log_path.open("a", encoding="utf-8")
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self._items[name] = {
                "process": process,
                "command": " ".join(command),
                "log_path": log_path,
                "handle": handle,
            }
            return True, f"Started {name} (pid {process.pid})"

    def stop(self, name: str) -> tuple[bool, str]:
        with self._lock:
            current = self._items.get(name)
            if not current:
                return False, f"{name} is not running"
            process = current["process"]
            if process.poll() is not None:
                return False, f"{name} is already stopped"
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except Exception:
                process.terminate()
            handle = current.get("handle")
            if handle:
                handle.close()
            return True, f"Stopped {name}"


registry = ProcessRegistry()


PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>BlockCap Container</title>
  <style>
    :root { --bg:#f6f1e7; --panel:#fffdf8; --line:#d6cdbd; --ink:#182127; --muted:#5d6b72; --primary:#1f5f87; --ok:#236d3e; --warn:#9c6907; }
    * { box-sizing:border-box; }
    body { margin:0; background:linear-gradient(180deg,#efe6d6 0%,#f8f4ec 100%); color:var(--ink); font-family:"IBM Plex Sans","Segoe UI",sans-serif; }
    .wrap { max-width:1100px; margin:0 auto; padding:24px; }
    .hero, .panel { background:var(--panel); border:1px solid var(--line); border-radius:20px; padding:18px; margin-bottom:16px; }
    .hero h1, .panel h2 { margin:0 0 8px; }
    .sub { color:var(--muted); font-size:14px; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; margin-top:14px; }
    .card { background:#fff; border:1px solid var(--line); border-radius:16px; padding:14px; }
    .label { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
    .value { font-size:18px; font-weight:700; margin-top:6px; word-break:break-word; }
    .status { display:inline-flex; padding:5px 10px; border-radius:999px; font-size:12px; font-weight:700; }
    .status.running { background:#e5f3e8; color:var(--ok); }
    .status.stopped { background:#f0f2f4; color:#5e6a71; }
    .actions { display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }
    button { border:none; border-radius:12px; background:var(--primary); color:#fff; font:inherit; font-weight:700; padding:10px 14px; cursor:pointer; }
    button.alt { background:#dde7ee; color:#16384a; }
    pre { margin:0; background:#121c22; color:#dff4e5; border-radius:14px; padding:12px; min-height:140px; max-height:260px; overflow:auto; font-family:"IBM Plex Mono","SFMono-Regular",monospace; font-size:12px; }
    .row { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:16px; }
    .banner { margin-top:12px; padding:10px 12px; border-radius:12px; background:#fff4df; color:var(--warn); border:1px solid #ead3a3; }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>BlockCap Container Control</h1>
      <div class="sub">Default container entrypoint for local inspection. It shows the current container identity and can launch the API and chain processes inside this container.</div>
      {% if message %}<div class="banner">{{ message }}</div>{% endif %}
      <div class="grid">
        <div class="card"><div class="label">Container IP</div><div class="value">{{ info.ip }}</div></div>
                <div class="card"><div class="label">Control Page Port</div><div class="value">{{ info.control_port }}</div></div>
        <div class="card"><div class="label">API Port</div><div class="value">{{ info.api_port }}</div></div>
        <div class="card"><div class="label">Chain RPC Port</div><div class="value">{{ info.chain_rpc_port }}</div></div>
        <div class="card"><div class="label">Chain P2P Port</div><div class="value">{{ info.chain_p2p_port }}</div></div>
        <div class="card"><div class="label">Workspace</div><div class="value">{{ info.workspace }}</div></div>
                <div class="card"><div class="label">Mode</div><div class="value">{{ info.mode }}</div></div>
      </div>
    </section>

    <div class="row">
      {% for proc in processes %}
      <section class="panel">
        <h2>{{ proc.title }}</h2>
        <div class="sub">Script: {{ proc.script or 'unavailable in current workspace' }}</div>
                {% if proc.managed_externally %}<div class="banner">Managed by the topology launcher. Use this page for inspection; start and stop actions are disabled here.</div>{% endif %}
        <div class="actions">
          <span class="status {{ 'running' if proc.status.running else 'stopped' }}">{{ 'Running' if proc.status.running else 'Stopped' }}</span>
          <form method="post" action="{{ url_for('control_action', name=proc.name, action='start') }}">
                        <button type="submit" {% if not proc.available or proc.managed_externally %}disabled{% endif %}>Start {{ proc.title }}</button>
          </form>
          <form method="post" action="{{ url_for('control_action', name=proc.name, action='stop') }}">
                        <button type="submit" class="alt" {% if proc.managed_externally %}disabled{% endif %}>Stop {{ proc.title }}</button>
          </form>
        </div>
                {% if proc.external_url %}<div class="sub" style="margin-top:10px;">External target: {{ proc.external_url }}</div>{% endif %}
        <div class="grid">
          <div class="card"><div class="label">PID</div><div class="value">{{ proc.status.pid or '-' }}</div></div>
          <div class="card"><div class="label">Command</div><div class="value">{{ proc.status.command or '-' }}</div></div>
        </div>
        <div class="sub" style="margin:14px 0 8px;">Recent log tail</div>
        <pre>{{ proc.status.log or 'No log output yet.' }}</pre>
      </section>
      {% endfor %}
    </div>
  </div>
</body>
</html>
"""


def _process_descriptors() -> list[dict[str, object]]:
    api_script, api_cwd = _resolve_api_script()
    chain_script, chain_cwd = _resolve_chain_script()
    return [
        {
            "name": "api",
            "title": "API",
            "available": api_script is not None,
            "script": str(api_script) if api_script else None,
            "cwd": api_cwd,
            "managed_externally": MANAGED_MODE == "external",
            "external_url": EXTERNAL_API_URL,
            "command": [
                "python",
                str(api_script) if api_script else "",
                "--host",
                "0.0.0.0",
                "--port",
                str(API_PORT),
                "--repo-root",
                str(api_cwd or WORKSPACE_DIR),
            ],
            "status": registry.status("api"),
        },
        {
            "name": "chain",
            "title": "Chain",
            "available": chain_script is not None,
            "script": str(chain_script) if chain_script else None,
            "cwd": chain_cwd,
            "managed_externally": MANAGED_MODE == "external",
            "external_url": EXTERNAL_CHAIN_URL,
            "command": [
                "python",
                str(chain_script) if chain_script else "",
                "start_blockchain_node",
                str(CHAIN_P2P_PORT),
                str(CHAIN_RPC_PORT),
                CHAIN_HOST,
            ],
            "status": registry.status("chain"),
        },
    ]


@app.get("/")
def index():
    return render_template_string(
        PAGE,
        info={
            "ip": _container_ip(),
            "control_port": PUBLIC_CONTROL_PORT,
            "api_port": API_PORT,
            "chain_rpc_port": CHAIN_RPC_PORT,
            "chain_p2p_port": CHAIN_P2P_PORT,
            "workspace": str(WORKSPACE_DIR),
            "mode": MANAGED_MODE,
        },
        processes=_process_descriptors(),
        message=request.args.get("message", ""),
    )


@app.get("/status")
def status():
    return jsonify(
        {
            "ok": True,
            "ip": _container_ip(),
            "control_port": PUBLIC_CONTROL_PORT,
            "api_port": API_PORT,
            "chain_rpc_port": CHAIN_RPC_PORT,
            "chain_p2p_port": CHAIN_P2P_PORT,
            "workspace": str(WORKSPACE_DIR),
            "mode": MANAGED_MODE,
            "processes": {
                descriptor["name"]: descriptor["status"]
                for descriptor in _process_descriptors()
            },
        }
    )


@app.post("/control/<name>/<action>")
def control_action(name: str, action: str):
    descriptors = {item["name"]: item for item in _process_descriptors()}
    descriptor = descriptors.get(name)
    if not descriptor:
        return redirect(url_for("index", message=f"Unknown process: {name}"))
    if descriptor.get("managed_externally"):
        return redirect(url_for("index", message=f"{descriptor['title']} is managed by the topology launcher."))
    if action == "start":
        if not descriptor["available"]:
            return redirect(url_for("index", message=f"{descriptor['title']} script is not available in {WORKSPACE_DIR}"))
        env = os.environ.copy()
        if name == "api":
            env.setdefault("FLASK_PORT", str(API_PORT))
            env.setdefault("NODE_URL", f"http://{CHAIN_HOST}:{API_PORT}")
            env.setdefault("BESU_RPC_URL", f"http://{CHAIN_HOST}:{CHAIN_RPC_PORT}")
        else:
            env.setdefault("BESU_RPC_URL", f"http://{CHAIN_HOST}:{CHAIN_RPC_PORT}")
        ok, message = registry.start(
            name,
            descriptor["command"],
            descriptor["cwd"] or WORKSPACE_DIR,
            env,
            WORKSPACE_DIR / "logs" / f"control-panel-{name}.log",
        )
        return redirect(url_for("index", message=message))
    if action == "stop":
        _, message = registry.stop(name)
        return redirect(url_for("index", message=message))
    return redirect(url_for("index", message=f"Unsupported action: {action}"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=CONTROL_PORT)