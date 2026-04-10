#!/usr/bin/env python3
"""
Pipe JSON to this script for coloured, line-by-line output.
  curl http://localhost:5600/health | python3 scripts/pretty.py
"""
import json
import sys

GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def _fmt_value(v):
    if isinstance(v, bool):
        return f"\033[{'32' if v else '31'}m{str(v).lower()}{RESET}"
    if isinstance(v, (int, float)):
        return f"{YELLOW}{v}{RESET}"
    if v is None:
        return f"\033[90mnull{RESET}"
    return str(v)

def _print_dict(d, indent=0):
    pad = "  " * indent
    for k, v in d.items():
        key_str = f"{GREEN}{BOLD}{k}{RESET}"
        if isinstance(v, dict):
            print(f"{pad}{key_str}:")
            _print_dict(v, indent + 1)
        elif isinstance(v, list):
            print(f"{pad}{key_str}:")
            for item in v:
                if isinstance(item, dict):
                    print(f"{pad}  -")
                    _print_dict(item, indent + 2)
                else:
                    print(f"{pad}  - {_fmt_value(item)}")
        else:
            print(f"{pad}{key_str}: {_fmt_value(v)}")

def main():
    raw = sys.stdin.read().strip()
    if not raw:
        print("(empty response)")
        return
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(raw)
        return
    if isinstance(data, dict):
        _print_dict(data)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            print(f"{CYAN}[{i}]{RESET}")
            if isinstance(item, dict):
                _print_dict(item, indent=1)
            else:
                print(f"  {_fmt_value(item)}")
    else:
        print(_fmt_value(data))

if __name__ == "__main__":
    main()
