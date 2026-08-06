# isolation-plots — all isolation-experiment figures in one place

This directory collects generated PNG copies of the isolation-experiment figures and can
build a self-contained HTML deck. The generated PNGs and `deck.html` are ignored; source
CSVs, PDFs, and plotting scripts remain under `../results/<exp>/` and
`../experiments/<exp>/`. Run `./collect.sh`, then `python3 build_deck.py`, to recreate
the collection. All figures are PCM measurements using the corrected receive datapath.

| PNG | Experiment | What it shows |
|---|---|---|
| `ar_bandwidth.png` | E1 (`scaleup_ar_bandwidth`) | Reduction bandwidth 8·S/T vs size: fused **apex → 98% wire**, composed **RS∘AG → 49%** (half). \|G\|=64, single-switch. |
| `inc_speedup_overview.png` | M-A (`scaleup_coll_ab`) | INC-vs-endpoint speed-up vs size, all collectives and both tracked fabrics, \|G\|=64. |
| `inc_time_<coll>__single_switch.png` | M-A | Per-collective completion time, in-network vs endpoint baseline(s), single-switch. `<coll>` ∈ allreduce, allreduce_rs_ag, reduce_scatter, allgather, bcast, reduce. |
| `inc_time_<coll>__fat3tier.png` | M-A | Same, 3-tier fat-tree. |
| `groupsweep_time__4096.png` / `__4194304.png` | X1 (group-sweep) | Completion time vs \|G\| (4 KiB / 4 MiB): in-network flat = **constant-in-\|G\|** (AR/Reduce/Bcast); AllGather rises (ingress-bound). |
| `groupsweep_speedup__4096.png` / `__4194304.png` | X1 | Speed-up vs \|G\| at the two sizes. |
| `pfc_backpressure.png` | X2 (`scaleup_pfc_concurrent`) | Slowest completion vs N concurrent AllReduces: **pinned grows (PFC serialises), distributed flat**, 0 drops. |
| `footprint_allgather.png` | M-B (`scaleup_coll_footprint`) | Khalilov Fig.2 reproduction: AllGather byte-ratio vs P, Ring vs RD, per topology. |
| `footprint_all_collectives.png` | M-B | Byte-ratio for AllGather/AllReduce/ReduceScatter × single-switch/3-tier. |
| `analytic_khalilov.png` | M-B | Analytic byte-ratio model overlay (measured-vs-analytic agreement). |

These generated convenience copies are not sources of record.
