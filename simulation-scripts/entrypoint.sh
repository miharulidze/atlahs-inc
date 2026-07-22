#!/bin/bash
# Simulation-only entrypoint. Repo bind-mounted at /workspace.
# NOTE: this file is baked into the atlahs-sim image at build time (Dockerfile
# COPY) — rebuild the image after editing it.
set -e

# Docker contract: repo at /workspace. SIM_SCRIPTS_DIR overrides for local use.
SS=${SIM_SCRIPTS_DIR:-/workspace/simulation-scripts}
EXPS="$SS/experiments"

list_experiments() {
  for d in "$EXPS"/*/; do
    name=$(basename "$d")
    case $name in _*) continue ;; esac
    [ -f "$d/run.py" ] && echo "  $name"
  done
}

usage() {
  echo "Usage: docker run --rm -v \$(pwd):/workspace atlahs-sim <build|list|run> [args]"
  echo "Options:"
  echo "  build              build the pcm-sdk htsim simulator (INC datapath) + coll-extended txt2bin"
  echo "                     (CPU-only; requires the sim/pcm-sdk_zhiyi submodule materialized on the host)"
  echo "  list               list the available experiments"
  echo "  run <exp> [args]   run experiments/<exp>/run.py; args pass through"
  echo "                     (e.g. run scaleup_coll_ab --validate)"
  echo "Experiments:"
  list_experiments
}

if [ "$#" -eq 0 ]; then
  usage
  exit 1
fi

opt=$1
shift

case $opt in
  build)
    python3 "$SS/build_sim.py"
    ;;
  list)
    list_experiments
    ;;
  run)
    exp=${1:-}
    case $exp in
      ""|-*)
        echo "Error: 'run' needs an experiment name (run <exp> [args])" >&2
        echo "Experiments:" >&2
        list_experiments >&2
        exit 1
        ;;
      _*)
        echo "Error: '$exp' is a template/hidden experiment (underscore prefix); invoke its run.py directly" >&2
        exit 1
        ;;
    esac
    shift
    if [ ! -f "$EXPS/$exp/run.py" ]; then
      echo "Error: unknown experiment '$exp'" >&2
      echo "Experiments:" >&2
      list_experiments >&2
      exit 1
    fi
    exec python3 "$EXPS/$exp/run.py" "$@"
    ;;
  *)
    echo "Error: unknown option '$opt' (expected 'build', 'list' or 'run')" >&2
    exit 1
    ;;
esac
