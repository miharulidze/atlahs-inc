# G72: the application-level llama3 A/B at group size 72 (2026-07-15, in progress)

Successor of `../llama3_ab_pcm/tp_share_sweep` (16 ranks): every experiment
redone with the TP group pinned at **72** — the collective-level headline
group size — on the multi-domain simulator with one 72-GPU scale-up domain
per node (`scaleup_single_switch_72_3600Gbps.topo`, lossless_input PFC,
`-intranode_linkspeed 4000000` pin), scale-out `tree16.topo`.

## The one modelling decision to know about

**No published llama3 shape divides 72** (8B: 32 heads / hidden 4096;
70B: 64 / 8192) and Megatron-style TP requires heads % tp == 0 and
hidden % tp == 0. The model is therefore an **8B-scale llama3-class shape
re-factored to 72 heads**: head_dim 64 -> hidden 4608 (vs 4096),
intermediate 3.5x = 16128, kv = heads = 72 (the generator's existing MHA
convention), seq_len 144 (the SP rendering asserts batch_seq % tp == 0;
128 % 72 != 0), 2 layers, 2 iterations — same excerpt structure as the
16-rank suite, parameter volume within ~13 % of it. Alternatives rejected:
head_dim 128 -> hidden 9216 quintuples parameter count and multiplies DP
traffic ~x90 (simulation-intractable and no longer 8B-class); TP <= 32
keeps real llama3 shapes but cannot produce a 72-wide group.

## Configs

| label | tp/dp/pp | ranks | domains | analog of |
|---|---|---|---|---|
| G1 | 72/1/1 | 72 | 1 | C5 (pure TP, share = 1) |
| G2 | 72/2/1 | 144 | 2 | — (light DP) |
| G3 | 72/4/1 | 288 | 4 | C2 (DP-heavy) |
| G4 | 72/2/2 | 288 | 4 | C3 (PP-amplified) |
| SPG1/SPG4 | + sequence parallelism | | | SP-C5 / SP-C3 |
| H4 | G4 under COMPUTE_MODEL=h100 | 288 | 4 | computemodel probe |

Pipeline + gotchas (04d pkl padding — the stock 02d scrambles lexical rank
order at >= 100 ranks; dp=1 size-1-communicator guard; literals patched and
restored via git checkout): `scripts/run_g72_suite.py`.

## Pipeline findings (hit while building; all fixed in scripts/)

1. **Baseline AllReduce algorithm**: `simple_sim2goal.py` hardcodes
   RECURSIVE_DOUBLING, which asserts a power-of-two communicator — impossible
   at |G| = 72 (the same constraint that excludes RD from the collective-level
   N = 72 row). The guarded renderer forces `CollAlgo.RING` for every
   AllReduce (DP groups included): one uniform, group-size-agnostic baseline.
2. **device2goal_rank**: hardcoded identity map over `range(32)`
   (simple_sim2goal.py:76) — KeyError beyond 32 ranks; renderer rebinds it.
3. **pkl rank order**: the stock drivers write `{device_id:02d}.pkl` and
   `get_graphs` sorts lexically — silent rank scrambling at >= 100 ranks;
   the suite patches to `04d`.
4. **SP context gate**: SP renderings tag TP comm `context="tp_sp"`;
   `INC_CONTEXTS=tp` selects nothing (empty groups, INC arm == baseline).
   SP configs render with `INC_CONTEXTS=tp,tp_sp` (sp_ab precedent).
5. **Scale-out topo capacity**: the pcm federation gives EVERY GPU its own
   scale-out NIC (rank = scale-out endpoint). `tree16.topo` therefore capped
   all earlier suites at 16 total ranks; multi-domain G72 runs segfaulted
   (rc = -11) until the scale-out tier was regenerated as
   `topologies/tree288_100Gbps.topo` (288 hosts, same 100 Gbps class/shape).
   Single-domain configs (G1/SPG1) never launch a scale-out flow, which is
   why they ran.
