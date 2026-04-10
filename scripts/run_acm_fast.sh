#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"

SCENARIO="acm-fast"
MODE="all"
QUICK_RUNS=10
FINAL_RUNS=24
RESUME=0
FOG_COUNT=1
EDGE_COUNT=1
ENDPOINT_COUNT=1

usage() {
  cat <<'EOF'
Usage:
  scripts/run_acm_fast.sh [--scenario NAME] [--mode all|topology|quick|static|final] [--quick-runs N] [--final-runs N] [--fog N] [--edge N] [--endpoint N] [--resume]

Modes:
  all      Start topology, run quick pass, collect static metrics, run final pass
  topology Start topology only
  quick    Run quick experiment + figure generation
  static   Collect gas/contract metrics and build gas comparison
  final    Run final experiment + figure generation

Examples:
  scripts/run_acm_fast.sh
  scripts/run_acm_fast.sh --mode quick --scenario acm-fast
  scripts/run_acm_fast.sh --mode final --final-runs 30
  scripts/run_acm_fast.sh --resume
  scripts/run_acm_fast.sh --mode all --fog 2 --edge 2 --endpoint 2
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenario)
      SCENARIO="$2"
      shift 2
      ;;
    --mode)
      MODE="$2"
      shift 2
      ;;
    --quick-runs)
      QUICK_RUNS="$2"
      shift 2
      ;;
    --final-runs)
      FINAL_RUNS="$2"
      shift 2
      ;;
    --resume)
      RESUME=1
      shift
      ;;
    --fog)
      FOG_COUNT="$2"
      shift 2
      ;;
    --edge)
      EDGE_COUNT="$2"
      shift 2
      ;;
    --endpoint)
      ENDPOINT_COUNT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

case "$MODE" in
  all|topology|quick|static|final) ;;
  *)
    echo "Invalid --mode: $MODE" >&2
    usage
    exit 2
    ;;
esac

if [[ ! -x "$VENV_PY" ]]; then
  echo "Python venv not found at: $VENV_PY" >&2
  echo "Create it first, then retry." >&2
  exit 1
fi

# Prevent overlapping full-run invocations that contend for the same artifacts.
OTHER_RUNNERS=$(ps -axo pid=,command= | grep -E "[r]un_acm_fast\.sh" | awk '{print $1}' | grep -v "^$$$" || true)
if [[ -n "$OTHER_RUNNERS" ]]; then
  echo "Another run_acm_fast.sh process is already active: $OTHER_RUNNERS" >&2
  echo "Wait for it to finish, or stop it before starting a new run." >&2
  exit 1
fi

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

TOPOLOGY_JSON="$ROOT_DIR/runtime/generated/$SCENARIO/topology.json"

ensure_topology_file() {
  if [[ ! -f "$TOPOLOGY_JSON" ]]; then
    echo "Missing topology file: $TOPOLOGY_JSON" >&2
    echo "Run with --mode topology or --mode all first." >&2
    exit 1
  fi
}

run_topology() {
  if [[ -f "$TOPOLOGY_JSON" && $RESUME -eq 0 ]]; then
    echo "[info] existing scenario detected, reusing topology: $TOPOLOGY_JSON"
    echo "[info] use --resume explicitly to silence this message"
    return
  fi
  if [[ $RESUME -eq 1 && -f "$TOPOLOGY_JSON" ]]; then
    echo "[resume] topology exists, skipping start: $TOPOLOGY_JSON"
    return
  fi
  echo "[step] starting reusable topology scenario=$SCENARIO"
  "$VENV_PY" "$ROOT_DIR/scripts/run_topology.py" \
    --fog "$FOG_COUNT" --edge "$EDGE_COUNT" --endpoint "$ENDPOINT_COUNT" \
    --runtime-backend native \
    --scenario "$SCENARIO" \
    --mode local
}

run_quick() {
  ensure_topology_file
  if [[ $RESUME -eq 1 && -f "$ROOT_DIR/results/metrics_report_summary.json" ]]; then
    echo "[resume] summary exists, quick pass may already be available"
  fi
  echo "[step] quick pass runs=$QUICK_RUNS"
  "$VENV_PY" "$ROOT_DIR/scripts/run_all_experiments.py" \
    --scenario-file "$TOPOLOGY_JSON" \
    --runs "$QUICK_RUNS"
  "$VENV_PY" "$ROOT_DIR/scripts/generate_matplotlib_report.py" \
    --results-dir "$ROOT_DIR/results" \
    --scenario "$SCENARIO-quick"
}

run_static() {
  echo "[step] collecting gas and contract static metrics"
  (cd "$ROOT_DIR" && node scripts/measure_gas.js)
  (cd "$ROOT_DIR" && node scripts/collect_contract_metrics.js)
  "$VENV_PY" "$ROOT_DIR/scripts/build_gas_comparison.py"
}

run_final() {
  ensure_topology_file
  echo "[step] final pass runs=$FINAL_RUNS"
  "$VENV_PY" "$ROOT_DIR/scripts/run_all_experiments.py" \
    --scenario-file "$TOPOLOGY_JSON" \
    --runs "$FINAL_RUNS"
  "$VENV_PY" "$ROOT_DIR/scripts/generate_matplotlib_report.py" \
    --results-dir "$ROOT_DIR/results" \
    --scenario "$SCENARIO-final"
}

case "$MODE" in
  all)
    run_topology
    run_quick
    run_static
    run_final
    ;;
  topology)
    run_topology
    ;;
  quick)
    run_quick
    ;;
  static)
    run_static
    ;;
  final)
    run_final
    ;;
esac

echo "[done] mode=$MODE scenario=$SCENARIO"
echo "[done] plots in: $ROOT_DIR/results/plots"