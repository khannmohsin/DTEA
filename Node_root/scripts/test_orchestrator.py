# tests/test_orchestrator.py
import json
import os
import time
import pytest
from pathlib import Path
from unittest.mock import patch
# Make repo importable
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import orchestrator as orch_mod
from orchestrator import Orchestrator

# ---------------------------
# Fake subprocess.run helpers
# ---------------------------

IS_REAL = bool(os.getenv("REAL_INTERACT"))

class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

def make_router(state):
    """
    Returns a function to mock subprocess.run for: node interact.js <cmd> <args...>
    """
    OP = {"READ":1<<0, "WRITE":1<<1, "UPDATE":1<<2, "REMOVE":1<<3}

    def _now():
        return int(time.time())

    def _run(argv, capture_output=True, text=True, env=None):
        if len(argv) < 3:
            return FakeCompleted("", "bad argv", 1)

        cmd = argv[2]
        args = argv[3:]

        # ---- Deployment / misc ----
        if cmd == "checkIfDeployed":
            return FakeCompleted("true\n" if state["deployed"] else "false\n")

        if cmd == "peerCount":
            return FakeCompleted(f"{state['peerCount']}\n")

        if cmd in ("qbft_getValidators", "getValidatorsByBlockNumber"):
            # Return a simple lowercase string list for the orchestrator to "in" check
            return FakeCompleted("[" + ",".join(state["validators"]) + "]\n")

        # ---- Multisig info / approval ----
        if cmd == "msigInfo":
            payload = {
                "msigRequired": state["msigRequired"],
                "msigApproverCount": state["msigApproverCount"],
                "msigThreshold": state["msigThreshold"],
                "defaultFrom": {"index": 0, "account": state["registrar_addr"]},
            }
            return FakeCompleted(json.dumps(payload) + "\n")

        if cmd == "approveCreatePolicy":
            return FakeCompleted("✅ approveCreatePolicy: 0xabc\n")

        # ---- Policies ----
        if cmd == "createPolicy":
            from_role_name, to_role_name, ops_csv, ctx_schema = args
            policy_id = state["nextPolicyId"]
            role_to_num = {"Unknown": 0, "Cloud": 1, "Fog": 2, "Edge": 3, "Sensor": 4, "Actuator": 5}
            state["policies"][policy_id] = {
                "fromRole": role_to_num.get(from_role_name, 0),
                "toRole": role_to_num.get(to_role_name, 0),
                "opsAllowed": OP.get(ops_csv, 0),
                "isDeprecated": False,
                "ctxSchema": orch_mod._ctx_hash(ctx_schema),
                "policyHash": "0x" + "11" * 32,
                "version": 1,
            }
            state["nextPolicyId"] += 1
            return FakeCompleted("✅ createPolicy: 0xdead\n")

        if cmd == "nextPolicyId":
            return FakeCompleted(f"{state['nextPolicyId']}\n")

        if cmd == "getPolicy":
            pid = int(args[0])
            p = state["policies"].get(pid, {
                "fromRole":"0","toRole":"0","opsAllowed":"0",
                "isDeprecated": False, "ctxSchema":"0x"+"00"*32,
                "policyHash":"0x"+"00"*32, "version":"0",
            })
            return FakeCompleted(json.dumps(p) + "\n")

        # ---- Nodes / Registration ----
        if cmd == "isNodeRegistered":
            sig = args[0]
            return FakeCompleted("true\n" if sig in state["registered_sigs"] else "false\n")

        if cmd == "registerNode":
            if not state["allow_register_tx"]:
                return FakeCompleted("", "revert", 1)
            sig = args[-1]
            state["registered_sigs"].add(sig)
            node_id, node_name, node_type_str, public_key, reg_addr, rpc_url, reg_by_type, node_sig = args
            state["node_types"][node_sig] = node_type_str
            state["node_addrs"][node_sig] = state["last_address"]
            return FakeCompleted("✅ registerNodePacked: 0xfeed\n")

        if cmd in ("getNodeBySig", "getNodeDetails", "getNodeDetailsBySignature"):
            node_sig = args[0]
            node_type_str = state["node_types"].get(node_sig, "Edge")
            role_to_num = {"Unknown":0,"Cloud":1,"Fog":2,"Edge":3,"Sensor":4,"Actuator":5}
            details = {
                "nodeId": "N/A",
                "nodeName": "N/A",
                "nodeType": role_to_num.get(node_type_str, 0),
                "publicKey": "0xabc",
                "isRegistered": node_sig in state["registered_sigs"],
                "registeredBy": state["registrar_addr"],
                "nodeSignature": node_sig,
                "registeredByNodeType": 1,
            }
            return FakeCompleted(json.dumps(details) + "\n")

        if cmd == "proposeValidator":
            addr = args[0].lower()
            if addr not in state["validators"]:
                state["validators"].append(addr)
            return FakeCompleted("✅ proposeValidator: 0xbeef\n")

        if cmd == "isValidator":
            node_sig = args[0]
            addr = state["node_addrs"].get(node_sig, "").lower()
            return FakeCompleted("true\n" if addr in state["validators"] else "false\n")

        # ---- Grants / Delegation ----
        if cmd == "issueGrant":
            from_sig, to_sig, policy_id, ops_csv, exp_str = args
            exp = int(exp_str)
            grant_key = (from_sig, to_sig)
            state["grants"][grant_key] = {
                "policyId": int(policy_id),
                "ops": set([x.strip() for x in ops_csv.split(",") if x.strip()]),
                "issuedAt": _now(),
                "expiresAt": exp,
                "isIssued": True,
                "isRevoked": False,
                "delegationAllowed": False,
                "delegationDepth": 0,
            }
            return FakeCompleted("✅ issueGrant: 0xaaa\n")

        if cmd == "issueGrantDelegable":
            from_sig, to_sig, policy_id, ops_csv, exp_str, allow_str, depth_str = args
            exp = int(exp_str); depth = int(depth_str)
            allow = (allow_str.lower() == "true")
            grant_key = (from_sig, to_sig)
            state["grants"][grant_key] = {
                "policyId": int(policy_id),
                "ops": set([x.strip() for x in ops_csv.split(",") if x.strip()]),
                "issuedAt": _now(),
                "expiresAt": exp,
                "isIssued": True,
                "isRevoked": False,
                "delegationAllowed": allow,
                "delegationDepth": depth,
            }
            return FakeCompleted("✅ issueGrantDelegable: 0xaab\n")

        if cmd == "getGrantEx":
            from_sig, to_sig = args
            g = state["grants"].get((from_sig, to_sig))
            if not g:
                resp = {
                    "0": 0, "1": 0, "2": 0, "3": 0,
                    "4": False, "5": False, "6": False, "7": 0,
                    "policyId": 0, "opsSubset": 0, "issuedAt": 0, "expiresAt": 0,
                    "isIssued": False, "isRevoked": False,
                    "delegationAllowed": False, "delegationDepth": 0
                }
                return FakeCompleted(json.dumps(resp)+"\n")
            resp = {
                "policyId": g["policyId"],
                "opsSubset": 0,
                "issuedAt": g["issuedAt"],
                "expiresAt": g["expiresAt"],
                "isIssued": g["isIssued"],
                "isRevoked": g["isRevoked"],
                "delegationAllowed": g["delegationAllowed"],
                "delegationDepth": g["delegationDepth"],
            }
            return FakeCompleted(json.dumps(resp)+"\n")

        if cmd == "checkGrant":
            from_sig, to_sig, op_csv = args
            g = state["grants"].get((from_sig, to_sig))
            if not g or g["isRevoked"]:
                return FakeCompleted("false\n")
            if g["expiresAt"] <= _now():
                return FakeCompleted("false\n")
            ok = op_csv in g["ops"]
            return FakeCompleted(("true\n" if ok else "false\n"))

        if cmd == "delegateGrant":
            current_from, to_sig, new_from, ops_csv, exp_str = args
            parent = state["grants"].get((current_from, to_sig))
            exp = int(exp_str)
            if (not parent) or (not parent["delegationAllowed"]) or parent["delegationDepth"] <= 0:
                return FakeCompleted("", "revert", 1)
            if exp >= parent["expiresAt"]:
                return FakeCompleted("", "revert", 1)
            state["grants"][(new_from, to_sig)] = {
                "policyId": parent["policyId"],
                "ops": set([x.strip() for x in ops_csv.split(",") if x.strip()]),
                "issuedAt": _now(),
                "expiresAt": exp,
                "isIssued": True,
                "isRevoked": False,
                "delegationAllowed": True if parent["delegationDepth"]-1 > 0 else False,
                "delegationDepth": max(0, parent["delegationDepth"]-1),
            }
            return FakeCompleted("✅ delegateGrant: 0xdd\n")

        if cmd == "revokeGrant":
            from_sig, to_sig = args
            g = state["grants"].get((from_sig, to_sig))
            if g:
                g["isRevoked"] = True
            return FakeCompleted("✅ revokeGrant: 0xcc\n")

        return FakeCompleted("", f"unknown cmd: {cmd}", 1)

    return _run

# ---------------------------
# Fixtures
# ---------------------------

@pytest.fixture
def tmp_policy_index_file(tmp_path, monkeypatch):
    path = tmp_path / "policy_index.json"
    monkeypatch.setattr(orch_mod, "POLICY_INDEX_FILE", str(path))
    return str(path)

@pytest.fixture
def state(tmp_policy_index_file):
    return {
        "deployed": True,
        "peerCount": 1,
        "msigRequired": False,
        "msigApproverCount": 2,
        "msigThreshold": 2,
        "registrar_addr": "0x1111111111111111111111111111111111111111",
        "nextPolicyId": 1,
        "policies": {},
        "registered_sigs": set(),
        "node_types": {},   # sig -> role name
        "node_addrs": {},   # sig -> address
        "validators": [],   # list of addresses (lowercase)
        "grants": {},       # (from,to) -> grant dict
        "allow_register_tx": True,
        "last_address": "0x2222222222222222222222222222222222222222",
    }

@pytest.fixture
def orch(monkeypatch, state, tmp_policy_index_file):
    # keep sleep short for tests
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    if not IS_REAL:
        monkeypatch.setattr(orch_mod.subprocess, "run", make_router(state))

    o = Orchestrator(enforce_signature=not IS_REAL)
    o.registrar_addr = state["registrar_addr"]
    return o

# ---------------------------
# Helpers for REAL mode
# ---------------------------

def _ensure_registered(orch, sig, role, addr="0x" + "3"*40):
    """REAL mode: register on-chain via orchestrator; Mock mode: router handles it."""
    payload = {
        "node_id": f"id-{sig}",
        "node_name": f"{role}-node",
        "node_type": role,
        "public_key": "0xabc",
        "address": addr,
        "rpcURL": "http://dummy",
        "signature": sig,
    }
    out = orch.registration_flow(payload)
    assert out["ok"], f"registration failed for {sig}: {out}"

def _register_pair(state, orch, from_sig, from_role, to_sig, to_role):
    if IS_REAL:
        _ensure_registered(orch, from_sig, from_role)
        _ensure_registered(orch, to_sig, to_role)
    else:
        state["registered_sigs"].update([from_sig, to_sig])
        state["node_types"][from_sig] = from_role
        state["node_types"][to_sig] = to_role
        state["node_addrs"][from_sig] = "0xaaa"
        state["node_addrs"][to_sig] = "0xbbb"

# ---------------------------
# Tests: Registration flow
# ---------------------------

def test_registration_non_endpoint(orch, state):
    payload = {
        "node_id": "ED-1",
        "node_name": "Edge-1",
        "node_type": "Edge",
        "public_key": "0xabc",
        "address": state["last_address"],
        "rpcURL": "http://x",
        "signature": "sig-edge-1",
    }
    out = orch.registration_flow(payload)
    assert out["ok"] is True
    status = out.get("status")
    assert status in ("registered", "already_registered", "validator_proposed", "validator_included")
    assert out["ack_sent"] is True
    assert "tx" in out or status != "registered"
    if not IS_REAL:
        assert "sig-edge-1" in state["registered_sigs"]

def test_registration_endpoint(orch, state):
    payload = {
        "node_id": "S-1",
        "node_name": "Sensor-1",
        "node_type": "Sensor",
        "public_key": "0xabc",
        "address": state["last_address"],
        "rpcURL": "http://x",
        "signature": "sig-sensor-1",
    }
    out = orch.registration_flow(payload)
    assert out["ok"] is True
    assert out.get("status") in ("endpoint_registered", "already_registered")
    assert out["ack_sent"] is False

def test_registration_validator_included(orch, state):
    payload = {
        "node_id": "FG-1",
        "node_name": "Fog-1",
        "node_type": "Fog",
        "public_key": "0xabc",
        "address": "0x3333333333333333333333333333333333333333",
        "rpcURL": "http://x",
        "signature": "sig-fog-1",
        "wants_validator": True,
    }
    state["last_address"] = payload["address"]
    out = orch.registration_flow(payload)
    assert out["ok"] is True
    assert out["status"] in ("validator_included", "validator_proposed")
    if IS_REAL:
        vlist = (orch.qbft_get_validators() or "").lower()
        assert payload["address"].lower() in vlist
    else:
        assert payload["address"].lower() in state["validators"]


def test_registration_fog_without_validator_request_stays_registered(orch, state):
    payload = {
        "node_id": "FG-2",
        "node_name": "Fog-2",
        "node_type": "Fog",
        "public_key": "0xabc",
        "address": "0x4444444444444444444444444444444444444444",
        "rpcURL": "http://x",
        "signature": "sig-fog-2",
        "wants_validator": False,
    }
    state["last_address"] = payload["address"]
    out = orch.registration_flow(payload)
    assert out["ok"] is True
    assert out["status"] == "registered"

# ---------------------------
# Tests: Access flow
# ---------------------------

def test_access_flow_creates_policy_and_grant(orch, state):
    from_sig, to_sig = "sig-edge-A", "sig-fog-B"
    _register_pair(state, orch, from_sig, "Edge", to_sig, "Fog")

    out = orch.access_flow(from_sig, to_sig, "GET", "/sensors/1")
    assert out["ok"] is True
    assert out["granted"] is True
    assert out["op"] == "READ"
    assert out["policyId"] is not None
    if IS_REAL:
        gx = orch.get_grant_ex(from_sig, to_sig)
        assert gx.get("isIssued") and not gx.get("isRevoked")
    else:
        g = state["grants"][(from_sig, to_sig)]
        assert "READ" in g["ops"]

def test_access_flow_reuses_valid_grant(orch, state, monkeypatch):
    from_sig, to_sig = "sig-edge-C", "sig-fog-D"
    _register_pair(state, orch, from_sig, "Edge", to_sig, "Fog")
    if IS_REAL:
        _seed_grant(orch, from_sig, to_sig, op="READ", expires_secs=600, delegable=False, depth=0)
    else:
        now = int(time.time())
        state["grants"][(from_sig, to_sig)] = {
            "policyId": 99, "ops": {"READ"}, "issuedAt": now-5,
            "expiresAt": now+600, "isIssued": True, "isRevoked": False,
            "delegationAllowed": False, "delegationDepth": 0,
        }
    out = orch.access_flow(from_sig, to_sig, "GET", "/sensors/2")
    assert out["ok"] is True
    assert out["granted"] is True

def test_access_flow_pending_msig(orch, state, monkeypatch):
    if IS_REAL:
        pytest.skip("msig mode cannot be toggled in REAL mode; run mock mode for this test")
    from_sig, to_sig = "sig-edge-E", "sig-fog-F"
    _register_pair(state, orch, from_sig, "Edge", to_sig, "Fog")
    state["msigRequired"] = True
    out = orch.access_flow(from_sig, to_sig, "POST", "/actuators/1")
    assert out["ok"] is False
    assert out["why"] == "policy_pending_multisig"
    state["msigRequired"] = False

def test_access_flow_unknown_method(orch, state):
    from_sig, to_sig = "sig-edge-G", "sig-fog-H"
    _register_pair(state, orch, from_sig, "Edge", to_sig, "Fog")
    out = orch.access_flow(from_sig, to_sig, "TRACE", "/x")
    assert out["ok"] is False
    assert out["why"].startswith("unsupported_method")

# ---------------------------
# Tests: Delegation flow
# ---------------------------

def test_delegate_flow_success(orch, state):
    parent_from, to_sig, child_from = "sig-edge-P", "sig-fog-Q", "sig-edge-R"
    _register_pair(state, orch, parent_from, "Edge", to_sig, "Fog")
    _register_pair(state, orch, child_from, "Edge", to_sig, "Fog")
    if IS_REAL:
        _seed_grant(orch, parent_from, to_sig, op="READ", expires_secs=900, delegable=True, depth=2)
    else:
        now = int(time.time())
        state["grants"][(parent_from, to_sig)] = {
            "policyId": 7, "ops": {"READ"}, "issuedAt": now-5,
            "expiresAt": now+900, "isIssued": True, "isRevoked": False,
            "delegationAllowed": True, "delegationDepth": 2,
        }
    out = orch.delegate_flow(parent_from, to_sig, child_from, "READ", child_expiry_secs=300)
    assert out["ok"] is True
    assert out["granted"] is True
    if IS_REAL:
        gx = orch.get_grant_ex(child_from, to_sig)
        assert gx.get("isIssued") and gx.get("expiresAt") > int(time.time())
    else:
        child_g = state["grants"][(child_from, to_sig)]
        assert "READ" in child_g["ops"]
        assert child_g["delegationDepth"] == 1

def test_delegate_flow_rejects_when_not_allowed(orch, state):
    if IS_REAL:
        pytest.skip("Router-based negative case; skip in REAL mode")
    parent_from, to_sig, child_from = "sig-edge-X", "sig-fog-Y", "sig-edge-Z"
    _register_pair(state, orch, parent_from, "Edge", to_sig, "Fog")
    _register_pair(state, orch, child_from, "Edge", to_sig, "Fog")
    now = int(time.time())
    state["grants"][(parent_from, to_sig)] = {
        "policyId": 7, "ops": {"READ"}, "issuedAt": now-5,
        "expiresAt": now+900, "isIssued": True, "isRevoked": False,
        "delegationAllowed": False, "delegationDepth": 0,
    }
    out = orch.delegate_flow(parent_from, to_sig, child_from, "READ", child_expiry_secs=300)
    assert out["ok"] is False
    assert out["why"] in ("delegation_not_allowed", "delegation_depth_exhausted")

def test_delegate_flow_rejects_on_expired_parent(orch, state):
    if IS_REAL:
        pytest.skip("Router-based expiry simulation; skip in REAL mode")
    parent_from, to_sig, child_from = "sig-edge-M1", "sig-fog-M2", "sig-edge-M3"
    _register_pair(state, orch, parent_from, "Edge", to_sig, "Fog")
    _register_pair(state, orch, child_from, "Edge", to_sig, "Fog")
    now = int(time.time())
    state["grants"][(parent_from, to_sig)] = {
        "policyId": 7, "ops": {"READ"}, "issuedAt": now-900,
        "expiresAt": now-10, "isIssued": True, "isRevoked": False,
        "delegationAllowed": True, "delegationDepth": 2,
    }
    out = orch.delegate_flow(parent_from, to_sig, child_from, "READ", child_expiry_secs=300)
    assert out["ok"] is False
    assert out["why"] == "parent_expired"

# --- Access denials ---

def test_access_denied_when_from_not_registered(orch, state):
    from_sig, to_sig = "sig-new-A", "sig-fog-existing"
    if IS_REAL:
        _ensure_registered(orch, to_sig, "Fog")
    else:
        state["registered_sigs"].add(to_sig)
        state["node_types"][to_sig] = "Fog"
    out = orch.access_flow(from_sig, to_sig, "GET", "/r")
    assert out["ok"] is False and out["why"] == "from_not_registered"

def test_access_denied_when_to_not_registered(orch, state):
    from_sig, to_sig = "sig-edge-existing", "sig-new-B"
    if IS_REAL:
        _ensure_registered(orch, from_sig, "Edge")
    else:
        state["registered_sigs"].add(from_sig)
        state["node_types"][from_sig] = "Edge"
    out = orch.access_flow(from_sig, to_sig, "GET", "/r")
    assert out["ok"] is False and out["why"] == "to_not_registered"

def test_resource_key_normalization(orch, state):
    from_sig, to_sig = "sig-e1", "sig-f1"
    if IS_REAL:
        _ensure_registered(orch, from_sig, "Edge")
        _ensure_registered(orch, to_sig, "Fog")
    else:
        state["registered_sigs"].update([from_sig, to_sig])
        state["node_types"][from_sig] = "Edge"
        state["node_types"][to_sig] = "Fog"
    out = orch.access_flow(from_sig, to_sig, "GET", "metrics")
    assert out["ok"] is True and out["granted"] is True

def test_policy_id_unknown_when_nextPolicyId_fails(orch, state, monkeypatch):
    if IS_REAL:
        pytest.skip("nextPolicyId failure is a router-only branch; skip in REAL mode")
    def failing_router(argv, capture_output=True, text=True, env=None):
        base = make_router(state)
        if len(argv) >= 3 and argv[2] == "nextPolicyId":
            return FakeCompleted("", "boom", 1)
        return base(argv, capture_output, text, env)
    monkeypatch.setattr(orch_mod.subprocess, "run", failing_router)

    o = Orchestrator()
    o.registrar_addr = state["registrar_addr"]
    state["registered_sigs"].update(["sf", "st"])
    state["node_types"]["sf"] = "Edge"
    state["node_types"]["st"] = "Fog"

    out = o.access_flow("sf", "st", "GET", "/x")
    assert out["ok"] is False and out["why"] == "policy_id_unknown"

def test_ensure_policy_create_if_missing_false(orch, state, tmp_policy_index_file):
    res = orch.ensure_policy("Edge", "Fog", "READ", "api:GET:/x", create_if_missing=False)
    assert res["status"] == "missing" and res["policyId"] is None

def test_grant_revoked_then_reissued(orch, state):
    f, t = "sig-a1", "sig-b1"
    if IS_REAL:
        _ensure_registered(orch, f, "Edge")
        _ensure_registered(orch, t, "Fog")
    else:
        state["registered_sigs"].update([f, t])
        state["node_types"][f] = "Edge"; state["node_types"][t] = "Fog"

    out1 = orch.access_flow(f, t, "GET", "/r1")
    assert out1["ok"] and out1["granted"]

    if IS_REAL:
        orch.revoke_grant(f, t)
    else:
        g = state["grants"][(f, t)]; g["isRevoked"] = True

    out2 = orch.access_flow(f, t, "GET", "/r1")
    assert out2["ok"] and out2["granted"]

def test_delegate_child_expiry_not_shorter_is_rejected(orch, state):
    if IS_REAL:
        pytest.skip("Router-based exact-expiry rejection; skip in REAL mode")
    parent_from, to_sig, child_from = "pf1","tf1","cf1"
    for s in (parent_from, to_sig, child_from):
        state["registered_sigs"].add(s); state["node_types"][s] = "Edge" if s!=to_sig else "Fog"
    now = int(time.time())
    state["grants"][(parent_from, to_sig)] = {
        "policyId": 1, "ops": {"READ"}, "issuedAt": now-1,
        "expiresAt": now+300, "isIssued": True, "isRevoked": False,
        "delegationAllowed": True, "delegationDepth": 1,
    }
    orig_router = make_router(state)
    def strict_delegate(argv, capture_output=True, text=True, env=None):
        if len(argv) >= 3 and argv[2] == "delegateGrant":
            exp = int(argv[6])
            parent = state["grants"][(parent_from, to_sig)]
            if exp >= parent["expiresAt"]:
                return FakeCompleted("", "revert", 1)
        return orig_router(argv, capture_output, text, env)
    with patch.object(orch_mod.subprocess, "run", strict_delegate):
        res = orch.delegate_flow(parent_from, to_sig, child_from, "READ", child_expiry_secs=10**6)
        assert res["ok"] is False

def test_delegate_ops_not_subset_rejected(orch, state):
    if IS_REAL:
        pytest.skip("Router-based op-subset enforcement; skip in REAL mode")
    parent_from, to_sig, child_from = "pf2","tf2","cf2"
    for s in (parent_from, to_sig, child_from):
        state["registered_sigs"].add(s); state["node_types"][s] = "Edge" if s!=to_sig else "Fog"
    now = int(time.time())
    state["grants"][(parent_from, to_sig)] = {
        "policyId": 2, "ops": {"READ"}, "issuedAt": now-1,
        "expiresAt": now+600, "isIssued": True, "isRevoked": False,
        "delegationAllowed": True, "delegationDepth": 2,
    }
    base = make_router(state)
    def subset_router(argv, capture_output=True, text=True, env=None):
        if len(argv) >= 3 and argv[2] == "delegateGrant":
            ops_csv = argv[6]
            if ops_csv.strip() != "READ":
                return FakeCompleted("", "revert", 1)
        return base(argv, capture_output, text, env)
    with patch.object(orch_mod.subprocess, "run", subset_router):
        res = orch.delegate_flow(parent_from, to_sig, child_from, "WRITE", child_expiry_secs=100)
        assert res["ok"] is False and "delegate_reverted" in res["why"]

def test_msig_mode_flap_safe(orch, state, monkeypatch):
    if IS_REAL:
        pytest.skip("msig flap is simulated via router; skip in REAL mode")
    from_sig, to_sig = "sig-u1", "sig-u2"
    state["registered_sigs"].update([from_sig, to_sig])
    state["node_types"][from_sig] = "Edge"; state["node_types"][to_sig] = "Fog"

    state["msigRequired"] = True
    out_pending = orch.access_flow(from_sig, to_sig, "POST", "/w")
    assert out_pending["ok"] is False and out_pending["why"] == "policy_pending_multisig"

    state["msigRequired"] = False
    out_ok = orch.access_flow(from_sig, to_sig, "POST", "/w")
    assert out_ok["ok"] and out_ok["granted"]

def test_policy_cache_hit(orch, state, tmp_policy_index_file):
    f, t = "sig-c1","sig-c2"
    if IS_REAL:
        _ensure_registered(orch, f, "Edge")
        _ensure_registered(orch, t, "Fog")
        r1 = orch.access_flow(f, t, "GET", "/cache1")
        assert r1["ok"] and r1["policyId"] is not None
        pid1 = r1["policyId"]
        r2 = orch.access_flow(f, t, "GET", "/cache1")
        assert r2["ok"] and r2["policyId"] == pid1
    else:
        state["registered_sigs"].update([f, t])
        state["node_types"][f] = "Edge"; state["node_types"][t] = "Fog"
        r1 = orch.access_flow(f, t, "GET", "/cache1")
        assert r1["ok"] and r1["policyId"] is not None
        before = state["nextPolicyId"]
        r2 = orch.access_flow(f, t, "GET", "/cache1")
        assert r2["ok"] and state["nextPolicyId"] == before

def test_verify_signature_failure_is_respected(orch, state, monkeypatch):
    if IS_REAL:
        pytest.skip("enforce_signature is disabled in REAL mode fixture")
    monkeypatch.setattr(orch_mod.Orchestrator, "verify_signature", lambda *_: False)
    payload = {
        "node_id": "X", "node_name": "X", "node_type": "Edge",
        "public_key": "0x", "address": "0x", "rpcURL": "http://x", "signature": "sigX"
    }
    out = orch.registration_flow(payload)
    assert out["ok"] is False and out["why"] == "signature_verification_failed"

def test_is_grant_expired_wrapper(orch, state):
    if IS_REAL:
        pytest.skip("expiry path is simulated via router; skip in REAL mode")
    f, t = "sig-e-exp", "sig-f-exp"
    state["registered_sigs"].update([f, t])
    state["node_types"][f] = "Edge"; state["node_types"][t] = "Fog"
    now = int(time.time())
    state["grants"][(f, t)] = {
        "policyId": 9, "ops": {"READ"}, "issuedAt": now-10,
        "expiresAt": now-1, "isIssued": True, "isRevoked": False,
        "delegationAllowed": False, "delegationDepth": 0,
    }
    assert orch.is_grant_expired(f, t) is True


def test_access_flow_emits_correlated_process_events(orch, state):
    from_sig, to_sig = "sig-evt-a", "sig-evt-b"
    _register_pair(state, orch, from_sig, "Edge", to_sig, "Fog")

    orch.start_flow(
        "access",
        stage="request_received",
        message="Access request received",
        component="api",
        from_signature=from_sig,
        to_signature=to_sig,
    )
    out = orch.access_flow(from_sig, to_sig, "GET", "/timeline")

    assert out["ok"] is True
    events = orch.recent_events(limit=20)
    stages = [event["stage"] for event in events]
    flow_ids = {event["flow_id"] for event in events}
    assert "registration_validation" in stages
    assert "role_resolution" in stages
    assert "grant_check" in stages
    assert len(flow_ids) == 1


def test_registration_flow_emits_signature_and_status_events(orch, state):
    payload = {
        "node_id": "EVT-REG-1",
        "node_name": "EdgeReg",
        "node_type": "Edge",
        "public_key": "0xabc",
        "address": state["last_address"],
        "rpcURL": "http://x",
        "signature": "sig-reg-events",
    }
    orch.start_flow(
        "registration",
        stage="request_received",
        message="Registration request received",
        component="api",
        from_signature=payload["signature"],
    )
    out = orch.registration_flow(payload)

    assert out["ok"] is True
    stages = [event["stage"] for event in orch.recent_events(limit=20)]
    assert "signature_verification" in stages
    assert "already_registered_check" in stages
    assert "registration_submit" in stages

# ------------- helpers -------------

def _seed_grant(orch, from_sig, to_sig, op="READ", expires_secs=600, delegable=False, depth=0):
    f = orch.get_node_by_sig(from_sig)
    t = orch.get_node_by_sig(to_sig)
    from_role = orch._role_name(f["nodeType"])
    to_role   = orch._role_name(t["nodeType"])
    ctx = f"api:{'GET' if op=='READ' else 'POST'}:/seed"
    res = orch.ensure_policy(from_role, to_role, op, ctx, create_if_missing=True)
    assert res["status"] in ("exists","created"), f"policy not ready: {res}"
    pid = res["policyId"]
    assert pid is not None, "policy_id_unknown in real mode"
    exp_at = int(time.time()) + int(expires_secs)
    if delegable and depth > 0:
        orch.issue_grant_delegable(from_sig, to_sig, pid, op, exp_at, True, depth)
    else:
        orch.issue_grant(from_sig, to_sig, pid, op, exp_at)
