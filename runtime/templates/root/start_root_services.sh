#!/bin/bash

# =========[ NET HELPERS ]=========
detect_ip(){
  local ip=""
  if command -v ip >/dev/null 2>&1; then
    ip="$(ip -4 addr show wlan0 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 || true)"
    [[ -z "$ip" ]] && ip="$(ip -4 addr show eth0 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 || true)"
  fi
  [[ -z "$ip" && $(command -v hostname) ]] && ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  [[ -z "$ip" ]] && ip="127.0.0.1"
  echo "$ip"
}

# =========[ CONFIG ]=========
ROOT_PATH="$(pwd)"
ROOT_PATH_PYTHON="$(dirname "$(pwd)")"
PYTHON_V_ENV="$ROOT_PATH_PYTHON/.venv/bin/python"

# Default root API server & chain
# OWNER_HOST="127.0.0.1"
OWNER_HOST="$(detect_ip)"
FLASK_PORT=5000      # legacy, used by self-register helper
BESU_PORT=8545
OWNER_API="http://$OWNER_HOST:$FLASK_PORT"
BESU_RPC_URL="http://$OWNER_HOST:$BESU_PORT"


BLOCKCHAIN_SCRIPT="$ROOT_PATH/root_blockchain_init.py"
FLASK_SCRIPT="$ROOT_PATH/orchestration_service.py"

SOURCE_FILE="$ROOT_PATH/smart_contract_deployment/build/contracts/NodeRegistry.json"
DESTINATION_DIR="$ROOT_PATH/data"

# Orchestrator env toggles (export before calling if you want different behavior)
export REAL_INTERACT="${REAL_INTERACT:-1}"   # talk to real interact.js by default
export ORCH_TRACE="${ORCH_TRACE:-0}"         # set 1 to see node commands
export FROM_IDX="${FROM_IDX:-0}"             # signer index for interact.js

# =========[ COLORS ]=========
RED="\033[31m"; GREEN="\033[32m"; YELLOW="\033[33m"; BLUE="\033[34m"; CYAN="\033[36m"; RESET="\033[0m"

# =========[ UI HELPERS ]=========
print_header() { echo -e "\n${BLUE}========== $1 ==========${RESET}"; }
success() { echo -e "${GREEN}[✔] $1${RESET}"; }
error()   { echo -e "${RED}[✘] $1${RESET}"; }
warn()    { echo -e "${YELLOW}[!] $1${RESET}"; }

# =========[ OWNER: BLOCKCHAIN / SERVICE ]=========
root_start_service() {
  print_header "Starting Orchestration API (Owner)"
  if lsof -iTCP:"$FLASK_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    error "Flask is already running on port $FLASK_PORT"
    return 1
  fi
  osascript -e "tell application \"Terminal\" to do script \"$PYTHON_V_ENV $FLASK_SCRIPT --host 0.0.0.0 --port $FLASK_PORT --repo-root $ROOT_PATH\""
  # $PYTHON_V_ENV "$FLASK_SCRIPT" --host 0.0.0.0 --port $FLASK_PORT --repo-root $ROOT_PATH \
  # >"$ROOT_PATH/root_flask.log" 2>&1 &
  success "API starting on $FLASK_PORT"
}

root_health() {
  print_header "Owner Health"
  curl -s "$OWNER_API/health" | jq .
}

root_initialize_blockchain() {
  print_header "Initializing Blockchain Root"

  [ -f "$ROOT_PATH/qbftConfigFile.json" ] && warn "qbftConfigFile.json exists" \
    || $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" create_qbft_file 1 1

  [ -f "$ROOT_PATH/data/key.priv" ] && [ -f "$ROOT_PATH/data/key.pub" ] && warn "Keys exist" \
    || $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" generate_keys

  [ -f "$ROOT_PATH/genesis/genesis.json" ] && warn "genesis.json exists" \
    || $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" create_genesis_file "$ROOT_PATH/qbftConfigFile.json"

  [ -f "$ROOT_PATH/genesis/validator_address.json" ] && warn "validator_address.json exists" \
    || $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" update_genesis_file

  [ -f "$ROOT_PATH/genesis/extraData.rlp" ] && warn "extraData.rlp exists" \
    || $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" update_extra_data_in_genesis

  success "Blockchain init complete"
}

root_start_chain() {
  print_header "Starting Blockchain"
  if lsof -iTCP:"$BESU_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    warn "Blockchain is already running on port $BESU_PORT"
    return 1
  fi
  osascript -e "tell application \"Terminal\" to do script \"$PYTHON_V_ENV $BLOCKCHAIN_SCRIPT start_blockchain_node $OWNER_HOST\""
  # >"$ROOT_PATH/root_besu.log" 2>&1 &
  sleep 3
  pgrep -f besu >/dev/null && success "Blockchain node started" || error "Failed to start blockchain"
}

root_stop_chain() {
  print_header "Stopping Blockchain"
  pkill -f "besu" && success "Blockchain stopped" || warn "No blockchain process found"
}

root_restart_chain() {
  print_header "Restarting Blockchain"
  root_stop_chain
  sleep 2
  root_start_chain
}

root_reinit_chain() {
  print_header "Reinitializing Blockchain Root"
  rm -rf "$ROOT_PATH/data" "$ROOT_PATH/genesis" "$ROOT_PATH/qbftConfigFile.json" "$ROOT_PATH/prefunded_keys.json" "$ROOT_PATH/node-details.json" "$ROOT_PATH/policy_index.json"
  root_initialize_blockchain
}

root_deploy_contract() {
  print_header "Deploying Smart Contract"
  read -rp "Enter private key for deployment: " private_key
  [ -z "$private_key" ] && { error "Private key required"; exit 1; }
  bash "$ROOT_PATH/smart_contract_deployment/compile_deploy_contract.sh" "$private_key"
  [ -f "$SOURCE_FILE" ] && cp "$SOURCE_FILE" "$DESTINATION_DIR" && success "Contract deployed & copied" \
    || error "Deployed contract artifact not found"
}

# =========[ OWNER: ORCHESTRATION OPS ]=========

root_node_info() {
  local signature="$1"
  [ -z "$signature" ] && { error "Usage: $0 root-node-info <signature>"; exit 1; }
  print_header "Node Details (Owner)"
  curl -s "$OWNER_API/node/$signature" | jq .
}

root_validators() {
  print_header "Validators (Owner)"
  curl -s "$OWNER_API/validators" | jq .
}

root_revoke_grant() {
  local from_sig="$1" to_sig="$2"
  [ -z "$from_sig" ] || [ -z "$to_sig" ] && { error "Usage: $0 root-revoke-grant <from_sig> <to_sig>"; exit 1; }
  print_header "Revoke Grant (Owner)"
  curl -s -X POST "$OWNER_API/revoke-grant" -H "Content-Type: application/json" \
    -d "{\"from_signature\":\"$from_sig\",\"to_signature\":\"$to_sig\"}" | jq .
}

root_delegate() {
  local parent_from="$1" to_sig="$2" child_from="$3" ops_csv="$4" child_expiry="${5:-600}"
  [ -z "$parent_from" ] || [ -z "$to_sig" ] || [ -z "$child_from" ] || [ -z "$ops_csv" ] && {
    error "Usage: $0 root-delegate <parent_from_sig> <to_sig> <child_from_sig> <ops_csv> [child_expiry_secs]"
    exit 1
  }
  print_header "Delegate Grant (Owner / current grant holder)"
  curl -s -X POST "$OWNER_API/delegate" -H "Content-Type: application/json" \
    -d "{\"parent_from_sig\":\"$parent_from\",\"to_sig\":\"$to_sig\",\"child_from_sig\":\"$child_from\",\"ops_csv\":\"$ops_csv\",\"child_expiry_secs\":$child_expiry}" | jq .
}

root_grant_info() {
  local from_sig="$1" to_sig="$2" method="${3:-GET}" path="${4:-/temperature}"
  [ -z "$from_sig" ] || [ -z "$to_sig" ] && { error "Usage: $0 root-grant-info <from_sig> <to_sig> [METHOD] [/resource_path]"; exit 1; }
  print_header "Grant Info (Owner)"
  curl -s "$OWNER_API/grant?from_signature=$from_sig&to_signature=$to_sig&method=$method&resource_path=$path" | jq .
}

register() {
  # # Prevent duplicate nodeId before sending
  # if curl -fsS "$OWNER_API/nodes" | jq -e ".[] | select(.node_id==\"$node_id\")" >/dev/null; then
  #   error "Duplicate nodeId '$node_id' already registered"
  #   return 1
  # fi
  print_header "Registration (POST /register-node)"

  local node_id="$1"
  local node_name="$2"
  local node_type="$3"              # e.g., Cloud|Fog|Edge|Sensor|Actuator
  local wants="${4:-auto}"          # true|false|auto

  # Preflight: Owner API must be up (Flask on $port

  if [ -z "$node_id" ] || [ -z "$node_name" ] || [ -z "$node_type" ]; then
    error "Usage: $0 register <node_id> <node_name> <node_type> [wants_validator:true|false|auto]"
    return 1
  fi

  # Key paths + RPC
  local pub_path="$ROOT_PATH/data/key.pub"
  local priv_path="$ROOT_PATH/data/key.priv"

  if [ ! -f "$pub_path" ] || [ ! -f "$priv_path" ]; then
    error "Missing $pub_path or $priv_path. Run root-init-chain first."
    return 1
  fi

  # Resolve wants_validator if "auto":
  # heuristic: Cloud/Fog => true, else false. If /validators reachable and our address is in it, force true.
  local wants_bool
  if [ "$wants" = "auto" ]; then
    wants_bool=false
    case "$node_type" in
      Cloud|Fog) wants_bool=true ;;
    esac
    # If we can prove we’re (already) a validator, force true
    if curl -fsS "$OWNER_API/validators" >/dev/null 2>&1; then
      # get our address using node_identity
      my_addr="$($PYTHON_V_ENV "$ROOT_PATH/node_identity.py" address "$priv_path" 2>/dev/null || true)"
      if [ -n "$my_addr" ] && curl -fsS "$OWNER_API/validators" | grep -qi "$(echo "$my_addr" | tr '[:upper:]' '[:lower:]')" ; then
        wants_bool=true
      fi
    fi
  else
    wants_bool=$( [ "$wants" = "true" ] && echo true || echo false )
  fi

  # Build identity bundle JSON (also writes node-details.json for you)
  local bundle_json
  bundle_json="$($PYTHON_V_ENV "$ROOT_PATH/node_identity.py" bundle \
    "$node_id" "$node_name" "$node_type" "$pub_path" "$priv_path" "$BESU_RPC_URL" "$wants_bool")" || {
      error "Failed to build identity bundle"
      return 1
    }

  # Pretty print what we’re sending
  echo "POST -> $OWNER_API/register-node"
  if command -v jq >/dev/null 2>&1; then echo "$bundle_json" | jq .; else echo "$bundle_json"; fi

  # Send registration
  curl -s -X POST "$OWNER_API/register-node" \
    -H "Content-Type: application/json" \
    -d "$bundle_json" | (command -v jq >/dev/null 2>&1 && jq . || cat)

  success "Registration sent. node-details.json updated."
}

# ===== Unified access helpers (root/client) =====

# --- compatibility wrappers so this block works in both scripts ---
_header() {
  if declare -F print_header >/dev/null 2>&1; then print_header "$1";
  elif declare -F hdr >/dev/null 2>&1; then hdr "$1";
  else echo -e "\n========== $1 =========="; fi
}
_err() {
  if declare -F error >/dev/null 2>&1; then error "$1";
  elif declare -F err >/dev/null 2>&1; then err "$1";
  else echo "ERROR: $1" >&2; fi
}

# POST /access (generic)
# Usage: client-access <root_host:port> <from_sig> <METHOD> </resource_path> [expiry_secs] [allow_delegation(true|false)] [delegation_depth]
client_access() {
  local root_hp="$1" from_sig="$2" method="$3" path="$4" expiry="${5:-900}" allow="${6:-false}" depth="${7:-0}"
  if [ -z "$root_hp" ] || [ -z "$from_sig" ] || [ -z "$method" ] || [ -z "$path" ]; then
    _err "Usage: $0 client-access <root_host:port> <from_sig> <METHOD> </resource_path> [expiry_secs] [allow_delegation(true|false)] [delegation_depth]"
    exit 1
  fi
  local API="http://$root_hp"
  _header "Client → POST $API/access ($method $path)"

  curl -s -X POST "$API/access" -H "Content-Type: application/json" \
    -d "{\"from_signature\":\"$from_sig\",\"method\":\"$method\",\"resource_path\":\"$path\",\"expiry_secs\":$expiry,\"allow_delegation\":$allow,\"delegation_depth\":$depth}" \
    | (command -v jq >/dev/null 2>&1 && jq . || cat)
}

# ----- Convenience endpoints (receiver derives to_signature itself) -----

# GET /temperature
# Usage: temp-read <root_host:port> <from_sig> [/resource_path]


temp_read() {    
  local root_hp="$1" from_sig="$2" path="${3:-/temperature}"
  if [ -z "$root_hp" ] || [ -z "$from_sig" ]; then
    _err "Usage: $0 temp-read <root_host:port> <from_sig> [/resource_path]"
    exit 1
  fi
  local API="http://$root_hp"
  _header "Client → GET $API$path"
  curl -s -X GET "$API/temperature?from_signature=$from_sig&resource_path=$path" \
    | (command -v jq >/dev/null 2>&1 && jq . || cat)
}

# POST /temperature
# Usage: temp-write <root_host:port> <from_sig> [/resource_path]
temp_write() {
  local root_hp="$1" from_sig="$2" path="${3:-/temperature}"
  if [ -z "$root_hp" ] || [ -z "$from_sig" ]; then
    _err "Usage: $0 temp-write <root_host:port> <from_sig> [/resource_path]"
    exit 1
  fi
  local API="http://$root_hp"
  _header "Client → POST $API$path"
  curl -s -X POST "$API/temperature?from_signature=$from_sig&resource_path=$path" \
    | (command -v jq >/dev/null 2>&1 && jq . || cat)
}

# PUT /firmware
# Usage: fw-update <root_host:port> <from_sig> [/resource_path]
fw_update() {
  local root_hp="$1" from_sig="$2" path="${3:-/firmware}"
  if [ -z "$root_hp" ] || [ -z "$from_sig" ]; then
    _err "Usage: $0 fw-update <root_host:port> <from_sig> [/resource_path]"
    exit 1
  fi
  local API="http://$root_hp"
  _header "Client → PUT $API$path"
  curl -s -X PUT "$API/firmware?from_signature=$from_sig&resource_path=$path" \
    | (command -v jq >/dev/null 2>&1 && jq . || cat)
}

# DELETE /firmware
# Usage: fw-remove <root_host:port> <from_sig> [/resource_path]
fw_remove() {
  local root_hp="$1" from_sig="$2" path="${3:-/firmware}"
  if [ -z "$root_hp" ] || [ -z "$from_sig" ]; then
    _err "Usage: $0 fw-remove <root_host:port> <from_sig> [/resource_path]"
    exit 1
  fi
  local API="http://$root_hp"
  _header "Client → DELETE $API$path"
  curl -s -X DELETE "$API/firmware?from_signature=$from_sig&resource_path=$path" \
    | (command -v jq >/dev/null 2>&1 && jq . || cat)
}

# GET /alerts
# Usage: alerts-read <root_host:port> <from_sig> [/resource_path]
alerts_read() {
  local root_hp="$1" from_sig="$2" path="${3:-/alerts}"
  if [ -z "$root_hp" ] || [ -z "$from_sig" ]; then
    _err "Usage: $0 alerts-read <root_host:port> <from_sig> [/resource_path]"
    exit 1
  fi
  local API="http://$root_hp"
  _header "Client → GET $API$path"
  curl -s -X GET "$API/alerts?from_signature=$from_sig&resource_path=$path" \
    | (command -v jq >/dev/null 2>&1 && jq . || cat)
}

# POST /alerts
# Usage: alerts-create <root_host:port> <from_sig> [/resource_path]
alerts_create() {
  local root_hp="$1" from_sig="$2" path="${3:-/alerts}"
  if [ -z "$root_hp" ] || [ -z "$from_sig" ]; then
    _err "Usage: $0 alerts-create <root_host:port> <from_sig> [/resource_path]"
    exit 1
  fi
  local API="http://$root_hp"
  _header "Client → POST $API$path"
  curl -s -X POST "$API/alerts?from_signature=$from_sig&resource_path=$path" \
    | (command -v jq >/dev/null 2>&1 && jq . || cat)
}

# PUT /control/led
# Usage: control-led <root_host:port> <from_sig> [/resource_path]
control_led() {
  local root_hp="$1" from_sig="$2" path="${3:-/control/led}"
  if [ -z "$root_hp" ] || [ -z "$from_sig" ]; then
    _err "Usage: $0 control-led <root_host:port> <from_sig> [/resource_path]"
    exit 1
  fi
  local API="http://$root_hp"
  _header "Client → PUT $API$path"
  curl -s -X PUT "$API/control/led?from_signature=$from_sig&resource_path=$path" \
    | (command -v jq >/dev/null 2>&1 && jq . || cat)
}

# DELETE /control/motor
# Usage: control-motor-stop <root_host:port> <from_sig> [/resource_path]
control_motor_stop() {
  local root_hp="$1" from_sig="$2" path="${3:-/control/motor}"
  if [ -z "$root_hp" ] || [ -z "$from_sig" ]; then
    _err "Usage: $0 control-motor-stop <root_host:port> <from_sig> [/resource_path]"
    exit 1
  fi
  local API="http://$root_hp"
  _header "Client → DELETE $API$path"
  curl -s -X DELETE "$API/control/motor?from_signature=$from_sig&resource_path=$path" \
    | (command -v jq >/dev/null 2>&1 && jq . || cat)
}


# =======[ Policy Admin ]=======
_policy_normalize_ops() {
  local ops_csv="$1"
  IFS=',' read -r -a raw <<< "$ops_csv"
  local out=()
  for x in "${raw[@]}"; do
    x_uc="$(echo "$x" | tr '[:lower:]' '[:upper:]')"
    case "$x_uc" in
      READ|WRITE|UPDATE|REMOVE) out+=("$x_uc") ;;
      GET)    out+=("READ") ;;
      POST)   out+=("WRITE") ;;
      PUT|PATCH) out+=("UPDATE") ;;
      DELETE) out+=("REMOVE") ;;
      *) echo "Unknown op: $x" 1>&2; return 1 ;;
    esac
  done
  (IFS=,; echo "${out[*]}")
}

policy_next_id() {
  print_header "Next Policy ID"
  node "$ROOT_PATH/interact.js" nextPolicyId
}

policy_set_admin() {
  print_header "Set Policy Admin"
  local new_admin="$1"
  [[ -z "$new_admin" ]] && { error "Usage: $0 policy-set-admin <address>"; return 1; }
  node "$ROOT_PATH/interact.js" setPolicyAdmin "$new_admin"
}

policy_create() {
  print_header "Create Policy"
  local from_role="$1" to_role="$2" ops_in="$3" ctx="${4:-device:v1}"
  [[ -z "$from_role" || -z "$to_role" || -z "$ops_in" ]] && {
    error "Usage: $0 policy-create <fromRole|num> <toRole|num> <ops CSV|GET,POST|mask> [ctxSchema]"
    return 1
  }
  local ops_csv
  ops_csv="$(_policy_normalize_ops "$ops_in")" || return 1
  printf "Creating policy: %s -> %s, ops=%s, ctx=%s\n" "$from_role" "$to_role" "$ops_csv" "$ctx"
  node "$ROOT_PATH/interact.js" createPolicy "$from_role" "$to_role" "$ops_csv" "$ctx"
}

policy_update() {
  print_header "Update Policy"
  local id="$1" ops_in="$2" ctx="${3:-device:v1}"
  [[ -z "$id" || -z "$ops_in" ]] && {
    error "Usage: $0 policy-update <policyId> <ops CSV|GET,POST|mask> [ctxSchema]"
    return 1
  }
  local ops_csv
  ops_csv="$(_policy_normalize_ops "$ops_in")" || return 1
  node "$ROOT_PATH/interact.js" updatePolicy "$id" "$ops_csv" "$ctx"
}

policy_deprecate() {
  print_header "Deprecate Policy"
  local id="$1"
  [[ -z "$id" ]] && { error "Usage: $0 policy-deprecate <policyId>"; return 1; }
  node "$ROOT_PATH/interact.js" deprecatePolicy "$id"
}

policy_get() {
  print_header "Get Policy"
  local id="$1"
  [[ -z "$id" ]] && { error "Usage: $0 policy-get <policyId>"; return 1; }
  node "$ROOT_PATH/interact.js" getPolicy "$id"
}

approve_create_policy() {
  local from_role="$1" to_role="$2" ops="$3" ctx="${4:-device:v1}"
  [[ -z "$from_role" || -z "$to_role" || -z "$ops" ]] && {
    err "Usage: $0 approve-create-policy <fromRole|num> <toRole|num> <opsCsv|mask> [ctx]"
    exit 1
  }
  print_header "Approve Create Policy"
  node "$ROOT_PATH/interact.js" approveCreatePolicy "$from_role" "$to_role" "$ops" "$ctx"
}


msig_mode() {
  node "$ROOT_PATH/interact.js" msigMode "$1"
}
msig_is_approver() {
  local addr="$1"
  [[ -z "$addr" ]] && { err "Usage: $0 msig-is-approver <address>"; exit 1; }
  hdr "Is Approver?"
  node "$ROOT_PATH/interact.js" msigIsApprover "$addr"
}
msig_add() {
  node "$ROOT_PATH/interact.js" msigAdd "$1"
}
msig_remove() {
  node "$ROOT_PATH/interact.js" msigRemove "$1"
}
msig_threshold() {
  node "$ROOT_PATH/interact.js" msigThreshold "$1"
}
msig_info() {
  node "$ROOT_PATH/interact.js" msigInfo | (command -v jq >/dev/null 2>&1 && jq . || cat)
}

# Idempotent create: reuses an existing identical policy if present
policy_ensure() {
  print_header "Ensure Policy"
  local from_role="$1" to_role="$2" ops_in="$3" ctx="${4:-device:v1}"
  [[ -z "$from_role" || -z "$to_role" || -z "$ops_in" ]] && {
    error "Usage: $0 policy-ensure <fromRole|num> <toRole|num> <ops CSV|GET,POST|mask> [ctxSchema]"
    return 1
  }
  local ops_csv
  ops_csv="$(_policy_normalize_ops "$ops_in")" || return 1
  node "$ROOT_PATH/interact.js" ensurePolicy "$from_role" "$to_role" "$ops_csv" "$ctx"
}

# =========[ HELP ]=========
show_help() {
  echo -e "${CYAN}Usage:${RESET} $0 <command> [args]"
  echo
  echo -e "${CYAN}Owner: Service & Chain${RESET}"
  echo "  root-flask.                        Start orchestration Flask API on $OWNER_API"
  echo "  root-health                        Check /health"
  echo "  root-init-chain                    Initialize blockchain root (keys/genesis/extraData)"
  echo "  root-start-chain                   Start blockchain process"
  echo "  root-stop-chain                    Stop blockchain process"
  echo "  root-restart-chain                 Restart blockchain"
  echo "  root-reinit-chain                  Wipe and re-init chain data"
  echo "  root-deploy-contract               Compile/deploy smart contract and copy artifact"
  echo "  root-node-register <node_id> <node_name> <node_type> <wants_validator:true|false>  Self-register root node"
  echo
  echo -e "${CYAN}Owner: Orchestration Ops (device root only)${RESET}"
  echo "  root-node-info <signature>         Show on-chain node details"
  echo "  root-validators                    Show validator set"
  echo "  root-revoke-grant <from> <to>      Revoke a grant"
  echo "  root-delegate <parent> <to> <child> <ops_csv> [expiry]   Delegate grant"
  echo "  root-grant-info <from> <to>        Read grant info"
  echo
  echo -e "${CYAN}Access${RESET}"
  echo "  client-access <root_host:port> <from_sig> <METHOD> </path> [expiry] [allow_delegation] [depth]"
  echo "  temp-read <root_host:port> <from_sig> [/path]         GET /temperature"
  echo "  temp-write <root_host:port> <from_sig> [/path]        POST /temperature"
  echo "  fw-update <root_host:port> <from_sig> [/path]         PUT /firmware"
  echo "  fw-remove <root_host:port> <from_sig> [/path]         DELETE /firmware"
  echo "  alerts-read <root_host:port> <from_sig> [/path]       GET /alerts"
  echo "  alerts-create <root_host:port> <from_sig> [/path]     POST /alerts"
  echo "  control-led <root_host:port> <from_sig> [/path]       PUT /control/led"
  echo "  control-motor-stop <root_host:port> <from_sig> [/path]  DELETE /control/motor"
  echo
  echo -e "${CYAN}Env toggles (export before running):${RESET}"
  echo "  REAL_INTERACT=1   talk to chain via interact.js (default 1)"
  echo "  ORCH_TRACE=1      print interact.js calls"
  echo "  FROM_IDX=0        signer index used by interact.js"
  echo "  OWNER_PORT=8080   port for orchestration_service"
  echo
  echo -e "${CYAN}Owner: Policy Admin (contract)${RESET}"
  echo "  policy-next-id                     Show next policy id"
  echo "  policy-set-admin <address>         Set new policy admin"
  echo "  policy-create <fromRole> <toRole> <opsCsv|mask> [schema]   Create policy"
  echo "  policy-update <id> <opsCsv|mask> [schema]                  Update policy"
  echo "  policy-deprecate <id>              Deprecate policy"
  echo "  policy-get <id>                    Read policy"
  echo "  policy-ensure <fromRole> <toRole> <opsCsv|mask> [schema]   Idempotent create policy"

}

# =========[ DISPATCH ]=========
cmd="$1"; shift || true
case "$cmd" in
  # Owner: service/chain
  init-chain-root) root_initialize_blockchain ;;
  reinit-chain-root) root_reinit_chain ;;
  start-chain-root) root_start_chain ;;
  stop-chain-root) root_stop_chain ;;
  restart-chain-root) root_restart_chain ;;
  deploy-contract) root_deploy_contract ;;
  start-flask-root) root_start_service ;;
  root-health) root_health ;;
  root-node-register) register "$@" ;;

  # Owner: Policy Admin
  policy-next-id)                policy_next_id ;;
  policy-set-admin)              policy_set_admin "$@" ;;
  policy-create)                 policy_create "$@" ;;
  policy-update)                 policy_update "$@" ;;
  policy-deprecate)              policy_deprecate "$@" ;;
  policy-get)                    policy_get "$@" ;;
  policy-ensure)                 policy_ensure "$@" ;;
  approve-create-policy)         approve_create_policy "$@" ;;

  # Multisig (owner/admin)
  msig-mode)        msig_mode "$@" ;;
  msig-is-approver)          msig_is_approver "$@" ;;
  msig-add)         msig_add "$@" ;;
  msig-remove)      msig_remove "$@" ;;
  msig-threshold)   msig_threshold "$@" ;;
  msig-info)        msig_info ;;

  # Owner: orchestration ops
  root-node-info) root_node_info "$@" ;;
  root-validators) root_validators ;;
  root-revoke-grant) root_revoke_grant "$@" ;;
  root-delegate) root_delegate "$@" ;;
  root-grant-info) root_grant_info "$@" ;;

  # Access
  client-access)       client_access "$@" ;;
  temp-read)           temp_read "$@" ;;
  temp-write)          temp_write "$@" ;;
  fw-update)           fw_update "$@" ;;
  fw-remove)           fw_remove "$@" ;;
  alerts-read)         alerts_read "$@" ;;
  alerts-create)       alerts_create "$@" ;;
  control-led)         control_led "$@" ;;
  control-motor-stop)  control_motor_stop "$@" ;;

  help|"") show_help ;;
  *) error "Unknown command: $cmd"; show_help; exit 1 ;;
esac