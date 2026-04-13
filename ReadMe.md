# BlockCap

BlockCap is a permissioned IoT authorization prototype built on Hyperledger Besu, Solidity, and a Python/Flask control plane.

## Quickstart

Build and run the container stack:

```bash
docker compose up --build
```

The root node is exposed at `http://127.0.0.1:5600/`.

Useful pages:

- `http://127.0.0.1:5600/topology`
- `http://127.0.0.1:5600/control`
- `http://127.0.0.1:5600/results`
- `http://127.0.0.1:5600/live-dashboard`
- `http://127.0.0.1:5600/apidocs`

## Local Development

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install
```

Run the web operator locally:

```bash
./.venv/bin/python Node_root/orchestration_service.py --host 127.0.0.1 --port 5600 --repo-root "$(pwd)/Node_root"
```

Run a generated local topology:

```bash
./.venv/bin/python scripts/run_topology.py --cloud 1 --fog 2 --edge 2 --endpoint 3 --scenario demo-1
```

Run the operator CLI:

```bash
BLOCKCAP_URL=http://localhost:5600 BLOCKCAP_TOKEN=changeme \
./.venv/bin/python scripts/blockcap_ctl.py root nodes list
```

## Hardware Deployment

For real hardware:

- keep the `Cloud/Root` node on the MacBook or another server-class host
- set `ROOT_URL` on every non-root node
- set `PARENT_URL` on endpoint nodes so access requests forward to their fog or edge parent
- run the same container image on each device

Example:

```bash
docker run --rm \
  -e NODE_ROLE=fog \
  -e ROOT_URL=http://<root-ip>:5600 \
  -p 5600:5600 \
  blockcap-node
```

## Tests

Python:

```bash
./.venv/bin/python -m pytest Node_root/ scripts/ -q
```

Solidity:

```bash
cd node-registry-test && npx hardhat test
```

## Operations Guide

Full deployment, policy, API, CLI, experiment, and troubleshooting documentation is in [OPERATIONS.md](./OPERATIONS.md).

## Experiment Reproduction (ACM Results)

Reset local runtime state:

```bash
rm -rf "$(pwd)/runtime/generated/acm-results"
pkill -f "besu" ; pkill -f "BlockCap" ; pkill -f "orchestration_service" ; pkill -f "run_topology"
pkill -9 -f "besu" ; pkill -9 -f "BlockCap"
```

Generate topology and run experiments:

```bash
./.venv/bin/python scripts/run_topology.py \
  --mode local \
  --runtime-backend native \
  --cloud 1 --fog 1 --edge 1 --endpoint 1 \
  --scenario acm-results

./.venv/bin/python scripts/run_all_experiments.py \
  --scenario-file runtime/generated/acm-results/topology.json \
  --runs 1
```

Build gas and reports:

```bash
node scripts/measure_gas.js
./.venv/bin/python scripts/build_gas_comparison.py
./.venv/bin/python scripts/generate_matplotlib_report.py
```

Baseline curation rules:

- Strict gas comparisons only: `experiment_baselines/gas_baselines.json`
- Non-gas related-work metrics (latency/throughput/energy): `experiment_baselines/literature_metrics.json`