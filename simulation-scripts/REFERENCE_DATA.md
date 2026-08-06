# Committed thesis results

The tracked CSVs and Chapter 5 run preserve the measurements used by the thesis. They
can be used to regenerate the existing figures and tables without rerunning the
simulator.

| dataset | tracked scope | source revision | thesis use |
|---|---|---|---|
| `scaleup_coll_ab` | 126 rows: 7 cases × 9 sizes × 2 fabrics, group 64 | `e2c3042` | Chapter 4 completion time |
| `scaleup_coll_ab_groupsweep` | 84 rows: 7 cases × 2 sizes × 6 group sizes | `e2c3042` | Chapter 4 group-size sweep |
| `scaleup_coll_footprint` | 96 rows: 2 fabrics, groups 2–64, 5 collectives | `e2c3042` | Chapter 4 network footprint |
| canonical Chapter 5 run | 6 rows: TP 4/8/16 × baseline/INC | run `20260731T1405Z` | Chapter 5 case study |

## Historical and current results

The current backend includes later correctness fixes, including globally unique
collective flow IDs. Such changes can alter ECMP choices and packet scheduling even
when the workload is unchanged. Therefore, do not append results from the current code
to the committed historical CSVs or compare a mixture as one experiment matrix. Keep a
new run together with the exact root and submodule revisions that produced it.

The frozen Chapter 4 wrappers stage a complete run under
`results/generated-runs/<run-id>/` and update the result CSV only when the matrix
finishes successfully. Chapter 5 always creates a separate immutable run directory.

## Footprint CSV cleanup

The footprint CSV previously contained the same 96-row generation five times. All
measured fields were identical. The tracked file retains one copy of each logical row,
removing 384 duplicates without changing any numerical result.

The structural consistency check is included in the optional top-level validation:

```bash
simulation-scripts/validate_artifact.sh
```
