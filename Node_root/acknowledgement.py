# acknowledgement.py
import base64
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

import requests

from monitor import track_performance

ALLOWED_ACK_ROLES = {"Fog", "Edge"}

_ENODE_RE = re.compile(r"^enode://[a-fA-F0-9]+@[\w\.\-:\[\]]+:\d+$")
_URL_RE = re.compile(r"^http?://[^?#]+$")


class AcknowledgementSender:
    """Sends bootstrap acknowledgement as a compact manifest to Fog/Edge nodes."""

    _cache_lock = threading.RLock()
    _hash_cache: dict[tuple[str, int, int], str] = {}
    _enode_cache: dict[str, tuple[str, float]] = {}

    def __init__(
        self,
        registering_node_url,
        genesis_file,
        node_registry_file,
        besu_rpc_url,
        prefunded_keys_file,
        *,
        bootstrap_base_url,
        timeout=3,
        verify_ssl=False,
    ):
        self.registering_node_url = str(registering_node_url).rstrip("/")
        self.genesis_file = str(genesis_file)
        self.node_registry_file = str(node_registry_file)
        self.besu_rpc_url = str(besu_rpc_url)
        self.prefunded_keys_file = str(prefunded_keys_file)
        self.bootstrap_base_url = str(bootstrap_base_url or "").rstrip("/")
        self.timeout = float(timeout)
        self.verify_ssl = verify_ssl

    @staticmethod
    def _role_allows(node_type: str) -> bool:
        nt = (node_type or "").strip()
        return nt in ALLOWED_ACK_ROLES

    @classmethod
    def _cached_sha256(cls, path: str) -> str:
        file_path = Path(path)
        stat = file_path.stat()
        key = (str(file_path), int(stat.st_mtime_ns), int(stat.st_size))
        with cls._cache_lock:
            cached = cls._hash_cache.get(key)
            if cached:
                return cached
        h = hashlib.sha256()
        with file_path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        digest = h.hexdigest()
        with cls._cache_lock:
            cls._hash_cache = {cache_key: value for cache_key, value in cls._hash_cache.items() if cache_key[0] != str(file_path)}
            cls._hash_cache[key] = digest
        return digest

    @classmethod
    def _cached_enode(cls, rpc_url: str, *, timeout: float, verify_ssl: bool) -> str | None:
        with cls._cache_lock:
            cached = cls._enode_cache.get(rpc_url)
            if cached and (time.time() - cached[1]) < 15.0:
                return cached[0]
        payload = {"jsonrpc": "2.0", "method": "admin_nodeInfo", "params": [], "id": 1}
        try:
            response = requests.post(
                rpc_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
                verify=verify_ssl if rpc_url.startswith("https") else True,
            )
            response.raise_for_status()
            data = response.json() or {}
            enode = str((data.get("result") or {}).get("enode") or "")
            if enode and _ENODE_RE.match(enode):
                with cls._cache_lock:
                    cls._enode_cache[rpc_url] = (enode, time.time())
                return enode
        except requests.RequestException as exc:
            print(f"[ack] Error fetching enode: {exc}")
        return None

    def get_enode(self) -> str | None:
        return self._cached_enode(self.besu_rpc_url, timeout=self.timeout, verify_ssl=self.verify_ssl)

    @staticmethod
    def _read_json(path: str) -> dict | list | None:
        file_path = Path(path)
        if not file_path.exists():
            return None
        return json.loads(file_path.read_text())

    def build_bootstrap_manifest(self, node_id: str, *, node_type: str) -> dict[str, str] | None:
        if not self._role_allows(node_type):
            return None
        if not _URL_RE.match(self.registering_node_url):
            print(f"[ack] Invalid ack URL: {self.registering_node_url}")
            return None
        if not self.bootstrap_base_url or not _URL_RE.match(self.bootstrap_base_url):
            print(f"[ack] Invalid bootstrap base URL: {self.bootstrap_base_url}")
            return None
        enode = self.get_enode()
        if not enode:
            print("[ack] Error: enode could not be retrieved!")
            return None
        return {
            "node_id": str(node_id),
            "node_type": str(node_type),
            "enode": enode,
            "genesis_url": f"{self.bootstrap_base_url}/bootstrap/genesis.json",
            "registry_url": f"{self.bootstrap_base_url}/bootstrap/node-registry.json",
            "prefunded_keys_url": f"{self.bootstrap_base_url}/bootstrap/prefunded_keys.json",
            "genesis_sha256": self._cached_sha256(self.genesis_file),
            "registry_sha256": self._cached_sha256(self.node_registry_file),
            "prefunded_sha256": self._cached_sha256(self.prefunded_keys_file),
        }

    @track_performance
    def send_acknowledgment(self, node_id: str, *, node_type: str) -> bool:
        if not self._role_allows(node_type):
            print(f"[ack] Skipping ack for node_type='{node_type}'.")
            return False
        enode = self.get_enode()
        if not enode:
            print("[ack] Error: enode could not be retrieved!")
            return False
        try:
            payload = {
                "node_id": str(node_id),
                "node_type": str(node_type),
                "genesis_b64": base64.b64encode(Path(self.genesis_file).read_bytes()).decode("ascii"),
                "node_registry": self._read_json(self.node_registry_file),
                "prefunded_keys": self._read_json(self.prefunded_keys_file),
                "enode": enode,
                "besu_rpc_url": self.besu_rpc_url,
            }
        except Exception as exc:
            print(f"[ack] Failed to prepare bootstrap payload: {exc}")
            return False
        headers = {
            "X-Ack-NodeId": str(node_id),
            "X-Ack-Role": str(node_type),
            "Content-Type": "application/json",
        }
        url = f"{self.registering_node_url}/bootstrap-ack"
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            print(f"[ack] Response {response.status_code}: {response.text[:500]}")
            if response.status_code == 200:
                print(f"[ack] Bootstrap acknowledgement sent to {url} for node {node_id} (type={node_type})")
                return True
            print(f"[ack] Ack HTTP {response.status_code}: {response.text}")
            return False
        except Exception as exc:
            print(f"[ack] Error sending acknowledgment: {exc}")
            return False
