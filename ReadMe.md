# BlockCap

BlockCap is a permissioned-IoT authorization prototype built around Hyperledger Besu, Solidity smart contracts, and a Python trust-enforcement daemon. The active project now centers on one maintained implementation path instead of the older duplicated per-role source trees.

## Active Structure

- `Node_root/`
  - Source of truth for the Flask daemon, orchestration logic, metrics helpers, contract interaction layer, and contract sources.
- `runtime/templates/`
  - Minimal runtime templates used by `scripts/run_topology.py`.
  - Templates: `root/`, `client/`, `endpoint/`.
- `scripts/`
  - Topology runner, load testing, experiment aggregation, gas summarization, and contract metrics collection.
- `node-registry-test/`
  - Hardhat-based Solidity test harness for the active contract.
- `results/`
  - Generated experiment outputs.

## What The Project Provides

- Permissioned Besu/QBFT network bootstrap for a cloud-root node and generated fog, edge, and endpoint nodes.
- On-chain node registration and role-aware policy/grant management through `NodeRegistry.sol`.
- Flask APIs for registration, access decisions, delegation, revocation, grant lookup, expiry checks, and latency metrics.
- Experiment tooling for:
  - end-to-end latency
  - concurrent load testing
  - gas logging and gas summaries
  - contract code metrics
  - unified experiment result aggregation

## Requirements

- Java 21+
- Python 3.13+
- Node.js with the dependencies in `package.json`
- Hyperledger Besu on `PATH`
- `jq` recommended for readable JSON output

Install project dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install
```

## Local Multi-Node Development

Start a generated local topology:

```bash
./.venv/bin/python scripts/run_topology.py --cloud 1 --fog 2 --edge 2 --endpoint 3 --scenario demo-1
```

Run the Python test suite:

```bash
./.venv/bin/python -m pytest Node_root/scripts/test_tef_metrics.py Node_root/scripts/test_orchestration_service.py scripts/test_experiment_scripts.py scripts/test_measurement_scripts.py scripts/test_run_topology.py Node_root/scripts/test_orchestrator.py -q
```

Run the Solidity suite:

```bash
cd node-registry-test && npx hardhat test
```

Run the experiment bundle against a generated topology:

```bash
./.venv/bin/python scripts/run_all_experiments.py --scenario-file runtime/generated/<scenario>/topology.json --runs 30
```

## Real Deployment Model

The most practical real deployment is:

- `Mac` as `Cloud/Root`
- `Jetson` as `Fog` or `Edge`
- `Raspberry Pi` as `Edge` or `Endpoint`

Recommended role split:

- `Cloud/Root`: laptop, workstation, or server
- `Fog`: Jetson preferred
- `Edge`: Jetson or Raspberry Pi
- `Endpoint`: Raspberry Pi or other lightweight ARM host

Important notes:

- `Fog` nodes in this project can become validators, so they need more CPU and RAM than endpoints.
- `Endpoint` nodes do not need to run a full Besu validator flow in the active deployment path.
- The old shell helpers under `Node_root/start_root_services.sh` are still useful for local experiments, but real multi-device deployment is clearer with the direct commands below.

## Real Deployment: Mac As Cloud / Root

All commands in this section are run on the Mac.

### 1. Initialize the Root Chain Material

```bash
cd Node_root

../.venv/bin/python root_blockchain_init.py create_qbft_file 1 1
../.venv/bin/python root_blockchain_init.py generate_keys
../.venv/bin/python root_blockchain_init.py create_genesis_file qbftConfigFile.json
../.venv/bin/python root_blockchain_init.py update_genesis_file
../.venv/bin/python root_blockchain_init.py update_extra_data_in_genesis
```

### 2. Start the Root Besu Node

Replace `<MAC_IP>` with the IP address reachable by the Jetson and Raspberry Pi devices.

```bash
cd Node_root

REAL_INTERACT=1 \
ROOT_BESU_RPC_PORT=8545 \
ROOT_BESU_P2P_PORT=30303 \
ROOT_BESU_METRICS_PORT=9545 \
BESU_RPC_URL=http://<MAC_IP>:8545 \
../.venv/bin/python root_blockchain_init.py start_blockchain_node <MAC_IP>
```

### 3. Start the Root Orchestration API

In a second terminal:

```bash
cd Node_root

REAL_INTERACT=1 \
BESU_RPC_URL=http://<MAC_IP>:8545 \
../.venv/bin/python orchestration_service.py --host 0.0.0.0 --port 5600 --repo-root "$(pwd)"
```

### 4. Deploy the Contract

Update `Node_root/smart_contract_deployment/truffle-config.js` if your RPC URL is not the default value inside that file.

```bash
cd Node_root/smart_contract_deployment
npx truffle compile
npx truffle migrate --network besuWallet --reset
cp build/contracts/NodeRegistry.json ../data/NodeRegistry.json
```

### 5. Get the Root Enode

```bash
curl -s -X POST http://<MAC_IP>:8545 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"admin_nodeInfo","params":[],"id":1}' | jq -r '.result.enode'
```

Save the returned enode value. Remote fog and edge nodes need it.

## Real Deployment: Jetson or Raspberry Pi As Fog / Edge

All commands in this section run on the remote device.

### 1. Create a Working Node Directory

```bash
mkdir -p ~/BlockCap/runtime/hw-node/{data,genesis,client_inbox,static,measurements}

cp runtime/templates/client/client_blockchain_init.py ~/BlockCap/runtime/hw-node/
cp Node_root/acknowledgement.py ~/BlockCap/runtime/hw-node/
cp Node_root/interact.js ~/BlockCap/runtime/hw-node/
cp Node_root/monitor.py ~/BlockCap/runtime/hw-node/
cp Node_root/node_identity.py ~/BlockCap/runtime/hw-node/
cp Node_root/orchestration_service.py ~/BlockCap/runtime/hw-node/
cp Node_root/orchestrator.py ~/BlockCap/runtime/hw-node/
cp Node_root/tef_metrics.py ~/BlockCap/runtime/hw-node/
cp Node_root/prefunded_keys.json ~/BlockCap/runtime/hw-node/
```

### 2. Copy Root Chain Files From the Mac

Copy these files from the Mac to the device:

- `Node_root/genesis/genesis.json` -> `~/BlockCap/runtime/hw-node/genesis/genesis.json`
- `Node_root/data/NodeRegistry.json` -> `~/BlockCap/runtime/hw-node/data/NodeRegistry.json`

Then write the root enode:

```bash
echo "<ROOT_ENODE>" > ~/BlockCap/runtime/hw-node/data/enode.txt
cp ~/BlockCap/runtime/hw-node/data/enode.txt ~/BlockCap/runtime/hw-node/client_inbox/enode.txt
cp ~/BlockCap/runtime/hw-node/data/enode.txt ~/BlockCap/runtime/hw-node/static/enode.txt
```

### 3. Generate Device Keys

```bash
cd ~/BlockCap
./.venv/bin/python runtime/hw-node/client_blockchain_init.py generate_keys
```

### 4. Start the Device API

Replace `<DEVICE_IP>` with the remote device IP.

```bash
cd ~/BlockCap

REAL_INTERACT=1 \
BESU_RPC_URL=http://<DEVICE_IP>:8547 \
./.venv/bin/python runtime/hw-node/orchestration_service.py --host 0.0.0.0 --port 5002 --repo-root runtime/hw-node
```

### 5. Start the Device Besu Node

In a second terminal:

```bash
cd ~/BlockCap

REAL_INTERACT=1 \
BESU_RPC_URL=http://<DEVICE_IP>:8547 \
./.venv/bin/python runtime/hw-node/client_blockchain_init.py start_blockchain_node 30304 8547 <DEVICE_IP>
```

### 6. Build the Registration Payload

For a fog node:

```bash
cd ~/BlockCap

./.venv/bin/python runtime/hw-node/node_identity.py bundle \
  FOG001 Fog1 Fog \
  runtime/hw-node/data/key.pub \
  runtime/hw-node/data/key.priv \
  http://<DEVICE_IP>:8547 \
  true > /tmp/fog1.json
```

For an edge node:

```bash
cd ~/BlockCap

./.venv/bin/python runtime/hw-node/node_identity.py bundle \
  EDGE001 Edge1 Edge \
  runtime/hw-node/data/key.pub \
  runtime/hw-node/data/key.priv \
  http://<DEVICE_IP>:8547 \
  false > /tmp/edge1.json
```

### 7. Register With the Root

Fog:

```bash
curl -X POST http://<MAC_IP>:5600/register-node \
  -H "Content-Type: application/json" \
  -d @/tmp/fog1.json
```

Edge:

```bash
curl -X POST http://<MAC_IP>:5600/register-node \
  -H "Content-Type: application/json" \
  -d @/tmp/edge1.json
```

## Real Deployment: Jetson or Raspberry Pi As Endpoint

All commands in this section run on the endpoint device.

### 1. Create a Working Endpoint Directory

```bash
mkdir -p ~/BlockCap/runtime/hw-endpoint/{data,measurements}

cp runtime/templates/endpoint/end_node_initialization.py ~/BlockCap/runtime/hw-endpoint/
cp Node_root/acknowledgement.py ~/BlockCap/runtime/hw-endpoint/
cp Node_root/interact.js ~/BlockCap/runtime/hw-endpoint/
cp Node_root/monitor.py ~/BlockCap/runtime/hw-endpoint/
cp Node_root/node_identity.py ~/BlockCap/runtime/hw-endpoint/
cp Node_root/orchestration_service.py ~/BlockCap/runtime/hw-endpoint/
cp Node_root/orchestrator.py ~/BlockCap/runtime/hw-endpoint/
cp Node_root/tef_metrics.py ~/BlockCap/runtime/hw-endpoint/
cp Node_root/prefunded_keys.json ~/BlockCap/runtime/hw-endpoint/
cp Node_root/data/NodeRegistry.json ~/BlockCap/runtime/hw-endpoint/data/
```

### 2. Generate Endpoint Keys

```bash
cd ~/BlockCap
./.venv/bin/python runtime/hw-endpoint/end_node_initialization.py generate_keys
```

### 3. Start the Endpoint API

```bash
cd ~/BlockCap

REAL_INTERACT=1 \
BESU_RPC_URL=http://<MAC_IP>:8545 \
./.venv/bin/python runtime/hw-endpoint/orchestration_service.py --host 0.0.0.0 --port 5006 --repo-root runtime/hw-endpoint
```

### 4. Build the Registration Payload

```bash
cd ~/BlockCap

./.venv/bin/python runtime/hw-endpoint/node_identity.py bundle \
  ENDP001 Sensor1 Sensor \
  runtime/hw-endpoint/data/key.pub \
  runtime/hw-endpoint/data/key.priv \
  http://<MAC_IP>:8545 \
  false > /tmp/endpoint1.json
```

### 5. Register With the Root

```bash
curl -X POST http://<MAC_IP>:5600/register-node \
  -H "Content-Type: application/json" \
  -d @/tmp/endpoint1.json
```

## Access Test

Example access request against a fog or edge API:

```bash
curl -X POST http://<NODE_IP>:5002/access \
  -H "Content-Type: application/json" \
  -d '{
    "from_signature": "<FROM_SIG>",
    "to_signature": "<TO_SIG>",
    "method": "GET",
    "resource_path": "/temperature",
    "expiry_secs": 900,
    "allow_delegation": false,
    "delegation_depth": 0
  }'
```

Example grant lookup:

```bash
curl -s "http://<NODE_IP>:5002/grant?from_signature=<FROM_SIG>&to_signature=<TO_SIG>&method=GET&resource_path=/temperature"
```

Example revocation:

```bash
curl -X POST http://<MAC_IP>:5600/revoke-grant \
  -H "Content-Type: application/json" \
  -d '{
    "from_signature": "<FROM_SIG>",
    "to_signature": "<TO_SIG>",
    "policy_id": <POLICY_ID>
  }'
```

## Notes

- `results/` and `runtime/generated/` are generated artifacts and are intentionally excluded from version control.
- The repository was cleaned to keep only the active implementation path and the files needed to run, test, and evaluate the current BlockCap prototype.
- Real deployment is currently command-driven. The local topology runner is best suited for same-machine experiments, while hardware deployment is better done with the explicit commands above.
