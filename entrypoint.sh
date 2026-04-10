#!/bin/bash
set -euo pipefail

NODE_ROLE="${NODE_ROLE:-cloud}"
ROOT_URL="${ROOT_URL:-}"
PARENT_URL="${PARENT_URL:-}"
REPO_ROOT="${REPO_ROOT:-/app/Node_root}"
DATA_DIR="${DATA_DIR:-$REPO_ROOT/data}"
GENESIS_DIR="${GENESIS_DIR:-$REPO_ROOT/genesis}"
GENESIS_FILE="${GENESIS_DIR}/genesis.json"
REGISTRY_FILE="${DATA_DIR}/NodeRegistry.json"
KEYS_FILE="${REPO_ROOT}/prefunded_keys.json"
ENODE_FILE="${DATA_DIR}/enode.txt"
FLASK_PORT="${FLASK_PORT:-5600}"

mkdir -p "$DATA_DIR" "$GENESIS_DIR"

wait_for_url() {
  local url="$1"
  local attempts="${2:-60}"
  local i=0
  while ! curl -sf "${url}/health" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge "$attempts" ]; then
      echo "Timeout waiting for ${url}" >&2
      return 1
    fi
    sleep 2
  done
}

wait_for_file() {
  local file_path="$1"
  local label="$2"
  local attempts="${3:-60}"
  local i=0
  until [ -f "$file_path" ]; do
    i=$((i + 1))
    if [ "$i" -ge "$attempts" ]; then
      echo "Timeout waiting for ${label} (${file_path})" >&2
      return 1
    fi
    sleep 2
  done
}

ensure_non_root_identity() {
  local role="$1"
  cd "$REPO_ROOT"
  NODE_ROLE="$role" python - <<'PY'
import hashlib
import json
import os
import secrets
from pathlib import Path

from eth_keys import keys
from eth_utils import keccak


def to_bool(value: str | None, default: bool) -> bool:
  if value is None:
    return default
  return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


role_raw = os.environ.get("NODE_ROLE", "").strip().lower()
if role_raw == "cloud":
  raise SystemExit(0)

role_to_type = {
  "fog": "Fog",
  "edge": "Edge",
  "endpoint": "Sensor",
}
if role_raw not in role_to_type:
  raise SystemExit(f"Unsupported non-root role for identity generation: {role_raw}")

repo_root = Path(os.environ.get("REPO_ROOT", "/app/Node_root"))
data_dir = Path(os.environ.get("DATA_DIR", str(repo_root / "data")))
data_dir.mkdir(parents=True, exist_ok=True)

persistent_details_path = data_dir / "node-details.json"
working_details_path = repo_root / "node-details.json"
priv_path = data_dir / "key.priv"
pub_path = data_dir / "key.pub"
identity_marker = data_dir / ".identity_initialized"

# For non-root roles, initialize a role-specific Besu keypair on first boot.
# Named Docker volumes can be pre-populated from image defaults, so relying on
# file existence alone can preserve a shared baked-in key across nodes.
if not identity_marker.exists():
  private_key_bytes = secrets.token_bytes(32)
  priv_key = keys.PrivateKey(private_key_bytes)
  priv_path.write_text(priv_key.to_hex(), encoding="utf-8")
  pub_path.write_text(priv_key.public_key.to_hex(), encoding="utf-8")
  identity_marker.write_text("initialized\n", encoding="utf-8")
elif not priv_path.exists() or not pub_path.exists():
  private_key_bytes = secrets.token_bytes(32)
  priv_key = keys.PrivateKey(private_key_bytes)
  priv_path.write_text(priv_key.to_hex(), encoding="utf-8")
  pub_path.write_text(priv_key.public_key.to_hex(), encoding="utf-8")

priv_hex = priv_path.read_text(encoding="utf-8").strip()
if priv_hex.startswith("0x"):
  priv_hex = priv_hex[2:]
priv_key = keys.PrivateKey(bytes.fromhex(priv_hex))
public_key = pub_path.read_text(encoding="utf-8").strip()
address = "0x" + priv_key.public_key.to_canonical_address().hex()

existing = {}
if persistent_details_path.exists():
  try:
    existing = json.loads(persistent_details_path.read_text(encoding="utf-8"))
  except Exception:
    existing = {}

existing_public_key = str(existing.get("public_key", "")).strip().lower()
current_public_key = str(public_key).strip().lower()
identity_mismatch = bool(existing_public_key and existing_public_key != current_public_key)

seed = os.environ.get("NODE_SIGNATURE_SEED", "").strip()
if seed:
  suffix = hashlib.sha256(f"{seed}:{role_raw}".encode("utf-8")).hexdigest()[:10].upper()
else:
  suffix = secrets.token_hex(5).upper()

node_id = (
  os.environ.get("NODE_ID")
  or (None if identity_mismatch else existing.get("node_id"))
  or f"{role_raw.upper()}-{suffix}"
)
node_name = (
  os.environ.get("NODE_NAME")
  or (None if identity_mismatch else existing.get("node_name"))
  or f"{role_raw.title()}-{suffix}"
)
node_type = (
  os.environ.get("NODE_TYPE")
  or (None if identity_mismatch else existing.get("node_type"))
  or role_to_type[role_raw]
)

wants_validator_default = role_raw == "fog"
wants_validator = to_bool(os.environ.get("NODE_WANTS_VALIDATOR"), wants_validator_default)
rpc_url = os.environ.get("BESU_RPC_URL") or existing.get("rpcURL") or "http://127.0.0.1:8545"

message_dict = {
  "node_id": node_id,
  "node_name": node_name,
  "node_type": node_type,
  "public_key": public_key,
}
signature = priv_key.sign_msg_hash(keccak(text=json.dumps(message_dict, sort_keys=True))).to_hex()

payload = {
  "node_id": node_id,
  "node_name": node_name,
  "node_type": node_type,
  "public_key": public_key,
  "address": address,
  "rpcURL": rpc_url,
  "signature": signature,
  "wants_validator": bool(wants_validator),
}

persistent_details_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
working_details_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
print(f"Prepared non-root identity for {role_raw}: {node_id}")
PY
}

register_with_root() {
  local expected_role="$1"
  cd "$REPO_ROOT"
  python - <<'PY'
import json
import os
import sys
import time

import requests

target = (
  os.environ.get("REGISTRATION_URL", "").rstrip("/")
  or os.environ.get("PARENT_URL", "").rstrip("/")
  or os.environ.get("ROOT_URL", "").rstrip("/")
)
if not target:
  raise SystemExit("ROOT_URL or PARENT_URL is required for non-root nodes")

node_details_path = os.path.join(os.environ.get("REPO_ROOT", "/app/Node_root"), "node-details.json")
with open(node_details_path, "r", encoding="utf-8") as handle:
    payload = json.load(handle)

for attempt in range(30):
  try:
    response = requests.post(f"{target}/register-node", json=payload, timeout=10)
    body_text = (response.text or "").lower()
    if response.status_code in (200, 201):
      print("registered")
      sys.exit(0)
    if response.status_code == 409 and "already registered" in body_text:
      print("already_registered")
      sys.exit(0)
    if response.status_code == 500 and "registration_failed" in body_text and "transaction_reverted" in body_text:
      print("already_registered")
      sys.exit(0)
    print(f"Registration attempt {attempt + 1} failed: {response.status_code} {response.text}")
  except Exception as exc:
    print(f"Waiting for upstream registration endpoint: {exc}")
  time.sleep(4)
raise SystemExit("Could not register with upstream")
PY
}

fetch_bootstrap_from_root() {
  cd "$REPO_ROOT"
  python - <<'PY'
import os
import ipaddress
import socket
from urllib.parse import urlparse
from pathlib import Path

import requests

root = os.environ.get("ROOT_URL", "").rstrip("/")
repo_root = Path(os.environ.get("REPO_ROOT", "/app/Node_root"))
data_dir = Path(os.environ.get("DATA_DIR", str(repo_root / "data")))
genesis_dir = Path(os.environ.get("GENESIS_DIR", str(repo_root / "genesis")))

if not root:
    raise SystemExit("ROOT_URL is required to fetch bootstrap assets")

data_dir.mkdir(parents=True, exist_ok=True)
genesis_dir.mkdir(parents=True, exist_ok=True)

artifacts = [
    (f"{root}/bootstrap/genesis.json", genesis_dir / "genesis.json", False),
    (f"{root}/bootstrap/node-registry.json", data_dir / "NodeRegistry.json", False),
    (f"{root}/bootstrap/prefunded_keys.json", repo_root / "prefunded_keys.json", False),
    (f"{root}/bootstrap/enode.txt", data_dir / "enode.txt", True),
]

for url, path, optional in artifacts:
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        print(f"Fetched {path.name} from {url}")
    except Exception as exc:
        if optional:
            print(f"Bootstrap optional asset unavailable at {url}: {exc}")
            continue
        raise

enode_path = data_dir / "enode.txt"
if enode_path.exists():
    enode = enode_path.read_text(encoding="utf-8").strip()
    # Root often advertises 0.0.0.0 in containerized setups. Replace with a
    # routable host derived from ROOT_URL unless explicitly overridden.
    root_host = os.environ.get("ROOT_BOOTNODE_HOST", "").strip()
    if not root_host:
        root_host = urlparse(root).hostname or ""
    if root_host:
      try:
        ipaddress.ip_address(root_host)
      except ValueError:
        try:
          root_host = socket.gethostbyname(root_host)
        except Exception:
          pass
    if root_host and "@" in enode and ":" in enode.rsplit("@", 1)[1]:
      before, after = enode.split("@", 1)
      host, port = after.rsplit(":", 1)
      host_is_ip = False
      try:
        ipaddress.ip_address(host)
        host_is_ip = True
      except ValueError:
        host_is_ip = False
      if host == "0.0.0.0" or not host_is_ip:
        enode = f"{before}@{root_host}:{port}"
        enode_path.write_text(enode + "\n", encoding="utf-8")
    for relative in ("static/enode.txt", "client_inbox/enode.txt"):
        target = repo_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(enode + "\n", encoding="utf-8")
PY
}

ensure_bootstrap_ready() {
  local needs_genesis="${1:-false}"
  if [ ! -f "$REGISTRY_FILE" ] || [ ! -f "$ENODE_FILE" ] || { [ "$needs_genesis" = "true" ] && [ ! -f "$GENESIS_FILE" ]; }; then
    echo "Bootstrap artifacts missing locally; fetching from root..."
    fetch_bootstrap_from_root
  fi

  if [ "$needs_genesis" = "true" ]; then
    wait_for_file "$GENESIS_FILE" "genesis file"
  fi
  wait_for_file "$REGISTRY_FILE" "NodeRegistry bootstrap file"
  wait_for_file "$ENODE_FILE" "root enode bootstrap file"

  # Normalize bootnode host for persisted enode files copied from older runs.
  python - <<'PY'
import os
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

root = os.environ.get("ROOT_URL", "").rstrip("/")
if not root:
    raise SystemExit(0)

repo_root = Path(os.environ.get("REPO_ROOT", "/app/Node_root"))
data_dir = Path(os.environ.get("DATA_DIR", str(repo_root / "data")))
enode_file = data_dir / "enode.txt"
if not enode_file.exists():
    raise SystemExit(0)

enode = enode_file.read_text(encoding="utf-8").strip()
if not enode:
    raise SystemExit(0)

root_host = os.environ.get("ROOT_BOOTNODE_HOST", "").strip() or (urlparse(root).hostname or "")
if root_host:
  try:
    ipaddress.ip_address(root_host)
  except ValueError:
    try:
      root_host = socket.gethostbyname(root_host)
    except Exception:
      pass
if root_host and "@" in enode and ":" in enode.rsplit("@", 1)[1]:
  before, after = enode.split("@", 1)
  host, port = after.rsplit(":", 1)
  host_is_ip = False
  try:
    ipaddress.ip_address(host)
    host_is_ip = True
  except ValueError:
    host_is_ip = False
  if host == "0.0.0.0" or not host_is_ip:
    enode = f"{before}@{root_host}:{port}"
    enode_file.write_text(enode + "\n", encoding="utf-8")

for relative in ("static/enode.txt", "client_inbox/enode.txt"):
    target = repo_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(enode + "\n", encoding="utf-8")
PY
}

start_gunicorn() {
  local workers="$1"
  exec gunicorn -w "$workers" -b "0.0.0.0:${FLASK_PORT}" \
    --timeout 120 --worker-class sync \
    --chdir "$REPO_ROOT" "orchestration_service:make_app()"
}

deploy_root_contract() {
  cd /app
  python - <<'PY'
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app/scripts")
from run_topology import ensure_root_contract_deployed

repo_root = Path("/app")
root_dir = repo_root / "Node_root"
root_rpc_url = os.environ.get("ROOT_RPC_URL", "http://127.0.0.1:8545")
ensure_root_contract_deployed(root_dir, root_rpc_url, os.environ.copy())
print("Root contract deployment check complete")
PY
}

case "$NODE_ROLE" in
  cloud)
    cd "$REPO_ROOT"
    python root_blockchain_init.py start_blockchain_node 0.0.0.0 &
    BESU_WRAPPER_PID=$!
    sleep 8

    deploy_root_contract
    cd "$REPO_ROOT"
    python root_blockchain_init.py self_register_root_node || true

    start_gunicorn 4
    ;;

  fog|edge)
    [ -n "$ROOT_URL" ] || { echo "ROOT_URL is required for ${NODE_ROLE}" >&2; exit 1; }
    ensure_non_root_identity "$NODE_ROLE"
    registration_url="${REGISTRATION_URL:-${PARENT_URL:-$ROOT_URL}}"
    wait_for_url "$registration_url"
    if [ "$registration_url" != "$ROOT_URL" ]; then
      wait_for_url "$ROOT_URL"
    fi
    registration_status="$(register_with_root "$NODE_ROLE" | tail -n 1)"
    case "$registration_status" in
      registered) echo "Registered with root" ;;
      already_registered) echo "Node already registered with root" ;;
      *) echo "Unexpected registration status: ${registration_status}" >&2 ;;
    esac
    ensure_bootstrap_ready true

    cd "$REPO_ROOT"
    if [ ! -f "$REPO_ROOT/client_blockchain_init.py" ] && [ -f /app/runtime/templates/client/client_blockchain_init.py ]; then
      cp /app/runtime/templates/client/client_blockchain_init.py "$REPO_ROOT/client_blockchain_init.py"
    fi
    python client_blockchain_init.py start_blockchain_node 30303 8545 0.0.0.0 &
    CLIENT_CHAIN_PID=$!
    sleep 8

    start_gunicorn 2
    ;;

  endpoint)
    [ -n "$ROOT_URL" ] || { echo "ROOT_URL is required for endpoint" >&2; exit 1; }
    ensure_non_root_identity "$NODE_ROLE"
    registration_url="${REGISTRATION_URL:-${PARENT_URL:-$ROOT_URL}}"
    wait_for_url "$registration_url"
    if [ "$registration_url" != "$ROOT_URL" ]; then
      wait_for_url "$ROOT_URL"
    fi
    registration_status="$(register_with_root "$NODE_ROLE" | tail -n 1)"
    case "$registration_status" in
      registered) echo "Registered with root" ;;
      already_registered) echo "Node already registered with root" ;;
      *) echo "Unexpected registration status: ${registration_status}" >&2 ;;
    esac
    ensure_bootstrap_ready false

    start_gunicorn 1
    ;;

  *)
    echo "Unsupported NODE_ROLE: ${NODE_ROLE}" >&2
    exit 1
    ;;
esac
