# Scale-up AllReduce microbenchmark — short summary

**Goal.** Validate the in-network-computing (INC) collective mechanism and the new GOAL
collective format, and get the expected AllReduce result, on a realistic scale-up tier —
ahead of the full two-tier sim.

**Setup.** One logical AllReduce, two renderings of it, run in our htsim fork:
- **INC arm** — kept whole: one first-class `coll` op per rank → in-network reduce + multicast.
- **Baseline arm** — decomposed into a **chunked, bandwidth-optimal ring** of point-to-point
  send/recv (`2(N−1)` steps × `size/N` bytes = the textbook `2(N−1)/N·S` per-rank traffic).

Both arms are GOAL `.bin` with identical framing, measured by the same
`Maximum finishing time at host` oracle. Sweep: `|G| ∈ {2,4,8,16}` × size `{4 KiB…4 MiB}`.
All 24 cells complete with **0 drops**, seed-invariant; ring correctness + the latency model
were adversarially verified.

## Tier grounding (why the model is comparable)

Scale-up tier = the reference two-tier harness's intra-node tier, **reused**:
`tree16` (16 GPUs, 4/leaf), **3600 Gbps/link**, **500 ns link** + **300 ns NVSwitch** latency.

- **Comparability by construction:** identical params to the validated two-tier sim
  (`tree16_bw3600Gbps.topo`, the harness's copy-engine speed), so results compose directly.
- **NVLink-class in absolute terms:** NVLink5 is 1.8 TB/s per GPU (18 links × 100 GB/s);
  a GB200 NVL72 is a 130 TB/s single-switch-layer domain. Our 3600 Gbps/link = 450 GB/s/dir
  ≈ **9 NVLink5 links ≈ half a GB200 GPU's NVLink bandwidth** — conservative vs the full
  1.8 TB/s. Per-hop 500 ns + 300 ns ≈ 0.8 µs sits below NVLink's ~2 µs P2P latency, so a
  2–4-hop intra-node path is in the right range. NVSwitch is lossless (matches our PFC plan).
- **Bias:** a faster fabric would speed the bandwidth-bound ring but not the latency-regime
  gap (serial hops are BW-independent), so the headline result is robust to the exact BW.

## Result

INC speedup (ring ÷ INC): **3.7× (|G|=2) → 33× (|G|=16, small messages)**. Read as a
**crossover**, not a flat multiple:
- **Latency regime (small msgs) — algorithmic, defensible:** the ring pays `2(N−1)` *serial*
  dependency hops, INC pays its apex tree-depth (~`O(log N)`). Transport-independent (would
  survive a line-rate ring).
- **Bandwidth regime (large msgs) — transport-sensitive:** INC's ACK-less sources stream at
  ~line rate (~97%) while the decomposed ring runs over CC'd UEC (~10%); a fair line-rate ring
  would narrow this toward the ~2× in-network ceiling. Realistic (real decomposed AllReduce
  *is* CC'd) but the magnitude is CC-sensitive.

See `datacenter/allreduce_ab/allreduce_ab.png`, data in `…/results.csv`, generator
`datacenter/make_allreduce_ab.cpp`, harness `…/allreduce_ab/run_allreduce_ab_sweep.py`.

## Next: use Shuhao's own decomposition

Shuhao's `simple_sim` generator already lowers collectives to a **chunked bandwidth-optimal
ring** (`to_goal`/`_ring_steps`, `chunk = size/comm.size`; `communication.py:142-153`) and
runs purely synthetic. So the trace-driven step can emit **both arms from his generator** —
whole `coll` (INC) for scale-up, his ring decomposition for scale-out — making the baseline
the generator's *own* decomposition (same algorithm as our hand-written ring) rather than a
bespoke one. That is the per-domain emit hook (Stage 2).

Sources: [NVIDIA NVLink](https://www.nvidia.com/en-us/data-center/nvlink/),
[GB200 NVL72](https://www.nvidia.com/en-us/data-center/gb200-nvl72/),
[Introl: NVLink scale-up networking](https://introl.com/blog/nvlink-scale-up-networking-gpu-interconnect-infrastructure-2025).
