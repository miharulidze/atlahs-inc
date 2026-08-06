#!/usr/bin/env python3
"""Simulation-only build: the pcm-sdk two-tier htsim simulator (our INC datapath)
+ the coll-extended LogGOPSim txt2bin. No GPU/tracing dependencies.

Runs either from a host checkout or inside the atlahs-sim container (with the
sim/pcm-sdk_zhiyi submodule materialized recursively). Self-contained so the legacy
top-level build script stays untouched; the PCM recipe mirrors its
build_pcm_sdk (overlay copy + cmake + pcm/build.py --debug --build-htsim-atlahs),
the coll-txt2bin recipe mirrors tools/loggopsim-coll/README.md.
"""
import os
import subprocess
import sys

WS = os.environ.get(
    "ATLAHS_WORKSPACE",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
PCM_DIR = os.path.join(WS, "sim/pcm-sdk_zhiyi")
COLL_DIR = os.path.join(WS, "tools/loggopsim-coll")
PCM_BIN = os.path.join(PCM_DIR, "pcm/build/bin/htsim_flow_app_atlahs")
COLL_BIN = os.path.join(COLL_DIR, "LogGOPSim-1.1/txt2bin")
COLL_ID_PATCH = os.path.join(COLL_DIR, "op-flow-id-range.patch")
LOGGOPSIM_TARBALL_SHA256 = "c9960b206e4b9458632424595450a5feaaaad1b2f57a5474226ff99405230b64"


def _log(m):
    print(f"\033[94m[build_sim] {m}\033[0m", file=sys.stderr, flush=True)


def _sh(script, cwd):
    subprocess.run(["bash", "-lc", script], check=True, cwd=cwd,
                   stdout=sys.stderr, stderr=sys.stderr)


def build_coll_txt2bin():
    _log("coll-extended txt2bin ...")
    assert os.path.exists(os.path.join(COLL_DIR, "coll.patch")), \
        f"coll.patch not found under {COLL_DIR}"
    assert os.path.exists(COLL_ID_PATCH), \
        f"op-flow-id-range.patch not found under {COLL_DIR}"
    _sh(
        "set -euo pipefail; "
        "if [ ! -d LogGOPSim-1.1 ]; then "
        "  curl -fSL -o LogGOPSim-1.1.tgz https://htor.inf.ethz.ch/research/LogGOPSim/LogGOPSim-1.1.tgz; "
        f"  echo '{LOGGOPSIM_TARBALL_SHA256}  LogGOPSim-1.1.tgz' | sha256sum -c -; "
        "  tar xzf LogGOPSim-1.1.tgz; "
        "  ( cd LogGOPSim-1.1 && patch -p1 < ../coll.patch ); "
        "fi; "
        "cd LogGOPSim-1.1; "
        "if patch --dry-run --forward -p1 < ../op-flow-id-range.patch >/dev/null 2>&1; then "
        "  patch --forward -p1 < ../op-flow-id-range.patch; "
        "fi; "
        "grep -Fq 'item.coll_instance = add_collective_instance' txt2bin.re && "
        "grep -Fq 'const uint64_t max = 999999999ULL' txt2bin.re || "
        "  { echo 'simulator-safe op_flow_id guard is missing from txt2bin.re' >&2; exit 1; }; "
        "re2c -o txt2bin.cpp txt2bin.re; "
        "g++ -g -O3 txt2bin.cpp cmdline_txt2bin.c -o txt2bin",
        cwd=COLL_DIR)
    assert os.path.exists(COLL_BIN), f"coll txt2bin not built: {COLL_BIN}"
    _log("coll txt2bin OK")


def build_pcm():
    _log("pcm-sdk htsim (INC datapath) ...")
    assert os.path.isdir(PCM_DIR), (
        f"{PCM_DIR} not found — on the host run:\n"
        "  git submodule update --init --recursive sim/pcm-sdk_zhiyi")
    for d in ["uet-htsim-patch", "uet-htsim/htsim/sim", "HTSIM_spcl-patch",
              "HTSIM_spcl/htsim/sim", "xxHash", "pcm"]:
        assert os.path.exists(os.path.join(PCM_DIR, d)), \
            f"pcm-sdk_zhiyi missing {d} (recursive submodule init?)"
    _sh(
        "set -euo pipefail; "
        "cp -rf ./uet-htsim-patch/. ./uet-htsim/htsim/sim/; "
        "( cd uet-htsim/htsim/sim && cmake -E remove_directory build && "
        "  cmake -S . -B build && cmake --build build --parallel ); "
        "cp -rf ./HTSIM_spcl-patch/. ./HTSIM_spcl/htsim/sim/; "
        "( cd HTSIM_spcl/htsim/sim && cmake -E remove_directory build && "
        "  cmake -S . -B build && cmake --build build --parallel ); "
        "( cd xxHash && make clean && make ); "
        "( cd pcm && python3 build.py --debug --clean --build-htsim-atlahs )",
        cwd=PCM_DIR)
    assert os.path.exists(PCM_BIN), f"pcm binary not built: {PCM_BIN}"
    _log("pcm binary OK")


if __name__ == "__main__":
    build_coll_txt2bin()
    build_pcm()
    _log("simulation build complete")
    print(f"  simulator : {PCM_BIN}", file=sys.stderr)
    print(f"  txt2bin   : {COLL_BIN}", file=sys.stderr)
