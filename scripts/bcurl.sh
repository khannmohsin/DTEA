#!/bin/bash
# BlockCap curl wrapper — pretty prints JSON responses
# Usage: ./scripts/bcurl.sh [curl args...]
# Examples:
#   ./scripts/bcurl.sh http://localhost:5600/health
#   ./scripts/bcurl.sh http://localhost:5600/admin/nodes/list -H "Authorization: Bearer changeme"
#   ./scripts/bcurl.sh -X POST http://localhost:5600/access -H "Content-Type: application/json" -d '{"from_sig":"...","to_sig":"...","method":"GET","resource_path":"/temperature"}'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
curl -s "$@" | python3 "$SCRIPT_DIR/pretty.py"
