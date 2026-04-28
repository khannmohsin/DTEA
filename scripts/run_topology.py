#!/usr/bin/env python3
import argparse
import json
import os
import random
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests
from eth_keys import keys
from eth_utils import keccak


REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_ROOT = REPO_ROOT / "Node_root"
if str(NODE_ROOT) not in sys.path:
    sys.path.insert(0, str(NODE_ROOT))

from device_catalog import normalize_device_ids, resolve_device


RUNTIME_ROOT = REPO_ROOT / "runtime"
GENERATED_ROOT = RUNTIME_ROOT / "generated"
TEMPLATES_ROOT = RUNTIME_ROOT / "templates"
ROOT_TEMPLATE_DIR = TEMPLATES_ROOT / "root"
CLIENT_TEMPLATE_DIR = TEMPLATES_ROOT / "client"
ENDPOINT_TEMPLATE_DIR = TEMPLATES_ROOT / "endpoint"
SERVICE_BUNDLE_DIR = REPO_ROOT / "Node_root"
PYTHON_BIN = REPO_ROOT / ".venv" / "bin" / "python"
LOCAL_HOST = "127.0.0.1"
CONTAINER_HOST = "192.168.65.254"  # Use IP instead of host.docker.internal for Besu compatibility
CONTAINER_IMAGE = "blockcap-node:local"
CONTAINER_CONTROL_PORT = 8080
CONTAINER_DOCKERFILE = REPO_ROOT / "docker" / "blockcap-node" / "Dockerfile"
PORT_CURSOR = 34000 + (int(time.time()) % 1000)
DEFAULT_ROOT_RPC_PORT = 0
DEFAULT_ROOT_P2P_PORT = 0
DEFAULT_ROOT_METRICS_PORT = 0
DEFAULT_ROOT_API_PORT = 0

SERVICE_BUNDLE_FILES = (
    "acknowledgement.py",
    "device_catalog.py",
    "infrastructure_control.py",
    "interact.js",
    "monitor.py",
    "orchestration_service.py",
    "orchestrator.py",
    "tef_metrics.py",
)

STALE_CHAIN_PATHS = (
    Path("data/database"),
    Path("data/caches"),
    Path("data/DATABASE_METADATA.json"),
    Path("data/VERSION_METADATA.json"),
    Path("data/besu.networks"),
    Path("data/besu.ports"),
)


def tail_file(path: Path, lines: int = 20) -> str:
    if not path.exists():
        return ""
    content = path.read_text(errors="replace").splitlines()
    return "\n".join(content[-lines:])


def format_command(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def normalize_address_text(value: str) -> str:
    text = ANSI_ESCAPE_RE.sub("", str(value or "")).strip()
    match = re.search(r"0x[a-fA-F0-9]{40}", text)
    return match.group(0) if match else text


def port_conflict_reported(log_text: str) -> bool:
    text = (log_text or "").lower()
    return "port(s)" in text and "already in use" in text


def parse_java_major_version(stderr_text: str) -> int | None:
    text = stderr_text or ""
    match = re.search(r'version "(\d+)(?:\.\d+)?', text)
    if match:
        return int(match.group(1))
    if "openjdk version" in text:
        match = re.search(r'openjdk version "(\d+)(?:\.\d+)?', text)
        if match:
            return int(match.group(1))
    return None


def port_is_available(port: int) -> bool:
    tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        tcp_socket.bind(("0.0.0.0", port))
        udp_socket.bind(("0.0.0.0", port))
        return True
    except OSError as exc:
        if exc.errno not in {1, 13}:
            return False
        completed = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", f"-iUDP:{port}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.returncode != 0
    finally:
        tcp_socket.close()
        udp_socket.close()


def allocate_free_port() -> int:
    global PORT_CURSOR
    for _ in range(128):
        candidate = random.randint(40000, 62000)
        if port_is_available(candidate):
            return candidate
    for candidate in range(PORT_CURSOR, 65000):
        if port_is_available(candidate):
            PORT_CURSOR = candidate + 1
            return candidate
    raise RuntimeError("Could not allocate a free port")


def docker_available() -> bool:
    try:
        completed = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return completed.returncode == 0


def ensure_container_runtime_prereqs() -> None:
    if not docker_available():
        raise RuntimeError("docker is not installed or not on PATH")


def ensure_container_image() -> None:
    inspect_result = run(
        ["docker", "image", "inspect", CONTAINER_IMAGE],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if inspect_result.returncode == 0:
        return
    if not CONTAINER_DOCKERFILE.exists():
        raise RuntimeError(f"Container Dockerfile not found at {CONTAINER_DOCKERFILE}")
    run(
        ["docker", "build", "-t", CONTAINER_IMAGE, "-f", str(CONTAINER_DOCKERFILE), "."],
        cwd=REPO_ROOT,
    )


def scenario_network_name(scenario_name: str) -> str:
    return f"blockcap-{sanitize_name(scenario_name)}"


def container_name(scenario_name: str, spec: "NodeSpec", process: str) -> str:
    return f"{scenario_network_name(scenario_name)}-{spec.tier}{spec.ordinal}-{process}"


def ensure_container_network(network_name: str) -> None:
    result = run(
        ["docker", "network", "inspect", network_name],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return
    run(["docker", "network", "create", network_name], cwd=REPO_ROOT)


def netem_shell(profile: dict[str, Any]) -> str:
    network = dict(profile.get("network") or {})
    delay = float(network.get("delay_ms") or 0)
    jitter = float(network.get("jitter_ms") or 0)
    loss = float(network.get("loss_percent") or 0)
    if delay <= 0 and jitter <= 0 and loss <= 0:
        return ""
    parts = [f"delay {delay:g}ms"]
    if jitter > 0:
        parts.append(f"{jitter:g}ms")
    if loss > 0:
        parts.append(f"loss {loss:g}%")
    return f"tc qdisc replace dev eth0 root netem {' '.join(parts)} >/dev/null 2>&1 || true"


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def launch_container_background(
    docker_cmd: list[str],
    *,
    cwd: Path,
    log_path: Path,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    return launch_background(docker_cmd, cwd=cwd, log_path=log_path, env=env or os.environ.copy())


def ensure_containerized_root_enode(node_dir: Path, root_enode: str) -> None:
    rewritten = str(root_enode or "").replace("@127.0.0.1:", f"@{CONTAINER_HOST}:").replace("@localhost:", f"@{CONTAINER_HOST}:")
    for rel in ("data/enode.txt", "static/enode.txt", "client_inbox/enode.txt"):
        target = node_dir / rel
        if target.exists():
            target.write_text(rewritten.strip() + "\n")


def local_url_for_container(url: str) -> str:
    return str(url or "").replace("127.0.0.1", CONTAINER_HOST).replace("localhost", CONTAINER_HOST)


def root_rpc_url_for_container(root_rpc_url: str) -> str:
    return local_url_for_container(root_rpc_url)


def host_total_memory_mb() -> int | None:
    try:
        if hasattr(os, "sysconf") and "SC_PAGE_SIZE" in os.sysconf_names and "SC_PHYS_PAGES" in os.sysconf_names:
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            pages = int(os.sysconf("SC_PHYS_PAGES"))
            if page_size > 0 and pages > 0:
                return int((page_size * pages) / (1024 * 1024))
    except Exception:
        pass
    return None


def validate_memory_budget(specs: list["NodeSpec"]) -> None:
    requested = sum(int((spec.device_profile or {}).get("memory_mb") or 0) for spec in specs if spec.tier != "cloud")
    if requested <= 0:
        return
    total_mb = host_total_memory_mb()
    if not total_mb:
        return
    if requested > int(total_mb * 0.85):
        raise RuntimeError(
            f"Requested simulated device memory ({requested} MB) exceeds the safe host budget on this machine ({total_mb} MB total)."
        )
    if requested > int(total_mb * 0.7):
        print(
            f"[topology] warning: requested simulated device memory is high ({requested} MB of {total_mb} MB host memory)",
            flush=True,
        )


@dataclass
class NodeSpec:
    tier: str
    ordinal: int
    node_type: str
    name: str
    node_id: str
    signature_seed: str
    directory: str
    api_port: int | None = None
    control_port: int | None = None
    rpc_port: int | None = None
    p2p_port: int | None = None
    metrics_port: int | None = None
    wants_validator: bool = False
    simulated_device: str = ""
    runtime_backend: str = "container"
    device_profile: dict[str, Any] = field(default_factory=dict)


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    location = f" (cwd={cwd})" if cwd else ""
    print(f"[exec] {format_command(cmd)}{location}", flush=True)
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=capture_output,
        text=True,
        check=check,
    )


def ensure_runtime_prereqs() -> None:
    try:
        run(["besu", "--version"], capture_output=True)
    except FileNotFoundError as exc:
        raise RuntimeError("besu is not installed or not on PATH") from exc

    try:
        completed = run(["java", "-version"], capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError("java is not installed or not on PATH") from exc

    java_major = parse_java_major_version((completed.stderr or "") + "\n" + (completed.stdout or ""))
    if java_major is not None and java_major < 21:
        raise RuntimeError(
            f"Besu requires Java 21+, but the current Java version is {java_major}. "
            "Install/select JDK 21 and try again."
        )


def wait_for(predicate, timeout: float, interval: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def wait_for_rpc_or_exit(
    rpc_url: str,
    process: subprocess.Popen[str],
    log_path: Path,
    timeout: float,
    interval: float = 2.0,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if rpc_ready(rpc_url):
            return True
        if port_conflict_reported(tail_file(log_path)):
            return False
        if process.poll() is not None:
            return False
        time.sleep(interval)
    return False


def json_rpc(url: str, method: str, params: list[Any]) -> Any:
    response = requests.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(str(payload["error"]))
    return payload.get("result")


def rpc_ready(url: str) -> bool:
    try:
        result = json_rpc(url, "net_version", [])
        return bool(result)
    except Exception:
        return False


def peer_count(url: str) -> int:
    try:
        result = json_rpc(url, "net_peerCount", [])
        return int(str(result), 16)
    except Exception:
        return 0


def block_number(url: str) -> int | None:
    try:
        result = json_rpc(url, "eth_blockNumber", [])
        return int(str(result), 16)
    except Exception:
        return None


def node_enode(url: str) -> str | None:
    try:
        info = json_rpc(url, "admin_nodeInfo", [])
        enode = str((info or {}).get("enode") or "").strip()
        return enode or None
    except Exception:
        return None


def add_peer(rpc_url: str, peer_enode: str) -> bool:
    try:
        return bool(json_rpc(rpc_url, "admin_addPeer", [peer_enode]))
    except Exception:
        return False


def ensure_root_peer_link(
    *,
    root_rpc_url: str,
    root_enode: str,
    node_rpc_url: str,
    node_label: str,
    timeout: float = 20.0,
) -> bool:
    candidate_enode = node_enode(node_rpc_url)
    if not candidate_enode:
        print(f"[topology] {node_label} enode is not available yet; proceeding without explicit peering", flush=True)
        return False

    print(f"[topology] forcing peer link between root and {node_label}", flush=True)
    add_peer(node_rpc_url, root_enode)
    add_peer(root_rpc_url, candidate_enode)

    linked = wait_for(
        lambda: peer_count(root_rpc_url) > 0 and peer_count(node_rpc_url) > 0,
        timeout=timeout,
        interval=1.0,
    )
    if linked:
        print(f"[topology] peer link established for {node_label}", flush=True)
    else:
        print(f"[topology] peer link for {node_label} is still pending; continuing", flush=True)
    return linked


def health_ready(url: str) -> bool:
    try:
        response = requests.get(url.rstrip("/") + "/health", timeout=5)
        return response.ok
    except Exception:
        return False


def parse_validator_addresses(raw: Any) -> list[str]:
    if isinstance(raw, str):
        cleaned = raw.replace("[", "").replace("]", "").replace('"', "").replace("'", "")
        return [part.strip().lower() for part in cleaned.split(",") if part.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(part).strip().lower() for part in raw if str(part).strip()]
    return []


def validator_in_set(api_url: str, address: str) -> bool:
    try:
        response = requests.get(api_url.rstrip("/") + "/validators", timeout=5)
        if not response.ok:
            return False
        payload = response.json()
        validators = parse_validator_addresses(payload.get("validators"))
        return address.strip().lower() in validators
    except Exception:
        return False


def wait_for_chain_progress(rpc_url: str, *, label: str, timeout: float = 45.0, interval: float = 2.0) -> bool:
    start_block = block_number(rpc_url)
    if start_block is None:
        return False

    target_block = start_block + 2

    def progressed() -> bool:
        current = block_number(rpc_url)
        return current is not None and current >= target_block

    if wait_for(progressed, timeout=timeout, interval=interval):
        print(f"[topology] {label} chain progressed from block {start_block} to at least {target_block}", flush=True)
        return True
    return False


def control_page_ready(url: str) -> bool:
    try:
        response = requests.get(url.rstrip("/") + "/status", timeout=5)
        return response.ok
    except Exception:
        return False


def http_reachable(url: str) -> bool:
    try:
        requests.get(url.rstrip("/") + "/health", timeout=5)
        return True
    except Exception:
        return False


def build_identity_signature(
    node_id: str,
    node_name: str,
    node_type: str,
    public_key: str,
    private_key_path: Path,
) -> str:
    message = {
        "node_id": node_id,
        "node_name": node_name,
        "node_type": node_type,
        "public_key": public_key,
    }
    digest = keccak(text=json.dumps(message, sort_keys=True))
    private_key_hex = private_key_path.read_text().strip()
    if private_key_hex.startswith("0x"):
        private_key_hex = private_key_hex[2:]
    private_key = keys.PrivateKey(bytes.fromhex(private_key_hex))
    return private_key.sign_msg_hash(digest).to_hex()


def derive_address(private_key_path: Path) -> str:
    completed = run(
        [
            "besu",
            "public-key",
            "export-address",
            f"--node-private-key-file={private_key_path}",
        ],
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0 and completed.stdout.strip():
        return normalize_address_text(completed.stdout.strip().splitlines()[-1])

    private_key_hex = private_key_path.read_text().strip()
    if private_key_hex.startswith("0x"):
        private_key_hex = private_key_hex[2:]
    private_key = keys.PrivateKey(bytes.fromhex(private_key_hex))
    return normalize_address_text("0x" + private_key.public_key.to_canonical_address().hex())


def build_registration_payload(
    *,
    node_dir: Path,
    node_id: str,
    node_name: str,
    node_type: str,
    rpc_url: str,
    node_url: str | None,
    wants_validator: bool,
) -> dict[str, Any]:
    public_key_path = node_dir / "data" / "key.pub"
    private_key_path = node_dir / "data" / "key.priv"
    public_key = public_key_path.read_text().strip()
    payload = {
        "node_id": node_id,
        "node_name": node_name,
        "node_type": node_type,
        "public_key": public_key,
        "address": normalize_address_text(derive_address(private_key_path)),
        "rpcURL": rpc_url,
        "signature": build_identity_signature(node_id, node_name, node_type, public_key, private_key_path),
        "wants_validator": bool(wants_validator),
    }
    if node_url:
        payload["node_url"] = node_url
    (node_dir / "node-details.json").write_text(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def ensure_root_node_details(root_dir: Path, *, rpc_url: str, node_url: str | None) -> dict[str, Any]:
    details_path = root_dir / "node-details.json"
    if details_path.exists():
        try:
            payload = json.loads(details_path.read_text())
        except Exception:
            payload = {}
        payload["rpcURL"] = rpc_url
        if node_url:
            payload["node_url"] = node_url
        details_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        return payload
    return build_registration_payload(
        node_dir=root_dir,
        node_id="CLOUD01",
        node_name="Root Cloud",
        node_type="Cloud",
        rpc_url=rpc_url,
        node_url=node_url,
        wants_validator=False,
    )


def render_client_env(
    api_port: int,
    rpc_port: int,
    p2p_port: int,
    host: str = LOCAL_HOST,
    parent_url: str | None = None,
) -> str:
    return (
        f"FLASK_PORT={api_port}\n"
        f"BESU_PORT={rpc_port}\n"
        f"P2P_PORT={p2p_port}\n"
        f"NODE_URL=http://{host}:{api_port}\n"
        f"BESU_RPC_URL=http://{host}:{rpc_port}\n"
        + (f"PARENT_URL={parent_url}\n" if parent_url else "")
    )


def render_endpoint_env(
    api_port: int,
    rpc_url: str,
    host: str = LOCAL_HOST,
    parent_url: str | None = None,
) -> str:
    payload = (
        f"FLASK_PORT={api_port}\n"
        f"NODE_URL=http://{host}:{api_port}\n"
        f"BESU_RPC_URL={rpc_url}\n"
    )
    if parent_url:
        payload += f"PARENT_URL={parent_url}\n"
    return payload


def write_client_env(
    node_dir: Path,
    api_port: int,
    rpc_port: int,
    p2p_port: int,
    host: str = LOCAL_HOST,
    parent_url: str | None = None,
) -> None:
    (node_dir / ".env").write_text(render_client_env(api_port, rpc_port, p2p_port, host, parent_url=parent_url))


def write_endpoint_env(
    node_dir: Path,
    api_port: int,
    rpc_url: str,
    host: str = LOCAL_HOST,
    parent_url: str | None = None,
) -> None:
    (node_dir / ".env").write_text(render_endpoint_env(api_port, rpc_url, host, parent_url))


def build_node_env(
    base_env: dict[str, str],
    *,
    spec: NodeSpec,
    root_rpc_url: str,
    host: str = LOCAL_HOST,
    parent_api_url: str | None = None,
) -> dict[str, str]:
    env = dict(base_env)
    env["FLASK_PORT"] = str(spec.api_port)
    env["NODE_URL"] = f"http://{host}:{spec.api_port}"
    env["NODE_ROLE"] = spec.tier  # fog / edge / endpoint / cloud
    if parent_api_url:
        env["PARENT_URL"] = local_url_for_container(parent_api_url) if spec.runtime_backend == "container" else parent_api_url
    if spec.tier in {"fog", "edge"}:
        env["BESU_PORT"] = str(spec.rpc_port)
        env["P2P_PORT"] = str(spec.p2p_port)
        env["BESU_RPC_URL"] = f"http://{host}:{spec.rpc_port}"
    else:
        env["BESU_RPC_URL"] = root_rpc_url
    return env


def patch_metrics_port(script_path: Path, metrics_port: int) -> None:
    text = script_path.read_text()
    updated = re.sub(r"--metrics-port=\d+", f"--metrics-port={metrics_port}", text, count=1)
    script_path.write_text(updated)


def copy_tree(src: Path, dst: Path, ignore_names: set[str]) -> None:
    def _ignore(_root: str, names: list[str]) -> set[str]:
        ignored = set()
        for name in names:
            if name in ignore_names:
                ignored.add(name)
            elif name == "__pycache__":
                ignored.add(name)
            elif name.endswith(".pyc") or name.endswith(".log"):
                ignored.add(name)
        return ignored

    shutil.copytree(src, dst, ignore=_ignore)


def sync_service_bundle(destination_dir: Path) -> None:
    for name in SERVICE_BUNDLE_FILES:
        source = SERVICE_BUNDLE_DIR / name
        if not source.exists() and name == "interact.js":
            fallback = REPO_ROOT / "node-registry-test" / "contracts" / "interact.js"
            if fallback.exists():
                source = fallback
        if not source.exists():
            if name == "interact.js":
                print("[topology] optional bundle file missing: interact.js (skipping)", flush=True)
                continue
            raise FileNotFoundError(f"Required service bundle file not found: {source}")
        shutil.copy2(source, destination_dir / name)


def clear_stale_chain_state(destination_dir: Path) -> None:
    for rel_path in STALE_CHAIN_PATHS:
        target = destination_dir / rel_path
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()


def prepare_root_dir(template_dir: Path, destination_dir: Path) -> None:
    ignore_names = {"__pycache__", ".DS_Store"}
    copy_tree(template_dir, destination_dir, ignore_names)
    clear_stale_chain_state(destination_dir)
    sync_service_bundle(destination_dir)
    (destination_dir / "measurements").mkdir(parents=True, exist_ok=True)


def prepare_client_dir(template_dir: Path, destination_dir: Path, *, chain_backed: bool) -> None:
    ignore_names = {"data", "genesis", "node-details.json", ".env", "client_inbox", "static", "measurements"}
    copy_tree(template_dir, destination_dir, ignore_names)
    (destination_dir / "data").mkdir(parents=True, exist_ok=True)
    (destination_dir / "measurements").mkdir(parents=True, exist_ok=True)
    if chain_backed:
        (destination_dir / "genesis").mkdir(parents=True, exist_ok=True)
        (destination_dir / "static").mkdir(parents=True, exist_ok=True)
        (destination_dir / "client_inbox").mkdir(parents=True, exist_ok=True)
    sync_service_bundle(destination_dir)
    clear_stale_chain_state(destination_dir)


def root_node_registry_artifact(root_dir: Path) -> Path:
    preferred = root_dir / "data" / "NodeRegistry.json"
    if preferred.exists():
        return preferred
    fallback = root_dir / "smart_contract_deployment" / "build" / "contracts" / "NodeRegistry.json"
    if fallback.exists():
        return fallback
    raise FileNotFoundError("Could not locate root NodeRegistry.json artifact")


def copy_root_chain_material(root_dir: Path, node_dir: Path, root_enode: str) -> None:
    shutil.copy2(root_dir / "genesis" / "genesis.json", node_dir / "genesis" / "genesis.json")
    shutil.copy2(root_dir / "prefunded_keys.json", node_dir / "prefunded_keys.json")
    shutil.copy2(root_node_registry_artifact(root_dir), node_dir / "data" / "NodeRegistry.json")
    (node_dir / "data" / "enode.txt").write_text(root_enode.strip() + "\n")
    (node_dir / "static" / "enode.txt").write_text(root_enode.strip() + "\n")
    (node_dir / "client_inbox" / "enode.txt").write_text(root_enode.strip() + "\n")


def launch_background(cmd: list[str], *, cwd: Path, log_path: Path, env: dict[str, str]) -> subprocess.Popen[str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "w")
    print(f"[spawn] {format_command(cmd)} -> {log_path}", flush=True)
    return subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )


def post_registration(api_url: str, payload: dict[str, Any], timeout_seconds: float = 180.0) -> dict[str, Any]:
    response = requests.post(
        api_url.rstrip("/") + "/register-node",
        json=payload,
        timeout=timeout_seconds,
    )
    if response.status_code not in {200, 409}:
        raise RuntimeError(f"registration failed ({response.status_code}): {response.text}")
    result = response.json()
    if isinstance(result, dict):
        result["_http_status"] = response.status_code
    return result


def registration_target_ready(url: str) -> bool:
    """Return True when target API is healthy and has contract deployed."""
    try:
        response = requests.get(url.rstrip("/") + "/health", timeout=5)
        if not response.ok:
            return False
        payload = response.json()
        checks = payload.get("checks") or {}
        deployed = payload.get("deployed")
        contract_check = checks.get("contract_deployed")
        return bool(deployed) or bool(contract_check)
    except Exception:
        return False


def registration_visible(api_url: str, signature: str) -> bool:
    try:
        response = requests.get(api_url.rstrip("/") + f"/node/{signature}", timeout=5)
        return response.status_code == 200
    except Exception:
        return False


def verified_registration(
    reg_url: str,
    payload: dict[str, Any],
    tier: str,
    ordinal: int,
    parent_rpc_url: str | None = None,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    """Register a node against its designated parent with readiness retries."""

    attempts = 20
    retry_delay_seconds = 5.0
    last_exc: RuntimeError | None = None
    last_result: dict[str, Any] | None = None
    warmup_checked = False

    for attempt in range(1, attempts + 1):
        if parent_rpc_url and not warmup_checked:
            if not wait_for_chain_progress(parent_rpc_url, label=f"{tier}{ordinal} parent", timeout=45, interval=2):
                print(
                    f"[topology] parent chain for {tier}{ordinal} did not advance before registration; continuing",
                    flush=True,
                )
            warmup_checked = True
        if not registration_target_ready(reg_url):
            if attempt == 1:
                print(
                    f"[topology] waiting for parent registration target before {tier}{ordinal} registration",
                    flush=True,
                )
            time.sleep(retry_delay_seconds)
            continue
        try:
            result = post_registration(reg_url, payload, timeout_seconds)
            last_result = result
            if result.get("status") and result.get("tx"):
                return result
            if (
                int(result.get("_http_status", 200) or 200) == 409
                and str(result.get("error") or "").strip().lower() == "already registered"
                and registration_visible(reg_url, str(payload.get("signature") or ""))
            ):
                return {
                    "ok": True,
                    "status": "already_registered",
                    "tx": None,
                    "_http_status": 409,
                }
            # Parent reached but did not produce TX yet: retry same parent.
            time.sleep(retry_delay_seconds)
            continue
        except RuntimeError as exc:
            last_exc = exc
            msg = str(exc)
            if (
                ("TimeExhausted" in msg or "not in the chain after 60 seconds" in msg or "not in the chain after 120 seconds" in msg)
                and registration_visible(reg_url, str(payload.get("signature") or ""))
            ):
                return {
                    "ok": True,
                    "status": "already_registered",
                    "tx": None,
                }
            # DuplicateNodeId revert means the first TX landed but timed out before
            # the receipt was returned — the node is already on-chain. Treat as success.
            if "DuplicateNodeId" in msg or "70477a48" in msg:
                if registration_visible(reg_url, str(payload.get("signature") or "")):
                    return {
                        "ok": True,
                        "status": "already_registered",
                        "tx": None,
                    }
            # Parent API can be up while chain inclusion is still stabilizing.
            # Treat these startup-time failures as transient and retry parent.
            if (
                "contract_not_deployed" in msg
                or "not in the chain after 60 seconds" in msg
                or "not in the chain after 120 seconds" in msg
                or "TimeExhausted" in msg
            ):
                print(
                    f"[topology] {tier}{ordinal}: transient registration failure on parent "
                    f"(attempt {attempt}/{attempts}); retrying",
                    flush=True,
                )
                time.sleep(retry_delay_seconds)
                continue
            raise

    if last_exc is not None:
        raise last_exc
    if last_result is not None:
        return last_result
    raise RuntimeError(f"registration failed for {tier}{ordinal}: parent registration target not ready")


def resolve_registration_url(
    tier: str,
    ordinal: int,
    manifest: dict[str, Any],
    root_api_url: str,
    root_rpc_url: str,
) -> tuple[str, str, str | None]:
    """Return (api_url, label, rpc_url) for where this node should register.

    Hierarchy:
      fog      → cloud root
      edge     → fog whose ordinal is ((edge_ordinal - 1) % fog_count) + 1
      endpoint → edge whose ordinal is ((ep_ordinal - 1) % edge_count) + 1
                 falling back to fog, then cloud root
    """
    nodes = manifest.get("nodes") or []
    if tier == "fog":
        return root_api_url, "root cloud", root_rpc_url

    if tier == "edge":
        fog_nodes = [n for n in nodes if n.get("tier") == "fog"]
        if fog_nodes:
            parent = fog_nodes[(ordinal - 1) % len(fog_nodes)]
            return parent["api_url"], parent["name"], parent.get("rpc_url")
        return root_api_url, "root cloud (no fog available)", root_rpc_url

    if tier == "endpoint":
        edge_nodes = [n for n in nodes if n.get("tier") == "edge"]
        if edge_nodes:
            parent = edge_nodes[(ordinal - 1) % len(edge_nodes)]
            return parent["api_url"], parent["name"], parent.get("rpc_url")
        fog_nodes = [n for n in nodes if n.get("tier") == "fog"]
        if fog_nodes:
            parent = fog_nodes[(ordinal - 1) % len(fog_nodes)]
            return parent["api_url"], parent["name"], parent.get("rpc_url")
        return root_api_url, "root cloud (no edge/fog available)", root_rpc_url

    return root_api_url, "root cloud", root_rpc_url


def root_contract_is_deployed(root_dir: Path, env: dict[str, str]) -> bool:
    """Check whether the NodeRegistry contract has code at its stored address."""
    artifact_path = root_dir / "data" / "NodeRegistry.json"
    if not artifact_path.exists():
        return False
    import sys
    sys.path.insert(0, str(root_dir))
    # The topology runner assembles runtime env in-process; apply relevant values
    # while constructing the orchestrator so deployment checks do not fall back
    # to the legacy JS bridge when interact.js is intentionally absent.
    previous: dict[str, str | None] = {}
    for key in ("REAL_INTERACT", "USE_JS_BRIDGE", "BESU_RPC_URL", "FROM_IDX"):
        previous[key] = os.environ.get(key)
        if key in env:
            os.environ[key] = str(env[key])
    try:
        from orchestrator import Orchestrator
        orch = Orchestrator(repo_root=str(root_dir))
        return orch.check_if_deployed()
    except Exception as exc:
        print(f"[topology] contract deploy check failed: {exc}", flush=True)
        return False
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _deploy_contract_web3(root_dir: Path, root_rpc_url: str, env: dict[str, str]) -> None:
    """Deploy NodeRegistry using web3.py from pre-compiled artifacts (no truffle/ganache required)."""
    from web3 import Web3
    from eth_account import Account

    smart_dir = root_dir / "smart_contract_deployment"
    artifact_src = smart_dir / "build" / "contracts" / "NodeRegistry.json"
    if not artifact_src.exists():
        raise FileNotFoundError(f"NodeRegistry build artifact not found: {artifact_src}")

    # Always compile from current source so the deployed ABI matches NodeRegistry.sol.
    import subprocess as _subprocess
    node_modules_npx = smart_dir / "node_modules" / ".bin" / "truffle"
    npx_cmd = str(node_modules_npx) if node_modules_npx.exists() else "npx"
    print("[topology] compiling NodeRegistry contract from source …", flush=True)
    try:
        _subprocess.run(
            [npx_cmd, "truffle", "compile", "--all"],
            cwd=str(smart_dir),
            check=True,
            capture_output=True,
            text=True,
        )
        print("[topology] truffle compile succeeded", flush=True)
    except Exception as _exc:
        raise RuntimeError(f"truffle compile failed: {_exc}") from _exc

    if not artifact_src.exists():
        raise FileNotFoundError(f"NodeRegistry build artifact not found after compile: {artifact_src}")

    artifact = json.loads(artifact_src.read_text())
    abi = artifact["abi"]
    bytecode = artifact["bytecode"]

    # Use the root node's own generated key (data/key.priv) to deploy so that
    # policyAdmin = root's own address, which is never shared with other nodes.
    # Prefunded keys are shared during bootstrap; using one of them as policyAdmin
    # would allow any Fog/Edge node to call admin-only contract functions.
    node_key_file = root_dir / "data" / "key.priv"
    if not node_key_file.exists():
        raise FileNotFoundError(f"Root node private key not found: {node_key_file}")
    private_key_hex = node_key_file.read_text().strip()
    if not private_key_hex.startswith("0x"):
        private_key_hex = "0x" + private_key_hex

    w3 = Web3(Web3.HTTPProvider(root_rpc_url, request_kwargs={"timeout": 60}))
    if not w3.is_connected():
        raise RuntimeError(f"Cannot connect to Besu RPC at {root_rpc_url}")

    acct = Account.from_key(private_key_hex)
    deployer_address = acct.address
    chain_id = w3.eth.chain_id
    nonce = w3.eth.get_transaction_count(deployer_address)

    NodeRegistry = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = NodeRegistry.constructor().build_transaction({
        "from": deployer_address,
        "nonce": nonce,
        "gas": 8_000_000,
        "gasPrice": 0,
        "chainId": chain_id,
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"[topology] NodeRegistry deploy tx: {tx_hash.hex()}", flush=True)

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.status != 1:
        raise RuntimeError(f"NodeRegistry deployment reverted (receipt status={receipt.status})")

    print(f"[topology] NodeRegistry deployed at {receipt.contractAddress}", flush=True)

    # Patch the artifact with the deployed address and write to data/
    artifact.setdefault("networks", {})[str(chain_id)] = {
        "events": {},
        "links": {},
        "address": receipt.contractAddress,
        "transactionHash": tx_hash.hex(),
    }
    dest_artifact = root_dir / "data" / "NodeRegistry.json"
    dest_artifact.write_text(json.dumps(artifact, indent=2))
    print(f"[topology] NodeRegistry artifact written to {dest_artifact}", flush=True)


def ensure_root_contract_deployed(root_dir: Path, root_rpc_url: str, env: dict[str, str]) -> None:
    if root_contract_is_deployed(root_dir, env):
        return

    print("[topology] deploying NodeRegistry contract via web3.py", flush=True)
    transient_markers = ("connection refused", "timeout", "econnrefused", "econnreset")
    last_error: Exception | None = None
    for attempt in range(1, 4):
        print(f"[topology] contract deploy attempt {attempt}/3", flush=True)
        try:
            _deploy_contract_web3(root_dir, root_rpc_url, env)
            break
        except Exception as exc:
            last_error = exc
            lower = str(exc).lower()
            if attempt < 3 and any(m in lower for m in transient_markers):
                print(f"[topology] transient RPC error, retrying: {exc}", flush=True)
                time.sleep(5 * attempt)
                continue
            raise RuntimeError(str(exc)) from exc
    else:
        raise RuntimeError(str(last_error)) from last_error

    if not root_contract_is_deployed(root_dir, env):
        raise RuntimeError("NodeRegistry deployment completed but check_if_deployed is still false")


def seed_root_policies(root_dir: Path, root_rpc_url: str, env: dict[str, str]) -> None:
    from eth_account import Account
    from web3 import Web3

    artifact_path = root_dir / "data" / "NodeRegistry.json"
    if not artifact_path.exists():
        raise FileNotFoundError(f"NodeRegistry artifact not found: {artifact_path}")

    key_path = root_dir / "data" / "key.priv"
    if not key_path.exists():
        raise FileNotFoundError(f"Root node private key not found: {key_path}")

    artifact = json.loads(artifact_path.read_text())
    abi = artifact.get("abi") or []
    networks = artifact.get("networks") or {}
    network_id = next(iter(networks.keys()), None)
    contract_address = networks.get(network_id, {}).get("address") if network_id else None
    if not abi or not contract_address:
        raise RuntimeError("NodeRegistry artifact is missing ABI or deployed address")

    private_key_hex = key_path.read_text().strip()
    if not private_key_hex.startswith("0x"):
        private_key_hex = "0x" + private_key_hex

    w3 = Web3(Web3.HTTPProvider(root_rpc_url, request_kwargs={"timeout": 60}))
    if not w3.is_connected():
        raise RuntimeError(f"Cannot connect to Besu RPC at {root_rpc_url}")

    contract = w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=abi)
    acct = Account.from_key(private_key_hex)
    chain_id = w3.eth.chain_id
    ctx_zero = "0x" + ("0" * 64)
    wanted_pairs = [
        ("Cloud", "Fog"),
        ("Cloud", "Edge"),
        ("Cloud", "Sensor"),
        ("Fog", "Edge"),
        ("Fog", "Sensor"),
        ("Edge", "Sensor"),
    ]
    role_num = {"Cloud": 1, "Fog": 2, "Edge": 3, "Sensor": 4, "Actuator": 5}

    try:
        latest_id = int(contract.functions.nextPolicyId().call() or 0)
    except Exception as exc:
        raise RuntimeError(f"failed to read nextPolicyId during policy seeding: {exc}") from exc

    existing_pairs: set[tuple[str, str]] = set()
    for pid in range(1, max(0, latest_id) + 1):
        try:
            policy = contract.functions.getPolicy(pid).call()
        except Exception:
            continue
        if not policy:
            continue
        from_role = int(policy[0])
        to_role = int(policy[1])
        ops_allowed = int(policy[2])
        is_deprecated = bool(policy[3])
        raw_ctx = policy[4]
        if isinstance(raw_ctx, (bytes, bytearray)):
            ctx_schema = "0x" + bytes(raw_ctx).hex()
        else:
            ctx_schema = str(raw_ctx).lower()
        if is_deprecated or ops_allowed != 1 or ctx_schema != ctx_zero:
            continue
        from_name = next((name for name, number in role_num.items() if number == from_role), None)
        to_name = next((name for name, number in role_num.items() if number == to_role), None)
        if from_name and to_name:
            existing_pairs.add((from_name, to_name))

    nonce = w3.eth.get_transaction_count(acct.address, "pending")
    for from_role, to_role in wanted_pairs:
        if (from_role, to_role) in existing_pairs:
            print(f"[topology] policy seed already present for {from_role}->{to_role}", flush=True)
            continue

        print(f"[topology] seeding policy {from_role}->{to_role} (READ, empty ctx)", flush=True)
        tx = contract.functions.createPolicy(role_num[from_role], role_num[to_role], 1, bytes(32)).build_transaction({
            "from": acct.address,
            "nonce": nonce,
            "gas": 3_000_000,
            "gasPrice": 0,
            "chainId": chain_id,
        })
        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if int(receipt.status or 0) != 1:
            raise RuntimeError(
                f"policy seeding reverted for {from_role}->{to_role}: {tx_hash.hex()}"
            )
        nonce += 1


def ensure_root_started(
    root_dir: Path,
    root_api_port: int,
    root_rpc_port: int,
    *,
    root_p2p_port: int,
    root_metrics_port: int,
    host: str,
    env: dict[str, str],
    logs_dir: Path,
) -> dict[str, Any]:
    print("[topology] preparing root cloud runtime", flush=True)
    root_api_url = f"http://{host}:{root_api_port}"
    chain_proc = None
    service_proc = None
    root_besu_log = logs_dir / "root-besu.log"
    current_rpc_port = root_rpc_port
    current_p2p_port = root_p2p_port
    current_metrics_port = root_metrics_port
    root_env = dict(env)
    root_env["IS_POLICY_ADMIN"] = "1"

    ensure_runtime_prereqs()
    for attempt in range(4):
        root_rpc_url = f"http://{host}:{current_rpc_port}"
        print(
            f"[topology] starting root chain attempt {attempt + 1} on rpc={current_rpc_port} p2p={current_p2p_port}",
            flush=True,
        )
        root_env.update({
            "ROOT_BESU_RPC_PORT": str(current_rpc_port),
            "ROOT_BESU_P2P_PORT": str(current_p2p_port),
            "ROOT_BESU_METRICS_PORT": str(current_metrics_port),
            "BESU_RPC_URL": root_rpc_url,
        })

        if rpc_ready(root_rpc_url):
            print("[topology] root chain already reachable", flush=True)
            break

        chain_proc = launch_background(
            [str(PYTHON_BIN), "root_blockchain_init.py", "start_blockchain_node", host],
            cwd=root_dir,
            log_path=root_besu_log,
            env=root_env,
        )
        if wait_for_rpc_or_exit(root_rpc_url, chain_proc, root_besu_log, timeout=90, interval=2):
            print("[topology] root chain is ready", flush=True)
            break

        time.sleep(1)
        detail = tail_file(root_besu_log)
        if attempt < 3 and port_conflict_reported(detail):
            print("[topology] root chain ports conflicted, retrying with fresh ports", flush=True)
            current_rpc_port = allocate_free_port()
            current_p2p_port = allocate_free_port()
            current_metrics_port = allocate_free_port()
            continue

        if detail:
            raise RuntimeError(f"root RPC did not become ready\n{detail}")
        raise RuntimeError("root RPC did not become ready")

    print("[topology] ensuring root smart contract is deployed", flush=True)
    ensure_root_contract_deployed(root_dir, root_rpc_url, root_env)
    print("[topology] seeding root policies", flush=True)
    seed_root_policies(root_dir, root_rpc_url, root_env)

    if not health_ready(root_api_url):
        print(f"[topology] starting root api on port {root_api_port}", flush=True)
        service_proc = launch_background(
            [
                str(PYTHON_BIN),
                "orchestration_service.py",
                "--host",
                "0.0.0.0",
                "--port",
                str(root_api_port),
                "--repo-root",
                str(root_dir),
            ],
            cwd=root_dir,
            log_path=logs_dir / "root-api.log",
            env=root_env,
        )
        if not wait_for(lambda: health_ready(root_api_url), timeout=60, interval=1):
            raise RuntimeError("root API did not become ready")
    print("[topology] root api is ready", flush=True)
    if not wait_for_chain_progress(root_rpc_url, label="root", timeout=45, interval=2):
        print("[topology] root chain did not show additional block progress during warm-up; continuing", flush=True)
    node_details = ensure_root_node_details(root_dir, rpc_url=root_rpc_url, node_url=root_api_url)

    return {
        "rpc_url": root_rpc_url,
        "api_url": root_api_url,
        "p2p_port": current_p2p_port,
        "chain_pid": chain_proc.pid if chain_proc else None,
        "service_pid": service_proc.pid if service_proc else None,
        "node_details": node_details,
    }


def start_service_and_chain(
    *,
    node_dir: Path,
    spec: NodeSpec,
    env: dict[str, str],
    logs_dir: Path,
    root_rpc_url: str,
    root_enode: str,
    scenario_name: str | None = None,
) -> tuple[int | None, int | None, int | None]:
    if spec.runtime_backend == "container":
        scenario_label = str(scenario_name or node_dir.parent.name or "scenario")
        network_name = scenario_network_name(scenario_label)
        control_name = container_name(scenario_label, spec, "control")
        chain_name = container_name(scenario_label, spec, "chain")
        api_name = container_name(scenario_label, spec, "api")
        control_port = int(spec.control_port or 0)
        api_port = int(spec.api_port or 0)
        rpc_port = int(spec.rpc_port or 0)
        p2p_port = int(spec.p2p_port or 0)
        cpu_limit = str((spec.device_profile or {}).get("vcpu") or 1)
        memory_limit = f"{int((spec.device_profile or {}).get('memory_mb') or 512)}m"
        ensure_containerized_root_enode(node_dir, root_enode)
        netem_prefix = netem_shell(spec.device_profile)
        control_url = f"http://{LOCAL_HOST}:{control_port}" if control_port else ""
        if control_port:
            print(f"[topology] starting {spec.tier}{spec.ordinal} page on port {control_port} (container)", flush=True)
            control_proc = launch_container_background(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    control_name,
                    "--network",
                    network_name,
                    "--cpus",
                    cpu_limit,
                    "--memory",
                    memory_limit,
                    "-p",
                    f"{control_port}:{CONTAINER_CONTROL_PORT}",
                    "-v",
                    f"{node_dir}:/workspace",
                    "-w",
                    "/workspace",
                    "-e",
                    f"BLOCKCAP_CONTROL_PORT={CONTAINER_CONTROL_PORT}",
                    "-e",
                    f"BLOCKCAP_PUBLIC_CONTROL_PORT={control_port}",
                    "-e",
                    "BLOCKCAP_MANAGED_MODE=external",
                    "-e",
                    f"BLOCKCAP_API_PORT={api_port}",
                    "-e",
                    f"BLOCKCAP_CHAIN_RPC_PORT={rpc_port}",
                    "-e",
                    f"BLOCKCAP_CHAIN_P2P_PORT={p2p_port}",
                    "-e",
                    f"BLOCKCAP_EXTERNAL_API_URL=http://{LOCAL_HOST}:{api_port}",
                    "-e",
                    f"BLOCKCAP_EXTERNAL_CHAIN_URL=http://{LOCAL_HOST}:{rpc_port}",
                    CONTAINER_IMAGE,
                ],
                cwd=node_dir,
                log_path=logs_dir / f"{spec.tier}-{spec.ordinal}-control.log",
            )
            if not wait_for(lambda: control_page_ready(control_url), timeout=45, interval=1):
                raise RuntimeError(f"{spec.tier}{spec.ordinal} page did not become ready")
            print(f"[topology] {spec.tier}{spec.ordinal} page is ready", flush=True)
        else:
            control_proc = None
        chain_shell_parts = [part for part in [
            netem_prefix,
            shell_join([
                "python",
                "client_blockchain_init.py",
                "start_blockchain_node",
                str(p2p_port),
                str(rpc_port),
                LOCAL_HOST,
            ]),
        ] if part]
        api_env = dict(env)
        api_env["BESU_RPC_URL"] = f"http://{chain_name}:{rpc_port}"
        api_shell_parts = [part for part in [
            netem_prefix,
            shell_join([
                "python",
                "orchestration_service.py",
                "--host",
                "0.0.0.0",
                "--port",
                str(api_port),
                "--repo-root",
                "/workspace",
            ]),
        ] if part]
        print(f"[topology] starting {spec.tier}{spec.ordinal} api on port {spec.api_port} (container)", flush=True)
        service_proc = launch_container_background(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                api_name,
                "--network",
                network_name,
                "--cap-add",
                "NET_ADMIN",
                "--cpus",
                cpu_limit,
                "--memory",
                memory_limit,
                "-p",
                f"{api_port}:{api_port}",
                "-v",
                f"{node_dir}:/workspace",
                "-w",
                "/workspace",
                "-e",
                f"REAL_INTERACT={api_env.get('REAL_INTERACT', '1')}",
                "-e",
                f"FLASK_PORT={api_port}",
                "-e",
                f"NODE_URL=http://{LOCAL_HOST}:{api_port}",
                "-e",
                f"BESU_RPC_URL={api_env['BESU_RPC_URL']}",
                CONTAINER_IMAGE,
                "sh",
                "-lc",
                " && ".join(api_shell_parts),
            ],
            cwd=node_dir,
            log_path=logs_dir / f"{spec.tier}-{spec.ordinal}-api.log",
        )
        if not wait_for(lambda: http_reachable(f"http://{LOCAL_HOST}:{spec.api_port}"), timeout=45, interval=1):
            raise RuntimeError(f"{spec.tier}{spec.ordinal} API did not become ready")
        print(f"[topology] starting {spec.tier}{spec.ordinal} chain on rpc={spec.rpc_port} p2p={spec.p2p_port} (container)", flush=True)
        chain_proc = launch_container_background(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                chain_name,
                "--network",
                network_name,
                "--cap-add",
                "NET_ADMIN",
                "--cpus",
                cpu_limit,
                "--memory",
                memory_limit,
                "-p",
                f"{rpc_port}:{rpc_port}",
                "-p",
                f"{p2p_port}:{p2p_port}/tcp",
                "-p",
                f"{p2p_port}:{p2p_port}/udp",
                "-v",
                f"{node_dir}:/workspace",
                "-w",
                "/workspace",
                "-e",
                f"REAL_INTERACT={env.get('REAL_INTERACT', '1')}",
                "-e",
                f"BESU_RPC_URL=http://{LOCAL_HOST}:{rpc_port}",
                CONTAINER_IMAGE,
                "sh",
                "-lc",
                " && ".join(chain_shell_parts),
            ],
            cwd=node_dir,
            log_path=logs_dir / f"{spec.tier}-{spec.ordinal}-besu.log",
        )
        if not wait_for(lambda: rpc_ready(f"http://{LOCAL_HOST}:{spec.rpc_port}"), timeout=90, interval=2):
            raise RuntimeError(f"{spec.tier}{spec.ordinal} RPC did not become ready")
        ensure_root_peer_link(
            root_rpc_url=root_rpc_url,
            root_enode=str(root_enode or "").replace("@127.0.0.1:", f"@{CONTAINER_HOST}:").replace("@localhost:", f"@{CONTAINER_HOST}:"),
            node_rpc_url=f"http://{LOCAL_HOST}:{spec.rpc_port}",
            node_label=f"{spec.tier}{spec.ordinal}",
        )
        if not wait_for(lambda: health_ready(f"http://{LOCAL_HOST}:{spec.api_port}"), timeout=45, interval=1):
            if http_reachable(f"http://{LOCAL_HOST}:{spec.api_port}"):
                print(
                    f"[topology] {spec.tier}{spec.ordinal} api is reachable but /health is still unhealthy; continuing",
                    flush=True,
                )
            else:
                raise RuntimeError(f"{spec.tier}{spec.ordinal} API did not become healthy after RPC startup")
        print(f"[topology] {spec.tier}{spec.ordinal} api and chain are ready", flush=True)
        return control_proc.pid if control_proc else None, service_proc.pid, chain_proc.pid

    print(f"[topology] starting {spec.tier}{spec.ordinal} api on port {spec.api_port}", flush=True)
    service_proc = launch_background(
        [
            str(PYTHON_BIN),
            "orchestration_service.py",
            "--host",
            "0.0.0.0",
            "--port",
            str(spec.api_port),
            "--repo-root",
            str(node_dir),
        ],
        cwd=node_dir,
        log_path=logs_dir / f"{spec.tier}-{spec.ordinal}-api.log",
        env=env,
    )
    if not wait_for(lambda: http_reachable(f"http://{LOCAL_HOST}:{spec.api_port}"), timeout=45, interval=1):
        raise RuntimeError(f"{spec.tier}{spec.ordinal} API did not become ready")

    print(f"[topology] starting {spec.tier}{spec.ordinal} chain on rpc={spec.rpc_port} p2p={spec.p2p_port}", flush=True)
    chain_proc = launch_background(
        [
            str(PYTHON_BIN),
            "client_blockchain_init.py",
            "start_blockchain_node",
            str(spec.p2p_port),
            str(spec.rpc_port),
            LOCAL_HOST,
        ],
        cwd=node_dir,
        log_path=logs_dir / f"{spec.tier}-{spec.ordinal}-besu.log",
        env=env,
    )
    if not wait_for(lambda: rpc_ready(f"http://{LOCAL_HOST}:{spec.rpc_port}"), timeout=90, interval=2):
        raise RuntimeError(f"{spec.tier}{spec.ordinal} RPC did not become ready")
    ensure_root_peer_link(
        root_rpc_url=root_rpc_url,
        root_enode=root_enode,
        node_rpc_url=f"http://{LOCAL_HOST}:{spec.rpc_port}",
        node_label=f"{spec.tier}{spec.ordinal}",
    )
    if not wait_for(lambda: health_ready(f"http://{LOCAL_HOST}:{spec.api_port}"), timeout=45, interval=1):
        if http_reachable(f"http://{LOCAL_HOST}:{spec.api_port}"):
            print(
                f"[topology] {spec.tier}{spec.ordinal} api is reachable but /health is still unhealthy; continuing",
                flush=True,
            )
        else:
            raise RuntimeError(f"{spec.tier}{spec.ordinal} API did not become healthy after RPC startup")
    print(f"[topology] {spec.tier}{spec.ordinal} api and chain are ready", flush=True)

    return None, service_proc.pid, chain_proc.pid


def start_service_only(
    *,
    node_dir: Path,
    spec: NodeSpec,
    env: dict[str, str],
    logs_dir: Path,
    scenario_name: str | None = None,
) -> tuple[int | None, int | None]:
    if spec.runtime_backend == "container":
        scenario_label = str(scenario_name or node_dir.parent.name or "scenario")
        network_name = scenario_network_name(scenario_label)
        control_name = container_name(scenario_label, spec, "control")
        api_name = container_name(scenario_label, spec, "api")
        control_port = int(spec.control_port or 0)
        api_port = int(spec.api_port or 0)
        cpu_limit = str((spec.device_profile or {}).get("vcpu") or 1)
        memory_limit = f"{int((spec.device_profile or {}).get('memory_mb') or 512)}m"
        netem_prefix = netem_shell(spec.device_profile)
        endpoint_rpc_url = str(env.get("BESU_RPC_URL") or "")
        control_url = f"http://{LOCAL_HOST}:{control_port}" if control_port else ""
        if control_port:
            print(f"[topology] starting {spec.tier}{spec.ordinal} page on port {control_port} (container)", flush=True)
            control_proc = launch_container_background(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    control_name,
                    "--network",
                    network_name,
                    "--cpus",
                    cpu_limit,
                    "--memory",
                    memory_limit,
                    "-p",
                    f"{control_port}:{CONTAINER_CONTROL_PORT}",
                    "-v",
                    f"{node_dir}:/workspace",
                    "-w",
                    "/workspace",
                    "-e",
                    f"BLOCKCAP_CONTROL_PORT={CONTAINER_CONTROL_PORT}",
                    "-e",
                    f"BLOCKCAP_PUBLIC_CONTROL_PORT={control_port}",
                    "-e",
                    "BLOCKCAP_MANAGED_MODE=external",
                    "-e",
                    f"BLOCKCAP_API_PORT={api_port}",
                    "-e",
                    f"BLOCKCAP_EXTERNAL_API_URL=http://{LOCAL_HOST}:{api_port}",
                    "-e",
                    f"BLOCKCAP_EXTERNAL_CHAIN_URL={endpoint_rpc_url}",
                    CONTAINER_IMAGE,
                ],
                cwd=node_dir,
                log_path=logs_dir / f"{spec.tier}-{spec.ordinal}-control.log",
            )
            if not wait_for(lambda: control_page_ready(control_url), timeout=45, interval=1):
                raise RuntimeError(f"{spec.tier}{spec.ordinal} page did not become ready")
            print(f"[topology] {spec.tier}{spec.ordinal} page is ready", flush=True)
        else:
            control_proc = None
        api_shell_parts = [part for part in [
            netem_prefix,
            shell_join([
                "python",
                "orchestration_service.py",
                "--host",
                "0.0.0.0",
                "--port",
                str(api_port),
                "--repo-root",
                "/workspace",
            ]),
        ] if part]
        print(f"[topology] starting {spec.tier}{spec.ordinal} api on port {spec.api_port} (container)", flush=True)
        service_proc = launch_container_background(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                api_name,
                "--network",
                network_name,
                "--cap-add",
                "NET_ADMIN",
                "--cpus",
                cpu_limit,
                "--memory",
                memory_limit,
                "-p",
                f"{api_port}:{api_port}",
                "-v",
                f"{node_dir}:/workspace",
                "-w",
                "/workspace",
                "-e",
                f"REAL_INTERACT={env.get('REAL_INTERACT', '1')}",
                "-e",
                f"FLASK_PORT={api_port}",
                "-e",
                f"NODE_URL=http://{LOCAL_HOST}:{api_port}",
                "-e",
                f"BESU_RPC_URL={endpoint_rpc_url}",
                "-e",
                f"PARENT_URL={env.get('PARENT_URL', '')}",
                CONTAINER_IMAGE,
                "sh",
                "-lc",
                " && ".join(api_shell_parts),
            ],
            cwd=node_dir,
            log_path=logs_dir / f"{spec.tier}-{spec.ordinal}-api.log",
        )
        if not wait_for(lambda: health_ready(f"http://{LOCAL_HOST}:{spec.api_port}"), timeout=60, interval=1):
            raise RuntimeError(f"{spec.tier}{spec.ordinal} API did not become ready")
        print(f"[topology] {spec.tier}{spec.ordinal} api is ready", flush=True)
        return control_proc.pid if control_proc else None, service_proc.pid

    print(f"[topology] starting {spec.tier}{spec.ordinal} api on port {spec.api_port}", flush=True)
    service_proc = launch_background(
        [
            str(PYTHON_BIN),
            "orchestration_service.py",
            "--host",
            "0.0.0.0",
            "--port",
            str(spec.api_port),
            "--repo-root",
            str(node_dir),
        ],
        cwd=node_dir,
        log_path=logs_dir / f"{spec.tier}-{spec.ordinal}-api.log",
        env=env,
    )
    if not wait_for(lambda: health_ready(f"http://{LOCAL_HOST}:{spec.api_port}"), timeout=60, interval=1):
        raise RuntimeError(f"{spec.tier}{spec.ordinal} API did not become ready")
    print(f"[topology] {spec.tier}{spec.ordinal} api is ready", flush=True)
    return None, service_proc.pid


def generate_keys(node_dir: Path, *, chain_backed: bool) -> None:
    if chain_backed:
        run([str(PYTHON_BIN), "client_blockchain_init.py", "generate_keys"], cwd=node_dir)
    else:
        run([str(PYTHON_BIN), "end_node_initialization.py", "generate_keys"], cwd=node_dir)


def sanitize_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-") or f"scenario-{int(time.time())}"


def normalize_endpoint_roles(
    *,
    endpoint_count: int | None,
    endpoint_role: str = "Sensor",
    endpoint_roles: list[str] | None = None,
) -> list[str]:
    explicit = [str(role).strip().title() for role in (endpoint_roles or []) if str(role).strip()]
    if explicit:
        invalid = [role for role in explicit if role not in {"Sensor", "Actuator"}]
        if invalid:
            raise ValueError(f"Unsupported endpoint roles: {', '.join(invalid)}")
        if endpoint_count is not None and int(endpoint_count) != len(explicit):
            raise ValueError("endpoint count does not match the number of endpoint roles provided")
        return explicit
    count = max(0, int(endpoint_count or 0))
    role = str(endpoint_role or "Sensor").title()
    if role not in {"Sensor", "Actuator"}:
        raise ValueError(f"Unsupported endpoint role: {role}")
    return [role] * count


def make_node_specs(
    *,
    fog_count: int,
    edge_count: int,
    endpoint_count: int,
    endpoint_role: str,
    endpoint_roles: list[str] | None = None,
    fog_devices: list[str] | None = None,
    edge_devices: list[str] | None = None,
    endpoint_devices: list[str] | None = None,
    runtime_backend_override: str | None = None,
    scenario_name: str,
    scenario_dir: Path,
) -> list[NodeSpec]:
    specs: list[NodeSpec] = []
    stamp = int(time.time())
    resolved_endpoint_roles = normalize_endpoint_roles(
        endpoint_count=endpoint_count,
        endpoint_role=endpoint_role,
        endpoint_roles=endpoint_roles,
    )
    resolved_fog_devices = normalize_device_ids(tier="fog", count=fog_count, selected_ids=fog_devices)
    resolved_edge_devices = normalize_device_ids(tier="edge", count=edge_count, selected_ids=edge_devices)
    resolved_endpoint_devices = normalize_device_ids(tier="endpoint", count=len(resolved_endpoint_roles), selected_ids=endpoint_devices)

    for idx in range(1, fog_count + 1):
        preset = resolve_device(resolved_fog_devices[idx - 1], "fog")
        specs.append(
            NodeSpec(
                tier="fog",
                ordinal=idx,
                node_type="Fog",
                name=f"Fog{idx}",
                node_id=f"{scenario_name.upper()}-FOG-{idx:03d}",
                signature_seed=f"{scenario_name}-fog-{idx}-{stamp}",
                directory=str(scenario_dir / f"fog{idx}"),
                api_port=allocate_free_port(),
                control_port=allocate_free_port(),
                rpc_port=allocate_free_port(),
                p2p_port=allocate_free_port(),
                metrics_port=allocate_free_port(),
                wants_validator=(idx == 1),
                simulated_device=str(preset["id"]),
                runtime_backend=str(runtime_backend_override or (preset.get("simulation_profile") or {}).get("runtime_backend") or "container"),
                device_profile=dict(preset.get("simulation_profile") or {}),
            )
        )

    for idx in range(1, edge_count + 1):
        preset = resolve_device(resolved_edge_devices[idx - 1], "edge")
        specs.append(
            NodeSpec(
                tier="edge",
                ordinal=idx,
                node_type="Edge",
                name=f"Edge{idx}",
                node_id=f"{scenario_name.upper()}-EDGE-{idx:03d}",
                signature_seed=f"{scenario_name}-edge-{idx}-{stamp}",
                directory=str(scenario_dir / f"edge{idx}"),
                api_port=allocate_free_port(),
                control_port=allocate_free_port(),
                rpc_port=allocate_free_port(),
                p2p_port=allocate_free_port(),
                metrics_port=allocate_free_port(),
                wants_validator=False,
                simulated_device=str(preset["id"]),
                runtime_backend=str(runtime_backend_override or (preset.get("simulation_profile") or {}).get("runtime_backend") or "container"),
                device_profile=dict(preset.get("simulation_profile") or {}),
            )
        )

    for idx, role in enumerate(resolved_endpoint_roles, start=1):
        preset = resolve_device(resolved_endpoint_devices[idx - 1], "endpoint")
        specs.append(
            NodeSpec(
                tier="endpoint",
                ordinal=idx,
                node_type=role,
                name=f"{role}{idx}",
                node_id=f"{scenario_name.upper()}-END-{idx:03d}",
                signature_seed=f"{scenario_name}-endpoint-{idx}-{stamp}",
                directory=str(scenario_dir / f"endpoint{idx}"),
                api_port=allocate_free_port(),
                control_port=allocate_free_port(),
                wants_validator=False,
                simulated_device=str(preset["id"]),
                runtime_backend=str(runtime_backend_override or (preset.get("simulation_profile") or {}).get("runtime_backend") or "container"),
                device_profile=dict(preset.get("simulation_profile") or {}),
            )
        )

    return specs


def next_node_ordinal(manifest: dict[str, Any], tier: str) -> int:
    ordinals = [
        int(node.get("ordinal") or 0)
        for node in (manifest.get("nodes") or [])
        if str(node.get("tier") or "") == str(tier)
    ]
    return (max(ordinals) if ordinals else 0) + 1


def active_node_count(manifest: dict[str, Any], tier: str) -> int:
    return sum(
        1
        for node in (manifest.get("nodes") or [])
        if str(node.get("tier") or "") == str(tier)
        and str(node.get("lifecycle_status") or "active") == "active"
    )


def make_single_node_spec(
    *,
    tier: str,
    ordinal: int,
    scenario_name: str,
    scenario_dir: Path,
    endpoint_role: str = "Sensor",
    device_id: str = "",
    wants_validator: bool = False,
    runtime_backend_override: str | None = None,
) -> NodeSpec:
    stamp = int(time.time())
    preset = resolve_device(device_id, tier)
    if tier == "fog":
        return NodeSpec(
            tier="fog",
            ordinal=ordinal,
            node_type="Fog",
            name=f"Fog{ordinal}",
            node_id=f"{scenario_name.upper()}-FOG-{ordinal:03d}",
            signature_seed=f"{scenario_name}-fog-{ordinal}-{stamp}",
            directory=str(scenario_dir / f"fog{ordinal}"),
            api_port=allocate_free_port(),
            control_port=allocate_free_port(),
            rpc_port=allocate_free_port(),
            p2p_port=allocate_free_port(),
            metrics_port=allocate_free_port(),
            wants_validator=bool(wants_validator),
            simulated_device=str(preset["id"]),
            runtime_backend=str(runtime_backend_override or (preset.get("simulation_profile") or {}).get("runtime_backend") or "container"),
            device_profile=dict(preset.get("simulation_profile") or {}),
        )
    if tier == "edge":
        return NodeSpec(
            tier="edge",
            ordinal=ordinal,
            node_type="Edge",
            name=f"Edge{ordinal}",
            node_id=f"{scenario_name.upper()}-EDGE-{ordinal:03d}",
            signature_seed=f"{scenario_name}-edge-{ordinal}-{stamp}",
            directory=str(scenario_dir / f"edge{ordinal}"),
            api_port=allocate_free_port(),
            control_port=allocate_free_port(),
            rpc_port=allocate_free_port(),
            p2p_port=allocate_free_port(),
            metrics_port=allocate_free_port(),
            wants_validator=False,
            simulated_device=str(preset["id"]),
            runtime_backend=str(runtime_backend_override or (preset.get("simulation_profile") or {}).get("runtime_backend") or "container"),
            device_profile=dict(preset.get("simulation_profile") or {}),
        )
    if tier == "endpoint":
        role = str(endpoint_role or "Sensor").title()
        if role not in {"Sensor", "Actuator"}:
            raise ValueError(f"Unsupported endpoint role: {role}")
        return NodeSpec(
            tier="endpoint",
            ordinal=ordinal,
            node_type=role,
            name=f"{role}{ordinal}",
            node_id=f"{scenario_name.upper()}-END-{ordinal:03d}",
            signature_seed=f"{scenario_name}-endpoint-{ordinal}-{stamp}",
            directory=str(scenario_dir / f"endpoint{ordinal}"),
            api_port=allocate_free_port(),
            control_port=allocate_free_port(),
            wants_validator=False,
            simulated_device=str(preset["id"]),
            runtime_backend=str(runtime_backend_override or (preset.get("simulation_profile") or {}).get("runtime_backend") or "container"),
            device_profile=dict(preset.get("simulation_profile") or {}),
        )
    raise ValueError(f"Unsupported tier: {tier}")


def prepare_node(
    spec: NodeSpec,
    root_dir: Path,
    root_enode: str,
    *,
    root_rpc_url: str,
    parent_api_url: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    node_dir = Path(spec.directory)
    print(f"[topology] preparing {spec.tier}{spec.ordinal} workspace in {node_dir}", flush=True)
    template_dir = {
        "fog": CLIENT_TEMPLATE_DIR,
        "edge": CLIENT_TEMPLATE_DIR,
        "endpoint": ENDPOINT_TEMPLATE_DIR,
    }[spec.tier]

    prepare_client_dir(template_dir, node_dir, chain_backed=spec.tier in {"fog", "edge"})
    generate_keys(node_dir, chain_backed=spec.tier in {"fog", "edge"})

    if spec.tier in {"fog", "edge"}:
        print(f"[topology] generating blockchain identity for {spec.tier}{spec.ordinal}", flush=True)
        copy_root_chain_material(root_dir, node_dir, root_enode)
        write_client_env(
            node_dir,
            spec.api_port or 0,
            spec.rpc_port or 0,
            spec.p2p_port or 0,
            parent_url=parent_api_url,
        )
        patch_metrics_port(node_dir / "client_blockchain_init.py", spec.metrics_port or 0)
        payload = build_registration_payload(
            node_dir=node_dir,
            node_id=spec.node_id,
            node_name=spec.name,
            node_type=spec.node_type,
            rpc_url=f"http://{LOCAL_HOST}:{spec.rpc_port}",
            node_url=f"http://{LOCAL_HOST}:{spec.api_port}",
            wants_validator=spec.wants_validator,
        )
    else:
        print(f"[topology] generating endpoint identity for {spec.tier}{spec.ordinal}", flush=True)
        shutil.copy2(root_node_registry_artifact(root_dir), node_dir / "data" / "NodeRegistry.json")
        shutil.copy2(root_dir / "prefunded_keys.json", node_dir / "prefunded_keys.json")
        write_endpoint_env(node_dir, spec.api_port or 0, root_rpc_url, parent_url=parent_api_url)
        payload = build_registration_payload(
            node_dir=node_dir,
            node_id=spec.node_id,
            node_name=spec.name,
            node_type=spec.node_type,
            rpc_url=root_rpc_url,
            node_url=f"http://{LOCAL_HOST}:{spec.api_port}",
            wants_validator=False,
        )

    return node_dir, payload


def build_base_env() -> dict[str, str]:
    base_env = os.environ.copy()
    base_env.update({
        "REAL_INTERACT": "1",
        "ORCH_TRACE": base_env.get("ORCH_TRACE", "0"),
        "FROM_IDX": base_env.get("FROM_IDX", "0"),
        "PYTHONUNBUFFERED": "1",
    })
    return base_env


def initialize_root_scenario(
    *,
    scenario_name: str,
    host: str,
    root_dir_template: str,
    root_api_port: int = DEFAULT_ROOT_API_PORT,
    root_rpc_port: int = DEFAULT_ROOT_RPC_PORT,
    root_p2p_port: int = DEFAULT_ROOT_P2P_PORT,
    root_metrics_port: int = DEFAULT_ROOT_METRICS_PORT,
) -> dict[str, Any]:
    scenario_dir = GENERATED_ROOT / scenario_name
    if scenario_dir.exists():
        raise RuntimeError(f"Scenario directory already exists: {scenario_dir}")
    scenario_dir.mkdir(parents=True, exist_ok=False)
    logs_dir = scenario_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    print(f"[topology] provisioning scenario {scenario_name}", flush=True)

    root_template_dir = Path(root_dir_template).resolve()
    root_dir = scenario_dir / "root"
    prepare_root_dir(root_template_dir, root_dir)
    resolved_root_api_port = root_api_port or allocate_free_port()
    resolved_root_rpc_port = root_rpc_port or allocate_free_port()
    resolved_root_p2p_port = root_p2p_port or allocate_free_port()
    resolved_root_metrics_port = root_metrics_port or allocate_free_port()

    root_env = build_base_env()
    root_env.update({
        "ROOT_BESU_RPC_PORT": str(resolved_root_rpc_port),
        "ROOT_BESU_P2P_PORT": str(resolved_root_p2p_port),
        "ROOT_BESU_METRICS_PORT": str(resolved_root_metrics_port),
        "BESU_RPC_URL": f"http://{host}:{resolved_root_rpc_port}",
    })

    root_info = ensure_root_started(
        root_dir,
        resolved_root_api_port,
        resolved_root_rpc_port,
        root_p2p_port=resolved_root_p2p_port,
        root_metrics_port=resolved_root_metrics_port,
        host=host,
        env=root_env,
        logs_dir=logs_dir,
    )
    manifest = {
        "scenario": scenario_name,
        "root": {
            "directory": str(root_dir),
            "api_url": root_info["api_url"],
            "rpc_url": root_info["rpc_url"],
            "p2p_port": root_info.get("p2p_port"),
            "chain_pid": root_info["chain_pid"],
            "service_pid": root_info["service_pid"],
            "node_details": root_info.get("node_details") or {},
            "runtime_backend": "native",
        },
        "nodes": [],
    }
    manifest_path = scenario_dir / "topology.json"
    write_manifest(manifest_path, manifest)
    print(f"[topology] root cloud is ready for scenario {scenario_name}", flush=True)
    return {
        "scenario": scenario_name,
        "topology_file": str(manifest_path),
        "root_api_url": root_info["api_url"],
        "root_rpc_url": root_info["rpc_url"],
        "nodes_started": 0,
    }


def append_node_to_scenario(
    *,
    scenario_name: str,
    tier: str,
    device_id: str,
    endpoint_role: str = "Sensor",
    host: str = LOCAL_HOST,
) -> dict[str, Any]:
    started_pids: list[int] = []
    scenario_dir = GENERATED_ROOT / scenario_name
    manifest_path = scenario_dir / "topology.json"
    if not manifest_path.exists():
        raise RuntimeError(f"Scenario manifest not found: {manifest_path}")
    manifest = load_manifest(manifest_path)
    root = dict(manifest.get("root") or {})
    root_dir = Path(root.get("directory") or scenario_dir / "root")
    root_api_url = str(root.get("api_url") or "").strip()
    root_rpc_url = str(root.get("rpc_url") or "").strip()
    if not root_api_url or not root_rpc_url:
        raise RuntimeError("Root topology manifest is missing API/RPC URLs")

    ensure_container_runtime_prereqs()
    ensure_container_image()
    ensure_container_network(scenario_network_name(scenario_name))

    ordinal = next_node_ordinal(manifest, tier)
    wants_validator = tier == "fog" and ordinal == 1
    spec = make_single_node_spec(
        tier=tier,
        ordinal=ordinal,
        scenario_name=scenario_name,
        scenario_dir=scenario_dir,
        endpoint_role=endpoint_role,
        device_id=device_id,
        wants_validator=wants_validator,
    )
    validate_memory_budget([spec])

    try:
        print(f"[topology] provisioning {spec.tier}{spec.ordinal}", flush=True)
        reg_url, reg_label, parent_rpc_url = resolve_registration_url(
            spec.tier,
            spec.ordinal,
            manifest,
            root_api_url,
            root_rpc_url,
        )
        root_enode = json_rpc(root_rpc_url, "admin_nodeInfo", [])["enode"]
        node_dir, payload = prepare_node(
            spec,
            root_dir,
            root_enode,
            root_rpc_url=root_rpc_url,
            parent_api_url=reg_url,
        )
        node_record = {
            **asdict(spec),
            "directory": str(node_dir),
            "payload": payload,
            "parent_api_url": reg_url,
            "control_url": f"http://{LOCAL_HOST}:{spec.control_port}" if spec.control_port else None,
            "api_url": f"http://{LOCAL_HOST}:{spec.api_port}" if spec.api_port else None,
            "rpc_url": f"http://{LOCAL_HOST}:{spec.rpc_port}" if spec.rpc_port else None,
            "registration": None,
            "control_pid": None,
            "api_pid": None,
            "chain_pid": None,
            "selected_device": resolve_device(spec.simulated_device, spec.tier),
            "lifecycle_status": "active",
            "retired_at_ms": None,
        }
        base_env = build_base_env()
        node_env = build_node_env(
            base_env,
            spec=spec,
            root_rpc_url=root_rpc_url_for_container(root_rpc_url) if spec.runtime_backend == "container" else root_rpc_url,
            host=LOCAL_HOST,
            parent_api_url=reg_url,
        )
        logs_dir = scenario_dir / "logs"
        if spec.tier in {"fog", "edge"}:
            control_pid, api_pid, chain_pid = start_service_and_chain(
                node_dir=node_dir,
                spec=spec,
                env=node_env,
                logs_dir=logs_dir,
                root_rpc_url=root_rpc_url,
                root_enode=root_enode,
                scenario_name=scenario_name,
            )
            node_record["control_pid"] = control_pid
            node_record["api_pid"] = api_pid
            node_record["chain_pid"] = chain_pid
            started_pids.extend(pid for pid in (control_pid, api_pid, chain_pid) if pid)

        print(f"[topology] registering {spec.tier}{spec.ordinal} with {reg_label}", flush=True)
        registration_started = time.perf_counter()
        node_record["registration"] = verified_registration(
            reg_url,
            payload,
            spec.tier,
            spec.ordinal,
            parent_rpc_url=parent_rpc_url,
        )
        node_record["registration"]["registration_latency_ms"] = round(
            (time.perf_counter() - registration_started) * 1000,
            3,
        )
        reg_status = (node_record["registration"] or {}).get("status")
        print(f"[topology] registration result for {spec.tier}{spec.ordinal}: {reg_status}", flush=True)
        if spec.wants_validator:
            promotion_started = time.perf_counter()
            if wait_for(
                lambda: validator_in_set(root_api_url, str((payload or {}).get("address") or "")),
                timeout=90,
                interval=2,
            ):
                node_record["registration"]["validator_promotion_latency_ms"] = round(
                    (time.perf_counter() - promotion_started) * 1000,
                    3,
                )
            else:
                node_record["registration"]["validator_promotion_latency_ms"] = None
        if spec.tier == "endpoint":
            control_pid, api_pid = start_service_only(
                node_dir=node_dir,
                spec=spec,
                env=node_env,
                logs_dir=logs_dir,
                scenario_name=scenario_name,
            )
            node_record["control_pid"] = control_pid
            node_record["api_pid"] = api_pid
            started_pids.extend(pid for pid in (control_pid, api_pid) if pid)

        manifest.setdefault("nodes", []).append(node_record)
        write_manifest(manifest_path, manifest)
        print(f"[topology] {spec.tier}{spec.ordinal} added to scenario {scenario_name}", flush=True)
        return {
            "scenario": scenario_name,
            "topology_file": str(manifest_path),
            "node_key": f"{spec.tier}-{spec.ordinal}",
            "tier": spec.tier,
            "ordinal": spec.ordinal,
            "name": spec.name,
            "registration_status": reg_status,
        }
    except Exception:
        for pid in reversed(started_pids):
            terminate_pid(pid)
        raise


def terminate_pid(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


def _compose_scalar(value: Any) -> str:
    text = str(value)
    if text == "" or any(ch in text for ch in [":", "{", "}", "[", "]", ",", "#", "&", "*", "?", "|", ">", "!", "%", "@", "`", "\"", "'"]) or text.strip() != text:
        return json.dumps(text)
    return text


def _yaml_dump(data: Any, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(data, dict):
        lines: list[str] = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(_yaml_dump(value, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {_compose_scalar(value)}")
        return lines
    if isinstance(data, list):
        lines = []
        for item in data:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(_yaml_dump(item, indent + 2))
            else:
                lines.append(f"{prefix}- {_compose_scalar(item)}")
        return lines
    return [f"{prefix}{_compose_scalar(data)}"]


def generate_docker_compose(args, *, endpoint_roles: list[str], fog_devices: list[str], edge_devices: list[str], endpoint_devices: list[str]) -> dict[str, Any]:
    services: dict[str, Any] = {}
    volumes: dict[str, Any] = {}
    scenario = sanitize_name(args.scenario)

    def fog_parent_name(index: int) -> str:
        return "cloud"

    def edge_parent_name(index: int) -> str:
        if args.fog > 0:
            return f"fog{((index - 1) % args.fog) + 1}"
        return "cloud"

    def endpoint_parent_name(index: int) -> str:
        if args.edge > 0:
            return f"edge{((index - 1) % args.edge) + 1}"
        if args.fog > 0:
            return f"fog{((index - 1) % args.fog) + 1}"
        return "cloud"

    services["cloud"] = {
        "build": {"context": ".", "dockerfile": "Dockerfile"},
        "image": "blockcap-node",
        "restart": "unless-stopped",
        "environment": {
            "NODE_ROLE": "cloud",
            "ADMIN_TOKEN": "${ADMIN_TOKEN:-changeme}",
            "FLASK_PORT": "5600",
            "SCENARIO_NAME": scenario,
        },
        "volumes": [
            "cloud-data:/app/Node_root/data",
            "cloud-genesis:/app/Node_root/genesis",
        ],
        "ports": ["5600:5600", "8545:8545", "30303:30303"],
        "deploy": {"resources": {"limits": {"cpus": "4.0", "memory": "2g"}}},
    }
    volumes["cloud-data"] = {}
    volumes["cloud-genesis"] = {}

    next_port = 5601

    def add_non_root_service(name: str, role: str, *, ordinal: int, device_id: str = "", extra_env: dict[str, str] | None = None, with_genesis: bool = True, cpus: str = "1.0", memory: str = "256m") -> None:
        nonlocal next_port
        parent_service = ""
        if role == "fog":
            parent_service = fog_parent_name(ordinal)
        elif role == "edge":
            parent_service = edge_parent_name(ordinal)
        elif role == "endpoint":
            parent_service = endpoint_parent_name(ordinal)
        env = {
            "NODE_ROLE": role,
            "ROOT_URL": "http://cloud:5600",
            "PARENT_URL": f"http://{parent_service}:5600" if parent_service and parent_service != "cloud" else ("http://cloud:5600" if parent_service == "cloud" else ""),
            "FLASK_PORT": "5600",
            "SIMULATED_DEVICE_ID": device_id,
            "SCENARIO_NAME": scenario,
        }
        if extra_env:
            env.update(extra_env)
        mounts = [f"{name}-data:/app/Node_root/data"]
        if with_genesis:
            mounts.append(f"{name}-genesis:/app/Node_root/genesis")
            volumes[f"{name}-genesis"] = {}
        services[name] = {
            "image": "blockcap-node",
            "restart": "unless-stopped",
            "environment": env,
            "volumes": mounts,
            "ports": [f"{next_port}:5600"],
            "depends_on": [parent_service or "cloud"],
            "deploy": {"resources": {"limits": {"cpus": cpus, "memory": memory}}},
        }
        volumes[f"{name}-data"] = {}
        next_port += 1

    normalized_fog = normalize_device_ids(tier="fog", count=args.fog, selected_ids=fog_devices)
    normalized_edge = normalize_device_ids(tier="edge", count=args.edge, selected_ids=edge_devices)
    normalized_endpoint = normalize_device_ids(tier="endpoint", count=args.endpoint, selected_ids=endpoint_devices)

    for idx, device_id in enumerate(normalized_fog, start=1):
        add_non_root_service(f"fog{idx}", "fog", ordinal=idx, device_id=device_id, cpus="1.5", memory="512m")
    for idx, device_id in enumerate(normalized_edge, start=1):
        add_non_root_service(f"edge{idx}", "edge", ordinal=idx, device_id=device_id, cpus="1.0", memory="256m")
    for idx, device_id in enumerate(normalized_endpoint, start=1):
        role = endpoint_roles[idx - 1] if idx - 1 < len(endpoint_roles) else args.endpoint_role
        add_non_root_service(
            f"endpoint{idx}",
            "endpoint",
            ordinal=idx,
            device_id=device_id,
            extra_env={"ENDPOINT_ROLE": role},
            with_genesis=False,
            cpus="0.5",
            memory="128m",
        )

    return {"services": services, "volumes": volumes}


def batch_main(argv: list[str] | None = None) -> None:
    endpoint_count_was_explicit = "--endpoint" in sys.argv
    parser = argparse.ArgumentParser(description="Provision a BlockCap runtime topology with variable node counts")
    parser.add_argument("--mode", choices=["local", "docker"], default="local")
    parser.add_argument("--cloud", type=int, default=1)
    parser.add_argument("--fog", type=int, default=2)
    parser.add_argument("--edge", type=int, default=2)
    parser.add_argument("--endpoint", type=int, default=3)
    parser.add_argument("--endpoint-role", choices=["Sensor", "Actuator"], default="Sensor")
    parser.add_argument("--endpoint-roles", default="")
    parser.add_argument("--fog-devices", default="")
    parser.add_argument("--edge-devices", default="")
    parser.add_argument("--endpoint-devices", default="")
    parser.add_argument("--runtime-backend", choices=["auto", "container", "native"], default="auto")
    parser.add_argument("--scenario", default=f"scenario-{int(time.time())}")
    parser.add_argument("--root-api-port", type=int, default=DEFAULT_ROOT_API_PORT)
    parser.add_argument("--root-rpc-port", type=int, default=DEFAULT_ROOT_RPC_PORT)
    parser.add_argument("--root-p2p-port", type=int, default=DEFAULT_ROOT_P2P_PORT)
    parser.add_argument("--root-metrics-port", type=int, default=DEFAULT_ROOT_METRICS_PORT)
    parser.add_argument("--root-dir", default=str(ROOT_TEMPLATE_DIR))
    parser.add_argument("--host", default=LOCAL_HOST)
    args = parser.parse_args(argv)

    if args.cloud != 1:
        raise SystemExit("This runner currently supports exactly one cloud/root node")
    if not PYTHON_BIN.exists():
        raise SystemExit(f"Python virtualenv not found at {PYTHON_BIN}")

    endpoint_roles = []
    if args.endpoint_roles:
        endpoint_roles = [item.strip().title() for item in str(args.endpoint_roles).split(",") if item.strip()]
        try:
            args.endpoint = len(normalize_endpoint_roles(
                endpoint_count=args.endpoint if endpoint_count_was_explicit else None,
                endpoint_role=args.endpoint_role,
                endpoint_roles=endpoint_roles,
            ))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    fog_devices = [item.strip() for item in str(args.fog_devices).split(",") if item.strip()]
    edge_devices = [item.strip() for item in str(args.edge_devices).split(",") if item.strip()]
    endpoint_devices = [item.strip() for item in str(args.endpoint_devices).split(",") if item.strip()]
    runtime_backend_override = None if args.runtime_backend == "auto" else str(args.runtime_backend)

    if args.mode == "docker":
        if runtime_backend_override == "native":
            raise SystemExit("--runtime-backend native is only supported with --mode local")
        compose = generate_docker_compose(
            args,
            endpoint_roles=endpoint_roles,
            fog_devices=fog_devices,
            edge_devices=edge_devices,
            endpoint_devices=endpoint_devices,
        )
        output_path = REPO_ROOT / f"docker-compose.{sanitize_name(args.scenario)}.yml"
        output_path.write_text("\n".join(_yaml_dump(compose)) + "\n")
        subprocess.run(["docker", "compose", "-f", str(output_path), "up", "--build"], cwd=REPO_ROOT, check=True)
        print(json.dumps({"mode": "docker", "scenario": sanitize_name(args.scenario), "compose_file": str(output_path)}, indent=2))
        return

    scenario_name = sanitize_name(args.scenario)
    scenario_dir = GENERATED_ROOT / scenario_name
    logs_dir = scenario_dir / "logs"
    base_env = build_base_env()

    started_pids: list[int] = []
    try:
        root_result = initialize_root_scenario(
            scenario_name=scenario_name,
            host=args.host,
            root_dir_template=args.root_dir,
            root_api_port=args.root_api_port,
            root_rpc_port=args.root_rpc_port,
            root_p2p_port=args.root_p2p_port,
            root_metrics_port=args.root_metrics_port,
        )
        manifest = load_manifest(scenario_dir / "topology.json")
        root_dir = Path((manifest.get("root") or {}).get("directory") or scenario_dir / "root")
        root_info = dict(manifest.get("root") or {})
        root_info["node_details"] = ((manifest.get("root") or {}).get("node_details") or {})
        if root_info["chain_pid"]:
            started_pids.append(root_info["chain_pid"])
        if root_info["service_pid"]:
            started_pids.append(root_info["service_pid"])

        root_enode = json_rpc(root_info["rpc_url"], "admin_nodeInfo", [])["enode"]
        print("[topology] root node info retrieved", flush=True)
        specs = make_node_specs(
            fog_count=args.fog,
            edge_count=args.edge,
            endpoint_count=args.endpoint,
            endpoint_role=args.endpoint_role,
            endpoint_roles=endpoint_roles,
            fog_devices=fog_devices,
            edge_devices=edge_devices,
            endpoint_devices=endpoint_devices,
            runtime_backend_override=runtime_backend_override,
            scenario_name=scenario_name,
            scenario_dir=scenario_dir,
        )
        validate_memory_budget(specs)

        if any(spec.runtime_backend == "container" for spec in specs):
            ensure_container_runtime_prereqs()
            ensure_container_image()
            ensure_container_network(scenario_network_name(scenario_name))

        manifest = load_manifest(scenario_dir / "topology.json")

        for spec in specs:
            print(f"[topology] provisioning {spec.tier}{spec.ordinal}", flush=True)
            reg_url, reg_label, parent_rpc_url = resolve_registration_url(
                spec.tier,
                spec.ordinal,
                manifest,
                root_info["api_url"],
                root_info["rpc_url"],
            )
            node_dir, payload = prepare_node(
                spec,
                root_dir,
                root_enode,
                root_rpc_url=root_info["rpc_url"],
                parent_api_url=reg_url,
            )
            node_record = {
                **asdict(spec),
                "directory": str(node_dir),
                "payload": payload,
                "parent_api_url": reg_url,
                "control_url": f"http://{LOCAL_HOST}:{spec.control_port}" if spec.control_port else None,
                "api_url": f"http://{LOCAL_HOST}:{spec.api_port}" if spec.api_port else None,
                "rpc_url": f"http://{LOCAL_HOST}:{spec.rpc_port}" if spec.rpc_port else None,
                "registration": None,
                "control_pid": None,
                "api_pid": None,
                "chain_pid": None,
                "selected_device": resolve_device(spec.simulated_device, spec.tier),
            }
            node_env = build_node_env(
                base_env,
                spec=spec,
                root_rpc_url=root_rpc_url_for_container(root_info["rpc_url"]) if spec.runtime_backend == "container" else root_info["rpc_url"],
                host=LOCAL_HOST,
                parent_api_url=reg_url,
            )

            if spec.tier in {"fog", "edge"}:
                control_pid, api_pid, chain_pid = start_service_and_chain(
                    node_dir=node_dir,
                    spec=spec,
                    env=node_env,
                    logs_dir=logs_dir,
                    root_rpc_url=root_info["rpc_url"],
                    root_enode=root_enode,
                    scenario_name=scenario_name,
                )
                node_record["control_pid"] = control_pid
                node_record["api_pid"] = api_pid
                node_record["chain_pid"] = chain_pid
                started_pids.extend(pid for pid in (control_pid, api_pid, chain_pid) if pid)

            print(f"[topology] registering {spec.tier}{spec.ordinal} with {reg_label}", flush=True)
            registration_started = time.perf_counter()
            node_record["registration"] = verified_registration(
                reg_url,
                payload,
                spec.tier,
                spec.ordinal,
                parent_rpc_url=parent_rpc_url,
            )
            node_record["registration"]["registration_latency_ms"] = round(
                (time.perf_counter() - registration_started) * 1000,
                3,
            )
            reg_status = (node_record["registration"] or {}).get("status")
            print(f"[topology] registration result for {spec.tier}{spec.ordinal}: {reg_status}", flush=True)
            if spec.wants_validator:
                promotion_started = time.perf_counter()
                if wait_for(
                    lambda: validator_in_set(root_info["api_url"], str((payload or {}).get("address") or "")),
                    timeout=90,
                    interval=2,
                ):
                    node_record["registration"]["validator_promotion_latency_ms"] = round(
                        (time.perf_counter() - promotion_started) * 1000,
                        3,
                    )
                else:
                    node_record["registration"]["validator_promotion_latency_ms"] = None
            if spec.tier == "endpoint":
                control_pid, api_pid = start_service_only(node_dir=node_dir, spec=spec, env=node_env, logs_dir=logs_dir, scenario_name=scenario_name)
                node_record["control_pid"] = control_pid
                node_record["api_pid"] = api_pid
                started_pids.extend(pid for pid in (control_pid, api_pid) if pid)
            manifest["nodes"].append(node_record)

        output_path = scenario_dir / "topology.json"
        write_manifest(output_path, manifest)
        print(f"[topology] scenario {scenario_name} is ready", flush=True)

        final_result = dict(root_result)
        final_result.update({
            "topology_file": str(output_path),
            "nodes_started": len(manifest["nodes"]),
        })
        print(json.dumps(final_result, indent=2))
    except Exception:
        for pid in reversed(started_pids):
            terminate_pid(pid)
        raise


def start_root_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Start a live BlockCap root topology")
    parser.add_argument("--scenario", default=f"demo-{int(time.time())}")
    parser.add_argument("--root-api-port", type=int, default=DEFAULT_ROOT_API_PORT)
    parser.add_argument("--root-rpc-port", type=int, default=DEFAULT_ROOT_RPC_PORT)
    parser.add_argument("--root-p2p-port", type=int, default=DEFAULT_ROOT_P2P_PORT)
    parser.add_argument("--root-metrics-port", type=int, default=DEFAULT_ROOT_METRICS_PORT)
    parser.add_argument("--root-dir", default=str(ROOT_TEMPLATE_DIR))
    parser.add_argument("--host", default=LOCAL_HOST)
    args = parser.parse_args(argv)
    if not PYTHON_BIN.exists():
        raise SystemExit(f"Python virtualenv not found at {PYTHON_BIN}")
    scenario_name = sanitize_name(args.scenario)
    result = initialize_root_scenario(
        scenario_name=scenario_name,
        host=args.host,
        root_dir_template=args.root_dir,
        root_api_port=args.root_api_port,
        root_rpc_port=args.root_rpc_port,
        root_p2p_port=args.root_p2p_port,
        root_metrics_port=args.root_metrics_port,
    )
    print(json.dumps(result, indent=2))


def spawn_node_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Spawn a single node into an existing BlockCap topology")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--tier", choices=["fog", "edge", "endpoint"], required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--endpoint-role", choices=["Sensor", "Actuator"], default="Sensor")
    parser.add_argument("--host", default=LOCAL_HOST)
    args = parser.parse_args(argv)
    if not PYTHON_BIN.exists():
        raise SystemExit(f"Python virtualenv not found at {PYTHON_BIN}")
    result = append_node_to_scenario(
        scenario_name=sanitize_name(args.scenario),
        tier=args.tier,
        device_id=args.device_id,
        endpoint_role=args.endpoint_role,
        host=args.host,
    )
    print(json.dumps(result, indent=2))


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "start-root":
        start_root_main(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "spawn-node":
        spawn_node_main(sys.argv[2:])
        return
    batch_main(sys.argv[1:])


if __name__ == "__main__":
    main()
