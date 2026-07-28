# _template — skeleton for a new experiment

Copy this directory to `experiments/<your_name>/` (no leading underscore) and
replace the GOAL synthesis, sweep loop, and CSV schema. The `run.py` here is a
working end-to-end demo of the experiment contract:

1. two-line `sys.path` header, then `from common import goal, paths, report, sim`;
2. synthesize a `.goal` trace (generator decomposition and/or INC `coll` ops);
3. `goal.compile_goal()` -> canonical `.bin`;
4. `sim.run_sim()` -> makespan/drops/status/command/pfc-extras;
5. `report.CsvAppender` -> `results/<your_name>/…csv`.

Conventions:
- `run.py` must be argparse-based; `entrypoint.sh run <name> [args]` passes
  args through. `list` enumerates experiments (underscore-prefixed dirs are
  skipped, which is why this template is invisible to dispatch).
- Results go under `results/<your_name>/` via `paths.results_dir(EXP_NAME)`
  (gitignored; env-overridable via `SIM_RESULTS_ROOT`).
- Topologies live in `topo_files/`; resolve basenames with `paths.topo()`.
- Add a README like this one: what the experiment measures, how to run it,
  what the output columns mean.

Smoke-test the template itself (skipped by dispatch, so invoke directly):

```bash
python3 experiments/_template/run.py            # locally, binaries built in-tree
docker run --rm --entrypoint python3 -v "$(pwd)":/workspace atlahs-sim \
    /workspace/simulation-scripts/experiments/_template/run.py
```
