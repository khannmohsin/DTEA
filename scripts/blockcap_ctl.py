#!/usr/bin/env python3
"""
BlockCap operator CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import requests


DEFAULT_BASE_URL = os.getenv("BLOCKCAP_URL", "http://localhost:5600").rstrip("/")
DEFAULT_TOKEN = os.getenv("BLOCKCAP_TOKEN", "")


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _request(method: str, base_url: str, path: str, *, token: str = "", json_body: dict | None = None):
    response = requests.request(method, f"{base_url}{path}", headers=_headers(token), json=json_body, timeout=120)
    response.raise_for_status()
    payload = response.json()
    print(json.dumps(payload, indent=2, sort_keys=True))


def cmd_root_nodes_list(args):
    _request("GET", args.url, "/admin/nodes/list", token=args.token)


def cmd_root_policy_list(args):
    _request("GET", args.url, "/admin/policy/list", token=args.token)


def cmd_root_policy_upload(args):
    with open(args.file, "r", encoding="utf-8") as handle:
        policies = json.load(handle)
    for policy in list(policies.get("policies") or []):
        body = {
            "from_role": policy["from_role"],
            "to_role": policy["to_role"],
            "ops": policy["ops"],
            "resource": policy.get("resource", ""),
        }
        _request("POST", args.url, "/admin/policy/create", token=args.token, json_body=body)


def cmd_root_grant_revoke(args):
    _request(
        "POST",
        args.url,
        "/admin/grant/revoke",
        token=args.token,
        json_body={"from_sig": args.from_sig, "to_sig": args.to_sig, "policy_id": args.policy_id},
    )


def cmd_node_status(args):
    _request("GET", args.url, "/health")


def cmd_spec(args):
    _request("GET", args.url, "/spec", token=args.token)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BlockCap operator CLI")
    parser.add_argument("--url", default=DEFAULT_BASE_URL)
    parser.add_argument("--token", default=DEFAULT_TOKEN)
    subparsers = parser.add_subparsers(dest="command")

    root_parser = subparsers.add_parser("root")
    root_sub = root_parser.add_subparsers(dest="root_command")

    nodes_parser = root_sub.add_parser("nodes")
    nodes_sub = nodes_parser.add_subparsers(dest="nodes_command")
    nodes_list = nodes_sub.add_parser("list")
    nodes_list.set_defaults(func=cmd_root_nodes_list)

    policy_parser = root_sub.add_parser("policy")
    policy_sub = policy_parser.add_subparsers(dest="policy_command")
    policy_list = policy_sub.add_parser("list")
    policy_list.set_defaults(func=cmd_root_policy_list)
    policy_upload = policy_sub.add_parser("upload")
    policy_upload.add_argument("--file", required=True)
    policy_upload.set_defaults(func=cmd_root_policy_upload)

    grant_parser = root_sub.add_parser("grant")
    grant_sub = grant_parser.add_subparsers(dest="grant_command")
    grant_revoke = grant_sub.add_parser("revoke")
    grant_revoke.add_argument("--from", dest="from_sig", required=True)
    grant_revoke.add_argument("--to", dest="to_sig", required=True)
    grant_revoke.add_argument("--policy-id", dest="policy_id", type=int, required=True)
    grant_revoke.set_defaults(func=cmd_root_grant_revoke)

    node_parser = subparsers.add_parser("node")
    node_parser.add_argument("name")
    node_sub = node_parser.add_subparsers(dest="node_command")
    node_status = node_sub.add_parser("status")
    node_status.set_defaults(func=cmd_node_status)

    spec_parser = subparsers.add_parser("spec")
    spec_parser.set_defaults(func=cmd_spec)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    try:
        args.func(args)
        return 0
    except requests.HTTPError as exc:
        response = exc.response
        if response is not None:
            print(response.text, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
