#!/usr/bin/env python3
"""_template — copy-me skeleton for a new GOAL-driven pcm-sdk experiment.

To create a new experiment:
  1. cp -r experiments/_template experiments/<your_name>   (no leading underscore)
  2. Replace the GOAL synthesis, sweep loop, and CSV schema with yours.
  3. `entrypoint.sh run <your_name> [args]` dispatches to it; `list` shows it.

The skeleton demonstrates the full contract end-to-end on a deliberately tiny
case (one generator-decomposed AllGather, N=2): synthesize .goal -> compile to
.bin (coll txt2bin) -> run htsim_flow_app_atlahs -> parse makespan -> CSV row.
Underscore-prefixed directories are skipped by dispatch; run this one directly:
  python3 experiments/_template/run.py
"""
import argparse
import os
import sys

# Contract: put the simulation-scripts root on sys.path, then use common/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from common import goal, paths, report, sim  # noqa: E402

EXP_NAME = "_template"
TAIL_NS = 100  # dependent calc tail after the collective; subtracted from the metric

CSV_FIELDS = ["collective", "group_size", "msg_bytes", "makespan_ns", "net_ns",
              "drops", "status", "su_topo", "so_topo", "engine", "command"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=2, help="ranks (all in node 0, intranode)")
    ap.add_argument("--size", type=int, default=4096, help="message bytes")
    ap.add_argument("--su-topo", default="tree16_bw200Gbps.topo",
                    help=".topo basename in TOPO_FILES_PATH for the scale-up domain")
    ap.add_argument("--so-topo", default="tree16_bw200Gbps.topo",
                    help=".topo basename for the (idle) scale-out fabric; needs >= n hosts")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--tmpdir", default="/tmp/sim_template")
    args = ap.parse_args()

    # 0. Loud preflights (build via `entrypoint.sh build`, or env-override paths).
    goal.require_txt2bin()
    goal.require_generator()
    sim.require_simulator()

    # 1. Synthesize the GOAL trace (here: the generator's own ring AllGather).
    os.makedirs(args.tmpdir, exist_ok=True)
    g = os.path.join(args.tmpdir, f"allgather_{args.n}_{args.size}.goal")
    goal.gen_baseline_goal(g, args.n, args.size, "allgather", "ring", TAIL_NS)

    # 2. Compile to the canonical LogGOPSim .bin.
    b = g[:-5] + ".bin"
    goal.compile_goal(g, b)

    # 3. Run the pcm-sdk simulator, single-domain isolation (nodes == gpus == n).
    fin, drops, status, cmd, _pfc = sim.run_sim(
        b, paths.topo(args.so_topo), paths.topo(args.su_topo),
        nodes=args.n, gpus_per_node=args.n, timeout=args.timeout)

    # 4. Report.
    out_dir = paths.results_dir(EXP_NAME)
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "template.csv")
    with report.CsvAppender(csv_path, CSV_FIELDS) as out:
        out.write({"collective": "allgather", "group_size": args.n,
                   "msg_bytes": args.size, "makespan_ns": fin or "",
                   "net_ns": (fin - TAIL_NS) if fin else "", "drops": drops,
                   "status": status, "su_topo": args.su_topo, "so_topo": args.so_topo,
                   "engine": "pcm-sdk", "command": cmd})
    ok = fin is not None and status == "ok"
    (report.print_success if ok else report.print_error)(
        f"allgather N={args.n} {args.size}B -> makespan {fin} ns "
        f"(status={status}, drops={drops}); CSV: {csv_path}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
