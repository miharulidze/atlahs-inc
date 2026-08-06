# Track A audit — the two-tier scale-up/scale-out htsim harness

Date: 2026-06-24. Subject: `ZhiyiHu1999/atlahs` branch `validations_atlahs_v1.1`
(clone at `~/CLionProjects/atlahs_ZhiyiHu`), the harness our supervisor pointed at
(`entrypoint.sh#L70`, `run_goal_workloads_exp.py#L256`). Companion to
`scaleup-inc-integration-ultraplan.md` and `scope-delta-2026-06-19-supervisor-meeting.md`.

Purpose: lock the **input contract** of the existing two-tier simulator so our INC
microbenchmark (built in `htsim_uec`, Option B) mirrors it exactly, and confirm which
pieces are public/private. Every claim below is grounded in a read of the branch.

---

## 1. The pipeline at a glance

```
entrypoint.sh  run -g <dir>                         (entrypoint.sh:70-72)
   └─ python3 scripts/run_goal_workloads_exp.py -g <dir>
        ├─ JOBS = 4 Llama7B GOAL .bin workloads      (run_goal_workloads_exp.py:98-103)
        └─ for each job: sweep 4 case dimensions, each → one CSV
             └─ invoke the PCM-SDK binary htsim_flow_app_atlahs per (job, case)
                  PCM_APP_HTSIM_ATLAHS_EXEC_PATH =
                    /workspace/sim/pcm-sdk_zhiyi/pcm/build/bin/htsim_flow_app_atlahs
```

- The binary `htsim_flow_app_atlahs` lives in the **PRIVATE** submodule
  `sim/pcm-sdk_zhiyi` (`ZhiyiHu1999/pcm-sdk`, branch
  `feature/support_for_dragonfly_slimfly`). Confirmed inaccessible: `git submodule
  update --init` returns `remote: Repository not found` (404). **This does not block
  Option B** — we mirror its *interface*, not its code.
- `build.py:208-252` builds it: `cd pcm; python3 build.py --debug --clean
  --build-htsim-atlahs`.
- `run.py` is unrelated (validation/quick/full reproduction only — no goal-workload
  sweep, no A/B).

## 2. The exact input contract (the thing we mirror)

The active command template (intra-node-BW sweep, `run_goal_workloads_exp.py:256-267`):

```
htsim_flow_app_atlahs
  -topo            <TOPO_FILES>/tree1024_bw200Gbps.topo   # inter-node (scale-OUT) tier
  -linkspeed       200000                                 # NIC speed, kbps-style (=200 Gbps)
  -q               1000000
  -intranode_topo  <TOPO_FILES>/<swept tree16_*.topo>     # intra-node (scale-UP) tier
  -intranode_linkspeed <swept, e.g. 3600000>              # =3600 Gbps ≈ NVLink-class
  -intranode_q     1000000
  -strat ecmp_host -seed 42 -mtu 4096 -paths 128
  -nodes 1024 -num_gpus_per_node 4                        # 1024 nodes × 4 GPUs = 4096 endpoints
  -goal            <Llama7B_*.bin>
  -end             100000000000
  -sender_cc_only
  -pcm_enable -pcm_cc_config_file <CC>/pcm_cc_config_all_uec_dctcp.json
  -pcm_sched_poll_delay 1000 -pcm_handler_delay 1000      # PCM-engine latencies (units TBD)
```

Two-tier composition flags = **`-topo` (scale-out) + `-intranode_topo` +
`-num_gpus_per_node` (scale-up)**. A "node" expands into `num_gpus_per_node` GPU
endpoints behind an intra-node `tree16`-class switch; the node attaches to the
inter-node fabric. The intra-node tier is pinned at `tree16_bw3600Gbps` by default
(`COPY_ENG_SPEED=3600000`).

**Four independent sweep dimensions** (`run_goal_workloads_exp.py:24-89`), each its own
loop + CSV:
| dimension | knob | values |
|---|---|---|
| `NETWORK_TOPO_TYPE_CASES` (5) | inter-node topo type | fattree / dragonfly / slimfly |
| **`INTRANODE_BW_CASES` (9)** | **scale-UP tier BW** | tree16 @ 12800→100 Gbps |
| `NETWORK_BW_CASES` (11) | scale-out tier BW | tree1024 @ 12800→100 Gbps |
| `PCM_CC_CONFIG_CASES` (20) | congestion control | cubic/dctcp/dcqcn/swift/strack/nscc/smartt/uec_dctcp/… |

The **scale-up knob is `INTRANODE_BW_CASES`** — exactly the dimension our INC work targets.

## 3. Key finding — `pcm-sdk` is UEC-family (answers Zhiyi-Q1 for free)

Every non-CC sweep pins `pcm_cc_config_all_uec_dctcp.json`
(`run_goal_workloads_exp.py:203/243/310`). So the default transport of the two-tier sim
is **UEC with a DCTCP-style PCM congestion handler**. This is the same transport family
our INC primitives are coupled to (`UecSrc`/`UecReduceSrc`/`UecCollectiveSink`). Practical
consequence: **Option A (port INC → pcm-sdk) is cheaper than feared** — no transport
re-coupling — and INC's ACK-less sources bypass CC anyway. Option B remains the
recommended path; this just de-risks the future-work cross-check.

## 4. The A/B slot — `VT_OPT_CASES` (commented out)

`run_goal_workloads_exp.py:91-94` carries a disabled optimized-vs-baseline pair:
```python
# VT_OPT_CASES = [
#   {"case_name": "vt_no_opt", "goal": ".../Llama7B_..._BS32.bin"},
#   {"case_name": "vt_opt",    "goal": ".../vt_opt/Llama7B_..._BS32.bin"},
# ]
```
It is never referenced (grep finds it only here, not in `run.py`). This is **the exact
slot our INC A/B fits**: two renderings of one workload (`.bin`), all other flags held
fixed, swapping only the trace. Enabling it = uncomment + a fifth loop. We replicate this
A/B *shape* in `htsim_uec` (INC arm vs point-to-point arm).

## 5. The completion oracle

`parse_htsim_max_host_time` (`run_goal_workloads_exp.py:120-132`) scans stdout for lines
beginning `Host`, splits on `:`, takes the integer, and returns the **max across hosts**:
```python
if line.startswith("Host"):
    host_times.append(int(line.split(":")[1].strip()))
return max(host_times) if host_times else None
```
**Crucially, our `htsim_uec` GOAL path already prints this exact format** —
`logsim-interface.cpp:1314` emits `Maximum finishing time at host H: T` *and* per-host
`Host N: T` lines (verified by running the INC arm). So our microbenchmark is already
oracle-compatible with Zhiyi's harness; no output-format work needed.

## 6. The `.topo` grammar (compatible with our fork)

`tree16_bw3600Gbps.topo` (the NVLink-class scale-up tier):
```
Nodes 16
Tiers 2
Podsize 16

Tier 0
Downlink_speed_Gbps 3600
Radix_Down 4
Radix_Up 4
Downlink_Latency_ns 500
Switch_Latency_ns 0          # ← note: even the "realistic" harness runs switches at 0 ns

Tier 1
Downlink_speed_Gbps 3600
Radix_Down 4
Downlink_Latency_ns 500
Switch_Latency_ns 0
```
Our `htsim_uec` `FatTreeTopology::load` (`fat_tree_topology.cpp:82-202`) parses the **same
keys** (it lowercases tokens; supports 2–3 tiers; honours per-tier `Switch_Latency_ns` and
`Downlink_Latency_ns`, plus `Oversubscribed`). **We can reuse the validations
`tree16_bw3600Gbps.topo` verbatim** as our scale-up tier — strong methodological alignment
with Zhiyi's sim. (Observation worth flagging in the thesis: the validations harness sets
`Switch_Latency_ns 0` and `Downlink_Latency_ns 500`, so switch *processing* latency is
unmodelled there too — see the switch-latency note.)

## 7. The generator side (the scale-up baseline + Shuhao's TP path)

All three relevant branches **exist and are fetchable** on the public generator remote
`Yanksi/nccl_generator_v2` (`git ls-remote --heads`):
- **`zhiyi/tests`** (`7a9f76f`) — real intra-node traffic via
  `enable_intra_node_transfer=False` + topo-aware virtual topology
  (`configure_topo.py`/`configure_ring_topo.py`/`configure_tree_topo.py`). This is the GOAL
  format `pcm-sdk` consumes = the scale-up **baseline arm** (real intra-node p2p, still
  decomposed).
- **`simple_sim`** (`8f35ce9`) — Shuhao Li's parameterised Llama-3 trainer
  (`llama3_training.py` → per-device IR → `simple_sim2goal.py`). Plain-TP Megatron keeps the
  **AllReduce whole** (no sequence-parallelism) = the first-class INC target; the per-domain
  emit hook is one branch in `translate_comm_node` (context `tp`→emit `coll`, else decompose).
- **`tb/intermediate-collective-goal`** (`10bc69b`) — Tommaso's first-class GOAL-emit WIP
  (may subsume our generator-side emit work).

(The pinned submodule commit sits on `master`, so these aren't checked out by default —
fetch the branch to read them.)

**What this gives us now:** our microbenchmark does not need to *run* `simple_sim` — it
models Shuhao's workload directly (plain-TP whole AllReduce, scale-up domain, realistic
Llama sizes) as a controlled `.bin` A/B. The full trace-driven run on `simple_sim` /
`zhiyi/tests` output is Stage 2–3 of the ultraplan.

### Realistic TP-AllReduce message sizes (Megatron, no sequence-parallelism)
Per transformer layer the TP group AllReduces the activation tensor `[batch·seq, hidden]`
(once after attention, once after MLP, ×2 again in the backward pass). Size ≈
`tokens · hidden · dtype_bytes`:
| config | hidden | seq×mbs (tokens) | bf16 size per AllReduce |
|---|---|---|---|
| Llama-7B | 4096 | 4096 | ≈ 32 MiB |
| mid | 2048 | 2048 | ≈ 8 MiB |
| small | 1024 | 512 | ≈ 1 MiB |
NCCL tiles these into chunks, but the *logical* collective is one whole AllReduce. Our
sweep therefore brackets ~64 KiB … ~32 MiB to cover small-to-Llama-7B-class TP AllReduce,
at TP group sizes |G| ∈ {2,4,8,16} (TP rarely exceeds 8 intra-node).

## 8. What Track A means for our work

1. **Contract to mirror** (§2): `-topo` + `-intranode_topo` + `-num_gpus_per_node`, NIC
   kbps-style linkspeeds, `tree16@3600` scale-up tier, `-seed 42 -mtu 4096 -paths 128`. Our
   microbenchmark adopts the scale-up tier (`tree16_bw3600Gbps`) and the seed/MTU/paths
   conventions so results compose with Zhiyi's sim later.
2. **Oracle already matches** (§5): our GOAL path prints `Host N:` / `Maximum finishing
   time`, exactly what `parse_htsim_max_host_time` reads.
3. **A/B shape** (§4): two `.bin` renderings of one workload, all flags fixed — our INC
   (whole AllReduce) vs P2P (decomposed) arms are the `vt_opt`/`vt_no_opt` analogue.
4. **No private dependency** on the critical path (Option B); `pcm-sdk`'s UEC-family
   transport (§3) makes the eventual Option-A cross-check cheap.

## 9. Open questions for Zhiyi (sharpened)
1. Units of `-pcm_sched_poll_delay` / `-pcm_handler_delay` (both `1000`) — sim ticks? ns?
   (Needed only for the Option-A cross-check.)
2. Default of `-topo_type` when omitted (intranode/network-BW/CC loops omit it) — assume
   `fattree`?
3. Does `htsim_flow_app_atlahs` emit the `Host N:` lines from the **GOAL** path the same way
   our `htsim_uec` does, or from a different print site? (Confirms oracle equivalence.)
4. `pcm-sdk` access — to lift the `-intranode_topo`/`-num_gpus_per_node` composition code
   (Stage-1 two-tier build) and as the Option-A fallback.
