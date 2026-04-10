# BlockCap Operations

## Root Node (Cloud)

Build and start the stack:

```bash
docker compose up --build cloud
```

The cloud node exposes:

- API: `http://<root-host>:5600`
- Besu RPC: `http://<root-host>:8545`
- P2P: `30303/tcp`

Useful checks:

```bash
curl -s http://<root-host>:5600/health
curl -s http://<root-host>:5600/spec
curl -s http://<root-host>:5600/admin/nodes/list -H "Authorization: Bearer <ADMIN_TOKEN>"
```

Policy bootstrap:

- set `POLICY_FILE=/path/to/policies.json`
- on startup the root node will attempt to `ensure_policy` for each entry after the contract is ready

## Fog / Edge Nodes

Prerequisites:

- `ROOT_URL` must reach the cloud node API
- the node container or host must persist `data/` and `genesis/`

Runtime behavior:

1. registers with root
2. waits for `/bootstrap-ack`
3. writes `genesis.json`, `NodeRegistry.json`, `prefunded_keys.json`, and `enode`
4. starts Besu
5. starts Gunicorn for the API

Validation:

```bash
curl -s http://<fog-or-edge-host>:5600/health
curl -s http://<root-host>:5600/validators
```

## Endpoint Nodes

Prerequisites:

- `ROOT_URL` points at the cloud node
- `PARENT_URL` points at the fog or edge parent

Runtime behavior:

1. registers with root
2. waits for `NodeRegistry.json`
3. starts the API only
4. forwards `/access`, `/delegate`, and `/grant` upstream when configured with `PARENT_URL`

## blockcap_ctl Reference

List nodes:

```bash
BLOCKCAP_URL=http://localhost:5600 BLOCKCAP_TOKEN=changeme \
./.venv/bin/python scripts/blockcap_ctl.py root nodes list
```

List policies:

```bash
BLOCKCAP_URL=http://localhost:5600 BLOCKCAP_TOKEN=changeme \
./.venv/bin/python scripts/blockcap_ctl.py root policy list
```

Upload policies from file:

```bash
BLOCKCAP_URL=http://localhost:5600 BLOCKCAP_TOKEN=changeme \
./.venv/bin/python scripts/blockcap_ctl.py root policy upload --file policies.json
```

Revoke a grant:

```bash
BLOCKCAP_URL=http://localhost:5600 BLOCKCAP_TOKEN=changeme \
./.venv/bin/python scripts/blockcap_ctl.py root grant revoke --from <sig> --to <sig> --policy-id 7
```

Check node health:

```bash
./.venv/bin/python scripts/blockcap_ctl.py --url http://localhost:5600 node root status
```

Inspect API capabilities:

```bash
./.venv/bin/python scripts/blockcap_ctl.py --url http://localhost:5600 spec
```

## API Summary

| Endpoint | Method | Auth | Roles |
|---|---|---|---|
| `/health` | `GET` | none | cloud, fog, edge, endpoint |
| `/spec` | `GET` | optional admin token | cloud, fog, edge, endpoint |
| `/register-node` | `POST` | none | cloud |
| `/bootstrap-ack` | `POST` | none | fog, edge, endpoint |
| `/access` | `POST` | none | cloud, fog, edge, endpoint |
| `/delegate` | `POST` | none | cloud, fog, edge |
| `/grant` | `GET` | none | cloud, fog, edge, endpoint |
| `/metrics/latency` | `GET` | none | cloud, fog, edge, endpoint |
| `/admin/*` | mixed | bearer token | cloud |

## Experiment Workflow

1. Start root and topology from `/topology` or `scripts/run_topology.py`
2. Upload or auto-load policies
3. Run:

```bash
./.venv/bin/python scripts/load_test.py --host http://localhost:5600 --operation access --concurrency 50 --total-requests 500
```

4. Review:

- `/results`
- `/live-dashboard`
- `/apidocs`

## Troubleshooting

Node stuck waiting for bootstrap:

- verify `ROOT_URL`
- check `/bootstrap-ack` response on the non-root node
- confirm `genesis.json` and `NodeRegistry.json` exist in the mounted data paths

Besu genesis mismatch:

- stop the node
- clear the node’s `data/database` and `data/caches`
- ensure the current `genesis.json` came from the root bootstrap flow

Unexpected access denial:

- check `/grant?from_signature=...&to_signature=...`
- check `/expiry-check`
- list policies with `/admin/policy/list`

Container restarts losing state:

- mount persistent volumes for `data/` and `genesis/`
- avoid running non-root nodes without persistent storage mounts
