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


from flask import Flask, Response, jsonify, request, stream_with_context
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


def _int_arg(name: str, default: int) -> int:
    raw = request.args.get(name, str(default))
    try:
        return int(raw)
    except Exception:
        return default


def _dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>BlockCap Node Dashboard</title>
  <style>
    :root { color-scheme: light; --bg:#f3f1ea; --panel:#fffdf7; --ink:#162124; --muted:#647274; --line:#d8d1c5; --ok:#1d7a43; --err:#ae2c2c; --wait:#b86a00; --start:#1f5fa8; }
    * { box-sizing:border-box; }
    body { margin:0; font-family: "IBM Plex Sans", "Segoe UI", sans-serif; color:var(--ink); background:linear-gradient(180deg,#efe9db 0%,#f7f4ec 100%); }
    .wrap { max-width:1400px; margin:0 auto; padding:24px; }
    .hero { display:grid; grid-template-columns:2fr 1fr; gap:16px; margin-bottom:16px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:18px; padding:18px; box-shadow:0 8px 30px rgba(32,32,24,0.06); }
    h1,h2 { margin:0 0 10px; font-weight:700; }
    h1 { font-size:28px; }
    h2 { font-size:18px; }
    .sub { color:var(--muted); font-size:14px; }
    .grid { display:grid; grid-template-columns:1.1fr 1.4fr; gap:16px; }
    .grid + .grid { margin-top:16px; }
    .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:10px; }
    .stat { border:1px solid var(--line); border-radius:14px; padding:12px; background:#fff; }
    .stat b { display:block; font-size:20px; margin-top:6px; }
    .list { display:flex; flex-direction:column; gap:10px; max-height:420px; overflow:auto; }
    .event, .flow { border:1px solid var(--line); border-radius:14px; padding:12px; background:#fff; cursor:pointer; }
    .event-head, .flow-head { display:flex; justify-content:space-between; gap:10px; align-items:center; font-size:13px; }
    .msg { margin-top:6px; font-size:14px; }
    .meta { color:var(--muted); font-size:12px; margin-top:6px; word-break:break-word; }
    .badge { display:inline-block; padding:3px 8px; border-radius:999px; font-size:12px; font-weight:700; color:#fff; }
    .ok { background:var(--ok); } .error, .denied { background:var(--err); } .waiting { background:var(--wait); } .started, .reused, .skipped { background:var(--start); }
    pre { white-space:pre-wrap; word-break:break-word; font-size:13px; margin:0; }
    .detail-events { display:flex; flex-direction:column; gap:8px; max-height:460px; overflow:auto; }
    @media (max-width: 960px) { .hero,.grid { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <section class="panel">
        <h1>BlockCap Live Process Visibility</h1>
        <div id="identity" class="sub">Loading node identity...</div>
      </section>
      <section class="panel">
        <h2>Health</h2>
        <div id="health" class="sub">Loading...</div>
      </section>
    </div>
    <div class="grid">
      <section class="panel"><h2>Active Flows</h2><div id="active" class="list"></div></section>
      <section class="panel"><h2>Counters</h2><div id="stats" class="stats"></div></section>
    </div>
    <div class="grid">
      <section class="panel"><h2>Recent Timeline</h2><div id="timeline" class="list"></div></section>
      <section class="panel"><h2>Flow Detail</h2><div id="detail" class="detail-events sub">Select a flow to inspect it.</div></section>
    </div>
    <div class="grid">
      <section class="panel"><h2>Recent Flows</h2><div id="flows" class="list"></div></section>
      <section class="panel"><h2>Latency Summary</h2><div id="latency" class="list"></div></section>
    </div>
  </div>
  <script>
    const state = { flows: [] };
    const esc = (v) => String(v ?? "").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    const badge = (status) => `<span class="badge ${esc(status)}">${esc(status)}</span>`;
    const ts = (ms) => new Date(ms).toLocaleTimeString();
    function renderStats(stats) {
      const items = [
        ["Total Events", stats.total_events || 0],
        ["Granted", (stats.status_counts || {}).ok || 0],
        ["Denied", (stats.status_counts || {}).denied || 0],
        ["Errors", (stats.status_counts || {}).error || 0],
        ["Registrations", (stats.flow_type_counts || {}).registration || 0],
        ["Delegations", (stats.flow_type_counts || {}).delegation || 0],
        ["Revocations", (stats.flow_type_counts || {}).revocation || 0],
        ["Validator", (stats.flow_type_counts || {}).validator || 0],
      ];
      document.getElementById("stats").innerHTML = items.map(([label, value]) => `<div class="stat"><span class="sub">${esc(label)}</span><b>${esc(value)}</b></div>`).join("");
    }
    function renderList(id, items, mapFn, emptyText) {
      document.getElementById(id).innerHTML = items.length ? items.map(mapFn).join("") : `<div class="sub">${esc(emptyText)}</div>`;
    }
    function bindFlowClicks() {
      document.querySelectorAll("[data-flow-id]").forEach(el => el.onclick = () => showFlow(el.dataset.flowId));
    }
    function renderTimeline(events) {
      renderList("timeline", events, (event) => `<div class="event" data-flow-id="${esc(event.flow_id)}"><div class="event-head"><span>${esc(event.flow_type)} / ${esc(event.stage)}</span>${badge(event.status)}</div><div class="msg">${esc(event.message)}</div><div class="meta">${ts(event.ts_unix_ms)} | ${esc(event.node_tier)} | ${esc(event.component)} | ${esc(event.flow_id)}</div></div>`, "No events yet.");
    }
    function renderActive(flows) {
      renderList("active", flows, (flow) => `<div class="flow" data-flow-id="${esc(flow.flow_id)}"><div class="flow-head"><span>${esc(flow.flow_type)} / ${esc(flow.last_stage)}</span>${badge(flow.last_status)}</div><div class="msg">${esc(flow.message)}</div><div class="meta">${ts(flow.started_at_ms)} | ${esc(flow.node_tier)} | ${esc(flow.flow_id)}</div></div>`, "No active flows.");
    }
    function renderFlows(flows) {
      state.flows = flows;
      renderList("flows", flows, (flow) => `<div class="flow" data-flow-id="${esc(flow.flow_id)}"><div class="flow-head"><span>${esc(flow.flow_type)} / ${esc(flow.last_stage)}</span>${badge(flow.final_status)}</div><div class="msg">${esc(flow.message)}</div><div class="meta">${ts(flow.started_at_ms)} | ${flow.duration_ms == null ? "running" : `${flow.duration_ms} ms`} | ${esc(flow.flow_id)}</div></div>`, "No completed flows yet.");
    }
    function renderLatency(summary) {
      const rows = Object.values(summary || {});
      renderList("latency", rows, (item) => `<div class="event"><div class="event-head"><span>${esc(item.operation)} / ${esc(item.condition)}</span><span class="sub">${esc(item.node_tier)}</span></div><div class="msg">mean ${esc(item.mean_ms)} ms, count ${esc(item.count)}</div><div class="meta">min ${esc(item.min_ms)} ms, max ${esc(item.max_ms)} ms, stddev ${esc(item.stddev_ms)} ms</div></div>`, "No latency samples yet.");
    }
    function showFlow(flowId) {
      const flow = state.flows.find(item => item.flow_id === flowId);
      if (!flow) { document.getElementById("detail").innerHTML = `<div class="sub">Flow details unavailable.</div>`; return; }
      document.getElementById("detail").innerHTML = flow.events.map((event) => `<div class="event"><div class="event-head"><span>${esc(event.stage)}</span>${badge(event.status)}</div><div class="msg">${esc(event.message)}</div><div class="meta">${ts(event.ts_unix_ms)} | ${esc(event.component)} | ${event.duration_ms == null ? "" : `${event.duration_ms} ms`}</div><pre>${esc(JSON.stringify(event.details || {}, null, 2))}</pre></div>`).join("");
    }
    async function refresh() {
      const response = await fetch("/dashboard/data");
      const payload = await response.json();
      document.getElementById("identity").textContent = `${payload.node.node_name || "Unnamed"} (${payload.node.node_tier}) | node_id=${payload.node.node_id || "-"} | rpc=${payload.node.rpc_url || "-"} | api=${payload.node.api_url || "-"}`;
      document.getElementById("health").textContent = `deployed=${payload.health.deployed} | validator=${payload.health.is_validator} | validators=${payload.health.validators}`;
      renderActive(payload.active_flows || []);
      renderStats(payload.event_stats || {});
      renderTimeline(payload.recent_events || []);
      renderFlows(payload.recent_flows || []);
      renderLatency(payload.latency_summary || {});
      bindFlowClicks();
    }
    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>"""

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
        flow_id = orch.start_flow(
            "registration",
            stage="request_received",
            message="Registration request received",
            component="api",
            details={"node_id": req.get("node_id"), "node_type": req.get("node_type"), "rpc_url": req.get("rpcURL")},
            from_signature=req.get("signature"),
        )

        # --- Registration hardening preflight (soft-fail if orchestrator lacks helpers) ---
        try:
            # 1) duplicate nodeId check (HTTP 409)
            if hasattr(orch, "is_node_id_taken") and orch.is_node_id_taken(req.get("node_id", "")):
                orch.finish_flow(
                    "denied",
                    stage="registration_finished",
                    message="Registration denied because the node_id already exists",
                    details={"node_id": req.get("node_id")},
                    from_signature=req.get("signature"),
                )
                return err("duplicate_node_id", 409)

            # 2) duplicate node signature check (HTTP 409)
            if hasattr(orch, "is_node_registered") and orch.is_node_registered(req.get("signature", "")):
                orch.finish_flow(
                    "denied",
                    stage="registration_finished",
                    message="Registration denied because the signature is already registered",
                    from_signature=req.get("signature"),
                )
                return err("Already Registered", 409)

            # 3) signature verification over canonical payload (HTTP 403)
            #    Expect orchestrator.verify_registration_sig(req) -> bool
            if hasattr(orch, "verify_registration_sig"):
                if not orch.verify_registration_sig(req):
                    orch.finish_flow(
                        "error",
                        stage="registration_finished",
                        message="Registration denied because signature verification failed",
                        from_signature=req.get("signature"),
                    )
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
            orch.finish_flow(
                "ok",
                stage="registration_finished",
                message=f"Registration flow completed with status {out.get('status')}",
                details={"status": out.get("status"), "ack_sent": out.get("ack_sent", False)},
                tx_hash=out.get("tx"),
                from_signature=req.get("signature"),
            )
            return ok({
                "status": out.get("status"),
                "ack_sent": out.get("ack_sent", False),
                "tx": out.get("tx"),
                "flow_id": flow_id,
            })
        except Exception as e:
            orch.finish_flow(
                "error",
                stage="registration_finished",
                message="Registration flow failed",
                details={"detail": str(e)},
                from_signature=req.get("signature"),
            )
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

    @app.get("/events/recent")
    def recent_events():
        limit = max(1, min(_int_arg("limit", 100), 500))
        return ok({"events": orch.recent_events(limit=limit)})

    @app.get("/events/flows")
    def event_flows():
        limit = max(1, min(_int_arg("limit", 50), 200))
        return ok({"flows": orch.flow_summaries(limit=limit)})

    @app.get("/events/active")
    def active_flows():
        return ok({"flows": orch.active_flow_summaries()})

    @app.get("/events/stats")
    def event_stats():
        return ok({"stats": orch.event_stats()})

    @app.get("/events/stream")
    def stream_events():
        follow = request.args.get("follow", "1") != "0"
        after = _int_arg("after", 0)
        limit = max(1, min(_int_arg("limit", 100), 500))

        def generate():
            events = [event for event in orch.recent_events(limit=limit) if int(event.get("sequence", 0)) > after]
            current = after
            for event in events:
                current = max(current, int(event.get("sequence", 0)))
                yield json.dumps(event, sort_keys=True) + "\n"
            if not follow:
                return
            while True:
                fresh = orch.wait_for_events(after_sequence=current, timeout=2.0)
                if not fresh:
                    continue
                for event in fresh:
                    current = max(current, int(event.get("sequence", 0)))
                    yield json.dumps(event, sort_keys=True) + "\n"

        return Response(stream_with_context(generate()), mimetype="application/x-ndjson")

    @app.get("/dashboard")
    def dashboard():
        return Response(_dashboard_html(), mimetype="text/html")

    @app.get("/dashboard/data")
    def dashboard_data():
        try:
            health_payload = {
                "deployed": orch.check_if_deployed(),
                "validators": orch.qbft_get_validators(),
                "is_validator": orch.is_validator(),
            }
        except Exception as exc:
            health_payload = {"deployed": False, "validators": "", "is_validator": False, "detail": str(exc)}
        return ok({
            "node": {
                "node_id": orch.local_node_id,
                "node_name": orch.local_node_name,
                "node_tier": orch.local_node_tier,
                "rpc_url": orch.besu_rpc_url,
                "api_url": request.host_url.rstrip("/"),
            },
            "health": health_payload,
            "active_flows": orch.active_flow_summaries(),
            "recent_events": orch.recent_events(limit=100),
            "recent_flows": orch.flow_summaries(limit=50),
            "event_stats": orch.event_stats(),
            "latency_summary": orch.latency_summary(),
        })

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
        flow_id = orch.start_flow(
            "access",
            stage="request_received",
            message="Access request received",
            component="api",
            details={"method": req.get("method"), "resource_path": req.get("resource_path")},
            from_signature=req.get("from_signature"),
        )
        
        to_sig = req.get("to_signature") or local_sig
        if not to_sig:
            orch.finish_flow(
                "error",
                stage="access_finished",
                message="Access request failed because to_signature could not be resolved",
                from_signature=req.get("from_signature"),
            )
            return err("to_signature missing and local node signature not found", 422)

        orch.emit_event(
            component="rate_limiter",
            stage="rate_limit_check",
            status="started",
            message="Evaluating per-object rate limit",
            from_signature=req.get("from_signature"),
            to_signature=to_sig,
        )
        allowed, retry_after_ms = rate_limiter.allow(to_sig, orch.local_node_tier)
        if not allowed:
            orch.finish_flow(
                "denied",
                stage="rate_limit_check",
                message="Access request was rate limited",
                component="rate_limiter",
                details={"retry_after_ms": retry_after_ms},
                from_signature=req.get("from_signature"),
                to_signature=to_sig,
            )
            return err("rate_limit_exceeded", 429, reason="rate_limit_exceeded", retry_after_ms=retry_after_ms)
        orch.emit_event(
            component="rate_limiter",
            stage="rate_limit_check",
            status="ok",
            message="Rate limit check passed",
            from_signature=req.get("from_signature"),
            to_signature=to_sig,
        )
        
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
                orch.finish_flow(
                    "denied",
                    stage="access_finished",
                    message=result.get("why", "access_denied"),
                    details={k: v for k, v in result.items() if k != "ok"},
                    from_signature=req.get("from_signature"),
                    to_signature=to_sig,
                )
                return err(result.get("why", "access_denied"), 403, **{k:v for k,v in result.items() if k not in {"ok"}})
            orch.finish_flow(
                "ok" if result.get("granted") else "denied",
                stage="access_finished",
                message="Access granted" if result.get("granted") else "Access denied by grant check",
                details={"granted": result.get("granted"), "policy_id": result.get("policyId"), "ctx": result.get("ctx")},
                policy_id=result.get("policyId"),
                from_signature=req.get("from_signature"),
                to_signature=to_sig,
            )
            return ok({**result, "flow_id": flow_id})
        except Exception as e:
            orch.finish_flow(
                "error",
                stage="access_finished",
                message="Access flow failed",
                details={"detail": str(e)},
                from_signature=req.get("from_signature"),
                to_signature=to_sig,
            )
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
        flow_id = orch.start_flow(
            "delegation",
            stage="request_received",
            message="Delegation request received",
            component="api",
            details={"ops_csv": req.get("ops_csv")},
            from_signature=req.get("parent_from_sig"),
            to_signature=req.get("to_sig"),
        )

        try:
            res = orch.delegate_flow(
                req["parent_from_sig"], req["to_sig"], req["child_from_sig"],
                req["ops_csv"], int(req.get("child_expiry_secs", 600)),
                int(req["policy_id"]) if req.get("policy_id") is not None else None
            )
            if not res.get("ok"):
                orch.finish_flow(
                    "denied",
                    stage="delegation_finished",
                    message=res.get("why", "delegate_failed"),
                    details={k: v for k, v in res.items() if k != "ok"},
                    from_signature=req.get("parent_from_sig"),
                    to_signature=req.get("to_sig"),
                )
                return err(res.get("why", "delegate_failed"), 400, **{k:v for k,v in res.items() if k not in {"ok"}})
            orch.finish_flow(
                "ok" if res.get("granted", True) else "denied",
                stage="delegation_finished",
                message="Delegation flow completed",
                details={"granted": res.get("granted", False)},
                tx_hash=res.get("tx"),
                from_signature=req.get("child_from_sig"),
                to_signature=req.get("to_sig"),
            )
            return ok({**res, "flow_id": flow_id})
        except Exception as e:
            orch.finish_flow(
                "error",
                stage="delegation_finished",
                message="Delegation flow failed",
                details={"detail": str(e)},
                from_signature=req.get("parent_from_sig"),
                to_signature=req.get("to_sig"),
            )
            return err("delegate_flow_failed", 500, detail=str(e), trace=traceback.format_exc())

    @app.post("/revoke-grant")
    #@track_performance
    def revoke_grant():
        req, bad = require_json(["from_signature", "to_signature"])
        if bad: return bad
        flow_id = orch.start_flow(
            "revocation",
            stage="request_received",
            message="Revocation request received",
            component="api",
            from_signature=req.get("from_signature"),
            to_signature=req.get("to_signature"),
        )
        try:
            policy_id = req.get("policy_id")
            if policy_id is None:
                orch.emit_event(
                    component="orchestrator",
                    stage="revocation_resolution",
                    status="started",
                    message="Resolving the policy_id for revocation",
                    from_signature=req.get("from_signature"),
                    to_signature=req.get("to_signature"),
                )
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
                orch.finish_flow(
                    "error",
                    stage="revocation_finished",
                    message="Revocation failed because policy_id could not be resolved",
                    from_signature=req.get("from_signature"),
                    to_signature=req.get("to_signature"),
                )
                return err("missing policy_id", 422)
            tx = orch.revoke_grant(req["from_signature"], req["to_signature"], int(policy_id))
            orch.finish_flow(
                "ok",
                stage="revocation_finished",
                message="Revocation flow completed",
                policy_id=int(policy_id),
                tx_hash=tx,
                from_signature=req.get("from_signature"),
                to_signature=req.get("to_signature"),
            )
            return ok({"tx": tx, "flow_id": flow_id})
        except Exception as e:
            orch.finish_flow(
                "error",
                stage="revocation_finished",
                message="Revocation flow failed",
                details={"detail": str(e)},
                from_signature=req.get("from_signature"),
                to_signature=req.get("to_signature"),
            )
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
