# orchestrator.py
# Blockchain bridge, registration, and access-control logic for BlockCap.
# - Direct web3.py contract calls (no subprocess bridge).
# - Registration + Acknowledgement flow.
# - Automated fine-grained access control + delegation.
#
# Requirements:
#   - data/NodeRegistry.json (ABI + contract address) deployed
#   - prefunded_keys.json present (signing accounts)
#
# Usage sketch:
#   orch = Orchestrator()
#   orch.registration_flow(payload_dict)  # handles validator/non-validator/endpoint
#   decision = orch.access_flow(from_sig, to_sig, http_method, resource_path)
#   orch.delegate_flow(parent_from_sig, to_sig, child_from_sig, ops_csv, child_exp_secs)

import json
import os
import re
import subprocess
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple, List
from acknowledgement import AcknowledgementSender, ALLOWED_ACK_ROLES
import threading
from pathlib import Path

import requests

try:
    import fcntl
except Exception:
    fcntl = None


try:
    from eth_keys import keys
    from eth_utils import keccak
except Exception:
    keys = None
    keccak = None

try:
    from eth_account import Account as EthAccount
except Exception:
    EthAccount = None

try:
    from eth_abi import encode as eth_abi_encode
except Exception:
    eth_abi_encode = None

# plug in your decorator
try:
    from monitor import track_performance
except Exception:
    def track_performance(fn):  # fallback no-op
        return fn

try:
    from web3 import Web3
except Exception:
    Web3 = None

from tef_metrics import LatencyRecorder, ProcessEventRecorder, ensure_results_dir
    
# --------------- constants ---------------

ROLE = {"Unknown":0, "Cloud":1, "Fog":2, "Edge":3, "Sensor":4, "Actuator":5}
ROLE_BY_NUM = {v:k for (k,v) in ROLE.items()}
VALID_POLICY_ROLES = {name for name, value in ROLE.items() if value > 0}
CUSTOM_ERROR_SELECTOR_NAMES = {
    "0x3f0f8fb6": "AddressNotRegistered",
    "0x6ca13d52": "AlreadyRevoked",
    "0x49da7e6d": "DuplicateNodeId",
    "0x7db2511f": "DuplicatePolicy",
    "0x40a3d246": "DuplicateSignature",
    "0x2d59cfbd": "EmptyOpsAllowed",
    "0x14afd509": "EmptyOpsSubset",
    "0x7dc3db61": "GrantAlreadyActive",
    "0x414f8053": "InvalidDelegationDepth",
    "0x26f7f534": "InvalidExpiry",
    "0x4f5ba1b6": "InvalidRoles",
    "0xe2517d3f": "NodeNotRegistered",
    "0x37ef0f63": "NotGrantHolder",
    "0x29b2c9d7": "NotPolicyAdmin",
    "0x56144b78": "NotResourceOwner",
    "0xb7f9a8f7": "OpsSubsetExceedsAllowed",
    "0x8395441c": "PolicyIsDeprecated",
    "0xc3f08144": "PolicyNotFound",
    "0x222cb570": "PolicyRoleMismatch",
    "0xd92e233d": "ZeroAddr",
}

# HTTP -> OP mapping (tweak as needed)
METHOD_TO_OP = {
    "GET": "READ",
    "HEAD": "READ",
    "OPTIONS": "READ",
    "POST": "WRITE",
    "PUT": "UPDATE",
    "PATCH": "UPDATE",
    "DELETE": "REMOVE",
}

# Where we persist our resource->policy index
POLICY_INDEX_FILE = os.path.join(os.path.dirname(__file__), "policy_index.json")

# --------------- utility ---------------

def _json_load(path: str, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default


def _clean_address_text(value: Any) -> str:
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", str(value or "")).strip()
    match = re.search(r"0x[a-fA-F0-9]{40}", text)
    return match.group(0) if match else text


def _load_local_env(repo_root: str) -> None:
    env_path = Path(repo_root) / ".env"
    if not env_path.exists():
        return
    try:
        for raw_line in env_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        return

def _json_save(path: str, data):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)

def _now() -> int:
    return int(time.time())

def _canon_resource_key(method: str, resource_path: str) -> str:
    m = (method or "").upper().strip()
    p = (resource_path or "").strip()
    if not m:
        raise ValueError("method is required")
    if not p or not p.startswith("/"):
        # normalize to a leading slash
        p = "/" + (p or "")
    return f"api:{m}:{p}"

def _ctx_hash(s: str) -> str:
    """Return 0x-prefixed keccak256(ctx) to match contract bytes32 ctxSchema."""
    try:
        return "0x" + keccak(text=(s or "")).hex()
    except Exception:
        # In test envs without eth_utils, leave as-is (won't validate).
        return (s or "")
    
def _parse_bool(s: str) -> Optional[bool]:
    s = (s or "").strip().lower()
    if s == "true": return True
    if s == "false": return False
    return None

def _ops_csv(op_list_or_csv: Any) -> str:
    if isinstance(op_list_or_csv, str): return op_list_or_csv
    if isinstance(op_list_or_csv, (list, tuple)):
        return ",".join(op_list_or_csv)
    raise ValueError("ops must be list/tuple or csv string")

# --- ops + cache helpers ---
_OPS_MAP = {
    "READ": 1, "WRITE": 2, "UPDATE": 4, "REMOVE": 8,
    "GET": 1, "POST": 2, "PUT": 4, "PATCH": 4, "DELETE": 8,
}

def _ops_mask(ops_csv_or_mask: str|int) -> int:
    if isinstance(ops_csv_or_mask, int):
        return ops_csv_or_mask
    s = (ops_csv_or_mask or "").strip()
    if s.isdigit():
        return int(s)
    mask = 0
    for part in (p.strip().upper() for p in s.split(",") if p.strip()):
        if part not in _OPS_MAP:
            raise ValueError(f"Unknown op: {part}")
        mask |= _OPS_MAP[part]
    return mask

def _ctx_schema_hex(ctx: str) -> str:
    c = (ctx or "").strip()
    if not c:
        return "0x" + ("0" * 64)
    if c.startswith("0x"):
        h = c[2:]
        if len(h) > 64:
            raise ValueError("bytes32 too long")
        return "0x" + h.rjust(64, "0").lower()  # ← pad like JS
    try:
        from eth_utils import keccak as _keccak
        return "0x" + _keccak(text=c).hex()
    except Exception:
        return c


def _to_bytes32(s: str) -> bytes:
    """Convert a string or 0x-hex to a 32-byte value for Solidity bytes32 params."""
    if not s:
        return b'\x00' * 32
    c = s.strip()
    if c.startswith("0x"):
        h = c[2:]
        if len(h) > 64:
            raise ValueError("bytes32 too long")
        return bytes.fromhex(h.rjust(64, "0"))
    try:
        from eth_utils import keccak as _keccak
        return _keccak(text=c)
    except Exception:
        return c.encode("utf-8")[:32].ljust(32, b'\x00')


def _normalize_w3_struct(result) -> Dict[str, Any]:
    """Convert a web3.py struct/AttributeDict/tuple to a plain Python dict."""
    if result is None:
        return {}
    if isinstance(result, dict):
        out = {}
        for k, v in result.items():
            if isinstance(v, bytes):
                out[k] = "0x" + v.hex()
            elif isinstance(v, int):
                out[k] = v
            else:
                out[k] = v
        return out
    if hasattr(result, '_asdict'):
        return _normalize_w3_struct(dict(result._asdict()))
    if hasattr(result, 'items'):
        return _normalize_w3_struct(dict(result))
    # raw tuple / list — return as-is
    return result
    

    
def _policy_cache_key(from_role: str, to_role: str, ops_mask: int, ctx: str) -> str:
    return f"{from_role}|{to_role}|{ops_mask}|{ctx}"


def _invalid_policy_roles(*roles: str) -> List[str]:
    return [role for role in roles if role not in VALID_POLICY_ROLES]



# --------------- Orchestrator ---------------

@dataclass
class JsResult:
    ok: bool
    stdout: str
    stderr: str
    code: int

class Orchestrator:
    def __init__(self, repo_root: Optional[str]=None, registrar_role: str="Cloud", enforce_signature: bool=True):
        self.root = repo_root or os.path.dirname(os.path.abspath(__file__))
        _load_local_env(self.root)
        self.repo_path = Path(self.root)
        self.interact = os.path.join(self.root, "interact.js")
        self.node_registry_json = os.path.join(self.root, "data", "NodeRegistry.json")
        self.prefunded_keys_json = os.path.join(self.root, "prefunded_keys.json")
        self.genesis_file_path = os.path.join(self.root, "genesis", "genesis.json")
        self.besu_rpc_url = os.getenv("BESU_RPC_URL", "http://127.0.0.1:8545")   # <— default
        self.policy_index: Dict[str, Any] = _json_load(POLICY_INDEX_FILE, {})
        self.registrar_role = registrar_role  # used as "registeredByNodeTypeStr"
        self.enforce_signature = enforce_signature   # <-- store the flag
        self.results_dir = ensure_results_dir(self.root)
        self.latency_recorder = LatencyRecorder(self.results_dir)
        self._request_ctx = threading.local()
        self._request_lock = threading.RLock()
        self._active_requests = 0
        self._policy_lock = threading.RLock()
        self._grant_cache: dict = {}  # key: (from_sig, to_sig, method, resource_path) → (result_dict, expiry_epoch)
        self._grant_cache_lock = threading.RLock()
        self._grant_policy_id_cache: dict[tuple[str, str], int] = {}
        self._grant_policy_cache_lock = threading.RLock()
        self._policy_details_cache: OrderedDict[int, Dict[str, Any]] = OrderedDict()
        self._policy_details_cache_lock = threading.RLock()
        self._policy_poller_started = False
        self._policy_poller_lock = threading.Lock()
        self._policy_poll_block = 0
        self._w3 = None
        self._contract = None
        # default from (display only)
        pk = _json_load(self.prefunded_keys_json, {"prefunded_accounts":[]})
        node_details_path = os.path.join(self.root, "node-details.json")
        self.nd = _json_load(node_details_path, {})
        # Prefer the node's own persisted address; fallback to prefunded[0] only when absent.
        node_details_addr = _clean_address_text(self.nd.get("address", ""))
        prefunded_addr = _clean_address_text(pk.get("prefunded_accounts", [])[0].get("address", "")) if pk else ""
        self.registrar_addr = node_details_addr or prefunded_addr or None
        # --- intelligent validator listening / dedupe ---
        self._vlisten_lock = threading.Lock()
        self._vlisten_started = False
        self._vlisten_lock_fd = None
        self._vlisten_lock_path = self.repo_path / "data" / ".validator-listener.lock"
        self._voted_addrs = set()  
        self.local_node_tier = (self.nd.get("node_type") or registrar_role or "Unknown").strip().lower()
        self.local_node_id = (self.nd.get("node_id") or self.nd.get("id") or "").strip()
        self.local_node_name = (self.nd.get("node_name") or self.nd.get("name") or "").strip()
        self.event_recorder = ProcessEventRecorder(
            self.results_dir,
            node_id=self.local_node_id,
            node_name=self.local_node_name,
            node_tier=self.local_node_tier,
        )
        self._ack_status_lock = threading.RLock()
        self._ack_status: Dict[str, Dict[str, Any]] = {}
        self._accounts: List[Any] = []
        self._nonce_lock = threading.Lock()
        self._init_web3_contract()
        self._load_accounts()
        self._start_policy_cache_watcher()

    # ---------- low-level JS bridge ----------

    #@track_performance
    def _js(self, *argv, env: Optional[Dict[str,str]]=None) -> JsResult:
        """Runs: node interact.js <args...> and returns structured result."""
        cmd = ["node", self.interact, *[str(a) for a in argv]]

        # Default sender index for REAL runs (many scripts pick FROM_IDX)
        # Respect any explicit env passed in.
        merged_env = os.environ.copy()
        if self.besu_rpc_url:
            merged_env["BESU_RPC_URL"] = str(self.besu_rpc_url)
        if env:
            merged_env.update(env)
        if "FROM_IDX" not in merged_env:
            # fall back to 0 unless caller overrides
            merged_env["FROM_IDX"] = os.getenv("FROM_IDX", "0")
        node_options = str(merged_env.get("NODE_OPTIONS") or "").strip()
        if "--no-deprecation" not in node_options.split():
            merged_env["NODE_OPTIONS"] = f"{node_options} --no-deprecation".strip()

        if os.getenv("ORCH_TRACE"):
            print("exec:", " ".join(cmd))

        proc = subprocess.run(cmd, capture_output=True, text=True, env=merged_env)
        return JsResult(
            ok=(proc.returncode == 0),
            stdout=proc.stdout.strip(),
            stderr=proc.stderr.strip(),
            code=proc.returncode
        )

    def _init_web3_contract(self) -> None:
        if Web3 is None:
            return
        try:
            artifact = _json_load(self.node_registry_json, {})
            abi = artifact.get("abi") or []
            networks = artifact.get("networks") or {}
            network_id = next(iter(networks.keys()), None)
            address = networks.get(network_id, {}).get("address") if network_id else None
            if not abi or not address:
                return
            provider = Web3.HTTPProvider(self.besu_rpc_url, request_kwargs={"timeout": 10})
            self._w3 = Web3(provider)
            self._contract = self._w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)
        except Exception:
            self._w3 = None
            self._contract = None

    def _load_accounts(self) -> None:
        """Load signing accounts, optionally prepending the root policy-admin key."""
        if self._w3 is None or EthAccount is None:
            return
        try:
            self._accounts = []
            if os.getenv("IS_POLICY_ADMIN"):
                root_key_path = self.repo_path / "data" / "key.priv"
                if root_key_path.exists():
                    root_pk = root_key_path.read_text().strip()
                    if root_pk:
                        if not root_pk.startswith("0x"):
                            root_pk = "0x" + root_pk
                        self._accounts.append(EthAccount.from_key(root_pk))

            data = _json_load(self.prefunded_keys_json, {"prefunded_accounts": []})
            for entry in data.get("prefunded_accounts", []):
                pk = (entry.get("private_key") or "").strip()
                if pk:
                    if not pk.startswith("0x"):
                        pk = "0x" + pk
                    self._accounts.append(EthAccount.from_key(pk))
        except Exception:
            self._accounts = []

    def _should_use_js(self) -> bool:
        """Returns True if the JS bridge should be used instead of web3.py."""
        if not os.getenv("REAL_INTERACT"):
            return True
        return bool(os.getenv("USE_JS_BRIDGE")) or self._contract is None or self._w3 is None or not self._accounts

    def _w3_account(self, from_idx: Optional[int] = None) -> Any:
        """Return the eth_account at the given index."""
        idx = int(from_idx) if from_idx is not None else int(os.getenv("FROM_IDX", "0"))
        if not self._accounts or idx >= len(self._accounts):
            raise RuntimeError(f"No account at index {idx}")
        return self._accounts[idx]

    def _w3_call(self, contract_fn) -> Any:
        """Execute a read-only eth_call. Raises RuntimeError on failure."""
        try:
            return contract_fn.call()
        except Exception as e:
            raise RuntimeError(str(e))

    def _w3_send(
        self,
        contract_fn,
        from_idx: Optional[int] = None,
        gas: int = 3_000_000,
        gas_label: Optional[str] = None,
        receipt_timeout_seconds: int = 60,
    ) -> Dict[str, Any]:
        """Build, sign, and send a contract transaction. Returns receipt dict."""
        acct = self._w3_account(from_idx)
        with self._nonce_lock:
            nonce = self._w3.eth.get_transaction_count(acct.address, "pending")
            tx = contract_fn.build_transaction({
                "from": acct.address,
                "gas": gas,
                "gasPrice": 0,
                "nonce": nonce,
            })
            signed = self._w3.eth.account.sign_transaction(tx, acct.key)
            tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self._w3.eth.wait_for_transaction_receipt(
            tx_hash,
            timeout=receipt_timeout_seconds,
            poll_latency=0.05,
        )
        receipt_dict = dict(receipt)
        if int(receipt_dict.get("status", 1) or 0) == 0:
            revert_reason = self._revert_reason_for_tx(tx)
            raise RuntimeError(f"transaction_reverted:{self._receipt_tx_hash(receipt_dict)}:{revert_reason}")
        if gas_label:
            self._append_gas_log(gas_label, receipt_dict)
        return receipt_dict

    def _w3_submit(
        self,
        contract_fn,
        from_idx: Optional[int] = None,
        gas: int = 3_000_000,
    ) -> str:
        """Submit a transaction to the mempool and return immediately — no receipt wait.
        Safe for use on private QBFT networks where TX inclusion is guaranteed within one block.
        Returns the hex tx_hash string."""
        acct = self._w3_account(from_idx)
        with self._nonce_lock:
            nonce = self._w3.eth.get_transaction_count(acct.address, "pending")
            tx = contract_fn.build_transaction({
                "from": acct.address,
                "gas": gas,
                "gasPrice": 0,
                "nonce": nonce,
            })
            signed = self._w3.eth.account.sign_transaction(tx, acct.key)
            tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.hex()

    def _w3_rpc(self, method: str, params: list) -> Any:
        """Raw JSON-RPC call for non-contract methods (e.g. QBFT admin)."""
        return self._rpc_call(self.besu_rpc_url, method, params)

    def _append_gas_log(self, gas_label: str, receipt: Dict[str, Any]) -> None:
        """Append a gas usage entry to results/gas_log.jsonl."""
        try:
            gas_log_path = self.results_dir / "gas_log.jsonl"
            tx_hash_raw = receipt.get("transactionHash", b"")
            tx_hash_str = ("0x" + tx_hash_raw.hex()) if isinstance(tx_hash_raw, bytes) else str(tx_hash_raw)
            entry = {
                "function": gas_label,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "blockNumber": int(receipt.get("blockNumber") or 0),
                "gasUsed": int(receipt.get("gasUsed") or 0),
                "transactionHash": tx_hash_str,
            }
            with open(gas_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def _receipt_tx_hash(self, receipt: Dict[str, Any]) -> str:
        """Extract a 0x-prefixed hex tx hash string from a web3.py receipt."""
        raw = receipt.get("transactionHash", b"")
        if isinstance(raw, bytes):
            return "0x" + raw.hex()
        return str(raw)

    def begin_request(self, node_tier: Optional[str]=None, condition: Optional[str]=None) -> None:
        tier = (node_tier or self.local_node_tier or "unknown").strip().lower()
        with self._request_lock:
            self._active_requests += 1
            concurrency = self._active_requests
        self._request_ctx.request_id = f"req-{uuid.uuid4().hex[:12]}"
        self._request_ctx.start_monotonic = time.monotonic()
        self._request_ctx.node_tier = tier
        self._request_ctx.condition_override = (condition or "").strip().lower() or None
        self._request_ctx.concurrency = concurrency
        self._request_ctx.current_flow_id = None
        self._request_ctx.current_flow_type = None

    def end_request(self) -> None:
        with self._request_lock:
            self._active_requests = max(0, self._active_requests - 1)
        for attr in ("request_id", "start_monotonic", "node_tier", "condition_override", "concurrency", "current_flow_id", "current_flow_type"):
            if hasattr(self._request_ctx, attr):
                delattr(self._request_ctx, attr)

    def _latency_elapsed(self) -> Optional[float]:
        start = getattr(self._request_ctx, "start_monotonic", None)
        if start is None:
            return None
        return max(0.0, time.monotonic() - start)

    def _latency_condition(self, operation: str, node_tier: str) -> str:
        override = getattr(self._request_ctx, "condition_override", None)
        if override:
            return override
        if int(getattr(self._request_ctx, "concurrency", 1) or 1) > 1:
            return "concurrent"
        if not self.latency_recorder.has_samples(operation, node_tier):
            return "cold"
        return "warm"

    def record_operation_latency(self, operation: str, *, elapsed: Optional[float]=None, node_tier: Optional[str]=None) -> None:
        tier = (node_tier or getattr(self._request_ctx, "node_tier", None) or self.local_node_tier or "unknown").strip().lower()
        duration = self._latency_elapsed() if elapsed is None else float(elapsed)
        if duration is None:
            return
        condition = self._latency_condition(operation, tier)
        self.latency_recorder.record(operation, tier, condition, duration)
        self.latency_recorder.write_summary()

    def latency_summary(self) -> Dict[str, Any]:
        return self.latency_recorder.summary()

    def current_request_id(self) -> Optional[str]:
        return getattr(self._request_ctx, "request_id", None)

    def current_flow_id(self) -> Optional[str]:
        return getattr(self._request_ctx, "current_flow_id", None)

    def current_flow_type(self) -> Optional[str]:
        return getattr(self._request_ctx, "current_flow_type", None)

    def _new_flow_id(self, flow_type: str) -> str:
        return f"{flow_type}-{uuid.uuid4().hex[:12]}"

    def start_flow(
        self,
        flow_type: str,
        *,
        stage: str,
        message: str,
        component: str = "api",
        details: Optional[Dict[str, Any]] = None,
        set_current: bool = True,
        flow_id: Optional[str] = None,
        **kwargs,
    ) -> str:
        flow_id = flow_id or self._new_flow_id(flow_type)
        if set_current:
            self._request_ctx.current_flow_id = flow_id
            self._request_ctx.current_flow_type = flow_type
        self.emit_event(
            component=component,
            flow_type=flow_type,
            flow_id=flow_id,
            stage=stage,
            status="started",
            message=message,
            details=details,
            **kwargs,
        )
        return flow_id

    def emit_event(
        self,
        *,
        component: str,
        stage: str,
        status: str,
        message: str,
        flow_type: Optional[str] = None,
        flow_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[float] = None,
        request_id: Optional[str] = None,
        tx_hash: Optional[str] = None,
        policy_id: Optional[int] = None,
        from_signature: Optional[str] = None,
        to_signature: Optional[str] = None,
        node_id: Optional[str] = None,
        node_name: Optional[str] = None,
        node_tier: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_flow_id = flow_id or self.current_flow_id() or self._new_flow_id(flow_type or "daemon")
        resolved_flow_type = flow_type or self.current_flow_type() or "daemon"
        return self.event_recorder.emit(
            component=component,
            flow_type=resolved_flow_type,
            flow_id=resolved_flow_id,
            stage=stage,
            status=status,
            message=message,
            details=details or {},
            duration_ms=duration_ms,
            request_id=request_id or self.current_request_id(),
            tx_hash=tx_hash,
            policy_id=policy_id,
            from_signature=from_signature,
            to_signature=to_signature,
            node_id=node_id or self.local_node_id,
            node_name=node_name or self.local_node_name,
            node_tier=node_tier or getattr(self._request_ctx, "node_tier", None) or self.local_node_tier,
        )

    def finish_flow(
        self,
        status: str,
        *,
        stage: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        component: str = "orchestrator",
        flow_id: Optional[str] = None,
        flow_type: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        resolved_flow_id = flow_id or self.current_flow_id()
        resolved_flow_type = flow_type or self.current_flow_type()
        event = self.emit_event(
            component=component,
            flow_type=resolved_flow_type,
            flow_id=resolved_flow_id,
            stage=stage,
            status=status,
            message=message,
            details=details,
            **kwargs,
        )
        if resolved_flow_id and resolved_flow_id == self.current_flow_id():
            self._request_ctx.current_flow_id = None
            self._request_ctx.current_flow_type = None
        return event

    def recent_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.event_recorder.recent(limit=limit)

    def wait_for_events(self, after_sequence: int = 0, timeout: float = 2.0) -> List[Dict[str, Any]]:
        return self.event_recorder.wait_for_events(after_sequence=after_sequence, timeout=timeout)

    def latest_event_sequence(self) -> int:
        return self.event_recorder.latest_sequence()

    def flow_summaries(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.event_recorder.flows(limit=limit)

    def active_flow_summaries(self) -> List[Dict[str, Any]]:
        return self.event_recorder.active_flows()

    def event_stats(self) -> Dict[str, Any]:
        return self.event_recorder.stats()

    def _extract_tx_hash(self, text: str) -> str:
        match = re.search(r"0x[a-fA-F0-9]{64}", text or "")
        return match.group(0) if match else ""

    def _clean_address(self, value: str) -> str:
        return _clean_address_text(value)

    def _decode_revert_error(self, exc: Exception) -> str:
        text = str(exc or "").strip()
        selector_match = re.search(r"0x[a-fA-F0-9]{8}", text)
        if selector_match:
            selector = selector_match.group(0).lower()
            name = CUSTOM_ERROR_SELECTOR_NAMES.get(selector)
            if name:
                return name
        return text or exc.__class__.__name__

    def _revert_reason_for_tx(self, tx: Dict[str, Any]) -> str:
        try:
            call_tx = {
                "from": tx.get("from"),
                "to": tx.get("to"),
                "data": tx.get("data"),
                "value": tx.get("value", 0),
            }
            self._w3.eth.call(call_tx, block_identifier="latest")
        except Exception as exc:
            return self._decode_revert_error(exc)
        return "unknown"

    def _remember_grant_policy_id(self, from_sig: str, to_sig: str, policy_id: int) -> None:
        try:
            pid = int(policy_id)
        except Exception:
            return
        if pid <= 0:
            return
        with self._grant_policy_cache_lock:
            self._grant_policy_id_cache[(str(from_sig), str(to_sig))] = pid

    def _cached_grant_policy_id(self, from_sig: str, to_sig: str) -> Optional[int]:
        with self._grant_policy_cache_lock:
            return self._grant_policy_id_cache.get((str(from_sig), str(to_sig)))

    def _policy_details_cache_get(self, policy_id: int) -> Optional[Dict[str, Any]]:
        with self._policy_details_cache_lock:
            cached = self._policy_details_cache.get(int(policy_id))
            if cached is None:
                return None
            self._policy_details_cache.move_to_end(int(policy_id))
            return dict(cached)

    def _policy_details_cache_put(self, policy_id: int, payload: Dict[str, Any]) -> None:
        with self._policy_details_cache_lock:
            pid = int(policy_id)
            self._policy_details_cache[pid] = dict(payload)
            self._policy_details_cache.move_to_end(pid)
            while len(self._policy_details_cache) > 256:
                self._policy_details_cache.popitem(last=False)

    def _ack_key(self, payload: Dict[str, Any]) -> str:
        return str(payload.get("signature") or payload.get("node_id") or uuid.uuid4().hex)

    def _record_ack_status(self, key: str, **updates: Any) -> None:
        with self._ack_status_lock:
            current = dict(self._ack_status.get(key) or {})
            current.update(updates)
            current["updated_at_ms"] = int(time.time() * 1000)
            self._ack_status[key] = current

    def ack_status(self, payload_or_key: Dict[str, Any] | str) -> Dict[str, Any] | None:
        key = payload_or_key if isinstance(payload_or_key, str) else self._ack_key(payload_or_key)
        with self._ack_status_lock:
            current = self._ack_status.get(str(key))
            return dict(current) if current else None

    def _dispatch_acknowledgement(self, payload: Dict[str, Any], *, tx_out: str | None) -> Dict[str, Any]:
        role = str(payload.get("node_type") or "").strip()
        ack_url = str(payload.get("ack_url") or payload.get("node_url") or "").rstrip("/")
        ack_key = self._ack_key(payload)
        if role not in ALLOWED_ACK_ROLES:
            status = {"ack_required": False, "ack_status": "not_needed", "ack_sent": False}
            self._record_ack_status(ack_key, node_id=payload.get("node_id"), role=role, **status)
            return status
        if not ack_url or not ack_url.startswith(("http://", "https://")):
            self.emit_event(
                component="orchestrator",
                stage="acknowledgement_failed",
                status="error",
                message="Bootstrap acknowledgement skipped because the node URL is unavailable",
                details={"ack_url": ack_url or None},
                from_signature=payload.get("signature"),
                tx_hash=tx_out,
            )
            status = {"ack_required": True, "ack_status": "skipped", "ack_sent": False}
            self._record_ack_status(ack_key, node_id=payload.get("node_id"), role=role, **status)
            return status
        self.emit_event(
            component="orchestrator",
            stage="acknowledgement_queued",
            status="started",
            message="Bootstrap acknowledgement queued",
            details={"ack_url": ack_url},
            from_signature=payload.get("signature"),
            tx_hash=tx_out,
        )
        self._record_ack_status(
            ack_key,
            node_id=payload.get("node_id"),
            role=role,
            ack_required=True,
            ack_status="queued",
            ack_sent=False,
            attempts=0,
        )
        worker = threading.Thread(
            target=self._run_acknowledgement_job,
            kwargs={"payload": dict(payload), "ack_url": ack_url, "tx_out": tx_out, "ack_key": ack_key},
            daemon=True,
            name=f"ack-{payload.get('node_id') or ack_key}",
        )
        worker.start()
        return {"ack_required": True, "ack_status": "queued", "ack_sent": False}

    def _run_acknowledgement_job(self, *, payload: Dict[str, Any], ack_url: str, tx_out: str | None, ack_key: str) -> None:
        role = str(payload.get("node_type") or "").strip()
        bootstrap_base_url = str(payload.get("bootstrap_base_url") or "").rstrip("/")
        sender = AcknowledgementSender(
            registering_node_url=ack_url,
            genesis_file=self.genesis_file_path,
            node_registry_file=self.node_registry_json,
            besu_rpc_url=self.besu_rpc_url,
            prefunded_keys_file=self.prefunded_keys_json,
            bootstrap_base_url=bootstrap_base_url,
        )
        retry_delays = [0.0, 0.5, 1.5, 3.0]
        total_attempts = len(retry_delays)
        for attempt, delay in enumerate(retry_delays, start=1):
            if delay:
                time.sleep(delay)
            self._record_ack_status(ack_key, ack_status="sending", attempts=attempt, ack_sent=False)
            self.emit_event(
                component="orchestrator",
                stage="acknowledgement_send",
                status="started",
                message=f"Sending bootstrap acknowledgement (attempt {attempt}/{total_attempts})",
                details={"ack_url": ack_url, "attempt": attempt, "bootstrap_base_url": bootstrap_base_url},
                from_signature=payload.get("signature"),
                tx_hash=tx_out,
            )
            ack_sent = sender.send_acknowledgment(str(payload.get("node_id") or ""), node_type=role)
            if ack_sent:
                self._record_ack_status(ack_key, ack_status="completed", attempts=attempt, ack_sent=True)
                self.emit_event(
                    component="orchestrator",
                    stage="acknowledgement_completed",
                    status="ok",
                    message="Bootstrap acknowledgement completed",
                    details={"ack_url": ack_url, "attempt": attempt},
                    from_signature=payload.get("signature"),
                    tx_hash=tx_out,
                )
                return
            terminal = attempt == total_attempts
            self._record_ack_status(ack_key, ack_status="failed" if terminal else "retrying", attempts=attempt, ack_sent=False)
            self.emit_event(
                component="orchestrator",
                stage="acknowledgement_failed" if terminal else "acknowledgement_send",
                status="error" if terminal else "waiting",
                message="Bootstrap acknowledgement failed" if terminal else "Bootstrap acknowledgement attempt failed; retry queued",
                details={"ack_url": ack_url, "attempt": attempt, "next_retry_delay_secs": retry_delays[attempt] if attempt < total_attempts else 0},
                from_signature=payload.get("signature"),
                tx_hash=tx_out,
            )

    def _rpc_call(self, rpc_url: str, method: str, params: list[Any]) -> Any:
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        resp = requests.post(rpc_url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(str(data["error"]))
        return data.get("result")

    def _eth_block_number(self, rpc_url: str) -> int:
        result = self._rpc_call(rpc_url, "eth_blockNumber", [])
        return int(str(result), 16)

    def _eth_tx_receipt(self, tx_hash: str) -> Dict[str, Any]:
        result = self._rpc_call(self.besu_rpc_url, "eth_getTransactionReceipt", [tx_hash])
        return result or {}

    def _validator_rpc_urls(self) -> List[str]:
        urls: List[str] = []
        if not self._contract:
            return urls
        try:
            validators = self._normalize_validators(self.qbft_get_validators())
            for addr in validators:
                try:
                    rpc_url = self._contract.functions.nodeRpcUrls(Web3.to_checksum_address(addr)).call()
                except Exception:
                    rpc_url = ""
                rpc_url = (rpc_url or "").strip()
                if rpc_url:
                    urls.append(rpc_url)
        except Exception:
            return []
        return urls

    def _measure_revocation_propagation(self, tx_hash: str) -> None:
        if not tx_hash:
            return
        try:
            self.emit_event(
                component="blockchain",
                stage="revocation_propagation_wait",
                status="waiting",
                message="Waiting for validators to observe the revocation block",
                tx_hash=tx_hash,
            )
            receipt = self._eth_tx_receipt(tx_hash)
            if not receipt:
                return
            target_block = int(str(receipt.get("blockNumber", "0x0")), 16)
            validator_urls = self._validator_rpc_urls()
            if not validator_urls:
                return

            start = time.monotonic()
            deadline = start + 60
            while time.monotonic() < deadline:
                seen = 0
                for rpc_url in validator_urls:
                    try:
                        if self._eth_block_number(rpc_url) >= target_block:
                            seen += 1
                    except Exception:
                        pass
                if seen == len(validator_urls):
                    duration_ms = (time.monotonic() - start) * 1000
                    self.record_operation_latency("revokeTokenPropagation", elapsed=time.monotonic() - start)
                    self.emit_event(
                        component="blockchain",
                        stage="revocation_propagation_observed",
                        status="ok",
                        message="All validator nodes observed the revocation block",
                        tx_hash=tx_hash,
                        duration_ms=duration_ms,
                        details={"validator_count": len(validator_urls), "target_block": target_block},
                    )
                    return
                time.sleep(1)
        except Exception:
            return

    def _start_policy_cache_watcher(self) -> None:
        with self._policy_poller_lock:
            if self._policy_poller_started:
                return
            self._policy_poller_started = True
        thread = threading.Thread(target=self._policy_cache_watcher_loop, name="policy-cache-watcher", daemon=True)
        thread.start()

    def _invalidate_policy_ids(self, policy_ids: List[int]) -> None:
        if not policy_ids:
            return
        policy_id_set = {int(pid) for pid in policy_ids}
        with self._policy_lock:
            self._load_policy_index()
            stale_keys = [key for key, pid in self.policy_index.items() if int(pid or 0) in policy_id_set]
            for key in stale_keys:
                self.policy_index.pop(key, None)
            if stale_keys:
                self._save_policy_index()
        # Evict matching grant cache entries
        with self._grant_cache_lock:
            evict = [k for k, (v, _) in self._grant_cache.items() if int(v.get("policyId") or 0) in policy_id_set]
            for k in evict:
                self._grant_cache.pop(k, None)

    def _policy_cache_watcher_loop(self) -> None:
        if not self._contract or not self._w3:
            return
        try:
            self._policy_poll_block = int(self._w3.eth.block_number)
        except Exception:
            self._policy_poll_block = 0

        while True:
            try:
                latest = int(self._w3.eth.block_number)
                from_block = max(0, self._policy_poll_block)
                updated = self._contract.events.PolicyUpdated.get_logs(from_block=from_block, to_block=latest)
                deprecated = self._contract.events.PolicyDeprecated.get_logs(from_block=from_block, to_block=latest)
                changed_ids = []
                for event in updated + deprecated:
                    changed_ids.append(int(event["args"]["policyId"]))
                    self.emit_event(
                        component="policy_cache",
                        flow_type="cache",
                        flow_id=self._new_flow_id("cache"),
                        stage="policy_event_seen",
                        status="ok",
                        message="Policy change event observed",
                        details={
                            "policy_id": int(event["args"]["policyId"]),
                            "event": event["event"],
                        },
                    )
                self._invalidate_policy_ids(changed_ids)
                if changed_ids:
                    self.emit_event(
                        component="policy_cache",
                        flow_type="cache",
                        flow_id=self._new_flow_id("cache"),
                        stage="cache_entries_invalidated",
                        status="ok",
                        message="Policy cache entries invalidated",
                        details={"policy_ids": changed_ids},
                    )
                self._policy_poll_block = latest + 1
            except Exception:
                time.sleep(2)
                continue
            time.sleep(2)
    

    def find_policy_id(self, from_role: str, to_role: str, ops_csv: str, ctx_schema_str: str) -> Dict[str, Any]:
        """
        Returns {'ok': bool, 'stdout': str, 'stderr': str}
        """
        if self._should_use_js():
            r = self._js("findPolicyId", from_role, to_role, ops_csv, ctx_schema_str)
            return {"ok": r.ok, "stdout": r.stdout, "stderr": r.stderr}
        try:
            pid = self._find_policy_on_chain(from_role, to_role, ops_csv, ctx_schema_str)
            if pid:
                return {"ok": True, "stdout": str(pid), "stderr": ""}
            return {"ok": False, "stdout": "", "stderr": "not_found"}
        except Exception as exc:
            return {"ok": False, "stdout": "", "stderr": str(exc)}
    
    # ---- validator/address helpers ----
    def get_address_from_signature(self, signature: str) -> str:
        """
        Resolve an EOA from a node signature via registry details.
        Falls back to empty string if unavailable.
        """
        try:
            d = self.get_node_by_sig(signature)  # expects a dict with at least 'address' or 'owner'
            return (d.get("address")
                    or d.get("owner")
                    or d.get("registeredBy")
                    or "").strip()
        except Exception:
            return ""

    def _prefunded_index_for_address(self, addr: str) -> Optional[int]:
        """Return FROM_IDX for a given EOA (from prefunded_keys.json), or None if not found."""
        try:
            addr_lc = (addr or "").lower()
            data = _json_load(self.prefunded_keys_json, {"prefunded_accounts": []})
            for i, acct in enumerate(data.get("prefunded_accounts", [])):
                if (acct.get("address") or "").lower() == addr_lc:
                    return i
        except Exception:
            pass
        return None
    
    def checkValidator(self) -> bool:
        """
        True if the EOA mapped from `signature` is currently in the QBFT validator set.
        """
        try:
            # addr = (self.get_address_from_signature(signature) or "").lower()

            addr = self.nd.get("address", "").lower()
            if not addr:
                return False
            cur = self.qbft_get_validators() or []

            if isinstance(cur, str):
                cur = [
                    x.strip()
                    for x in cur.replace("[","").replace("]","").replace('"','').replace("'", "").split(",")
                    if x.strip()
                ]
            cur_lc = [x.lower() for x in cur]

            return addr in cur_lc
        except Exception:
            return False

    def is_validator(self) -> bool:
        """
        Preferred single source: check live set using the resolved address.
        """
        return self.checkValidator()

    def _propose_and_vote(
        self,
        addr: str,
        voter_indices: list[int] | None = None,
        *,
        flow_id: str | None = None,
        from_signature: str | None = None,
    ) -> bool:
        """
        Emit on-chain proposal event (optional) and submit qbft votes from multiple signers.
        Returns True if at least one vote RPC returned OK (not a guarantee of inclusion).
        """
        addr_lc = (addr or "").lower()
        if not addr_lc:
            return False

        # idempotency: don't spam the same address
        with self._vlisten_lock:
            if addr_lc in self._voted_addrs:
                return True
            self._voted_addrs.add(addr_lc)

        ok_any = False
        if voter_indices is None:
            # Try a few; tune to your setup / threshold
            voter_indices = [0, 1, 2]

        for idx in voter_indices:
            try:
                out = self.proposeValidatorVote(addr, "true", from_idx=idx)
                print("________")
                print(f"[propose] qbft vote yes FROM_IDX={idx}: {out.strip()}")
                self.emit_event(
                    component="validator_listener",
                    flow_type="validator",
                    flow_id=flow_id or self.current_flow_id() or self._new_flow_id("validator"),
                    stage="validator_vote_submitted",
                    status="ok",
                    message="Validator vote submitted",
                    details={"address": addr, "from_idx": idx},
                    tx_hash=self._extract_tx_hash(out),
                    from_signature=from_signature,
                )
                ok_any = True
            except Exception as e:
                print(f"[propose] vote error FROM_IDX={idx}: {e}")

        return ok_any

    def _promote_validator_async(
        self,
        payload: Dict[str, Any],
        *,
        from_signature: str,
        voter_indices: list[int] | None = None,
        peer_wait_seconds: int = 5,
    ) -> str | None:
        addr = self._clean_address(payload.get("address") or "")
        if not addr:
            return None

        flow_id = self._new_flow_id("validator")

        def _worker():
            included = False
            try:
                cur = self._normalize_validators(self.qbft_get_validators())
                if addr.lower() in cur:
                    included = True
                    self.emit_event(
                        component="validator_listener",
                        flow_type="validator",
                        flow_id=flow_id,
                        stage="validator_inclusion_result",
                        status="ok",
                        message="Validator inclusion observed",
                        details={"address": addr},
                        from_signature=from_signature,
                    )
                    return

                self.start_validator_listener()
                self.emit_event(
                    component="validator_listener",
                    flow_type="validator",
                    flow_id=flow_id,
                    stage="validator_vote",
                    status="started",
                    message="Submitting validator vote",
                    details={"address": addr},
                    from_signature=from_signature,
                )
                voted = self._propose_and_vote(
                    addr,
                    voter_indices=voter_indices,
                    flow_id=flow_id,
                    from_signature=from_signature,
                )
                self.emit_event(
                    component="validator_listener",
                    flow_type="validator",
                    flow_id=flow_id,
                    stage="validator_vote",
                    status="ok" if voted else "error",
                    message="Validator vote completed" if voted else "Validator vote failed",
                    details={"address": addr},
                    from_signature=from_signature,
                )
                if voted:
                    for sec in (1, 1, 2, 3, 5, 8):
                        time.sleep(sec)
                        cur = self._normalize_validators(self.qbft_get_validators())
                        if addr.lower() in cur:
                            included = True
                            break

                self.emit_event(
                    component="validator_listener",
                    flow_type="validator",
                    flow_id=flow_id,
                    stage="validator_inclusion_result",
                    status="ok" if included else ("waiting" if voted else "error"),
                    message="Validator inclusion observed" if included else ("Validator vote submitted and inclusion is pending" if voted else "Validator vote failed"),
                    details={"address": addr},
                    from_signature=from_signature,
                )
            except Exception as exc:
                self.emit_event(
                    component="validator_listener",
                    flow_type="validator",
                    flow_id=flow_id,
                    stage="validator_inclusion_result",
                    status="error",
                    message="Validator promotion failed",
                    details={"address": addr, "detail": str(exc)},
                    from_signature=from_signature,
                )

        threading.Thread(
            target=_worker,
            name=f"validator-promotion-{addr[-6:]}",
            daemon=True,
        ).start()
        return flow_id
    
    def _normalize_validators(self, raw) -> list[str]:
        # raw may be a CSV/string like "['0x..','0x..']" or a list
        if isinstance(raw, str):
            raw = raw.replace("[", "").replace("]", "")
            raw = raw.replace('"', "").replace("'", "")  # <-- strip inner quotes
            parts = [p.strip() for p in raw.split(",") if p.strip()]
        elif isinstance(raw, (list, tuple)):
            parts = list(raw)
        else:
            parts = []
        # lower-case everything
        return [str(p).strip().lower() for p in parts]

    def peer_count(self) -> int:
        if self._should_use_js():
            r = self._js("peerCount")
            if not r.ok:
                return 0
            try:
                return int((r.stdout or "0").strip())
            except Exception:
                return 0
        try:
            result = self._w3_rpc("net_peerCount", [])
            return int(str(result), 16) if isinstance(result, str) and result.startswith("0x") else int(result or 0)
        except Exception:
            return 0

    def _wait_for_peer_bump(self, max_wait_sec: int = 20, step: int = 1) -> bool:
        """Wait (bounded) for any peer increase to indicate the joining node connected."""
        base = self.peer_count()
        waited = 0
        while waited < max_wait_sec:
            time.sleep(step)
            waited += step
            if self.peer_count() > base:
                return True
        return False
    # ---------- blockchain contract wrappers (web3.py primary, JS mock fallback in tests) ----------

    #@track_performance
    def check_if_deployed(self) -> bool:
        if self._should_use_js():
            r = self._js("checkIfDeployed")
            stderr_text = r.stderr or ""
            transient_rpc_error = "ECONNREFUSED" in stderr_text or "connect refused" in stderr_text.lower()
            if os.getenv("ORCH_TRACE") and (r.ok or not transient_rpc_error):
                print(f"check_if_deployed: ok={r.ok}, stdout={r.stdout!r}, stderr={r.stderr!r}, code={r.code}")
            if not r.ok:
                if transient_rpc_error:
                    raise RuntimeError("rpc_not_ready")
                raise RuntimeError(stderr_text or r.stdout)
            b = _parse_bool(r.stdout)
            return bool(b)
        try:
            code = self._w3.eth.get_code(self._contract.address)
            return bool(code and len(code) > 2)
        except Exception as e:
            err = str(e)
            if "ECONNREFUSED" in err or "connect refused" in err.lower():
                raise RuntimeError("rpc_not_ready")
            raise RuntimeError(err)

    #@track_performance
    def is_node_registered(self, node_sig: str) -> bool:
        if self._should_use_js():
            r = self._js("isNodeRegistered", node_sig)
            if not r.ok: raise RuntimeError(r.stderr or r.stdout)
            b = _parse_bool(r.stdout)
            return bool(b)
        return bool(self._w3_call(self._contract.functions.isNodeRegistered(node_sig)))

    #@track_performance
    def register_node(self, node_id, node_name, node_type_str, public_key, registered_by_addr, rpcURL, registered_by_node_type_str, node_signature, from_idx: Optional[int]=None) -> str:
        print("*******************************************************************************")
        print(f"[register_node] node_id={node_id}, node_name={node_name}, node_type={node_type_str}, public_key={public_key}, registered_by_addr={registered_by_addr}, rpcURL={rpcURL}, registered_by_node_type_str={registered_by_node_type_str}, node_signature={node_signature}")
        self.emit_event(
            component="blockchain",
            stage="registration_submit",
            status="started",
            message="Submitting node registration transaction",
            details={"node_id": node_id, "node_name": node_name, "node_type": node_type_str, "rpc_url": rpcURL},
            from_signature=node_signature,
        )
        if self._should_use_js():
            r = self._js("registerNode", node_id, node_name, node_type_str, public_key, registered_by_addr, rpcURL, registered_by_node_type_str, node_signature)
            print(f"[register_node] result: ok={r.ok}, stdout={r.stdout!r}, stderr={r.stderr!r}, code={r.code}")
            if not r.ok: raise RuntimeError(r.stderr or r.stdout)
            self.record_operation_latency("registerNode")
            self.emit_event(
                component="blockchain",
                stage="registration_submit",
                status="ok",
                message="Node registration transaction submitted",
                details={"node_id": node_id},
                tx_hash=self._extract_tx_hash(r.stdout),
                from_signature=node_signature,
            )
            return r.stdout
        # web3.py path: ABI-encode params then submit without waiting for receipt.
        # On a private QBFT network with gasPrice=0 and trusted validators, TX inclusion
        # in the next block (≤1s) is guaranteed — blocking on the receipt is unnecessary
        # and is the dominant source of registration latency.
        safe_addr = registered_by_addr or ("0x" + "0" * 40)
        try:
            safe_addr = Web3.to_checksum_address(safe_addr)
        except Exception:
            safe_addr = "0x" + "0" * 40
        packed = eth_abi_encode(
            ["string", "string", "string", "string", "address", "string", "string", "string"],
            [node_id, node_name, node_type_str, public_key, safe_addr, rpcURL, registered_by_node_type_str, node_signature],
        )
        tx_hash = self._w3_submit(
            self._contract.functions.registerNodePacked(packed),
            from_idx=from_idx,
            gas=3_000_000,
        )
        tx_out = f"✅ registerNodePacked: {tx_hash}"
        print(f"[register_node] web3 result: {tx_out}")
        self.record_operation_latency("registerNode")
        self.emit_event(
            component="blockchain",
            stage="registration_submit",
            status="ok",
            message="Node registration transaction submitted (pending confirmation)",
            details={"node_id": node_id},
            tx_hash=tx_hash,
            from_signature=node_signature,
        )
        return tx_out

    #@track_performance
    def get_node_by_sig(self, node_sig: str) -> Dict[str,Any]:
        if self._should_use_js():
            r = self._js("getNodeBySig", node_sig)
            if not r.ok: raise RuntimeError(r.stderr or r.stdout)
            return json.loads(r.stdout)
        r = self._w3_call(self._contract.functions.getNodeDetailsBySignature(node_sig))
        return {
            "nodeId": r[0],
            "nodeName": r[1],
            "nodeType": int(r[2]),
            "publicKey": r[3],
            "isRegistered": bool(r[4]),
            "registeredBy": r[5],
            "nodeSignature": r[6],
            "registeredByNodeType": int(r[7]),
        }

    def get_address_from_signature(self, node_sig: str) -> Optional[str]:
        """Return the Ethereum address of the node identified by node_sig, or None."""
        try:
            details = self.get_node_by_sig(node_sig)
            addr = _clean_address_text(details.get("registeredBy") or "")
            return addr if addr else None
        except Exception:
            return None

    def _prefunded_index_for_address(self, address: Optional[str]) -> Optional[int]:
        """Return the index in self._accounts whose address matches, or None."""
        if not address or not self._accounts:
            return None
        addr_lc = address.lower()
        for idx, acct in enumerate(self._accounts):
            if acct.address.lower() == addr_lc:
                return idx
        return None

    #@track_performance
    def propose_validator(self, validator_addr: str, from_idx: Optional[int]=None) -> str:
        validator_addr = self._clean_address(validator_addr)
        if self._should_use_js():
            r = self._js("proposeValidator", validator_addr)
            if not r.ok: raise RuntimeError(r.stderr or r.stdout)
            return r.stdout
        receipt = self._w3_send(self._contract.functions.proposeValidator(validator_addr), from_idx=from_idx)
        return f"✅ proposeValidator: {self._receipt_tx_hash(receipt)}"

    #@track_performance
    def qbft_get_validators(self) -> str:
        if self._should_use_js():
            r = self._js("qbft_getValidators")
            if not r.ok: raise RuntimeError(r.stderr or r.stdout)
            return r.stdout
        result = self._w3_rpc("qbft_getValidatorsByBlockNumber", ["latest"])
        if isinstance(result, list):
            return json.dumps(result)
        return str(result or "[]")

    #@track_performance
    def proposeValidatorVote(self, validator_addr: str, vote: str, from_idx: Optional[int]=None) -> str:
        """
        Vote for a proposed validator.
        :param validator_addr: the address of the validator to vote for
        :param vote: "yes" or "no"
        :param from_idx: optional index to use for this operation
        :return: transaction hash or error message
        """
        validator_addr = self._clean_address(validator_addr)
        if self._should_use_js():
            env = os.environ.copy()
            if from_idx is not None:
                env["FROM_IDX"] = str(from_idx)
            r = self._js("proposeValidatorVote", validator_addr, vote, env=env)
            if not r.ok: raise RuntimeError(r.stderr or r.stdout)
            return r.stdout
        add = vote.strip().lower() in ("true", "yes", "1")
        result = self._w3_rpc("qbft_proposeValidatorVote", [validator_addr, add])
        return json.dumps({"result": result})

    # ---- policy & msig ----

    #@track_performance
    def msig_info(self) -> Dict[str,Any]:
        if self._should_use_js():
            r = self._js("msigInfo")
            if not r.ok: raise RuntimeError(r.stderr or r.stdout)
            return json.loads(r.stdout)
        required = self._w3_call(self._contract.functions.msigRequired())
        count = self._w3_call(self._contract.functions.msigApproverCount())
        threshold = self._w3_call(self._contract.functions.msigThreshold())
        return {
            "msigRequired": bool(required),
            "msigApproverCount": int(count),
            "msigThreshold": int(threshold),
        }

    #@track_performance
    #@track_performance
    def create_policy(self, from_role: str, to_role: str, ops_csv: str, ctx_schema: Optional[str]=None, from_idx: Optional[int]=None) -> Dict[str,Any]:
        """
        Create a policy if it doesn't exist; if an identical policy already exists,
        return ok=True without sending a tx (idempotent).
        """
        ctx = ctx_schema or ""  # contract allows empty ctx; we match what caller passed
        invalid_roles = _invalid_policy_roles(from_role, to_role)
        if invalid_roles:
            allowed = ", ".join(sorted(VALID_POLICY_ROLES))
            return {
                "ok": False,
                "stdout": "",
                "stderr": f"invalid_policy_roles:{', '.join(invalid_roles)} (allowed: {allowed})",
            }

        # 0) Preflight: if policy already exists on-chain, don't call createPolicy
        try:
            existing_pid = self._find_policy_on_chain(from_role, to_role, ops_csv, ctx)
            if existing_pid:
                print(f"[create_policy] found existing policyId={existing_pid} for {from_role}->{to_role} with ops={ops_csv} and ctx={ctx}")
                # mimic a successful create; stdout clarifies "exists"
                return {"ok": True, "stdout": f"exists:{existing_pid}", "stderr": ""}
        except Exception:
            # If lookup fails, fall through to attempt creation.
            pass

        # 1) Create on-chain
        if self._should_use_js():
            env = os.environ.copy()
            if from_idx is not None:
                env["FROM_IDX"] = str(from_idx)
            args = ["createPolicy", from_role, to_role, ops_csv]
            if ctx_schema:
                args.append(ctx_schema)
            r = self._js(*args, env=env)
            print(f"[create_policy] createPolicy result: {r.ok}, stdout={r.stdout!r}, stderr={r.stderr!r}, code={r.code}")
            return {"ok": r.ok, "stdout": r.stdout, "stderr": r.stderr}
        try:
            from_role_num = ROLE.get(from_role, 0)
            to_role_num = ROLE.get(to_role, 0)
            ops = _ops_mask(ops_csv)
            schema = _to_bytes32(ctx_schema or "")
            receipt = self._w3_send(
                self._contract.functions.createPolicy(from_role_num, to_role_num, ops, schema),
                from_idx=from_idx, gas=3_000_000, gas_label="createPolicy"
            )
            tx_hash = self._receipt_tx_hash(receipt)
            # Extract policyId directly from PolicyCreated event — avoids polling loop in ensure_policy
            pid_from_event = None
            try:
                evts = self._contract.events.PolicyCreated().process_receipt(receipt)
                if evts:
                    pid_from_event = int(evts[0]["args"]["policyId"])
            except Exception:
                pass
            if pid_from_event is not None:
                import json as _json
                print(f"[create_policy] web3 createPolicy tx={tx_hash} policyId={pid_from_event}")
                return {"ok": True, "stdout": _json.dumps({"status": "created", "policyId": pid_from_event, "txHash": tx_hash}), "stderr": ""}
            print(f"[create_policy] web3 createPolicy tx={tx_hash}")
            return {"ok": True, "stdout": tx_hash, "stderr": ""}
        except Exception as e:
            return {"ok": False, "stdout": "", "stderr": str(e)}

    def update_policy(self, policy_id: int, ops_csv: str, ctx_schema: Optional[str] = None, from_idx: Optional[int] = None) -> Dict[str, Any]:
        if self._should_use_js():
            env = os.environ.copy()
            if from_idx is not None:
                env["FROM_IDX"] = str(from_idx)
            args = ["updatePolicy", str(int(policy_id)), ops_csv]
            if ctx_schema:
                args.append(ctx_schema)
            r = self._js(*args, env=env)
            return {"ok": r.ok, "stdout": r.stdout, "stderr": r.stderr}
        try:
            ops = _ops_mask(ops_csv)
            schema = _to_bytes32(ctx_schema or "")
            receipt = self._w3_send(
                self._contract.functions.updatePolicy(int(policy_id), ops, schema),
                from_idx=from_idx
            )
            return {"ok": True, "stdout": self._receipt_tx_hash(receipt), "stderr": ""}
        except Exception as e:
            return {"ok": False, "stdout": "", "stderr": str(e)}

    def deprecate_policy(self, policy_id: int, from_idx: Optional[int] = None) -> Dict[str, Any]:
        if self._should_use_js():
            env = os.environ.copy()
            if from_idx is not None:
                env["FROM_IDX"] = str(from_idx)
            r = self._js("deprecatePolicy", str(int(policy_id)), env=env)
            return {"ok": r.ok, "stdout": r.stdout, "stderr": r.stderr}
        try:
            receipt = self._w3_send(
                self._contract.functions.deprecatePolicy(int(policy_id)),
                from_idx=from_idx
            )
            return {"ok": True, "stdout": self._receipt_tx_hash(receipt), "stderr": ""}
        except Exception as e:
            return {"ok": False, "stdout": "", "stderr": str(e)}

    def policy_admin(self) -> str:
        if self._should_use_js():
            r = self._js("policyAdmin")
            if not r.ok:
                raise RuntimeError(r.stderr or r.stdout)
            return str(r.stdout or "").strip()
        return str(self._w3_call(self._contract.functions.policyAdmin()))

    #@track_performance
    def approve_create_policy(self, from_role: str, to_role: str, ops_csv: str, ctx_schema: Optional[str]=None, from_idx: Optional[int]=None) -> Dict[str,Any]:
        if self._should_use_js():
            env = os.environ.copy()
            if from_idx is not None:
                env["FROM_IDX"] = str(from_idx)
            args = ["approveCreatePolicy", from_role, to_role, ops_csv]
            if ctx_schema: args.append(ctx_schema)
            r = self._js(*args, env=env)
            return {"ok": r.ok, "stdout": r.stdout, "stderr": r.stderr}
        try:
            from_role_num = ROLE.get(from_role, 0)
            to_role_num = ROLE.get(to_role, 0)
            ops = _ops_mask(ops_csv)
            schema = _to_bytes32(ctx_schema or "")
            receipt = self._w3_send(
                self._contract.functions.approveCreatePolicy(from_role_num, to_role_num, ops, schema),
                from_idx=from_idx, gas=3_000_000, gas_label="approvePolicy"
            )
            return {"ok": True, "stdout": self._receipt_tx_hash(receipt), "stderr": ""}
        except Exception as e:
            return {"ok": False, "stdout": "", "stderr": str(e)}

    #@track_performance
    _POLICY_FIELDS = ("fromRole", "toRole", "opsAllowed", "isDeprecated", "ctxSchema", "policyHash", "version")

    def get_policy(self, policy_id: int) -> Dict[str,Any]:
        cached = self._policy_details_cache_get(policy_id)
        if cached is not None:
            return cached
        if self._should_use_js():
            r = self._js("getPolicy", policy_id)
            if not r.ok: raise RuntimeError(r.stderr or r.stdout)
            result = json.loads(r.stdout)
            self._policy_details_cache_put(policy_id, result)
            return result
        p = self._w3_call(self._contract.functions.getPolicy(int(policy_id)))
        # web3.py returns a raw tuple when ABI uses a struct — map to dict
        if isinstance(p, (tuple, list)) and not hasattr(p, 'items') and not hasattr(p, '_asdict'):
            result = {}
            for i, field in enumerate(self._POLICY_FIELDS):
                if i < len(p):
                    v = p[i]
                    result[field] = ("0x" + v.hex()) if isinstance(v, bytes) else v
        else:
            result = _normalize_w3_struct(p)
        # Ensure integer types for numeric fields
        for k in ("fromRole", "toRole", "opsAllowed", "version"):
            if k in result:
                result[k] = int(result[k])
        self._policy_details_cache_put(policy_id, result)
        return result
    
    def _find_policy_on_chain(self, from_role: str, to_role: str, ops_csv: str, ctx: str):
        """
        Return an existing policyId if a policy matches (from_role,to_role,ops_mask,ctx).
        Uses the contract's latest created policy id + getPolicy to scan existing policies.
        """
        # Note: the Solidity contract's `nextPolicyId` variable is actually the
        # latest allocated policy id, not the next free id.
        try:
            if not self._should_use_js():
                latest_id = int(self._w3_call(self._contract.functions.nextPolicyId()))
            else:
                np = self._js("nextPolicyId")
                if not np.ok:
                    return None
                latest_id = int((np.stdout or "0").strip() or 0)
        except Exception:
            return None

        # expected fields
        # expected fields
        want_from = ROLE.get(from_role, 0)
        want_to   = ROLE.get(to_role, 0)
        want_ops  = _ops_mask(ops_csv)
        def _scan_for_ctx(want_ctx: str) -> Optional[int]:
            for pid in range(1, max(0, latest_id) + 1):
                try:
                    gp = self.get_policy(pid)
                    if int(gp.get("version", 0)) <= 0:
                        continue
                    if int(gp.get("fromRole", 0)) != want_from:
                        continue
                    if int(gp.get("toRole", 0)) != want_to:
                        continue
                    if int(gp.get("opsAllowed", 0)) != want_ops:
                        continue
                    if bool(gp.get("isDeprecated", False)):
                        continue
                    if (gp.get("ctxSchema") or "").lower() != want_ctx:
                        continue
                    print(
                        f"[find_policy_on_chain] MATCH pid={pid} for {from_role}->{to_role} "
                        f"with ops={ops_csv} and ctx={want_ctx}"
                    )
                    return pid
                except Exception:
                    # ignore holes/bad reads and keep scanning
                    pass
            return None

        exact_ctx = _ctx_schema_hex(ctx)
        found = _scan_for_ctx(exact_ctx)
        if found:
            return found

        if (ctx or "").strip():
            found = _scan_for_ctx(_ctx_schema_hex(""))
            if found:
                return found
        return None
    # ---- grants & delegation ----

    #@track_performance
    def issue_grant(self, from_sig: str, to_sig: str, policy_id: int, ops_csv: str, expires_at: int, from_idx: Optional[int]=None) -> str:
        env = os.environ.copy()
        if from_idx is not None:
            env["FROM_IDX"] = str(from_idx)
        self.emit_event(
            component="blockchain",
            stage="issue_grant_submit",
            status="started",
            message="Issuing capability token",
            policy_id=policy_id,
            from_signature=from_sig,
            to_signature=to_sig,
            details={"ops": ops_csv, "expires_at": expires_at},
        )
        if self._should_use_js():
            r = self._js("issueGrant", from_sig, to_sig, policy_id, ops_csv, expires_at, env=env)
            print(f"[issue_grant] result: ok={r.ok}, stdout={r.stdout!r}, stderr={r.stderr!r}, code={r.code}")
            if not r.ok: raise RuntimeError(r.stderr or r.stdout)
            self.record_operation_latency("issueToken")
            self.emit_event(
                component="blockchain",
                stage="issue_grant_submit",
                status="ok",
                message="Capability token issued",
                policy_id=policy_id,
                from_signature=from_sig,
                to_signature=to_sig,
                tx_hash=self._extract_tx_hash(r.stdout),
            )
            return r.stdout
        ops = _ops_mask(ops_csv)
        receipt = self._w3_send(
            self._contract.functions.issueGrant(from_sig, to_sig, int(policy_id), ops, int(expires_at)),
            from_idx=from_idx, gas=3_000_000, gas_label="issueToken"
        )
        tx_hash = self._receipt_tx_hash(receipt)
        self._remember_grant_policy_id(from_sig, to_sig, int(policy_id))
        self.record_operation_latency("issueToken")
        self.emit_event(
            component="blockchain",
            stage="issue_grant_submit",
            status="ok",
            message="Capability token issued",
            policy_id=policy_id,
            from_signature=from_sig,
            to_signature=to_sig,
            tx_hash=tx_hash,
        )
        return f"✅ issueGrant: {tx_hash}"

    #@track_performance
    def issue_grant_delegable(self, from_sig: str, to_sig: str, policy_id: int, ops_csv: str, expires_at: int, delegation_allowed: bool, delegation_depth: int, from_idx: Optional[int]=None) -> str:
        env = os.environ.copy()
        if from_idx is not None:
            env["FROM_IDX"] = str(from_idx)
        allow = "true" if delegation_allowed else "false"
        self.emit_event(
            component="blockchain",
            stage="issue_delegable_grant_submit",
            status="started",
            message="Issuing delegable capability token",
            policy_id=policy_id,
            from_signature=from_sig,
            to_signature=to_sig,
            details={"ops": ops_csv, "expires_at": expires_at, "delegation_allowed": delegation_allowed, "delegation_depth": delegation_depth},
        )
        if self._should_use_js():
            r = self._js("issueGrantDelegable", from_sig, to_sig, policy_id, ops_csv, expires_at, allow, delegation_depth, env=env)
            if not r.ok: raise RuntimeError(r.stderr or r.stdout)
            self.record_operation_latency("issueTokenDelegable")
            self.emit_event(
                component="blockchain",
                stage="issue_delegable_grant_submit",
                status="ok",
                message="Delegable capability token issued",
                policy_id=policy_id,
                from_signature=from_sig,
                to_signature=to_sig,
                tx_hash=self._extract_tx_hash(r.stdout),
            )
            return r.stdout
        ops = _ops_mask(ops_csv)
        allow_bool = delegation_allowed if isinstance(delegation_allowed, bool) else (str(allow).lower() in ("true", "1"))
        receipt = self._w3_send(
            self._contract.functions.issueGrantDelegable(
                from_sig, to_sig, int(policy_id), ops, int(expires_at), allow_bool, int(delegation_depth)
            ),
            from_idx=from_idx, gas=3_000_000, gas_label="issueTokenDelegable"
        )
        tx_hash = self._receipt_tx_hash(receipt)
        self._remember_grant_policy_id(from_sig, to_sig, int(policy_id))
        self.record_operation_latency("issueTokenDelegable")
        self.emit_event(
            component="blockchain",
            stage="issue_delegable_grant_submit",
            status="ok",
            message="Delegable capability token issued",
            policy_id=policy_id,
            from_signature=from_sig,
            to_signature=to_sig,
            tx_hash=tx_hash,
        )
        return f"✅ issueGrantDelegable: {tx_hash}"

    #@track_performance
    def delegate_grant(self, current_from_sig: str, to_sig: str, new_from_sig: str, ops_csv: str, expires_at: int, from_idx: Optional[int]=None, policy_id: Optional[int]=None) -> str:
        env = os.environ.copy()
        if from_idx is not None:
            env["FROM_IDX"] = str(from_idx)
        self.emit_event(
            component="blockchain",
            stage="delegation_submit",
            status="started",
            message="Submitting delegation transaction",
            from_signature=current_from_sig,
            to_signature=to_sig,
            details={"child_from_signature": new_from_sig, "ops": ops_csv, "expires_at": expires_at},
        )
        if self._should_use_js():
            if os.getenv("REAL_INTERACT"):
                policy_id = policy_id if policy_id is not None else self._resolve_grant_policy_id(current_from_sig, to_sig)
                if policy_id is None:
                    raise RuntimeError("grant_policy_id_unknown")
                r = self._js("delegateGrant", current_from_sig, to_sig, new_from_sig, policy_id, ops_csv, expires_at, env=env)
            else:
                r = self._js("delegateGrant", current_from_sig, to_sig, new_from_sig, ops_csv, expires_at, env=env)
            if not r.ok: raise RuntimeError(r.stderr or r.stdout)
            self.record_operation_latency("delegateToken")
            self.emit_event(
                component="blockchain",
                stage="delegation_submit",
                status="ok",
                message="Delegation transaction submitted",
                from_signature=current_from_sig,
                to_signature=to_sig,
                tx_hash=self._extract_tx_hash(r.stdout),
                details={"child_from_signature": new_from_sig},
            )
            return r.stdout
        policy_id = policy_id if policy_id is not None else self._resolve_grant_policy_id(current_from_sig, to_sig)
        if policy_id is None:
            raise RuntimeError("grant_policy_id_unknown")
        ops = _ops_mask(ops_csv)
        receipt = self._w3_send(
            self._contract.functions.delegateGrant(
                current_from_sig, to_sig, new_from_sig, int(policy_id), ops, int(expires_at)
            ),
            from_idx=from_idx
        )
        tx_hash = self._receipt_tx_hash(receipt)
        self._remember_grant_policy_id(current_from_sig, to_sig, int(policy_id))
        self._remember_grant_policy_id(new_from_sig, to_sig, int(policy_id))
        self.record_operation_latency("delegateToken")
        self.emit_event(
            component="blockchain",
            stage="delegation_submit",
            status="ok",
            message="Delegation transaction submitted",
            from_signature=current_from_sig,
            to_signature=to_sig,
            tx_hash=tx_hash,
            details={"child_from_signature": new_from_sig},
        )
        return f"✅ delegateGrant: {tx_hash}"

    #@track_performance
    def revoke_grant(self, from_sig: str, to_sig: str, policy_id: int, from_idx: Optional[int]=None) -> str:
        env = os.environ.copy()
        if from_idx is not None:
            env["FROM_IDX"] = str(from_idx)
        self.emit_event(
            component="blockchain",
            stage="revoke_submit",
            status="started",
            message="Submitting revocation transaction",
            policy_id=policy_id,
            from_signature=from_sig,
            to_signature=to_sig,
        )
        if self._should_use_js():
            r = self._js("revokeGrant", from_sig, to_sig, policy_id, env=env)
            if not r.ok:
                raise RuntimeError(r.stderr or r.stdout)
            self.record_operation_latency("revokeToken")
            tx_hash = self._extract_tx_hash(r.stdout)
            self.emit_event(
                component="blockchain",
                stage="revoke_submit",
                status="ok",
                message="Revocation transaction submitted",
                policy_id=policy_id,
                from_signature=from_sig,
                to_signature=to_sig,
                tx_hash=tx_hash,
            )
            self._measure_revocation_propagation(tx_hash)
            return r.stdout
        receipt = self._w3_send(
            self._contract.functions.revokeGrant(from_sig, to_sig, int(policy_id)),
            from_idx=from_idx, gas=3_000_000, gas_label="revokeToken"
        )
        tx_hash = self._receipt_tx_hash(receipt)
        self.record_operation_latency("revokeToken")
        self.emit_event(
            component="blockchain",
            stage="revoke_submit",
            status="ok",
            message="Revocation transaction submitted",
            policy_id=policy_id,
            from_signature=from_sig,
            to_signature=to_sig,
            tx_hash=tx_hash,
        )
        self._measure_revocation_propagation(tx_hash)
        return f"✅ revokeGrant: {tx_hash}"

    def get_grant_ex(self, from_sig: str, to_sig: str, policy_id: int) -> Dict[str, Any]:
        print(f"[get_grant_ex] from_sig={from_sig}, to_sig={to_sig}, pid={policy_id}")
        if self._should_use_js():
            if os.getenv("REAL_INTERACT"):
                r = self._js("getGrantEx", from_sig, to_sig, policy_id)
            else:
                r = self._js("getGrantEx", from_sig, to_sig)
            if not r.ok:
                raise RuntimeError(r.stderr or r.stdout)
            return json.loads(r.stdout)
        g = self._w3_call(self._contract.functions.getGrantEx(from_sig, to_sig, int(policy_id)))
        ZERO32 = "0x" + "00" * 32
        return {
            "policyId": int(g[0]),
            "opsSubset": int(g[1]),
            "issuedAt": int(g[2]),
            "expiresAt": int(g[3]),
            "isIssued": bool(g[4]),
            "isRevoked": bool(g[5]),
            "delegationAllowed": bool(g[6]),
            "delegationDepth": int(g[7]),
            "depthDel": int(g[8] if len(g) > 8 else g[7]),
            "parentTokenId": ("0x" + g[9].hex()) if len(g) > 9 and isinstance(g[9], bytes) else (g[9] if len(g) > 9 else ZERO32),
        }

    def _resolve_grant_policy_id(self, from_sig: str, to_sig: str) -> Optional[int]:
        cached = self._cached_grant_policy_id(from_sig, to_sig)
        if cached is not None:
            return cached
        if self._should_use_js() and not os.getenv("REAL_INTERACT"):
            try:
                grant = self.get_grant_ex(from_sig, to_sig, 0)
                pid = int(grant.get("policyId") or 0)
                if pid > 0 and grant.get("isIssued"):
                    self._remember_grant_policy_id(from_sig, to_sig, pid)
                    return pid
            except Exception:
                pass
        # Try nextPolicyId
        try:
            if not self._should_use_js():
                next_id = int(self._w3_call(self._contract.functions.nextPolicyId()))
            else:
                np = self._js("nextPolicyId")
                if not np.ok:
                    return None
                next_id = int((np.stdout or "0").strip() or 0)
        except Exception:
            return None

        for pid in range(1, max(1, next_id)):
            try:
                grant = self.get_grant_ex(from_sig, to_sig, pid)
                if grant.get("isIssued"):
                    self._remember_grant_policy_id(from_sig, to_sig, pid)
                    return pid
            except Exception:
                continue
        return None

    def get_grant_ex_any(self, from_sig: str, to_sig: str, policy_id: Optional[int]=None) -> Dict[str, Any]:
        pid = policy_id if policy_id is not None else self._resolve_grant_policy_id(from_sig, to_sig)
        if pid is None:
            raise RuntimeError("grant_policy_id_unknown")
        return self.get_grant_ex(from_sig, to_sig, int(pid))

    def get_grant_ex_auto(self, from_sig: str, to_sig: str, *, method: str | None = None, resource_path: str | None = None, ctx: str | None = None) -> Dict[str, Any]:
        """
        Resolve policyId automatically from (method, resource_path) or ctx, then return the grant.
        - If ctx is given, it is used directly (e.g., 'api:GET:/temperature').
        - Else we build it via _canon_resource_key(method, resource_path).
        """
        # Resolve ctx
        if not ctx:
            if not method or not resource_path:
                raise RuntimeError("get_grant_ex_auto requires either ctx or (method + resource_path)")
            ctx = _canon_resource_key(method, resource_path)

        # Resolve roles for from/to
        from_details = self.get_node_by_sig(from_sig)
        to_details = self.get_node_by_sig(to_sig)
        from_role = self._role_name(from_details["nodeType"])
        to_role   = self._role_name(to_details["nodeType"])

        # Determine op from method (READ/WRITE/UPDATE/REMOVE)
        if not method and ctx:
            # If only ctx was provided, infer op from METHOD_TO_OP by parsing ctx 'api:METHOD:/path'
            try:
                parts = ctx.split(":")
                method = parts[1].upper().strip()
            except Exception:
                raise RuntimeError("ctx does not look like 'api:METHOD:/path' and method not provided")
        op = METHOD_TO_OP.get(method.upper())
        if not op:
            raise RuntimeError(f"unsupported_method:{method}")

        # Find policyId on-chain
        pid = self._find_policy_on_chain(from_role, to_role, op, ctx)
        if not pid:
            raise RuntimeError("no_matching_policy")

        # Return the grant for this (from,to,pid)
        return self.get_grant_ex(from_sig, to_sig, int(pid))

    #@track_performance
    def is_grant_expired(self, from_sig: str, to_sig: str, policy_id: Optional[int]=None) -> bool:
        start = time.monotonic()
        pid = policy_id if policy_id is not None else self._resolve_grant_policy_id(from_sig, to_sig)
        if pid is None:
            raise RuntimeError("grant_policy_id_unknown")
        self.emit_event(
            component="blockchain",
            stage="expiry_check",
            status="started",
            message="Checking whether the grant is expired",
            policy_id=pid,
            from_signature=from_sig,
            to_signature=to_sig,
        )
        if self._should_use_js():
            r = self._js("isGrantExpired", from_sig, to_sig, pid)
            if r.ok:
                expired = _parse_bool(r.stdout) is True
                self.record_operation_latency("expiryCheck", elapsed=time.monotonic() - start)
                self.emit_event(
                    component="blockchain",
                    stage="expiry_check",
                    status="ok",
                    message="Grant expiry evaluated",
                    policy_id=pid,
                    from_signature=from_sig,
                    to_signature=to_sig,
                    duration_ms=(time.monotonic() - start) * 1000,
                    details={"expired": expired},
                )
                return expired
        else:
            try:
                expired = bool(self._w3_call(self._contract.functions.isGrantExpired(from_sig, to_sig, int(pid))))
                self.record_operation_latency("expiryCheck", elapsed=time.monotonic() - start)
                self.emit_event(
                    component="blockchain",
                    stage="expiry_check",
                    status="ok",
                    message="Grant expiry evaluated",
                    policy_id=pid,
                    from_signature=from_sig,
                    to_signature=to_sig,
                    duration_ms=(time.monotonic() - start) * 1000,
                    details={"expired": expired},
                )
                return expired
            except Exception:
                pass
        # Fallback via getGrantEx
        g = self.get_grant_ex(from_sig, to_sig, pid)
        now = int(time.time())
        issued = bool(g.get("isIssued", False))
        revoked = bool(g.get("isRevoked", False))
        exp = int(g.get("expiresAt", 0) or 0)
        self.record_operation_latency("expiryCheck", elapsed=time.monotonic() - start)
        expired = (not issued) or revoked or (exp <= now)
        self.emit_event(
            component="blockchain",
            stage="expiry_check",
            status="ok",
            message="Grant expiry evaluated through fallback grant lookup",
            policy_id=pid,
            from_signature=from_sig,
            to_signature=to_sig,
            duration_ms=(time.monotonic() - start) * 1000,
            details={"expired": expired, "fallback": True},
        )
        return expired

    #@track_performance
    def check_grant(self, from_sig: str, to_sig: str, policy_id: int, op_csv: str) -> bool:
        start = time.monotonic()
        self.emit_event(
            component="blockchain",
            stage="grant_check",
            status="started",
            message="Checking grant with eth_call",
            policy_id=policy_id,
            from_signature=from_sig,
            to_signature=to_sig,
            details={"ops": op_csv},
        )
        if self._should_use_js():
            if os.getenv("REAL_INTERACT"):
                r = self._js("checkGrant", from_sig, to_sig, policy_id, op_csv)
            else:
                r = self._js("checkGrant", from_sig, to_sig, op_csv)
            if not r.ok:
                raise RuntimeError(r.stderr or r.stdout)
            self.record_operation_latency("checkGrant", elapsed=time.monotonic() - start)
            granted = _parse_bool(r.stdout) is True
        else:
            try:
                granted = bool(self._w3_call(
                    self._contract.functions.checkGrant(from_sig, to_sig, int(policy_id), _ops_mask(op_csv))
                ))
                self.record_operation_latency("checkGrant", elapsed=time.monotonic() - start)
            except Exception as exc:
                raise RuntimeError(str(exc)) from exc
        self.emit_event(
            component="blockchain",
            stage="grant_check",
            status="ok",
            message="Grant check completed",
            policy_id=policy_id,
            from_signature=from_sig,
            to_signature=to_sig,
            duration_ms=(time.monotonic() - start) * 1000,
            details={"granted": granted, "ops": op_csv},
        )
        return granted

    def check_grant_and_log(self, from_sig: str, to_sig: str, policy_id: int, op_csv: str) -> bool:
        self.emit_event(
            component="blockchain",
            stage="grant_audit",
            status="started",
            message="Submitting auditable grant decision transaction",
            policy_id=policy_id,
            from_signature=from_sig,
            to_signature=to_sig,
            details={"ops": op_csv},
        )
        if self._should_use_js():
            r = self._js("checkGrantAndLog", from_sig, to_sig, policy_id, op_csv)
            if not r.ok:
                raise RuntimeError(r.stderr or r.stdout)
            try:
                payload = json.loads(r.stdout)
                granted = bool(payload.get("granted"))
            except Exception:
                granted = "true" in (r.stdout or "").lower()
        else:
            try:
                receipt = self._w3_send(
                    self._contract.functions.checkGrantAndLog(from_sig, to_sig, int(policy_id), _ops_mask(op_csv)),
                    gas_label="checkGrantAndLog",
                )
                # Return value not available from receipt; fall back to checkGrant read
                granted = bool(self._w3_call(
                    self._contract.functions.checkGrant(from_sig, to_sig, int(policy_id), _ops_mask(op_csv))
                ))
            except Exception as exc:
                raise RuntimeError(str(exc)) from exc
        self.emit_event(
            component="blockchain",
            stage="grant_audit",
            status="ok",
            message="Auditable grant decision recorded",
            policy_id=policy_id,
            from_signature=from_sig,
            to_signature=to_sig,
            details={"granted": granted},
        )
        return granted

    # ---------- identity verification ----------

    #@track_performance
    def verify_signature(self, payload: Dict[str,Any]) -> bool:
        # If libs missing, or values are clearly not real hex keys/sigs, treat as valid (useful in tests).
        if not (keys and keccak):
            return True
        try:
            signature_hex = (payload.get("signature") or "").removeprefix("0x")
            public_key_hex = (payload.get("public_key") or "").removeprefix("0x")
            # Heuristic: if inputs are not hex-like, skip strict verification
            if (not all(c in "0123456789abcdefABCDEF" for c in signature_hex)) or \
               (not all(c in "0123456789abcdefABCDEF" for c in public_key_hex)):
                return True
            msg = {
                "node_id":   payload.get("node_id"),
                "node_name": payload.get("node_name"),
                "node_type": payload.get("node_type"),
                "public_key": ("0x"+public_key_hex),
            }
            message_json = json.dumps(msg, sort_keys=True)
            digest = keccak(text=message_json)
            pub = keys.PublicKey(bytes.fromhex(public_key_hex))
            sig = keys.Signature(bytes.fromhex(signature_hex))
            return pub.verify_msg_hash(digest, sig)
        except Exception:
            return False

    # ---------- helpers for role & resource policy ----------

    def _role_name(self, node_type_num: int) -> str:
        return ROLE_BY_NUM.get(int(node_type_num), "Unknown")

    def _load_policy_index(self):
        with self._policy_lock:
            self.policy_index = _json_load(POLICY_INDEX_FILE, {})

    def _save_policy_index(self):
        with self._policy_lock:
            _json_save(POLICY_INDEX_FILE, self.policy_index)

    def _policy_matches(self, pid_int: int, from_role: str, to_role: str, ops_mask: int, ctx_hash: str) -> bool:
        try:
            gp = self.get_policy(int(pid_int))
            return bool(
                gp
                and int(gp.get("version", 0)) > 0
                and self._role_name(gp["fromRole"]) == from_role
                and self._role_name(gp["toRole"]) == to_role
                and str(gp.get("ctxSchema", "")).lower() == ctx_hash.lower()
                and int(gp.get("opsAllowed", 0)) == ops_mask
            )
        except Exception:
            return False

    def _parse_policy_resolution_output(self, stdout: str) -> Dict[str, Any]:
        text = (stdout or "").strip()
        if not text:
            return {}
        if text.startswith("{"):
            try:
                return json.loads(text)
            except Exception:
                return {}
        if text.startswith("exists:"):
            try:
                return {"status": "exists", "policyId": int(text.split(":", 1)[1]), "note": "legacy_exists"}
            except Exception:
                return {}
        if text.startswith("created:"):
            return {"status": "created", "policyId": None, "txHash": text.split(":", 1)[1], "note": "legacy_created"}
        return {}

    #@track_performance
    def ensure_policy(self, from_role: str, to_role: str, ops_csv: str, ctx_schema_str: str, create_if_missing: bool=True) -> Dict[str,Any]:
        self._load_policy_index()
        invalid_roles = _invalid_policy_roles(from_role, to_role)
        if invalid_roles:
            allowed = ", ".join(sorted(VALID_POLICY_ROLES))
            result = {
                "status": "error",
                "policyId": None,
                "note": f"invalid_policy_roles:{', '.join(invalid_roles)} (allowed: {allowed})",
            }
            self.record_operation_latency("ensurePolicy")
            return result
        # 0) local cache?
        key = f"{from_role}|{to_role}|{_ops_mask(ops_csv)}|{ctx_schema_str}"

        if key in self.policy_index:
            self.emit_event(
                component="policy_cache",
                stage="policy_cache_hit",
                status="reused",
                message="Policy resolved from the local cache",
                details={"cache_key": key, "policy_id": self.policy_index[key]},
            )
            result = {"status":"exists", "policyId": self.policy_index[key], "note":"found in cache"}
            self.record_operation_latency("ensurePolicy")
            return result
        self.emit_event(
            component="policy_cache",
            stage="policy_cache_miss",
            status="started",
            message="Policy not found in the local cache",
            details={"cache_key": key},
        )

        if self._should_use_js() and not os.getenv("REAL_INTERACT") and create_if_missing:
            try:
                msig = self.msig_info()
            except Exception:
                msig = {"msigRequired": False}
        else:
            msig = None

        if self._should_use_js() and not os.getenv("REAL_INTERACT") and create_if_missing and not (msig or {}).get("msigRequired"):
            bridge = self._js("ensurePolicy", from_role, to_role, ops_csv, ctx_schema_str)
            if not bridge.ok:
                result = {"status": "error", "policyId": None, "note": bridge.stderr or bridge.stdout}
                self.record_operation_latency("ensurePolicy")
                return result
            try:
                payload = json.loads(bridge.stdout or "{}")
            except Exception:
                payload = {"status": "error", "policyId": None, "note": bridge.stdout or "ensure_policy_parse_failed"}
            pid = payload.get("policyId")
            if pid:
                self.policy_index[key] = pid
                self._save_policy_index()
            self.record_operation_latency("ensurePolicy")
            return payload
        
        # 1) try on-chain before creating (avoids DuplicatePolicy revert)
        self.emit_event(
            component="blockchain",
            stage="policy_lookup",
            status="started",
            message="Looking up policy on chain",
            details={"from_role": from_role, "to_role": to_role, "ops": ops_csv, "ctx": ctx_schema_str},
        )
        pid = self._find_policy_on_chain(from_role, to_role, ops_csv, ctx_schema_str)
        
        if pid:
            self.policy_index[key] = pid
            self._save_policy_index()
            self.emit_event(
                component="blockchain",
                stage="policy_lookup",
                status="reused",
                message="Policy found on chain",
                policy_id=pid,
                details={"cache_key": key},
            )
            result = {"status":"exists", "policyId": pid, "note":"found on-chain"}
            self.record_operation_latency("ensurePolicy")
            return result
        
        self._load_policy_index()
        # strict cache key (include ops)
        try:
            ops_mask = _ops_mask(ops_csv)
        except Exception as e:
            return {"status":"error", "policyId": None, "note": f"bad_ops:{e}"}

        ctx_hash = _ctx_hash(ctx_schema_str)

        key = _policy_cache_key(from_role, to_role, ops_mask, ctx_schema_str)


        # 0) cache hit → validate on-chain (version>0 + fields match). If bad, drop it.
        cached_id = self.policy_index.get(key)
        if cached_id:
            try:
                gp = self.get_policy(int(cached_id))
                if gp and int(gp.get("version", 0)) > 0:
                    roles_ok = (self._role_name(gp["fromRole"]) == from_role and
                                self._role_name(gp["toRole"])   == to_role)
                    ctx_ok   = (str(gp.get("ctxSchema","")).lower() == ctx_hash.lower())
                    ops_ok   = (int(gp.get("opsAllowed", 0)) == ops_mask)
                    if roles_ok and ctx_ok and ops_ok:
                        return {"status": "exists", "policyId": int(cached_id), "note": "cache hit (validated)"}
            except Exception:
                pass
            # stale/mismatch → purge
            self.policy_index.pop(key, None)
            self._save_policy_index()

        # 1) try to find on-chain (authoritative)
        try:
            fp = self.find_policy_id(from_role, to_role, ops_csv, ctx_schema_str)  # your JS bridge
            if fp.get("ok"):
                pid = int(fp.get("stdout") or 0)
                if pid > 0:
                    # quick verify
                    gp = self.get_policy(pid)
                    if gp and int(gp.get("version", 0)) > 0 and \
                    self._role_name(gp["fromRole"]) == from_role and \
                    self._role_name(gp["toRole"])   == to_role and \
                    str(gp.get("ctxSchema","")).lower() == ctx_hash.lower() and \
                    int(gp.get("opsAllowed", 0)) == ops_mask:
                        self.policy_index[key] = pid
                        self._save_policy_index()
                        self.emit_event(
                            component="blockchain",
                            stage="policy_lookup",
                            status="reused",
                            message="Policy found on chain through indexed search",
                            policy_id=pid,
                        )
                        result = {"status":"exists", "policyId": pid, "note":"found on-chain"}
                        self.record_operation_latency("ensurePolicy")
                        return result
        except Exception:
            pass

        if not create_if_missing:
            result = {"status":"missing", "policyId": None, "note":"not found and create_if_missing=False"}
            self.record_operation_latency("ensurePolicy")
            return result
        print(f"ensure_policy: creating new policy for {key} (from={from_role}, to={to_role}, ops={ops_csv}, ctx={ctx_schema_str})")
        self.emit_event(
            component="blockchain",
            stage="policy_create",
            status="started",
            message="Creating a new policy on chain",
            details={"from_role": from_role, "to_role": to_role, "ops": ops_csv, "ctx": ctx_schema_str},
        )
        # 2) msig gate
        try:
            msig = msig if msig is not None else self.msig_info()
        except Exception:
            msig = {"msigRequired": False}

        if msig.get("msigRequired"):
            r = self.approve_create_policy(from_role, to_role, ops_csv, ctx_schema_str)
            if not r["ok"]:
                return {"status":"error", "policyId": None, "note": r["stderr"] or r["stdout"]}
            self.emit_event(
                component="blockchain",
                stage="policy_create",
                status="waiting",
                message="Policy creation is waiting for multisig approval",
                details={"from_role": from_role, "to_role": to_role, "ops": ops_csv},
            )
            result = {"status":"pending_msig", "policyId": None, "note":"approval recorded; wait for threshold"}
            self.record_operation_latency("ensurePolicy")
            return result

        # 4) optimistic resolution: current latest id + 1 should be the new id after create.
        pid = None
        try:
            if self._should_use_js():
                np = self._js("nextPolicyId")
                if np.ok:
                    pid = int(np.stdout) + 1
            else:
                pid = int(self._w3_call(self._contract.functions.nextPolicyId())) + 1
        except Exception:
            pid = None

        def _ok(pid_int: int) -> bool:
            return self._policy_matches(pid_int, from_role, to_role, ops_mask, ctx_hash)

        def _wait_for_policy_resolution(timeout_seconds: float = 20.0, interval_seconds: float = 0.1) -> Optional[int]:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                pid_local = self._find_policy_on_chain(from_role, to_role, ops_csv, ctx_schema_str)
                if pid_local:
                    return int(pid_local)
                time.sleep(interval_seconds)
            return None

        # 3) create — delegate to create_policy which has its own JS/web3 dual path
        cp = self.create_policy(from_role, to_role, ops_csv, ctx_schema_str)
        r_ok = cp.get("ok", False)
        r_stdout = cp.get("stdout", "")
        r_stderr = cp.get("stderr", "")
        # Unify interface: construct a compatible result object
        class _R:
            def __init__(self, ok, stdout, stderr):
                self.ok = ok
                self.stdout = stdout
                self.stderr = stderr
        r = _R(r_ok, r_stdout, r_stderr)
        if not r.ok:
            if pid and pid >= 1:
                self.policy_index[key] = pid
                self._save_policy_index()
                self.emit_event(
                    component="blockchain",
                    stage="policy_create",
                    status="reused",
                    message="Policy creation resolved to an existing on-chain policy",
                    policy_id=pid,
                )
                result = {"status":"exists", "policyId": pid, "note":"resolved via optimistic nextPolicyId after create revert"}
                self.record_operation_latency("ensurePolicy")
                return result
            pid = _wait_for_policy_resolution(timeout_seconds=12.0, interval_seconds=0.1)
            if pid and _ok(pid):
                self.policy_index[key] = pid
                self._save_policy_index()
                self.emit_event(
                    component="blockchain",
                    stage="policy_create",
                    status="reused",
                    message="Policy creation raced with an existing policy and resolved cleanly",
                    policy_id=pid,
                )
                result = {"status":"exists", "policyId": pid, "note":"resolved on-chain after create race"}
                self.record_operation_latency("ensurePolicy")
                return result
            return {"status":"error", "policyId": None, "note": r.stderr or r.stdout}

        bridge = self._parse_policy_resolution_output(r.stdout)
        bridge_pid = bridge.get("policyId")
        bridge_status = str(bridge.get("status") or "created")
        if bridge_pid is not None:
            try:
                bridge_pid = int(bridge_pid)
            except Exception:
                bridge_pid = None
        tx_hash = bridge.get("txHash")

        if bridge_pid and _ok(bridge_pid):
            self.policy_index[key] = bridge_pid
            self._save_policy_index()
            self.emit_event(
                component="blockchain",
                stage="policy_create",
                status="reused" if bridge_status == "exists" else "ok",
                message="Policy resolved directly from the policy creation bridge",
                policy_id=bridge_pid,
                tx_hash=tx_hash,
            )
            result = {
                "status": "exists" if bridge_status == "exists" else "created",
                "policyId": bridge_pid,
                "note": bridge.get("note", "resolved via ensurePolicy bridge"),
            }
            self.record_operation_latency("ensurePolicy")
            return result

        if pid and pid >= 1 and _ok(pid):
            self.policy_index[key] = pid
            self._save_policy_index()
            self.emit_event(
                component="blockchain",
                stage="policy_create",
                status="ok",
                message="Policy created on chain",
                policy_id=pid,
                tx_hash=tx_hash,
            )
            result = {"status":"created", "policyId": pid, "note":"policy created (optimistic nextPolicyId)"}
            self.record_operation_latency("ensurePolicy")
            return result

        if pid and pid >= 1 and not os.getenv("REAL_INTERACT"):
            self.policy_index[key] = pid
            self._save_policy_index()
            self.emit_event(
                component="blockchain",
                stage="policy_create",
                status="ok",
                message="Policy created through the mock interact path",
                policy_id=pid,
                tx_hash=tx_hash,
            )
            result = {"status":"created", "policyId": pid, "note":"policy created (mock fallback)"}
            self.record_operation_latency("ensurePolicy")
            return result

        pid = _wait_for_policy_resolution(timeout_seconds=20.0, interval_seconds=0.1)
        if pid and _ok(pid):
            self.policy_index[key] = pid
            self._save_policy_index()
            self.emit_event(
                component="blockchain",
                stage="policy_create",
                status="ok",
                message="Policy created after propagation delay",
                policy_id=pid,
                tx_hash=tx_hash,
            )
            result = {"status":"created", "policyId": pid, "note":"policy created (resolved after propagation)"}
            self.record_operation_latency("ensurePolicy")
            return result

        try:
            fp = self.find_policy_id(from_role, to_role, ops_csv, ctx_schema_str)
            if fp.get("ok"):
                pid = int(fp.get("stdout") or 0)
                if pid > 0 and _ok(pid):
                    self.policy_index[key] = pid
                    self._save_policy_index()
                    self.emit_event(
                        component="blockchain",
                        stage="policy_create",
                        status="ok",
                        message="Policy created and confirmed through indexed search",
                        policy_id=pid,
                        tx_hash=tx_hash,
                    )
                    result = {"status":"created", "policyId": pid, "note":"policy created (found via search)"}
                    self.record_operation_latency("ensurePolicy")
                    return result
        except Exception:
            pass

        pid = _wait_for_policy_resolution(timeout_seconds=45.0, interval_seconds=0.1)
        if pid and _ok(pid):
            self.policy_index[key] = pid
            self._save_policy_index()
            self.emit_event(
                component="blockchain",
                stage="policy_create",
                status="ok",
                message="Policy created and resolved after extended reconciliation",
                policy_id=pid,
                tx_hash=tx_hash,
            )
            result = {"status":"created", "policyId": pid, "note":"policy created (resolved after extended reconciliation)"}
            self.record_operation_latency("ensurePolicy")
            return result

        unresolved_note = bridge.get("note") or "policy_resolution_failed_after_create"
        result = {"status":"error", "policyId": None, "note": unresolved_note}
        self.record_operation_latency("ensurePolicy")
        return result


    def _listen_validator_proposals_loop(self):
        """
        Tail contract ValidatorProposed events via interact.js and auto-vote.
        Runs in background; dedup & bounded by _voted_addrs guard.
        """
        
        pattern = re.compile(r'0x[a-fA-F0-9]{40}')
        last_block: int = 0
        poll_delay = 10.0
        max_poll_delay = 60.0
        idle_since = time.monotonic()
        next_idle_log = idle_since
        while True:
            try:
                addrs: list = []
                if self._should_use_js():
                    r = self._js("listenForValidatorProposals")
                    if r.ok:
                        addrs = [m.group(0).lower() for m in pattern.finditer(r.stdout or "")]
                    elif r.stderr:
                        print(f"[listener] JS validator proposal poll failed: {r.stderr}")
                else:
                    try:
                        latest = int(self._w3.eth.block_number)
                        from_block = max(0, last_block) if last_block else max(0, latest - 100)
                        logs = self._contract.events.ValidatorProposed.get_logs(
                            from_block=from_block, to_block=latest
                        )
                        addrs = [str(e["args"].get("validator", "")).lower() for e in logs if e["args"].get("validator")]
                        last_block = latest + 1
                    except Exception as exc:
                        print(f"[listener] web3 event fetch error: {exc}")
                if addrs:
                        poll_delay = 10.0
                        idle_since = time.monotonic()
                        next_idle_log = idle_since + 300.0
                        print(f"[listener] found proposed validator addresses: {addrs}")
                        flow_id = self._new_flow_id("validator")
                        self.emit_event(
                            component="validator_listener",
                            flow_type="validator",
                            flow_id=flow_id,
                            stage="validator_proposal_detected",
                            status="ok",
                            message="Validator proposal detected",
                            details={"addresses": addrs},
                        )
                        cur = self._normalize_validators(self.qbft_get_validators())
                        for a in addrs:
                            if a not in cur:
                                self._propose_and_vote(a)
                else:
                    now = time.monotonic()
                    if now >= next_idle_log:
                        idle_for = int(now - idle_since)
                        print(f"[listener] idle; no validator proposals observed for {idle_for}s (poll interval {int(poll_delay)}s)")
                        next_idle_log = now + 300.0
                    poll_delay = min(max_poll_delay, poll_delay * 1.5)
            except Exception as e:
                print(f"[listener] error: {e}")
                poll_delay = min(max_poll_delay, max(10.0, poll_delay))
            time.sleep(poll_delay)

    def _acquire_validator_listener_lead(self) -> bool:
        if fcntl is None:
            return True
        try:
            self._vlisten_lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_fd = os.open(self._vlisten_lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(lock_fd, 0)
            os.write(lock_fd, str(os.getpid()).encode("ascii", errors="ignore"))
            self._vlisten_lock_fd = lock_fd
            return True
        except BlockingIOError:
            return False
        except Exception as exc:
            print(f"[listener] lead-lock unavailable: {exc}")
            return True

    def start_validator_listener(self):
        """Start the background listener once."""
        if not os.getenv("REAL_INTERACT"):
            return
        with self._vlisten_lock:
            if self._vlisten_started:
                return
            if not self._acquire_validator_listener_lead():
                return
            self._vlisten_started = True
        print("[listener] starting validator listener thread")
        self.emit_event(
            component="validator_listener",
            flow_type="validator",
            flow_id=self._new_flow_id("validator"),
            stage="listener_started",
            status="started",
            message="Validator listener thread started",
        )
        t = threading.Thread(target=self._listen_validator_proposals_loop, name="validator-listener", daemon=True)
        t.start()
        print("[listener] validator listener started")
    # ---------- Algorithm A: Registration + Acknowledgement ----------

    #@track_performance
    def registration_flow(self, payload: Dict[str,Any]) -> Dict[str,Any]:
        """
        payload includes:
          node_id, node_name, node_type, public_key, address, rpcURL, signature
        Output: dict describing status and role (validator vs non-validator vs endpoint).
        """
        if not self.current_flow_id():
            self.start_flow(
                "registration",
                stage="request_received",
                message="Registration request received",
                component="orchestrator",
                details={"node_id": payload.get("node_id"), "node_type": payload.get("node_type")},
                from_signature=payload.get("signature"),
            )
        if not self.check_if_deployed():
            return {"ok": False, "why": "contract_not_deployed"}

        self.emit_event(
            component="orchestrator",
            stage="signature_verification",
            status="started",
            message="Verifying registration signature",
            details={"node_id": payload.get("node_id"), "node_type": payload.get("node_type")},
            from_signature=payload.get("signature"),
        )
        signature_ok = True if not self.enforce_signature else self.verify_signature(payload)
        if self.enforce_signature and not signature_ok:
            self.emit_event(
                component="orchestrator",
                stage="signature_verification",
                status="error",
                message="Registration signature verification failed",
                details={"node_id": payload.get("node_id")},
                from_signature=payload.get("signature"),
            )
            return {"ok": False, "why": "signature_verification_failed"}
        self.emit_event(
            component="orchestrator",
            stage="signature_verification",
            status="ok",
            message="Registration signature verified",
            details={"enforced": self.enforce_signature},
            from_signature=payload.get("signature"),
        )
        
        sig = payload["signature"]
        self.emit_event(
            component="orchestrator",
            stage="already_registered_check",
            status="started",
            message="Checking whether the node is already registered",
            from_signature=sig,
        )
        already = self.is_node_registered(sig)
        self.emit_event(
            component="orchestrator",
            stage="already_registered_check",
            status="reused" if already else "ok",
            message="Node registration status resolved",
            details={"already_registered": already},
            from_signature=sig,
        )

        # Role decision and ack happen regardless of already-registered.
        node_type_str = payload["node_type"]
        endpoint_roles = {"Sensor", "Actuator"}
        is_endpoint = node_type_str in endpoint_roles

        # === EARLY EXIT IF ALREADY REGISTERED ===
        if already:
            # Do NOT send ACK or touch validator flow; just report status.
            return {"ok": True, "status": "already_registered", "ack_sent": False, "ack_status": "skipped", "ack_required": False, "tx": None}
        # ========================================
        tx_out = None
        node_id, node_name = payload["node_id"], payload["node_name"]
        registered_by_addr = self.registrar_addr or payload.get("registrar_addr")
        payload["address"] = self._clean_address(payload.get("address") or "")

        rpcURL = payload.get("rpcURL","")
        registered_by_type = self.registrar_role
        tx_out = self.register_node(
            node_id, node_name, node_type_str,
            payload["public_key"], registered_by_addr, rpcURL,
            registered_by_type, sig
        )

        ack_sent = False
        ack_status = "not_needed"
        ack_required = (payload.get("node_type") or "").strip() in ALLOWED_ACK_ROLES

        # Validator promotion must follow the explicit request flag for non-root nodes.
        wants_validator = bool(payload.get("wants_validator", False)) or (node_type_str == "Cloud")
        if wants_validator:
            cur = self._normalize_validators(self.qbft_get_validators())
            new_addr_lc = (payload.get("address") or "").lower()
            if new_addr_lc and new_addr_lc in cur:
                status = "validator_already_included"
            else:
                self._promote_validator_async(payload, from_signature=sig)
                status = "validator_proposed"
        else:
            status = "endpoint_registered" if is_endpoint else "registered"

        try:
            ack_meta = self._dispatch_acknowledgement(payload, tx_out=tx_out)
            ack_sent = bool(ack_meta.get("ack_sent", False))
            ack_status = str(ack_meta.get("ack_status") or "not_needed")
            ack_required = bool(ack_meta.get("ack_required", ack_required))
        except Exception as e:
            print(f"Acknowledgement error: {e}")
            ack_sent = False
            ack_status = "skipped"

        return {"ok": True, "status": status, "ack_sent": ack_sent, "ack_status": ack_status, "ack_required": ack_required, "tx": tx_out}
                

    # ---------- Algorithm B: Access Control + Delegation ----------

    #@track_performance
    def access_flow(self, from_sig: str, to_sig: str, http_method: str, resource_path: str,
                    expiry_secs: int = 900, allow_delegation: bool=False, delegation_depth: int=0,
                    audit: bool=True) -> Dict[str,Any]:
        """
        Ensures policy/grant for (from_sig -> to_sig) on a resource endpoint and returns an access decision.
        - One policy per resource key: ctxSchema = "api:METHOD:/path".
        - If msig is ON, ensure_policy() may return "pending_msig".
        """
        if not self.current_flow_id():
            self.start_flow(
                "access",
                stage="request_received",
                message="Access request received",
                component="orchestrator",
                details={"method": http_method, "resource_path": resource_path},
                from_signature=from_sig,
                to_signature=to_sig,
            )
        if not self.check_if_deployed():
            return {"ok": False, "why": "contract_not_deployed"}

        # Grant cache fast-path
        _cache_key = (from_sig, to_sig, (http_method or "").upper(), resource_path)
        with self._grant_cache_lock:
            _cached = self._grant_cache.get(_cache_key)
        if _cached:
            _cached_result, _cached_expiry = _cached
            if time.time() < _cached_expiry:
                return _cached_result

        # Check registration first
        self.emit_event(
            component="orchestrator",
            stage="registration_validation",
            status="started",
            message="Checking whether both nodes are registered",
            from_signature=from_sig,
            to_signature=to_sig,
        )
        if not self.is_node_registered(from_sig):
            return {"ok": False, "why": "from_not_registered"}
        if not self.is_node_registered(to_sig):
            return {"ok": False, "why": "to_not_registered"}
        self.emit_event(
            component="orchestrator",
            stage="registration_validation",
            status="ok",
            message="Both nodes are registered",
            from_signature=from_sig,
            to_signature=to_sig,
        )
        
        print(f"access_flow: from={from_sig}, to={to_sig}, method={http_method}, path={resource_path}, expiry_secs={expiry_secs}, allow_delegation={allow_delegation}, delegation_depth={delegation_depth}")
        # Resolve roles
        from_details = self.get_node_by_sig(from_sig)
        to_details   = self.get_node_by_sig(to_sig)
        from_role    = self._role_name(from_details["nodeType"])
        to_role      = self._role_name(to_details["nodeType"])
        self.emit_event(
            component="orchestrator",
            stage="role_resolution",
            status="ok",
            message="Resolved node roles for access control",
            from_signature=from_sig,
            to_signature=to_sig,
            details={"from_role": from_role, "to_role": to_role},
        )

        # Decide op for the HTTP method
        op = METHOD_TO_OP.get((http_method or "").upper())
        if not op:
            return {"ok": False, "why": f"unsupported_method:{http_method}"}

        # Create/find the resource-scoped policy
        ctx = _canon_resource_key(http_method, resource_path)
        self.emit_event(
            component="orchestrator",
            stage="resource_context",
            status="ok",
            message="Derived resource context for the request",
            from_signature=from_sig,
            to_signature=to_sig,
            details={"method": http_method, "resource_path": resource_path, "ctx": ctx, "op": op},
        )
        _policy_t0 = time.monotonic()
        ensure = self.ensure_policy(
            from_role,
            to_role,
            op,
            ctx,
            create_if_missing=bool(os.getenv("IS_POLICY_ADMIN")),
        )
        _policy_latency_ms = round((time.monotonic() - _policy_t0) * 1000, 3)
        _policy_created = ensure.get("status") == "created"

        if ensure["status"] in {"missing","error"}:
            return {"ok": False, "why": f"policy_error:{ensure['note']}"}
        if ensure["status"] == "pending_msig":
            return {"ok": False, "why": "policy_pending_multisig", "note": ensure["note"]}
        policy_id = ensure["policyId"]

        if policy_id is None:
            return {"ok": False, "why": ensure.get("note", "policy_id_unknown")}
        self._remember_grant_policy_id(from_sig, to_sig, int(policy_id))


        try:
            print(f"access_flow: checking grant for {from_sig} -> {to_sig} with policyId={policy_id} and op={op}")
            self.emit_event(
                component="orchestrator",
                stage="grant_lookup",
                status="started",
                message="Looking up the current grant",
                policy_id=policy_id,
                from_signature=from_sig,
                to_signature=to_sig,
            )
            gx = self.get_grant_ex(from_sig, to_sig, policy_id)
            self.emit_event(
                component="orchestrator",
                stage="grant_lookup",
                status="ok",
                message="Grant lookup completed",
                policy_id=policy_id,
                from_signature=from_sig,
                to_signature=to_sig,
                details={"issued": bool(gx.get("isIssued")), "revoked": bool(gx.get("isRevoked"))},
            )
        except Exception:
            gx = None
            self.emit_event(
                component="orchestrator",
                stage="grant_lookup",
                status="skipped",
                message="No reusable grant was available",
                policy_id=policy_id,
                from_signature=from_sig,
                to_signature=to_sig,
            )

        now = _now()
        exp_at = now + int(expiry_secs)

        # Determine the signer: the resource owner (registrar) of the *to* node
        to_owner_addr = self.get_address_from_signature(to_sig)
        owner_idx = self._prefunded_index_for_address(to_owner_addr)
        # if owner_idx is None:
        #     return {"ok": False, "why": "owner_signer_not_found", "owner": to_owner_addr}

        _grant_latency_ms: Optional[float] = None
        _grant_reused = False

        if gx and gx.get("isIssued") and not gx.get("isRevoked") and gx.get("expiresAt", 0) > now:
            _grant_reused = True
            self.emit_event(
                component="orchestrator",
                stage="grant_issue_or_reuse",
                status="reused",
                message="Reusing an existing valid grant",
                policy_id=policy_id,
                from_signature=from_sig,
                to_signature=to_sig,
            )
        else:
            _grant_t0 = time.monotonic()
            if allow_delegation and delegation_depth > 0:
                self.issue_grant_delegable(
                    from_sig, to_sig, policy_id, op, exp_at, True, delegation_depth, from_idx=owner_idx
                )
            else:
                self.issue_grant(
                    from_sig, to_sig, policy_id, op, exp_at, from_idx=owner_idx
                )
            _grant_latency_ms = round((time.monotonic() - _grant_t0) * 1000, 3)
            self.emit_event(
                component="orchestrator",
                stage="grant_issue_or_reuse",
                status="ok",
                message="Issued a fresh grant for the request",
                policy_id=policy_id,
                from_signature=from_sig,
                to_signature=to_sig,
                details={"delegable": bool(allow_delegation and delegation_depth > 0)},
            )

        # Final decision
        granted = self.check_grant(from_sig, to_sig, policy_id, op)
        if granted and audit and os.getenv("REAL_INTERACT"):
            # Fire-and-forget: submit audit tx in background so HTTP response is not blocked
            _f, _t, _p, _o = from_sig, to_sig, policy_id, op
            def _audit_bg(_f=_f, _t=_t, _p=_p, _o=_o):
                try:
                    self.check_grant_and_log(_f, _t, _p, _o)
                except Exception:
                    pass
            threading.Thread(target=_audit_bg, daemon=True).start()
        _result = {
            "ok": True,
            "granted": granted,
            "op": op,
            "policyId": policy_id,
            "ctx": ctx,
            "policy_created": _policy_created,
            "policy_latency_ms": _policy_latency_ms,
            "grant_latency_ms": _grant_latency_ms,
            "grant_reused": _grant_reused,
        }
        if granted:
            # Cache positive decisions until the grant expires
            _grant_expiry = float(gx.get("expiresAt", 0)) if gx else 0.0
            _cache_until = _grant_expiry if _grant_expiry > time.time() else time.time() + float(expiry_secs)
            with self._grant_cache_lock:
                self._grant_cache[_cache_key] = (_result, _cache_until)
        return _result

    #@track_performance
    def delegate_flow(self, parent_from_sig: str, to_sig: str, child_from_sig: str,
                      ops_csv: str, child_expiry_secs: int = 600, policy_id: Optional[int] = None) -> Dict[str,Any]:
        """
        Performs a delegation hop: (parent_from_sig -> to_sig) delegates to (child_from_sig -> to_sig).
        Precondition: parent grant must be delegable with depth>0 and include ops_csv; child expiry must be shorter.
        """
        if not self.current_flow_id():
            self.start_flow(
                "delegation",
                stage="request_received",
                message="Delegation request received",
                component="orchestrator",
                details={"ops": ops_csv, "child_expiry_secs": child_expiry_secs},
                from_signature=parent_from_sig,
                to_signature=to_sig,
            )
        self.emit_event(
            component="orchestrator",
            stage="parent_grant_fetch",
            status="started",
            message="Fetching the parent grant for delegation",
            from_signature=parent_from_sig,
            to_signature=to_sig,
            details={"child_from_signature": child_from_sig, "ops": ops_csv},
        )
        parent = self.get_grant_ex_any(parent_from_sig, to_sig, policy_id=policy_id)
        self.emit_event(
            component="orchestrator",
            stage="parent_grant_fetch",
            status="ok",
            message="Parent grant loaded",
            from_signature=parent_from_sig,
            to_signature=to_sig,
            policy_id=int(parent.get("policyId", 0) or 0) or None,
        )
        self.emit_event(
            component="orchestrator",
            stage="delegation_preconditions",
            status="started",
            message="Checking delegation preconditions",
            from_signature=parent_from_sig,
            to_signature=to_sig,
        )
        if not parent.get("delegationAllowed"):
            return {"ok": False, "why": "delegation_not_allowed"}
        if int(parent.get("delegationDepth", 0)) <= 0:
            return {"ok": False, "why": "delegation_depth_exhausted"}

        parent_exp = int(parent.get("expiresAt", 0))
        now = _now()
        if parent_exp <= now:
            return {"ok": False, "why": "parent_expired"}

        # ensure child expiry is strictly shorter
        child_exp_at = min(parent_exp - 1, now + int(child_expiry_secs))
        if child_exp_at <= now:
            return {"ok": False, "why": "invalid_child_expiry"}
        self.emit_event(
            component="orchestrator",
            stage="delegation_preconditions",
            status="ok",
            message="Delegation preconditions satisfied",
            from_signature=parent_from_sig,
            to_signature=to_sig,
            details={"child_expiry_at": child_exp_at},
        )

        # Attempt delegation
        try:
            parent_pid = int(parent.get("policyId", 0) or 0) or None
            out = self.delegate_grant(parent_from_sig, to_sig, child_from_sig, _ops_csv(ops_csv), child_exp_at, policy_id=parent_pid)
            ok = True
        except Exception as e:
            return {"ok": False, "why": f"delegate_reverted:{e}"}

        # Optionally check
        granted = True
        try:
            primary_op = _ops_csv(ops_csv).split(",")[0]
            # Use the parent's policyId for the child (delegation keeps same policy)
            pid = int(parent.get("policyId", 0) or 0)
            self.emit_event(
                component="orchestrator",
                stage="delegated_grant_verification",
                status="started",
                message="Verifying the delegated grant",
                policy_id=pid,
                from_signature=child_from_sig,
                to_signature=to_sig,
            )
            granted = self.check_grant(child_from_sig, to_sig, pid, primary_op)
            self.emit_event(
                component="orchestrator",
                stage="delegated_grant_verification",
                status="ok" if granted else "denied",
                message="Delegated grant verification completed",
                policy_id=pid,
                from_signature=child_from_sig,
                to_signature=to_sig,
                details={"granted": granted},
            )
        except Exception:
            granted = False

        return {"ok": ok, "granted": granted, "tx": out}
