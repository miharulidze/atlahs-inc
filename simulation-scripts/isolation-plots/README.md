# isolation-plots — all isolation-experiment figures in one place

PNG copies of every isolation-experiment figure (the source PDFs live next to each
experiment's CSV under `../results/<exp>/`). Regenerate a plot with its experiment's
`plot.py`, then run `./collect.sh` to refresh this folder. All figures are pcm-sdk,
measured-only, post-fix (real receive datapath), 0 drops.

| PNG | Experiment | What it shows |
|---|---|---|
| `ar_bandwidth.png` | E1 (`scaleup_ar_bandwidth`) | Reduction bandwidth 8·S/T vs size: fused **apex → 98% wire**, composed **RS∘AG → 49%** (half). \|G\|=64, single-switch. |
| `inc_speedup_overview__single_switch.png` | M-A (`scaleup_coll_ab`) | INC-vs-endpoint speed-up vs size, all collectives, single-switch, \|G\|=64. |
| `inc_speedup_overview__fat3tier.png` | M-A | Same, 256-host 3-tier fat-tree. |
| `inc_time_<coll>__single_switch.png` | M-A | Per-collective completion time, in-network vs endpoint baseline(s), single-switch. `<coll>` ∈ allreduce, allreduce_rs_ag, reduce_scatter, allgather, bcast, reduce. |
| `inc_time_<coll>__fat3tier.png` | M-A | Same, 3-tier fat-tree. |
| `groupsweep_time__4096.png` / `__4194304.png` | X1 (group-sweep) | Completion time vs \|G\| (4 KiB / 4 MiB): in-network flat = **constant-in-\|G\|** (AR/Reduce/Bcast); AllGather rises (ingress-bound). |
| `groupsweep_speedup__4096.png` / `__4194304.png` | X1 | Speed-up vs \|G\| at the two sizes. |
| `pfc_backpressure.png` | X2 (`scaleup_pfc_concurrent`) | Slowest completion vs N concurrent AllReduces: **pinned grows (PFC serialises), distributed flat**, 0 drops. |
| `footprint_allgather.png` | M-B (`scaleup_coll_footprint`) | Khalilov Fig.2 reproduction: AllGather byte-ratio vs P, Ring vs RD, per topology. |
| `footprint_all_collectives.png` | M-B | Byte-ratio for AllGather/AllReduce/ReduceScatter × single-switch/3-tier. |
| `analytic_khalilov.png` | M-B | Analytic byte-ratio model overlay (measured-vs-analytic agreement). |

Design context: `sim/htsim-backend/sim/AA-plan-Validation-Chapter/plan.md` §10.
