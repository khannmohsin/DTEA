import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from eth_keys import keys
from eth_utils import keccak

from tef_metrics import find_repo_root


class InfrastructureController:
    def __init__(self, start_path: str | os.PathLike[str]):
        self._repo_root = find_repo_root(str(start_path))
        self._runtime_generated = self._repo_root / "runtime" / "generated"
        self._python_bin = self._repo_root / ".venv" / "bin" / "python"
        self._run_topology_script = self._repo_root / "scripts" / "run_topology.py"
        self._pretty_log_script = self._repo_root / "Node_root" / "pretty_log_stream.py"
        self._lock = threading.RLock()
        self._current_job: dict[str, Any] | None = None

    def _default_scenario(self, scenario: str | None = None) -> str | None:
        if scenario:
            return scenario
        with self._lock:
            if (
                self._current_job
                and self._current_job.get("status") == "running"
                and self._current_job.get("scenario")
            ):
                return str(self._current_job["scenario"])
        self._runtime_generated.mkdir(parents=True, exist_ok=True)
        candidates = [entry for entry in self._runtime_generated.iterdir() if entry.is_dir()]
        if not candidates:
            return None
        scored: list[tuple[tuple[int, int, int, float], str]] = []
        for entry in candidates:
            manifest = self._read_manifest(self._manifest_path(entry.name))
            if not manifest:
                continue
            summary = self._scenario_summary(entry.name, manifest)
            score = (
                1 if summary.get("running") else 0,
                len(summary.get("alive_pids") or []),
                int(summary.get("node_count") or 0),
                entry.stat().st_mtime,
            )
            scored.append((score, entry.name))
        running = [item for item in scored if item[0][0] == 1]
        if running:
            running.sort(reverse=True)
            return running[0][1]
        return None

    def _scenario_dir(self, scenario: str) -> Path:
        return self._runtime_generated / scenario

    def _manifest_path(self, scenario: str) -> Path:
        return self._scenario_dir(scenario) / "topology.json"

    def _logs_dir(self, scenario: str) -> Path:
        return self._scenario_dir(scenario) / "logs"

    def _read_manifest(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    def _write_manifest(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))

    def _pid_alive(self, pid: int | None) -> bool:
        if not pid:
            return False
        try:
            os.kill(int(pid), 0)
            return True
        except Exception:
            return False

    def _terminate_pid(self, pid: int | None) -> None:
        if not pid:
            return
        try:
            os.killpg(int(pid), signal.SIGTERM)
            return
        except Exception:
            pass
        try:
            os.kill(int(pid), signal.SIGTERM)
        except Exception:
            return

    def _port_from_url(self, url: str | None) -> int | None:
        if not url:
            return None
        try:
            return int(urlparse(url).port or 0) or None
        except Exception:
            return None

    def _listening_pids_for_port(self, port: int | None) -> list[int]:
        if not port:
            return []
        try:
            proc = subprocess.run(
                ["lsof", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-n", "-P", "-t"],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return []
        if proc.returncode not in {0, 1}:
            return []
        pids = []
        for line in (proc.stdout or "").splitlines():
            try:
                pids.append(int(line.strip()))
            except Exception:
                continue
        return pids

    def _pid_command(self, pid: int | None) -> str:
        if not pid:
            return ""
        try:
            proc = subprocess.run(
                ["ps", "-p", str(int(pid)), "-o", "command="],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                return ""
            return (proc.stdout or "").strip()
        except Exception:
            return ""

    def _directory_process_pids(self, directory: Path | None) -> list[int]:
        if not directory:
            return []
        try:
            proc = subprocess.run(
                ["ps", "-axo", "pid=,command="],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return []
        rows: list[int] = []
        needle = str(directory)
        for line in (proc.stdout or "").splitlines():
            text = line.strip()
            if not text:
                continue
            parts = text.split(None, 1)
            if len(parts) != 2:
                continue
            try:
                pid = int(parts[0])
            except Exception:
                continue
            cmd = parts[1]
            if needle in cmd and self._pid_alive(pid):
                rows.append(pid)
        return sorted(set(rows))

    def _pid_matches_directory(self, pid: int | None, directory: Path | None) -> bool:
        if not pid or not directory:
            return False
        cmd = self._pid_command(pid)
        if not cmd:
            return False
        return str(directory) in cmd

    def _health_reachable(self, url: str | None) -> bool:
        if not url:
            return False
        try:
            response = requests.get(url.rstrip("/") + "/health", timeout=2)
            return bool(response.ok)
        except Exception:
            return False

    def _page_reachable(self, url: str | None) -> bool:
        if not url:
            return False
        try:
            response = requests.get(url.rstrip("/") + "/status", timeout=2)
            return bool(response.ok)
        except Exception:
            return False

    def _rpc_reachable(self, url: str | None) -> bool:
        if not url:
            return False
        try:
            response = requests.post(
                url,
                json={"jsonrpc": "2.0", "id": 1, "method": "net_version", "params": []},
                timeout=2,
            )
            payload = response.json()
            return response.ok and "error" not in payload and bool(payload.get("result"))
        except Exception:
            return False

    def _status_dot(self, status: str, label: str, detail: str | None = None) -> dict[str, Any]:
        return {"status": status, "label": label, "detail": detail or ""}

    def _process_status(
        self,
        *,
        manifest_pid: int | None,
        observed_pid: int | None,
        reachable: bool,
        kind: str,
        port: int | None = None,
    ) -> dict[str, Any]:
        observed_alive = self._pid_alive(observed_pid)
        manifest_alive = self._pid_alive(manifest_pid)
        if observed_pid and manifest_pid and int(observed_pid) != int(manifest_pid):
            pid_status = "stale_manifest_pid"
        elif observed_pid and not manifest_pid:
            pid_status = "discovered_pid"
        elif manifest_alive:
            pid_status = "matched_manifest_pid"
        else:
            pid_status = "missing_pid"

        if reachable:
            status = "ok"
            label = f"{kind} ready"
        elif observed_alive or manifest_alive:
            status = "running"
            label = f"{kind} starting"
        else:
            status = "down"
            label = f"{kind} stopped"

        payload = self._status_dot(status, label)
        payload.update({
            "manifest_pid": int(manifest_pid) if manifest_pid else None,
            "observed_pid": int(observed_pid) if observed_pid else None,
            "observed_running": bool(reachable or observed_alive or manifest_alive),
            "pid_status": pid_status,
            "port": int(port) if port else None,
        })
        return payload

    def _registration_status(self, node: dict[str, Any]) -> dict[str, Any]:
        registration = node.get("registration")
        if registration is None:
            return self._status_dot("pending", "registration pending")
        if registration.get("ok"):
            status = registration.get("status") or "registered"
            return self._status_dot("ok", status.replace("_", " "))
        return self._status_dot("error", "registration failed", registration.get("error") or registration.get("detail"))

    def _root_process_commands(self, scenario_dir: Path, root: dict[str, Any]) -> dict[str, str]:
        root_dir = Path(root.get("directory") or scenario_dir / "root")
        rpc_url = root.get("rpc_url") or "http://127.0.0.1:8545"
        rpc_port = rpc_url.rsplit(":", 1)[-1]
        api_url = root.get("api_url") or "http://127.0.0.1:5600"
        api_port = api_url.rsplit(":", 1)[-1]
        return {
            "chain": (
                f"REAL_INTERACT=1 BESU_RPC_URL={rpc_url} "
                f"ROOT_BESU_RPC_PORT={rpc_port} {self._python_bin} "
                f"{root_dir / 'root_blockchain_init.py'} start_blockchain_node 127.0.0.1"
            ),
            "api": (
                f"REAL_INTERACT=1 BESU_RPC_URL={rpc_url} {self._python_bin} "
                f"{root_dir / 'orchestration_service.py'} --host 0.0.0.0 --port {api_port} --repo-root {root_dir}"
            ),
        }

    def _node_process_commands(self, node: dict[str, Any], root_rpc_url: str | None) -> dict[str, str]:
        node_dir = Path(node.get("directory") or ".")
        api_url = node.get("api_url")
        api_port = api_url.rsplit(":", 1)[-1] if api_url else "0"
        rpc_url = node.get("rpc_url") or root_rpc_url or "http://127.0.0.1:8545"
        rpc_port = rpc_url.rsplit(":", 1)[-1] if rpc_url else "0"
        p2p_port = str(node.get("p2p_port") or 0)
        commands = {
            "api": (
                f"REAL_INTERACT=1 BESU_RPC_URL={rpc_url} {self._python_bin} "
                f"{node_dir / 'orchestration_service.py'} --host 0.0.0.0 --port {api_port} --repo-root {node_dir}"
            )
        }
        if node.get("tier") in {"fog", "edge"}:
            commands["chain"] = (
                f"REAL_INTERACT=1 BESU_RPC_URL={rpc_url} {self._python_bin} "
                f"{node_dir / 'client_blockchain_init.py'} start_blockchain_node {p2p_port} {rpc_port} 127.0.0.1"
            )
        return commands

    def _log_paths_for_root(self, scenario: str) -> dict[str, Path]:
        logs_dir = self._logs_dir(scenario)
        return {
            "api": logs_dir / "root-api.log",
            "chain": logs_dir / "root-besu.log",
        }

    def _log_paths_for_node(self, scenario: str, tier: str, ordinal: int | None) -> dict[str, Path]:
        if tier == "cloud":
            return self._log_paths_for_root(scenario)
        logs_dir = self._logs_dir(scenario)
        suffix = f"{tier}-{ordinal}"
        paths = {"api": logs_dir / f"{suffix}-api.log"}
        if tier in {"fog", "edge"}:
            paths["chain"] = logs_dir / f"{suffix}-besu.log"
        return paths

    def _job_node_log_fallback(self, *, scenario: str, node_key: str, process: str, lines: int) -> dict[str, Any] | None:
        with self._lock:
            job = dict(self._current_job or {})
        if not job or str(job.get("scenario") or "") != str(scenario):
            return None
        if process not in {"api", "chain"}:
            return None

        if node_key == "root":
            root_logs = self._log_paths_for_root(scenario)
            root_dir = self._scenario_dir(scenario) / "root"
            rpc_url = "http://127.0.0.1:8545"
            topo = dict(job.get("topology_request") or {})
            host = str(topo.get("host") or "127.0.0.1")
            log_path = root_logs.get(process)
            if process == "api":
                api_port = 5600
                command = (
                    f"REAL_INTERACT=1 BESU_RPC_URL={rpc_url} {self._python_bin} "
                    f"{root_dir / 'orchestration_service.py'} --host 0.0.0.0 --port {api_port} --repo-root {root_dir}"
                )
            else:
                command = (
                    f"REAL_INTERACT=1 BESU_RPC_URL={rpc_url} {self._python_bin} "
                    f"{root_dir / 'root_blockchain_init.py'} start_blockchain_node {host}"
                )
        else:
            try:
                tier, ordinal_text = str(node_key).split("-", 1)
                ordinal = int(ordinal_text)
            except Exception:
                return None
            if process == "chain" and tier not in {"fog", "edge"}:
                return None
            log_path = self._log_paths_for_node(scenario, tier, ordinal).get(process)
            node_dir = self._scenario_dir(scenario) / f"{tier}{ordinal}"
            env_path = node_dir / ".env"
            api_port = "0"
            rpc_url = "http://127.0.0.1:8545"
            rpc_port = "0"
            p2p_port = "0"
            if env_path.exists():
                env_lines = {}
                for line in env_path.read_text(errors="replace").splitlines():
                    if "=" in line:
                        key, value = line.split("=", 1)
                        env_lines[key.strip()] = value.strip()
                api_port = env_lines.get("FLASK_PORT", api_port)
                rpc_port = env_lines.get("BESU_PORT", rpc_port)
                p2p_port = env_lines.get("P2P_PORT", p2p_port)
                rpc_url = env_lines.get("ROOT_RPC_URL") or env_lines.get("BESU_RPC_URL") or rpc_url
            if process == "api":
                command = (
                    f"REAL_INTERACT=1 BESU_RPC_URL={rpc_url} {self._python_bin} "
                    f"{node_dir / 'orchestration_service.py'} --host 0.0.0.0 --port {api_port} --repo-root {node_dir}"
                )
            else:
                command = (
                    f"REAL_INTERACT=1 BESU_RPC_URL={rpc_url} {self._python_bin} "
                    f"{node_dir / 'client_blockchain_init.py'} start_blockchain_node {p2p_port} {rpc_port} 127.0.0.1"
                )

        if not log_path:
            return None
        return {
            "selected_scenario": scenario,
            "node_key": node_key,
            "process": process,
            "lines": lines,
            "exists": bool(log_path.exists() and log_path.is_file()),
            "path": str(log_path),
            "content": self._tail_log(log_path, lines) if log_path.exists() and log_path.is_file() else "",
            "command": command,
        }

    def _file_updated_at_ms(self, path: Path) -> int | None:
        if not path.exists():
            return None
        try:
            return int(path.stat().st_mtime * 1000)
        except Exception:
            return None

    def _tail_log(self, path: Path, lines: int) -> str:
        if not path.exists():
            return ""
        content = path.read_text(errors="replace").splitlines()
        return "\n".join(content[-lines:])

    def _launch_logged_command(self, *, command: str, cwd: Path, log_path: Path) -> int:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(log_path, "a", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                command,
                cwd=cwd,
                env=os.environ.copy(),
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                shell=True,
                executable="/bin/zsh",
                start_new_session=True,
            )
        finally:
            handle.close()
        return int(proc.pid)

    def _derive_address(self, private_key_path: Path) -> str | None:
        try:
            completed = subprocess.run(
                [
                    "besu",
                    "public-key",
                    "export-address",
                    f"--node-private-key-file={private_key_path}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return None
        if completed.returncode != 0:
            return None
        lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
        return lines[-1] if lines else None

    def _build_identity_signature(
        self,
        *,
        node_id: str,
        node_name: str,
        node_type: str,
        public_key: str,
        private_key_path: Path,
    ) -> str | None:
        try:
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
        except Exception:
            return None

    def _rpc_node_info(self, rpc_url: str | None) -> dict[str, Any]:
        if not rpc_url:
            return {}
        try:
            response = requests.post(
                rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "admin_nodeInfo", "params": []},
                timeout=2,
            )
            payload = response.json()
        except Exception:
            return {}
        if not response.ok or payload.get("error"):
            return {}
        result = payload.get("result")
        return result if isinstance(result, dict) else {}

    def _root_runtime_details(self, scenario_dir: Path, root: dict[str, Any]) -> dict[str, Any]:
        details = dict(root.get("node_details") or {})
        root_dir = Path(root.get("directory") or scenario_dir / "root")
        details_path = root_dir / "node-details.json"
        if details_path.exists():
            try:
                details.update(json.loads(details_path.read_text()))
            except Exception:
                pass

        private_key_path = root_dir / "data" / "key.priv"
        public_key_path = root_dir / "data" / "key.pub"
        if private_key_path.exists() and public_key_path.exists():
            node_id = str(details.get("node_id") or "CLOUD01")
            node_name = str(details.get("node_name") or "Root Cloud")
            node_type = str(details.get("node_type") or "Cloud")
            public_key = str(details.get("public_key") or public_key_path.read_text().strip())
            details.setdefault("node_id", node_id)
            details.setdefault("node_name", node_name)
            details.setdefault("node_type", node_type)
            details.setdefault("public_key", public_key)
            details.setdefault("rpcURL", root.get("rpc_url"))
            details.setdefault("node_url", root.get("api_url"))
            if not details.get("address"):
                details["address"] = self._derive_address(private_key_path)
            if not details.get("signature"):
                details["signature"] = self._build_identity_signature(
                    node_id=node_id,
                    node_name=node_name,
                    node_type=node_type,
                    public_key=public_key,
                    private_key_path=private_key_path,
                )
            if details.get("signature"):
                try:
                    details_path.write_text(json.dumps(details, indent=2, sort_keys=True))
                except Exception:
                    pass

        p2p_port = root.get("p2p_port")
        node_info = self._rpc_node_info(root.get("rpc_url"))
        if not p2p_port:
            ports = node_info.get("ports") or {}
            p2p_port = ports.get("discovery") or ports.get("listener")
            if not p2p_port:
                enode = str(node_info.get("enode") or "")
                parsed = urlparse(enode.replace("enode://", "http://", 1)) if enode.startswith("enode://") else None
                p2p_port = parsed.port if parsed else None
        if p2p_port and not root.get("p2p_port"):
            root["p2p_port"] = int(p2p_port)
            manifest_path = self._manifest_path(str(root.get("scenario") or scenario_dir.name))
            manifest = self._read_manifest(manifest_path) or {}
            if manifest.get("root") is not None:
                manifest["root"] = dict(manifest.get("root") or {})
                manifest["root"]["p2p_port"] = int(p2p_port)
                if details:
                    manifest["root"]["node_details"] = details
                self._write_manifest(manifest_path, manifest)
        return details

    def _graph_payload(self, node_views: list[dict[str, Any]]) -> dict[str, Any]:
        lane_order = ["cloud", "fog", "edge", "endpoint"]
        lane_map = {lane: idx for idx, lane in enumerate(lane_order)}
        by_lane: dict[str, list[dict[str, Any]]] = {lane: [] for lane in lane_order}
        for node in node_views:
            tier = str(node.get("tier") or "endpoint")
            if tier not in by_lane:
                by_lane[tier] = []
            by_lane[tier].append(node)

        graph_nodes = []
        index_lookup: dict[str, list[str]] = {}
        for lane in lane_order:
            lane_nodes = sorted(by_lane.get(lane, []), key=lambda item: int(item.get("ordinal") or 0))
            index_lookup[lane] = [str(node.get("key")) for node in lane_nodes]
            count = max(1, len(lane_nodes))
            for idx, node in enumerate(lane_nodes):
                graph_nodes.append({
                    "key": node.get("key"),
                    "label": node.get("name"),
                    "tier": lane,
                    "status": node.get("summary_status"),
                    "x": round((idx + 1) / (count + 1), 3),
                    "y": lane_map.get(lane, len(lane_order) - 1),
                })

        edges = []
        for fog_key in index_lookup.get("fog", []):
            edges.append({"from": "root", "to": fog_key})
        fog_keys = index_lookup.get("fog", []) or ["root"]
        for idx, edge_key in enumerate(index_lookup.get("edge", [])):
            edges.append({"from": fog_keys[idx % len(fog_keys)], "to": edge_key})
        edge_keys = index_lookup.get("edge", []) or fog_keys or ["root"]
        for idx, endpoint_key in enumerate(index_lookup.get("endpoint", [])):
            edges.append({"from": edge_keys[idx % len(edge_keys)], "to": endpoint_key})
        return {
            "lanes": [{"key": lane, "label": lane.title()} for lane in lane_order],
            "nodes": graph_nodes,
            "edges": edges,
        }

    def _probe_statuses(self, manifest: dict[str, Any], selected: str) -> dict[tuple[str, str], dict[str, Any]]:
        root = manifest.get("root", {})
        root_dir = Path(root.get("directory") or self._scenario_dir(selected) / "root")
        probes: list[tuple[str, str, int | None, str | None, str, Path | None]] = [
            ("root", "api", root.get("service_pid"), root.get("api_url"), "API", root_dir),
            ("root", "chain", root.get("chain_pid"), root.get("rpc_url"), "Chain", root_dir),
        ]
        for node in manifest.get("nodes", []):
            tier = str(node.get("tier") or "unknown")
            ordinal = int(node.get("ordinal") or 0)
            key = f"{tier}-{ordinal}"
            node_dir = Path(node.get("directory") or self._scenario_dir(selected) / key)
            probes.append((key, "api", node.get("api_pid"), node.get("api_url"), "API", node_dir))
            if tier in {"fog", "edge"}:
                probes.append((key, "chain", node.get("chain_pid"), node.get("rpc_url"), "Chain", node_dir))

        def do_probe(item: tuple[str, str, int | None, str | None, str, Path | None]) -> tuple[tuple[str, str], dict[str, Any]]:
            node_key, process, pid, url, kind, directory = item
            port = self._port_from_url(url)
            observed_candidates = self._listening_pids_for_port(port)
            observed_pid = next((candidate for candidate in observed_candidates if self._pid_matches_directory(candidate, directory)), None)
            if observed_pid is None and observed_candidates:
                observed_pid = observed_candidates[0]
            if process == "api":
                reachable = self._health_reachable(url)
            else:
                reachable = self._rpc_reachable(url)
            return (node_key, process), self._process_status(
                manifest_pid=pid,
                observed_pid=observed_pid,
                reachable=reachable,
                kind=kind,
                port=port,
            )

        results: dict[tuple[str, str], dict[str, Any]] = {}
        max_workers = min(16, max(2, len(probes)))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"infractl-{selected}") as executor:
            for key, status in executor.map(do_probe, probes):
                results[key] = status
        return results

    def refresh_topology(self, scenario: str, *, persist: bool = True) -> dict[str, Any]:
        manifest_path = self._manifest_path(scenario)
        manifest = self._read_manifest(manifest_path)
        if not manifest:
            raise RuntimeError("scenario_not_found")

        probe_statuses = self._probe_statuses(manifest, scenario)
        changed = False

        root = manifest.get("root", {})
        root_api = probe_statuses.get(("root", "api")) or {}
        root_chain = probe_statuses.get(("root", "chain")) or {}
        if root_api.get("observed_pid") and root_api.get("observed_pid") != root.get("service_pid"):
            root["service_pid"] = root_api["observed_pid"]
            changed = True
        elif not root_api.get("observed_running") and root.get("service_pid") is not None:
            root["service_pid"] = None
            changed = True
        if root_chain.get("observed_pid") and root_chain.get("observed_pid") != root.get("chain_pid"):
            root["chain_pid"] = root_chain["observed_pid"]
            changed = True
        elif not root_chain.get("observed_running") and root.get("chain_pid") is not None:
            root["chain_pid"] = None
            changed = True

        for node in manifest.get("nodes", []):
            key = f"{node.get('tier')}-{int(node.get('ordinal') or 0)}"
            api_status = probe_statuses.get((key, "api")) or {}
            chain_status = probe_statuses.get((key, "chain")) or {}
            control_url = node.get("control_url")
            control_port = self._port_from_url(control_url)
            control_candidates = self._listening_pids_for_port(control_port)
            control_pid = next((candidate for candidate in control_candidates if self._pid_matches_directory(candidate, Path(node.get("directory") or self._scenario_dir(scenario) / key))), None)
            if control_pid is None and control_candidates:
                control_pid = control_candidates[0]
            if api_status.get("observed_pid") and api_status.get("observed_pid") != node.get("api_pid"):
                node["api_pid"] = api_status["observed_pid"]
                changed = True
            elif not api_status.get("observed_running") and node.get("api_pid") is not None:
                node["api_pid"] = None
                changed = True
            if chain_status.get("observed_pid") and chain_status.get("observed_pid") != node.get("chain_pid"):
                node["chain_pid"] = chain_status["observed_pid"]
                changed = True
            elif chain_status and not chain_status.get("observed_running") and node.get("chain_pid") is not None:
                node["chain_pid"] = None
                changed = True
            if control_pid and control_pid != node.get("control_pid"):
                node["control_pid"] = control_pid
                changed = True
            elif (not self._page_reachable(control_url)) and node.get("control_pid") is not None and not self._pid_alive(node.get("control_pid")):
                node["control_pid"] = None
                changed = True

        if changed and persist:
            self._write_manifest(manifest_path, manifest)

        return {
            "scenario": scenario,
            "manifest_updated": changed,
            "probe_statuses": probe_statuses,
        }

    def shell_grid(self, *, scenario: str | None = None, lines: int = 80) -> dict[str, Any]:
        details = self.scenario_details(scenario)
        scenario_name = details.get("selected_scenario")
        scenario_payload = details.get("scenario")
        if not scenario_name or not scenario_payload:
            return {"selected_scenario": scenario_name, "shells": []}

        shells = []
        for node in scenario_payload.get("node_views") or []:
            for process, path_str in sorted((node.get("logs") or {}).items()):
                path = Path(path_str)
                process_state = (node.get("processes") or {}).get(process) or {}
                shells.append({
                    "node_key": node.get("key"),
                    "node_name": node.get("name"),
                    "tier": node.get("tier"),
                    "process": process,
                    "command": (node.get("commands") or {}).get(process),
                    "content": self._tail_log(path, lines),
                    "running": process_state.get("status") in {"ok", "running"},
                    "status": process_state.get("status"),
                    "label": process_state.get("label"),
                    "updated_at_ms": self._file_updated_at_ms(path),
                })
        return {"selected_scenario": scenario_name, "shells": shells}

    def _scenario_summary(self, scenario: str, manifest: dict[str, Any]) -> dict[str, Any]:
        root = manifest.get("root", {})
        nodes = manifest.get("nodes", [])
        pids = [root.get("chain_pid"), root.get("service_pid")]
        for node in nodes:
            pids.extend([node.get("control_pid"), node.get("api_pid"), node.get("chain_pid")])
        alive = [int(pid) for pid in pids if self._pid_alive(pid)]
        return {
            "scenario": scenario,
            "exists": True,
            "running": bool(alive),
            "manifest_path": str(self._manifest_path(scenario)),
            "root_api_url": root.get("api_url"),
            "root_rpc_url": root.get("rpc_url"),
            "alive_pids": alive,
            "node_count": len(nodes),
        }

    def scenario_details(self, scenario: str | None = None, *, active_only: bool = False) -> dict[str, Any]:
        selected = self._default_scenario(scenario)
        if not selected:
            return {"selected_scenario": None, "scenario": None}

        manifest_path = self._manifest_path(selected)
        manifest = self._read_manifest(manifest_path)
        if not manifest:
            return {"selected_scenario": selected, "scenario": None}

        self.refresh_topology(selected, persist=True)
        manifest = self._read_manifest(manifest_path) or manifest

        scenario_dir = self._scenario_dir(selected)
        root = manifest.get("root", {})
        root_node_details = self._root_runtime_details(scenario_dir, root)
        root_api_url = root.get("api_url")
        root_rpc_url = root.get("rpc_url")
        probe_statuses = self._probe_statuses(manifest, selected)

        node_views: list[dict[str, Any]] = []
        root_logs = self._log_paths_for_root(selected)
        root_processes = {
            "api": probe_statuses.get(("root", "api")) or self._status_dot("down", "API stopped"),
            "chain": probe_statuses.get(("root", "chain")) or self._status_dot("down", "Chain stopped"),
            "registration": self._status_dot("ok", "root active"),
        }
        node_views.append({
            "key": "root",
            "name": root_node_details.get("node_name") or "Root Cloud",
            "node_id": root_node_details.get("node_id") or "CLOUD01",
            "signature": root_node_details.get("signature"),
            "tier": "cloud",
            "ordinal": 0,
            "api_url": root_api_url,
            "rpc_url": root_rpc_url,
            "p2p_port": root.get("p2p_port"),
            "directory": root.get("directory"),
            "processes": root_processes,
            "summary_status": "ok" if all(item["status"] == "ok" for item in root_processes.values()) else "running",
            "summary_label": "ready" if all(item["status"] == "ok" for item in root_processes.values()) else "starting",
            "commands": self._root_process_commands(scenario_dir, root),
            "logs": {kind: str(path) for kind, path in root_logs.items()},
            "control_url": root_api_url.rstrip("/") + "/control" if root_api_url else None,
            "dashboard_url": root_api_url.rstrip("/") + "/dashboard" if root_api_url else None,
            "runtime_backend": root.get("runtime_backend") or "native",
        })

        visible_nodes = [
            node
            for node in (manifest.get("nodes") or [])
            if not active_only or str(node.get("lifecycle_status") or "active") == "active"
        ]

        for node in visible_nodes:
            tier = str(node.get("tier") or "unknown")
            ordinal = int(node.get("ordinal") or 0)
            api_url = node.get("api_url")
            rpc_url = node.get("rpc_url")
            logs = self._log_paths_for_node(selected, tier, ordinal)
            control_url = node.get("control_url") or (f"http://127.0.0.1:{node.get('control_port')}" if node.get("control_port") else None)
            control_port = self._port_from_url(control_url)
            control_candidates = self._listening_pids_for_port(control_port)
            control_pid = next((candidate for candidate in control_candidates if self._pid_matches_directory(candidate, Path(node.get("directory") or self._scenario_dir(selected) / f"{tier}-{ordinal}"))), None)
            if control_pid is None and control_candidates:
                control_pid = control_candidates[0]
            control_status = self._process_status(
                manifest_pid=node.get("control_pid"),
                observed_pid=control_pid,
                reachable=self._page_reachable(control_url),
                kind="Node page",
                port=control_port,
            ) if control_url else self._status_dot("not_applicable", "no node page")
            processes = {
                "api": probe_statuses.get((f"{tier}-{ordinal}", "api")) or self._status_dot("down", "API stopped"),
                "registration": self._registration_status(node),
            }
            if tier in {"fog", "edge"}:
                processes["chain"] = probe_statuses.get((f"{tier}-{ordinal}", "chain")) or self._status_dot("down", "Chain stopped")
            else:
                processes["chain"] = self._status_dot("not_applicable", "no local chain")

            if processes["registration"]["status"] == "error":
                summary_status = "error"
                summary_label = "registration failed"
            elif all(item["status"] in {"ok", "not_applicable"} for item in processes.values()):
                summary_status = "ok"
                summary_label = "ready"
            elif any(item["status"] == "running" for item in processes.values()) or processes["registration"]["status"] == "pending":
                summary_status = "running"
                summary_label = "provisioning"
            else:
                summary_status = "down"
                summary_label = "stopped"

            node_views.append({
                "key": f"{tier}-{ordinal}",
                "name": node.get("name") or f"{tier.title()} {ordinal}",
                "node_id": node.get("node_id"),
                "signature": (node.get("payload") or {}).get("signature"),
                "tier": tier,
                "ordinal": ordinal,
                "api_url": api_url,
                "rpc_url": rpc_url,
                "p2p_port": node.get("p2p_port"),
                "directory": node.get("directory"),
                "processes": processes,
                "summary_status": summary_status,
                "summary_label": summary_label,
                "commands": self._node_process_commands(node, root_rpc_url),
                "logs": {kind: str(path) for kind, path in logs.items()},
                "control_url": control_url,
                "control_pid": node.get("control_pid"),
                "node_page": control_status,
                "dashboard_url": api_url.rstrip("/") + "/dashboard" if api_url else None,
                "registration": node.get("registration"),
                "runtime_backend": node.get("runtime_backend") or "native",
                "simulated_device": ((node.get("selected_device") or {}).get("label") or node.get("simulated_device") or ""),
                "selected_device": node.get("selected_device") or {},
            })

        return {
            "selected_scenario": selected,
            "scenario": {
                **self._scenario_summary(selected, manifest),
                "directory": str(scenario_dir),
                "logs_directory": str(self._logs_dir(selected)),
                "root": root,
                "node_views": node_views,
                "graph": self._graph_payload(node_views),
                "node_count": len(visible_nodes),
            },
        }

    def node_logs(self, *, scenario: str | None = None, node_key: str = "root", process: str = "api", lines: int = 80) -> dict[str, Any]:
        details = self.scenario_details(scenario)
        scenario_name = details.get("selected_scenario")
        scenario_payload = details.get("scenario")
        if not scenario_name or not scenario_payload:
            fallback = self._job_node_log_fallback(
                scenario=str(scenario_name or scenario or ""),
                node_key=node_key,
                process=process,
                lines=lines,
            )
            if fallback:
                return fallback
            return {
                "selected_scenario": scenario_name,
                "node_key": node_key,
                "process": process,
                "lines": lines,
                "exists": False,
                "content": "",
            }

        node_views = scenario_payload.get("node_views") or []
        selected_node = next((item for item in node_views if item.get("key") == node_key), None)
        if not selected_node:
            fallback = self._job_node_log_fallback(
                scenario=str(scenario_name),
                node_key=node_key,
                process=process,
                lines=lines,
            )
            if fallback:
                return fallback
            raise RuntimeError("node_not_found")
        raw_path = str((selected_node.get("logs") or {}).get(process) or "").strip()
        log_path = Path(raw_path) if raw_path else None
        return {
            "selected_scenario": scenario_name,
            "node_key": node_key,
            "process": process,
            "lines": lines,
            "exists": bool(log_path and log_path.exists() and log_path.is_file()),
            "path": str(log_path) if log_path else "",
            "content": self._tail_log(log_path, lines) if log_path and log_path.exists() and log_path.is_file() else "",
            "command": (selected_node.get("commands") or {}).get(process),
        }

    def open_terminal(self, *, scenario: str | None = None, node_key: str = "root", process: str = "api") -> dict[str, Any]:
        if sys.platform != "darwin":
            raise RuntimeError("native_terminal_requires_macos")

        payload = self.node_logs(scenario=scenario, node_key=node_key, process=process, lines=200)
        raw_path = str(payload.get("path") or "").strip()
        if not raw_path:
            raise RuntimeError("log_not_ready")
        log_path = Path(raw_path)
        if log_path.is_dir():
            raise RuntimeError("log_path_is_directory")
        if not log_path.exists():
            raise RuntimeError("log_not_ready")

        launch_command = str(payload.get("command") or "").strip()
        pretty_script = self._pretty_log_script
        pretty_cmd = (
            f"{shlex.quote(str(self._python_bin))} -u {shlex.quote(str(pretty_script))} "
            f"--node {shlex.quote(str(node_key))} "
            f"--stream {shlex.quote(str(process))} "
            f"--command {shlex.quote(launch_command)} "
            f"--log-path {shlex.quote(str(log_path))}"
        )
        shell_body = f"tail -n 200 -F {shlex.quote(str(log_path))} | {pretty_cmd}"
        shell_command = f"/bin/zsh -lc {shlex.quote(shell_body)}"
        apple_script_command = shell_command.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "Terminal" to activate\ntell application "Terminal" to do script "{apple_script_command}"'
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "terminal_open_failed").strip())
        return {
            "selected_scenario": payload.get("selected_scenario"),
            "node_key": node_key,
            "process": process,
            "path": str(log_path),
            "command": launch_command,
            "opened": True,
        }

    def scenario_status(self, scenario: str) -> dict[str, Any]:
        manifest = self._read_manifest(self._manifest_path(scenario))
        if not manifest:
            return {"scenario": scenario, "exists": False, "running": False}
        return self._scenario_summary(scenario, manifest)

    def list_scenarios(self) -> list[dict[str, Any]]:
        self._runtime_generated.mkdir(parents=True, exist_ok=True)
        rows = []
        for entry in sorted(self._runtime_generated.iterdir()):
            if entry.is_dir():
                rows.append(self.scenario_status(entry.name))
        return rows

    def current_job(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._current_job:
                return None
            job = dict(self._current_job)
            job["log_lines"] = list(job["log_lines"])
            return job

    def live_scenario(self) -> str | None:
        return self._default_scenario()

    def suggested_scenario_name(self) -> str:
        return f"demo-{int(time.time())}"

    def _start_runner_job(
        self,
        *,
        scenario: str,
        runner_cmd: list[str],
        job_kind: str,
        topology_request: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            if self._current_job and self._current_job.get("status") == "running":
                raise RuntimeError("another_topology_job_is_running")
            job = {
                "job_id": f"{job_kind}-{int(time.time())}",
                "scenario": scenario,
                "status": "running",
                "started_at": time.time(),
                "finished_at": None,
                "exit_code": None,
                "error": None,
                "runner_pid": None,
                "log_lines": deque(maxlen=400),
                "runner_command": " ".join(shlex.quote(part) for part in runner_cmd),
                "topology_request": topology_request,
                "job_kind": job_kind,
            }
            self._current_job = job

        thread = threading.Thread(
            target=self._run_runner_job,
            kwargs={"cmd": runner_cmd},
            daemon=True,
            name=f"{job_kind}-job",
        )
        thread.start()
        return {"job_id": job["job_id"], "status": "running", "scenario": scenario}

    def _run_runner_job(self, *, cmd: list[str]) -> None:
        proc = subprocess.Popen(
            cmd,
            cwd=self._repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with self._lock:
            if self._current_job:
                self._current_job["runner_pid"] = proc.pid

        assert proc.stdout is not None
        for line in proc.stdout:
            with self._lock:
                if self._current_job:
                    self._current_job["log_lines"].append(line.rstrip())
        code = proc.wait()

        with self._lock:
            if not self._current_job:
                return
            self._current_job["exit_code"] = code
            self._current_job["finished_at"] = time.time()
            if code == 0:
                self._current_job["status"] = "succeeded"
            else:
                self._current_job["status"] = "failed"
                lines = list(self._current_job["log_lines"])
                last_line = lines[-1] if lines else ""
                if "Scenario directory already exists:" in last_line:
                    self._current_job["error"] = "scenario_already_exists"
                    self._current_job["detail"] = last_line
                else:
                    self._current_job["error"] = "run_topology_failed"
                    self._current_job["detail"] = last_line or "Topology runner failed"

    def start_root(self, *, host: str) -> dict[str, Any]:
        live = self.live_scenario()
        if live:
            live_status = self.scenario_status(live)
            if live_status.get("running"):
                raise RuntimeError("live_topology_already_exists")
        scenario = self.suggested_scenario_name()
        runner_cmd = [
            str(self._python_bin),
            str(self._run_topology_script),
            "start-root",
            "--scenario", str(scenario),
            "--host", str(host),
        ]
        return self._start_runner_job(
            scenario=scenario,
            runner_cmd=runner_cmd,
            job_kind="start-root",
            topology_request={"host": str(host)},
        )

    def spawn_node(self, *, tier: str, device_id: str, endpoint_role: str | None = None, host: str) -> dict[str, Any]:
        scenario = self.live_scenario()
        if not scenario:
            raise RuntimeError("live_topology_not_found")
        if tier not in {"fog", "edge", "endpoint"}:
            raise RuntimeError("invalid_tier")
        runner_cmd = [
            str(self._python_bin),
            str(self._run_topology_script),
            "spawn-node",
            "--scenario", str(scenario),
            "--tier", str(tier),
            "--device-id", str(device_id),
            "--host", str(host),
        ]
        if tier == "endpoint":
            runner_cmd.extend(["--endpoint-role", str(endpoint_role or "Sensor")])
        return self._start_runner_job(
            scenario=scenario,
            runner_cmd=runner_cmd,
            job_kind=f"spawn-{tier}",
            topology_request={
                "tier": str(tier),
                "device_id": str(device_id),
                "endpoint_role": str(endpoint_role or "Sensor"),
                "host": str(host),
            },
        )

    def start_topology(
        self,
        *,
        scenario: str,
        cloud: int,
        fog: int,
        edge: int,
        endpoint: int,
        endpoint_role: str,
        endpoint_roles: list[str] | None,
        fog_devices: list[str] | None,
        edge_devices: list[str] | None,
        endpoint_devices: list[str] | None,
        host: str,
    ) -> dict[str, Any]:
        runner_cmd = [
            str(self._python_bin),
            str(self._run_topology_script),
            "--cloud", str(cloud),
            "--fog", str(fog),
            "--edge", str(edge),
            "--endpoint", str(endpoint),
            "--endpoint-role", str(endpoint_role),
            "--scenario", str(scenario),
            "--host", str(host),
        ]
        if endpoint_roles:
            runner_cmd.extend(["--endpoint-roles", ",".join(str(role) for role in endpoint_roles)])
        if fog_devices:
            runner_cmd.extend(["--fog-devices", ",".join(str(device) for device in fog_devices)])
        if edge_devices:
            runner_cmd.extend(["--edge-devices", ",".join(str(device) for device in edge_devices)])
        if endpoint_devices:
            runner_cmd.extend(["--endpoint-devices", ",".join(str(device) for device in endpoint_devices)])
        with self._lock:
            if self._current_job and self._current_job.get("status") == "running":
                raise RuntimeError("another_topology_job_is_running")

            job = {
                "job_id": f"topology-{int(time.time())}",
                "scenario": scenario,
                "status": "running",
                "started_at": time.time(),
                "finished_at": None,
                "exit_code": None,
                "error": None,
                "runner_pid": None,
                "log_lines": deque(maxlen=400),
                "runner_command": " ".join(shlex.quote(part) for part in runner_cmd),
                "topology_request": {
                    "cloud": int(cloud),
                    "fog": int(fog),
                    "edge": int(edge),
                    "endpoint": int(endpoint),
                    "endpoint_role": str(endpoint_role),
                    "endpoint_roles": [str(role) for role in (endpoint_roles or [])],
                    "fog_devices": [str(device) for device in (fog_devices or [])],
                    "edge_devices": [str(device) for device in (edge_devices or [])],
                    "endpoint_devices": [str(device) for device in (endpoint_devices or [])],
                    "host": str(host),
                },
            }
            self._current_job = job

        thread = threading.Thread(
            target=self._run_start_job,
            kwargs={
                "scenario": scenario,
                "cloud": cloud,
                "fog": fog,
                "edge": edge,
                "endpoint": endpoint,
                "endpoint_role": endpoint_role,
                "endpoint_roles": endpoint_roles or [],
                "fog_devices": fog_devices or [],
                "edge_devices": edge_devices or [],
                "endpoint_devices": endpoint_devices or [],
                "host": host,
            },
            daemon=True,
            name="topology-start-job",
        )
        thread.start()
        return {"job_id": job["job_id"], "status": "running", "scenario": scenario}

    def _run_start_job(self, *, scenario: str, cloud: int, fog: int, edge: int, endpoint: int, endpoint_role: str, endpoint_roles: list[str], fog_devices: list[str], edge_devices: list[str], endpoint_devices: list[str], host: str) -> None:
        cmd = [
            str(self._python_bin),
            str(self._run_topology_script),
            "--cloud", str(cloud),
            "--fog", str(fog),
            "--edge", str(edge),
            "--endpoint", str(endpoint),
            "--endpoint-role", endpoint_role,
            "--scenario", scenario,
            "--host", host,
        ]
        if endpoint_roles:
            cmd.extend(["--endpoint-roles", ",".join(endpoint_roles)])
        if fog_devices:
            cmd.extend(["--fog-devices", ",".join(fog_devices)])
        if edge_devices:
            cmd.extend(["--edge-devices", ",".join(edge_devices)])
        if endpoint_devices:
            cmd.extend(["--endpoint-devices", ",".join(endpoint_devices)])
        proc = subprocess.Popen(
            cmd,
            cwd=self._repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with self._lock:
            if self._current_job:
                self._current_job["runner_pid"] = proc.pid

        assert proc.stdout is not None
        for line in proc.stdout:
            with self._lock:
                if self._current_job:
                    self._current_job["log_lines"].append(line.rstrip())
        code = proc.wait()

        with self._lock:
            if not self._current_job:
                return
            self._current_job["exit_code"] = code
            self._current_job["finished_at"] = time.time()
            if code == 0:
                self._current_job["status"] = "succeeded"
            else:
                self._current_job["status"] = "failed"
                lines = list(self._current_job["log_lines"])
                last_line = lines[-1] if lines else ""
                if "Scenario directory already exists:" in last_line:
                    self._current_job["error"] = "scenario_already_exists"
                    self._current_job["detail"] = last_line
                else:
                    self._current_job["error"] = "run_topology_failed"
                    self._current_job["detail"] = last_line or "Topology runner failed"

    def stop_topology(self, scenario: str, *, delete_scenario: bool = False) -> dict[str, Any]:
        details = self.scenario_details(scenario)
        scenario_payload = details.get("scenario")
        if not scenario_payload:
            raise RuntimeError("scenario_not_found")

        ports: set[int] = set()
        pids: set[int] = set()
        for node in scenario_payload.get("node_views") or []:
            if node.get("api_url"):
                api_port = self._port_from_url(node.get("api_url"))
                if api_port:
                    ports.add(api_port)
            if node.get("control_url"):
                control_port = self._port_from_url(node.get("control_url"))
                if control_port:
                    ports.add(control_port)
            if node.get("rpc_url"):
                rpc_port = self._port_from_url(node.get("rpc_url"))
                if rpc_port:
                    ports.add(rpc_port)
            if node.get("p2p_port"):
                try:
                    ports.add(int(node.get("p2p_port")))
                except Exception:
                    pass
            if node.get("control_pid"):
                pids.add(int(node.get("control_pid")))
            for process in (node.get("processes") or {}).values():
                for pid in (process.get("manifest_pid"), process.get("observed_pid")):
                    if pid:
                        pids.add(int(pid))

        stopped = []
        for pid in sorted(pids, reverse=True):
            self._terminate_pid(pid)
            stopped.append(pid)

        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if not any(self._pid_alive(pid) for pid in pids):
                if all(not self._listening_pids_for_port(port) for port in ports):
                    break
            time.sleep(0.25)

        remaining_live_pids = sorted({pid for pid in pids if self._pid_alive(pid)})
        freed_ports = sorted(port for port in ports if not self._listening_pids_for_port(port))
        if delete_scenario:
            shutil.rmtree(self._scenario_dir(scenario), ignore_errors=True)
        return {
            "scenario": scenario,
            "stopped_pids": stopped,
            "remaining_live_pids": remaining_live_pids,
            "freed_ports": freed_ports,
            "deleted_scenario": bool(delete_scenario),
            "scenario_exists_after_stop": self._scenario_dir(scenario).exists(),
        }

    def stop_live_topology(self) -> dict[str, Any]:
        scenario = self.live_scenario()
        if not scenario:
            raise RuntimeError("live_topology_not_found")
        return self.stop_topology(scenario, delete_scenario=False)

    def delete_topology(self, scenario: str) -> dict[str, Any]:
        scenario_dir = self._scenario_dir(scenario)
        if not scenario_dir.exists():
            raise RuntimeError("scenario_not_found")
        details = self.scenario_details(scenario)
        scenario_payload = details.get("scenario")
        pids: set[int] = set()
        if scenario_payload:
            for node in scenario_payload.get("node_views") or []:
                if node.get("control_pid") and self._pid_alive(node.get("control_pid")):
                    pids.add(int(node.get("control_pid")))
                for process in (node.get("processes") or {}).values():
                    for pid in (process.get("manifest_pid"), process.get("observed_pid")):
                        if pid and self._pid_alive(pid):
                            pids.add(int(pid))
        else:
            for pid in self._directory_process_pids(scenario_dir):
                pids.add(int(pid))

        if pids:
            raise RuntimeError("topology_has_running_processes")

        shutil.rmtree(scenario_dir, ignore_errors=True)
        return {
            "scenario": scenario,
            "deleted_scenario": True,
            "scenario_exists_after_delete": scenario_dir.exists(),
        }

    def stop_process(self, *, scenario: str, node_key: str, process: str) -> dict[str, Any]:
        if process not in {"api", "chain"}:
            raise RuntimeError("invalid_process")
        details = self.scenario_details(scenario)
        scenario_payload = details.get("scenario")
        if not scenario_payload:
            raise RuntimeError("scenario_not_found")

        node_views = scenario_payload.get("node_views") or []
        selected_node = next((item for item in node_views if str(item.get("key")) == str(node_key)), None)
        if not selected_node:
            raise RuntimeError("node_not_found")

        process_state = (selected_node.get("processes") or {}).get(process) or {}
        manifest_pid = process_state.get("manifest_pid")
        observed_pid = process_state.get("observed_pid")
        pids = []
        for pid in (observed_pid, manifest_pid):
            if pid:
                try:
                    pid_int = int(pid)
                except Exception:
                    continue
                if pid_int not in pids:
                    pids.append(pid_int)

        port = self._port_from_url(selected_node.get("api_url") if process == "api" else selected_node.get("rpc_url"))
        if not pids and not port:
            raise RuntimeError("process_not_found")

        stopped: list[int] = []
        for pid in pids:
            self._terminate_pid(pid)
            stopped.append(pid)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            pid_alive = any(self._pid_alive(pid) for pid in pids)
            port_busy = bool(self._listening_pids_for_port(port)) if port else False
            if not pid_alive and not port_busy:
                break
            time.sleep(0.25)

        refreshed = self.refresh_topology(scenario, persist=True)
        refreshed_details = self.scenario_details(scenario)
        refreshed_node = next(
            (item for item in ((refreshed_details.get("scenario") or {}).get("node_views") or []) if str(item.get("key")) == str(node_key)),
            None,
        )
        remaining_live_pids = sorted({pid for pid in pids if self._pid_alive(pid)})
        freed_ports = [port] if port and not self._listening_pids_for_port(port) else []
        return {
            "scenario": scenario,
            "node_key": node_key,
            "process": process,
            "stopped_pids": stopped,
            "remaining_live_pids": remaining_live_pids,
            "freed_ports": freed_ports,
            "manifest_updated": bool(refreshed.get("manifest_updated")),
            "node": refreshed_node,
        }

    def start_process(self, *, scenario: str, node_key: str, process: str) -> dict[str, Any]:
        if process not in {"api", "chain"}:
            raise RuntimeError("invalid_process")
        details = self.scenario_details(scenario)
        scenario_payload = details.get("scenario")
        if not scenario_payload:
            raise RuntimeError("scenario_not_found")

        node_views = scenario_payload.get("node_views") or []
        selected_node = next((item for item in node_views if str(item.get("key")) == str(node_key)), None)
        if not selected_node:
            raise RuntimeError("node_not_found")

        process_state = (selected_node.get("processes") or {}).get(process) or {}
        if str(process_state.get("status") or "") in {"ok", "running", "started"}:
            raise RuntimeError("process_already_running")

        command = str((selected_node.get("commands") or {}).get(process) or "").strip()
        raw_path = str((selected_node.get("logs") or {}).get(process) or "").strip()
        directory = Path(selected_node.get("directory") or "")
        if not command:
            raise RuntimeError("process_command_not_found")
        if not raw_path:
            raise RuntimeError("process_log_not_found")
        if not directory.exists():
            raise RuntimeError("process_directory_not_found")

        pid = self._launch_logged_command(
            command=command,
            cwd=directory,
            log_path=Path(raw_path),
        )

        manifest_path = self._manifest_path(scenario)
        manifest = self._read_manifest(manifest_path) or {}
        changed = False
        if node_key == "root":
            root = manifest.get("root") or {}
            pid_key = "service_pid" if process == "api" else "chain_pid"
            if root.get(pid_key) != pid:
                root[pid_key] = pid
                manifest["root"] = root
                changed = True
        else:
            for node in manifest.get("nodes") or []:
                key = f"{node.get('tier')}-{int(node.get('ordinal') or 0)}"
                if key != node_key:
                    continue
                pid_key = "api_pid" if process == "api" else "chain_pid"
                if node.get(pid_key) != pid:
                    node[pid_key] = pid
                    changed = True
                break
        if changed:
            self._write_manifest(manifest_path, manifest)

        time.sleep(0.25)
        refreshed = self.refresh_topology(scenario, persist=True)
        refreshed_details = self.scenario_details(scenario)
        refreshed_node = next(
            (item for item in ((refreshed_details.get("scenario") or {}).get("node_views") or []) if str(item.get("key")) == str(node_key)),
            None,
        )
        return {
            "scenario": scenario,
            "node_key": node_key,
            "process": process,
            "started_pid": pid,
            "manifest_updated": bool(refreshed.get("manifest_updated")),
            "node": refreshed_node,
        }

    def stop_node(self, *, scenario: str, node_key: str) -> dict[str, Any]:
        details = self.scenario_details(scenario)
        scenario_payload = details.get("scenario")
        if not scenario_payload:
            raise RuntimeError("scenario_not_found")

        node_views = scenario_payload.get("node_views") or []
        selected_node = next((item for item in node_views if str(item.get("key")) == str(node_key)), None)
        if not selected_node:
            raise RuntimeError("node_not_found")

        process_states = selected_node.get("processes") or {}
        targets: list[str] = []
        api_status = str(((process_states.get("api") or {}).get("status")) or "")
        chain_status = str(((process_states.get("chain") or {}).get("status")) or "")
        if api_status not in {"down", "not_applicable", ""}:
            targets.append("api")
        if chain_status not in {"down", "not_applicable", ""}:
            targets.append("chain")
        if not targets:
            raise RuntimeError("node_not_running")

        control_pid = selected_node.get("control_pid")
        control_port = self._port_from_url(selected_node.get("control_url"))

        stopped_pids: set[int] = set()
        remaining_live_pids: set[int] = set()
        freed_ports: set[int] = set()
        results: list[dict[str, Any]] = []
        for process in targets:
            result = self.stop_process(scenario=scenario, node_key=node_key, process=process)
            results.append(result)
            stopped_pids.update(int(pid) for pid in (result.get("stopped_pids") or []) if pid)
            remaining_live_pids.update(int(pid) for pid in (result.get("remaining_live_pids") or []) if pid)
            freed_ports.update(int(port) for port in (result.get("freed_ports") or []) if port)

        if control_pid:
            self._terminate_pid(int(control_pid))
            stopped_pids.add(int(control_pid))
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if not self._pid_alive(int(control_pid)) and (not control_port or not self._listening_pids_for_port(control_port)):
                    break
                time.sleep(0.25)
            if control_port and not self._listening_pids_for_port(control_port):
                freed_ports.add(int(control_port))

        if node_key != "root":
            manifest_path = self._manifest_path(scenario)
            manifest = self._read_manifest(manifest_path) or {}
            changed = False
            for node in manifest.get("nodes") or []:
                key = f"{node.get('tier')}-{int(node.get('ordinal') or 0)}"
                if key == node_key and str(node.get("lifecycle_status") or "active") != "retired":
                    node["control_pid"] = None
                    node["lifecycle_status"] = "retired"
                    node["retired_at_ms"] = int(time.time() * 1000)
                    changed = True
                    break
            if changed:
                self._write_manifest(manifest_path, manifest)
                self.refresh_topology(scenario, persist=True)

        refreshed_details = self.scenario_details(scenario)
        refreshed_node = next(
            (item for item in ((refreshed_details.get("scenario") or {}).get("node_views") or []) if str(item.get("key")) == str(node_key)),
            None,
        )
        return {
            "scenario": scenario,
            "node_key": node_key,
            "stopped_processes": targets,
            "stopped_pids": sorted(stopped_pids),
            "remaining_live_pids": sorted(remaining_live_pids),
            "freed_ports": sorted(freed_ports),
            "results": results,
            "node": refreshed_node,
        }

    def kill_all_spawned_processes(self) -> dict[str, Any]:
        stopped_pids: set[int] = set()
        freed_ports: set[int] = set()
        remaining_live_pids: set[int] = set()
        scenario_results: list[dict[str, Any]] = []

        with self._lock:
            current_job = dict(self._current_job or {})
        runner_pid = current_job.get("runner_pid")
        if runner_pid:
            try:
                runner_pid_int = int(runner_pid)
                self._terminate_pid(runner_pid_int)
                stopped_pids.add(runner_pid_int)
            except Exception:
                pass

        scenario_names = [row.get("scenario") for row in self.list_scenarios() if row.get("scenario")]
        for scenario in scenario_names:
            try:
                result = self.stop_topology(str(scenario), delete_scenario=False)
                scenario_results.append(result)
                stopped_pids.update(int(pid) for pid in (result.get("stopped_pids") or []) if pid)
                freed_ports.update(int(port) for port in (result.get("freed_ports") or []) if port)
                remaining_live_pids.update(int(pid) for pid in (result.get("remaining_live_pids") or []) if pid)
            except Exception as exc:
                scenario_results.append({
                    "scenario": scenario,
                    "error": str(exc),
                })

        with self._lock:
            if self._current_job:
                self._current_job["status"] = "stopped" if not remaining_live_pids else self._current_job.get("status", "running")
                self._current_job["finished_at"] = time.time()
                self._current_job["detail"] = "All spawned processes were terminated from the control page."

        return {
            "stopped_pids": sorted(stopped_pids),
            "freed_ports": sorted(freed_ports),
            "remaining_live_pids": sorted(remaining_live_pids),
            "scenarios": scenario_results,
            "runner_pid": int(runner_pid) if runner_pid else None,
        }
