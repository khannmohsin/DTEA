#!/usr/bin/env python3
import argparse
import json
import sys
import time
from datetime import datetime
from typing import Any

import requests


DEFAULT_HOST = "http://127.0.0.1:5600"


def http_get_json(host: str, path: str, **params) -> dict[str, Any]:
    response = requests.get(host.rstrip("/") + path, params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def format_event(event: dict[str, Any]) -> str:
    stamp = datetime.fromtimestamp(event["ts_unix_ms"] / 1000).strftime("%H:%M:%S")
    return (
        f"{stamp} "
        f"[{event.get('node_tier', 'unknown')}] "
        f"{event.get('flow_type', 'daemon')} "
        f"{event.get('stage', '')} "
        f"{event.get('status', '')} "
        f"{event.get('message', '')}"
    )


def filter_items(items: list[dict[str, Any]], flow_type: str | None, status: str | None) -> list[dict[str, Any]]:
    filtered = items
    if flow_type:
        filtered = [item for item in filtered if item.get("flow_type") == flow_type]
    if status:
        filtered = [item for item in filtered if item.get("status") == status or item.get("final_status") == status or item.get("last_status") == status]
    return filtered


def cmd_watch(args: argparse.Namespace) -> int:
    if args.follow:
        after = 0
        try:
            with requests.get(
                args.host.rstrip("/") + "/events/stream",
                params={"follow": 1, "after": after, "limit": args.limit},
                stream=True,
                timeout=30,
            ) as response:
                response.raise_for_status()
                for raw_line in response.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue
                    event = json.loads(raw_line)
                    if filter_items([event], args.flow_type, args.status):
                        print(json.dumps(event, sort_keys=True) if args.json else format_event(event))
        except Exception:
            seen = 0
            while True:
                payload = http_get_json(args.host, "/events/recent", limit=args.limit)
                events = [event for event in payload["events"] if int(event.get("sequence", 0)) > seen]
                events = filter_items(events, args.flow_type, args.status)
                for event in events:
                    seen = max(seen, int(event.get("sequence", 0)))
                    print(json.dumps(event, sort_keys=True) if args.json else format_event(event))
                time.sleep(args.interval)
        return 0

    payload = http_get_json(args.host, "/events/recent", limit=args.limit)
    for event in filter_items(payload["events"], args.flow_type, args.status):
        print(json.dumps(event, sort_keys=True) if args.json else format_event(event))
    return 0


def cmd_flows(args: argparse.Namespace) -> int:
    payload = http_get_json(args.host, "/events/flows", limit=args.limit)
    flows = filter_items(payload["flows"], args.flow_type, args.status)
    for flow in flows:
        if args.json:
            print(json.dumps(flow, sort_keys=True))
            continue
        duration = "running" if flow.get("duration_ms") is None else f"{flow['duration_ms']} ms"
        print(f"{flow['flow_id']} [{flow['node_tier']}] {flow['flow_type']} {flow['final_status']} {duration} {flow['message']}")
    return 0


def cmd_active(args: argparse.Namespace) -> int:
    payload = http_get_json(args.host, "/events/active")
    flows = filter_items(payload["flows"], args.flow_type, args.status)
    for flow in flows:
        print(json.dumps(flow, sort_keys=True) if args.json else f"{flow['flow_id']} [{flow['node_tier']}] {flow['flow_type']} {flow['last_status']} {flow['last_stage']} {flow['message']}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    payload = http_get_json(args.host, "/events/stats")
    stats = payload["stats"]
    if args.json:
        print(json.dumps(stats, indent=2, sort_keys=True))
        return 0
    print("Status counts:")
    for key, value in sorted((stats.get("status_counts") or {}).items()):
        print(f"  {key}: {value}")
    print("Flow types:")
    for key, value in sorted((stats.get("flow_type_counts") or {}).items()):
        print(f"  {key}: {value}")
    if stats.get("top_reasons"):
        print("Top reasons:")
        for item in stats["top_reasons"]:
            print(f"  {item['reason']}: {item['count']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Watch a BlockCap node's live process events")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--flow-type", default=None)
    parser.add_argument("--status", default=None)
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    watch = sub.add_parser("watch")
    watch.add_argument("--limit", type=int, default=100)
    watch.add_argument("--follow", action="store_true")
    watch.add_argument("--interval", type=float, default=2.0)
    watch.set_defaults(func=cmd_watch)

    flows = sub.add_parser("flows")
    flows.add_argument("--limit", type=int, default=50)
    flows.set_defaults(func=cmd_flows)

    active = sub.add_parser("active")
    active.set_defaults(func=cmd_active)

    stats = sub.add_parser("stats")
    stats.set_defaults(func=cmd_stats)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
