# Reference-data lineage

The tracked CSVs preserve the thesis results. They regenerate the existing figures and
tables, but they are historical measurements rather than a promise that every future
backend revision will be bit-identical. Use one source revision for an entire paper
result set; never append newly simulated rows to these files.

| dataset | tracked scope | last data revision | publication role |
|---|---|---|---|
| `scaleup_coll_ab` | 126 rows: 7 cases × 9 sizes × 2 fabrics, group 64 | `e2c3042` | Chapter 4 completion time |
| `scaleup_coll_ab_groupsweep` | 84 rows: 7 cases × 2 sizes × 6 group sizes | `e2c3042` | Chapter 4 group sweep |
| `scaleup_coll_footprint` | 96 rows: two fabrics, groups 2–64, five collectives | `e2c3042` | Chapter 4 network footprint |
| four `scaleup_pfc_concurrent` CSVs | 60 rows across census, controls, stress, overdrive | `4ffb8f8` | flow-control validation |
| canonical Chapter 5 run | 6 rows: TP 4/8/16 × baseline/INC | run `20260731T1405Z` | Chapter 5 case study |
| `scaleup_ar_bandwidth` | 18 rows | `a4cc8be` | supplementary only |

The footprint file originally contained five appended 96-row generations. Every
non-command field, including every measured value and status, was identical for all
five copies. The public artifact retains the final `e2c3042` occurrence of each logical
row, whose command records the current explicit queue caps. This removes 384 duplicate
rows without changing a numerical result. The radix-32, 1024-host topology is an
optional extension and is not part of this tracked thesis dataset.

## Compatibility with the corrected backend

Correctness fixes can legitimately change packet-level scheduling. In particular,
globally unique flow IDs can change ECMP choices that previously depended on colliding
IDs. Therefore, archived and newly generated rows must not be mixed even when the
difference is small.

A pre-release spot check on 2026-08-06 reran all seven cases at group 64 and 4 MiB. The
single-switch results were exact. On the three-tier fabric, all in-network times and the
rooted baselines were exact; ring baselines differed by 3 ns and recursive doubling by
427 ns (0.88%). The check was deterministic, but it was not a clean tagged release and
does not replace the required full sweep.

For a workshop submission, keep the thesis CSVs as historical reference data and create
a coherent new result set from the final public commit. Regenerate all Chapter 4 rows,
the full flow-control gate, and the Chapter 5 canonical run before replotting. Review the
old/new delta before replacing any tracked reference file.

Run the structural audit with:

```bash
python3 simulation-scripts/model_checks/audit_reference_data.py
```

Frozen wrappers write a fresh, ignored `results/generated-runs/<run-id>/` archive and
only publish a CSV after the complete matrix succeeds. Preserve that archive until the
replacement dataset has been reviewed and tied to a release commit and image digest.
