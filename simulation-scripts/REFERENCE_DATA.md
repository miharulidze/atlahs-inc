# Committed thesis results

The tracked CSVs and Chapter 5 run preserve the measurements used by the thesis. They
can be used to regenerate the existing figures and tables without rerunning the
simulator.

| dataset | tracked scope | source revision | thesis use |
|---|---|---|---|
| `scaleup_coll_ab` | 126 rows: 7 cases × 9 sizes × 2 fabrics, group 64 | PCM `3179f0b` | Chapter 4 completion time |
| `scaleup_coll_ab_groupsweep` | 84 rows: 7 cases × 2 sizes × 6 group sizes | `e2c3042` | Chapter 4 group-size sweep |
| `scaleup_coll_footprint` | 96 rows: 2 fabrics, groups 2–64, 5 collectives | `e2c3042` | Chapter 4 network footprint |
| canonical Chapter 5 run | 6 rows: TP 4/8/16 × baseline/INC | run `stable-order-ga32-20260808` | Chapter 5 case study |

## Reference results

The Chapter 4 completion-time matrix and Chapter 5 case study were refreshed with the
stable equal-time event ordering and globally unique collective flow IDs. The Chapter 4
group sweep was checked and remained identical. The footprint data was retained because
its byte-link metric is independent of equal-time event ordering.

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
