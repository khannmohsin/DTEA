#!/usr/bin/env python3
import argparse
import re
import sys
from datetime import datetime


RESET = "\033[0m"
COLORS = {
    "EXEC": "\033[96m",
    "SPAWN": "\033[94m",
    "START": "\033[94m",
    "READY": "\033[92m",
    "REGISTER": "\033[95m",
    "FLOW": "\033[96m",
    "WARN": "\033[93m",
    "ERROR": "\033[91m",
    "HTTP": "\033[90m",
    "INFO": "\033[97m",
}


def classify(line: str) -> tuple[str, str]:
    text = line.strip()
    lower = text.lower()
    if not text:
        return "INFO", ""
    if text.startswith("[exec]"):
        return "EXEC", text.replace("[exec]", "", 1).strip()
    if text.startswith("[spawn]"):
        return "SPAWN", text.replace("[spawn]", "", 1).strip()
    if "traceback" in lower or "runtimeerror" in lower or " failed" in lower or lower.startswith("error:") or "exception" in lower:
        return "ERROR", text
    if "warning" in lower or "deprecated" in lower:
        return "WARN", text
    if "register" in lower:
        return "REGISTER", text
    if any(word in lower for word in ("access", "delegate", "delegation", "revoke", "revocation", "grant", "policy")):
        return "FLOW", text
    if "ready" in lower or "already reachable" in lower or "healthy" in lower:
        return "READY", text
    if "starting" in lower or "initializing" in lower or "provisioning" in lower:
        return "START", text
    if re.search(r'"\s*(get|post|put|delete|patch)\s+/.+\s+http/1\.[01]"\s+\d+', lower):
        return "HTTP", text
    return "INFO", text


def emit(kind: str, message: str, *, node: str, stream: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    tag = f"{kind:<8}"
    color = COLORS.get(kind, "")
    prefix = f"{ts}  |  {tag}  |  {node}/{stream}"
    print(f"{color}{prefix}{RESET}  --  {message}", flush=True)


def banner(title: str) -> str:
    bar = "=" * 72
    return f"{bar}\n{title}\n{bar}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Pretty-print BlockCap log streams")
    parser.add_argument("--node", default="node")
    parser.add_argument("--stream", default="api")
    parser.add_argument("--command", default="")
    parser.add_argument("--log-path", default="")
    args = parser.parse_args()

    print(banner(f" BlockCap Live Log  --  node: {args.node}  --  stream: {args.stream} "), flush=True)
    if args.command:
        print(f" command  --  {args.command}", flush=True)
    if args.log_path:
        print(f" log file --  {args.log_path}", flush=True)
    print("-" * 72, flush=True)

    for raw in sys.stdin:
        kind, message = classify(raw.rstrip("\n"))
        if not message and kind == "INFO":
            print("", flush=True)
            continue
        emit(kind, message, node=args.node, stream=args.stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
