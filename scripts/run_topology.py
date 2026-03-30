#!/usr/bin/env python3
import argparse
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests
from eth_keys import keys
from eth_utils import keccak


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "runtime"
GENERATED_ROOT = RUNTIME_ROOT / "generated"
TEMPLATES_ROOT = RUNTIME_ROOT / "templates"
ROOT_TEMPLATE_DIR = TEMPLATES_ROOT / "root"
CLIENT_TEMPLATE_DIR = TEMPLATES_ROOT / "client"
ENDPOINT_TEMPLATE_DIR = TEMPLATES_ROOT / "endpoint"
SERVICE_BUNDLE_DIR = REPO_ROOT / "Node_root"
PYTHON_BIN = REPO_ROOT / ".venv" / "bin" / "python"
LOCAL_HOST = "127.0.0.1"
PORT_CURSOR = 34000 + (int(time.time()) % 1000)
DEFAULT_ROOT_RPC_PORT = 0
DEFAULT_ROOT_P2P_PORT = 0
DEFAULT_ROOT_METRICS_PORT = 0
DEFAULT_ROOT_API_PORT = 0

SERVICE_BUNDLE_FILES = (
    "acknowledgement.py",
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
    rpc_port: int | None = None
    p2p_port: int | None = None
    metrics_port: int | None = None
    wants_validator: bool = False


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
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


def health_ready(url: str) -> bool:
    try:
        response = requests.get(url.rstrip("/") + "/health", timeout=5)
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
    )
    return completed.stdout.strip().splitlines()[-1]


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
        "address": derive_address(private_key_path),
        "rpcURL": rpc_url,
        "signature": build_identity_signature(node_id, node_name, node_type, public_key, private_key_path),
        "wants_validator": bool(wants_validator),
    }
    if node_url:
        payload["node_url"] = node_url
    (node_dir / "node-details.json").write_text(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def render_client_env(api_port: int, rpc_port: int, p2p_port: int, host: str = LOCAL_HOST) -> str:
    return (
        f"FLASK_PORT={api_port}\n"
        f"BESU_PORT={rpc_port}\n"
        f"P2P_PORT={p2p_port}\n"
        f"NODE_URL=http://{host}:{api_port}\n"
        f"BESU_RPC_URL=http://{host}:{rpc_port}\n"
    )


def render_endpoint_env(api_port: int, rpc_url: str, host: str = LOCAL_HOST) -> str:
    return (
        f"FLASK_PORT={api_port}\n"
        f"NODE_URL=http://{host}:{api_port}\n"
        f"BESU_RPC_URL={rpc_url}\n"
    )


def write_client_env(node_dir: Path, api_port: int, rpc_port: int, p2p_port: int, host: str = LOCAL_HOST) -> None:
    (node_dir / ".env").write_text(render_client_env(api_port, rpc_port, p2p_port, host))


def write_endpoint_env(node_dir: Path, api_port: int, rpc_url: str, host: str = LOCAL_HOST) -> None:
    (node_dir / ".env").write_text(render_endpoint_env(api_port, rpc_url, host))


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
        shutil.copy2(SERVICE_BUNDLE_DIR / name, destination_dir / name)


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
    return subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )


def post_registration(root_api_url: str, payload: dict[str, Any], timeout_seconds: float = 180.0) -> dict[str, Any]:
    response = requests.post(
        root_api_url.rstrip("/") + "/register-node",
        json=payload,
        timeout=timeout_seconds,
    )
    if response.status_code not in {200, 409}:
        raise RuntimeError(f"registration failed ({response.status_code}): {response.text}")
    return response.json()


def root_contract_is_deployed(root_dir: Path, env: dict[str, str]) -> bool:
    completed = run(
        ["node", "interact.js", "checkIfDeployed"],
        cwd=root_dir,
        env=env,
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0 and completed.stdout.strip().endswith("true")


def ensure_root_contract_deployed(root_dir: Path, root_rpc_url: str, env: dict[str, str]) -> None:
    if root_contract_is_deployed(root_dir, env):
        return

    smart_dir = root_dir / "smart_contract_deployment"
    prefunded = json.loads((root_dir / "prefunded_keys.json").read_text())
    private_key = prefunded["prefunded_accounts"][0]["private_key"]
    if private_key.startswith("0x"):
        private_key = private_key[2:]

    config_path = smart_dir / "truffle-config.generated.js"
    config_path.write_text(
        "\n".join([
            'const HDWalletProvider = require("@truffle/hdwallet-provider");',
            f'const privateKey = "{private_key}";',
            f'const besuRpcUrl = "{root_rpc_url}";',
            "",
            "module.exports = {",
            "  networks: {",
            "    generated: {",
            "      provider: () => new HDWalletProvider(privateKey, besuRpcUrl),",
            '      network_id: "*",',
            "      gas: 29000000,",
            "      gasPrice: 0,",
            "      confirmations: 0,",
            "      timeoutBlocks: 200,",
            "      skipDryRun: true,",
            "    },",
            "  },",
            "  compilers: {",
            "    solc: {",
            '      version: "0.8.4",',
            "      settings: { optimizer: { enabled: true, runs: 20 } },",
            "    },",
            "  },",
            "};",
            "",
        ])
    )

    run(
        ["npx", "truffle", "migrate", "--network", "generated", "--reset", "--config", str(config_path)],
        cwd=smart_dir,
        env=env,
    )
    shutil.copy2(smart_dir / "build" / "contracts" / "NodeRegistry.json", root_dir / "data" / "NodeRegistry.json")

    if not root_contract_is_deployed(root_dir, env):
        raise RuntimeError("NodeRegistry deployment completed but checkIfDeployed is still false")


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
    root_api_url = f"http://{host}:{root_api_port}"
    chain_proc = None
    service_proc = None
    root_besu_log = logs_dir / "root-besu.log"
    current_rpc_port = root_rpc_port
    current_p2p_port = root_p2p_port
    current_metrics_port = root_metrics_port
    root_env = dict(env)

    ensure_runtime_prereqs()
    for attempt in range(4):
        root_rpc_url = f"http://{host}:{current_rpc_port}"
        root_env.update({
            "ROOT_BESU_RPC_PORT": str(current_rpc_port),
            "ROOT_BESU_P2P_PORT": str(current_p2p_port),
            "ROOT_BESU_METRICS_PORT": str(current_metrics_port),
            "BESU_RPC_URL": root_rpc_url,
        })

        if rpc_ready(root_rpc_url):
            break

        chain_proc = launch_background(
            [str(PYTHON_BIN), "root_blockchain_init.py", "start_blockchain_node", host],
            cwd=root_dir,
            log_path=root_besu_log,
            env=root_env,
        )
        if wait_for_rpc_or_exit(root_rpc_url, chain_proc, root_besu_log, timeout=90, interval=2):
            break

        time.sleep(1)
        detail = tail_file(root_besu_log)
        if attempt < 3 and port_conflict_reported(detail):
            current_rpc_port = allocate_free_port()
            current_p2p_port = allocate_free_port()
            current_metrics_port = allocate_free_port()
            continue

        if detail:
            raise RuntimeError(f"root RPC did not become ready\n{detail}")
        raise RuntimeError("root RPC did not become ready")

    ensure_root_contract_deployed(root_dir, root_rpc_url, root_env)

    if not health_ready(root_api_url):
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

    return {
        "rpc_url": root_rpc_url,
        "api_url": root_api_url,
        "chain_pid": chain_proc.pid if chain_proc else None,
        "service_pid": service_proc.pid if service_proc else None,
    }


def start_service_and_chain(
    *,
    node_dir: Path,
    spec: NodeSpec,
    env: dict[str, str],
    logs_dir: Path,
) -> tuple[int | None, int | None]:
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
    if not wait_for(lambda: health_ready(f"http://{LOCAL_HOST}:{spec.api_port}"), timeout=45, interval=1):
        raise RuntimeError(f"{spec.tier}{spec.ordinal} API did not become healthy after RPC startup")

    return service_proc.pid, chain_proc.pid


def start_service_only(
    *,
    node_dir: Path,
    spec: NodeSpec,
    env: dict[str, str],
    logs_dir: Path,
) -> int | None:
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
    return service_proc.pid


def generate_keys(node_dir: Path, *, chain_backed: bool) -> None:
    if chain_backed:
        run([str(PYTHON_BIN), "client_blockchain_init.py", "generate_keys"], cwd=node_dir)
    else:
        run([str(PYTHON_BIN), "end_node_initialization.py", "generate_keys"], cwd=node_dir)


def sanitize_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-") or f"scenario-{int(time.time())}"


def make_node_specs(
    *,
    fog_count: int,
    edge_count: int,
    endpoint_count: int,
    endpoint_role: str,
    scenario_name: str,
    scenario_dir: Path,
) -> list[NodeSpec]:
    specs: list[NodeSpec] = []
    stamp = int(time.time())

    for idx in range(1, fog_count + 1):
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
                rpc_port=allocate_free_port(),
                p2p_port=allocate_free_port(),
                metrics_port=allocate_free_port(),
                wants_validator=(idx == 1),
            )
        )

    for idx in range(1, edge_count + 1):
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
                rpc_port=allocate_free_port(),
                p2p_port=allocate_free_port(),
                metrics_port=allocate_free_port(),
                wants_validator=False,
            )
        )

    for idx in range(1, endpoint_count + 1):
        specs.append(
            NodeSpec(
                tier="endpoint",
                ordinal=idx,
                node_type=endpoint_role,
                name=f"{endpoint_role}{idx}",
                node_id=f"{scenario_name.upper()}-END-{idx:03d}",
                signature_seed=f"{scenario_name}-endpoint-{idx}-{stamp}",
                directory=str(scenario_dir / f"endpoint{idx}"),
                api_port=allocate_free_port(),
                wants_validator=False,
            )
        )

    return specs


def prepare_node(
    spec: NodeSpec,
    root_dir: Path,
    root_enode: str,
    endpoint_role: str,
    *,
    root_rpc_url: str,
) -> tuple[Path, dict[str, Any]]:
    node_dir = Path(spec.directory)
    template_dir = {
        "fog": CLIENT_TEMPLATE_DIR,
        "edge": CLIENT_TEMPLATE_DIR,
        "endpoint": ENDPOINT_TEMPLATE_DIR,
    }[spec.tier]

    prepare_client_dir(template_dir, node_dir, chain_backed=spec.tier in {"fog", "edge"})
    generate_keys(node_dir, chain_backed=spec.tier in {"fog", "edge"})

    if spec.tier in {"fog", "edge"}:
        copy_root_chain_material(root_dir, node_dir, root_enode)
        write_client_env(node_dir, spec.api_port or 0, spec.rpc_port or 0, spec.p2p_port or 0)
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
        shutil.copy2(root_node_registry_artifact(root_dir), node_dir / "data" / "NodeRegistry.json")
        shutil.copy2(root_dir / "prefunded_keys.json", node_dir / "prefunded_keys.json")
        write_endpoint_env(node_dir, spec.api_port or 0, root_rpc_url)
        payload = build_registration_payload(
            node_dir=node_dir,
            node_id=spec.node_id,
            node_name=spec.name,
            node_type=endpoint_role,
            rpc_url=root_rpc_url,
            node_url=f"http://{LOCAL_HOST}:{spec.api_port}",
            wants_validator=False,
        )

    return node_dir, payload


def terminate_pid(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision a BlockCap runtime topology with variable node counts")
    parser.add_argument("--cloud", type=int, default=1)
    parser.add_argument("--fog", type=int, default=2)
    parser.add_argument("--edge", type=int, default=2)
    parser.add_argument("--endpoint", type=int, default=3)
    parser.add_argument("--endpoint-role", choices=["Sensor", "Actuator"], default="Sensor")
    parser.add_argument("--scenario", default=f"scenario-{int(time.time())}")
    parser.add_argument("--root-api-port", type=int, default=DEFAULT_ROOT_API_PORT)
    parser.add_argument("--root-rpc-port", type=int, default=DEFAULT_ROOT_RPC_PORT)
    parser.add_argument("--root-p2p-port", type=int, default=DEFAULT_ROOT_P2P_PORT)
    parser.add_argument("--root-metrics-port", type=int, default=DEFAULT_ROOT_METRICS_PORT)
    parser.add_argument("--root-dir", default=str(ROOT_TEMPLATE_DIR))
    parser.add_argument("--host", default=LOCAL_HOST)
    args = parser.parse_args()

    if args.cloud != 1:
        raise SystemExit("This runner currently supports exactly one cloud/root node")
    if not PYTHON_BIN.exists():
        raise SystemExit(f"Python virtualenv not found at {PYTHON_BIN}")

    scenario_name = sanitize_name(args.scenario)
    scenario_dir = GENERATED_ROOT / scenario_name
    if scenario_dir.exists():
        raise SystemExit(f"Scenario directory already exists: {scenario_dir}")
    scenario_dir.mkdir(parents=True, exist_ok=False)
    logs_dir = scenario_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    root_template_dir = Path(args.root_dir).resolve()
    root_dir = scenario_dir / "root"
    prepare_root_dir(root_template_dir, root_dir)
    root_api_port = args.root_api_port or allocate_free_port()
    root_rpc_port = args.root_rpc_port or allocate_free_port()
    root_p2p_port = args.root_p2p_port or allocate_free_port()
    root_metrics_port = args.root_metrics_port or allocate_free_port()

    base_env = os.environ.copy()
    base_env.update({
        "REAL_INTERACT": "1",
        "ORCH_TRACE": base_env.get("ORCH_TRACE", "0"),
        "FROM_IDX": base_env.get("FROM_IDX", "0"),
        "PYTHONUNBUFFERED": "1",
    })
    root_env = dict(base_env)
    root_env.update({
        "ROOT_BESU_RPC_PORT": str(root_rpc_port),
        "ROOT_BESU_P2P_PORT": str(root_p2p_port),
        "ROOT_BESU_METRICS_PORT": str(root_metrics_port),
        "BESU_RPC_URL": f"http://{args.host}:{root_rpc_port}",
    })

    started_pids: list[int] = []
    try:
        root_info = ensure_root_started(
            root_dir,
            root_api_port,
            root_rpc_port,
            root_p2p_port=root_p2p_port,
            root_metrics_port=root_metrics_port,
            host=args.host,
            env=root_env,
            logs_dir=logs_dir,
        )
        if root_info["chain_pid"]:
            started_pids.append(root_info["chain_pid"])
        if root_info["service_pid"]:
            started_pids.append(root_info["service_pid"])

        root_enode = json_rpc(root_info["rpc_url"], "admin_nodeInfo", [])["enode"]
        specs = make_node_specs(
            fog_count=args.fog,
            edge_count=args.edge,
            endpoint_count=args.endpoint,
            endpoint_role=args.endpoint_role,
            scenario_name=scenario_name,
            scenario_dir=scenario_dir,
        )

        manifest: dict[str, Any] = {
            "scenario": scenario_name,
            "root": {
                "directory": str(root_dir),
                "api_url": root_info["api_url"],
                "rpc_url": root_info["rpc_url"],
                "chain_pid": root_info["chain_pid"],
                "service_pid": root_info["service_pid"],
                "node_details": json.loads((root_dir / "node-details.json").read_text()) if (root_dir / "node-details.json").exists() else {},
            },
            "nodes": [],
        }

        for spec in specs:
            node_dir, payload = prepare_node(
                spec,
                root_dir,
                root_enode,
                args.endpoint_role,
                root_rpc_url=root_info["rpc_url"],
            )
            node_record = {
                **asdict(spec),
                "directory": str(node_dir),
                "payload": payload,
                "api_url": f"http://{LOCAL_HOST}:{spec.api_port}" if spec.api_port else None,
                "rpc_url": f"http://{LOCAL_HOST}:{spec.rpc_port}" if spec.rpc_port else None,
                "registration": None,
                "api_pid": None,
                "chain_pid": None,
            }

            if spec.tier in {"fog", "edge"}:
                api_pid, chain_pid = start_service_and_chain(node_dir=node_dir, spec=spec, env=base_env, logs_dir=logs_dir)
                node_record["api_pid"] = api_pid
                node_record["chain_pid"] = chain_pid
                started_pids.extend(pid for pid in (api_pid, chain_pid) if pid)

            node_record["registration"] = post_registration(root_info["api_url"], payload)
            if spec.tier == "endpoint":
                api_pid = start_service_only(node_dir=node_dir, spec=spec, env=base_env, logs_dir=logs_dir)
                node_record["api_pid"] = api_pid
                started_pids.extend(pid for pid in (api_pid,) if pid)
            manifest["nodes"].append(node_record)

        output_path = scenario_dir / "topology.json"
        output_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

        print(json.dumps({
            "scenario": scenario_name,
            "topology_file": str(output_path),
            "root_api_url": root_info["api_url"],
            "nodes_started": len(manifest["nodes"]),
        }, indent=2))
    except Exception:
        for pid in reversed(started_pids):
            terminate_pid(pid)
        raise


if __name__ == "__main__":
    main()
