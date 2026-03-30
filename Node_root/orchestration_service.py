#!/usr/bin/env python3
"""
Flask service that exposes orchestration endpoints backed by orchestrator.py.
- Registration flow that can include validator proposal/inclusion.
- Access flow that creates resource-scoped policies and issues (or reuses) grants.
- Delegation flow for parent->child grant delegation.
- Helpful read-only endpoints (node details, grant info, validators, health).

Environment:
  REAL_INTERACT=1   -> talk to real interact.js and chain (default: mock in tests)
  FROM_IDX=0        -> signer index interact.js should use (default: 0)
  ORCH_TRACE=1      -> log underlying node commands for debugging

Run:
  python orchestration_service.py --host 0.0.0.0 --port 8080
"""

import os
import sys
import traceback
from typing import Any, Dict
import base64
import json


from flask import Flask, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix
from pathlib import Path
# import orchestrator module you already have
from orchestrator import Orchestrator, METHOD_TO_OP
from tef_metrics import TokenBucketRateLimiter

# Optional perf decorator (fallback to no-op if absent)
try:
    from monitor import track_performance
except Exception:
    def track_performance(fn):
        return fn


# ------------ helpers ------------

def ok(data: Dict[str, Any], code: int = 200):
    return jsonify({"ok": True, **data}), code

def err(message: str, code: int = 400, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra or {})
    return jsonify(payload), code

def require_json(keys):
    """Simple required-keys validator. Returns (json, error_response_or_None)."""
    if not request.is_json:
        return None, err("expected application/json body", 415)
    try:
        data = request.get_json(force=True, silent=False)
    except Exception:
        return None, err("malformed JSON", 400)
    missing = [k for k in keys if k not in data or data[k] in (None, "")]
    if missing:
        return None, err(f"missing required field: {', '.join(missing)}", 422)
    return data, None

def _b64read(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    return base64.b64encode(p.read_bytes()).decode("ascii")

def _local_signature_from_node_details(repo_root: str | None) -> str | None:
    # tries Node_root/node-details.json by default; falls back to repo_root
    try:
        # common locations
        candidates = [
            Path("node-details.json"),
            Path(repo_root or ".") / "node-details.json",
            Path(repo_root or ".") / "Node_root" / "node-details.json",
        ]
        for p in candidates:
            if p.exists():
                with p.open("r") as f:
                    data = json.load(f)
                sig = data.get("signature")
                if isinstance(sig, str) and len(sig) > 0:
                    return sig
    except Exception:
        pass
    return None

def make_app(repo_root: str | None = None) -> Flask:
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # if behind a proxy

    # Construct orchestrator. In REAL mode, signature enforcement can be toggled.
    enforce_signature = os.getenv("ORCH_ENFORCE_SIG", "1") != "0"
    orch = Orchestrator(repo_root=repo_root, enforce_signature=enforce_signature)
    # If you prefer a fixed registrar, set it here (else orchestrator uses prefunded_keys.json[0])
    # orch.registrar_addr = "0x1111..."
    local_sig = _local_signature_from_node_details(repo_root)
    rate_limiter = TokenBucketRateLimiter({
        "cloud": 200.0,
        "fog": 100.0,
        "edge": 50.0,
        "sensor": 10.0,
        "actuator": 10.0,
        "endpoint": 10.0,
        "unknown": 10.0,
    })
    # Ensure validator auto‑voter runs even after restarts
    try:
        if local_sig and orch.is_validator():
            orch.start_validator_listener()   # idempotent
    except Exception as _e:
        print(f"[listener] startup check skipped: {_e}")

    @app.before_request
    def _start_request_timer():
        orch.begin_request(
            node_tier=orch.local_node_tier,
            condition=request.headers.get("X-Latency-Condition")
        )

    @app.teardown_request
    def _finish_request_timer(_exc):
        orch.end_request()

    # --------------- routes ---------------
    @app.get("/health")
    #@track_performance
    def health():
        try:
            deployed = orch.check_if_deployed()
            validators = (orch.qbft_get_validators() or "").strip()
            return ok({
                "deployed": deployed,
                "validators": validators,
                "from_idx": os.getenv("FROM_IDX", "0"),
                "real_interact": bool(os.getenv("REAL_INTERACT")),
            })
        except Exception as e:
            return err("unhealthy", 500, detail=str(e), trace=traceback.format_exc())

    @app.post("/register-node")
    #@track_performance
    def register_node():
        req, bad = require_json(
            ["node_id", "node_name", "node_type", "public_key", "address", "rpcURL", "signature"]
        )

        print(f"[register] payload: {req}")

        if bad: return bad

        # --- Registration hardening preflight (soft-fail if orchestrator lacks helpers) ---
        try:
            # 1) duplicate nodeId check (HTTP 409)
            if hasattr(orch, "is_node_id_taken") and orch.is_node_id_taken(req.get("node_id", "")):
                return err("duplicate_node_id", 409)

            # 2) duplicate node signature check (HTTP 409)
            if hasattr(orch, "is_node_registered") and orch.is_node_registered(req.get("signature", "")):
                return err("Already Registered", 409)

            # 3) signature verification over canonical payload (HTTP 403)
            #    Expect orchestrator.verify_registration_sig(req) -> bool
            if hasattr(orch, "verify_registration_sig"):
                if not orch.verify_registration_sig(req):
                    return err("bad_registration_sig", 403)
        except Exception as _pre_e:
            # Do not crash on preflight; log and continue to on-chain checks
            print(f"[preflight] registration checks skipped due to: {_pre_e}")

        # Optional flags accepted in payload:
        # - wants_validator (bool)
        try:
            out = orch.registration_flow(req)
            try:
                # Only Fog/Cloud nodes can be validators in your model
                if req.get("node_type") in {"Fog", "Cloud"}:
                    my_sig = req.get("signature", "")
                    status = out.get("status")

                    # If we’re already included (or this node was already a validator), start immediately.
                    if status in {"validator_included", "already_registered"} and orch.is_validator():
                        print("[listener] post-register: already a validator, starting listener")
                        orch.start_validator_listener()
                    # If proposal was made but inclusion is pending, arm a background waiter.
                    elif status == "validator_proposed":
                        print("[listener] post-register: proposed, starting listener when becomes validator")
                        orch.start_listener_when_becomes_validator(my_sig)
            except Exception as _e:
                # Don’t fail registration just because the listener start logic hiccupped.
                print(f"[listener] post-register start logic error: {_e}")
            # normalize response for clients
            return ok({
                "status": out.get("status"),
                "ack_sent": out.get("ack_sent", False),
                "tx": out.get("tx"),
            })
        except Exception as e:
            return err("registration_failed", 500, detail=str(e), trace=traceback.format_exc())

    @app.get("/node/<signature>")
    #@track_performance
    def node_details(signature: str):
        try:
            # quick reg check
            is_reg = orch.is_node_registered(signature)
            if not is_reg:
                return err("node_not_registered", 404)
            details = orch.get_node_by_sig(signature)
            return ok({"details": details})
        except Exception as e:
            return err("node_query_failed", 500, detail=str(e))

    @app.get("/validators")
    #@track_performance
    def validators():
        try:
            v = orch.qbft_get_validators()
            return ok({"validators": v})
        except Exception as e:
            return err("validator_query_failed", 500, detail=str(e))

    @app.get("/metrics/latency")
    def latency_metrics():
        try:
            output_path = orch.latency_recorder.write_summary()
            return ok({"summary": orch.latency_summary(), "output": str(output_path)})
        except Exception as e:
            return err("latency_metrics_failed", 500, detail=str(e))

    @app.post("/acknowledgement")
    def acknowledgement():
        try:
            node_id = request.form.get("node_id")
            enode = request.form.get("enode")
            if not node_id or not enode:
                return err("Missing node_id or enode", 400)

            repo_dir = Path(repo_root or ".").resolve()
            if request.files.get("genesis_file"):
                genesis_path = repo_dir / "genesis" / "genesis.json"
                genesis_path.parent.mkdir(parents=True, exist_ok=True)
                request.files["genesis_file"].save(genesis_path)

            if request.files.get("node_registry_file"):
                registry_path = repo_dir / "data" / "NodeRegistry.json"
                registry_path.parent.mkdir(parents=True, exist_ok=True)
                request.files["node_registry_file"].save(registry_path)

            if request.files.get("prefunded_keys_file"):
                prefunded_path = repo_dir / "prefunded_keys.json"
                prefunded_path.parent.mkdir(parents=True, exist_ok=True)
                request.files["prefunded_keys_file"].save(prefunded_path)

            for rel in ("data/enode.txt", "static/enode.txt", "client_inbox/enode.txt"):
                enode_path = repo_dir / rel
                enode_path.parent.mkdir(parents=True, exist_ok=True)
                enode_path.write_text(enode.strip() + "\n")

            return ok({
                "status": "success",
                "message": f"Acknowledgment received for {node_id}",
                "enode": enode,
            })
        except Exception as e:
            return err("acknowledgement_failed", 500, detail=str(e), trace=traceback.format_exc())

    @app.post("/access")
    ##@track_performance
    def access():
        """
        Request body:
        {
          "from_signature": "...",
          "to_signature": "...",
          "method": "GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD",
          "resource_path": "/sensors/1",
          "expiry_secs": 900,                # optional
          "allow_delegation": false,         # optional
          "delegation_depth": 0              # optional
        }
        """
        req, bad = require_json(["from_signature", "method", "resource_path"])
        if bad: return bad
        
        to_sig = req.get("to_signature") or local_sig
        if not to_sig:
            return err("to_signature missing and local node signature not found", 422)

        allowed, retry_after_ms = rate_limiter.allow(to_sig, orch.local_node_tier)
        if not allowed:
            return err("rate_limit_exceeded", 429, reason="rate_limit_exceeded", retry_after_ms=retry_after_ms)
        
        try:
            result = orch.access_flow(
                req["from_signature"], to_sig,
                req["method"], req["resource_path"],
                int(req.get("expiry_secs", 900)),
                bool(req.get("allow_delegation", False)),
                int(req.get("delegation_depth", 0)),
                bool(req.get("audit", True))
            )
            print("yoar taaam chu waatnaavun")
            if not result.get("ok"):
                # bubble up reason
                return err(result.get("why", "access_denied"), 403, **{k:v for k,v in result.items() if k not in {"ok"}})
            return ok(result)
        except Exception as e:
            return err("access_flow_failed", 500, detail=str(e), trace=traceback.format_exc())

    @app.post("/delegate")
    #@track_performance
    def delegate():
        """
        Body:
        {
          "parent_from_sig": "...",   # existing grant owner (delegable, depth>0)
          "to_sig": "...",
          "child_from_sig": "...",
          "ops_csv": "READ"           # or "READ,WRITE" (subset should be enforced by contract)
          "child_expiry_secs": 600
        }
        """

        req, bad = require_json(["parent_from_sig", "to_sig", "child_from_sig", "ops_csv"])
        if bad: return bad

        try:
            res = orch.delegate_flow(
                req["parent_from_sig"], req["to_sig"], req["child_from_sig"],
                req["ops_csv"], int(req.get("child_expiry_secs", 600)),
                int(req["policy_id"]) if req.get("policy_id") is not None else None
            )
            if not res.get("ok"):
                return err(res.get("why", "delegate_failed"), 400, **{k:v for k,v in res.items() if k not in {"ok"}})
            return ok(res)
        except Exception as e:
            return err("delegate_flow_failed", 500, detail=str(e), trace=traceback.format_exc())

    @app.post("/revoke-grant")
    #@track_performance
    def revoke_grant():
        req, bad = require_json(["from_signature", "to_signature"])
        if bad: return bad
        try:
            policy_id = req.get("policy_id")
            if policy_id is None:
                if req.get("ctx") or (req.get("method") and req.get("resource_path")):
                    grant = orch.get_grant_ex_auto(
                        req["from_signature"],
                        req["to_signature"],
                        method=req.get("method"),
                        resource_path=req.get("resource_path"),
                        ctx=req.get("ctx")
                    )
                    policy_id = grant.get("policyId")
            if policy_id is None:
                return err("missing policy_id", 422)
            tx = orch.revoke_grant(req["from_signature"], req["to_signature"], int(policy_id))
            return ok({"tx": tx})
        except Exception as e:
            return err("revoke_failed", 500, detail=str(e))

    @app.get("/grant")
    #@track_performance
    def grant_info():
        from_sig = request.args.get("from_signature")
        to_sig   = request.args.get("to_signature")
        method   = request.args.get("method")          # e.g., GET/POST/PUT/DELETE
        path     = request.args.get("resource_path")   # e.g., /temperature
        ctx      = request.args.get("ctx")             # optional raw ctx (api:METHOD:/path)

        if not from_sig or not to_sig:
            return err("from_signature and to_signature are required", 422)

        try:
            gx = orch.get_grant_ex_auto(
                from_sig, to_sig,
                method=method, resource_path=path, ctx=ctx
            )
            return ok({"grant": gx})
        except Exception as e:
            return err("grant_query_failed", 500, detail=str(e))

    @app.get("/expiry-check")
    def expiry_check():
        from_sig = request.args.get("from_signature")
        to_sig = request.args.get("to_signature")
        if not from_sig or not to_sig:
            return err("from_signature and to_signature are required", 422)

        policy_id = request.args.get("policy_id")
        method = request.args.get("method")
        path = request.args.get("resource_path")
        ctx = request.args.get("ctx")

        try:
            expired = orch.is_grant_expired(
                from_sig,
                to_sig,
                int(policy_id) if policy_id is not None else None,
            ) if policy_id is not None else orch.is_grant_expired(
                from_sig,
                to_sig,
                orch.get_grant_ex_auto(
                    from_sig,
                    to_sig,
                    method=method,
                    resource_path=path,
                    ctx=ctx,
                ).get("policyId")
            )
            return ok({"expired": bool(expired)})
        except Exception as e:
            return err("expiry_check_failed", 500, detail=str(e))

    # Convenience endpoints that mirror your old style (read/write/update/remove)
    # These just call /access under the hood with the correct op, using METHOD_TO_OP mapping.

    def _simple_access(expected_method: str):
        # query params for convenience: from_signature, to_signature, resource_path
        from_sig = request.args.get("from_signature")
        path = request.args.get("resource_path")
        to_sig = local_sig
        if not from_sig or not path:
            return err("from_signature and resource_path are required", 422)
        if not to_sig:
            return err("to_signature missing and local node signature not found", 422)
        try:
            res = orch.access_flow(from_sig, to_sig, expected_method, path)
            if not res.get("ok"):
                return err(res.get("why", "access_denied"), 403, **{k:v for k,v in res.items() if k not in {"ok"}})
            return ok(res)
        except Exception as e:
            return err("access_flow_failed", 500, detail=str(e))

    # Example: Reading temperature data from a sensor
    @app.get("/temperature")
    def read_temperature():
        return _simple_access("GET")   # GET means READ in access control

    # Example: Posting new temperature reading to Edge
    @app.post("/temperature")
    #@track_performance
    def post_temperature():

        return _simple_access("POST")  # POST means WRITE in access control

    # Example: Updating firmware on a device
    @app.put("/firmware")
    #@track_performance
    def update_firmware():
        return _simple_access("PUT")   # PUT means UPDATE in access control

    # Example: Removing a firmware version from a device
    @app.delete("/firmware")
    #@track_performance
    def remove_firmware():
        return _simple_access("DELETE")  # DELETE means REMOVE in access control

    # Example: Reading alerts from a node
    @app.get("/alerts")
    #@track_performance
    def read_alerts():
        return _simple_access("GET")

    # Example: Creating a new alert
    @app.post("/alerts")
    #@track_performance
    def create_alert():
        return _simple_access("POST")

    # Example: Controlling LED (update state)
    @app.put("/control/led")
    #@track_performance
    def control_led():
        return _simple_access("PUT")

    # Example: Stopping motor control (remove control rights)
    @app.delete("/control/motor")
    #@track_performance
    def stop_motor():
        return _simple_access("DELETE")

    return app


# --------------- main ---------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Orchestration API")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--repo-root", default=None, help="Path where interact.js & artifacts live (defaults to this file's dir)")
    args = parser.parse_args()

    app = make_app(repo_root=args.repo_root)
    # start Flask when run directly
    app.run(host=args.host, port=args.port, debug=True)
